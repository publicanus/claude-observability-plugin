"""The files under ~/.claude/state that the hook and /hurt both use, and the lock
that keeps two processes from writing one of them at once.

Uses only the Python standard library, so /hurt can import it without langfuse.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

STATE_DIR = Path.home() / ".claude" / "state"
STATE_FILE = STATE_DIR / "langfuse_state.json"
# The local queue of /hurt notes waiting to be attached to their traces; see
# hurt_annotations.py for the format.
HURT_PENDING_FILE = STATE_DIR / "hurt_pending.json"
# The /hurt notes the hook has not yet given a trace; see hurt_annotations.py.
HURT_UNRESOLVED_FILE = STATE_DIR / "hurt_unresolved.json"
# Guards both /hurt queue files.
HURT_PENDING_LOCK_FILE = STATE_DIR / "hurt_pending.lock"
# How long /hurt, and the hook when it places notes, wait for that lock.
HURT_LOCK_TIMEOUT_S = 60.0


class StateFileError(Exception):
    """The state file exists but cannot be read or is not valid JSON."""


def retry_until(attempt: Callable[[], bool], timeout_s: float) -> bool:
    """Calls attempt() until it returns True, every 0.05 s; False once timeout_s has passed."""
    deadline = time.time() + timeout_s
    while not attempt():
        if time.time() > deadline:
            return False
        time.sleep(0.05)
    return True


if sys.platform == "win32":
    import msvcrt

    # Every process locks and unlocks the same first byte, whatever the file's length:
    # msvcrt.locking works from the current position, and "a+" opens at the end.
    def _try_lock(fh) -> bool:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except PermissionError:
            return False
        return True

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class FileLock:
    """Holds an exclusive lock on path for the length of a with block, across
    processes, on Windows, macOS and Linux.

    Creates path and its folder when missing. Raises TimeoutError when another
    holder keeps the lock for longer than timeout_s seconds.
    """

    def __init__(self, path: Path, timeout_s: float = 2.0):
        self.path = path
        self.timeout_s = timeout_s
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        try:
            if not retry_until(lambda: _try_lock(self._fh), self.timeout_s):
                raise TimeoutError(
                    f"could not acquire {self.path} within {self.timeout_s}s"
                )
        except BaseException:
            # __exit__ is not called when __enter__ raises — close the fh
            # we just opened so it doesn't leak.
            try:
                self._fh.close()
            except Exception:
                pass
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            _unlock(self._fh)
        finally:
            self._fh.close()


def read_state(path: Path) -> Any:
    """The parsed content of the state file at path; {} when it does not exist.

    Raises StateFileError, naming path, when it cannot be read or is not valid JSON.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise StateFileError(f"cannot read {path}: {e}") from e


def get_session_state_key(session_id: str, transcript_path: str) -> str:
    # stable key even if session_id collides
    raw = f"{session_id}::{transcript_path}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
