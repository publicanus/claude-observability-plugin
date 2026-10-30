#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Prepares a repo's .claude/settings.local.json for Langfuse keys, kept out of git.

    sh hooks/run_hook.sh prepare_keys_file.py --project-dir <dir>

    --project-dir  the folder the Claude Code session started in, inside a git repository
    target         .claude/settings.local.json in the repository's main checkout, the folder
                   that holds its git folder (for a bare repository with worktrees, the
                   folder that holds the bare repository, outside every checkout)
    writes         only what is missing, in this order:
                   - the line **/.claude/settings.local.json in the clone's .git/info/exclude,
                     when the target is inside a checkout and git does not ignore it yet
                   - LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY as "" in the target's env
                     block; values already there and every other setting stay as they are
                   - the line .claude/settings.local.json in .worktreeinclude at the root of
                     the checkout that holds <dir>
    stdout         {"settings": <target path>, "changed": [<paths written>]}
    exit           0 done; 1 a git or file problem, named on stderr, with the target left
                   as it was; 2 bad arguments
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# The plugin's hooks folder, so its shared modules import however this script is run.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
from repo_checkout import find_checkout  # noqa: E402
from settings_file import SettingsFileError, load_settings  # noqa: E402

SETTINGS = Path(".claude") / "settings.local.json"
EXCLUDE_LINE = "**/.claude/settings.local.json"
WORKTREEINCLUDE_LINE = ".claude/settings.local.json"
KEY_NAMES = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
GIT_CHECK_IGNORE_NOT_IGNORED = 1


class SetupError(Exception):
    """A git or file problem; str() is the message for the developer."""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    except OSError as e:
        raise SetupError(f"cannot run git: {e}") from e


def git_output(cwd: Path, *args: str) -> str:
    result = git(cwd, *args)
    if result.returncode != 0:
        raise SetupError(f"`git {' '.join(args)}` failed in {cwd}: {result.stderr.strip()}")
    return result.stdout


def main_checkout(project_dir: Path) -> Path:
    main = find_checkout(project_dir).main
    if main is None:
        raise SetupError(f"git names no main checkout for {project_dir}")
    return main


def in_work_tree(root: Path) -> bool:
    result = git(root, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def is_ignored(root: Path) -> bool:
    result = git(root, "check-ignore", "-q", SETTINGS.as_posix())
    if result.returncode == 0:
        return True
    if result.returncode == GIT_CHECK_IGNORE_NOT_IGNORED:
        return False
    raise SetupError(f"`git check-ignore` failed in {root}: {result.stderr.strip()}")


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_bytes() if path.exists() else b""
    separator = b"\n" if existing and not existing.endswith(b"\n") else b""
    with path.open("ab") as f:
        f.write(separator + line.encode("utf-8") + b"\n")


def ensure_ignored(root: Path) -> List[Path]:
    """Makes git ignore root's settings file; returns the files it changed."""
    if is_ignored(root):
        return []
    exclude = Path(git_output(root, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude").strip())
    append_line(exclude, EXCLUDE_LINE)
    if not is_ignored(root):
        raise SetupError(
            f"git does not ignore {root / SETTINGS} even with {EXCLUDE_LINE} in {exclude}; "
            "is the file tracked by git?"
        )
    return [exclude]


def add_placeholders(settings: Dict[str, Any]) -> bool:
    """Adds each missing key to settings' env as ""; True when it added one."""
    env = settings.setdefault("env", {})
    missing = [name for name in KEY_NAMES if name not in env]
    for name in missing:
        env[name] = ""
    return bool(missing)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    )
    temp_path = Path(temp_file.name)
    try:
        with temp_file:
            temp_file.write(json.dumps(data, indent=2) + "\n")
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def lists_settings_file(path: Path) -> bool:
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    return any(line.strip() == WORKTREEINCLUDE_LINE for line in lines)


def prepare(project_dir: Path) -> Dict[str, Any]:
    checkout_root = Path(git_output(project_dir, "rev-parse", "--show-toplevel").strip())
    root = main_checkout(project_dir)
    target = root / SETTINGS
    settings = load_settings(target)

    changed = ensure_ignored(root) if in_work_tree(root) else []
    if add_placeholders(settings) or not target.exists():
        write_json(target, settings)
        changed.append(target)

    worktreeinclude = checkout_root / ".worktreeinclude"
    if not lists_settings_file(worktreeinclude):
        append_line(worktreeinclude, WORKTREEINCLUDE_LINE)
        changed.append(worktreeinclude)

    return {"settings": str(target), "changed": [str(path) for path in changed]}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare .claude/settings.local.json for Langfuse keys.")
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.project_dir.resolve())
    except (SetupError, SettingsFileError) as e:
        print(f"prepare_keys_file: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
