#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Queues a /hurt note for the trace of the turn before the one it runs in.

    hurt.py --category <category> [--session-id <id>] < <note>

    --category    one of hurt_annotations.CATEGORIES
    --session-id  the session; defaults to CLAUDE_CODE_SESSION_ID
    stdin         the developer's own words, as UTF-8; stored as given, less one
                  line end at its end
    reads         the repo's own .claude/settings.local.json, as the hook finds it
                  from CLAUDE_PROJECT_DIR or the working directory: to store which
                  Langfuse project the note is for, or to say why nothing was queued
    stdout        one line: what was queued, or why nothing was
    exit          0 when queued; 1 when nothing was queued; 2 on a wrong argument
                  or an empty stdin
    writes        appends one entry to ~/.claude/state/hurt_unresolved.json, under
                  the lock the hook holds while it works on the /hurt queues. The
                  hook gives the entry its trace once it has built the turn before
                  this one, and attaches it on a later run.

Never contacts Langfuse and needs no Langfuse keys.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Mapping, Optional

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
)
from state_files import (  # noqa: E402
    HURT_LOCK_TIMEOUT_S,
    HURT_PENDING_LOCK_FILE,
    HURT_UNRESOLVED_FILE,
    FileInUseError,
    FileLock,
)

NO_KEYS = (
    f"This repo's {REPO_SETTINGS_FILE.as_posix()} holds no Langfuse keys, so this session "
    f"is not traced. To add keys, run {SETUP_COMMAND}."
)


def append_unresolved_hurt(
    *,
    category: str,
    comment: str,
    session_id: str,
    target: str,
    unresolved_file: Path,
    lock_file: Path,
    timeout_s: float,
) -> None:
    """Queues one note for the hook to give a trace. Never contacts Langfuse; never
    removes or inspects other entries.

    target: hurt_annotations.queue_target() of the session's Langfuse project, stored
    so only a hook run with those keys attempts it.
    Raises TimeoutError when the hook holds the queue for longer than timeout_s, and
    FileInUseError when another program keeps unresolved_file open for too long.
    """
    record = {
        "id": str(uuid.uuid4()),
        "category": category,
        "comment": comment,
        "created": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "target": target,
    }
    with FileLock(lock_file, timeout_s=timeout_s):
        records = load_pending_hurts(unresolved_file)
        records.append(record)
        save_pending_hurts(unresolved_file, records)


def read_queue_target() -> Optional[str]:
    """queue_target() of the keys and address in the repo's own settings file, found
    from CLAUDE_PROJECT_DIR or else the working directory; None when it holds no keys.
    Raises SettingsFileError."""
    env = repo_settings_env(find_checkout(project_folder(None)))
    if not env_holds_keys(env):
        return None
    return queue_target(read_env_host(env), read_env_keys(env)[0])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Queue a /hurt note for the trace of the turn before this one.")
    parser.add_argument("--category", required=True, choices=CATEGORIES)
    parser.add_argument("--session-id", default=None, help="Override CLAUDE_CODE_SESSION_ID (for testing).")
    return parser.parse_args(argv)


def run(argv: list[str], *, stdin: BinaryIO, env: Optional[Mapping[str, str]] = None) -> int:
    """stdin: the note, as stdin in this module's header describes it."""
    env = env if env is not None else os.environ
    args = parse_args(argv)
    # The line end a shell here-document puts after the note.
    note = stdin.read().decode("utf-8").removesuffix("\n")
    if not note:
        print("/hurt: no note was given. Nothing queued.")
        return 2

    session_id = args.session_id or env.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        print("/hurt: No session id available (CLAUDE_CODE_SESSION_ID is unset). Nothing queued.")
        return 1
    try:
        target = read_queue_target()
    except SettingsFileError as e:
        print(f"/hurt: {e}. Nothing queued.")
        return 1
    if target is None:
        print(f"/hurt: {NO_KEYS} Nothing queued.")
        return 1

    try:
        append_unresolved_hurt(
            category=args.category,
            comment=note,
            session_id=session_id,
            target=target,
            unresolved_file=HURT_UNRESOLVED_FILE,
            lock_file=HURT_PENDING_LOCK_FILE,
            timeout_s=HURT_LOCK_TIMEOUT_S,
        )
    except TimeoutError:
        print(
            f"/hurt: the queue stayed busy for {HURT_LOCK_TIMEOUT_S:g} s while the hook sent "
            "earlier notes to Langfuse. Nothing queued. Run /hurt again."
        )
        return 1
    except FileInUseError as e:
        print(f"/hurt: {e}. Nothing queued. Run /hurt again.")
        return 1

    print(
        f"/hurt: queued '{args.category}' for the turn before this one. "
        "mate-record's hook attaches it to that turn's trace once the trace is in Langfuse."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], stdin=sys.stdin.buffer))
