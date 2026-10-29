"""Pins skills/langfuse-setup/prepare_keys_file.py against real git repositories:
empty key placeholders in .claude/settings.local.json, kept out of git, listed in
.worktreeinclude, and nothing else in the file touched.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "skills" / "langfuse-setup" / "prepare_keys_file.py"
SETTINGS = Path(".claude") / "settings.local.json"
PLACEHOLDERS = {"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}


@pytest.fixture(scope="module")
def prepare() -> Any:
    spec = importlib.util.spec_from_file_location("prepare_keys_file_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the machine's git config and global excludes (which may already ignore
    settings.local.json) out of every test."""
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / ".gitconfig"
    gitconfig.write_text("[user]\n\tname = Test\n\temail = test@example.com\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(sys, "platform", "linux")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = (tmp_path / "repo").resolve()
    path.mkdir()
    git(path, "init", "-q")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


def run(prepare: Any, capsys: Any, project_dir: Path) -> tuple[int, Any, str]:
    code = prepare.main(["--project-dir", str(project_dir)])
    captured = capsys.readouterr()
    stdout = json.loads(captured.out) if code == 0 else captured.out
    return code, stdout, captured.err


def read_settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_new_file_gets_empty_placeholders_and_stays_out_of_git(prepare, capsys, repo):
    code, out, _ = run(prepare, capsys, repo)

    settings = repo / SETTINGS
    assert code == 0
    assert out["settings"] == str(settings)
    assert read_settings(settings) == {"env": PLACEHOLDERS}
    assert ".claude/" not in git(repo, "status", "--porcelain", "--untracked-files=all")

    settings.write_text(
        json.dumps({"env": {"LANGFUSE_PUBLIC_KEY": "pk-lf-1", "LANGFUSE_SECRET_KEY": "sk-lf-1"}}), encoding="utf-8"
    )
    assert ".claude/" not in git(repo, "status", "--porcelain", "--untracked-files=all")


def test_existing_settings_and_pasted_keys_are_kept(prepare, capsys, repo):
    settings = repo / SETTINGS
    settings.parent.mkdir()
    before = {
        "permissions": {"allow": ["Bash(ls)"]},
        "env": {"FOO": "1", "LANGFUSE_PUBLIC_KEY": "pk-lf-pasted"},
    }
    settings.write_text(json.dumps(before), encoding="utf-8")

    code, _, _ = run(prepare, capsys, repo)

    assert code == 0
    assert read_settings(settings) == {
        "permissions": {"allow": ["Bash(ls)"]},
        "env": {"FOO": "1", "LANGFUSE_PUBLIC_KEY": "pk-lf-pasted", "LANGFUSE_SECRET_KEY": ""},
    }


@pytest.mark.parametrize("content", [b"{not json", b"[]", b'{"env": []}'], ids=["invalid", "array", "env-array"])
def test_a_file_that_is_not_a_settings_object_is_left_as_it_was(prepare, capsys, repo, content):
    settings = repo / SETTINGS
    settings.parent.mkdir()
    settings.write_bytes(content)

    code, _, err = run(prepare, capsys, repo)

    assert code == 1
    assert str(settings) in err
    assert settings.read_bytes() == content


def test_a_tracked_file_is_refused_and_left_as_it_was(prepare, capsys, repo):
    settings = repo / SETTINGS
    settings.parent.mkdir()
    settings.write_bytes(b"{}\n")
    git(repo, "add", str(SETTINGS))
    git(repo, "commit", "-q", "-m", "track it")

    code, _, err = run(prepare, capsys, repo)

    assert code == 1
    assert "not ignore" in err
    assert settings.read_bytes() == b"{}\n"


@pytest.mark.parametrize(
    "before, after",
    [
        (None, b".claude/settings.local.json\n"),
        (b".env", b".env\n.claude/settings.local.json\n"),
        (b".env\n .claude/settings.local.json \n", b".env\n .claude/settings.local.json \n"),
    ],
    ids=["missing", "without-the-line", "with-the-line"],
)
def test_worktreeinclude_lists_the_settings_file_once(prepare, capsys, repo, before, after):
    include = repo / ".worktreeinclude"
    if before is not None:
        include.write_bytes(before)

    code, _, _ = run(prepare, capsys, repo)

    assert code == 0
    assert include.read_bytes() == after


def test_from_a_worktree_the_main_checkouts_file_is_prepared(prepare, capsys, repo, tmp_path):
    worktree = (tmp_path / "wt").resolve()
    git(repo, "worktree", "add", "-q", str(worktree))

    code, out, _ = run(prepare, capsys, worktree)

    assert code == 0
    assert out["settings"] == str(repo / SETTINGS)
    assert read_settings(repo / SETTINGS) == {"env": PLACEHOLDERS}
    assert not (worktree / SETTINGS).exists()
    assert (worktree / ".worktreeinclude").is_file()


def test_on_windows_from_a_worktree_the_main_checkouts_file_is_prepared(prepare, capsys, repo, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    worktree = (tmp_path / "wt").resolve()
    git(repo, "worktree", "add", "-q", str(worktree))

    code, out, _ = run(prepare, capsys, worktree)

    assert code == 0
    assert out["settings"] == str(repo / SETTINGS)
    assert read_settings(repo / SETTINGS) == {"env": PLACEHOLDERS}
    assert not (worktree / SETTINGS).exists()


def test_outside_a_git_repository_nothing_is_written(prepare, capsys, tmp_path):
    project_dir = tmp_path / "plain"
    project_dir.mkdir()

    code, _, err = run(prepare, capsys, project_dir)

    assert code == 1
    assert err
    assert list(project_dir.iterdir()) == []
