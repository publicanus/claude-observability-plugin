"""Pins where the hook may send: Langfuse Cloud EU by default, never Langfuse
Cloud's US, HIPAA or Japan regions, and any other address as given.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import AcceptingLangfuse, run_main, write_one_turn_transcript, write_settings


EU = "https://cloud.langfuse.com"

pytestmark = pytest.mark.usefixtures("isolated_hook_state", "no_langfuse_env")

KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test"}


def write_own_settings(**env: str) -> None:
    """Writes env as the settings file of the session's repo: the working
    directory, which isolated_hook_state sets."""
    write_settings(Path.cwd(), env)


@pytest.fixture
def keys() -> None:
    write_own_settings(**KEYS)


def test_no_address_set_sends_to_langfuse_cloud_eu(hook_module: Any, keys):
    assert hook_module.get_langfuse_config(None).host == EU


REFUSED = [
    ("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com", "US"),
    ("LANGFUSE_BASE_URL", "https://hipaa.cloud.langfuse.com", "HIPAA"),
    ("LANGFUSE_BASE_URL", "https://jp.cloud.langfuse.com", "Japan"),
    ("CC_LANGFUSE_BASE_URL", "https://hipaa.cloud.langfuse.com", "HIPAA"),
    ("LANGFUSE_BASE_URL", "https://US.Cloud.Langfuse.COM", "US"),
    ("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com/", "US"),
    ("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com:443", "US"),
]


@pytest.mark.parametrize("variable, address, region", REFUSED)
def test_a_refused_address_sends_nothing_and_says_why_at_session_start(
    hook_module, nothing_may_be_sent, monkeypatch, capsys, variable, address, region
):
    write_own_settings(**KEYS, **{variable: address})

    out = run_main(hook_module, monkeypatch, capsys, {"hook_event_name": "SessionStart"})

    message = json.loads(out)["systemMessage"]
    assert address in message
    assert region in message
    assert EU in message
    assert "EU project" in message
    assert ".claude/settings.local.json" in message
    assert message.endswith("The hook reads that file again at the end of each turn.")


@pytest.mark.parametrize("event", ["Stop", "SessionEnd"])
def test_a_refused_address_sends_no_traces_and_no_hurt_notes_on_later_firings(
    hook_module, nothing_may_be_sent, monkeypatch, capsys, tmp_path, event
):
    write_own_settings(**KEYS, LANGFUSE_BASE_URL="https://us.cloud.langfuse.com")
    payload = {
        "hook_event_name": event,
        "sessionId": "sess",
        "transcriptPath": str(write_one_turn_transcript(tmp_path)),
    }

    run_main(hook_module, monkeypatch, capsys, payload)


def test_a_refused_address_without_keys_shows_the_refusal_not_the_no_keys_message(
    hook_module, nothing_may_be_sent, monkeypatch, capsys
):
    write_own_settings(LANGFUSE_BASE_URL="https://us.cloud.langfuse.com")

    out = run_main(hook_module, monkeypatch, capsys, {"hook_event_name": "SessionStart"})

    message = json.loads(out)["systemMessage"]
    assert message != hook_module.NO_KEYS_WARNING
    assert "https://us.cloud.langfuse.com" in message
    assert EU in message


def test_a_self_hosted_address_that_looks_like_a_cloud_host_still_gets_traces(
    hook_module, fake_langfuse, monkeypatch, capsys, tmp_path
):
    address = "https://us.cloud.langfuse.com.example.com"
    write_own_settings(**KEYS, LANGFUSE_BASE_URL=address)
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
