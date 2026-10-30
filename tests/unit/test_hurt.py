"""Pins hooks/hurt.py, the script behind /mate-record:hurt: which trace it finds for
the session, what it queues, and what it says when it queues nothing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import hurt
from conftest import write_settings
from state_files import get_session_state_key


# ----------------- helpers -----------------

def make_state_file(tmp_path: Path, entries: dict) -> Path:
    state_file = tmp_path / "langfuse_state.json"
    state_file.write_text(json.dumps(entries), encoding="utf-8")
    return state_file


def make_transcript(projects_dir: Path, project_slug: str, session_id: str) -> Path:
    project_dir = projects_dir / project_slug
    project_dir.mkdir(parents=True, exist_ok=True)
    transcript = project_dir / f"{session_id}.jsonl"
    transcript.write_text("", encoding="utf-8")
    return transcript.resolve()


def state_key(session_id: str, transcript: Path) -> str:
    return get_session_state_key(session_id, str(transcript))


@pytest.fixture
def pending_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    pending_file = tmp_path / "hurt_pending.json"
    lock_file = tmp_path / "hurt_pending.lock"
    monkeypatch.setattr(hurt, "HURT_PENDING_FILE", pending_file)
    monkeypatch.setattr(hurt, "HURT_PENDING_LOCK_FILE", lock_file)
    return pending_file, lock_file


# ----------------- resolution: happy path -----------------

def test_resolve_latest_trace_id_happy_path(tmp_path: Path) -> None:
    session_id = "abc-123"
    projects_dir = tmp_path / "projects"
    transcript = make_transcript(projects_dir, "-Users-op-myrepo", session_id)
    state_file = make_state_file(tmp_path, {state_key(session_id, transcript): {"latest_trace_id": "deadbeef" * 4}})

    resolution = hurt.resolve_latest_trace_id(
        session_id, projects_dir=projects_dir, state_file=state_file
    )

    assert resolution.ok
    assert resolution.trace_id == "deadbeef" * 4


# ----------------- resolution: each non-resolution shape -----------------

def test_resolve_latest_trace_id_no_session_id(tmp_path: Path) -> None:
    resolution = hurt.resolve_latest_trace_id(
        None, projects_dir=tmp_path / "projects", state_file=tmp_path / "state.json"
    )
    assert not resolution.ok
    assert "session id" in resolution.error.lower()


def test_resolve_latest_trace_id_no_transcript_found(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    resolution = hurt.resolve_latest_trace_id(
        "no-such-session", projects_dir=projects_dir, state_file=tmp_path / "state.json"
    )
    assert not resolution.ok
    assert "transcript" in resolution.error.lower()


SETUP_COMMAND = "/mate-record:langfuse-setup"
OUTSIDE_KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-terminal", "LANGFUSE_SECRET_KEY": "sk-lf-terminal"}


@pytest.fixture
def session_in(monkeypatch: pytest.MonkeyPatch, no_langfuse_env: None) -> Any:
    """Makes a folder the session's working directory, as the Bash tool runs /hurt."""
    def _in(folder: Path) -> None:
        monkeypatch.chdir(folder)
    return _in


def resolve_with_no_state_entry(tmp_path: Path) -> hurt.Resolution:
    session_id = "abc-123"
    projects_dir = tmp_path / "projects"
    make_transcript(projects_dir, "-Users-op-myrepo", session_id)
    state_file = make_state_file(tmp_path, {})  # empty state
    return hurt.resolve_latest_trace_id(session_id, projects_dir=projects_dir, state_file=state_file)


@pytest.mark.parametrize(
    "own_env",
    [
        {"LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test"},
        {"CC_LANGFUSE_PUBLIC_KEY": "pk-lf-test", "CC_LANGFUSE_SECRET_KEY": "sk-lf-test"},
    ],
)
def test_no_state_entry_with_keys_in_the_repos_file_points_at_the_hook(
    tmp_path: Path, repo: Path, session_in: Any, own_env: dict
) -> None:
    write_settings(repo, own_env)
    session_in(repo)

    resolution = resolve_with_no_state_entry(tmp_path)

    assert not resolution.ok
    assert "no mate-record state entry" in resolution.error.lower()
    assert "installed/enabled" in resolution.error
    assert "keys" not in resolution.error.lower()


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
def test_no_state_entry_without_keys_in_the_repos_file_points_at_that_file_and_the_setup(
    tmp_path: Path, repo: Path, session_in: Any, monkeypatch: pytest.MonkeyPatch, own_env: Any, outside: dict
) -> None:
    if own_env is not None:
        write_settings(repo, own_env)
    for name, value in outside.items():
        monkeypatch.setenv(name, value)
    session_in(repo)

    resolution = resolve_with_no_state_entry(tmp_path)

    assert not resolution.ok
    assert ".claude/settings.local.json" in resolution.error
    assert SETUP_COMMAND in resolution.error
    assert "installed/enabled" not in resolution.error
    for phrase in ("direnv", ".envrc", "terminal", "not visible"):
        assert phrase not in resolution.error
    for value in [*(own_env or {}).values(), *outside.values()]:
        assert value not in resolution.error


def test_resolve_latest_trace_id_no_latest_trace_id_field(tmp_path: Path) -> None:
    session_id = "abc-123"
    projects_dir = tmp_path / "projects"
    transcript = make_transcript(projects_dir, "-Users-op-myrepo", session_id)
    # entry exists (e.g. teammate-only entry) but the field is absent
    state_file = make_state_file(tmp_path, {state_key(session_id, transcript): {"turn_count": 3}})

    resolution = hurt.resolve_latest_trace_id(
        session_id, projects_dir=projects_dir, state_file=state_file
    )
    assert not resolution.ok
    assert "latest_trace_id" in resolution.error.lower()


def test_resolve_latest_trace_id_null_latest_trace_id_field(tmp_path: Path) -> None:
    session_id = "abc-123"
    projects_dir = tmp_path / "projects"
    transcript = make_transcript(projects_dir, "-Users-op-myrepo", session_id)
    state_file = make_state_file(tmp_path, {state_key(session_id, transcript): {"latest_trace_id": None}})

    resolution = hurt.resolve_latest_trace_id(
        session_id, projects_dir=projects_dir, state_file=state_file
    )
    assert not resolution.ok


# ----------------- run(): queuing behaviour -----------------

def test_run_queues_nothing_when_trace_unresolved(
    monkeypatch: pytest.MonkeyPatch, pending_paths: tuple[Path, Path]
) -> None:
    pending_file, _ = pending_paths
    monkeypatch.setattr(
        hurt, "resolve_latest_trace_id", lambda session_id, **_: hurt.Resolution(error="no dice")
    )

    exit_code = hurt.run(["--category", "other", "--comment", "it broke"], env={"CLAUDE_CODE_SESSION_ID": "s"})

    assert exit_code == 1
    assert not pending_file.exists()


def test_run_queues_a_well_formed_record(
    monkeypatch: pytest.MonkeyPatch, pending_paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    pending_file, _ = pending_paths
    monkeypatch.setattr(
        hurt, "resolve_latest_trace_id", lambda session_id, **_: hurt.Resolution(trace_id="tid-123")
    )

    exit_code = hurt.run(
        ["--category", "stalled-loop", "--comment", "went in circles"],
        env={"CLAUDE_CODE_SESSION_ID": "sess-1"},
    )

    assert exit_code == 0
    assert "attaches it once that trace is in Langfuse" in capsys.readouterr().out
    records = json.loads(pending_file.read_text(encoding="utf-8"))
    assert len(records) == 1
    record = records[0]
    assert record["trace_id"] == "tid-123"
    assert record["category"] == "stalled-loop"
    assert record["comment"] == "went in circles"
    assert record["session_id"] == "sess-1"
    assert isinstance(record["id"], str) and record["id"]
    assert isinstance(record["created"], str) and record["created"]
    # No trace or transcript content beyond what the operator typed: the
    # record is exactly these six fields, nothing more.
    assert set(record.keys()) == {"id", "trace_id", "category", "comment", "created", "session_id"}


def test_run_appends_without_disturbing_earlier_queued_hurts(
    monkeypatch: pytest.MonkeyPatch, pending_paths: tuple[Path, Path]
) -> None:
    pending_file, _ = pending_paths
    monkeypatch.setattr(
        hurt, "resolve_latest_trace_id", lambda session_id, **_: hurt.Resolution(trace_id="tid-1")
    )
    hurt.run(["--category", "other", "--comment", "first"], env={"CLAUDE_CODE_SESSION_ID": "s"})

    monkeypatch.setattr(
        hurt, "resolve_latest_trace_id", lambda session_id, **_: hurt.Resolution(trace_id="tid-2")
    )
    hurt.run(["--category", "ci-escape", "--comment", "second"], env={"CLAUDE_CODE_SESSION_ID": "s"})

    records = json.loads(pending_file.read_text(encoding="utf-8"))
    assert [r["comment"] for r in records] == ["first", "second"]
    assert len({r["id"] for r in records}) == 2


def test_append_pending_hurt_creates_a_missing_state_dir(tmp_path: Path) -> None:
    state_dir = tmp_path / "not-yet-created" / "state"
    pending_file = state_dir / "hurt_pending.json"
    lock_file = state_dir / "hurt_pending.lock"

    hurt.append_pending_hurt(
        trace_id="tid",
        category="other",
        comment="x",
        session_id="s",
        pending_file=pending_file,
        lock_file=lock_file,
    )

    assert json.loads(pending_file.read_text(encoding="utf-8"))


def test_load_pending_hurts_tolerates_missing_and_corrupt_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    assert hurt.load_pending_hurts(missing) == []

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert hurt.load_pending_hurts(corrupt) == []


def test_a_state_file_that_is_not_json_queues_nothing_and_says_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pending_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    pending_file, _ = pending_paths
    session_id = "abc-123"
    projects_dir = tmp_path / "projects"
    make_transcript(projects_dir, "-Users-op-myrepo", session_id)
    state_file = tmp_path / "langfuse_state.json"
    state_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(hurt, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(hurt, "STATE_FILE", state_file)

    exit_code = hurt.run(
        ["--category", "other", "--comment", "x"],
        env={"CLAUDE_CODE_SESSION_ID": session_id, "LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"},
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert out.startswith(f"/hurt: cannot read {state_file}: ")
    assert out.rstrip().endswith("Nothing queued.")
    assert not pending_file.exists()


def test_without_a_state_file_or_keys_it_points_at_the_repos_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pending_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    repo: Path,
    session_in: Any,
) -> None:
    session_in(repo)
    pending_file, _ = pending_paths
    session_id = "abc-123"
    projects_dir = tmp_path / "projects"
    make_transcript(projects_dir, "-Users-op-myrepo", session_id)
    monkeypatch.setattr(hurt, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(hurt, "STATE_FILE", tmp_path / "never-written" / "langfuse_state.json")

    exit_code = hurt.run(["--category", "other", "--comment", "x"], env={"CLAUDE_CODE_SESSION_ID": session_id})

    assert exit_code == 1
    out = capsys.readouterr().out
    assert ".claude/settings.local.json" in out
    assert SETUP_COMMAND in out
    assert not pending_file.exists()


def test_a_broken_repo_settings_file_queues_nothing_and_names_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pending_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    repo: Path,
    session_in: Any,
) -> None:
    pending_file, _ = pending_paths
    session_id = "abc-123"
    projects_dir = tmp_path / "projects"
    make_transcript(projects_dir, "-Users-op-myrepo", session_id)
    monkeypatch.setattr(hurt, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(hurt, "STATE_FILE", tmp_path / "never-written" / "langfuse_state.json")
    settings = repo / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text("{not json", encoding="utf-8")
    session_in(repo)

    exit_code = hurt.run(["--category", "other", "--comment", "x"], env={"CLAUDE_CODE_SESSION_ID": session_id})

    assert exit_code == 1
    out = capsys.readouterr().out
    assert out.startswith(f"/hurt: cannot read {settings} as JSON: ")
    assert out.rstrip().endswith("Nothing queued.")
    assert not pending_file.exists()


# ----------------- out-of-taxonomy rejection -----------------

def test_out_of_taxonomy_category_rejected() -> None:
    with pytest.raises(SystemExit):
        hurt.parse_args(["--category", "vibes-off", "--comment", "x"])


@pytest.mark.parametrize("category", hurt.CATEGORIES)
def test_every_taxonomy_category_accepted(category: str) -> None:
    args = hurt.parse_args(["--category", category, "--comment", "x"])
    assert args.category == category
