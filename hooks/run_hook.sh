#!/bin/sh
# Runs one of this plugin's Python scripts. Every hook entry and the setup skill
# start the hook through this file.
#
#   sh run_hook.sh <script> [args...]
#
#   <script>   path of the Python script; stdin and [args...] are passed on to it
#   runs with  uv (uv run --script) when uv is on the PATH, else python3
#   exit       the script's own exit code; 0 with no output when neither uv nor
#              python3 is on the PATH
if command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --script "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$@"
fi
exit 0
