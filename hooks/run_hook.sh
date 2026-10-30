#!/bin/sh
# Runs one of this plugin's Python scripts. Every hook entry and the setup skill
# start the hook through this file.
#
#   sh run_hook.sh [--warn-if-cannot-run | --fail-if-cannot-run] <script> [args...]
#
#   --warn-if-cannot-run  when the hook cannot run on this machine, print a Claude
#              Code systemMessage that this session is not traced and why, and
#              exit 0 without running <script>. The hook cannot run when uv is on
#              the PATH but cannot download the packages <script> needs, or when
#              there is no uv and no python3 that is Python 3.10+ with langfuse
#              4.2 or later
#   --fail-if-cannot-run  when there is no uv and no such python3, print one line
#              to stderr, and exit 1 without running <script>; when uv cannot
#              download the packages, uv's own error and exit code
#   <script>   path of the Python script; stdin and [args...] are passed on to it
#   runs with  uv (uv run --script) when uv is on the PATH, else python3
#   exit       the script's own exit code; 0 when the script does not run, except
#              with --fail-if-cannot-run

CANNOT_RUN_MESSAGE='{"systemMessage": "Langfuse: the hook cannot run on this machine, this session is not traced. It needs uv (on Windows, uv is required). To fix it, run /mate-record:langfuse-setup."}'
CANNOT_DOWNLOAD_MESSAGE='{"systemMessage": "Langfuse: this session is not traced, because the hook could not download what it needs to run."}'
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

# Exit 0 when uv has the packages script $1 needs, downloading them when they are
# not on this machine yet.
uv_has_packages() {
  # Offline first: some minutes after a download, uv without --offline asks the
  # package index again, and fails when there is no network.
  uv sync --offline --quiet --script "$1" </dev/null >/dev/null 2>&1 ||
    uv sync --quiet --script "$1" </dev/null >/dev/null 2>&1
}

# What to do when the hook cannot run: warn, fail, or nothing when empty.
if_cannot_run=
case "$1" in
  --warn-if-cannot-run) if_cannot_run=warn; shift ;;
  --fail-if-cannot-run) if_cannot_run=fail; shift ;;
esac

if command -v uv >/dev/null 2>&1; then
  if [ "$if_cannot_run" = fail ] || uv_has_packages "$1"; then
    exec uv run --quiet --script "$@"
  fi
  if [ "$if_cannot_run" = warn ]; then
    printf '%s\n' "$CANNOT_DOWNLOAD_MESSAGE"
  fi
  exit 0
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
