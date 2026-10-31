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

LATE_AGENT_ROWS = (
    "An agent's rows written after its run was sent are never sent: here its reply to a task "
    "notification that reached it after it had reported back."
)
PEER_MESSAGE = (
    "A message from another session (origin peer, not a hand-back) that arrives after the turn "
    "ended starts no turn, so the session's replies to it are lost, and so is the agent round a "
    "SendMessage in those replies continued."
)
QUEUED_NOTIFICATION = (
    "A queue-operation row that holds a queued copy of a task notification is read as the "
    "notification itself, and the agent round the notified SendMessage continued is lost."
)

LATE = "late-hand-back"
LATE_AGENT = "2f26c36b-4b64-48a5-9de8-b721448fe6f6/subagents/agent-af9f988dac6d29ad4.jsonl"
WORK = "work-session"
WORK_MAIN = "2e2b4ad6-fcba-4119-b37d-46420423f45c.jsonl"
WORK_AGENT_A0 = "2e2b4ad6-fcba-4119-b37d-46420423f45c/subagents/agent-a0efc5da91dc20730.jsonl"
WORK_AGENT_A8 = "2e2b4ad6-fcba-4119-b37d-46420423f45c/subagents/agent-a8d0c87bbace661c5.jsonl"

# Replies the hook loses or sends twice today: (fixture folder, transcript file, message id)
# to the fault, "lost" or "doubled", and the cause it shows.
KNOWN_FAULTS: Dict[Tuple[str, str, str], Tuple[str, str]] = {
    (LATE, LATE_AGENT, "msg_011CgXwbLyiyXvauv9zuNHr2"): ("lost", LATE_AGENT_ROWS),
    **{
        (WORK, WORK_MAIN, message_id): ("lost", PEER_MESSAGE)
        for message_id in (
            "msg_011CfoCjLjQagjVYb17336o6",
            "msg_011CfoCjyyGoMPpeBbMUJcg3",
            "msg_011CfoDHDYhEXhd7yNhoYM4J",
            "msg_011CfoDb9uRmMrdoZg1boMTG",
        )
    },
    **{
        (WORK, WORK_AGENT_A0, message_id): ("lost", PEER_MESSAGE)
        for message_id in (
            "msg_011CfoCjyM4zpspa4rxVZjVt",
            "msg_011CfoCkTHL9oTVXBSR6iik1",
            "msg_011CfoCm799EdXakhsmeUCni",
            "msg_011CfoCnairaNfrYk873tCwS",
            "msg_011CfoCnueQQzkbm9CuQLHCC",
            "msg_011CfoCp3f8NqjiYWBCxhXki",
            "msg_011CfoCpicSMHGF4YRFYSLoE",
            "msg_011CfoCqWxV4cK3NBNUNqL21",
            "msg_011CfoCrc9J3JDMShCjeK8W9",
            "msg_011CfoCzUsUNmTVoUYdyeoMX",
            "msg_011CfoD1NBB4i9zMCruTWMsn",
        )
    },
    (WORK, WORK_AGENT_A8, "msg_011CfnvS5igHF5fgcku2xKSE"): ("lost", QUEUED_NOTIFICATION),
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
