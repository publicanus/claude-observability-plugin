---
description: Set up Langfuse tracing for this repo. Prepares .claude/settings.local.json for the Langfuse keys, kept out of git, and opens it so the developer can paste the keys.
disable-model-invocation: true
---

Set up Langfuse tracing for the repo of this session. Follow the steps in order, and stop where a step says so.

Never ask the developer for a Langfuse key or a Langfuse address in the chat, and never write a key yourself: anything typed in this chat is sent to Langfuse with the session once tracing starts. The developer pastes the keys into the file you open in step 5.

Never read, print or show the settings file, because once the developer has pasted the keys it holds them. To check the setup, tell the developer to start a new session and look for the "not traced" line.

Run the commands in steps 1, 2 and 4 with the Bash tool, also on Windows.

## 1. Ask the hook

Run:

```
cd "${CLAUDE_PROJECT_DIR}" && printf '%s' '{"hook_event_name":"UserPromptExpansion"}' | sh "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/langfuse_hook.py"
```

Note its exit code, its stdout and its stderr.

## 2. Act on the answer

- **Exit 0, nothing on stderr, and stdout is a JSON object with `"decision": "block"`:** say only the text of its `reason`, and stop. Change no file.
- **Exit 0, nothing on stderr, and stdout is exactly `{}`:** the repo's own `.claude/settings.local.json` has no keys. Go on to step 4.
- **`sh` was not found:** say that the Langfuse hook needs a POSIX `sh`, which on Windows comes with Git for Windows. Stop, and change no file.
- **Anything else** (no output at all, a non-zero exit, text on stderr, or other stdout): run `uv --version`.
  - If `uv --version` fails, go on to step 3.
  - If it succeeds, show the developer what step 1 returned, say that the check failed, and stop. Change no file.

## 3. Offer uv

1. Say: "The Langfuse hook cannot run on this machine. It needs uv, or Python 3.10+ with the langfuse package."
2. Ask the developer whether to install uv. Never offer or install the langfuse package.
3. If they decline, stop. Change no file.
4. If they approve, run the installer for their system:
   - macOS and Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
5. Run `~/.local/bin/uv --version` (on Windows: `"$HOME/.local/bin/uv.exe" --version`) and show the result.
6. Tell the developer to restart Claude Code from a new terminal and run `/mate-record:langfuse-setup` again. Stop.

## 4. Prepare the file

Run:

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh" "${CLAUDE_SKILL_DIR}/prepare_keys_file.py" --project-dir "${CLAUDE_PROJECT_DIR}"
```

If it exits non-zero, show its stderr and stop. Otherwise its stdout is a JSON object: `settings` is the path of the file to open, and `changed` lists the files it wrote.

## 5. Open the file

Open the `settings` path in the developer's editor, with the Bash tool. Put the path in single quotes, as in `'<path>'` below, and write each `'` inside the path as `'\''`:

- macOS: `open -t '<path>'`
- Linux: `xdg-open '<path>'`
- Windows: `SETTINGS_PATH='<path>' powershell -NoProfile -Command 'Invoke-Item -LiteralPath $env:SETTINGS_PATH'`

If that command fails, give the developer the path and ask them to open the file themselves.

## 6. Tell the developer

- Paste the Langfuse public key and secret key between the empty quotes of `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`, and save the file.
- New sessions in this repo are then traced.
- Commit `.worktreeinclude`, so that worktrees Claude Code creates get a copy of the file.
- Name the files from `changed`.
