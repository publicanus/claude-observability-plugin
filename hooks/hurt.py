#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Queues a /hurt note for the trace of this session's latest turn.

    hurt.py --category <category> --comment <text> [--session-id <id>]

    --category    one of hurt_annotations.CATEGORIES
    --comment     the developer's own words, stored as given
    --session-id  the session; defaults to CLAUDE_CODE_SESSION_ID
    reads         ~/.claude/projects/*/<session>.jsonl, to find the session's
                  transcript, and ~/.claude/state/langfuse_state.json, where
                  the hook keeps each session's latest trace id; and the repo's
                  own .claude/settings.local.json, as the hook finds it from
                  CLAUDE_PROJECT_DIR or the working directory: to store which
                  Langfuse project the note is for, or to say why nothing was
                  queued
    stdout        one line: what was queued, or why nothing was
    exit          0 when queued; 1 when nothing was queued; 2 on a wrong argument
    writes        appends one entry to ~/.claude/state/hurt_pending.json, under
                  the lock the hook holds while it empties that queue. The hook
                  attaches the entry to the trace on a later run.

Never contacts Langfuse and needs no Langfuse keys.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hurt_annotations import CATEGORIES, load_pending_hurts, queue_target, save_pending_hurts  # noqa: E402
from repo_checkout import find_checkout, project_folder  # noqa: E402
from settings_file import (  # noqa: E402
    REPO_SETTINGS_FILE,
    SETUP_COMMAND,
    SettingsFileError,
    env_holds_keys,
    read_env_host,
    read_env_keys,
    repo_settings_env,
    repo_settings_hold_keys,
)
from state_files import (  # noqa: E402
    HURT_PENDING_FILE,
    HURT_PENDING_LOCK_FILE,
    STATE_FILE,
    FileLock,
    StateFileError,
    get_session_state_key,
    read_state,
)

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# How long /hurt waits while the hook holds the queue to send earlier notes.
HURT_LOCK_TIMEOUT_S = 60.0


class Resolution:
    """Outcome of trying to find the trace to annotate."""

    def __init__(self, trace_id: Optional[str] = None, error: Optional[str] = None) -> None:
        self.trace_id = trace_id
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None and self.trace_id is not None


# ----------------- Session -> trace resolution -----------------

def find_transcript_path(session_id: str, projects_dir: Path = PROJECTS_DIR) -> Optional[Path]:
    """The session's transcript, found by its file name, which is the session id;
    None unless exactly one matches."""
    if not projects_dir.is_dir():
        return None
    matches = sorted(projects_dir.glob(f"*/{session_id}.jsonl"))
    if len(matches) != 1:
        return None
    return matches[0].resolve()


def resolve_latest_trace_id(
    session_id: Optional[str],
    *,
    projects_dir: Path = PROJECTS_DIR,
    state_file: Path = STATE_FILE,
) -> Resolution:
    """The session's latest trace id, or why there is none.

    Without a state entry, it reads the repo's own settings file of the working
    directory, as the hook does, to say why.
    Raises StateFileError when state_file exists but cannot be read, and
    SettingsFileError when the repo's settings file cannot be read.
    """
    if not session_id:
        return Resolution(error="No session id available (CLAUDE_CODE_SESSION_ID is unset).")

    transcript_path = find_transcript_path(session_id, projects_dir)
    if transcript_path is None:
        return Resolution(error="Could not locate this session's transcript.")

    key = get_session_state_key(session_id, str(transcript_path))
    entry = read_state(state_file).get(key)
    if not isinstance(entry, dict):
        if not repo_settings_hold_keys(find_checkout(project_folder(None))):
            return Resolution(
                error="No mate-record state entry for this session, and this repo's "
                f"{REPO_SETTINGS_FILE.as_posix()} holds no Langfuse keys, so this session "
                f"is not traced. To add keys, run {SETUP_COMMAND}."
            )
        return Resolution(
            error="No mate-record state entry for this session — "
            "the hook may not be installed/enabled, or no turn has been emitted yet."
        )

    trace_id = entry.get("latest_trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        return Resolution(
            error="This session has no latest_trace_id yet — the hook has not "
            "seen a turn of this session yet."
        )

    return Resolution(trace_id=trace_id)


# ----------------- Pending-hurt queue (local, no Langfuse) -----------------

def append_pending_hurt(
    *,
    trace_id: str,
    category: str,
    comment: str,
    session_id: str,
    target: Optional[str] = None,
    pending_file: Path = HURT_PENDING_FILE,
    lock_file: Path = HURT_PENDING_LOCK_FILE,
    timeout_s: float = HURT_LOCK_TIMEOUT_S,
) -> None:
    """Queues one annotation locally. Never contacts Langfuse; never removes
    or inspects other entries.

    target: hurt_annotations.queue_target() of the session's Langfuse project, stored
    so only a hook run with those keys attempts it; None stores none.
    Raises TimeoutError when the hook holds the queue for longer than timeout_s.
    """
    record = {
        "id": str(uuid.uuid4()),
        "trace_id": trace_id,
        "category": category,
        "comment": comment,
        "created": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
    }
    if target is not None:
        record["target"] = target
    with FileLock(lock_file, timeout_s=timeout_s):
        records = load_pending_hurts(pending_file)
        records.append(record)
        save_pending_hurts(pending_file, records)


# ----------------- CLI -----------------

def read_queue_target() -> Optional[str]:
    """queue_target() of the keys and address in the repo's own settings file of the
    working directory; None when it holds no keys. Raises SettingsFileError."""
    env = repo_settings_env(find_checkout(project_folder(None)))
    if not env_holds_keys(env):
        return None
    return queue_target(read_env_host(env), read_env_keys(env)[0])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Queue a /hurt annotation for the current session's latest trace.")
    parser.add_argument("--category", required=True, choices=CATEGORIES)
    parser.add_argument("--comment", required=True)
    parser.add_argument("--session-id", default=None, help="Override CLAUDE_CODE_SESSION_ID (for testing).")
    return parser.parse_args(argv)


def run(argv: list[str], *, env: Optional[Mapping[str, str]] = None) -> int:
    env = env if env is not None else os.environ
    args = parse_args(argv)

    session_id = args.session_id or env.get("CLAUDE_CODE_SESSION_ID")
    try:
        resolution = resolve_latest_trace_id(session_id, projects_dir=PROJECTS_DIR, state_file=STATE_FILE)
        target = read_queue_target() if resolution.ok else None
    except (StateFileError, SettingsFileError) as e:
        print(f"/hurt: {e}. Nothing queued.")
        return 1
    if not resolution.ok:
        print(f"/hurt: {resolution.error} Nothing queued.")
        return 1

    trace_id = resolution.trace_id
    assert trace_id is not None

    try:
        append_pending_hurt(
            trace_id=trace_id,
            category=args.category,
            comment=args.comment,
            session_id=session_id or "",
            target=target,
            pending_file=HURT_PENDING_FILE,
            lock_file=HURT_PENDING_LOCK_FILE,
            timeout_s=HURT_LOCK_TIMEOUT_S,
        )
    except TimeoutError:
        print(
            f"/hurt: the queue stayed busy for {HURT_LOCK_TIMEOUT_S:g} s while the hook sent "
            "earlier notes to Langfuse. Nothing queued. Run /hurt again."
        )
        return 1

    print(
        f"/hurt: queued '{args.category}' for trace {trace_id}. "
        "mate-record's hook attaches it once that trace is in Langfuse."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
