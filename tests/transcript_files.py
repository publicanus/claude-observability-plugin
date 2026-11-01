"""Where Claude Code keeps a session's transcripts, and how their lines are read."""

from __future__ import annotations

from pathlib import Path
from typing import List


def agent_folder(session: Path) -> Path:
    """The folder that holds the agent transcripts of session (<session id>.jsonl):
    <session id>/subagents/ beside it. It may not exist."""
    return session.with_suffix("") / "subagents"


def transcript_lines(path: Path) -> List[bytes]:
    """The transcript's lines as written, without their line ends; blank lines left out."""
    return [line for line in path.read_bytes().split(b"\n") if line.strip()]
