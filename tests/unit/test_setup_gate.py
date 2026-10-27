"""Pins the hook's answer to the setup skill's check (a UserPromptExpansion input):
a block when keys are set or the address is refused, `{}` when there are no keys.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
LAUNCHER_COMMAND = 'sh "${CLAUDE_PLUGIN_ROOT}"/hooks/run_hook.sh "${CLAUDE_PLUGIN_ROOT}"/hooks/langfuse_hook.py'
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


@pytest.fixture(autouse=True)
def nothing_may_be_sent(hook_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_module, "create_langfuse_client", lambda config: pytest.fail("trace client built"))
    monkeypatch.setattr(hook_module, "process_pending_hurts", lambda *a, **k: pytest.fail("/hurt notes sent"))


def run_setup_check(hook_module: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> Any:
    monkeypatch.setattr(hook_module, "read_hook_payload", lambda: {"hook_event_name": "UserPromptExpansion"})
    assert hook_module.main() == 0
    return json.loads(capsys.readouterr().out)


def test_keys_set_blocks_with_already_configured(hook_module, monkeypatch, capsys):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    assert run_setup_check(hook_module, monkeypatch, capsys) == {
        "decision": "block",
        "reason": "Langfuse: tracing is already configured for this repo.",
    }


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty placeholder"])
def test_no_keys_answers_with_an_empty_object(hook_module, monkeypatch, capsys, value):
    if value is not None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", value)
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", value)

    assert run_setup_check(hook_module, monkeypatch, capsys) == {}


def test_refused_address_blocks_with_the_refusal(hook_module, monkeypatch, capsys):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")

    answer = run_setup_check(hook_module, monkeypatch, capsys)

    assert answer["decision"] == "block"
    assert answer["reason"].startswith("Langfuse: https://us.cloud.langfuse.com is Langfuse Cloud's US region.")


def load_hooks() -> dict:
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]


def test_the_gate_matches_only_the_setup_skill():
    [entry] = load_hooks()["UserPromptExpansion"]
    matcher = re.compile(entry["matcher"])

    assert matcher.search("langfuse-setup")
    assert matcher.search("langfuse-observability:langfuse-setup")
    assert not matcher.search("setup")
    assert not matcher.search("other-plugin:langfuse-setup")


def test_every_hook_entry_runs_through_the_launcher():
    commands = {
        event: {hook["command"] for entry in entries for hook in entry["hooks"]}
        for event, entries in load_hooks().items()
    }
    session_start_command = LAUNCHER_COMMAND.replace("run_hook.sh ", "run_hook.sh --warn-if-cannot-run ", 1)

    assert commands.pop("SessionStart") == {session_start_command}
    assert commands and all(event_commands == {LAUNCHER_COMMAND} for event_commands in commands.values())
