"""Pins that a /hurt typed while the session's turns wait for a running background
agent is not lost: the note lands on the session's trace once the hook has sent it.

The test writes a session whose first turn launched a background agent that is still
running, runs the hook over it, adds a /hurt turn, runs /hurt and the hook, lets the
agent finish, runs the hook again, and then attaches the queued notes the way the hook
does, against a fake Langfuse.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import hurt
import hurt_annotations
from conftest import write_settings
from test_continued_agent_runs import (
    AGENT_ID,
    AGENT_TYPE,
    DESCRIPTION,
    LAUNCH,
    SESSION_ID,
    ContinuedAgent,
    launch_turn,
    notification,
    round_1,
)
from test_hurt_after_stopped_turns import hurt_turn
from test_hurt_pending_processing import FakeConfig, RoutedClient, full_success_routes, install_fake_client


def session_in_projects_dir(projects_dir: Path) -> ContinuedAgent:
    """A session transcript, with the agent's transcript beside it."""
    project = projects_dir / "project"
    subagents = project / SESSION_ID / "subagents"
    subagents.mkdir(parents=True)
    (subagents / f"agent-{AGENT_ID}.meta.json").write_text(json.dumps({
        "agentType": AGENT_TYPE, "description": DESCRIPTION, "toolUseId": LAUNCH,
    }), encoding="utf-8")
    transcript = project / f"{SESSION_ID}.jsonl"
    agent_transcript = subagents / f"agent-{AGENT_ID}.jsonl"
    transcript.touch()
    agent_transcript.touch()
    return ContinuedAgent(transcript.resolve(), agent_transcript)


def run_hook(hook_module: Any, fake_langfuse: Any, session: ContinuedAgent) -> None:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1", repo_name="shop")
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION_ID, session.transcript)


def test_a_hurt_typed_while_the_sessions_turn_waits_for_a_running_agent_lands_on_its_trace(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, repo, monkeypatch, capsys, no_langfuse_env
):
    session = session_in_projects_dir(tmp_path / "projects")
    config = FakeConfig()
    write_settings(repo, {"LANGFUSE_PUBLIC_KEY": config.public_key, "LANGFUSE_SECRET_KEY": config.secret_key,
                          "LANGFUSE_BASE_URL": config.host})
    monkeypatch.chdir(repo)
    monkeypatch.setattr(hurt, "HURT_UNRESOLVED_FILE", hook_module.HURT_UNRESOLVED_FILE)
    monkeypatch.setattr(hurt, "HURT_PENDING_LOCK_FILE", hook_module.HURT_PENDING_LOCK_FILE)

    agent_prompt, *rest_of_round_1 = round_1()
    session.add_agent(agent_prompt)
    session.add_main(*launch_turn())
    run_hook(hook_module, fake_langfuse, session)

    session.add_main(*hurt_turn(10))
    exit_code = hurt.run(
        ["--category", "stalled-loop", "--comment", "the agent went in circles"],
        env={"CLAUDE_CODE_SESSION_ID": SESSION_ID},
    )
    assert exit_code == 0, capsys.readouterr().out
    run_hook(hook_module, fake_langfuse, session)

    session.add_agent(*rest_of_round_1)
    session.add_main(notification("main-note-1", LAUNCH, "Round 1 done.", 30))
    run_hook(hook_module, fake_langfuse, session)
    roots = [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]
    [launch_trace] = [o.trace_id for o in roots if o.kwargs["input"]["content"] == "Launch the researcher."]

    client = RoutedClient(hurt_annotations)
    full_success_routes(hurt_annotations, client).uploaded.update(o.trace_id for o in roots)
    install_fake_client(hurt_annotations, monkeypatch, client)
    hook_module.process_pending_hurts(config, hook_module.HURT_PENDING_FILE)

    scores = [body for method, path, body in client.calls if path == "/api/public/scores"]
    assert [(score["traceId"], score["comment"]) for score in scores] == [
        (launch_trace, "the agent went in circles"),
    ]
