---
description: Flag something in this session that hurt, on its Langfuse trace
argument-hint: <what happened> [category override]
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/hurt.py":*)
---

The operator just typed: $ARGUMENTS

This is a complaint about something that went wrong in the current session — queue it as a pending annotation for the Langfuse trace of the turn before this one. All of the deterministic work (queuing the record) is done by a script; your job is to read the operator's text, settle on a category, and invoke the script correctly. The actual Langfuse writes (tag, score, comment) happen later, made by this plugin's hook once that turn's trace is in Langfuse — this script never contacts Langfuse and holds no Langfuse credentials.

## 1. Determine the category

Six categories exist. Pick exactly one, in order of preference:

1. **Inline override**: if the operator's text names one of the categories below explicitly (e.g. ends with `category: stalled-loop`, or just contains the bare category name as a clear, deliberate label), use that category and strip the override token out of the text before passing it on as the comment.
2. **Inference**: otherwise, infer the category from what the operator described, using these definitions:
   - `correction-repeated` — the same correction had to be given more than once for essentially the same mistake.
   - `redone-work` — work that had already been done had to be thrown away and redone.
   - `stalled-loop` — the agent got stuck retrying something without converging or making progress.
   - `missed-trigger` — an instruction, spec, memory, or written rule that should have applied was missed or ignored.
   - `ci-escape` — something that should have been caught before merge/ship escaped into CI, review, or production.
   - `other` — none of the above fit.

If nothing in the text supports any of the first five, use `other` — don't force a fit.

## 2. Invoke the script

Run, via the Bash tool:

```
cd "${CLAUDE_PROJECT_DIR}" && sh "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/hurt.py" --category "<chosen-category>" <<'HURT_NOTE_END'
<text>
HURT_NOTE_END
```

`<text>` is the operator's text with the override token stripped, written as typed between the two lines above. Pass it this way, never as an argument: the quoted `'HURT_NOTE_END'` keeps a `$` or a backtick in the operator's words as typed, and on Windows an argument that starts with `/` is rewritten into a Windows path.

## 3. Report the result

Relay the script's own output line back to the operator verbatim — it already states plainly whether the annotation was queued or why it wasn't (no session id, no Langfuse keys in this repo, the queue stayed busy). Do not add trace or transcript content beyond what the operator themselves typed in $ARGUMENTS. Do not retry automatically on failure; a non-resolution means nothing was queued, which is the correct outcome, not an error to work around. A successful queue is not yet a successful write — the annotation lands in Langfuse once the hook has sent that turn's trace.
