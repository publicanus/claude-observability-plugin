"""Pins hooks/run_hook.sh, the one place that decides how the hook's Python runs:
uv when it is on the PATH, else python3, else a silent exit, also when uv cannot
download the script's packages; at session start, the message that the session is
not traced and why; and, for a command, the error.
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
FAIL = "--fail-if-cannot-run"
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


def test_for_a_command_without_uv_or_python3_it_fails_with_a_line_on_stderr(bin_dir: Path, hook_module: Any) -> None:
    result = run_launcher(bin_dir, FAIL)

    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == (
        f"langfuse-work-item: cannot run: needs uv or a suitable python3; run {hook_module.SETUP_COMMAND}\n"
    )


def test_at_session_start_a_python3_that_fails_the_check_gets_the_message_and_the_script_does_not_run(
    bin_dir: Path, hook_module: Any
) -> None:
    write_stub(bin_dir, "python3", 'printf "%s\\n" python3 "$@"\necho "Python was not found" >&2\nexit 1')

    result = run_launcher(bin_dir, WARN)

    assert_cannot_run_message(result, hook_module.SETUP_COMMAND)


def real_python3(bin_dir: Path) -> dict[str, str]:
    """Puts the Python running these tests on the launcher's PATH as `python3`, and
    returns the environment it needs to start. That Python sees no installed packages,
    so no langfuse package either."""
    write_stub(bin_dir, "python3", f'exec "{Path(sys.executable).as_posix()}" -S "$@"')
    # Python on Windows does not start without SYSTEMROOT.
    return {name: os.environ[name] for name in ["SYSTEMROOT"] if name in os.environ}


def test_at_session_start_a_real_python3_without_langfuse_gets_the_message(
    bin_dir: Path, hook_module: Any
) -> None:
    env = real_python3(bin_dir)

    result = run_launcher(bin_dir, WARN, env=env)

    assert_cannot_run_message(result, hook_module.SETUP_COMMAND)


def install_fake_langfuse(tmp_path: Path, version: str) -> Path:
    """A folder holding an empty langfuse package that says it is `version`."""
    packages = tmp_path / "packages"
    (packages / "langfuse").mkdir(parents=True)
    (packages / "langfuse" / "__init__.py").write_text("", encoding="utf-8")
    dist_info = packages / f"langfuse-{version}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: langfuse\nVersion: {version}\n", encoding="utf-8"
    )
    return packages


def test_at_session_start_a_real_python3_with_a_langfuse_older_than_4_2_gets_the_message(
    bin_dir: Path, tmp_path: Path, hook_module: Any
) -> None:
    env = {**real_python3(bin_dir), "PYTHONPATH": str(install_fake_langfuse(tmp_path, "4.1.0"))}

    result = run_launcher(bin_dir, WARN, env=env)

    assert_cannot_run_message(result, hook_module.SETUP_COMMAND)


def test_at_session_start_a_real_python3_with_langfuse_runs_the_script(bin_dir: Path, tmp_path: Path) -> None:
    # 4.10 is newer than 4.2, though "4.10" sorts before "4.2" as text.
    env = {**real_python3(bin_dir), "PYTHONPATH": str(install_fake_langfuse(tmp_path, "4.10.0"))}

    result = run_launcher(bin_dir, WARN, env=env)

    assert result.stdout == ""
    assert "some_script.py" in result.stderr  # Python could not open the script, so it tried to run it


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


def write_uv_that_cannot_download(bin_dir: Path) -> None:
    """A uv that fails as uv 0.10 does when the script's packages are not in its
    cache and no package index can be reached: exit 2, with the error on stderr."""
    write_stub(bin_dir, "uv", 'echo "error: Request failed after 3 retries" >&2\nexit 2')


CANNOT_DOWNLOAD = "Langfuse: this session is not traced, because the hook could not download what it needs to run."


def test_when_uv_cannot_download_the_packages_the_hook_exits_0_with_one_line_on_stderr(bin_dir: Path) -> None:
    write_uv_that_cannot_download(bin_dir)

    result = run_launcher(bin_dir)

    assert (result.returncode, result.stdout, result.stderr) == (0, "", CANNOT_DOWNLOAD + "\n")


def test_at_session_start_when_uv_cannot_download_the_packages_it_says_so_in_one_line(bin_dir: Path) -> None:
    write_uv_that_cannot_download(bin_dir)

    result = run_launcher(bin_dir, WARN)

    assert (result.returncode, result.stderr) == (0, "")
    assert json.loads(result.stdout) == {"systemMessage": CANNOT_DOWNLOAD}


def test_for_a_command_when_uv_cannot_download_the_packages_it_fails_with_uvs_error(bin_dir: Path) -> None:
    write_uv_that_cannot_download(bin_dir)

    result = run_launcher(bin_dir, FAIL)

    assert (result.returncode, result.stdout) == (2, "")
    assert result.stderr == "error: Request failed after 3 retries\n"


def test_when_uv_has_the_packages_and_no_index_can_be_reached_it_runs_the_script(bin_dir: Path) -> None:
    # As uv 0.10 does some minutes after its last download: with --offline it finds
    # the packages in its cache, but without it, it asks the index again.
    write_stub(
        bin_dir,
        "uv",
        'case "$*" in run*|*--offline*) printf "%s\\n" uv "$@"; exit 0 ;; esac\n'
        'echo "error: Request failed after 3 retries" >&2\nexit 2',
    )

    result = run_launcher(bin_dir, WARN)

    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout.splitlines() == ["uv", "run", "--quiet", "--script", SCRIPT, *ARGS]


def test_when_uv_does_not_have_the_packages_but_can_download_them_it_runs_the_script(bin_dir: Path) -> None:
    write_stub(
        bin_dir,
        "uv",
        'case "$*" in *--offline*) echo "error: not in the cache" >&2; exit 1 ;; esac\n'
        'printf "%s\\n" uv "$@"',
    )

    result = run_launcher(bin_dir, WARN)

    assert (result.returncode, result.stderr) == (0, "")
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
