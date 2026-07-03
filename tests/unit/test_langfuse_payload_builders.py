from __future__ import annotations


def test_generation_input_tracks_user_tool_and_async_tool_contexts(hook_module):
    assert hook_module.build_generation_input(0, "hello", [], []) == {
        "role": "user",
        "content": "hello",
    }
    assert hook_module.build_generation_input(
        1,
        "ignored",
        [{"tool_use_id": "toolu_read", "output": "file"}],
        [],
    ) == {"role": "tool", "tool_results": [{"tool_use_id": "toolu_read", "output": "file"}]}
    assert hook_module.build_generation_input(
        1,
        "ignored",
        [],
        [{"tool_result": {"tool_use_id": "toolu_agent", "output": "done"}}],
    ) == {"role": "tool", "tool_results": [{"tool_use_id": "toolu_agent", "output": "done"}]}


def test_generation_output_lists_tool_calls_without_full_tool_input(hook_module):
    output = hook_module.build_generation_output(
        "I will read it.",
        [{"id": "toolu_read", "name": "Read", "input": {"file_path": "README.md"}}],
    )

    assert output == {
        "role": "assistant",
        "content": "I will read it.",
        "tool_calls": [{"id": "toolu_read", "name": "Read"}],
    }


def test_tool_result_payload_preserves_initial_and_final_async_outputs(hook_module):
    result = hook_module.get_tool_result_for_observation(
        {
            "content": "Async agent launched successfully.",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "final_content": "Final async output",
            "final_timestamp": "2026-01-01T00:00:05.000Z",
        }
    )

    assert result.output == "Async agent launched successfully."
    assert result.final_output == "Final async output"
    assert result.result_timestamp.isoformat() == "2026-01-01T00:00:01+00:00"
    assert result.final_result_timestamp.isoformat() == "2026-01-01T00:00:05+00:00"


def test_tool_metadata_uses_short_subagent_transcript_paths(hook_module, tmp_path):
    metadata = hook_module.build_tool_metadata(
        "Agent",
        "toolu_agent",
        None,
        hook_module.ToolResultForObservation(output_meta={"truncated": False}),
        {
            "agent_type": "general-purpose",
            "description": "Summarize docs",
            "path": tmp_path / "agent-a123.jsonl",
        },
    )

    assert metadata["tool_name"] == "Agent"
    assert metadata["tool_id"] == "toolu_agent"
    assert metadata["subagent_type"] == "general-purpose"
    assert metadata["subagent_description"] == "Summarize docs"
    assert metadata["subagent_transcript_path"] == "agent-a123.jsonl"


def test_trace_metadata_includes_session_turn_project_and_branch(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
):
    rows = read_fixture_jsonl(fixture_transcript_path("simple_turn"))
    turn = hook_module.build_turns(rows)[0]
    user_text, user_text_meta = hook_module.truncate_text(
        hook_module.extract_text_from_content(hook_module.get_content_from_row(turn.user_msg))
    )

    metadata = hook_module.build_trace_metadata(
        "12345678-abcd-4000-8000-123456789abc",
        7,
        turn,
        fixture_transcript_path("simple_turn"),
        user_text_meta,
    )

    assert user_text == "Say hello."
    assert metadata["session_id"] == "12345678-abcd-4000-8000-123456789abc"
    assert metadata["turn_number"] == 7
    assert metadata["transcript_path"] == "transcript.jsonl"
    assert metadata["cwd"] == "/repo"
    assert metadata["git_branch"] == "feature/test"
