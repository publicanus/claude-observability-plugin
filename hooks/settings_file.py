"""Reads a Claude Code settings file, such as a repo's .claude/settings.local.json.

load_settings(path) returns the file's settings object.

  path    the settings file; a missing file reads as {}
  return  the parsed JSON object; its "env", where present, is a JSON object
  raises  SettingsFileError, naming path, when the file cannot be read, is not
          JSON, is not a JSON object, or has an "env" that is not a JSON object

read_keys(lookup) returns the Langfuse public and secret key from a set of
settings, such as an "env" block or the process environment.

read_env_keys(env) is read_keys for an "env" block, and env_holds_keys(env) is
True when that block holds both keys, not empty.

repo_settings_env(cwd) and repo_settings_hold_keys(cwd) read the repo's own
REPO_SETTINGS_FILE, the only source of the keys and the Langfuse address;
SETUP_COMMAND is the command that prepares that file.

Uses only the Python standard library and git, so /hurt can import it without langfuse.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from repo_checkout import find_main_checkout, find_repo_root

REPO_SETTINGS_FILE = Path(".claude") / "settings.local.json"
SETUP_COMMAND = "/mate-record:langfuse-setup"


class SettingsFileError(Exception):
    """A settings file exists but cannot be read as Claude Code settings; str() names the file."""


def load_settings(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise SettingsFileError(f"cannot read {path} as JSON: {e}") from e
    if not isinstance(data, dict):
        raise SettingsFileError(f"{path} does not hold a JSON object")
    if not isinstance(data.get("env", {}), dict):
        raise SettingsFileError(f'the "env" in {path} is not a JSON object')
    return data


def read_keys(lookup: Callable[[str], str]) -> Tuple[str, str]:
    """The public and secret key, each under its LANGFUSE_ name or else its
    CC_LANGFUSE_ name; lookup returns "" for a name it does not hold."""
    return (
        lookup("LANGFUSE_PUBLIC_KEY") or lookup("CC_LANGFUSE_PUBLIC_KEY"),
        lookup("LANGFUSE_SECRET_KEY") or lookup("CC_LANGFUSE_SECRET_KEY"),
    )


def read_env_keys(env: Mapping[str, Any]) -> Tuple[str, str]:
    return read_keys(lambda name: env.get(name) or "")


def env_holds_keys(env: Mapping[str, Any]) -> bool:
    return all(read_env_keys(env))


def repo_settings_env(cwd: Optional[str]) -> Dict[str, Any]:
    """The env block of the repo's own REPO_SETTINGS_FILE.

    cwd: the session's directory; None means the process's working directory.
    The file that counts is the one at the root of the checkout that holds cwd when it
    holds both keys, else the one of the repo's main checkout; outside git, the one in
    cwd. A missing file gives {}.
    Raises SettingsFileError, naming the file, when a file it reads is broken.
    """
    roots = [root for root in (find_repo_root(cwd), find_main_checkout(cwd)) if root is not None]
    env: Dict[str, Any] = {}
    for root in roots:
        env = load_settings(root / REPO_SETTINGS_FILE).get("env", {})
        if env_holds_keys(env):
            return env
    return env


def repo_settings_hold_keys(cwd: Optional[str]) -> bool:
    """True when repo_settings_env(cwd) holds both keys. Raises SettingsFileError."""
    return env_holds_keys(repo_settings_env(cwd))
