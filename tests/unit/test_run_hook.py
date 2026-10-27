"""Pins hooks/run_hook.sh, the one place that decides how the hook's Python runs:
uv when it is on the PATH, else python3, else a silent exit; and, at session start,
the message that the hook cannot run on this machine.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "hooks" / "run_hook.sh"
HOOK_SCRIPT = REPO_ROOT / "hooks" / "langfuse_hook.py"
WARN = "--warn-if-cannot-run"
KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test"}
SCRIPT = "/plugin/hooks/some_script.py"
ARGS = ["--project-dir", "/repo with space"]


def write_stub(bin_dir: Path, name: str, body: Optional[str] = None) -> None:
    """A `sh` program named `name` in `bin_dir`. Without `body`, it prints its own
    name and then each argument on its own line."""
    if body is None:
        body = f'printf "%s\\n" {name} "$@"'
    stub = bin_dir / name
    stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    stub.chmod(0o755)


def run_launcher(
    bin_dir: Path, *options: str, env: Optional[dict[str, str]] = None
) -> subprocess.CompletedProcess:
    """Runs the launcher as the hooks do, with the `sh` on the PATH (on Windows,
    the one Git for Windows provides), and with `bin_dir` as the launcher's only PATH.

    options: launcher options, put before the script path.
    env: more environment variables for the launcher.
    """
    sh = shutil.which("sh")
    assert sh is not None, "no sh on the PATH; on Windows, install Git for Windows"
    return subprocess.run(
        [sh, str(LAUNCHER), *options, SCRIPT, *ARGS],
        env={**(env or {}), "PATH": str(bin_dir)},
        capture_output=True,
        text=True,
    )


def assert_cannot_run_message(result: subprocess.CompletedProcess, setup_command: str) -> None:
    assert (result.returncode, result.stderr) == (0, "")
    message = json.loads(result.stdout)["systemMessage"]
    assert "this session is not traced" in message
    assert setup_command in message


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def test_uv_on_the_path_runs_the_script_with_uv(bin_dir: Path) -> None:
    write_stub(bin_dir, "uv")
    write_stub(bin_dir, "python3")

    result = run_launcher(bin_dir)

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["uv", "run", "--quiet", "--script", SCRIPT, *ARGS]


def test_without_uv_python3_runs_the_script(bin_dir: Path) -> None:
    write_stub(bin_dir, "python3")

    result = run_launcher(bin_dir)

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["python3", SCRIPT, *ARGS]


def test_without_uv_or_python3_it_exits_silently(bin_dir: Path) -> None:
    result = run_launcher(bin_dir)

    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize("env", [None, KEYS], ids=["no keys", "keys set"])
def test_at_session_start_without_uv_or_python3_it_says_the_hook_cannot_run(
    bin_dir: Path, hook_module: Any, env: Optional[dict[str, str]]
) -> None:
    result = run_launcher(bin_dir, WARN, env=env)

    assert_cannot_run_message(result, hook_module.SETUP_COMMAND)
    assert list(json.loads(result.stdout)) == ["systemMessage"]


def test_at_session_start_a_python3_that_fails_the_check_gets_the_message_and_the_script_does_not_run(
    bin_dir: Path, hook_module: Any
) -> None:
    write_stub(bin_dir, "python3", 'printf "%s\\n" python3 "$@"\necho "Python was not found" >&2\nexit 1')

    result = run_launcher(bin_dir, WARN)

    assert_cannot_run_message(result, hook_module.SETUP_COMMAND)


def test_at_session_start_a_real_python3_without_langfuse_gets_the_message(
    bin_dir: Path, hook_module: Any
) -> None:
    write_stub(bin_dir, "python3", f'exec "{Path(sys.executable).as_posix()}" "$@"')
    # Python on Windows does not start without SYSTEMROOT.
    env = {name: os.environ[name] for name in ["SYSTEMROOT"] if name in os.environ}

    result = run_launcher(bin_dir, WARN, env=env)

    assert_cannot_run_message(result, hook_module.SETUP_COMMAND)


def test_at_session_start_a_python3_that_passes_the_check_runs_the_script(bin_dir: Path) -> None:
    write_stub(bin_dir, "python3")

    result = run_launcher(bin_dir, WARN)

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["python3", SCRIPT, *ARGS]


def test_at_session_start_uv_runs_the_script(bin_dir: Path) -> None:
    write_stub(bin_dir, "uv")

    result = run_launcher(bin_dir, WARN)

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["uv", "run", "--quiet", "--script", SCRIPT, *ARGS]


# The reference reader of https://packaging.python.org/en/latest/specifications/inline-script-metadata/
INLINE_METADATA = r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$"


def test_the_launchers_minimum_python_is_the_scripts() -> None:
    tomllib = pytest.importorskip("tomllib")
    [block] = [m for m in re.finditer(INLINE_METADATA, HOOK_SCRIPT.read_text(encoding="utf-8")) if m["type"] == "script"]
    toml = "".join(
        line[2:] if line.startswith("# ") else line[1:] for line in block["content"].splitlines(keepends=True)
    )
    requires_python = tomllib.loads(toml)["requires-python"]

    version = re.fullmatch(r">=(\d+)\.(\d+)", requires_python)
    assert version, f"requires-python is {requires_python!r}, not >=X.Y"
    assert f"sys.version_info < ({version[1]}, {version[2]})" in LAUNCHER.read_text(encoding="utf-8")
