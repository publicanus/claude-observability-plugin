# Langfuse Observability Plugin for Claude Code

> This is Spire's maintained fork of [langfuse/claude-observability-plugin](https://github.com/langfuse/claude-observability-plugin), installed as `langfuse-observability` via the [mate marketplace](https://github.com/publicanus/mate-marketplace). Three behavioral differences from upstream:
>
> 1. This fork captures named teammate agents (subagents spawned with a `name:`, Claude Code's agent-teams feature) — upstream drops them silently, so a teammate-heavy session can do most of its work invisibly to Langfuse. Documented upstream as [langfuse/claude-observability-plugin#28](https://github.com/langfuse/claude-observability-plugin/issues/28).
> 2. This fork attaches pending `/hurt` annotations (from the companion [mate-hurt](https://github.com/publicanus/mate-hurt) plugin) onto their target traces — see [Attaching /hurt annotations](#attaching-hurt-annotations-mate-hurt) below. This is mate-ecosystem-specific and not expected to be upstreamed.
> 3. This fork sends only to Langfuse Cloud's EU region (`https://cloud.langfuse.com`, the default) or to a self-hosted Langfuse. It refuses Langfuse Cloud's US, HIPAA and Japan regions. Upstream defaults to the US region.
>
> The upstream README follows unchanged below.

Trace hook-backed Claude Code sessions to [Langfuse](https://langfuse.com) — turns, generations, tool calls, and token usage — with zero code changes.

## Install

```bash
claude plugin marketplace add langfuse/Claude-Observability-Plugin
claude plugin install langfuse-observability@langfuse-observability
```

The marketplace command registers the plugin marketplace and refreshes its local cache. The install command enables the plugin for your Claude Code user scope.

Restart Claude Code after install so the hook configuration is loaded.

Installing without configuration is fine: no field is required at install time, and the hook sends nothing until both API keys are set. Until then, each session starts with a message saying it is not traced, with the steps to add keys; nothing else changes in Claude Code. Configure whenever you have the keys at hand.

To set keys for one repo, run this in a Claude Code session in that repo:

```text
/langfuse-observability:langfuse-setup
```

The skill prepares the repo's `.claude/settings.local.json` with empty key fields, keeps that file out of git, lists it in `.worktreeinclude` so worktrees that Claude Code creates get a copy, and opens it in your editor. Paste your keys there and save; new sessions in the repo are then traced. The skill never asks for keys in the chat. When keys are already set, it says so and changes nothing. When the hook cannot run on the machine, it offers to install uv.

To set keys for every repo on the machine instead, configure the plugin from within a Claude Code session. This is a Claude Code slash command, not a shell command:

```text
/plugin configure langfuse-observability@langfuse-observability
```

Alternatively, pass configuration values during install:

```bash
claude plugin install langfuse-observability@langfuse-observability \
  --config LANGFUSE_PUBLIC_KEY=pk-lf-... \
  --config LANGFUSE_SECRET_KEY=sk-lf-... \
  --config LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

The plugin requires or accepts:

| Field                 | Description                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------- |
| `LANGFUSE_SECRET_KEY` | Your Langfuse secret key (`sk-lf-...`). Stored in your OS keychain.                                  |
| `LANGFUSE_PUBLIC_KEY` | Your Langfuse public key (`pk-lf-...`).                                                              |
| `LANGFUSE_BASE_URL`   | https://cloud.langfuse.com (Langfuse Cloud EU, the default) or your self-hosted URL. The plugin refuses Langfuse Cloud's US, HIPAA and Japan regions: it sends nothing there and says so when the session starts. |
| `LANGFUSE_USER_ID`    | Optional. User identifier attached to every trace (shown as the user in Langfuse). Leave it empty to use your login email. See [Trace user](#trace-user). |
| `CC_LANGFUSE_DEBUG`   | Verbose logging to `~/.claude/state/langfuse_hook.log`.                                              |
| `CC_LANGFUSE_MAX_CHARS` | Truncate captured inputs/outputs to this many characters (default 20000).                          |
| `CC_LANGFUSE_SKILL_TAGS` | Tag traces with `skill:<name>` for every skill invoked in the turn (default true).                 |
| `CC_LANGFUSE_CAPTURE_SKILL_CONTENT` | Include injected skill instruction text in the Skill tool span output (default false).  |
| `CC_LANGFUSE_TRACE_SEED` | Optional. Opt-in seed for deterministic trace IDs — see [Deterministic trace IDs](#deterministic-trace-ids). |

Get keys from your Langfuse project settings → API Keys.

### Trace user

Each trace carries a user, so Langfuse can show usage per person. The hook takes the first of these that has a value:

1. `LANGFUSE_USER_ID` or `CC_LANGFUSE_USER_ID`, as an env var or as plugin config.
2. Your Claude login email: `oauthAccount.emailAddress` in `~/.claude.json`.
3. `git config user.email`, as git sees it from the session's working directory.

With none of these, the trace has no user.

If `~/.claude.json` or the git config exists but cannot be read, the hook does not move on to the next source. The trace has no user, and Claude Code shows a message that names the problem.

### Repo name

Each trace carries the name of its repo, so a Langfuse dashboard can total token use per repo. The name is on the trace as the tag `repo:<name>`, and on the trace and every observation in it as the metadata key `repo`.

The name is the last part of the address of the git remote `origin`, without `.git`: `git@github.com:acme/shop.git` gives `shop`. Without `origin`, the hook uses the first remote that `git remote` lists. Without any remote, the name is the repo's folder name; all worktrees of that repo share the main folder's name. Outside git, it is the session folder's name. The remote's address itself, with any user name or token in it, is never sent.

A fork and its source with the same name, such as `jaap/shop` and `acme/shop`, share one tag.

## Requirements

One of:

- [uv](https://docs.astral.sh/uv/) (recommended) on `PATH`. The hook uses `uv run --script` and installs the Langfuse SDK from the script metadata automatically.
- Python 3.10+ as `python3` with `langfuse>=4.0,<5` installed in that Python environment. This is only used as a fallback when `uv` is not on `PATH`.

If neither is set up, the hook exits silently — no impact on Claude Code.

On Windows, install [Git for Windows](https://gitforwindows.org/) and uv. The hook starts through `sh`, which Git for Windows provides, and uv is the only supported way to run it there.

## How it works

A hook reads the session transcript incrementally on every turn (Stop) and at session end (SessionEnd), and emits a Langfuse trace with one span per turn, nested generations per assistant message, and child tool spans for every tool call. Token usage is captured when present.

When no Langfuse keys are set for the repo, the hook sends nothing. At session start (SessionStart) it shows a message saying so: the line "Langfuse: no keys for this repo, this session is not traced.", the setup skill's name, and short manual steps for your system (macOS, Linux or Windows). The plugin's steps are in `hooks/no_keys_steps/<system>.txt`. A repo can replace them for one or more systems with its own `.claude/langfuse-observability/no-keys-steps/<system>.txt` at the repo root, where `<system>` is `macos`, `linux` or `windows`.

A tool span whose call failed carries `level: ERROR` and a `statusMessage` naming the failure class (`Tool call failed`, or `Permission denied` for a denied tool call), so errors and denials are queryable via the Langfuse API without fetching the span's output. Permission denials additionally carry a `denial_kind` metadata field (`user-rejected` or `permission-rule`) distinguishing a user's explicit rejection from an automatic permission-rule block. Successful calls keep the default level.

The plugin observes only data that Claude Code exposes through hooks and transcript files.

Captured:

- Claude Code CLI sessions
- Claude Code GUI Code mode sessions

Not captured:

- Regular Claude Desktop Chat mode conversations

If you use the desktop app, switch to Code mode for hook-backed tracing. Regular Chat mode does not invoke Claude Code Stop or SessionEnd hooks and does not write the transcript file this plugin reads.

State is kept in `~/.claude/state/langfuse_state.json` so re-runs only emit new turns.

## Deterministic trace IDs

By default, trace IDs are auto-generated, so external systems (CI harnesses, benchmark
runners, dataset-experiment services) that run Claude Code headlessly have to poll the
Langfuse API to discover the trace ID of a run. Setting `CC_LANGFUSE_TRACE_SEED` makes
trace IDs predictable: any system that knows the seed can compute the trace ID of each
turn **before** the trace exists.

With the seed set, the trace ID for turn `N` (1-based, counted per session across hook
runs) is derived as:

```python
from langfuse import Langfuse

trace_id = Langfuse.create_trace_id(seed=f"{CC_LANGFUSE_TRACE_SEED}:{turn_number}")
# equivalent to: hashlib.sha256(f"{seed}:{turn_number}".encode()).hexdigest()[:32]
```

Use a **unique seed per session** — otherwise two sessions collide on the same trace
IDs. The recommended seed is the Claude session UUID you already pass via
`claude -p --session-id`.

Example: a harness runs a single-turn headless prompt and links the resulting trace to
a Langfuse dataset run item without ever fetching the trace:

```python
import subprocess, uuid, os
from langfuse import Langfuse

langfuse = Langfuse()
session_id = str(uuid.uuid4())

# Precompute the trace ID for turn 1 — no polling needed.
trace_id = Langfuse.create_trace_id(seed=f"{session_id}:1")

# Link it to a dataset run item BEFORE the run.
langfuse.api.dataset_run_items.create(
    dataset_item_id=item.id,
    run_name="claude-code-benchmark-v1",
    trace_id=trace_id,
)

subprocess.run(
    ["claude", "-p", "Refactor utils.py", "--session-id", session_id],
    env={**os.environ, "CC_LANGFUSE_TRACE_SEED": session_id},
)
# When the hook uploads the turn, its trace appears in Langfuse under trace_id.
```

If derivation or wiring fails for any reason, the hook falls back to an auto-generated
trace ID (fail-open, like the rest of the hook). Unset or empty seed = unchanged behavior.

### Reading the latest trace ID without polling

Whether or not a seed is set, each session's entry in
`~/.claude/state/langfuse_state.json` carries `latest_trace_id`: the trace ID
actually emitted for that session's most recent turn. A same-machine consumer
that knows the session ID and transcript path can read it directly instead of
polling the Langfuse API:

```python
import hashlib, json
from pathlib import Path

def latest_trace_id(session_id: str, transcript_path: str) -> str | None:
    key = hashlib.sha256(f"{session_id}::{transcript_path}".encode()).hexdigest()
    state = json.loads(Path.home().joinpath(".claude/state/langfuse_state.json").read_text())
    return state.get(key, {}).get("latest_trace_id")
```

`latest_trace_id` tracks the main session's conversational turns only. Teammate
(agent-team) turns are recorded under their own state entries and never set
this field, so a teammate-only entry has it null or absent — do not expect
teammate sub-traces to move it.

## Attaching /hurt annotations (mate-hurt)

The companion [mate-hurt](https://github.com/publicanus/mate-hurt) plugin lets an operator flag something that went wrong in the current session (`/hurt <what happened>`). mate-hurt holds no Langfuse credentials and never contacts Langfuse itself — it resolves the target trace id from `latest_trace_id` (see above) and queues the annotation locally at `~/.claude/state/hurt_pending.json`, a JSON array of:

| Field        | Meaning                                                          |
| ------------ | ----------------------------------------------------------------- |
| `id`         | uuid4, unique per queued hurt.                                    |
| `trace_id`   | The target trace, resolved once by mate-hurt.                     |
| `category`   | One of a fixed six-value taxonomy (`correction-repeated`, `redone-work`, `stalled-loop`, `missed-trigger`, `ci-escape`, `other`). |
| `comment`    | The operator's own words.                                         |
| `created`    | ISO 8601 UTC timestamp.                                            |
| `session_id` | For log correlation only.                                         |

This hook is the sole consumer (`hooks/hurt_annotations.py`): on every `Stop`/`SessionEnd` firing, before anything else, it attempts every queued entry using its own already-configured Langfuse credentials — the same ones writing this machine's traces, so a hurt can never be misrouted to the wrong project. For each entry it confirms the trace exists, provisions a `hurt` categorical score config idempotently, then writes the categorical `hurt` score with the operator's text as the score's comment. In Langfuse, find hurts by the `hurt` score.

The trace is looked up through Langfuse's v2 Observations API (`/api/public/v2/observations`), which Langfuse Cloud and self-hosted Langfuse v4 serve. A self-hosted Langfuse v3 server does not, so there hurts stay queued and are not attached. A hurt whose trace is not there yet waits in the queue for a later run.

An entry is removed only once all of that succeeds. One that can't yet be attached — Langfuse unreachable, the trace not yet uploaded, credentials misconfigured — stays queued and is retried the next time *any* Claude Code session's hook fires on this machine (the queue is not scoped to the session that created it, so a healthy project's hook can drain hurts left behind by one whose hook never runs again). Entries older than 30 days that are still unresolvable are dropped rather than retried forever.

## Privacy

This plugin transmits your Claude Code session data — conversation turns, assistant
generations, tool calls, and token-usage statistics — to the Langfuse endpoint you
configure (`LANGFUSE_BASE_URL`, default `https://cloud.langfuse.com`, Langfuse Cloud
EU; self-hosted endpoints are supported, Langfuse Cloud's other regions are refused). Data is sent at the end of each turn (the
`Stop` hook) and at session end (`SessionEnd`) using the Langfuse API keys you
provide, which are stored in your OS keychain. No data is sent anywhere other than
the endpoint you configure. If the companion mate-hurt plugin is installed, this
also includes any queued `/hurt` annotation's category and operator-written
comment, sent to the same endpoint (see [Attaching /hurt annotations](#attaching-hurt-annotations-mate-hurt)).

Before any data leaves your machine, eight kinds of secret are replaced by
`[REDACTED]` in every input and output: a key that starts with `sk-`, an AWS access
key (`AKIA…`), a JSON Web Token, the value after `password=` (also `DB_PASSWORD=` and
other names that end in `password`), a GitHub token (`ghp_…`), a Slack token (such as
`xoxb-…`), a private-key block, and the token after `Authorization: Bearer`. Other
kinds of secret are sent as they are. So are the metadata, such as the project folder
and the git branch, and the `/hurt` comment.

For how Langfuse Cloud handles data it receives, see the Langfuse privacy policy:
https://langfuse.com/privacy . When using a self-hosted Langfuse instance, your data
stays within your own infrastructure.

## Reconfigure

In Claude Code, run:

```text
/plugin configure langfuse-observability@langfuse-observability
```

## Disable tracing

To stop tracing for new Claude Code CLI sessions and new Claude Code GUI Code mode sessions, disable the plugin:

```bash
claude plugin disable langfuse-observability@langfuse-observability --scope user
claude plugin list
```

`claude plugin list` should show the plugin as disabled. This keeps the plugin installed and does not delete your configuration.

To enable tracing again:

```bash
claude plugin enable langfuse-observability@langfuse-observability --scope user
```

If a Claude Code session is already running, restart it after disabling the plugin. Running sessions may have loaded their hooks at startup, so disabling the plugin is intended for new sessions.

## Uninstall

```bash
claude plugin uninstall langfuse-observability
```

## Troubleshooting

- Nothing in Langfuse: check `~/.claude/state/langfuse_hook.log` (enable `CC_LANGFUSE_DEBUG`).
- Desktop chat has no traces: regular Claude Desktop Chat mode is not hook-backed. Use the `claude` CLI or Claude Code GUI Code mode.
- Hook not firing: confirm with `claude plugin list` that langfuse-observability is enabled; restart Claude Code.
- langfuse import errors (no uv): install uv, or ensure the `python3` on your PATH is Python 3.10+ and has `langfuse>=4.0,<5` installed.

## License

MIT
