"""Pins hooks/repo_checkout.py: which checkout and main checkout a session's folder
belongs to, the same for the hook, /hurt and the setup."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_checkout import Remote, find_checkout, find_remote, github_repo


def test_when_git_cannot_run_it_answers_as_outside_git_and_logs_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))
    logged: list = []

    checkout = find_checkout(tmp_path, log=logged.append)

    assert (checkout.root, checkout.main) == (tmp_path, None)
    assert logged and logged[0].startswith("git could not run: ")


@pytest.mark.parametrize(
    "address, expected",
    [
        ("https://github.com/acme/widgets.git", "acme/widgets"),
        ("https://github.com/acme/widgets", "acme/widgets"),
        ("https://token@github.com/acme/widgets.git/", "acme/widgets"),
        ("ssh://git@github.com/acme/widgets.git", "acme/widgets"),
        ("git@github.com:acme/widgets.git", "acme/widgets"),
        ("git@GitHub.com:acme/widgets", "acme/widgets"),
        ("https://gitlab.com/acme/widgets.git", None),
        ("git@gitlab.com:acme/widgets.git", None),
        ("https://github.com.evil.example/acme/widgets.git", None),
        ("https://github.com/acme", None),
        ("/srv/git/widgets.git", None),
        ("C:/git/widgets.git", None),
    ],
)
def test_github_repo_names_owner_and_repo_only_for_a_github_address(address: str, expected) -> None:
    assert github_repo(address) == expected


def test_the_remote_is_origin_else_the_first_by_name_as_configured(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    assert find_remote(tmp_path) is None
    git("remote", "add", "zeta", "https://github.com/acme/zeta.git")
    git("remote", "add", "alpha", "https://github.com/acme/alpha.git")
    assert find_remote(tmp_path) == Remote("alpha", "https://github.com/acme/alpha.git")
    git("remote", "add", "origin", "https://github.com/acme/widgets.git")
    git("config", "url./elsewhere/.insteadOf", "https://github.com/")
    assert find_remote(tmp_path) == Remote("origin", "https://github.com/acme/widgets.git")
