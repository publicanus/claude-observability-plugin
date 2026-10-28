"""Runs bin/langfuse-work-item itself, by its bare name through `sh`, as a tool's Bash
call does (on Windows, through the `sh` of Git for Windows), with the plugin's bin
folder on the PATH.

Its `python3` is the Python running these tests, which has langfuse installed, and
no uv is on the PATH, so the command runs the hook without installing anything.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION = "session-46"


def write_stub(bin_dir: Path, name: str, target: str) -> None:
    stub = bin_dir / name
    stub.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8", newline="\n")
    stub.chmod(0o755)


def run_command(tmp_path: Path, command: str, session_id: Optional[str]) -> subprocess.CompletedProcess:
    sh = shutil.which("sh")
    assert sh is not None, "no sh on the PATH; on Windows, install Git for Windows"
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    write_stub(stubs, "sh", Path(sh).as_posix())
    write_stub(stubs, "python3", Path(sys.executable).as_posix())
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": os.pathsep.join([str(REPO_ROOT / "bin"), str(stubs)]),
        "HOME": str(home),
        "USERPROFILE": str(home),
        **{name: os.environ[name] for name in ["SYSTEMROOT"] if name in os.environ},
    }
    if session_id is not None:
        env["CLAUDE_CODE_SESSION_ID"] = session_id
    return subprocess.run([sh, "-c", command], env=env, capture_output=True, text=True)


def test_set_by_bare_name_records_the_item_for_the_session(tmp_path: Path) -> None:
    result = run_command(tmp_path, "langfuse-work-item set 37", SESSION)

    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    marker = tmp_path / "home" / ".claude" / "state" / "langfuse_work_items" / f"{SESSION}.jsonl"
    assert [json.loads(line)["id"] for line in marker.read_text(encoding="utf-8").splitlines()] == ["37"]


def test_without_a_session_id_it_exits_2_with_a_line_on_stderr(tmp_path: Path) -> None:
    result = run_command(tmp_path, "langfuse-work-item clear", None)

    assert (result.returncode, result.stdout) == (2, "")
    assert result.stderr.startswith("langfuse-work-item: ") and len(result.stderr.splitlines()) == 1
