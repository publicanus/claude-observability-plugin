"""Finds the git checkout a session runs in, for the hook and /hurt alike.

Uses only the Python standard library, so /hurt can import it without langfuse.
Needs git on the PATH; answers as outside git when git cannot run.

find_checkout(folder, log=None) returns the Checkout that holds folder.

  folder  the session's folder
  log     called with one line when git cannot run; None logs nothing
  return  Checkout(root, main): root is the root of the git checkout that holds
          folder, or folder itself outside git; main is the repo's main checkout,
          the same for all its worktrees, or None outside git
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

Log = Optional[Callable[[str], None]]


@dataclass(frozen=True)
class Checkout:
    root: Path
    main: Optional[Path]


def session_dir(cwd: Optional[str]) -> Path:
    """cwd as a Path; None means the process's working directory."""
    return Path(cwd) if cwd else Path.cwd()


def git_rev_parse(folder: Path, *flags: str, log: Log = None) -> Optional[str]:
    """The output of `git rev-parse <flags>` in folder, or None when folder is not in
    a git checkout or git cannot run."""
    try:
        result = subprocess.run(["git", "rev-parse", *flags], cwd=folder, capture_output=True, text=True)
    except OSError as e:
        if log is not None:
            log(f"git rev-parse could not run: {type(e).__name__}: {e}")
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def find_checkout(folder: Path, log: Log = None) -> Checkout:
    toplevel = git_rev_parse(folder, "--show-toplevel", log=log)
    if toplevel is None:
        return Checkout(root=folder, main=None)
    common_dir = git_rev_parse(folder, "--path-format=absolute", "--git-common-dir", log=log)
    main = Path(common_dir).parent if common_dir is not None else None
    return Checkout(root=Path(toplevel), main=main)
