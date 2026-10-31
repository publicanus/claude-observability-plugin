"""Live ticket test: a /hurt after a turn stopped with Esc, or ended by an API error,
lands on that turn's trace in Langfuse.

    python3 tests/live/hurt_after_stopped_turn.py [--plugin-dir <dir>] [--keys-file <file>] [--project A|B]

    --plugin-dir  the plugin checkout to test; defaults to this repo
    --keys-file   KEY=VALUE lines with LANGFUSE_<project>_PUBLIC_KEY and
                  LANGFUSE_<project>_SECRET_KEY; defaults to
                  ~/.config/mate-record-test/keys.env. The keys are never printed.
    --project     the Langfuse Cloud EU test project, A by default
    needs         claude on the PATH, logged in; network access to Anthropic and
                  to https://cloud.langfuse.com
    stdout        one line per check, then PASS or FAIL
    exit          0 when every check passes; 1 when one fails; 2 when the test
                  cannot run (no claude, no keys)
    side effects  runs headless Claude Code sessions (model sonnet) in a temporary
                  git folder, which this machine's ~/.claude keeps transcripts and
                  hook state of; their traces in the test project are deleted at
                  the end, whatever the result

Session 1 stops turn 2 mid-reply, session 2 makes turn 2 fail through
fault_proxy.py, and session 3 stops its first turn before any reply; each then
types /mate-record:hurt.
"""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
LANGFUSE_HOST = "https://cloud.langfuse.com"
INTERRUPTED = "[Request interrupted by user]"
MODEL = "sonnet"
TURN_TIMEOUT_S = 180
SCORE_WAIT_S = 600
SCORE_POLL_S = 60
# Stops tried for session 3, in seconds after the prompt is sent: early enough that
# no reply has started, late enough that Claude Code has written the prompt row.
EARLY_STOPS_S = (1.0, 0.8, 1.5, 0.6)


class CannotRun(Exception):
    """The test cannot run on this machine; str() says why."""


# ----------------- Keys and Langfuse -----------------

def read_keys(keys_file: Path, project: str) -> Dict[str, str]:
    """The project's public and secret key from KEY=VALUE lines. Raises CannotRun."""
    if not keys_file.is_file():
        raise CannotRun(f"no keys file at {keys_file}")
    values: Dict[str, str] = {}
    for line in keys_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        values[name.strip()] = value.strip().strip("'\"")
    names = {"public": f"LANGFUSE_{project}_PUBLIC_KEY", "secret": f"LANGFUSE_{project}_SECRET_KEY"}
    missing = [name for name in names.values() if not values.get(name)]
    if missing:
        raise CannotRun(f"{keys_file} lacks {', '.join(missing)}")
    return {role: values[name] for role, name in names.items()}


class Langfuse:
    def __init__(self, keys: Dict[str, str]) -> None:
        token = base64.b64encode(f"{keys['public']}:{keys['secret']}".encode()).decode()
        self._auth = f"Basic {token}"

    def call(self, method: str, path: str, query: Optional[Dict[str, str]] = None) -> Any:
        url = LANGFUSE_HOST + path + ("?" + urllib.parse.urlencode(query) if query else "")
        request = urllib.request.Request(url, method=method, headers={"Authorization": self._auth})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
        return json.loads(body) if body else None

    def turns_of(self, session_id: str, since: datetime) -> List[Dict[str, Any]]:
        """The session's Conversational Turn root observations, each with its traceId and input."""
        result = self.call("GET", "/api/public/v2/observations", {
            "sessionId": session_id,
            "name": "Conversational Turn",
            "fields": "core,basic,io",
            "fromStartTime": iso(since - timedelta(hours=1)),
            "toStartTime": iso(datetime.now(timezone.utc) + timedelta(hours=1)),
            "limit": "100",
        })
        return result.get("data") or []

    def hurt_scores(self, trace_ids: List[str]) -> List[Dict[str, Any]]:
        if not trace_ids:
            return []
        result = self.call("GET", "/api/public/v3/scores", {
            "name": "hurt", "traceId": ",".join(trace_ids), "fields": "details,subject", "limit": "100",
        })
        return result.get("data") or []

    def delete_trace(self, trace_id: str) -> None:
        self.call("DELETE", f"/api/public/traces/{trace_id}")


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prompt_of(observation: Dict[str, Any]) -> str:
    """The user text of a turn's root observation."""
    value = observation.get("input")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return value.get("content", "") if isinstance(value, dict) else ""


def score_trace_id(score: Dict[str, Any]) -> Optional[str]:
    subject = score.get("subject") or {}
    return subject.get("id") if subject.get("kind") == "trace" else score.get("traceId")


# ----------------- Headless sessions -----------------

class Session:
    """One claude -p process; all its turns go through it."""

    def __init__(self, folder: Path, plugin_dir: Path, env: Dict[str, str]) -> None:
        self.events: List[Dict[str, Any]] = []
        self.process = subprocess.Popen(
            ["claude", "-p", "--plugin-dir", str(plugin_dir), "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose", "--model", MODEL],
            cwd=folder, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        threading.Thread(target=self._read, daemon=True).start()
        self._results = 0

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            if line.startswith("{"):
                self.events.append(json.loads(line))

    def _send(self, message: Dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _wait_for_result(self) -> None:
        self._results += 1
        deadline = time.time() + TURN_TIMEOUT_S
        while sum(1 for e in self.events if e.get("type") == "result") < self._results:
            if time.time() > deadline:
                raise RuntimeError(f"no result for turn {self._results} within {TURN_TIMEOUT_S} s")
            time.sleep(0.1)

    def turn(self, prompt: str, stop_after_s: Optional[float] = None) -> None:
        self._send({"type": "user", "message": {"role": "user", "content": prompt}})
        if stop_after_s is not None:
            time.sleep(stop_after_s)
            self._send({"type": "control_request", "request_id": f"stop-{self._results}",
                        "request": {"subtype": "interrupt"}})
        self._wait_for_result()

    def close(self) -> str:
        """Ends the process; returns the session id."""
        assert self.process.stdin is not None
        self.process.stdin.close()
        self.process.wait(timeout=120)
        return next(e["session_id"] for e in self.events if e.get("session_id"))


def transcript_rows(session_id: str) -> List[Dict[str, Any]]:
    [path] = glob.glob(str(Path.home() / ".claude" / "projects" / "*" / f"{session_id}.jsonl"))
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def text_of(row: Dict[str, Any]) -> str:
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def rows_after_prompt(rows: List[Dict[str, Any]], prompt: str) -> Optional[List[Dict[str, Any]]]:
    """The rows from the prompt's own user row up to the next user prompt row; None
    when the prompt row is not in the transcript."""
    start = next((i for i, r in enumerate(rows) if r.get("type") == "user" and text_of(r) == prompt), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(rows))
                if rows[i].get("type") == "user" and not rows[i].get("isMeta")
                and text_of(rows[i]) not in ("", INTERRUPTED) and "tool_result" not in json.dumps(rows[i]["message"])),
               len(rows))
    return rows[start:end]


def hurt_line(rows: List[Dict[str, Any]]) -> str:
    """The line /hurt's script printed, from its tool result."""
    for row in rows:
        for block in (row.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and "/hurt:" in json.dumps(block):
                content = block.get("content")
                return content if isinstance(content, str) else json.dumps(content)
    return ""


# ----------------- The test -----------------

class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, ok: bool, what: str) -> bool:
        print(f"{'ok  ' if ok else 'FAIL'} {what}", flush=True)
        self.failed += 0 if ok else 1
        return ok


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(plugin_dir: Path, keys: Dict[str, str]) -> int:
    checks = Checks()
    langfuse = Langfuse(keys)
    started = datetime.now(timezone.utc)
    session_ids: List[str] = []
    expected: Dict[str, str] = {}  # session id -> prompt of the turn the note must land on
    normal_first: Dict[str, str] = {}  # session id -> prompt of a turn that must have no note
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LANGFUSE_", "CC_LANGFUSE_", "CLAUDE_CODE_SESSION"))}

    try:
        with tempfile.TemporaryDirectory(prefix="mate-record-live-73-") as scratch:
            folder = Path(scratch).resolve()
            subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
            settings = folder / ".claude" / "settings.local.json"
            settings.parent.mkdir()
            settings.write_text(json.dumps({"env": {
                "LANGFUSE_PUBLIC_KEY": keys["public"], "LANGFUSE_SECRET_KEY": keys["secret"], "LANGFUSE_BASE_URL": LANGFUSE_HOST,
            }}), encoding="utf-8")
            port = free_port()
            proxy = subprocess.Popen([sys.executable, "-I", str(HERE / "fault_proxy.py"), str(port)])
            try:
                # Session 1: turn 2 stopped mid-reply.
                s = Session(folder, plugin_dir, env)
                s.turn("Say hi in one word.")
                s.turn("Write a 2000-word essay about rivers.", stop_after_s=10)
                s.turn("/mate-record:hurt it kept looping")
                sid = s.close()
                session_ids.append(sid)
                rows = transcript_rows(sid)
                stopped = rows_after_prompt(rows, "Write a 2000-word essay about rivers.") or []
                checks.check(any(r.get("type") == "assistant" for r in stopped)
                             and any(text_of(r) == INTERRUPTED for r in stopped)
                             and not any(r.get("subtype") == "stop_hook_summary" for r in stopped),
                             "session 1: turn 2 was stopped mid-reply, with no Stop after it")
                checks.check("/hurt: queued" in hurt_line(rows), f"session 1: /hurt queued the note ({hurt_line(rows)[:120]})")
                expected[sid] = "Write a 2000-word essay about rivers."
                normal_first[sid] = "Say hi in one word."

                # Session 2: turn 2 ends in an API error.
                s = Session(folder, plugin_dir, {**env, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}"})
                s.turn("Say hi in one word.")
                s.turn("Say yes. FAIL-THIS-TURN")
                s.turn("/mate-record:hurt the API failed")
                sid = s.close()
                session_ids.append(sid)
                rows = transcript_rows(sid)
                failed = rows_after_prompt(rows, "Say yes. FAIL-THIS-TURN") or []
                checks.check(any(r.get("isApiErrorMessage") for r in failed)
                             and not any(r.get("subtype") == "stop_hook_summary" for r in failed),
                             "session 2: turn 2 ended in an API error, with no Stop after it")
                checks.check("/hurt: queued" in hurt_line(rows), f"session 2: /hurt queued the note ({hurt_line(rows)[:120]})")
                expected[sid] = "Say yes. FAIL-THIS-TURN"
                normal_first[sid] = "Say hi in one word."

                # Session 3: the first turn stopped before any reply.
                essay = "Think very carefully, then write a long essay about mountains."
                for stop_after_s in EARLY_STOPS_S:
                    s = Session(folder, plugin_dir, env)
                    s.turn(essay, stop_after_s=stop_after_s)
                    s.turn("/mate-record:hurt it kept looping")
                    sid = s.close()
                    session_ids.append(sid)
                    rows = transcript_rows(sid)
                    stopped = rows_after_prompt(rows, essay)
                    if stopped is not None and not any(r.get("type") == "assistant" for r in stopped):
                        break
                    print(f"     session 3: stop after {stop_after_s} s gave no prompt-without-reply; trying again")
                checks.check(stopped is not None and not any(r.get("type") == "assistant" for r in stopped)
                             and any(text_of(r) == INTERRUPTED for r in stopped),
                             "session 3: the first turn was stopped before any reply")
                checks.check("/hurt: queued" in hurt_line(rows), f"session 3: /hurt queued the note ({hurt_line(rows)[:120]})")
                expected[sid] = essay
            finally:
                proxy.kill()

            # Later hook runs attach the notes once the traces are in Langfuse.
            deadline = time.time() + SCORE_WAIT_S
            while True:
                turns = {sid: langfuse.turns_of(sid, started) for sid in expected}
                trace_ids = [t["traceId"] for found in turns.values() for t in found]
                scores = langfuse.hurt_scores(trace_ids)
                if len(scores) >= len(expected) or time.time() > deadline:
                    break
                time.sleep(SCORE_POLL_S)
                poll = subprocess.run(
                    ["claude", "-p", "--plugin-dir", str(plugin_dir), "--model", MODEL, "--output-format", "json", "Say ok."],
                    cwd=folder, env=env, capture_output=True, text=True, timeout=TURN_TIMEOUT_S, check=False,
                )
                try:
                    session_ids.append(json.loads(poll.stdout)["session_id"])
                except (ValueError, KeyError):
                    print("     a poll session printed no session id")

        traces_by_prompt = {sid: {prompt_of(t): t["traceId"] for t in found} for sid, found in turns.items()}
        scored = {score_trace_id(score): score for score in scores}
        for number, (sid, prompt) in enumerate(expected.items(), 1):
            target = traces_by_prompt[sid].get(prompt)
            score = scored.get(target) if target else None
            checks.check(score is not None, f"session {number}: the hurt score is on the trace of {prompt!r}")
            if sid in normal_first:
                first = traces_by_prompt[sid].get(normal_first[sid])
                checks.check(first is not None and first not in scored, f"session {number}: turn 1 has no hurt score")
        checks.check(len(scores) == len(expected), f"exactly {len(expected)} hurt scores ({len(scores)} found)")
    finally:
        cleanup(langfuse, session_ids, started, checks)
    print("PASS" if checks.failed == 0 else f"FAIL ({checks.failed} checks failed)")
    return 0 if checks.failed == 0 else 1


def cleanup(langfuse: Langfuse, session_ids: List[str], started: datetime, checks: Checks) -> None:
    """Deletes every trace of the test's sessions, and waits until none is left."""
    deadline = time.time() + 300
    while True:
        left = [t["traceId"] for sid in session_ids for t in langfuse.turns_of(sid, started)]
        if not left or time.time() > deadline:
            break
        for trace_id in set(left):
            langfuse.delete_trace(trace_id)
        time.sleep(15)
    checks.check(not left, f"the test traces of {len(session_ids)} sessions are deleted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plugin-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--keys-file", type=Path, default=Path.home() / ".config" / "mate-record-test" / "keys.env")
    parser.add_argument("--project", choices=("A", "B"), default="A")
    args = parser.parse_args()
    try:
        if shutil.which("claude") is None:
            raise CannotRun("claude is not on the PATH")
        keys = read_keys(args.keys_file, args.project)
    except CannotRun as e:
        print(f"cannot run: {e}", file=sys.stderr)
        return 2
    return run(args.plugin_dir.resolve(), keys)


if __name__ == "__main__":
    sys.exit(main())
