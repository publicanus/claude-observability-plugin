from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest


STATE_WRITER = Path(__file__).resolve().parent / "state_writer.py"
ROUNDS = 20


def wait_for_files(paths: list[Path], timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while not all(path.exists() for path in paths):
        if time.time() > deadline:
            missing = [str(path) for path in paths if not path.exists()]
            pytest.fail(f"not created within {timeout_s}s: {missing}")
        time.sleep(0.01)


def test_two_processes_saving_at_once_keep_both_sessions_progress(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    go_file = tmp_path / "go"
    keys = ["a", "b"]
    ready_files = [tmp_path / f"ready-{key}" for key in keys]

    children = [
        subprocess.Popen(
            [sys.executable, str(STATE_WRITER), key, str(ROUNDS), str(ready_file), str(go_file)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for key, ready_file in zip(keys, ready_files)
    ]
    try:
        wait_for_files(ready_files, timeout_s=60)
        go_file.touch()
        results = [child.communicate(timeout=120) for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()

    for key, child, (_, stderr) in zip(keys, children, results):
        assert child.returncode == 0, f"writer {key} failed:\n{stderr}"

    state = json.loads((home / ".claude" / "state" / "langfuse_state.json").read_text(encoding="utf-8"))
    assert {key: state[key]["turn_count"] for key in keys} == {"a": ROUNDS, "b": ROUNDS}


def test_a_held_lock_gives_up_with_timeout_error(hook_module: Any, isolated_hook_state: Path) -> None:
    lock_path = isolated_hook_state / "held.lock"
    with hook_module.FileLock(lock_path):
        start = time.time()
        with pytest.raises(TimeoutError):
            with hook_module.FileLock(lock_path, timeout_s=0.3):
                pass
        waited = time.time() - start

    assert waited >= 0.3
