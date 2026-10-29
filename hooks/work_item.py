"""The work item a turn's trace names, and the command a tool runs to name it.

Each session has one marker file in the work-items folder, `<session_id>.jsonl`.
Every `set` or `clear` appends one line to it:

  at   when the call ran, ISO 8601 with a time zone
  id   the named work item, or null for a clear

A turn carries the item of the last line at or before the turn's end; after a
clear, or with no such line, the branch pattern decides.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

COMMAND = "langfuse-work-item"
USAGE = f"usage: {COMMAND} set <id> | {COMMAND} clear"
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9-]+")
DETACHED_BRANCH = "HEAD"


@dataclass(frozen=True)
class Entry:
    at: datetime
    id: Optional[str]  # None for a clear


@dataclass(frozen=True)
class WorkItems:
    """What a session's turns take their work item from."""

    entries: List[Entry]
    branch_pattern: Optional[re.Pattern]


class MarkerError(Exception):
    """A marker file that cannot be read; the message names the fault."""


def marker_path(folder: Path, session_id: str) -> Path:
    """The marker file of session_id in folder.

    Raises ValueError when session_id holds anything other than letters, digits and `-`.
    """
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"the session id {session_id!r} holds more than letters, digits and -")
    return folder / f"{session_id}.jsonl"


def _parse_entry(line: bytes) -> Entry:
    """Raises ValueError for a line that is not a valid entry."""
    row = json.loads(line.decode("utf-8"))
    if not isinstance(row, dict) or not isinstance(row.get("at"), str):
        raise ValueError("not an object with a text `at`")
    at = datetime.fromisoformat(row["at"])
    if at.tzinfo is None:
        raise ValueError("`at` has no time zone")
    item = row.get("id", "")
    if item is not None and not (isinstance(item, str) and item):
        raise ValueError("`id` is neither a non-empty text nor null")
    return Entry(at, item)


def read_entries(path: Path) -> List[Entry]:
    """The entries of the marker file at path, in file order; [] when it does not exist.

    A last line with no newline is an append still in progress, and is left out.
    Raises MarkerError when the file cannot be read or another line is not a valid entry.
    """
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise MarkerError(f"{type(e).__name__}: {e}") from e
    complete_lines = content.split(b"\n")[:-1]
    entries = []
    for number, line in enumerate(complete_lines, start=1):
        try:
            entries.append(_parse_entry(line))
        except ValueError as e:  # json.JSONDecodeError and UnicodeDecodeError are ValueErrors
            raise MarkerError(f"line {number}: {e}") from e
    return entries


def compile_branch_pattern(text: str) -> re.Pattern:
    """The branch pattern as a regular expression.

    Raises ValueError, with the reason, when text is not a valid regular expression
    or has no group.
    """
    try:
        pattern = re.compile(text)
    except re.error as e:
        raise ValueError(str(e)) from e
    if pattern.groups == 0:
        raise ValueError("it has no group")
    return pattern


def branch_item(pattern: Optional[re.Pattern], branch: Optional[str]) -> Optional[str]:
    """The item the pattern's first group finds in branch; None for HEAD or no match."""
    if pattern is None or not branch or branch == DETACHED_BRANCH:
        return None
    found = pattern.search(branch)
    return (found.group(1) or None) if found else None


def work_item_for(
    entries: List[Entry],
    pattern: Optional[re.Pattern],
    branch: Optional[str],
    turn_end: Optional[datetime],
) -> Optional[str]:
    """The work item of a turn that ended at turn_end on branch, or None.

    A turn_end of None takes the last entry.
    """
    reached = [entry for entry in entries if turn_end is None or entry.at <= turn_end]
    if reached and reached[-1].id is not None:
        return reached[-1].id
    return branch_item(pattern, branch)


def _item_problem(item: str) -> Optional[str]:
    if not item:
        return "the work item is empty"
    if any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in item):
        return "the work item holds whitespace or a control character"
    return None


def _fail(code: int, message: str) -> int:
    print(f"{COMMAND}: {message}", file=sys.stderr)
    return code


def run_command(argv: List[str], folder: Path, session_id: Optional[str], now: datetime) -> int:
    """Runs `langfuse-work-item <argv>` for session_id: records `set <id>` or `clear`
    as one line in the session's marker file in folder, timed now.

    now: an aware datetime.
    Returns 0 when recorded, printing nothing; 2 for wrong arguments or a missing or
    unusable session id; 1 when the line cannot be written. On 1 or 2, prints one
    line to stderr and writes nothing.
    """
    if argv == ["clear"]:
        item = None
    elif len(argv) == 2 and argv[0] == "set":
        item = argv[1]
        problem = _item_problem(item)
        if problem:
            return _fail(2, problem)
    else:
        return _fail(2, USAGE)
    if not session_id:
        return _fail(2, "CLAUDE_CODE_SESSION_ID is not set; run this from a Claude Code session")
    try:
        path = marker_path(folder, session_id)
    except ValueError as e:
        return _fail(2, str(e))
    line = json.dumps({"at": now.isoformat(), "id": item}) + "\n"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as marker:
            marker.write(line.encode("utf-8"))
    except OSError as e:
        return _fail(1, f"cannot write {path}: {e}")
    return 0


def prune(folder: Path, cutoff: datetime, log: Callable[[str], None]) -> None:
    """Deletes the marker files in folder last changed before cutoff (an aware datetime).

    A file that cannot be deleted is logged through log and kept.
    """
    if not folder.is_dir():
        return
    for path in folder.glob("*.jsonl"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, cutoff.tzinfo) < cutoff:
                path.unlink()
        except OSError as e:
            log(f"could not delete the old work item marker {path}: {type(e).__name__}: {e}")
