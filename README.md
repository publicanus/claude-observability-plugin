# mate-record: Langfuse tracing and /hurt for Claude Code

> This is Spire's maintained fork of [langfuse/claude-observability-plugin](https://github.com/langfuse/claude-observability-plugin), installed as `mate-record` via the [mate marketplace](https://github.com/publicanus/mate-marketplace). Three behavioral differences from upstream:
>
> 1. This fork captures named teammate agents (subagents spawned with a `name:`, Claude Code's agent-teams feature) — upstream drops them silently, so a teammate-heavy session can do most of its work invisibly to Langfuse. Documented upstream as [langfuse/claude-observability-plugin#28](https://github.com/langfuse/claude-observability-plugin/issues/28).
> 2. This fork has a `/mate-record:hurt` command, and attaches the annotations it queues onto their target traces — see [Flagging what hurt with /hurt](#flagging-what-hurt-with-hurt) below. This is mate-ecosystem-specific and not expected to be upstreamed.
> 3. This fork sends only to Langfuse Cloud's EU region (`https://cloud.langfuse.com`, the default) or to a self-hosted Langfuse. It refuses Langfuse Cloud's US, HIPAA and Japan regions. Upstream defaults to the US region.
>
> The upstream README follows unchanged below.

Trace hook-backed Claude Code sessions to [Langfuse](https://langfuse.com) — turns, generations, tool calls, and token usage — with zero code changes.

## Install

```bash
claude plugin marketplace add publicanus/mate-marketplace
claude plugin install mate-record@mate-marketplace
```

The marketplace command registers the plugin marketplace and refreshes its local cache. The install command enables the plugin for your Claude Code user scope.

Restart Claude Code after install so the hook configuration is loaded.

Installing without configuration is fine: no field is required at install time, and the hook sends nothing until both API keys are set. Until then, each session starts with a message saying it is not traced, with the steps to add keys; nothing else changes in Claude Code. Configure whenever you have the keys at hand.

To set keys for one repo, run this in a Claude Code session in that repo:

```text
/mate-record:langfuse-setup
```

The skill prepares `.claude/settings.local.json` in the repo's main checkout with empty key fields, keeps that file out of git, lists it in `.worktreeinclude` so worktrees that Claude Code creates get a copy, and opens it in your editor. Paste your keys there and save; new sessions in the repo are then traced. The skill never asks for keys in the chat. When the repo's own `.claude/settings.local.json` already holds both keys, the skill says so and changes nothing. In a worktree, that is the main checkout's file, or else the worktree's own. The repo is the one the Claude Code session started in, also after the session changes to another folder. Keys set anywhere else, such as for every repo on the machine, do not count. When the hook cannot run on the machine, it offers to install uv.

The keys and the Langfuse address go only in the `env` block of the repo's own `.claude/settings.local.json`: `LANGFUSE_PUBLIC_KEY` (`pk-lf-...`), `LANGFUSE_SECRET_KEY` (`sk-lf-...`) and, for your own Langfuse server, `LANGFUSE_BASE_URL`. Without an address there, traces go to https://cloud.langfuse.com, Langfuse Cloud EU. Keys and addresses set anywhere else, such as in your terminal or in the plugin's settings, are not used. The plugin refuses Langfuse Cloud's US, HIPAA and Japan regions: it sends nothing there and says so when the session starts.

The plugin's other settings are read from an environment variable, else from the plugin's settings (`/plugin configure`):

| Field                 | Description                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------- |
| `LANGFUSE_USER_ID`    | Optional. User identifier attached to every trace (shown as the user in Langfuse). Leave it empty to use your login email. See [Trace user](#trace-user). |
| `CC_LANGFUSE_DEBUG`   | Verbose logging to `~/.claude/state/langfuse_hook.log`.                                              |
| `CC_LANGFUSE_MAX_CHARS` | Truncate captured inputs/outputs to this many characters (default 20000).                          |
| `CC_LANGFUSE_SKILL_TAGS` | Tag traces with `skill:<name>` for every skill invoked in the turn (default true).                 |
| `CC_LANGFUSE_CAPTURE_SKILL_CONTENT` | Include injected skill instruction text in the Skill tool span output (default false).  |
| `CC_LANGFUSE_TRACE_SEED` | Optional. Opt-in seed for deterministic trace IDs — see [Deterministic trace IDs](#deterministic-trace-ids). |
| `CC_LANGFUSE_WORK_ITEM_BRANCH_PATTERN` | Optional. A regular expression whose first group picks the work item out of the git branch, such as `^feat/(\d+)-`. See [Work item](#work-item). |

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

### Work item

Each trace can name the work item its turn served, as the tag `work-item:<id>`, so a plugin's maintainer can see how each work item went. The tag goes on main-session traces and teammate traces alike, and an agent's work is inside its turn's trace.

A tool names the item with the command `langfuse-work-item`, run in a Bash tool call of the session. Claude Code puts it on the PATH while this plugin is enabled. It needs uv, or `python3` as in [Requirements](#requirements).

```bash
langfuse-work-item set 37   # from this turn on, traces carry work-item:37
langfuse-work-item clear    # from this turn on, the branch pattern decides again
```

The command prints nothing and exits 0 when it recorded the call. It exits 2, with one line on stderr, when the arguments are wrong, the id is empty or holds whitespace or a control character, or `CLAUDE_CODE_SESSION_ID` is not set or holds more than letters, digits and `-`. It exits 1, with one line on stderr, when it cannot write its record or cannot run on this machine.

The item a tool names applies from the turn in which it ran, and a later `set` replaces it from its own turn on. When a turn names two items, it carries the last. The records are in `~/.claude/state/langfuse_work_items/<session>.jsonl`, and the hook deletes those older than 30 days.

When no tool named an item, the setting `CC_LANGFUSE_WORK_ITEM_BRANCH_PATTERN` can give one from the turn's git branch. The pattern is a regular expression, and its first group is the id: `^feat/(\d+)-` gives `37` for the branch `feat/37-plugins-on-trace`. The pattern may match anywhere in the branch name unless it starts with `^`. An item a tool named wins over the branch. A detached checkout, whose branch is `HEAD`, gives no item.

A pattern that is not a valid regular expression, or has no group, is refused: traces are sent with no item from the branch, and the hook log `~/.claude/state/langfuse_hook.log` holds the line `work item branch pattern refused: <pattern>: <reason>`. When a record file cannot be read, the log names it, and that session's traces carry no work-item tag.

### Plugins

Each trace lists the plugins its turn used, with their versions, so a plugin's maintainer can compare plugins version against version. The list is in the trace's metadata key `plugins`, sorted by name, for example `[{"name": "mate-core", "version": "8.9.0"}, {"name": "document-skills"}]`.

A plugin counts as used when an agent, skill or command from it ran in the turn or in one of the agent rounds the turn started, at any depth (see [Agent runs](#agent-runs)). A teammate's trace lists the plugins of its own turn, its own agent's plugin included. A plugin used only through its hooks is not listed, because the transcript does not say which plugin a hook came from.

The version comes from Claude Code's `~/.claude/plugins/installed_plugins.json`. The plugin's install for this repo counts, a local install before a project install; else its machine-wide install counts. A session in a worktree uses the installs of the repo's main checkout. The version is cut at its first dash, so `8.9.0-0ae331d8a58b` shows as `8.9.0`. Only a version made of digits and dots up to that dash counts; anything else, such as a bare commit hash or `unknown`, is no version, and the plugin is listed without one.

If that file exists but cannot be read, the hook stops with an error that names the file, and the session is not traced until the file is fixed.

### Component

Each observation names the component that did its work, so a plugin's maintainer can measure each agent version against version. The component is in the observation's metadata key `component`: `session` for the main session's own work, and the agent's type, such as `pymate:developer`, for an agent and everything in it, its model calls included. Every observation of a teammate's trace names the teammate's agent type.

Where the component names a plugin, the metadata key `plugin` holds that plugin, such as `pymate`, and the metadata key `plugin_version` holds the plugin's version, by the rule above. A plugin with no version has no `plugin_version` key. The main session's own work, and an agent whose type names no plugin, such as `general-purpose`, have neither key. Langfuse's own version field stays empty, on observations and traces alike.

### Agent runs

Each round of an agent is a run of its own, in the trace of the turn that started the round, so a plugin's maintainer can count an agent's rounds and see what each one cost. A round is the agent's work for one message: the prompt it was launched with, or one message the main session sent it with `SendMessage`. A message to an agent that is still busy ends the running round where the message arrives, and starts the next one.

A run is the observation `Subagent: <description>`, with the metadata key `round` holding its number from 1. A round after the first is named `Subagent: <description> - round <n>`. A turn that continues an agent waits for the round before its trace is sent: until a task notification for its `SendMessage` call, or until the agent hands back its report, whichever comes first. At session end the trace is sent anyway, with the round's work up to then.

When an agent's task notification or hand-back arrives after the turn that started the agent has ended, the session's reply to it is a trace of its own, with the notification or hand-back as its input. That reply counts as a turn, numbered where it comes in the session, also for the turn number in a [deterministic trace ID](#deterministic-trace-ids).

When the agent's transcript holds no start of the round that a `SendMessage` call started, at or after the call, the trace is sent without that run, and the hook log `~/.claude/state/langfuse_hook.log` holds a line `No run for SendMessage call <id>: agent <agent id> has no round <n> that starts at or after the call`. An agent that another agent continues gets no runs of its own for those rounds.

## Requirements

One of:

- [uv](https://docs.astral.sh/uv/) (recommended) on `PATH`. The hook uses `uv run --script` and installs the Langfuse SDK from the script metadata automatically.
- Python 3.10+ as `python3` with `langfuse>=4.2,<5` installed in that Python environment. This is only used as a fallback when `uv` is not on `PATH`.

If neither is set up, each session start shows a message that the session is not traced. Claude Code works as usual. The same holds when uv is on `PATH` but cannot download the Langfuse SDK, such as on a machine without a network: the message then says that the hook could not download what it needs to run.

On Windows, install [Git for Windows](https://gitforwindows.org/) and uv. The hook starts through `sh`, which Git for Windows provides, and uv is the only supported way to run it there.

## How it works

A hook reads the session transcript incrementally on every turn (Stop) and at session end (SessionEnd), and emits a Langfuse trace with one span per turn, nested generations per assistant message, and child tool spans for every tool call. Token usage is captured when present.

The hook does not send the traces itself. It stores them in `~/.claude/state/langfuse_waiting/`, and a background process sends them and deletes each one once Langfuse has accepted it. When Langfuse cannot be reached or answers with an error, the traces wait there, and the next turn in any session on the computer sends them, each at the time it happened. Each waiting file holds a copy of its repo's Langfuse keys, so that a session in another repo can send it. When Langfuse rejects the keys, the traces are dropped.

When no Langfuse keys are set for the repo, the hook sends nothing. At session start (SessionStart) it shows a message saying so: the line "Langfuse: no keys for this repo, this session is not traced.", the setup skill's name, and short manual steps for your system (macOS, Linux or Windows). The plugin's steps are in `hooks/no_keys_steps/<system>.txt`. A repo can replace them for one or more systems with its own `.claude/mate-record/no-keys-steps/<system>.txt` at the repo root, where `<system>` is `macos`, `linux` or `windows`.

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

The seed also keeps a turn from showing up twice in Langfuse when the hook could not save its progress and sends that turn again on a later run.

With the seed set, the trace ID for turn `N` (1-based, counted per session across hook
runs, in the order the turns happened) is derived as:

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

If derivation or wiring fails, a turn sent at once falls back to an auto-generated
trace ID (fail-open, like the rest of the hook). A turn that waits for a background
agent is not sent when its trace ID cannot be derived, and the hook log says so.
Unset or empty seed = unchanged behavior.

### Reading the latest trace ID without polling

Whether or not a seed is set, each session's entry in
`~/.claude/state/langfuse_state.json` carries `latest_trace_id`: the trace ID
of that session's most recent turn. A turn that waits for a background agent
gets its trace ID when the hook first sees it, but is sent only when the agent
finishes or the session ends, so that trace may not be in Langfuse yet. A
same-machine consumer that knows the session ID and transcript path can read it
directly instead of polling the Langfuse API:

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

## Flagging what hurt with /hurt

`/mate-record:hurt <what happened>` lets an operator flag something that went wrong in the current session. It holds no Langfuse credentials and never contacts Langfuse itself. It queues the note locally at `~/.claude/state/hurt_unresolved.json`, without a trace id. Once the hook has built the turn before the `/hurt`, it moves the note to `~/.claude/state/hurt_pending.json` with that turn's trace id. That turn may have been stopped with Esc, also before any reply, or have ended in an API error. `hurt_pending.json` is a JSON array of:

| Field        | Meaning                                                          |
| ------------ | ----------------------------------------------------------------- |
| `id`         | uuid4, unique per queued hurt.                                    |
| `trace_id`   | The target trace, filled in by the hook.                          |
| `category`   | One of a fixed six-value taxonomy (`correction-repeated`, `redone-work`, `stalled-loop`, `missed-trigger`, `ci-escape`, `other`). |
| `comment`    | The operator's own words.                                         |
| `created`    | ISO 8601 UTC timestamp.                                            |
| `session_id` | For log correlation only.                                         |
| `target`     | Optional. A hash of the Langfuse address and public key in the repo's own `.claude/settings.local.json` when the hurt was queued. |

This hook is the sole consumer (`hooks/hurt_annotations.py`): on every `Stop`/`SessionEnd` firing, before anything else, it attempts every queued entry whose `target` matches its own repo's keys, or that has no `target`, using those keys — the same ones writing that repo's traces, so a hurt can never be misrouted to the wrong project. For each entry it confirms the trace exists, provisions a `hurt` categorical score config idempotently, then writes the categorical `hurt` score with the operator's text as the score's comment. In Langfuse, find hurts by the `hurt` score.

The trace is looked up through Langfuse's v2 Observations API (`/api/public/v2/observations`), which Langfuse Cloud and self-hosted Langfuse v4 serve. A self-hosted Langfuse v3 server does not, so there hurts stay queued and are not attached. A hurt whose trace is not there yet waits in the queue for a later run.

An entry is removed only once all of that succeeds. One that can't yet be attached — Langfuse unreachable, the trace not yet uploaded, credentials misconfigured — stays queued and is retried the next time the hook of a session with the same keys fires on this machine (the queue is not scoped to the session that created it, so another session of the same project can attach hurts left behind by one whose hook never runs again). Entries older than 30 days that are still unresolvable are dropped rather than retried forever.

## Privacy

This plugin transmits your Claude Code session data — conversation turns, assistant
generations, tool calls, and token-usage statistics — to the Langfuse endpoint you
configure (`LANGFUSE_BASE_URL` in the repo's `.claude/settings.local.json`, default `https://cloud.langfuse.com`, Langfuse Cloud
EU; self-hosted endpoints are supported, Langfuse Cloud's other regions are refused). Data is sent at the end of each turn (the
`Stop` hook) and at session end (`SessionEnd`) using the Langfuse API keys you
put in that file. No data is sent anywhere other than
the endpoint you configure. This also includes any queued `/hurt` annotation's category and operator-written
comment, sent to the same endpoint (see [Flagging what hurt with /hurt](#flagging-what-hurt-with-hurt)).

Before any data leaves your machine, these kinds of secret are replaced by
`[REDACTED]` in every input and output: a key that starts with `sk-`, an AWS access
key (`AKIA…`), a JSON Web Token, a GitHub token (`ghp_…`, `gho_…` or `github_pat_…`),
a Slack token (such as `xoxb-…`), a private-key block, the token after
`Authorization: Bearer`, and the password in an address such as
`postgres://user:password@host`.

A value is also replaced when its name ends in `password`, `secret`, `token`,
`api_key`, `apikey`, `secret_key`, `access_key` or `private_key`, in any case. This
holds for `name=value`, `name: value`, JSON, and the fields of a tool's input or
output. So `DB_PASSWORD=hunter2`, `{"api_key": "abc123"}` and
`AWS_SECRET_ACCESS_KEY: wJalr` all lose their value. Numbers and true/false stay
visible, so `input_token: 12` is sent as it is.

Other kinds of secret are sent as they are. So are the metadata, such as the project folder
and the git branch, and the `/hurt` comment.

For how Langfuse Cloud handles data it receives, see the Langfuse privacy policy:
https://langfuse.com/privacy . When using a self-hosted Langfuse instance, your data
stays within your own infrastructure.

## Reconfigure

To change the keys or the address, edit the repo's `.claude/settings.local.json`; the hook reads it again at the end of each turn. To change the other settings, run in Claude Code:

```text
/plugin configure mate-record@mate-marketplace
```

## Disable tracing

To stop tracing for new Claude Code CLI sessions and new Claude Code GUI Code mode sessions, disable the plugin:

```bash
claude plugin disable mate-record@mate-marketplace --scope user
claude plugin list
```

`claude plugin list` should show the plugin as disabled. This keeps the plugin installed and does not delete your configuration.

To enable tracing again:

```bash
claude plugin enable mate-record@mate-marketplace --scope user
```

If a Claude Code session is already running, restart it after disabling the plugin. Running sessions may have loaded their hooks at startup, so disabling the plugin is intended for new sessions.

## Uninstall

```bash
claude plugin uninstall mate-record@mate-marketplace
```

## Troubleshooting

- Nothing in Langfuse: check `~/.claude/state/langfuse_hook.log` (enable `CC_LANGFUSE_DEBUG`).
- Desktop chat has no traces: regular Claude Desktop Chat mode is not hook-backed. Use the `claude` CLI or Claude Code GUI Code mode.
- Hook not firing: confirm with `claude plugin list` that mate-record is enabled; restart Claude Code.
- langfuse import errors (no uv): install uv, or ensure the `python3` on your PATH is Python 3.10+ and has `langfuse>=4.2,<5` installed.

## License

MIT
