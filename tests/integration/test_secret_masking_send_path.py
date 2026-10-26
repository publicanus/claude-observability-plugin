from __future__ import annotations

import json
from typing import Any


SECRETS_IN_FIXTURE = [
    "sk-ant-api03-FAKEfakeFAKEfake0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "s3cret",
    "two words",
    "xoxb-FAKE-test-token",
    "fakeBearerToken0123",
    "ghp_FAKEfake0123456789_FAKEfake0123456789",
]


def _emit(hook_module: Any, fake_langfuse: Any, fixture_transcript_path: Any, name: str, session_id: str) -> None:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, session_id, fixture_transcript_path(name)
    )
    assert emitted == 1


def _observation(fake_langfuse: Any, name: str) -> Any:
    matches = [obs for obs in fake_langfuse.observations if obs.name == name]
    assert len(matches) == 1, f"expected one observation named {name!r}"
    return matches[0]


def _sent_input_and_output(observation: Any) -> str:
    return json.dumps(
        [observation.kwargs.get("input"), observation.kwargs.get("output"), observation.output],
        ensure_ascii=False,
    )


def test_secrets_reach_the_sdk_only_as_the_marker(
    hook_module, fixture_transcript_path, fake_langfuse, isolated_hook_state
):
    _emit(hook_module, fake_langfuse, fixture_transcript_path, "secrets_turn", "session-secrets")

    for observation in fake_langfuse.observations:
        sent = _sent_input_and_output(observation)
        for secret in SECRETS_IN_FIXTURE:
            assert secret not in sent, f"{secret!r} reached the SDK in {observation.name!r}"

    bash = _observation(fake_langfuse, "Tool: Bash")
    assert bash.kwargs["input"] == {
        "command": "aws s3 ls --access-key [REDACTED]",
        "description": "List buckets",
    }
    assert "password=[REDACTED]&sslmode=require" in bash.output
    assert "DB_PASSWORD=[REDACTED]" in bash.output

    subagent = _observation(fake_langfuse, "Subagent: Post to Slack")
    assert subagent.kwargs["input"] == {"role": "user", "content": "Post with [REDACTED]"}
    assert subagent.output == {
        "role": "assistant",
        "content": "Use the header Authorization: Bearer [REDACTED]",
    }


def test_a_turn_without_secrets_reaches_the_sdk_as_before(
    hook_module, fixture_transcript_path, fake_langfuse, isolated_hook_state
):
    _emit(hook_module, fake_langfuse, fixture_transcript_path, "tool_turn", "session-tool")

    assert _observation(fake_langfuse, "Conversational Turn").kwargs["input"] == {
        "role": "user",
        "content": "Read the file.",
    }
    read = _observation(fake_langfuse, "Tool: Read")
    assert read.kwargs["input"] == {"file_path": "README.md"}
    assert read.output == "# Example"
    assert _observation(fake_langfuse, "LLM Call 2").kwargs["output"]["content"] == "The file starts with a heading."
