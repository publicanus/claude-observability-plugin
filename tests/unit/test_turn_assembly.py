from __future__ import annotations


def test_build_turns_merges_split_assistant_rows(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
):
    rows = read_fixture_jsonl(fixture_transcript_path("simple_turn"))

    turns = hook_module.build_turns(rows)

    assert len(turns) == 1
    turn = turns[0]
    assert hook_module.extract_text_from_content(hook_module.get_content_from_row(turn.user_msg)) == "Say hello."
    assert len(turn.assistant_msgs) == 1
    content = hook_module.get_content_from_row(turn.assistant_msgs[0])
    assert [block["text"] for block in content] == ["Hello", "from Claude Code."]
    assert hook_module.get_usage_details_from_row(turn.assistant_msgs[0]) == {
        "input": 10,
        "output": 5,
    }


def test_build_turns_associates_tool_results_with_tool_use(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
):
    rows = read_fixture_jsonl(fixture_transcript_path("tool_turn"))

    turns = hook_module.build_turns(rows)

    assert len(turns) == 1
    turn = turns[0]
    assert [len(hook_module.get_tool_use_blocks(hook_module.get_content_from_row(row))) for row in turn.assistant_msgs] == [1, 0]
    assert turn.tool_results_by_id["toolu_read_1"] == {
        "content": "# Example",
        "timestamp": "2026-01-01T00:01:02.000Z",
    }
    assert turn.tool_use_timestamps_by_id["toolu_read_1"] == "2026-01-01T00:01:01.000Z"


def test_meta_rows_do_not_start_or_flush_turns(hook_module):
    rows = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "message": {"role": "user", "content": "Use a skill."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "message": {
                "id": "msg-skill",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_skill",
                        "name": "Skill",
                        "input": {"skill": "example"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "isMeta": True,
            "sourceToolUseID": "toolu_skill",
            "message": {"role": "user", "content": "Injected skill instructions"},
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:02.000Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_skill",
                        "content": "Skill result",
                    }
                ],
            },
        },
    ]

    turns = hook_module.build_turns(rows)

    assert len(turns) == 1
    assert turns[0].injected_by_tool_id == {"toolu_skill": "Injected skill instructions"}
    assert turns[0].tool_results_by_id["toolu_skill"]["content"] == "Skill result"


def _prompt(text, second):
    return {"type": "user", "timestamp": f"2026-01-01T00:00:{second:02d}.000Z", "message": {"role": "user", "content": text}}


def _interrupted(second):
    return {"type": "user", "timestamp": f"2026-01-01T00:00:{second:02d}.000Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}}


def _reply(message_id, second):
    return {"type": "assistant", "timestamp": f"2026-01-01T00:00:{second:02d}.000Z",
            "message": {"id": message_id, "role": "assistant", "content": [{"type": "text", "text": "Done."}]}}


def _prompts(hook_module, turns):
    return [hook_module.extract_text_from_content(hook_module.get_content_from_row(t.user_msg)) for t in turns]


def test_a_prompt_stopped_before_any_reply_is_a_turn_of_the_main_session(hook_module):
    rows = [_prompt("Write an essay.", 0), _interrupted(1), _prompt("Next.", 2), _reply("m", 3)]

    turns = hook_module.build_turns(rows, keep_prompts_stopped_before_reply=True)

    assert _prompts(hook_module, turns) == ["Write an essay.", "Next."]
    assert turns[0].assistant_msgs == []


def test_an_interrupt_row_with_no_prompt_before_it_is_no_turn(hook_module):
    rows = [_prompt("First.", 0), _reply("m1", 1), _interrupted(2), _interrupted(3), _prompt("Next.", 4), _reply("m2", 5)]

    turns = hook_module.build_turns(rows, keep_prompts_stopped_before_reply=True)

    assert _prompts(hook_module, turns) == ["First.", "Next."]


def test_without_the_main_session_switch_a_prompt_with_no_reply_is_no_turn(hook_module):
    rows = [_prompt("Write an essay.", 0), _interrupted(1), _prompt("Next.", 2), _reply("m", 3)]

    turns = hook_module.build_turns(rows)

    assert _prompts(hook_module, turns) == ["Next."]


def test_an_agent_message_after_the_turn_ended_starts_a_turn(hook_module):
    message = {"type": "user", "timestamp": "2026-01-01T00:00:01.000Z", "isMeta": True,
               "origin": {"kind": "peer", "from": "a-agent", "senderTaskId": "a-agent"},
               "message": {"role": "user", "content": "A finding for you."}}
    rows = [{"type": "system", "subtype": "stop_hook_summary", "timestamp": "2026-01-01T00:00:00.000Z", "isMeta": True},
            message, _reply("m", 2)]

    turns = hook_module.build_turns(rows)

    assert len(turns) == 1
    assert turns[0].user_msg is message
