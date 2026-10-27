"""Adds 1 to one session's saved turn count, a given number of times, as a hook run would.

    python state_writer.py <key> <count> <ready-file> <go-file>

    <key>         the session's key in the state file
    <count>       how many times to add 1
    <ready-file>  created once the hook is loaded
    <go-file>     the rounds start once this file exists
    HOME, USERPROFILE  the home folder; the state file lives under it, as in the hook
    exit          0 when all rounds are saved; 1 when <go-file> does not appear
                  within 60 seconds; any other failure exits non-zero with a traceback
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import load_hook_module  # noqa: E402

GO_WAIT_S = 60
# Leaves a gap between reading and writing the state, so that without a lock the
# other process writes in between and its update is lost.
HOLD_S = 0.01


def main() -> int:
    key, count, ready_file, go_file = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    hook = load_hook_module()
    ready_file.touch()

    deadline = time.time() + GO_WAIT_S
    while not go_file.exists():
        if time.time() > deadline:
            print(f"{go_file} did not appear within {GO_WAIT_S}s", file=sys.stderr)
            return 1
        time.sleep(0.005)

    for _ in range(count):
        with hook.FileLock(hook.LOCK_FILE, timeout_s=60):
            state = hook.load_hook_state()
            session_state = hook.get_session_state(state, key)
            session_state.turn_count += 1
            time.sleep(HOLD_S)
            hook.update_session_state(state, key, session_state)
            hook.save_hook_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
