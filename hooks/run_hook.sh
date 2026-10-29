#!/bin/sh
# Runs one of this plugin's Python scripts. Every hook entry and the setup skill
# start the hook through this file.
#
#   sh run_hook.sh [--warn-if-cannot-run | --fail-if-cannot-run] <script> [args...]
#
#   --warn-if-cannot-run  when the hook cannot run on this machine (no uv, and no
#              python3 that is Python 3.10+ with langfuse 4.2 or later), print a
#              Claude Code systemMessage that this session is not traced, and
#              exit 0 without running <script>
#   --fail-if-cannot-run  when the hook cannot run on this machine, print one line
#              to stderr, and exit 1 without running <script>
#   <script>   path of the Python script; stdin and [args...] are passed on to it
#   runs with  uv (uv run --script) when uv is on the PATH, else python3
#   exit       the script's own exit code; 0 when the script does not run, except
#              1 with --fail-if-cannot-run

CANNOT_RUN_MESSAGE='{"systemMessage": "Langfuse: the hook cannot run on this machine, this session is not traced. It needs uv (on Windows, uv is required). To fix it, run /mate-record:langfuse-setup."}'
CANNOT_RUN_COMMAND_MESSAGE='langfuse-work-item: cannot run: needs uv or a suitable python3; run /mate-record:langfuse-setup'

# Exit 0 when python3 is Python 3.10+ with langfuse 4.2 or later installed.
# The version is read from the package's metadata, not imported: an import costs
# about 0.2 s per session start.
python3_can_run_hook() {
  python3 -c 'import sys, importlib.metadata as metadata
major, minor = (int(part) for part in metadata.version("langfuse").split(".")[:2])
sys.exit(sys.version_info < (3, 10) or (major, minor) < (4, 2))' \
    </dev/null >/dev/null 2>&1
}

# What to do when the hook cannot run: warn, fail, or nothing when empty.
if_cannot_run=
case "$1" in
  --warn-if-cannot-run) if_cannot_run=warn; shift ;;
  --fail-if-cannot-run) if_cannot_run=fail; shift ;;
esac

if command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --script "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  if [ -z "$if_cannot_run" ] || python3_can_run_hook; then
    exec python3 "$@"
  fi
fi
case "$if_cannot_run" in
  warn) printf '%s\n' "$CANNOT_RUN_MESSAGE" ;;
  fail) printf '%s\n' "$CANNOT_RUN_COMMAND_MESSAGE" >&2; exit 1 ;;
esac
exit 0
