from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_STEPS_DIR = REPO_ROOT / "hooks" / "no_keys_steps"
REPO_STEPS_DIR = Path(".claude") / "langfuse-observability" / "no-keys-steps"
SETUP_LINE = "To add keys, run /langfuse-observability:langfuse-setup, or by hand:"
PLATFORMS = {"darwin": "macos", "linux": "linux", "win32": "windows"}

KEY_VARS = [
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "CC_LANGFUSE_PUBLIC_KEY",
    "CC_LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "CC_LANGFUSE_BASE_URL",
]


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{name}", raising=False)


def run_main(hook_module: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, payload: dict) -> str:
    monkeypatch.setattr(hook_module, "read_hook_payload", lambda: payload)
    assert hook_module.main() == 0
    return capsys.readouterr().out


def session_start_message(
    hook_module: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, platform: str, cwd: Optional[Path]
) -> str:
    monkeypatch.setattr(sys, "platform", platform)
    payload: dict = {"hook_event_name": "SessionStart"}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return json.loads(run_main(hook_module, monkeypatch, capsys, payload))["systemMessage"]


def expected_message(steps: str) -> str:
    return "\n".join(["Langfuse: no keys for this repo, this session is not traced.", SETUP_LINE, steps])


def plugin_steps(os_name: str) -> str:
    return (PLUGIN_STEPS_DIR / f"{os_name}.txt").read_text(encoding="utf-8").rstrip("\n")


def write_repo_steps(repo: Path, os_name: str, data: bytes) -> Path:
    path = repo / REPO_STEPS_DIR / f"{os_name}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
def test_session_start_without_keys_shows_the_steps_for_this_system(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys, tmp_path, platform
):
    message = session_start_message(hook_module, monkeypatch, capsys, platform, tmp_path)

    assert message == expected_message(plugin_steps(PLATFORMS[platform]))


def test_the_windows_steps_differ_from_the_others():
    assert plugin_steps("windows") not in (plugin_steps("macos"), plugin_steps("linux"))


def test_another_system_gets_the_warning_and_the_skill_name_only(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys, tmp_path
):
    message = session_start_message(hook_module, monkeypatch, capsys, "freebsd14", tmp_path)

    assert message == "\n".join([hook_module.NO_KEYS_WARNING, SETUP_LINE])


def test_a_repo_text_wins_for_its_own_system_only(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys, tmp_path
):
    write_repo_steps(tmp_path, "macos", b"Ask Anna for the team keys.\n")

    macos = session_start_message(hook_module, monkeypatch, capsys, "darwin", tmp_path)
    linux = session_start_message(hook_module, monkeypatch, capsys, "linux", tmp_path)

    assert macos == expected_message("Ask Anna for the team keys.")
    assert linux == expected_message(plugin_steps("linux"))


def test_an_input_without_cwd_finds_the_repo_text_in_the_working_directory(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys, tmp_path
):
    write_repo_steps(tmp_path, "linux", b"Run make langfuse-keys.\n")
    monkeypatch.chdir(tmp_path)

    message = session_start_message(hook_module, monkeypatch, capsys, "linux", None)

    assert message == expected_message("Run make langfuse-keys.")


def test_an_unreadable_repo_text_is_named_in_the_message(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys, tmp_path
):
    path = write_repo_steps(tmp_path, "linux", b"\xff\xfe not utf-8")

    message = session_start_message(hook_module, monkeypatch, capsys, "linux", tmp_path)

    steps = message.split("\n")[2]
    assert steps.startswith(f"Langfuse: cannot read {path}: ")


def test_empty_placeholder_keys_still_count_as_no_keys(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")

    message = session_start_message(hook_module, monkeypatch, capsys, "linux", tmp_path)

    assert message == expected_message(plugin_steps("linux"))


def test_the_named_setup_command_is_this_plugins_skill(hook_module):
    plugin_name = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    namespace, skill = hook_module.SETUP_COMMAND.removeprefix("/").split(":")

    assert hook_module.SETUP_COMMAND in SETUP_LINE
    assert namespace == plugin_name
    assert (REPO_ROOT / "skills" / skill / "SKILL.md").is_file()


def test_session_start_with_keys_says_nothing(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys
):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setattr(
        hook_module, "create_langfuse_client", lambda config: pytest.fail("SessionStart must not trace")
    )

    assert run_main(hook_module, monkeypatch, capsys, {"hook_event_name": "SessionStart"}) == ""


@pytest.mark.parametrize("event", ["Stop", "SessionEnd"])
def test_later_firings_without_keys_stay_silent(
    hook_module, isolated_hook_state, no_keys, monkeypatch, capsys, event
):
    # The notice comes once, at SessionStart — not again on every turn.
    assert run_main(hook_module, monkeypatch, capsys, {"hook_event_name": event}) == ""
