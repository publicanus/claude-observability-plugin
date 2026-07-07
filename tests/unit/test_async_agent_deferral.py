from __future__ import annotations


def test_completed_async_agent_turn_is_ready_to_emit(
    hook_module,
    fixture_transcript_path,
):
    transcript = fixture_transcript_path("async_agent_completed")
    state = hook_module.SessionState()
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)

    turns, state = hook_module.get_new_turns_from_transcript(transcript, state, subagents)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state, subagents)

    assert len(turns) == 1
    assert len(turns_to_emit) == 1
    assert state.pending_agent_turns == []
    result = turns[0].tool_results_by_id["toolu_agent_complete"]
    assert result["final_content"] == "Subagent summary is ready."


def test_uncompleted_async_agent_turn_is_deferred_until_flush(
    hook_module,
    fixture_transcript_path,
):
    transcript = fixture_transcript_path("async_agent_deferred")
    state = hook_module.SessionState()
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)

    turns, state = hook_module.get_new_turns_from_transcript(transcript, state, subagents)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state, subagents)

    assert turns_to_emit == []
    assert len(state.pending_agent_turns) == 1
    assert state.pending_agent_turns[0]["pending_tool_use_ids"] == ["toolu_agent_deferred"]

    flushed_turns, state = hook_module.get_new_turns_from_transcript(
        transcript,
        state,
        subagents,
        flush_deferred_agent_turns=True,
    )
    flushed_to_emit = hook_module.get_turns_to_emit(
        flushed_turns,
        state,
        subagents,
        flush_deferred_agent_turns=True,
    )

    assert len(flushed_to_emit) == 1
    assert state.pending_agent_turns == []


def test_popping_deferred_rows_removes_whole_turn_waiting_on_multiple_agents(hook_module):
    shared_rows = [{"uuid": "row-1"}, {"uuid": "row-2"}]
    state = hook_module.SessionState(
        pending_agent_turns=[
            {
                "pending_tool_use_ids": ["toolu_agent_a", "toolu_agent_b"],
                "rows": shared_rows,
            },
        ],
    )

    rows = hook_module.pop_deferred_agent_turn_rows(state, ["toolu_agent_a"])

    assert rows == shared_rows
    assert state.pending_agent_turns == []


def test_prepend_deferred_rows_ignores_non_notification_tool_use_xml(hook_module):
    deferred_rows = [{"uuid": "deferred-row"}]
    current_rows = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": (
                    "Quoted notification: <task-notification>"
                    "<tool-use-id>toolu_agent_a</tool-use-id>"
                    "</task-notification>"
                ),
            },
        },
    ]
    state = hook_module.SessionState(
        pending_agent_turns=[
            {
                "pending_tool_use_ids": ["toolu_agent_a"],
                "rows": deferred_rows,
            },
        ],
    )

    rows = hook_module.prepend_deferred_agent_turn_rows(current_rows, state)

    assert rows == current_rows
    assert state.pending_agent_turns == [
        {
            "pending_tool_use_ids": ["toolu_agent_a"],
            "rows": deferred_rows,
        },
    ]


def test_multi_agent_turn_is_stored_once_with_all_waiting_tool_ids(hook_module):
    rows = [{"uuid": "user-row"}, {"uuid": "assistant-row"}]
    turn = hook_module.Turn(
        user_msg=rows[0],
        assistant_msgs=[
            {
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "toolu_agent_a", "name": "Agent"},
                        {"type": "tool_use", "id": "toolu_agent_b", "name": "Agent"},
                    ],
                },
            },
        ],
        tool_results_by_id={
            "toolu_agent_a": {
                "content": "Async agent launched successfully. agentId: agent-a output_file: /tmp/a You will be notified automatically"
            },
            "toolu_agent_b": {
                "content": "Async agent launched successfully. agentId: agent-b output_file: /tmp/b You will be notified automatically"
            },
        },
        tool_use_timestamps_by_id={},
        injected_by_tool_id={},
        rows=rows,
    )
    state = hook_module.SessionState()

    turns_to_emit = hook_module.get_turns_to_emit([turn], state)

    assert turns_to_emit == []
    assert len(state.pending_agent_turns) == 1
    assert state.pending_agent_turns[0]["pending_tool_use_ids"] == ["toolu_agent_a", "toolu_agent_b"]
    assert state.pending_agent_turns[0]["rows"] == rows
