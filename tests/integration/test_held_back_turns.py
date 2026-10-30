"""Pins the turn number and trace id of a turn the hook holds back while its background
agent runs: the turn gets both when the hook first sees it, the session's
latest_trace_id points at it from then on, and it is sent under them later.

Each test writes a main transcript and one agent transcript in a temp folder (the
`continued_agent` fixture), runs the hook over them more than once, and reads the fake
Langfuse and the hook's state file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from test_continued_agent_runs import (
    LAUNCH,
    SESSION_ID,
    ContinuedAgent,
    agent_says,
    continued_agent,  # noqa: F401 - pytest fixture
    launch_turn,
    notification,
    round_1,
    user_row,
)

SEED = "seed-held-back"


def plain_turn() -> List[Dict[str, Any]]:
    return [user_row("main-user-2", "A plain question.", 5), agent_says("main-answer-2", "A plain answer.", 6)]


def run_hook(hook_module: Any, fake_langfuse: Any, session: ContinuedAgent, seed: str | None = None) -> None:
    config = hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", repo_name="shop", trace_seed=seed
    )
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION_ID, session.transcript)


def session_entry(hook_module: Any, session: ContinuedAgent) -> Dict[str, Any]:
    state = json.loads(Path(hook_module.STATE_FILE).read_text(encoding="utf-8"))
    return state[hook_module.get_session_state_key(SESSION_ID, str(session.transcript))]


def sent_trace_ids_by_question(fake_langfuse: Any) -> Dict[str, str]:
    return {
        o.kwargs["input"]["content"]: o.trace_id
        for o in fake_langfuse.observations
        if o.name == "Conversational Turn"
    }


def let_the_agent_finish(session: ContinuedAgent, rest_of_round_1: List[Dict[str, Any]]) -> None:
    session.add_agent(*rest_of_round_1)
    session.add_main(notification("main-note-1", LAUNCH, "Round 1 done.", 30))


def start_session(session: ContinuedAgent, *main_rows: Dict[str, Any]) -> List[Dict[str, Any]]:
    agent_prompt, *rest_of_round_1 = round_1()
    session.add_agent(agent_prompt)
    session.add_main(*main_rows)
    return rest_of_round_1


def test_a_held_back_turn_sent_later_leaves_latest_trace_id_on_the_newer_turn(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    rest_of_round_1 = start_session(continued_agent, *launch_turn(), *plain_turn())
    run_hook(hook_module, fake_langfuse, continued_agent)
    newer_turn_trace = sent_trace_ids_by_question(fake_langfuse)["A plain question."]

    let_the_agent_finish(continued_agent, rest_of_round_1)
    run_hook(hook_module, fake_langfuse, continued_agent)

    assert set(sent_trace_ids_by_question(fake_langfuse)) == {"A plain question.", "Launch the researcher."}
    assert session_entry(hook_module, continued_agent)["latest_trace_id"] == newer_turn_trace


def test_turns_are_numbered_in_the_order_they_were_typed_when_the_first_one_is_held_back(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    rest_of_round_1 = start_session(continued_agent, *launch_turn(), *plain_turn())
    run_hook(hook_module, fake_langfuse, continued_agent, SEED)
    let_the_agent_finish(continued_agent, rest_of_round_1)
    run_hook(hook_module, fake_langfuse, continued_agent, SEED)

    assert sent_trace_ids_by_question(fake_langfuse) == {
        "Launch the researcher.": hook_module.derive_turn_trace_id(SEED, 1),
        "A plain question.": hook_module.derive_turn_trace_id(SEED, 2),
    }
    assert session_entry(hook_module, continued_agent)["turn_count"] == 2


def test_a_held_back_turn_is_the_latest_trace_before_it_is_sent_and_is_sent_under_that_id(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    rest_of_round_1 = start_session(continued_agent, *launch_turn())
    run_hook(hook_module, fake_langfuse, continued_agent, SEED)
    latest_while_waiting = session_entry(hook_module, continued_agent)["latest_trace_id"]

    let_the_agent_finish(continued_agent, rest_of_round_1)
    run_hook(hook_module, fake_langfuse, continued_agent, SEED)

    assert latest_while_waiting == hook_module.derive_turn_trace_id(SEED, 1)
    assert sent_trace_ids_by_question(fake_langfuse) == {"Launch the researcher.": latest_while_waiting}


def test_a_held_back_turn_whose_seeded_id_cannot_be_derived_is_not_sent_and_says_why(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(hook_module, "derive_turn_trace_id", lambda trace_seed, turn_number: None)
    rest_of_round_1 = start_session(continued_agent, *launch_turn())
    run_hook(hook_module, fake_langfuse, continued_agent, SEED)
    let_the_agent_finish(continued_agent, rest_of_round_1)
    run_hook(hook_module, fake_langfuse, continued_agent, SEED)

    assert sent_trace_ids_by_question(fake_langfuse) == {}
    assert "turn 1" in Path(hook_module.LOG_FILE).read_text(encoding="utf-8")


def test_a_turn_held_back_by_the_old_hook_is_numbered_when_it_is_sent(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    rest_of_round_1 = start_session(continued_agent, *launch_turn())
    run_hook(hook_module, fake_langfuse, continued_agent, SEED)
    # Rewrite the entry the way the old hook saved it: no number and no trace id
    # on the held-back turn, and a turn count from earlier turns.
    state = hook_module.load_hook_state()
    entry = state[hook_module.get_session_state_key(SESSION_ID, str(continued_agent.transcript))]
    for held_back in entry["pending_agent_turns"]:
        del held_back["turn_number"], held_back["trace_id"]
    entry["turn_count"] = 4
    hook_module.save_hook_state(state)

    let_the_agent_finish(continued_agent, rest_of_round_1)
    run_hook(hook_module, fake_langfuse, continued_agent, SEED)

    assert sent_trace_ids_by_question(fake_langfuse) == {
        "Launch the researcher.": hook_module.derive_turn_trace_id(SEED, 5),
    }
    assert session_entry(hook_module, continued_agent)["turn_count"] == 5


def test_an_sdk_without_the_root_mark_logs_why_a_turn_is_not_sent(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setitem(sys.modules, "langfuse._client.attributes", None)
    start_session(continued_agent, *launch_turn(), *plain_turn())
    run_hook(hook_module, fake_langfuse, continued_agent, SEED)

    assert sent_trace_ids_by_question(fake_langfuse) == {}
    assert "langfuse._client.attributes" in Path(hook_module.LOG_FILE).read_text(encoding="utf-8")
