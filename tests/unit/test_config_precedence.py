"""Pins _opt's config precedence: the plain env var over the plugin's userConfig value.

A project's .claude/settings.local.json `env` block reaches the hook as plain env
vars, so the plain env var must win, or one machine-wide userConfig value would
override every repo's own setting.
"""

from typing import Any


def test_plain_env_var_wins_over_wizard_option(hook_module: Any, monkeypatch):
    monkeypatch.setenv("CC_LANGFUSE_TRACE_SEED", "seed-repo")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_TRACE_SEED", "seed-machine")

    assert hook_module._opt("CC_LANGFUSE_TRACE_SEED") == "seed-repo"


def test_wizard_option_is_the_fallback_when_no_env_var_is_set(hook_module: Any, monkeypatch):
    monkeypatch.delenv("CC_LANGFUSE_TRACE_SEED", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_TRACE_SEED", "seed-machine")

    assert hook_module._opt("CC_LANGFUSE_TRACE_SEED") == "seed-machine"


def test_empty_env_var_falls_through_to_wizard_option(hook_module: Any, monkeypatch):
    monkeypatch.setenv("CC_LANGFUSE_TRACE_SEED", "")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_TRACE_SEED", "seed-machine")

    assert hook_module._opt("CC_LANGFUSE_TRACE_SEED") == "seed-machine"


def test_neither_set_yields_empty_string(hook_module: Any, monkeypatch):
    monkeypatch.delenv("CC_LANGFUSE_TRACE_SEED", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_TRACE_SEED", raising=False)

    assert hook_module._opt("CC_LANGFUSE_TRACE_SEED") == ""
