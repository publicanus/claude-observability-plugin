"""Pins where the hook may send: Langfuse Cloud EU by default, never Langfuse
Cloud's US, HIPAA or Japan regions, and any other address as given.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


EU = "https://cloud.langfuse.com"
CONFIG_VARS = [
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "CC_LANGFUSE_PUBLIC_KEY",
    "CC_LANGFUSE_SECRET_KEY",
    "CC_LANGFUSE_BASE_URL",
]


@pytest.fixture(autouse=True)
def clean_env(isolated_hook_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{name}", raising=False)


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")


@pytest.fixture
def nothing_may_be_sent(hook_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every path in main() that talks to Langfuse fails the test when reached."""
    monkeypatch.setattr(hook_module, "create_langfuse_client", lambda config: pytest.fail("trace client built"))
    monkeypatch.setattr(hook_module, "LangfuseClient", lambda *a, **k: pytest.fail("delivery probe sent"))
    monkeypatch.setattr(hook_module, "process_pending_hurts", lambda *a, **k: pytest.fail("/hurt notes sent"))


class AcceptingLangfuse:
    """Answers the delivery probe as a Langfuse that accepts the keys."""

    def get(self, path: str) -> Any:
        return type("Response", (), {"status": 200, "body": None})()


def run_main(hook_module: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, payload: dict) -> str:
    monkeypatch.setattr(hook_module, "read_hook_payload", lambda: payload)
    assert hook_module.main() == 0
    return capsys.readouterr().out


def write_one_turn_transcript(tmp_path: Path) -> Path:
    rows = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "sessionId": "sess",
            "uuid": "user-1",
            "message": {"role": "user", "content": "A question."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "sessionId": "sess",
            "uuid": "assistant-1",
            "message": {
                "id": "msg-1",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "An answer."}],
            },
        },
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return transcript


def test_no_address_set_sends_to_langfuse_cloud_eu(hook_module: Any, keys):
    assert hook_module.get_langfuse_config(None).host == EU


REFUSED = [
    ("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com", "US"),
    ("LANGFUSE_BASE_URL", "https://hipaa.cloud.langfuse.com", "HIPAA"),
    ("LANGFUSE_BASE_URL", "https://jp.cloud.langfuse.com", "Japan"),
    ("CLAUDE_PLUGIN_OPTION_LANGFUSE_BASE_URL", "https://jp.cloud.langfuse.com", "Japan"),
    ("CC_LANGFUSE_BASE_URL", "https://hipaa.cloud.langfuse.com", "HIPAA"),
    ("LANGFUSE_BASE_URL", "https://US.Cloud.Langfuse.COM", "US"),
    ("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com/", "US"),
    ("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com:443", "US"),
]


@pytest.mark.parametrize("variable, address, region", REFUSED)
def test_a_refused_address_sends_nothing_and_says_why_at_session_start(
    hook_module, keys, nothing_may_be_sent, monkeypatch, capsys, variable, address, region
):
    monkeypatch.setenv(variable, address)

    out = run_main(hook_module, monkeypatch, capsys, {"hook_event_name": "SessionStart"})

    message = json.loads(out)["systemMessage"]
    assert address in message
    assert region in message
    assert EU in message
    assert "EU project" in message
    assert "restart Claude Code" in message


@pytest.mark.parametrize("event", ["Stop", "SessionEnd"])
def test_a_refused_address_sends_no_traces_and_no_hurt_notes_on_later_firings(
    hook_module, keys, nothing_may_be_sent, monkeypatch, capsys, tmp_path, event
):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    payload = {
        "hook_event_name": event,
        "sessionId": "sess",
        "transcriptPath": str(write_one_turn_transcript(tmp_path)),
    }

    run_main(hook_module, monkeypatch, capsys, payload)


def test_a_refused_address_without_keys_shows_the_refusal_not_the_no_keys_message(
    hook_module, nothing_may_be_sent, monkeypatch, capsys
):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")

    out = run_main(hook_module, monkeypatch, capsys, {"hook_event_name": "SessionStart"})

    message = json.loads(out)["systemMessage"]
    assert message != hook_module.NO_KEYS_WARNING
    assert "https://us.cloud.langfuse.com" in message
    assert EU in message


def test_a_self_hosted_address_that_looks_like_a_cloud_host_still_gets_traces(
    hook_module, keys, fake_langfuse, monkeypatch, capsys, tmp_path
):
    address = "https://us.cloud.langfuse.com.example.com"
    monkeypatch.setenv("LANGFUSE_BASE_URL", address)
    built_for = []

    def client(config):
        built_for.append(config.host)
        return fake_langfuse

    monkeypatch.setattr(hook_module, "create_langfuse_client", client)
    monkeypatch.setattr(hook_module, "LangfuseClient", lambda *a, **k: AcceptingLangfuse())
    assert run_main(hook_module, monkeypatch, capsys, {"hook_event_name": "SessionStart"}) == ""
    payload = {
        "hook_event_name": "Stop",
        "sessionId": "sess",
        "transcriptPath": str(write_one_turn_transcript(tmp_path)),
    }

    out = run_main(hook_module, monkeypatch, capsys, payload)

    assert built_for == [address]
    assert fake_langfuse.observations
    assert out == ""
