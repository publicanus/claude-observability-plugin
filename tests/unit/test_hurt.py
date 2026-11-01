"""Pins hooks/hurt.py, the script behind /mate-record:hurt: what it queues for the
hook to place, and what it says when it queues nothing."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

import hurt
import hurt_annotations
from conftest import write_settings

SETUP_COMMAND = "/mate-record:langfuse-setup"
OWN_KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-a", "LANGFUSE_SECRET_KEY": "sk-lf-a", "LANGFUSE_BASE_URL": "https://a.example"}
OUTSIDE_KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-terminal", "LANGFUSE_SECRET_KEY": "sk-lf-terminal"}


@pytest.fixture
def queue_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_langfuse_env: None) -> Path:
    """The unresolved queue /hurt writes, in tmp_path; the session starts in tmp_path."""
    unresolved_file = tmp_path / "state" / "hurt_unresolved.json"
    monkeypatch.setattr(hurt, "HURT_UNRESOLVED_FILE", unresolved_file)
    monkeypatch.setattr(hurt, "HURT_PENDING_LOCK_FILE", tmp_path / "state" / "hurt_pending.lock")
    # /hurt reads the working directory's repo settings; keep the developer's own repo out.
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    return unresolved_file


@pytest.fixture
def keyed_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """repo, with Langfuse keys in its settings file, as the session's working directory."""
    write_settings(repo, OWN_KEYS)
    monkeypatch.chdir(repo)
    return repo


def run_hurt(comment: str = "went in circles", category: str = "stalled-loop", session_id: Any = "sess-1") -> int:
    env = {"CLAUDE_CODE_SESSION_ID": session_id} if session_id else {}
    return hurt.run(["--category", category, "--comment", comment], env=env)


def read_queue(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------- What is queued -----------------

def test_a_note_is_queued_without_a_trace_for_the_hook_to_place(
    queue_file: Path, keyed_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = run_hurt()

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "/hurt: queued 'stalled-loop' for the turn before this one. "
        "mate-record's hook attaches it to that turn's trace once the trace is in Langfuse.\n"
    )
    [record] = read_queue(queue_file)
    # No trace or transcript content beyond what the operator typed.
    assert set(record) == {"id", "category", "comment", "created", "session_id", "target"}
    assert record["category"] == "stalled-loop"
    assert record["comment"] == "went in circles"
    assert record["session_id"] == "sess-1"
    assert record["target"] == hurt_annotations.queue_target("https://a.example", "pk-lf-a")
    assert record["id"] and record["created"]


def test_a_note_is_appended_after_earlier_notes(queue_file: Path, keyed_repo: Path) -> None:
    run_hurt("first")
    run_hurt("second", category="ci-escape")

    records = read_queue(queue_file)
    assert [r["comment"] for r in records] == ["first", "second"]
    assert len({r["id"] for r in records}) == 2


def test_a_missing_state_folder_is_created(queue_file: Path, keyed_repo: Path) -> None:
    assert not queue_file.parent.exists()

    assert run_hurt() == 0

    assert read_queue(queue_file)


# ----------------- When nothing is queued -----------------

def test_without_a_session_id_nothing_is_queued(
    queue_file: Path, keyed_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_hurt(session_id=None) == 1

    assert "session id" in capsys.readouterr().out.lower()
    assert not queue_file.exists()


@pytest.mark.parametrize(
    "own_env",
    [
        None,
        {"LANGFUSE_PUBLIC_KEY": "pk-lf-only-public"},
        {"CC_LANGFUSE_SECRET_KEY": "sk-lf-only-secret"},
    ],
    ids=["no file", "public key only", "secret key only"],
)
@pytest.mark.parametrize("outside", [{}, OUTSIDE_KEYS], ids=["no keys outside", "keys in the terminal"])
def test_without_keys_in_the_repos_file_nothing_is_queued_and_it_points_at_the_setup(
    queue_file: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    own_env: Any, outside: dict,
) -> None:
    if own_env is not None:
        write_settings(repo, own_env)
    for name, value in outside.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(repo)

    assert run_hurt() == 1

    out = capsys.readouterr().out
    assert out == (
        "/hurt: This repo's .claude/settings.local.json holds no Langfuse keys, so this session is not traced. "
        f"To add keys, run {SETUP_COMMAND}. Nothing queued.\n"
    )
    assert not queue_file.exists()


def test_a_broken_repo_settings_file_queues_nothing_and_names_it(
    queue_file: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = repo / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(repo)

    assert run_hurt() == 1

    out = capsys.readouterr().out
    assert out.startswith(f"/hurt: cannot read {settings} as JSON: ")
    assert out.rstrip().endswith("Nothing queued.")
    assert not queue_file.exists()


# ----------------- Another program holds the queue file open -----------------

def hold_queue_file(monkeypatch: pytest.MonkeyPatch, queue_file: Path, seconds: float) -> None:
    """Makes replacing queue_file fail as Windows does while another program, such
    as a virus scanner, has it open, for the given number of seconds from now."""
    real_replace = os.replace
    released_at = time.time() + seconds

    def replace(src: Any, dst: Any) -> None:
        if Path(dst) == queue_file and time.time() < released_at:
            raise PermissionError(13, "The process cannot access the file", str(dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)


def test_a_note_is_queued_when_the_queue_file_is_held_for_a_moment(
    queue_file: Path, keyed_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hold_queue_file(monkeypatch, queue_file, seconds=0.3)

    assert run_hurt() == 0

    assert capsys.readouterr().out.startswith("/hurt: queued 'stalled-loop'")
    assert [r["comment"] for r in read_queue(queue_file)] == ["went in circles"]


def test_a_queue_file_held_too_long_queues_nothing_and_says_so_in_one_line(
    queue_file: Path, keyed_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_hurt("earlier")
    capsys.readouterr()
    hold_queue_file(monkeypatch, queue_file, seconds=60)

    assert run_hurt() == 1

    assert capsys.readouterr().out == (
        f"/hurt: could not save {queue_file}: another program kept it open for 0.5 s. "
        "Nothing queued. Run /hurt again.\n"
    )
    assert [r["comment"] for r in read_queue(queue_file)] == ["earlier"]


# ----------------- Categories -----------------

def test_out_of_taxonomy_category_rejected() -> None:
    with pytest.raises(SystemExit):
        hurt.parse_args(["--category", "vibes-off", "--comment", "x"])


@pytest.mark.parametrize("category", hurt.CATEGORIES)
def test_every_taxonomy_category_accepted(category: str) -> None:
    args = hurt.parse_args(["--category", category, "--comment", "x"])
    assert args.category == category
