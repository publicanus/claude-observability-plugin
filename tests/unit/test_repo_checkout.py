"""Pins hooks/repo_checkout.py: which checkout and main checkout a session's folder
belongs to, the same for the hook, /hurt and the setup."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_checkout import find_checkout


def test_when_git_cannot_run_it_answers_as_outside_git_and_logs_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))
    logged: list = []

    checkout = find_checkout(tmp_path, log=logged.append)

    assert (checkout.root, checkout.main) == (tmp_path, None)
    assert logged and logged[0].startswith("git rev-parse could not run: ")
