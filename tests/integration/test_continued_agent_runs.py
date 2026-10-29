"""Pins how an agent's rounds reach Langfuse: the launch prompt and each message the
main session sends it with SendMessage start a round, and each round is a run (a
"Subagent: ..." observation) in the trace of the turn that started it.

Each test writes a main transcript and one agent transcript in a temp folder (the
`continued_agent` fixture), runs the hook over them one or more times, and reads what
the fake Langfuse received.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest


AGENT_ID = "a0123456789abcdef"
AGENT_TYPE = "mate-core:analyst"
DESCRIPTION = "Research the topic"
LAUNCH = "toolu_launch"
SEND_2 = "toolu_send_2"
SEND_3 = "toolu_send_3"
SESSION_ID = "session-continued"
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(seconds: float) -> str:
    return (BASE_TIME + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ----------------- Transcript rows -----------------

def user_row(uuid: str, text: str, seconds: float) -> Dict[str, Any]:
    return {
        "type": "user",
        "timestamp": at(seconds),
        "uuid": uuid,
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": text},
    }


def assistant_row(uuid: str, content: List[Dict[str, Any]], seconds: float, **fields: Any) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": at(seconds),
        "uuid": uuid,
        "message": {
            "id": f"msg-{uuid}",
            "role": "assistant",
            "model": "claude-test",
            "content": content,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
        **fields,
    }


def text(value: str) -> Dict[str, Any]:
    return {"type": "text", "text": value}


def tool_use(tool_use_id: str, name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}


def agent_call(seconds: float) -> Dict[str, Any]:
    return assistant_row("main-launch", [tool_use(LAUNCH, "Agent", {
        "description": DESCRIPTION, "prompt": "Research the topic.", "subagent_type": AGENT_TYPE,
        "run_in_background": True,
    })], seconds)


def send_call(uuid: str, tool_use_id: str, message: str, seconds: float, to: str = AGENT_ID) -> Dict[str, Any]:
    return assistant_row(uuid, [tool_use(tool_use_id, "SendMessage", {"to": to, "message": message})], seconds)


def tool_result_row(uuid: str, tool_use_id: str, result: str, seconds: float,
                    tool_use_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "user",
        "timestamp": at(seconds),
        "uuid": uuid,
        "toolUseResult": tool_use_result,
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": [text(result)]},
        ]},
    }


def launch_result(seconds: float) -> Dict[str, Any]:
    return tool_result_row("main-launch-result", LAUNCH, "Async agent launched successfully.", seconds,
                           {"status": "async_launched", "isAsync": True, "agentId": AGENT_ID})


def send_result(uuid: str, tool_use_id: str, seconds: float, pin_id: str = AGENT_ID,
                resumed: bool = True) -> Dict[str, Any]:
    result: Dict[str, Any] = {"success": True, "message": "Message sent.", "pin": {"id": pin_id, "name": pin_id}}
    if resumed:
        result["resumedAgentId"] = pin_id
    return tool_result_row(uuid, tool_use_id, json.dumps(result), seconds, result)


def notification(uuid: str, tool_use_id: str, result: str, seconds: float) -> Dict[str, Any]:
    return {
        "type": "user",
        "timestamp": at(seconds),
        "uuid": uuid,
        "origin": {"kind": "task-notification"},
        "message": {"role": "user", "content": (
            f"<task-notification><task-id>{AGENT_ID}</task-id><tool-use-id>{tool_use_id}</tool-use-id>"
            f"<result>{result}</result></task-notification>"
        )},
    }


def hand_back(uuid: str, report: str, seconds: float) -> Dict[str, Any]:
    return {
        "type": "user",
        "timestamp": at(seconds),
        "uuid": uuid,
        "isMeta": True,
        "origin": {"kind": "peer", "from": AGENT_ID, "senderTaskId": AGENT_ID, "handback": True},
        "message": {"role": "user", "content": f"<agent-message from=\"{AGENT_ID}\">{report}</agent-message>"},
    }


def agent_prompt(text_value: str, seconds: float) -> Dict[str, Any]:
    return {"type": "user", "timestamp": at(seconds), "uuid": "agent-prompt",
            "message": {"role": "user", "content": text_value}}


def coordinator_row(uuid: str, text_value: str, seconds: float) -> Dict[str, Any]:
    return {"type": "user", "timestamp": at(seconds), "uuid": uuid, "isMeta": True,
            "origin": {"kind": "coordinator"}, "message": {"role": "user", "content": text_value}}


def queued_message_row(uuid: str, text_value: str, seconds: float) -> Dict[str, Any]:
    return {"type": "attachment", "timestamp": at(seconds), "uuid": uuid, "attachment": {
        "type": "queued_command", "prompt": text_value, "origin": {"kind": "coordinator"}, "isMeta": True,
        "source_uuid": "elsewhere",
    }}


def agent_says(uuid: str, value: str, seconds: float, **fields: Any) -> Dict[str, Any]:
    return assistant_row(uuid, [text(value)], seconds, **fields)


# ----------------- The usual session -----------------
# Turn 1 launches the agent, turns 2 and 3 each send it one message. The agent's
# rounds have 2, 1 and 2 assistant messages.

def launch_turn() -> List[Dict[str, Any]]:
    return [
        user_row("main-user-1", "Launch the researcher.", 0),
        agent_call(1),
        launch_result(2),
        agent_says("main-launched", "It is running.", 3),
    ]


def send_turn(number: int, tool_use_id: str, start: float) -> List[Dict[str, Any]]:
    return [
        user_row(f"main-user-{number}", f"Ask it for round {number}.", start),
        send_call(f"main-send-{number}", tool_use_id, f"Message for round {number}.", start + 1),
        send_result(f"main-send-result-{number}", tool_use_id, start + 2),
        agent_says(f"main-sent-{number}", "Sent.", start + 3),
    ]


def round_1() -> List[Dict[str, Any]]:
    return [
        agent_prompt("Research the topic.", 1.5),
        agent_says("agent-1a", "Round 1, first.", 10),
        agent_says("agent-1b", "Round 1, report.", 20),
    ]


def round_2() -> List[Dict[str, Any]]:
    return [
        coordinator_row("agent-coord-2", "Message for round 2.", 101.5),
        agent_says("agent-2a", "Round 2, report.", 110),
    ]


def round_3() -> List[Dict[str, Any]]:
    return [
        coordinator_row("agent-coord-3", "Message for round 3.", 201.5),
        agent_says("agent-3a", "Round 3, first.", 210),
        agent_says("agent-3b", "Round 3, report.", 220),
    ]


def usual_main_transcript() -> List[Dict[str, Any]]:
    return [
        *launch_turn(),
        notification("main-note-1", LAUNCH, "Round 1 done.", 30),
        *send_turn(2, SEND_2, 100),
        notification("main-note-2", SEND_2, "Round 2 done.", 130),
        *send_turn(3, SEND_3, 200),
        notification("main-note-3", SEND_3, "Round 3 done.", 230),
    ]


# ----------------- The fixture -----------------

def append_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


@dataclass
class ContinuedAgent:
    transcript: Path
    agent_transcript: Path

    def add_main(self, *rows: Dict[str, Any]) -> None:
        append_jsonl(self.transcript, list(rows))

    def add_agent(self, *rows: Dict[str, Any]) -> None:
        append_jsonl(self.agent_transcript, list(rows))


@pytest.fixture
def continued_agent(tmp_path: Path) -> ContinuedAgent:
    transcript = tmp_path / "session.jsonl"
    transcript.touch()
    subagents = tmp_path / "session" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / f"agent-{AGENT_ID}.meta.json").write_text(json.dumps({
        "agentType": AGENT_TYPE, "description": DESCRIPTION, "toolUseId": LAUNCH,
    }), encoding="utf-8")
    agent_transcript = subagents / f"agent-{AGENT_ID}.jsonl"
    agent_transcript.touch()
    return ContinuedAgent(transcript, agent_transcript)


def run_hook(hook_module: Any, fake_langfuse: Any, session: ContinuedAgent, flush: bool = False) -> int:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1", repo_name="shop")
    return hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, SESSION_ID, session.transcript, flush_deferred_agent_turns=flush,
    )


# ----------------- Reading what Langfuse got -----------------

def traces(fake_langfuse: Any) -> List[List[Any]]:
    """The observations of each trace, in the order sent; a trace starts at its root."""
    found: List[List[Any]] = []
    for observation in fake_langfuse.observations:
        if observation.name == "Conversational Turn":
            found.append([])
        found[-1].append(observation)
    return found


def runs(observations: List[Any]) -> List[Tuple[Any, List[Any]]]:
    """Each run in observations, with the generations sent right after it."""
    found: List[Tuple[Any, List[Any]]] = []
    for observation in observations:
        if observation.name.startswith("Subagent:"):
            found.append((observation, []))
        elif found and observation.name.startswith("Subagent LLM Call"):
            found[-1][1].append(observation)
    return found


def generation_texts(generations: List[Any]) -> List[str]:
    return [generation.kwargs["output"]["content"] for generation in generations]


def run_summary(observations: List[Any]) -> List[Tuple[str, int, List[str]]]:
    return [
        (run.name, run.kwargs["metadata"]["round"], generation_texts(generations))
        for run, generations in runs(observations)
    ]


def hook_log(hook_module: Any) -> str:
    path = hook_module.LOG_FILE
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ----------------- Tests -----------------

def test_each_turn_shows_the_round_it_started_as_a_run_of_the_agent(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1(), *round_2(), *round_3())
    continued_agent.add_main(*usual_main_transcript())

    assert run_hook(hook_module, fake_langfuse, continued_agent) == 3

    assert [run_summary(trace) for trace in traces(fake_langfuse)] == [
        [(f"Subagent: {DESCRIPTION}", 1, ["Round 1, first.", "Round 1, report."])],
        [(f"Subagent: {DESCRIPTION} - round 2", 2, ["Round 2, report."])],
        [(f"Subagent: {DESCRIPTION} - round 3", 3, ["Round 3, first.", "Round 3, report."])],
    ]
    all_runs = [run for trace in traces(fake_langfuse) for run, _ in runs(trace)]
    assert [run.kwargs["metadata"]["component"] for run in all_runs] == [AGENT_TYPE] * 3
    assert [run.kwargs["input"]["content"] for run in all_runs] == [
        "Research the topic.", "Message for round 2.", "Message for round 3.",
    ]


def test_two_messages_in_one_turn_give_that_trace_two_runs(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1(), *round_2(), *round_3())
    continued_agent.add_main(
        *launch_turn(),
        notification("main-note-1", LAUNCH, "Round 1 done.", 30),
        user_row("main-user-2", "Ask it twice.", 100),
        send_call("main-send-2", SEND_2, "Message for round 2.", 101),
        send_result("main-send-result-2", SEND_2, 101.2),
        send_call("main-send-3", SEND_3, "Message for round 3.", 120),
        send_result("main-send-result-3", SEND_3, 120.2),
        agent_says("main-sent", "Sent both.", 121),
        notification("main-note-2", SEND_2, "Round 2 done.", 130),
        notification("main-note-3", SEND_3, "Round 3 done.", 230),
    )

    run_hook(hook_module, fake_langfuse, continued_agent)

    assert [run_summary(trace) for trace in traces(fake_langfuse)][1] == [
        (f"Subagent: {DESCRIPTION} - round 2", 2, ["Round 2, report."]),
        (f"Subagent: {DESCRIPTION} - round 3", 3, ["Round 3, first.", "Round 3, report."]),
    ]


def test_a_message_to_a_busy_agent_ends_the_launch_run_where_it_arrived(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(
        agent_prompt("Research the topic.", 1.5),
        assistant_row("agent-1a", [text("Round 1, first."), tool_use("toolu_sleep", "Bash", {"command": "sleep 15"})], 10),
        tool_result_row("agent-sleep-result", "toolu_sleep", "done", 25, {"stdout": ""}),
        queued_message_row("agent-queued", "Also say BANANA.", 25.1),
        agent_says("agent-2a", "Round 2, first.", 26),
        agent_says("agent-2b", "Round 2, BANANA.", 30),
    )
    continued_agent.add_main(
        user_row("main-user-1", "Launch the researcher.", 0),
        agent_call(1),
        launch_result(2),
        send_call("main-send-2", SEND_2, "Also say BANANA.", 5),
        send_result("main-send-result-2", SEND_2, 5.3, resumed=False),
        agent_says("main-sent", "Sent.", 6),
        hand_back("main-hand-back", "BANANA", 31),
        notification("main-note-1", LAUNCH, "See the hand-back.", 34),
    )

    run_hook(hook_module, fake_langfuse, continued_agent)

    assert [run_summary(trace) for trace in traces(fake_langfuse)] == [[
        (f"Subagent: {DESCRIPTION}", 1, ["Round 1, first."]),
        (f"Subagent: {DESCRIPTION} - round 2", 2, ["Round 2, first.", "Round 2, BANANA."]),
    ]]


def test_a_message_turn_is_sent_once_its_agent_hands_back_without_a_notification(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1(), *round_2())
    continued_agent.add_main(
        *launch_turn(),
        notification("main-note-1", LAUNCH, "Round 1 done.", 30),
        *send_turn(2, SEND_2, 100),
    )
    assert run_hook(hook_module, fake_langfuse, continued_agent) == 1

    continued_agent.add_main(hand_back("main-hand-back-2", "Round 2 report.", 120))

    assert run_hook(hook_module, fake_langfuse, continued_agent) == 1
    assert run_summary(traces(fake_langfuse)[1]) == [
        (f"Subagent: {DESCRIPTION} - round 2", 2, ["Round 2, report."]),
    ]


def test_a_launch_turn_is_sent_once_its_agent_hands_back_without_a_notification(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1())
    continued_agent.add_main(*launch_turn())
    assert run_hook(hook_module, fake_langfuse, continued_agent) == 0

    continued_agent.add_main(hand_back("main-hand-back-1", "Round 1 report.", 25))

    assert run_hook(hook_module, fake_langfuse, continued_agent) == 1
    assert run_summary(traces(fake_langfuse)[0]) == [
        (f"Subagent: {DESCRIPTION}", 1, ["Round 1, first.", "Round 1, report."]),
    ]


def test_a_notification_after_a_hand_back_changes_neither_the_result_nor_where_the_run_sits(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1())
    continued_agent.add_main(
        user_row("main-user-1", "Launch the researcher.", 0),
        agent_call(1),
        launch_result(2),
        agent_says("main-before", "Before the hand-back.", 3),
        hand_back("main-hand-back", "HAND-BACK REPORT", 21),
        agent_says("main-between", "Between hand-back and notification.", 22),
        notification("main-note-1", LAUNCH, "NOTIFICATION RESULT", 25),
        agent_says("main-after", "After the notification.", 26),
    )

    run_hook(hook_module, fake_langfuse, continued_agent)

    names = [observation.name for observation in traces(fake_langfuse)[0]]
    assert names.index(f"Subagent: {DESCRIPTION}") < names.index("LLM Call 3")
    generation_3 = next(o for o in fake_langfuse.observations if o.name == "LLM Call 3")
    assert [result["output"] for result in generation_3.kwargs["input"]["tool_results"]] == [
        f"<agent-message from=\"{AGENT_ID}\">HAND-BACK REPORT</agent-message>",
    ]


def test_a_turn_that_only_messages_a_teammate_is_sent_without_waiting(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1())
    continued_agent.add_main(
        user_row("main-user-1", "Ping the teammate.", 0),
        send_call("main-send-t", "toolu_send_teammate", "Hello.", 1, to="reviewer"),
        send_result("main-send-result-t", "toolu_send_teammate", 1.2, pin_id="reviewer@team"),
        agent_says("main-sent", "Sent.", 2),
    )

    assert run_hook(hook_module, fake_langfuse, continued_agent) == 1
    assert runs(traces(fake_langfuse)[0]) == []


def test_a_launch_run_sent_at_session_end_holds_only_the_first_round(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1(), *round_2(), *round_3())
    continued_agent.add_main(*launch_turn())
    assert run_hook(hook_module, fake_langfuse, continued_agent) == 0

    assert run_hook(hook_module, fake_langfuse, continued_agent, flush=True) == 1
    assert run_summary(traces(fake_langfuse)[0]) == [
        (f"Subagent: {DESCRIPTION}", 1, ["Round 1, first.", "Round 1, report."]),
    ]


def test_a_message_turn_waits_for_its_round_and_then_shows_all_of_it(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1(), coordinator_row("agent-coord-2", "Message for round 2.", 101.5),
                              agent_says("agent-2a", "Round 2, first.", 110))
    continued_agent.add_main(
        *launch_turn(),
        notification("main-note-1", LAUNCH, "Round 1 done.", 30),
        *send_turn(2, SEND_2, 100),
    )
    assert run_hook(hook_module, fake_langfuse, continued_agent) == 1

    continued_agent.add_agent(agent_says("agent-2b", "Round 2, report.", 120))
    continued_agent.add_main(notification("main-note-2", SEND_2, "Round 2 done.", 130))

    assert run_hook(hook_module, fake_langfuse, continued_agent) == 1
    assert run_summary(traces(fake_langfuse)[1]) == [
        (f"Subagent: {DESCRIPTION} - round 2", 2, ["Round 2, first.", "Round 2, report."]),
    ]


def test_over_growing_transcripts_each_agent_message_is_sent_once(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1())
    continued_agent.add_main(*launch_turn(), notification("main-note-1", LAUNCH, "Round 1 done.", 30))
    run_hook(hook_module, fake_langfuse, continued_agent)

    continued_agent.add_agent(*round_2())
    continued_agent.add_main(*send_turn(2, SEND_2, 100))
    run_hook(hook_module, fake_langfuse, continued_agent)

    continued_agent.add_agent(*round_3())
    continued_agent.add_main(
        notification("main-note-2", SEND_2, "Round 2 done.", 130),
        *send_turn(3, SEND_3, 200),
    )
    run_hook(hook_module, fake_langfuse, continued_agent)
    run_hook(hook_module, fake_langfuse, continued_agent, flush=True)

    sent = [o for o in fake_langfuse.observations if o.name.startswith("Subagent LLM Call")]
    assert generation_texts(sent) == [
        "Round 1, first.", "Round 1, report.", "Round 2, report.", "Round 3, first.", "Round 3, report.",
    ]


@pytest.mark.parametrize("round_2_rows", [
    pytest.param([], id="no start row"),
    pytest.param([coordinator_row("agent-coord-2", "Message for round 2.", 99)], id="start row before the call"),
])
def test_a_message_without_its_round_in_the_agent_transcript_gives_no_run_and_a_log_line(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent, round_2_rows
):
    continued_agent.add_agent(*round_1(), *round_2_rows)
    continued_agent.add_main(
        *launch_turn(),
        notification("main-note-1", LAUNCH, "Round 1 done.", 30),
        *send_turn(2, SEND_2, 100),
        notification("main-note-2", SEND_2, "Round 2 done.", 130),
    )

    assert run_hook(hook_module, fake_langfuse, continued_agent) == 2

    assert runs(traces(fake_langfuse)[1]) == []
    log_lines = [line for line in hook_log(hook_module).splitlines() if SEND_2 in line]
    assert len(log_lines) == 1
    assert AGENT_ID in log_lines[0] and "round 2" in log_lines[0]


def test_a_skill_used_in_a_later_round_counts_only_for_the_turn_that_started_that_round(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(
        *round_1(),
        coordinator_row("agent-coord-2", "Message for round 2.", 101.5),
        assistant_row("agent-2a", [tool_use("toolu_skill", "Skill", {"skill": "pymate:tidy"})], 110),
    )
    continued_agent.add_main(
        *launch_turn(),
        notification("main-note-1", LAUNCH, "Round 1 done.", 30),
        *send_turn(2, SEND_2, 100),
        notification("main-note-2", SEND_2, "Round 2 done.", 130),
    )

    run_hook(hook_module, fake_langfuse, continued_agent)

    launch_root, send_root = (trace[0] for trace in traces(fake_langfuse))
    assert "subagent-skill:pymate:tidy" not in launch_root.propagated["tags"]
    assert "subagent-skill:pymate:tidy" in send_root.propagated["tags"]
    assert "pymate" not in [plugin["name"] for plugin in launch_root.kwargs["metadata"]["plugins"]]
    assert "pymate" in [plugin["name"] for plugin in send_root.kwargs["metadata"]["plugins"]]


def api_request_blob(content: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A copy of a request's messages that Claude Code writes beside the turn."""
    return {"type": "api-request-blob", "hash": "blob", "sessionId": SESSION_ID,
            "message": {"role": "assistant", "content": content}}


def test_a_request_copy_in_the_transcript_adds_no_tool_call_and_no_run(
    hook_module, fake_langfuse, isolated_hook_state, continued_agent
):
    continued_agent.add_agent(*round_1(), *round_2())
    launch = launch_turn()
    send = send_turn(2, SEND_2, 100)
    continued_agent.add_main(
        *launch[:3],
        api_request_blob(launch[1]["message"]["content"]),
        *launch[3:],
        notification("main-note-1", LAUNCH, "Round 1 done.", 30),
        *send[:3],
        api_request_blob(send[1]["message"]["content"]),
        *send[3:],
        notification("main-note-2", SEND_2, "Round 2 done.", 130),
    )

    run_hook(hook_module, fake_langfuse, continued_agent)

    names = [observation.name for observation in fake_langfuse.observations]
    assert names.count("Tool: Agent") == 1
    assert names.count("Tool: SendMessage") == 1
    assert [run_summary(trace) for trace in traces(fake_langfuse)] == [
        [(f"Subagent: {DESCRIPTION}", 1, ["Round 1, first.", "Round 1, report."])],
        [(f"Subagent: {DESCRIPTION} - round 2", 2, ["Round 2, report."])],
    ]
