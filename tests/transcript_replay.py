"""Replays a recorded Claude Code session Stop by Stop through hooks/langfuse_hook.py, and
counts the model calls that each reply of the session became.

    result = replay_session(session, workdir)

    session   a session's transcript, <session id>.jsonl, with its agents' transcripts in
              <session id>/subagents/ beside it; it is only read
    workdir   an empty folder the replay may fill; the hook runs on copies there
    result    a ReplayResult: every reply, and how many model calls each became

The hook runs once at each recorded Stop, seeing the transcripts only as far as they were
written then, and once more at the session's end. Raises ReplayError when a transcript
cannot be read, or when a model call matches no reply or more than one. Fails the
running pytest test when the hook exits non-zero or its turns are not all sent within
120 seconds.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from langfuse_standin import KEYS_A, FakeLangfuse, Machine, free_port, spans_in, wait_until
from transcript_files import agent_folder, transcript_lines


STOP_SUBTYPE = "stop_hook_summary"
SEND_TIMEOUT_S = 120.0


class ReplayError(Exception):
    pass


@dataclass(frozen=True)
class ReplyId:
    """A reply: its transcript file, relative to the session's folder, and its message id."""

    file: str
    message_id: str


@dataclass
class Reply:
    line: int
    timestamps_ms: Set[int] = field(default_factory=set)
    tool_call_ids: Set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ModelCall:
    end_ms: int
    tool_call_ids: FrozenSet[str]


@dataclass
class ReplayResult:
    replies: Dict[ReplyId, Reply]
    counts: Dict[ReplyId, int]


@dataclass
class Transcript:
    """One transcript file: its path relative to the session's folder, its lines as
    written, and those lines parsed."""

    name: str
    lines: List[bytes]
    rows: List[dict]


def timestamp_ms(value: str) -> int:
    return round(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def read_transcript(path: Path, folder: Path) -> Transcript:
    name = path.relative_to(folder).as_posix()
    lines = transcript_lines(path)
    rows = []
    for number, line in enumerate(lines, start=1):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ReplayError(f"{name} line {number} is not JSON: {error}") from error
    return Transcript(name, lines, rows)


def agent_transcript_paths(session: Path) -> List[Path]:
    folder = agent_folder(session)
    return sorted(folder.glob("*.jsonl")) if folder.is_dir() else []


def meta_path(agent_transcript: Path) -> Path:
    return agent_transcript.with_name(agent_transcript.name[: -len(".jsonl")] + ".meta.json")


# ----------------- Replies and model calls -----------------

def replies_in(transcript: Transcript) -> Tuple[Dict[ReplyId, Reply], Set[int]]:
    """The transcript's replies, and the timestamps of its API error rows."""
    replies: Dict[ReplyId, Reply] = {}
    api_errors: Set[int] = set()
    for number, row in enumerate(transcript.rows, start=1):
        if row.get("type") != "assistant":
            continue
        if row.get("isApiErrorMessage") is True:
            api_errors.add(timestamp_ms(row["timestamp"]))
            continue
        message = row.get("message") or {}
        message_id = message.get("id")
        if not message_id:
            raise ReplayError(f"{transcript.name} line {number}: an assistant row without a message id")
        reply = replies.setdefault(ReplyId(transcript.name, message_id), Reply(line=number))
        reply.timestamps_ms.add(timestamp_ms(row["timestamp"]))
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                reply.tool_call_ids.add(block["id"])
    return replies, api_errors


def model_calls_in(server: FakeLangfuse) -> List[ModelCall]:
    calls = []
    for _, body in server.uploads:
        for span in spans_in(body):
            attributes = {attribute.key: attribute.value.string_value for attribute in span.attributes}
            if attributes.get("langfuse.observation.type") != "generation":
                continue
            output = json.loads(attributes.get("langfuse.observation.output") or "{}")
            tool_call_ids = frozenset(call["id"] for call in output.get("tool_calls") or [])
            calls.append(ModelCall(round(span.end_time_unix_nano / 1_000_000), tool_call_ids))
    return calls


def count_model_calls(
    replies: Dict[ReplyId, Reply], api_errors: Set[int], calls: List[ModelCall]
) -> Dict[ReplyId, int]:
    """How many model calls each reply became. A call without tool calls that ends at an
    API error row's timestamp is left out. Raises ReplayError for a call that matches no
    reply, or more than one."""
    counts = {reply_id: 0 for reply_id in replies}
    for call in calls:
        if not call.tool_call_ids and call.end_ms in api_errors:
            continue
        if call.tool_call_ids:
            matches = [rid for rid, reply in replies.items() if call.tool_call_ids <= reply.tool_call_ids]
        else:
            matches = [rid for rid, reply in replies.items() if call.end_ms in reply.timestamps_ms]
        if len(matches) != 1:
            raise ReplayError(
                f"a model call ending at {call.end_ms} ms with tool calls {sorted(call.tool_call_ids)} "
                f"matches {len(matches)} replies"
            )
        counts[matches[0]] += 1
    return counts


# ----------------- Report -----------------

def fault_of(count: int) -> Optional[str]:
    """"lost" for 0 model calls, "doubled" for 2 or more, None for exactly 1."""
    if count == 0:
        return "lost"
    if count >= 2:
        return "doubled"
    return None


def describe(prefix: str, reply_id: ReplyId, reply: Reply, count: int) -> str:
    calls = "1 model call" if count == 1 else f"{count} model calls"
    fault = fault_of(count)
    return f"{prefix}{reply_id.file} line {reply.line}, reply {reply_id.message_id}: " + (
        f"{fault} ({calls})" if fault else calls
    )


def fault_lines(
    result: ReplayResult, known_faults: Dict[Tuple[str, str], str], prefix: str = ""
) -> List[str]:
    """One line per reply that is not exactly one model call and is not listed with that
    fault in known_faults, and one per listed reply whose fault no longer happens or
    changed. known_faults maps (file, message id) to "lost" or "doubled"."""
    lines = []
    for reply_id, reply in result.replies.items():
        count = result.counts[reply_id]
        fault = fault_of(count)
        known = known_faults.get((reply_id.file, reply_id.message_id))
        if fault == known:
            continue
        line = describe(prefix, reply_id, reply, count)
        if known is None:
            lines.append(line)
        elif fault is None:
            lines.append(f"{line}; known fault no longer happens: remove its entry")
        else:
            lines.append(f"{line}; listed as {known}")
    return lines


# ----------------- Running the hook -----------------

class KeysInEnvironmentMachine(Machine):
    """A Machine that also gives the hook the keys and address in its environment."""

    def __init__(self, tmp_path: Path, port: int) -> None:
        super().__init__(tmp_path)
        self.port = port

    def env(self) -> Dict[str, str]:
        env = super().env()
        env.update(
            LANGFUSE_PUBLIC_KEY=KEYS_A[0],
            LANGFUSE_SECRET_KEY=KEYS_A[1],
            LANGFUSE_BASE_URL=f"http://127.0.0.1:{self.port}",
        )
        return env


@dataclass
class StopPoint:
    main_rows: int
    moment_ms: int


def stop_points(main: Transcript) -> List[StopPoint]:
    """Each row where the session ran its Stop hooks: the moment the hooks started, and
    the rows before it that were written by that moment."""
    points = []
    for index, row in enumerate(main.rows):
        if row.get("type") == "system" and row.get("subtype") == STOP_SUBTYPE:
            durations = [info.get("durationMs") or 0 for info in row.get("hookInfos") or []]
            moment_ms = timestamp_ms(row["timestamp"]) - max(durations, default=0)
            points.append(StopPoint(min(index, rows_written_by(main, moment_ms)), moment_ms))
    return points


def rows_written_by(transcript: Transcript, moment_ms: int) -> int:
    """How many of the transcript's rows were written by moment_ms: those before its first
    row stamped later. A row without a timestamp goes with the row before it."""
    for index, row in enumerate(transcript.rows):
        stamp = row.get("timestamp")
        if stamp and timestamp_ms(stamp) > moment_ms:
            return index
    return len(transcript.rows)


def write_lines(path: Path, lines: List[bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(line + b"\n" for line in lines))


def replay_session(session: Path, workdir: Path) -> ReplayResult:
    folder = session.parent
    main = read_transcript(session, folder)
    agents = [(read_transcript(path, folder), meta_path(path)) for path in agent_transcript_paths(session)]

    replies, api_errors = replies_in(main)
    for agent, _ in agents:
        agent_replies, agent_api_errors = replies_in(agent)
        replies.update(agent_replies)
        api_errors |= agent_api_errors

    target_folder = workdir / "project"
    target = target_folder / session.name

    def write_session(main_rows: int, moment_ms: Optional[int]) -> None:
        write_lines(target, main.lines[:main_rows])
        for agent, meta in agents:
            written = len(agent.rows) if moment_ms is None else rows_written_by(agent, moment_ms)
            if written == 0:
                continue
            write_lines(target_folder / agent.name, agent.lines[:written])
            if meta.exists():
                shutil.copyfile(meta, target_folder / meta.relative_to(folder))

    port = free_port()
    server = FakeLangfuse(port)
    try:
        machine = KeysInEnvironmentMachine(workdir / "machine", port)
        session_id = session.stem
        for point in stop_points(main):
            write_session(point.main_rows, point.moment_ms)
            machine.run_hook("Stop", session_id, target, port)
        write_session(len(main.rows), None)
        machine.run_hook("SessionEnd", session_id, target, port)
        wait_until(lambda: not machine.waiting_files(), "every turn sent", timeout_s=SEND_TIMEOUT_S)
        calls = model_calls_in(server)
    finally:
        server.stop()
    return ReplayResult(replies, count_model_calls(replies, api_errors, calls))
