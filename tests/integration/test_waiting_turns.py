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
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
from waiting_turns import send_waiting_turns
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "hooks" / "langfuse_hook.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "transcripts"
TRACES_PATH = "/api/public/otel/v1/traces"
KEYS_A = ("pk-lf-test-a", "sk-lf-test-a")
SECRETS_IN_FIXTURE = [
    "sk-ant-api03-FAKEfakeFAKEfake0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "s3cret",
    "two words",
    "xoxb-FAKE-test-token",
    "fakeBearerToken0123",
    "ghp_FAKEfake0123456789_FAKEfake0123456789",
]


# ----------------- A local stand-in for Langfuse -----------------

class FakeLangfuse:
    """Answers the delivery probe with 200 and each trace upload with `status`,
    after `delay_s`. Records every trace upload as (headers, body)."""

    def __init__(
        self,
        port: int,
        status: int = 200,
        delay_s: float = 0.0,
        on_probe: Optional[Callable[[], None]] = None,
    ) -> None:
        self.status = status
        self.delay_s = delay_s
        self.on_probe = on_probe
        self.uploads: List[tuple[Dict[str, str], bytes]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if fake.on_probe is not None:
                    fake.on_probe()
                self._answer(200, b'{"data": []}')

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                time.sleep(fake.delay_s)
                status = fake.status
                if self.path == TRACES_PATH:
                    fake.uploads.append(({k.lower(): v for k, v in self.headers.items()}, body))
                self._answer(status, b"{}")

            def _answer(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def root_spans(self) -> List[Any]:
        return [span for _, body in self.uploads for span in spans_in(body) if not span.parent_span_id]

    def trace_names(self) -> List[str]:
        return [trace_name(span) for span in self.root_spans()]

    def trace_starts(self) -> Dict[str, int]:
        return {trace_name(span): span.start_time_unix_nano for span in self.root_spans()}


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


def spans_in(body: bytes) -> List[Any]:
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    return [
        span
        for resource_spans in request.resource_spans
        for scope_spans in resource_spans.scope_spans
        for span in scope_spans.spans
    ]


def trace_name(span: Any) -> str:
    names = [a.value.string_value for a in span.attributes if a.key == "langfuse.trace.name"]
    assert len(names) == 1, f"root span {span.name!r} carries no trace name"
    return names[0]


def free_port() -> int:
    """A port nothing listens on: a request to it gets no answer."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(condition: Callable[[], bool], what: str, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    while not condition():
        if time.time() > deadline:
            pytest.fail(f"not within {timeout_s}s: {what}")
        time.sleep(0.1)


# ----------------- Running the hook -----------------

class Machine:
    """One computer: a home folder, and repos whose sessions run the hook."""

    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.home.mkdir(parents=True)
        self.tmp_path = tmp_path

    @property
    def waiting_dir(self) -> Path:
        return self.home / ".claude" / "state" / "langfuse_waiting"

    def waiting_files(self) -> List[Path]:
        return sorted(self.waiting_dir.glob("*.json")) if self.waiting_dir.is_dir() else []

    def env(self, port: Optional[int], keys: Optional[tuple[str, str]] = KEYS_A) -> Dict[str, str]:
        env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("LANGFUSE_", "CC_LANGFUSE_", "CLAUDE_PLUGIN_OPTION_"))
        }
        env.update(
            HOME=str(self.home),
            USERPROFILE=str(self.home),
            GIT_CONFIG_GLOBAL=str(self.home / ".gitconfig"),
            GIT_CONFIG_NOSYSTEM="1",
        )
        if keys is not None:
            env.update(
                LANGFUSE_PUBLIC_KEY=keys[0],
                LANGFUSE_SECRET_KEY=keys[1],
                LANGFUSE_BASE_URL=f"http://127.0.0.1:{port}",
            )
        return env

    def run_hook(
        self,
        event: str,
        session_id: str,
        transcript: Path,
        port: Optional[int],
        keys: Optional[tuple[str, str]] = KEYS_A,
    ) -> tuple[subprocess.CompletedProcess, float]:
        repo = self.tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        payload = {
            "hook_event_name": event,
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(repo),
        }
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            env=self.env(port, keys),
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = time.monotonic() - started
        assert result.returncode == 0, result.stderr
        return result, elapsed


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


def test_fifty_waiting_turns_do_not_slow_the_hook_down(machine: Machine, tmp_path: Path) -> None:
    port = free_port()
    transcript = machine.tmp_path / "transcript.jsonl"
    append_rows(transcript, turn_rows("s-slow", 1))
    _, without_waiting = machine.run_hook("Stop", "s-slow", transcript, port)

    other = Machine(tmp_path / "other")
    other.waiting_dir.mkdir(parents=True)
    for index in range(50):
        (other.waiting_dir / f"{index:04d}.json").write_text(
            json.dumps(
                {
                    "host": f"http://127.0.0.1:{port}",
                    "public_key": KEYS_A[0],
                    "secret_key": KEYS_A[1],
                    "body": base64.b64encode(b"x" * 20_000).decode("ascii"),
                }
            ),
            encoding="utf-8",
        )
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
    (machine.waiting_dir / "0001-good.json").write_text(
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
    log: List[str] = []

    sent = send_waiting_turns(machine.waiting_dir, "4.2.0", log.append)

    assert sent == 1
    assert [upload_body for _, upload_body in server.uploads] == [body]
    assert machine.waiting_files() == [broken]
    assert any("0000-empty.json" in line for line in log)
