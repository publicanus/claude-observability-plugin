from __future__ import annotations

from typing import Any


def make_user_row(text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "uuid": "user-1",
        "message": {"role": "user", "content": text},
    }


def make_skill_tool_use_row(skill: str) -> dict[str, Any]:
    """Invocation path 1: Claude calls the Skill tool itself."""
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:01.000Z",
        "uuid": "assistant-tool",
        "message": {
            "id": "msg-tool",
            "role": "assistant",
            "model": "claude-test",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_skill",
                    "name": "Skill",
                    "input": {"skill": skill, "args": "do the thing"},
                }
            ],
        },
    }


def make_attributed_assistant_row(skill: str, uuid: str = "assistant-attr") -> dict[str, Any]:
    """Invocation path 2: slash command — the harness expands the skill and
    marks the assistant rows with a top-level attributionSkill field."""
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:02.000Z",
        "uuid": uuid,
        "attributionSkill": skill,
        "message": {
            "id": f"msg-{uuid}",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": "Greetings, esteemed colleague."}],
        },
    }


def collect_tags(hook_module, rows: list[dict[str, Any]]) -> list[str]:
    turns = hook_module.build_turns(rows)
    assert len(turns) == 1
    return hook_module.collect_skill_tags(turns[0])


def test_skill_invoked_via_tool_call_is_tagged(hook_module):
    rows = [
        make_user_row("What are the current model ids?"),
        make_skill_tool_use_row("claude-api"),
    ]

    assert collect_tags(hook_module, rows) == ["skill:claude-api"]


def test_skill_invoked_via_slash_command_is_tagged(hook_module):
    # Slash commands never produce a Skill tool_use block; the skill shows up
    # only as attributionSkill on the assistant rows (GitHub #15).
    rows = [
        make_user_row("<command-message>greeting-style</command-message>\n<command-name>/greeting-style</command-name>"),
        make_attributed_assistant_row("greeting-style"),
    ]

    assert collect_tags(hook_module, rows) == ["skill:greeting-style"]


def test_skills_from_both_invocation_paths_are_tagged(hook_module):
    rows = [
        make_user_row("Do two things."),
        make_skill_tool_use_row("claude-api"),
        make_attributed_assistant_row("greeting-style"),
    ]

    assert sorted(collect_tags(hook_module, rows)) == [
        "skill:claude-api",
        "skill:greeting-style",
    ]


def test_same_skill_from_both_paths_is_tagged_once(hook_module):
    rows = [
        make_user_row("Do one thing."),
        make_skill_tool_use_row("greeting-style"),
        make_attributed_assistant_row("greeting-style"),
    ]

    assert collect_tags(hook_module, rows) == ["skill:greeting-style"]
