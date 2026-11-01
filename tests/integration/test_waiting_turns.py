"""Turns wait on disk until Langfuse takes them, and a background sender delivers them.

These tests run hooks/langfuse_hook.py as its own process with the real Langfuse SDK,
against a local stand-in for Langfuse, because tests/conftest.py replaces the SDK
with stubs inside the pytest process.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pytest
from langfuse_standin import KEYS_A, FakeLangfuse, Machine, free_port, wait_until
from waiting_turns import Target, send_waiting_turns, write_waiting_batch


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "transcripts"
SECRETS_IN_FIXTURE = [
    "sk-ant-api03-FAKEfakeFAKEfake0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "s3cret",
    "two words",
    "xoxb-FAKE-test-token",
    "fakeBearerToken0123",
    "ghp_FAKEfake0123456789_FAKEfake0123456789",
]


@pytest.fixture
def servers() -> Any:
    started: List[FakeLangfuse] = []

    def start(port: int, **kwargs: Any) -> FakeLangfuse:
        server = FakeLangfuse(port, **kwargs)
        started.append(server)
        return server

    yield start
    for server in started:
        server.stop()


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    return Machine(tmp_path)


def turn_rows(session_id: str, index: int) -> List[Dict[str, Any]]:
    """One finished turn that starts at minute `index`."""
    return [
        {
            "type": "user",
            "timestamp": f"2026-01-01T00:{index:02d}:00.000Z",
            "sessionId": session_id,
            "uuid": f"user-{index}",
            "message": {"role": "user", "content": f"Question {index}"},
        },
        {
            "type": "assistant",
            "timestamp": f"2026-01-01T00:{index:02d}:02.000Z",
            "sessionId": session_id,
            "uuid": f"assistant-{index}",
            "requestId": f"req-{index}",
            "message": {
                "id": f"msg-{index}",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": f"Answer {index}"}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        },
    ]


def append_rows(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def turn_name(session_id: str, turn: int) -> str:
    return f"Claude Code - Turn {turn} ({session_id})"


def write_waiting_file(path: Path, port: int, body: bytes, age_days: float = 0) -> None:
    """A waiting batch for the Langfuse stand-in on `port`, stored `age_days` before now."""
    path.write_text(
        json.dumps(
            {
                "host": f"http://127.0.0.1:{port}",
                "public_key": KEYS_A[0],
                "secret_key": KEYS_A[1],
                "body": base64.b64encode(body).decode("ascii"),
            }
        ),
        encoding="utf-8",
    )
    stored_at = time.time() - age_days * 24 * 60 * 60
    os.utime(path, (stored_at, stored_at))


def nanoseconds(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()) * 1_000_000_000


# ----------------- Criteria -----------------

def test_a_turn_that_ended_offline_arrives_at_its_own_time_when_the_next_turn_ends(
    machine: Machine, servers: Any
) -> None:
    port = free_port()
    session = "s-offline"
    transcript = machine.tmp_path / "transcript.jsonl"
    append_rows(transcript, turn_rows(session, 3))

    machine.run_hook("Stop", session, transcript, port)
    assert machine.waiting_files(), "the offline turn is not waiting on disk"

    server = servers(port)
    append_rows(transcript, turn_rows(session, 4))
    machine.run_hook("Stop", session, transcript, port)

    wait_until(lambda: not machine.waiting_files() and len(server.trace_names()) >= 2, "both turns sent")
    assert sorted(server.trace_names()) == [turn_name(session, 1), turn_name(session, 2)]
    starts = server.trace_starts()
    assert starts[turn_name(session, 1)] == nanoseconds("2026-01-01T00:03:00.000Z")
    assert starts[turn_name(session, 2)] == nanoseconds("2026-01-01T00:04:00.000Z")


def test_a_turn_that_got_an_error_answer_arrives_once_langfuse_answers_again(
    machine: Machine, servers: Any
) -> None:
    port = free_port()
    server = servers(port, status=500)
    session = "s-error"
    transcript = machine.tmp_path / "transcript.jsonl"
    append_rows(transcript, turn_rows(session, 1))

    machine.run_hook("Stop", session, transcript, port)
    wait_until(lambda: len(server.uploads) >= 1, "the sender tried once")
    assert machine.waiting_files(), "a turn answered with 500 was dropped"

    server.status = 200
    server.uploads.clear()
    append_rows(transcript, turn_rows(session, 2))
    machine.run_hook("Stop", session, transcript, port)

    wait_until(lambda: not machine.waiting_files(), "all turns sent")
    assert sorted(server.trace_names()) == [turn_name(session, 1), turn_name(session, 2)]


def test_teammate_turns_that_could_not_be_sent_arrive_later(machine: Machine, servers: Any) -> None:
    port = free_port()
    session = "s-team"
    transcript = machine.tmp_path / "transcript.jsonl"
    append_rows(transcript, turn_rows(session, 1))
    subagents = transcript.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-mate1.meta.json").write_text(
        json.dumps({"name": "alice", "taskKind": "in_process_teammate"}), encoding="utf-8"
    )
    append_rows(subagents / "agent-mate1.jsonl", turn_rows(session, 2))

    machine.run_hook("SessionEnd", session, transcript, port)
    assert machine.waiting_files()

    server = servers(port)
    machine.run_hook("SessionEnd", session, transcript, port)

    wait_until(lambda: not machine.waiting_files(), "all turns sent")
    assert f"Teammate alice - Turn 1 ({session})" in server.trace_names()


def test_a_session_in_a_repo_without_keys_sends_the_turns_another_session_left_waiting(
    machine: Machine, servers: Any
) -> None:
    port = free_port()
    server = servers(port, status=503)
    transcript = machine.tmp_path / "transcript-a.jsonl"
    append_rows(transcript, turn_rows("s-a", 1))
    machine.run_hook("Stop", "s-a", transcript, port)
    wait_until(lambda: len(server.uploads) >= 1, "the sender tried once")

    server.status = 200
    server.uploads.clear()
    other = machine.tmp_path / "transcript-b.jsonl"
    append_rows(other, turn_rows("s-b", 1))
    machine.run_hook("Stop", "s-b", other, port=None, keys=None)

    wait_until(lambda: not machine.waiting_files(), "session A's turn sent")
    assert server.trace_names() == [turn_name("s-a", 1)]
    headers, _ = server.uploads[0]
    expected_auth = base64.b64encode(f"{KEYS_A[0]}:{KEYS_A[1]}".encode()).decode()
    assert headers["authorization"] == f"Basic {expected_auth}"
    assert headers["content-type"] == "application/x-protobuf"
    assert headers["x-langfuse-public-key"] == KEYS_A[0]
    assert headers["x-langfuse-sdk-name"] == "python"
    assert headers["x-langfuse-sdk-version"]
    assert headers["x-langfuse-ingestion-version"] == "4"


def test_a_turn_sent_while_langfuse_answers_normally_arrives_once(machine: Machine, servers: Any) -> None:
    port = free_port()
    server = servers(port)
    session = "s-normal"
    transcript = machine.tmp_path / "transcript.jsonl"
    append_rows(transcript, turn_rows(session, 1))

    machine.run_hook("Stop", session, transcript, port)
    wait_until(lambda: not machine.waiting_files(), "the turn sent")
    machine.run_hook("Stop", session, transcript, port)
    time.sleep(2)

    assert server.trace_names() == [turn_name(session, 1)]


def test_turns_rejected_for_their_keys_are_dropped(machine: Machine, servers: Any) -> None:
    port = free_port()
    server = servers(port, status=401)
    transcript = machine.tmp_path / "transcript.jsonl"
    append_rows(transcript, turn_rows("s-rejected", 1))

    machine.run_hook("Stop", "s-rejected", transcript, port)

    wait_until(lambda: len(server.uploads) >= 1 and not machine.waiting_files(), "the rejected turn dropped")


def test_turns_answered_403_wait_and_arrive_once_the_address_takes_them(machine: Machine, servers: Any) -> None:
    port = free_port()
    server = servers(port, status=403)
    body = b"a batch"
    write_waiting_batch(machine.waiting_dir, Target(f"http://127.0.0.1:{port}", *KEYS_A), body)

    blocked = send_waiting_turns(machine.waiting_dir, "4.2.0", lambda line: None)

    assert blocked == 0
    assert len(machine.waiting_files()) == 1, "a turn answered with 403 was dropped"

    server.status = 200
    server.uploads.clear()
    sent = send_waiting_turns(machine.waiting_dir, "4.2.0", lambda line: None)

    assert sent == 1
    assert [upload_body for _, upload_body in server.uploads] == [body]
    assert machine.waiting_files() == []


def test_fifty_waiting_turns_do_not_slow_the_hook_down(machine: Machine, tmp_path: Path) -> None:
    port = free_port()
    transcript = machine.tmp_path / "transcript.jsonl"
    append_rows(transcript, turn_rows("s-slow", 1))
    _, without_waiting = machine.run_hook("Stop", "s-slow", transcript, port)

    other = Machine(tmp_path / "other")
    other.waiting_dir.mkdir(parents=True)
    for index in range(50):
        write_waiting_file(other.waiting_dir / f"{index:04d}.json", port, b"x" * 20_000)
    other_transcript = other.tmp_path / "transcript.jsonl"
    append_rows(other_transcript, turn_rows("s-slow", 1))
    _, with_waiting = other.run_hook("Stop", "s-slow", other_transcript, port)

    assert with_waiting - without_waiting < 1.0


def test_the_hook_does_not_wait_for_the_sender_and_the_sender_outlives_it(
    machine: Machine, servers: Any
) -> None:
    delay_s = 10.0
    port = free_port()
    server = servers(port, delay_s=delay_s)
    session = "s-slow-lf"
    transcript = machine.tmp_path / "transcript.jsonl"
    append_rows(transcript, turn_rows(session, 1))

    _, elapsed = machine.run_hook("Stop", session, transcript, port)

    assert elapsed < delay_s
    wait_until(lambda: not machine.waiting_files(), "the sender finished after the hook exited")
    assert server.trace_names() == [turn_name(session, 1)]


def test_waiting_turns_hold_secrets_only_masked(machine: Machine, tmp_path: Path) -> None:
    fixture = tmp_path / "secrets_turn"
    shutil.copytree(FIXTURES / "secrets_turn", fixture)

    machine.run_hook("Stop", "s-secrets", fixture / "transcript.jsonl", free_port())

    bodies = [
        base64.b64decode(json.loads(path.read_text(encoding="utf-8"))["body"])
        for path in machine.waiting_files()
    ]
    assert any(b"[REDACTED]" in body for body in bodies)
    for body in bodies:
        for secret in SECRETS_IN_FIXTURE:
            assert secret.encode("utf-8") not in body, f"{secret!r} is in a waiting turn"


def test_the_turns_are_on_disk_before_the_delivery_check_starts(machine: Machine, servers: Any) -> None:
    port = free_port()
    on_disk_at_probe: List[bool] = []
    servers(port, on_probe=lambda: on_disk_at_probe.append(bool(machine.waiting_files())))
    transcript = machine.tmp_path / "transcript.jsonl"
    append_rows(transcript, turn_rows("s-probe", 1))

    machine.run_hook("SessionEnd", "s-probe", transcript, port)

    assert on_disk_at_probe == [True]


def test_a_waiting_file_that_cannot_be_read_does_not_hold_back_the_others(
    machine: Machine, servers: Any
) -> None:
    port = free_port()
    server = servers(port)
    machine.waiting_dir.mkdir(parents=True)
    broken = machine.waiting_dir / "0000-empty.json"
    broken.write_text("", encoding="utf-8")
    body = b"a batch"
    write_waiting_file(machine.waiting_dir / "0001-good.json", port, body)
    log: List[str] = []

    sent = send_waiting_turns(machine.waiting_dir, "4.2.0", log.append)

    assert sent == 1
    assert [upload_body for _, upload_body in server.uploads] == [body]
    assert machine.waiting_files() == [broken]
    assert any("0000-empty.json" in line for line in log)


def test_a_turn_stored_more_than_30_days_ago_is_dropped_without_being_sent(
    machine: Machine, servers: Any
) -> None:
    port = free_port()
    server = servers(port)
    machine.waiting_dir.mkdir(parents=True)
    write_waiting_file(machine.waiting_dir / "0000-old.json", port, b"an old batch", age_days=31)
    log: List[str] = []

    sent = send_waiting_turns(machine.waiting_dir, "4.2.0", log.append)

    assert sent == 0
    assert server.uploads == []
    assert machine.waiting_files() == []
    assert any("0000-old.json" in line for line in log)


def test_a_turn_stored_less_than_30_days_ago_is_still_sent(machine: Machine, servers: Any) -> None:
    port = free_port()
    server = servers(port)
    machine.waiting_dir.mkdir(parents=True)
    body = b"a batch from last month"
    write_waiting_file(machine.waiting_dir / "0000-recent.json", port, body, age_days=29)

    sent = send_waiting_turns(machine.waiting_dir, "4.2.0", lambda _: None)

    assert sent == 1
    assert [upload_body for _, upload_body in server.uploads] == [body]
    assert machine.waiting_files() == []
