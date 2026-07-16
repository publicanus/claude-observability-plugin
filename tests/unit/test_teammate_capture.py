from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest


# ----------------- Synthetic transcript builders -----------------
# Modeled on the real named-teammate shapes (Agent tool_use with input.name,
# meta.json with taskKind "in_process_teammate" and no toolUseId, teammate
# transcripts that look like ordinary user/assistant transcripts). No real
# transcript content is copied.

def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _append_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_parent_transcript(
    path: Path,
    session_id: str,
    launches: List[Tuple[str, str]],
) -> None:
    """A one-turn parent that launches `launches` = [(teammate_name, tool_use_id)]."""
    rows: List[Dict[str, Any]] = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "sessionId": session_id,
            "uuid": "parent-user-1",
            "cwd": "/repo",
            "gitBranch": "main",
            "origin": {"kind": "human"},
            "message": {"role": "user", "content": "Spawn the team."},
        }
    ]
    tool_uses = [
        {
            "type": "tool_use",
            "id": tool_use_id,
            "name": "Agent",
            "input": {
                "name": name,
                "subagent_type": "general-purpose",
                "description": f"Build {name}",
                "prompt": "go",
            },
        }
        for name, tool_use_id in launches
    ]
    rows.append(
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "sessionId": session_id,
            "uuid": "parent-assistant-1",
            "requestId": "req-parent-1",
            "message": {
                "id": "msg-parent-1",
                "role": "assistant",
                "model": "claude-test",
                "content": tool_uses,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    )
    _write_jsonl(path, rows)


def teammate_meta(agent_id: str, name: str, team_name: str = "session-test") -> Dict[str, Any]:
    return {
        "agentType": name,
        "description": f"Build {name}",
        "name": name,
        "spawnDepth": 0,
        "model": "sonnet",
        "taskKind": "in_process_teammate",
        "teamName": team_name,
        "color": "blue",
        "permissionMode": "bypassPermissions",
    }


def teammate_turn_rows(
    session_id: str,
    agent_id: str,
    index: int,
    minute: int,
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> List[Dict[str, Any]]:
    ts = f"2026-01-01T00:{minute:02d}:00.000Z"
    ts2 = f"2026-01-01T00:{minute:02d}:02.000Z"
    return [
        {
            "type": "user",
            "timestamp": ts,
            "sessionId": session_id,
            "agentId": agent_id,
            "uuid": f"{agent_id}-user-{index}",
            "message": {"role": "user", "content": f"Instruction {index}"},
        },
        {
            "type": "assistant",
            "timestamp": ts2,
            "sessionId": session_id,
            "agentId": agent_id,
            "uuid": f"{agent_id}-assistant-{index}",
            "requestId": f"req-{agent_id}-{index}",
            "message": {
                "id": f"msg-{agent_id}-{index}",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": f"Did work {index}"}],
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            },
        },
    ]


def write_teammate(
    transcript_path: Path,
    session_id: str,
    file_stem: str,
    name: str,
    turn_count: int,
) -> Path:
    """Create a teammate meta + transcript with `turn_count` turns; returns the jsonl path."""
    subagent_dir = transcript_path.with_suffix("") / "subagents"
    subagent_dir.mkdir(parents=True, exist_ok=True)
    agent_id = file_stem
    (subagent_dir / f"agent-{file_stem}.meta.json").write_text(
        json.dumps(teammate_meta(agent_id, name)), encoding="utf-8"
    )
    jsonl_path = subagent_dir / f"agent-{file_stem}.jsonl"
    rows: List[Dict[str, Any]] = []
    for index in range(1, turn_count + 1):
        rows.extend(teammate_turn_rows(session_id, agent_id, index, minute=index))
    _write_jsonl(jsonl_path, rows)
    return jsonl_path


def make_config(hook_module: Any, trace_seed: Optional[str] = None) -> Any:
    return hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", trace_seed
    )


def teammate_turn_observations(fake_langfuse: Any) -> List[Any]:
    return [obs for obs in fake_langfuse.observations if obs.name == "Teammate Turn"]


# ----------------- Discovery -----------------

def test_discovers_teammate_transcript_without_tool_use_id(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    subagent_dir = tmp_path / "transcript" / "subagents"
    subagent_dir.mkdir(parents=True)

    # A named teammate: no toolUseId, taskKind in_process_teammate.
    (subagent_dir / "agent-abuilder-issue-4-hash.meta.json").write_text(
        json.dumps(teammate_meta("abuilder-issue-4-hash", "builder-issue-4")),
        encoding="utf-8",
    )
    (subagent_dir / "agent-abuilder-issue-4-hash.jsonl").write_text("", encoding="utf-8")

    # A classic subagent: has toolUseId, must NOT be picked up as a teammate.
    (subagent_dir / "agent-classic.meta.json").write_text(
        json.dumps({"agentType": "general-purpose", "description": "d", "toolUseId": "toolu_classic", "spawnDepth": 1}),
        encoding="utf-8",
    )
    (subagent_dir / "agent-classic.jsonl").write_text("", encoding="utf-8")

    teammates = hook_module.discover_teammate_transcripts(transcript)

    assert [t["name"] for t in teammates] == ["builder-issue-4"]
    teammate = teammates[0]
    assert teammate["team_name"] == "session-test"
    assert teammate["agent_type"] == "builder-issue-4"
    assert teammate["path"].name == "agent-abuilder-issue-4-hash.jsonl"

    # The classic subagent still resolves only via the toolUseId map, unchanged.
    classic = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)
    assert set(classic) == {"toolu_classic"}


# ----------------- name -> tool_use linkage -----------------

def test_matches_two_teammate_names_to_their_launching_tool_uses(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    make_parent_transcript(
        transcript,
        "session-test",
        [("builder-issue-4", "toolu_4"), ("builder-issue-5", "toolu_5")],
    )

    name_to_id, ambiguous = hook_module.get_teammate_name_to_tool_use_id(transcript)

    assert name_to_id == {"builder-issue-4": "toolu_4", "builder-issue-5": "toolu_5"}
    assert ambiguous == set()


def test_same_name_teammates_link_to_first_launch_deterministically(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    # Two launches share a name; the earliest (file order) wins and the name is
    # flagged ambiguous.
    make_parent_transcript(
        transcript,
        "session-test",
        [("dup", "toolu_first"), ("dup", "toolu_second")],
    )

    name_to_id, ambiguous = hook_module.get_teammate_name_to_tool_use_id(transcript)

    assert name_to_id == {"dup": "toolu_first"}
    assert ambiguous == {"dup"}


# ----------------- End-to-end emission -----------------

def test_emits_teammate_generations_with_token_usage_and_linkage(
    hook_module, fake_langfuse, isolated_hook_state
):
    session_id = "session-test"
    transcript = isolated_hook_state / "transcript.jsonl"
    make_parent_transcript(transcript, session_id, [("builder-issue-4", "toolu_4")])
    # Two turns so exactly one (the first) closes on a normal firing.
    write_teammate(transcript, session_id, "abuilder-4", "builder-issue-4", turn_count=2)

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, make_config(hook_module), session_id, transcript
    )

    # One parent turn + one closed teammate turn.
    assert emitted == 2
    teammate_turns = teammate_turn_observations(fake_langfuse)
    assert len(teammate_turns) == 1

    # Linkage lives in trace metadata.
    assert teammate_turns[0].kwargs["metadata"]["launching_tool_use_id"] == "toolu_4"
    assert teammate_turns[0].kwargs["metadata"]["teammate_name"] == "builder-issue-4"

    # Token usage reaches the teammate generation.
    generations = [obs for obs in fake_langfuse.observations if obs.name == "Teammate LLM Call 1"]
    assert generations, "expected a teammate generation"
    assert generations[0].kwargs["usage_details"] == {"input": 100, "output": 50}


def test_delta_emission_across_two_firings(hook_module, fake_langfuse, isolated_hook_state):
    session_id = "session-test"
    transcript = isolated_hook_state / "transcript.jsonl"
    make_parent_transcript(transcript, session_id, [("builder-issue-4", "toolu_4")])
    teammate_jsonl = write_teammate(transcript, session_id, "abuilder-4", "builder-issue-4", turn_count=2)
    config = make_config(hook_module)

    # Firing 1 (Stop): 2 turns present, the trailing one is held back.
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, session_id, transcript)
    assert len(teammate_turn_observations(fake_langfuse)) == 1

    # The teammate grows: turn 2 is now closed by a new turn 3 (the new trailing).
    _append_jsonl(teammate_jsonl, teammate_turn_rows(session_id, "abuilder-4", 3, minute=3))

    # Firing 2 (Stop): only the newly-closed turn 2 is emitted (delta), not turn 1 again.
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, session_id, transcript)
    assert len(teammate_turn_observations(fake_langfuse)) == 2

    # Firing 3 (SessionEnd): flush emits the final held-back turn 3.
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, session_id, transcript, flush_deferred_agent_turns=True
    )
    assert len(teammate_turn_observations(fake_langfuse)) == 3

    # Persisted per-teammate progress marker reflects all three emitted turns.
    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    key = hook_module.get_session_state_key(session_id, str(teammate_jsonl))
    assert state[key]["turn_count"] == 3


def test_idempotent_repeated_firings_emit_nothing_new(hook_module, fake_langfuse, isolated_hook_state):
    session_id = "session-test"
    transcript = isolated_hook_state / "transcript.jsonl"
    make_parent_transcript(transcript, session_id, [("builder-issue-4", "toolu_4")])
    write_teammate(transcript, session_id, "abuilder-4", "builder-issue-4", turn_count=2)
    config = make_config(hook_module)

    first = hook_module.emit_new_turns_from_transcript(fake_langfuse, config, session_id, transcript)
    count_after_first = len(teammate_turn_observations(fake_langfuse))
    assert count_after_first == 1

    # No transcript growth between firings: nothing new for parent or teammate.
    second = hook_module.emit_new_turns_from_transcript(fake_langfuse, config, session_id, transcript)
    assert second == 0
    assert len(teammate_turn_observations(fake_langfuse)) == count_after_first


def test_unattributed_teammate_emitted_with_tag(
    hook_module, fake_langfuse, isolated_hook_state, monkeypatch
):
    session_id = "session-test"
    transcript = isolated_hook_state / "transcript.jsonl"
    # Parent launches a DIFFERENT name, so the teammate cannot be linked.
    make_parent_transcript(transcript, session_id, [("some-other-agent", "toolu_x")])
    write_teammate(transcript, session_id, "aorphan", "orphan-teammate", turn_count=2)

    captured_tags: List[List[str]] = []
    original = hook_module.propagate_attributes

    def _capturing(**kwargs: Any):
        captured_tags.append(list(kwargs.get("tags") or []))
        return original(**kwargs)

    monkeypatch.setattr(hook_module, "propagate_attributes", _capturing)

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, make_config(hook_module), session_id, transcript
    )

    teammate_turns = teammate_turn_observations(fake_langfuse)
    assert len(teammate_turns) == 1
    # No linkage in metadata...
    assert teammate_turns[0].kwargs["metadata"]["launching_tool_use_id"] is None
    # ...and the trace is tagged unattributed.
    teammate_tag_sets = [tags for tags in captured_tags if any(t.startswith("teammate:") for t in tags)]
    assert teammate_tag_sets, "expected a teammate trace with tags"
    assert "teammate-unattributed" in teammate_tag_sets[0]
    assert "teammate:orphan-teammate" in teammate_tag_sets[0]
