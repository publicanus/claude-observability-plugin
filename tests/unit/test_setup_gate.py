"""Pins the hook's answer to the setup skill's check (a UserPromptExpansion input):
a block when the repo's own .claude/settings.local.json holds both keys or the
address is refused, `{}` otherwise, whatever keys are set elsewhere.
"""

from __future__ import annotations

import json
import re
import subprocess
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


ALREADY_CONFIGURED = {"decision": "block", "reason": "Langfuse: tracing is already configured for this repo."}
BOTH_KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test"}
EMPTY_KEYS = {"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo the setup check runs in, as the skill runs it: no cwd in the input."""
    path = (tmp_path / "shop").resolve()
    path.mkdir()
    git(path, "init", "-q")
    git(path, "commit", "-q", "--allow-empty", "-m", "first")
    monkeypatch.chdir(path)
    return path


@pytest.fixture
def worktree(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = (tmp_path / "shop-feature").resolve()
    git(repo, "worktree", "add", "-q", str(path))
    monkeypatch.chdir(path)
    return path


def write_settings(checkout: Path, env: dict) -> None:
    path = checkout / ".claude" / "settings.local.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"env": env}), encoding="utf-8")


def run_setup_check(hook_module: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> Any:
    monkeypatch.setattr(hook_module, "read_hook_payload", lambda: {"hook_event_name": "UserPromptExpansion"})
    assert hook_module.main() == 0
    return json.loads(capsys.readouterr().out)


def test_keys_in_the_repos_own_file_block_with_already_configured(hook_module, monkeypatch, capsys, repo):
    write_settings(repo, BOTH_KEYS)

    assert run_setup_check(hook_module, monkeypatch, capsys) == ALREADY_CONFIGURED


@pytest.mark.parametrize("prefix", ["", "CLAUDE_PLUGIN_OPTION_"], ids=["environment variable", "plugin userConfig"])
def test_keys_set_only_outside_the_repos_own_file_answer_with_an_empty_object(
    hook_module, monkeypatch, capsys, repo, prefix
):
    monkeypatch.setenv(f"{prefix}LANGFUSE_PUBLIC_KEY", "pk-lf-elsewhere")
    monkeypatch.setenv(f"{prefix}LANGFUSE_SECRET_KEY", "sk-lf-elsewhere")

    assert run_setup_check(hook_module, monkeypatch, capsys) == {}


def test_empty_placeholders_in_the_repos_own_file_answer_with_an_empty_object(hook_module, monkeypatch, capsys, repo):
    write_settings(repo, EMPTY_KEYS)

    assert run_setup_check(hook_module, monkeypatch, capsys) == {}


@pytest.mark.parametrize(
    "worktree_env, main_env",
    [(EMPTY_KEYS, BOTH_KEYS), (BOTH_KEYS, None)],
    ids=["keys in the main checkout's file", "keys in the worktree's own file"],
)
def test_in_a_worktree_either_file_with_both_keys_blocks_with_already_configured(
    hook_module, monkeypatch, capsys, repo, worktree, worktree_env, main_env
):
    write_settings(worktree, worktree_env)
    if main_env is not None:
        write_settings(repo, main_env)

    assert run_setup_check(hook_module, monkeypatch, capsys) == ALREADY_CONFIGURED


def test_an_unreadable_settings_file_fails_naming_the_file(hook_module, monkeypatch, repo):
    path = repo / ".claude" / "settings.local.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(hook_module, "read_hook_payload", lambda: {"hook_event_name": "UserPromptExpansion"})

    with pytest.raises(hook_module.SettingsFileError, match="settings.local.json"):
        hook_module.main()


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
    assert matcher.search("mate-record:langfuse-setup")
    assert not matcher.search("langfuse-observability:langfuse-setup")
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
