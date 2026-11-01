"""Finds the git checkout a session runs in, for the hook and /hurt alike.

Uses only the Python standard library, so /hurt can import it without langfuse.
Needs git on the PATH; answers as outside git when git cannot run.

project_folder(cwd) is the folder whose checkout a session belongs to: the
project folder Claude Code started in, CLAUDE_PROJECT_DIR, which stays the same
when the session changes directory; without it, cwd (None: the process's working
directory).

find_checkout(folder, log=None) returns the Checkout that holds folder.

  folder  the session's folder
  log     called with one line when git cannot run; None logs nothing
  return  Checkout(root, main): root is the root of the git checkout that holds
          folder, or folder itself outside git; main is the repo's main checkout,
          the same for all its worktrees: the folder that holds the repo's git
          folder (for a bare repo with worktrees, the folder that holds the bare
          repo), or None outside git

find_remote(root) returns the Remote a checkout's repo is reached through: origin,
else the first remote by name, with the address git uses for it (`git remote
get-url`, so url.<base>.insteadOf applied); None when the repo has no remote.
Raises subprocess.CalledProcessError when git fails.

github_repo(address) returns "<owner>/<name>" when address is a github.com address,
in URL form (https://github.com/o/n.git, ssh://git@github.com/o/n) or scp form
(git@github.com:o/n.git); else None. An SSH address whose host is an alias, such as
git@github-work:o/n.git, counts when `ssh -G <alias>` names github.com as its
hostname; without ssh on the PATH, or when ssh fails, it does not count.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

Log = Optional[Callable[[str], None]]
GITHUB_HOST = "github.com"


@dataclass(frozen=True)
class Checkout:
    root: Path
    main: Optional[Path]


@dataclass(frozen=True)
class Remote:
    name: str
    address: str


def session_dir(cwd: Optional[str]) -> Path:
    """cwd as a Path; None means the process's working directory."""
    return Path(cwd) if cwd else Path.cwd()


def project_folder(cwd: Optional[str]) -> Path:
    return session_dir(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)


def git_output(folder: Path, *args: str, log: Log = None) -> Optional[str]:
    """The output of `git <args>` in folder, stripped, or None when git fails there,
    such as outside a git checkout, or cannot run."""
    try:
        result = subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True)
    except OSError as e:
        if log is not None:
            log(f"git could not run: {type(e).__name__}: {e}")
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def find_checkout(folder: Path, log: Log = None) -> Checkout:
    toplevel = git_output(folder, "rev-parse", "--show-toplevel", log=log)
    if toplevel is None:
        return Checkout(root=folder, main=None)
    common_dir = git_output(folder, "rev-parse", "--path-format=absolute", "--git-common-dir", log=log)
    main = Path(common_dir).parent if common_dir is not None else None
    return Checkout(root=Path(toplevel), main=main)


def find_remote(root: Path) -> Optional[Remote]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, stdout=subprocess.PIPE, text=True, check=True
        ).stdout.strip()

    names = sorted(git("remote").split())
    if not names:
        return None
    name = "origin" if "origin" in names else names[0]
    return Remote(name, git("remote", "get-url", name))


SSH_SCHEMES = ("ssh", "git+ssh", "ssh+git")


def github_repo(address: str) -> Optional[str]:
    if "://" in address:
        parts = urlsplit(address)
        host, path = parts.hostname or "", parts.path
        over_ssh = parts.scheme in SSH_SCHEMES
    else:
        # scp form: [user@]host:path
        host_part, separator, path = address.partition(":")
        if not separator:
            return None
        host = host_part.rpartition("@")[2]
        over_ssh = True
    if host.lower() != GITHUB_HOST and not (over_ssh and ssh_hostname(host) == GITHUB_HOST):
        return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    owner_and_name = path.split("/")
    if len(owner_and_name) != 2 or not all(owner_and_name):
        return None
    return path


def ssh_hostname(alias: str) -> Optional[str]:
    """The host ssh connects to for alias, from the `hostname` line of `ssh -G`, lowercased;
    None without ssh on the PATH, or when ssh fails."""
    ssh = shutil.which("ssh")
    if ssh is None:
        return None
    try:
        result = subprocess.run([ssh, "-G", alias], capture_output=True, text=True)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key.lower() == "hostname":
            return value.strip().lower()
    return None
