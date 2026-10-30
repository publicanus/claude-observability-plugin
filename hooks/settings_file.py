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

KEY_NAMES are the names a key goes by, LANGFUSE_ before CC_LANGFUSE_.

repo_settings_envs(checkout) returns the env blocks of the repo's own settings
files, in the order they count; repo_settings_env(checkout) and
repo_settings_hold_keys(checkout) read the repo's
own REPO_SETTINGS_FILE, the only source of the keys and the Langfuse address, for a
repo_checkout.Checkout; SETUP_COMMAND is the command that prepares that file.

Uses only the Python standard library and git, so /hurt can import it without langfuse.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Tuple

from repo_checkout import Checkout

REPO_SETTINGS_FILE = Path(".claude") / "settings.local.json"
SETUP_COMMAND = "/mate-record:langfuse-setup"
PUBLIC_KEY_NAMES = ("LANGFUSE_PUBLIC_KEY", "CC_LANGFUSE_PUBLIC_KEY")
SECRET_KEY_NAMES = ("LANGFUSE_SECRET_KEY", "CC_LANGFUSE_SECRET_KEY")
KEY_NAMES = PUBLIC_KEY_NAMES + SECRET_KEY_NAMES


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
    return _first_set(lookup, PUBLIC_KEY_NAMES), _first_set(lookup, SECRET_KEY_NAMES)


def _first_set(lookup: Callable[[str], str], names: Tuple[str, ...]) -> str:
    for name in names:
        value = lookup(name)
        if value:
            return value
    return ""


def read_env_keys(env: Mapping[str, Any]) -> Tuple[str, str]:
    return read_keys(lambda name: env.get(name) or "")


def env_holds_keys(env: Mapping[str, Any]) -> bool:
    return all(read_env_keys(env))


def repo_settings_envs(checkout: Checkout) -> List[Dict[str, Any]]:
    """The env blocks of the repo's own REPO_SETTINGS_FILE files, in the order they
    count: checkout.main's, then checkout.root's, such as a worktree's own; outside
    git, or in the main checkout itself, only checkout.root's. A missing file gives {}.
    Raises SettingsFileError, naming the file, when a file is broken.
    """
    roots = [checkout.root] if checkout.main in (None, checkout.root) else [checkout.main, checkout.root]
    return [load_settings(root / REPO_SETTINGS_FILE).get("env", {}) for root in roots]


def repo_settings_env(checkout: Checkout) -> Dict[str, Any]:
    """The env block that counts: the first of repo_settings_envs(checkout) that holds
    both keys, else the last one. Raises SettingsFileError."""
    envs = repo_settings_envs(checkout)
    return next((env for env in envs if env_holds_keys(env)), envs[-1])


def repo_settings_hold_keys(checkout: Checkout) -> bool:
    """True when repo_settings_env(checkout) holds both keys. Raises SettingsFileError."""
    return env_holds_keys(repo_settings_env(checkout))
