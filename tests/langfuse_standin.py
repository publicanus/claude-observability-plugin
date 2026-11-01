"""A local stand-in for Langfuse, and a machine whose sessions run hooks/langfuse_hook.py
as its own process with the real Langfuse SDK.

FakeLangfuse(port) answers on 127.0.0.1:port and records every trace upload.
Machine(tmp_path).run_hook(...) runs the hook once, as Claude Code does, with a home
folder and a repo under tmp_path; it fails the test when the hook exits non-zero.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
from conftest import write_settings
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "hooks" / "langfuse_hook.py"
TRACES_PATH = "/api/public/otel/v1/traces"
KEYS_A = ("pk-lf-test-a", "sk-lf-test-a")


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

    def env(self) -> Dict[str, str]:
        env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("LANGFUSE_", "CC_LANGFUSE_", "CLAUDE_PLUGIN_OPTION_", "CLAUDE_PROJECT_DIR"))
        }
        env.update(
            HOME=str(self.home),
            USERPROFILE=str(self.home),
            GIT_CONFIG_GLOBAL=str(self.home / ".gitconfig"),
            GIT_CONFIG_NOSYSTEM="1",
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
        # A run without keys is a session in another repo, one without a settings file.
        repo = self.tmp_path / ("repo" if keys is not None else "repo-without-keys")
        repo.mkdir(exist_ok=True)
        if keys is not None:
            write_settings(
                repo,
                {
                    "LANGFUSE_PUBLIC_KEY": keys[0],
                    "LANGFUSE_SECRET_KEY": keys[1],
                    "LANGFUSE_BASE_URL": f"http://127.0.0.1:{port}",
                },
            )
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
            env=self.env(),
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = time.monotonic() - started
        assert result.returncode == 0, result.stderr
        return result, elapsed
