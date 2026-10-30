"""Finds the git checkout a session runs in, for the hook and /hurt alike.

Uses only the Python standard library, so /hurt can import it without langfuse.
Each function takes cwd, the session's directory; None means the process's
working directory. Each needs git on the PATH, and answers as outside git when
git cannot run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


def session_dir(cwd: Optional[str]) -> Path:
    """cwd as a Path; None means the process's working directory."""
    return Path(cwd) if cwd else Path.cwd()


def git_rev_parse(cwd: Optional[str], *flags: str) -> Optional[str]:
    """The output of `git rev-parse <flags>` in session_dir(cwd), or None when that
    folder is not in a git checkout or git cannot run."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", *flags], cwd=session_dir(cwd), capture_output=True, text=True
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def find_main_checkout(cwd: Optional[str]) -> Optional[Path]:
    """The main checkout of the git repo that holds cwd, the same for all its
    worktrees; None outside a git checkout."""
    common_dir = git_rev_parse(cwd, "--path-format=absolute", "--git-common-dir")
    return Path(common_dir).parent if common_dir is not None else None


def find_repo_root(cwd: Optional[str]) -> Path:
    """The root of the git checkout that holds cwd, or session_dir(cwd) itself
    outside a git checkout."""
    toplevel = git_rev_parse(cwd, "--show-toplevel")
    return Path(toplevel) if toplevel is not None else session_dir(cwd)
