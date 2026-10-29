"""Reads a Claude Code settings file, such as a repo's .claude/settings.local.json.

load_settings(path) returns the file's settings object.

  path    the settings file; a missing file reads as {}
  return  the parsed JSON object; its "env", where present, is a JSON object
  raises  SettingsFileError, naming path, when the file cannot be read, is not
          JSON, is not a JSON object, or has an "env" that is not a JSON object

read_keys(lookup) returns the Langfuse public and secret key from a set of
settings, such as an "env" block or the process environment.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, Tuple


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
