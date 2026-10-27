"""Pins hooks/run_hook.sh, the one place that decides how the hook's Python runs:
uv when it is on the PATH, else python3, else a silent exit.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "hooks" / "run_hook.sh"
SCRIPT = "/plugin/hooks/some_script.py"
ARGS = ["--project-dir", "/repo with space"]


def write_stub(bin_dir: Path, name: str) -> None:
    """A program that prints its own name and then each argument on its own line."""
    stub = bin_dir / name
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" {name} "$@"\n', encoding="utf-8", newline="\n")
    stub.chmod(0o755)


def run_launcher(bin_dir: Path) -> subprocess.CompletedProcess:
    """Runs the launcher as the hooks do, with the `sh` on the PATH (on Windows,
    the one Git for Windows provides), and with `bin_dir` as the launcher's only PATH."""
    sh = shutil.which("sh")
    assert sh is not None, "no sh on the PATH; on Windows, install Git for Windows"
    return subprocess.run(
        [sh, str(LAUNCHER), SCRIPT, *ARGS],
        env={"PATH": str(bin_dir)},
        capture_output=True,
        text=True,
    )


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
