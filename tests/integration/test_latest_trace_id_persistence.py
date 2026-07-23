from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_two_turn_transcript(tmp_path: Path, session_id: str) -> Path:
    rows = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "sessionId": session_id,
            "uuid": "user-1",
            "message": {"role": "user", "content": "First question."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "sessionId": session_id,
            "uuid": "assistant-1",
            "message": {
                "id": "msg-1",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "First answer."}],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:02.000Z",
            "sessionId": session_id,
            "uuid": "user-2",
            "message": {"role": "user", "content": "Second question."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:03.000Z",
            "sessionId": session_id,
            "uuid": "assistant-2",
            "message": {
                "id": "msg-2",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "Second answer."}],
            },
        },
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return transcript


def get_root_observations(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]


def read_session_entry(isolated_hook_state: Path, hook_module: Any, session_id: str, transcript: Path) -> dict[str, Any]:
    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    key = hook_module.get_session_state_key(session_id, str(transcript))
    return state[key]


def test_persisted_trace_id_matches_emitted_trace_id_seeded(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session_id = "session-seeded-pin"
    transcript = write_two_turn_transcript(tmp_path, session_id)
    config = hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", trace_seed="fixed-seed"
    )

    emitted = hook_module.emit_new_turns_from_transcript(fake_langfuse, config, session_id, transcript)

    assert emitted == 2
    roots = get_root_observations(fake_langfuse)
    entry = read_session_entry(isolated_hook_state, hook_module, session_id, transcript)
    assert entry["latest_trace_id"] == roots[-1].trace_id
    # The seeded id is genuinely the one derived from CC_LANGFUSE_TRACE_SEED,
    # not just an artifact of the fake's auto-id path.
    assert entry["latest_trace_id"] == hook_module.derive_turn_trace_id("fixed-seed", 2)


def test_persisted_trace_id_matches_emitted_trace_id_unseeded(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session_id = "session-unseeded-pin"
    transcript = write_two_turn_transcript(tmp_path, session_id)
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    emitted = hook_module.emit_new_turns_from_transcript(fake_langfuse, config, session_id, transcript)

    assert emitted == 2
    roots = get_root_observations(fake_langfuse)
    entry = read_session_entry(isolated_hook_state, hook_module, session_id, transcript)
    assert entry["latest_trace_id"] == roots[-1].trace_id


def test_persisted_trace_id_tracks_the_latest_turn_not_the_first(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session_id = "session-latest-pin"
    transcript = write_two_turn_transcript(tmp_path, session_id)
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, session_id, transcript)

    roots = get_root_observations(fake_langfuse)
    first_id, second_id = roots[0].trace_id, roots[1].trace_id
    assert first_id != second_id

    entry = read_session_entry(isolated_hook_state, hook_module, session_id, transcript)
    assert entry["latest_trace_id"] == second_id
