"""/hurt and the hook take the same lock on the /hurt queue, on every system: a note
typed while the hook works on the queue waits for it and is not lost."""

from __future__ import annotations

import io
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import hurt
import hurt_annotations
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
    return hurt.run(["--category", "other"], stdin=io.BytesIO(comment.encode("utf-8")), env={"CLAUDE_CODE_SESSION_ID": "s"})


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


def test_a_hurt_typed_while_the_hook_waits_on_a_silent_langfuse_is_queued(
    queue: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, silent_langfuse: str
) -> None:
    queue_file, lock_file = queue
    answer_timeout_s = 1.0
    real_client = hurt_annotations.LangfuseClient
    monkeypatch.setattr(hurt_annotations, "LangfuseClient", lambda config: real_client(config, timeout=answer_timeout_s))
    # /hurt waits for two of the hook's Langfuse waits, not for three.
    monkeypatch.setattr(hurt, "HURT_LOCK_TIMEOUT_S", 2 * answer_timeout_s)
    pending_file = tmp_path / "state" / "hurt_pending.json"
    created = datetime.now(timezone.utc).isoformat()
    pending_file.write_text(
        json.dumps(
            [
                {"id": f"hurt-{n}", "trace_id": f"tid-{n}", "category": "other", "comment": "queued", "created": created}
                for n in range(3)
            ]
        ),
        encoding="utf-8",
    )
    config = SimpleNamespace(host=silent_langfuse, public_key="pk", secret_key="sk")
    holding = threading.Event()

    def hook_attaches_queued_hurts() -> None:
        with FileLock(lock_file):
            holding.set()
            hurt_annotations.process_pending_hurts(config, pending_file)

    hook = threading.Thread(target=hook_attaches_queued_hurts)
    hook.start()
    holding.wait(timeout=10)
    exit_code = run_hurt("typed while the reply ends")
    hook.join(timeout=30)

    assert exit_code == 0
    records = json.loads(queue_file.read_text(encoding="utf-8"))
    assert "typed while the reply ends" in [record["comment"] for record in records]
