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
    assert hook_module.build_generation_input(1, "ignored", [], []) is None


def test_generation_input_merges_previous_batch_and_ready_async_results(hook_module):
    # Regression: a previous tool batch used to shadow async results that
    # became ready at the same time; they are popped from the pending list
    # when consumed, so dropping them here lost them for good.
    assert hook_module.build_generation_input(
        1,
        "ignored",
        [{"tool_use_id": "toolu_read", "output": "file"}],
        [{"tool_result": {"tool_use_id": "toolu_agent", "output": "done"}}],
    ) == {
        "role": "tool",
        "tool_results": [
            {"tool_use_id": "toolu_read", "output": "file"},
            {"tool_use_id": "toolu_agent", "output": "done"},
        ],
    }


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


def test_tool_result_payload_carries_error_and_denial_kind(hook_module):
    failed = hook_module.get_tool_result_for_observation(
        {"content": "boom", "is_error": True}
    )
    assert failed.is_error is True
    assert failed.tool_denial_kind is None

    denied = hook_module.get_tool_result_for_observation(
        {"content": "Permission denied.", "is_error": True, "tool_denial_kind": "permission-rule"}
    )
    assert denied.is_error is True
    assert denied.tool_denial_kind == "permission-rule"

    ok = hook_module.get_tool_result_for_observation({"content": "fine"})
    assert ok.is_error is False
    assert ok.tool_denial_kind is None


def test_tool_observation_status_names_failure_class_and_denial(hook_module):
    ok_level, ok_message = hook_module.get_tool_observation_status(
        hook_module.ToolResultForObservation()
    )
    assert (ok_level, ok_message) == (None, None)

    error_level, error_message = hook_module.get_tool_observation_status(
        hook_module.ToolResultForObservation(is_error=True)
    )
    assert (error_level, error_message) == ("ERROR", "Tool call failed")

    denial_level, denial_message = hook_module.get_tool_observation_status(
        hook_module.ToolResultForObservation(is_error=True, tool_denial_kind="user-rejected")
    )
    assert (denial_level, denial_message) == ("ERROR", "Permission denied")


def test_tool_metadata_carries_denial_kind_only_when_denied(hook_module):
    metadata = hook_module.build_tool_metadata(
        "Bash",
        "toolu_bash",
        None,
        hook_module.ToolResultForObservation(is_error=True, tool_denial_kind="user-rejected"),
        None,
    )
    assert metadata["denial_kind"] == "user-rejected"

    ok_metadata = hook_module.build_tool_metadata(
        "Bash",
        "toolu_bash",
        None,
        hook_module.ToolResultForObservation(),
        None,
    )
    assert "denial_kind" not in ok_metadata


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


def test_async_final_result_reaches_generation_input_despite_tool_batch(
    hook_module,
    fake_langfuse,
):
    """Regression: when a normal tool result and a ready async agent result
    both precede an assistant message, the async final result vanished from
    every generation input."""
    rows = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "uuid": "u1",
         "message": {"role": "user", "content": "Start agent then run a command."}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01.000Z", "uuid": "a1",
         "message": {"id": "msg-1", "role": "assistant", "model": "claude-test", "usage": {"input_tokens": 1, "output_tokens": 1},
                     "content": [{"type": "tool_use", "id": "toolu_bg", "name": "Agent", "input": {}}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:02.000Z", "uuid": "tr1",
         "toolUseResult": {"status": "async_launched", "isAsync": True},
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_bg",
              "content": [{"type": "text", "text": "Async agent launched successfully."}]}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:03.000Z", "uuid": "a2",
         "message": {"id": "msg-2", "role": "assistant", "model": "claude-test", "usage": {"input_tokens": 1, "output_tokens": 1},
                     "content": [{"type": "tool_use", "id": "toolu_bash", "name": "Bash",
                                  "input": {"command": "ls"}}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:05.000Z", "uuid": "tr2",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_bash",
              "content": [{"type": "text", "text": "bash output"}]}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:06.000Z", "uuid": "n1",
         "origin": {"kind": "task-notification"},
         "message": {"role": "user", "content": (
             "<task-notification><tool-use-id>toolu_bg</tool-use-id>"
             "<result>Async final result.</result></task-notification>")}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:07.000Z", "uuid": "a3",
         "message": {"id": "msg-3", "role": "assistant", "model": "claude-test", "usage": {"input_tokens": 1, "output_tokens": 1},
                     "content": [{"type": "text", "text": "All done."}]}},
    ]
    turn = hook_module.build_turns(rows)[0]

    hook_module.emit_turn_observations(fake_langfuse, None, turn, None)

    generation_inputs = [
        observation.kwargs.get("input")
        for observation in fake_langfuse.observations
        if observation.as_type == "generation"
    ]
    assert len(generation_inputs) == 3
    final_generation_results = generation_inputs[2]["tool_results"]
    assert [result["tool_use_id"] for result in final_generation_results] == [
        "toolu_bash",
        "toolu_bg",
    ]
    assert final_generation_results[1]["output"] == "Async final result."


def test_assistant_message_without_usage_emits_a_span_not_a_generation(hook_module, fake_langfuse):
    # A usage-less assistant message is not a billable LLM call: downstream token
    # metering treats every generation as meterable, so it must emit as a span.
    rows = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "uuid": "u1",
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01.000Z", "uuid": "a1",
         "message": {"id": "msg-1", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "text", "text": "no api usage recorded"}]}},
    ]
    turn = hook_module.build_turns(rows)[0]

    hook_module.emit_turn_observations(fake_langfuse, None, turn, None)

    llm_calls = [o for o in fake_langfuse.observations if o.kwargs.get("metadata", {}).get("assistant_index") == 0]
    assert len(llm_calls) == 1
    assert llm_calls[0].as_type == "span"
    assert llm_calls[0].kwargs["metadata"]["usage_missing"] is True
    assert llm_calls[0].kwargs["metadata"]["model"] == "claude-test"
    assert "usage_details" not in llm_calls[0].kwargs


def test_tool_observations_carry_error_level_and_permission_denial_signals(hook_module, fake_langfuse):
    # One assistant turn firing four tool calls: a success, a plain failure,
    # a user-rejected denial, and a permission-rule denial — pinning all four
    # outcomes side by side so a regression in one can't hide behind the others.
    rows = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "uuid": "u1",
         "message": {"role": "user", "content": "Run four tools."}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01.000Z", "uuid": "a1",
         "message": {"id": "msg-1", "role": "assistant", "model": "claude-test",
                     "usage": {"input_tokens": 1, "output_tokens": 1},
                     "content": [
                         {"type": "tool_use", "id": "toolu_ok", "name": "Read", "input": {"file_path": "a"}},
                         {"type": "tool_use", "id": "toolu_fail", "name": "Bash", "input": {"command": "false"}},
                         {"type": "tool_use", "id": "toolu_user_denied", "name": "Bash", "input": {"command": "rm -rf /"}},
                         {"type": "tool_use", "id": "toolu_rule_denied", "name": "Bash", "input": {"command": "sf org delete"}},
                     ]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:02.000Z", "uuid": "tr1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_ok", "content": "ok"}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:03.000Z", "uuid": "tr2",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_fail", "content": "boom", "is_error": True}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:04.000Z", "uuid": "tr3",
         "toolDenialKind": "user-rejected",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_user_denied", "is_error": True,
              "content": "The user doesn't want to proceed with this tool use."}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:05.000Z", "uuid": "tr4",
         "toolDenialKind": "permission-rule",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_rule_denied", "is_error": True,
              "content": "Permission to use Bash has been denied."}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:06.000Z", "uuid": "a2",
         "message": {"id": "msg-2", "role": "assistant", "model": "claude-test",
                     "usage": {"input_tokens": 1, "output_tokens": 1},
                     "content": [{"type": "text", "text": "Done."}]}},
    ]
    turn = hook_module.build_turns(rows)[0]

    hook_module.emit_turn_observations(fake_langfuse, None, turn, None)

    tools_by_id = {
        observation.kwargs["metadata"]["tool_id"]: observation
        for observation in fake_langfuse.observations
        if observation.as_type == "tool"
    }

    ok = tools_by_id["toolu_ok"]
    assert "level" not in ok.kwargs
    assert "status_message" not in ok.kwargs
    assert "denial_kind" not in ok.kwargs["metadata"]

    failed = tools_by_id["toolu_fail"]
    assert failed.kwargs["level"] == "ERROR"
    assert failed.kwargs["status_message"] == "Tool call failed"
    assert "denial_kind" not in failed.kwargs["metadata"]

    user_denied = tools_by_id["toolu_user_denied"]
    assert user_denied.kwargs["level"] == "ERROR"
    assert user_denied.kwargs["status_message"] == "Permission denied"
    assert user_denied.kwargs["metadata"]["denial_kind"] == "user-rejected"

    rule_denied = tools_by_id["toolu_rule_denied"]
    assert rule_denied.kwargs["level"] == "ERROR"
    assert rule_denied.kwargs["status_message"] == "Permission denied"
    assert rule_denied.kwargs["metadata"]["denial_kind"] == "permission-rule"
