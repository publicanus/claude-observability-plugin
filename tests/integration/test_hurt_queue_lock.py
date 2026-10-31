"""/hurt and the hook take the same lock on the /hurt queue, on every system: a note
typed while the hook works on the queue waits for it and is not lost."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import hurt
from conftest import write_settings
from state_files import FileLock

# Longer than the 2 s a FileLock waits by default.
HOOK_HOLDS_S = 3.0


@pytest.fixture
def queue(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, no_langfuse_env: None) -> tuple[Path, Path]:
    """The queue /hurt writes and its lock, with one earlier note, for a session in a repo with keys."""
    queue_file = tmp_path / "state" / "hurt_unresolved.json"
    lock_file = tmp_path / "state" / "hurt_pending.lock"
    queue_file.parent.mkdir()
    queue_file.write_text(json.dumps([{"id": "earlier", "comment": "placed by the hook"}]), encoding="utf-8")
    monkeypatch.setattr(hurt, "HURT_UNRESOLVED_FILE", queue_file)
    monkeypatch.setattr(hurt, "HURT_PENDING_LOCK_FILE", lock_file)
    write_settings(repo, {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"})
    monkeypatch.chdir(repo)
    return queue_file, lock_file


def run_hurt(comment: str) -> int:
    return hurt.run(["--category", "other", "--comment", comment], env={"CLAUDE_CODE_SESSION_ID": "s"})


def test_a_hurt_typed_while_the_hook_holds_the_queue_is_kept(queue: tuple[Path, Path]) -> None:
    queue_file, lock_file = queue
    exit_codes: list[int] = []

    with FileLock(lock_file):
        typed = threading.Thread(target=lambda: exit_codes.append(run_hurt("typed during the drain")))
        typed.start()
        time.sleep(HOOK_HOLDS_S)
        assert typed.is_alive(), "/hurt did not wait for the hook's lock"
        # The hook has placed the earlier note and writes the queue without it.
        queue_file.write_text("[]", encoding="utf-8")
    typed.join(timeout=30)

    assert exit_codes == [0]
    records = json.loads(queue_file.read_text(encoding="utf-8"))
    assert [record["comment"] for record in records] == ["typed during the drain"]


def test_a_hurt_that_waits_too_long_queues_nothing_and_says_so(
    queue: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    queue_file, lock_file = queue
    before = queue_file.read_text(encoding="utf-8")
    monkeypatch.setattr(hurt, "HURT_LOCK_TIMEOUT_S", 0.3)

    with FileLock(lock_file):
        exit_code = run_hurt("typed during a slow drain")

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "the queue stayed busy for 0.3 s while the hook sent earlier notes to Langfuse" in out
    assert out.rstrip().endswith("Nothing queued. Run /hurt again.")
    assert queue_file.read_text(encoding="utf-8") == before
