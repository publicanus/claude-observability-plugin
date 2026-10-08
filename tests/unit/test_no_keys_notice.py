from __future__ import annotations

import json
from typing import Any

import pytest


KEY_VARS = [
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "CC_LANGFUSE_PUBLIC_KEY",
    "CC_LANGFUSE_SECRET_KEY",
]


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{name}", raising=False)


def run_main(hook_module: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, event: str) -> str:
    monkeypatch.setattr(hook_module, "read_hook_payload", lambda: {"hook_event_name": event})
    assert hook_module.main() == 0
    return capsys.readouterr().out


def test_session_start_without_keys_tells_the_operator(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys
):
    out = run_main(hook_module, monkeypatch, capsys, "SessionStart")

    assert json.loads(out) == {"systemMessage": hook_module.NO_KEYS_WARNING}


def test_session_start_with_keys_says_nothing(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys
):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setattr(
        hook_module, "create_langfuse_client", lambda config: pytest.fail("SessionStart must not trace")
    )

    assert run_main(hook_module, monkeypatch, capsys, "SessionStart") == ""


@pytest.mark.parametrize("event", ["Stop", "SessionEnd"])
def test_later_firings_without_keys_stay_silent(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys, event
):
    # The notice comes once, at SessionStart — not again on every turn.
    assert run_main(hook_module, monkeypatch, capsys, event) == ""
