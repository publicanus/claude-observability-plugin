"""Pins the order the hook picks a trace's user in.

Explicit config (env var or plugin userConfig) wins; then the Claude login
email from ~/.claude.json; then `git config user.email` in the session's
working directory; with none of these, the trace carries no user. Every test
points the hook at a temp ~/.claude.json and a temp git config, never the
real ones.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

import pytest

from conftest import config_for, write_settings


USER_ID_VARS = ["LANGFUSE_USER_ID", "CC_LANGFUSE_USER_ID"]


@pytest.fixture
def traced(hook_module: Any, isolated_hook_state: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No explicit user, an absent temp ~/.claude.json and an empty git config."""
    for name in USER_ID_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{name}", raising=False)
    return hook_module.CLAUDE_JSON_FILE


def write_login(claude_json: Path, email: Optional[str]) -> None:
    claude_json.write_text(json.dumps({"oauthAccount": {"emailAddress": email}}), encoding="utf-8")


def make_repo(path: Path, email: Optional[str]) -> Path:
    """A git repo at path whose own settings file holds both keys."""
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    write_settings(path, {"LANGFUSE_PUBLIC_KEY": "public", "LANGFUSE_SECRET_KEY": "secret"})
    if email is not None:
        subprocess.run(["git", "-C", str(path), "config", "user.email", email], check=True)
    return path


@pytest.mark.parametrize(
    "var", ["LANGFUSE_USER_ID", "CC_LANGFUSE_USER_ID", "CLAUDE_PLUGIN_OPTION_LANGFUSE_USER_ID"]
)
def test_explicit_config_wins_over_login_and_git(hook_module, traced, monkeypatch, tmp_path, var):
    monkeypatch.setenv(var, "explicit-user")
    write_login(traced, "login@example.com")
    repo = make_repo(tmp_path / "repo", "git@example.com")

    assert config_for(hook_module, repo).user_id == "explicit-user"


def test_login_email_is_the_user_when_no_explicit_config(hook_module, traced, tmp_path):
    write_login(traced, "login@example.com")
    repo = make_repo(tmp_path / "repo", "git@example.com")

    assert config_for(hook_module, repo).user_id == "login@example.com"


@pytest.mark.parametrize(
    "claude_json_content",
    [
        None,  # no ~/.claude.json at all
        json.dumps({"numStartups": 3}),  # not logged in
        json.dumps({"oauthAccount": {"emailAddress": ""}}),
    ],
    ids=["no-file", "no-oauth-account", "empty-email"],
)
def test_git_email_of_the_session_dir_is_the_user_without_a_login_email(
    hook_module, traced, tmp_path, claude_json_content
):
    if claude_json_content is not None:
        traced.write_text(claude_json_content, encoding="utf-8")
    repo = make_repo(tmp_path / "repo", "git@example.com")

    assert config_for(hook_module, repo).user_id == "git@example.com"


def test_no_user_when_neither_login_nor_git_email(hook_module, traced, tmp_path):
    cwd = make_repo(tmp_path / "repo", None)

    config = config_for(hook_module, cwd)

    assert config is not None
    assert config.user_id is None


def run_hook(
    hook_module: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, tmp_path: Path, fake_langfuse: Any, cwd: Path
) -> tuple[Optional[str], Optional[str]]:
    """Runs one Stop firing in cwd; returns the trace user and the system message shown, if any."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        hook_module,
        "read_hook_payload",
        lambda: {
            "hook_event_name": "Stop",
            "session_id": "sess-user",
            "transcript_path": str(transcript),
            "cwd": str(cwd),
        },
    )
    seen = []

    def create_client(config: Any) -> Any:
        seen.append(config)
        return fake_langfuse

    monkeypatch.setattr(hook_module, "create_langfuse_client", create_client)

    assert hook_module.main() == 0
    out = capsys.readouterr().out
    message = json.loads(out)["systemMessage"] if out.strip() else None
    return seen[0].user_id, message


def test_hook_resolves_git_email_from_the_payload_cwd(
    hook_module, traced, monkeypatch, capsys, tmp_path, fake_langfuse
):
    repo = make_repo(tmp_path / "repo", "git@example.com")

    user, message = run_hook(hook_module, monkeypatch, capsys, tmp_path, fake_langfuse, repo)

    assert (user, message) == ("git@example.com", None)


def test_a_removed_session_folder_gives_no_git_user_and_the_session_is_still_traced(
    hook_module, traced, monkeypatch, capsys, tmp_path, fake_langfuse
):
    repo = make_repo(tmp_path / "repo", None)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))

    user, message = run_hook(hook_module, monkeypatch, capsys, tmp_path, fake_langfuse, tmp_path / "gone")

    assert (user, message) == (None, None)


def test_unreadable_login_file_attaches_no_user_and_says_so(
    hook_module, traced, monkeypatch, capsys, tmp_path, fake_langfuse
):
    traced.write_text("{not json", encoding="utf-8")
    repo = make_repo(tmp_path / "repo", "git@example.com")

    user, message = run_hook(hook_module, monkeypatch, capsys, tmp_path, fake_langfuse, repo)

    assert user is None
    assert str(traced) in message


def test_git_error_attaches_no_user_and_says_so(
    hook_module, traced, monkeypatch, capsys, tmp_path, fake_langfuse
):
    repo = make_repo(tmp_path / "repo", "git@example.com")
    (repo / ".git" / "config").write_text("[core\nbroken", encoding="utf-8")

    user, message = run_hook(hook_module, monkeypatch, capsys, tmp_path, fake_langfuse, repo)

    assert user is None
    assert "bad config line 1" in message
