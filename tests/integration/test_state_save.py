from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest


def read_state_file(hook_module: Any) -> Any:
    return json.loads(hook_module.STATE_FILE.read_text(encoding="utf-8"))


def fresh_state(turn_count: int) -> dict[str, Any]:
    # A recent "updated" keeps the entry clear of save_hook_state's 30-day pruning.
    return {"session": {"turn_count": turn_count, "updated": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}}


def test_a_save_outlasts_a_short_hold_on_the_state_file(
    hook_module: Any, isolated_hook_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace
    failures_left = [2]

    def held_twice_then_free(src: Any, dst: Any) -> None:
        if failures_left[0] > 0:
            failures_left[0] -= 1
            raise PermissionError(13, "held by another process", str(dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", held_twice_then_free)
    state = fresh_state(turn_count=3)

    hook_module.save_hook_state(state)

    assert read_state_file(hook_module) == state


def test_a_save_gives_up_after_a_long_hold_and_logs_it(
    hook_module: Any, isolated_hook_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook_module, "DEBUG", False)
    old_state = fresh_state(turn_count=1)
    hook_module.save_hook_state(old_state)

    def always_held(src: Any, dst: Any) -> None:
        raise PermissionError(13, "held by another process", str(dst))

    monkeypatch.setattr(os, "replace", always_held)

    start = time.time()
    hook_module.save_hook_state(fresh_state(turn_count=2))
    waited = time.time() - start

    assert waited >= 0.5
    assert read_state_file(hook_module) == old_state
    log_text = hook_module.LOG_FILE.read_text(encoding="utf-8")
    assert str(hook_module.STATE_FILE) in log_text
    assert "sent again" in log_text


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to replace a file that is open")
def test_a_save_outlasts_a_real_open_handle_on_the_state_file(hook_module: Any, isolated_hook_state: Path) -> None:
    hook_module.save_hook_state(fresh_state(turn_count=1))
    new_state = fresh_state(turn_count=2)

    handle = open(hook_module.STATE_FILE, "rb")
    releaser = threading.Timer(0.2, handle.close)
    releaser.start()
    try:
        hook_module.save_hook_state(new_state)
    finally:
        releaser.join()
        handle.close()

    assert read_state_file(hook_module) == new_state
