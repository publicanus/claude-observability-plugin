#!/bin/sh
# Runs one of this plugin's Python scripts. Every hook entry and the setup skill
# start the hook through this file.
#
#   sh run_hook.sh [--warn-if-cannot-run] <script> [args...]
#
#   --warn-if-cannot-run  when the hook cannot run on this machine (no uv, and no
#              python3 that is Python 3.10+ with the langfuse package), print a
#              Claude Code systemMessage that this session is not traced, and
#              exit 0 without running <script>
#   <script>   path of the Python script; stdin and [args...] are passed on to it
#   runs with  uv (uv run --script) when uv is on the PATH, else python3
#   exit       the script's own exit code; 0 when the script does not run

CANNOT_RUN_MESSAGE='{"systemMessage": "Langfuse: the hook cannot run on this machine, this session is not traced. It needs uv (on Windows, uv is required). To fix it, run /langfuse-observability:langfuse-setup."}'

# Exit 0 when python3 is Python 3.10+ and the langfuse package is installed.
# The package is looked up, not imported: an import costs about 0.2 s per session start.
python3_can_run_hook() {
  python3 -c 'import sys, importlib.util
sys.exit(sys.version_info < (3, 10) or importlib.util.find_spec("langfuse") is None)' \
    </dev/null >/dev/null 2>&1
}

warn_if_cannot_run=
if [ "$1" = --warn-if-cannot-run ]; then
  warn_if_cannot_run=1
  shift
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --script "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  if [ -z "$warn_if_cannot_run" ] || python3_can_run_hook; then
    exec python3 "$@"
  fi
fi
if [ -n "$warn_if_cannot_run" ]; then
  printf '%s\n' "$CANNOT_RUN_MESSAGE"
fi
exit 0
