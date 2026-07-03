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
    assert state.pending_agent_turns == {}
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
    assert set(state.pending_agent_turns) == {"toolu_agent_deferred"}

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
    assert state.pending_agent_turns == {}


def test_popping_deferred_rows_deduplicates_same_turn_waiting_on_multiple_agents(hook_module):
    shared_rows = [{"uuid": "row-1"}, {"uuid": "row-2"}]
    state = hook_module.SessionState(
        pending_agent_turns={
            "toolu_agent_a": shared_rows,
            "toolu_agent_b": shared_rows,
        }
    )

    rows = hook_module.pop_deferred_agent_turn_rows(state, ["toolu_agent_a"])

    assert rows == shared_rows
    assert state.pending_agent_turns == {}
