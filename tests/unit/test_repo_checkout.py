"""Pins hooks/repo_checkout.py: which checkout and main checkout a session's folder
belongs to, the same for the hook, /hurt and the setup."""

from __future__ import annotations

import subprocess
import sys
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


def test_the_remote_is_origin_else_the_first_by_name_at_the_address_git_uses(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    assert find_remote(tmp_path) is None
    git("remote", "add", "zeta", "https://github.com/acme/zeta.git")
    git("remote", "add", "alpha", "https://github.com/acme/alpha.git")
    assert find_remote(tmp_path) == Remote("alpha", "https://github.com/acme/alpha.git")
    git("remote", "add", "origin", "gh:acme/widgets")
    git("config", "url.git@github.com:.insteadOf", "gh:")
    assert find_remote(tmp_path) == Remote("origin", "git@github.com:acme/widgets")
    assert github_repo("git@github.com:acme/widgets") == "acme/widgets"



def put_fake_ssh_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostnames: dict) -> None:
    """A fake ssh whose `ssh -G <alias>` prints `hostname <name>` for the aliases in
    hostnames, and fails for any other alias."""
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    fake = folder / "fake_ssh.py"
    fake.write_text(
        "import sys\n"
        f"hostnames = {hostnames!r}\n"
        "alias = sys.argv[2]\n"
        "if alias not in hostnames:\n"
        "    sys.exit(255)\n"
        "print('user git')\n"
        "print(f'hostname {hostnames[alias]}')\n"
        "print('port 22')\n",
        encoding="utf-8",
    )
    (folder / "ssh").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8", newline="\n")
    (folder / "ssh").chmod(0o755)
    (folder / "ssh.cmd").write_text(f'@"{sys.executable}" "{fake}" %*\r\n', encoding="utf-8", newline="")
    monkeypatch.setenv("PATH", str(folder))


@pytest.mark.parametrize(
    "address, expected",
    [
        ("git@github-work:acme/widgets.git", "acme/widgets"),
        ("ssh://git@github-work/acme/widgets.git", "acme/widgets"),
        ("git@gitlab-work:acme/widgets.git", None),
        ("git@unknown-alias:acme/widgets.git", None),
        ("https://github-work/acme/widgets.git", None),
    ],
)
def test_an_ssh_host_alias_counts_when_ssh_maps_it_to_github(tmp_path, monkeypatch, address, expected) -> None:
    put_fake_ssh_on_path(tmp_path, monkeypatch, {"github-work": "GitHub.com", "gitlab-work": "gitlab.com"})

    assert github_repo(address) == expected


def test_an_ssh_host_alias_does_not_count_without_ssh(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "no-ssh-here"))

    assert github_repo("git@github-work:acme/widgets.git") is None
