"""Pins that a session is traced only with the keys and the address in its repo's
own .claude/settings.local.json: the worktree's own file when it holds both keys,
else the main checkout's. Keys and addresses set anywhere else are not used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

import pytest

from conftest import AcceptingLangfuse, config_for, run_main, write_one_turn_transcript, write_settings


EU = "https://cloud.langfuse.com"
KEYS_A = {"LANGFUSE_PUBLIC_KEY": "pk-lf-a", "LANGFUSE_SECRET_KEY": "sk-lf-a"}
EMPTY_KEYS = {"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}
# Where a value set outside the repo's own file reaches the hook: a plain
# environment variable (terminal, direnv) or the plugin's machine-wide userConfig.
OUTSIDE = pytest.mark.parametrize("prefix", ["", "CLAUDE_PLUGIN_OPTION_"], ids=["environment", "userConfig"])
NOT_USED_LINE = (
    "Langfuse: keys set outside this repo's .claude/settings.local.json, such as in your "
    "terminal or the plugin's settings, are not used."
)

pytestmark = pytest.mark.usefixtures("isolated_hook_state", "no_langfuse_env")


def set_outside(monkeypatch: pytest.MonkeyPatch, prefix: str, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setenv(f"{prefix}{name}", value)


def stop_payload(cwd: Path, tmp_path: Path) -> dict:
    return {
        "hook_event_name": "Stop",
        "sessionId": "sess",
        "transcriptPath": str(write_one_turn_transcript(tmp_path)),
        "cwd": str(cwd),
    }


@pytest.fixture
def clients_built(hook_module: Any, fake_langfuse: Any, monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """The configs the hook builds a trace client for, in order."""
    built: List[Any] = []

    def client(config: Any) -> Any:
        built.append(config)
        return fake_langfuse

    monkeypatch.setattr(hook_module, "create_langfuse_client", client)
    monkeypatch.setattr(hook_module, "LangfuseClient", lambda *a, **k: AcceptingLangfuse())
    monkeypatch.setattr(hook_module, "process_pending_hurts", lambda *a, **k: 0)
    return built


def sent_with(config: Any) -> tuple:
    return (config.public_key, config.secret_key, config.host)


@OUTSIDE
def test_the_repos_own_keys_and_address_win_over_ones_set_outside(
    hook_module, clients_built, monkeypatch, capsys, tmp_path, repo, prefix
):
    write_settings(repo, {**KEYS_A, "LANGFUSE_BASE_URL": "https://a.example.test"})
    set_outside(
        monkeypatch,
        prefix,
        LANGFUSE_PUBLIC_KEY="pk-lf-b",
        LANGFUSE_SECRET_KEY="sk-lf-b",
        LANGFUSE_BASE_URL="https://b.example.test",
    )

    run_main(hook_module, monkeypatch, capsys, stop_payload(repo, tmp_path))

    assert [sent_with(config) for config in clients_built] == [("pk-lf-a", "sk-lf-a", "https://a.example.test")]


@OUTSIDE
@pytest.mark.parametrize("own_env", [None, EMPTY_KEYS], ids=["no file", "empty keys"])
@pytest.mark.parametrize("event", ["Stop", "SessionEnd"])
def test_without_its_own_keys_a_session_sends_nothing_whatever_keys_are_set_outside(
    hook_module, nothing_may_be_sent, monkeypatch, capsys, tmp_path, repo, prefix, own_env, event
):
    if own_env is not None:
        write_settings(repo, own_env)
    set_outside(monkeypatch, prefix, LANGFUSE_PUBLIC_KEY="pk-lf-b", LANGFUSE_SECRET_KEY="sk-lf-b")
    payload = {**stop_payload(repo, tmp_path), "hook_event_name": event}

    assert run_main(hook_module, monkeypatch, capsys, payload) == ""


@OUTSIDE
def test_the_not_traced_line_says_that_keys_set_outside_are_not_used(
    hook_module, nothing_may_be_sent, monkeypatch, capsys, repo, prefix
):
    set_outside(monkeypatch, prefix, LANGFUSE_PUBLIC_KEY="pk-lf-b", LANGFUSE_SECRET_KEY="sk-lf-b")

    out = run_main(hook_module, monkeypatch, capsys, {"hook_event_name": "SessionStart", "cwd": str(repo)})

    lines = json.loads(out)["systemMessage"].split("\n")
    assert lines[:2] == [hook_module.NO_KEYS_WARNING, NOT_USED_LINE]


def test_without_keys_set_outside_the_not_traced_line_has_no_not_used_line(
    hook_module, nothing_may_be_sent, monkeypatch, capsys, repo
):
    out = run_main(hook_module, monkeypatch, capsys, {"hook_event_name": "SessionStart", "cwd": str(repo)})

    message = json.loads(out)["systemMessage"]
    assert message.startswith(hook_module.NO_KEYS_WARNING + "\n")
    assert NOT_USED_LINE not in message


def test_in_a_worktree_with_empty_keys_the_main_checkouts_keys_and_address_are_used(
    hook_module, clients_built, monkeypatch, capsys, tmp_path, repo, worktree
):
    write_settings(worktree, EMPTY_KEYS)
    write_settings(repo, {**KEYS_A, "LANGFUSE_BASE_URL": "https://main.example.test"})

    run_main(hook_module, monkeypatch, capsys, stop_payload(worktree, tmp_path))

    assert [sent_with(config) for config in clients_built] == [("pk-lf-a", "sk-lf-a", "https://main.example.test")]


def test_in_a_worktree_with_both_keys_its_own_keys_and_address_are_used(
    hook_module, clients_built, monkeypatch, capsys, tmp_path, repo, worktree
):
    write_settings(worktree, {"LANGFUSE_PUBLIC_KEY": "pk-lf-wt", "LANGFUSE_SECRET_KEY": "sk-lf-wt"})
    write_settings(repo, {**KEYS_A, "LANGFUSE_BASE_URL": "https://main.example.test"})

    run_main(hook_module, monkeypatch, capsys, stop_payload(worktree, tmp_path))

    assert [sent_with(config) for config in clients_built] == [("pk-lf-wt", "sk-lf-wt", EU)]


@OUTSIDE
def test_without_an_address_in_the_repos_file_turns_go_to_langfuse_cloud_eu(hook_module, monkeypatch, repo, prefix):
    write_settings(repo, KEYS_A)
    set_outside(monkeypatch, prefix, LANGFUSE_BASE_URL="https://example.test")

    assert config_for(hook_module, repo).host == EU


@pytest.mark.parametrize("name", ["LANGFUSE_BASE_URL", "CC_LANGFUSE_BASE_URL"])
def test_the_address_in_the_repos_file_is_used(hook_module, repo, name):
    write_settings(repo, {**KEYS_A, name: "https://self.example.test"})

    assert config_for(hook_module, repo).host == "https://self.example.test"


@pytest.fixture
def broken_file(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = repo / ".claude" / "settings.local.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    set_outside(monkeypatch, "", LANGFUSE_PUBLIC_KEY="pk-lf-b", LANGFUSE_SECRET_KEY="sk-lf-b")
    return path


def test_a_broken_settings_file_gives_one_not_traced_message_naming_it_at_session_start(
    hook_module, nothing_may_be_sent, monkeypatch, capsys, repo, broken_file
):
    out = run_main(hook_module, monkeypatch, capsys, {"hook_event_name": "SessionStart", "cwd": str(repo)})

    message = json.loads(out)["systemMessage"]
    assert str(broken_file) in message
    assert "This session is not traced until the file is fixed" in message
    assert hook_module.NO_KEYS_WARNING not in message


@pytest.mark.parametrize("event", ["Stop", "SessionEnd"])
def test_a_broken_settings_file_sends_nothing_and_stays_quiet_on_later_events(
    hook_module, nothing_may_be_sent, monkeypatch, capsys, tmp_path, repo, broken_file, event
):
    monkeypatch.setattr(hook_module, "start_waiting_turns_sender", lambda: pytest.fail("sender started"))
    payload = {**stop_payload(repo, tmp_path), "hook_event_name": event}

    assert run_main(hook_module, monkeypatch, capsys, payload) == ""
