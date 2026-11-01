"""Replaying real Claude Code sessions Stop by Stop, each reply becomes exactly one model
call: none is lost, none is sent twice.

The sessions in tests/fixtures/replay are real sessions cleaned by
tests/transcript_cleaning.py. The hook runs as its own process with the real Langfuse SDK,
against a local stand-in for Langfuse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import pytest
from transcript_cleaning import FIXTURES
from transcript_replay import fault_lines, replay_session

SESSIONS = sorted(path for path in FIXTURES.glob("*/*.jsonl"))

AGENT_WOKEN_AGAIN = (
    "An agent's reply after it has finished and woken up again without a new message from "
    "the main session is lost."
)

LATE = "late-hand-back"
LATE_AGENT = "2f26c36b-4b64-48a5-9de8-b721448fe6f6/subagents/agent-af9f988dac6d29ad4.jsonl"

# Replies the hook loses or sends twice today: (fixture folder, transcript file, message id)
# to the fault, "lost" or "doubled", and the cause it shows.
KNOWN_FAULTS: Dict[Tuple[str, str, str], Tuple[str, str]] = {
    (LATE, LATE_AGENT, "msg_011CgXwbLyiyXvauv9zuNHr2"): ("lost", AGENT_WOKEN_AGAIN),
}


def known_faults_of(fixture: str) -> Dict[Tuple[str, str], str]:
    return {
        (file, message_id): fault
        for (folder, file, message_id), (fault, _) in KNOWN_FAULTS.items()
        if folder == fixture
    }


@pytest.mark.parametrize("session", SESSIONS, ids=lambda path: path.parent.name)
def test_each_reply_becomes_exactly_one_model_call(session: Path, tmp_path: Path) -> None:
    fixture = session.parent.name

    result = replay_session(session, tmp_path)

    faults = fault_lines(result, known_faults_of(fixture), prefix=f"{fixture}/")
    assert not faults, "\n".join(faults)
