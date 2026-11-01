---
description: Set up Langfuse tracing for this repo. On a GitHub repo, gets the repo's Langfuse keys from the repo itself, or puts them there once; elsewhere, prepares .claude/settings.local.json for the keys, kept out of git, and opens it so the developer can paste them. Whoever manages a GitHub repo also uses it to take people or laptops off and to put in a new key pair.
argument-hint: "[people | new pair | help]"
disable-model-invocation: true
---

Set up Langfuse tracing for the repo of this session, or, for whoever manages a GitHub repo, change who can get its keys. Follow the steps in order, and stop where a step says so.

The words the developer typed after the command: $ARGUMENTS

Pick the first step from those words:

- No words: start at step 1.
- Words that ask to see who can get this repo's keys, or to take a person or a laptop off: start at step 8.
- Words that ask for a new key pair: start at step 9.
- `help`, or any other words: start at step 10. Show the help rather than guess what the words mean, because a wrong guess could take someone off.

Never ask the developer for a Langfuse key or a Langfuse address in the chat, and never write a key yourself: anything typed in this chat is sent to Langfuse with the session once tracing starts. The developer pastes the keys into the file you open in step 5.

Never read, print or show the settings file, because once the developer has pasted the keys it holds them. To check the setup, tell the developer to start a new session and look for the "not traced" line.

Run the commands in steps 1, 2, 4, 7, 8 and 9 with the Bash tool, also on Windows.

## 1. Ask the hook

Run:

```
cd "${CLAUDE_PROJECT_DIR}" && printf '%s' '{"hook_event_name":"UserPromptExpansion"}' | sh "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/langfuse_hook.py"
```

Note its exit code, its stdout and its stderr.

## 2. Act on the answer

- **Exit 0, nothing on stderr, and stdout is a JSON object with `"decision": "block"`:** say only the text of its `reason`, and stop. Change no file.
- **Exit 0, nothing on stderr, and stdout is a JSON object with `hookSpecificOutput`:** the repo is on GitHub. Go on to step 7.
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

## 7. Share the keys through GitHub

On a GitHub repo, the repo carries its own Langfuse keys, locked so that only laptops let in can open them.

1. Run `uv --version`. If it fails, go to step 3: the sharing needs uv to load its locking library.
2. Run the command below. On Windows, put `UV_PYTHON=cpython-3.13-windows-x86_64-none ` in front of it, because Windows on ARM can load the locking library only into that Python.

   ```
   sh "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh" --fail-if-cannot-run "${CLAUDE_SKILL_DIR}/repo_keys.py" setup --project-dir "${CLAUDE_PROJECT_DIR}"
   ```

3. Act on its answer. Say each quoted text word for word: a developer who joins a repo must never see the words key, lock or CI, and these texts are written without them.

   - **Exit 0, and stdout is a JSON object; act on its `result`:**
     - `not-github`: go on to step 4.
     - `ready` or `locked`: say "Tracing is ready. Your next session in this repo is traced." Stop.
     - `joining`: start the command of step 2 again, with `wait` in place of `setup`, with the Monitor tool and a 10-minute deadline. The command prints its one line within 9 minutes, and 10 minutes is the longest watch a session started with `claude -p` allows. Then say "Setting up tracing for this repo. This takes a minute or two. I'll tell you when it's ready." and end your turn, so the developer can go on working. When the watch reports a line, say that line word for word. When the watch ends without a line, say "Tracing is not ready yet. Run /mate-record:langfuse-setup again later, or ask whoever manages this repo." and nothing else. Never start a second watch: the command would start its 9 minutes again, and the developer would wait twice as long for the same answer.
     - `not-admin`: say "This repo is not set up for tracing yet. Ask whoever manages this repo to run /mate-record:langfuse-setup first. If you manage it, install the GitHub CLI, version 2.81 or later, from https://cli.github.com, run `gh auth login` in a terminal, and run /mate-record:langfuse-setup again." Then, when `settings_hold_keys` is false, go on to step 4; when it is true, stop.
     - `need-keys`: do steps 4 and 5. Then say "This repo is not set up for tracing yet. Paste its Langfuse keys into the file that just opened (Langfuse → project → Settings → API keys), save it, and say done. Never paste them in this chat." When the developer says done, do this step 7 again.
     - `need-workflow-scope`: say "Run `gh auth refresh -h github.com -s workflow` in a terminal, then run /mate-record:langfuse-setup again." Stop.
   - **Anything else** (a non-zero exit, or no JSON on stdout): say "Tracing is not ready yet. Run /mate-record:langfuse-setup again later, or ask whoever manages this repo.", then the last line of its stderr. Stop.

## 8. People

1. Run `uv --version`. If it fails, go to step 3.
2. Run the command below, with `UV_PYTHON=cpython-3.13-windows-x86_64-none ` in front of it on Windows, as in step 7:

   ```
   sh "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh" --fail-if-cannot-run "${CLAUDE_SKILL_DIR}/repo_keys.py" admin --project-dir "${CLAUDE_PROJECT_DIR}"
   ```

3. When its `result` is `people`, show the list as plain lines: each `person`, and under it each of their `laptops`. For any other answer, go to step 11.
4. When the developer names whom to take off, here or in the words after the command, run the command of step 2 with `remove` in place of `admin`, followed by either `--person '<person>'` for a person, or `--laptop '<person>/<laptop>'` for one laptop. Use only a person and a laptop exactly as the list shows them, each in single quotes as step 5 quotes a path, and write each `'` inside as `'\''`. When the developer names someone not on the list, say so and run nothing.
5. When its `result` is `removed`, say "Taken off: <laptops>. The keys already on those laptops keep working until you put in a new key pair and delete the old one in Langfuse. To put in a new pair, run /mate-record:langfuse-setup new pair.", with the `laptops` it names. For any other answer, go to step 11.

## 9. New pair

1. Run `uv --version`. If it fails, go to step 3.
2. Run the `admin` command of step 8. When its `result` is not `people`, go to step 11.
3. Do steps 4 and 5. Then say "Replace the two Langfuse keys in the file that just opened with the new pair (Langfuse → project → Settings → API keys), save it, and say done. Never paste them in this chat."
4. When the developer says done, run the command of step 8 with `new-pair` in place of `admin`.
   - `new-pair-in`: say "The new key pair is in. Your next session in this repo is traced with it. Now delete the old key pair in Langfuse (project → Settings → API keys). Everyone else on the list gets the new pair when they run /mate-record:langfuse-setup." Stop.
   - `same-pair`: say "The file still holds the repo's current keys. Paste the new pair into it, save it, and say done." When the developer says done, do this step 4 again.
   - Any other answer: go to step 11.

## 10. Help

Say only this text, word for word, and stop:

```
/mate-record:langfuse-setup: set up tracing for this repo.
For whoever manages this repo:
/mate-record:langfuse-setup people: see who can get this repo's Langfuse keys, and take a person or a laptop off.
/mate-record:langfuse-setup new pair: put in new Langfuse keys after you made them in Langfuse.
```

## 11. Other answers in steps 8 and 9

- `not-admin`: say "Only someone with admin rights on this repo can do that. Ask whoever manages this repo. If you manage it, install the GitHub CLI, version 2.81 or later, from https://cli.github.com, run `gh auth login` in a terminal, and run /mate-record:langfuse-setup again." Stop.
- `not-let-in`: say "Run /mate-record:langfuse-setup first, so this laptop gets the repo's keys, then ask again." Stop.
- `not-github` or `no-keys-on-github`: say "This repo does not get its Langfuse keys from GitHub, so there is no one to take off and no pair to replace here." Stop.
- A non-zero exit, or no JSON on stdout: say the last line of its stderr. Stop.
