#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "langfuse>=4.2,<5",
# ]
# ///
"""
Claude Code -> Langfuse hook

"""

import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
import hashlib
import importlib.metadata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit


# ----------------- Langfuse import (fail-open) -----------------
try:
    from langfuse import Langfuse, propagate_attributes
    from opentelemetry import trace as otel_trace_api
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
except Exception:
    sys.exit(0)


# Importable as a sibling of this file regardless of how it was invoked
# (uv run --script puts the script's own directory on sys.path[0] already,
# but the test harness loads this file via importlib without doing so).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_files import (  # noqa: E402
    HURT_PENDING_FILE,
    HURT_PENDING_LOCK_FILE,
    STATE_DIR,
    STATE_FILE,
    FileLock,
    get_session_state_key,
    read_state,
    retry_until,
)


# ----------------- Paths -----------------
LOG_FILE = STATE_DIR / "langfuse_hook.log"
LOCK_FILE = STATE_DIR / "langfuse_state.lock"

# Claude Code's own account file; its oauthAccount.emailAddress is the login email.
CLAUDE_JSON_FILE = Path.home() / ".claude.json"

# Claude Code's record of each plugin's installs on this machine, with their scope and version.
INSTALLED_PLUGINS_FILE = Path.home() / ".claude" / "plugins" / "installed_plugins.json"

# Trace data that waits until Langfuse has accepted it, and the lock the one
# background sender holds while it sends.
WAITING_DIR = STATE_DIR / "langfuse_waiting"
WAITING_LOCK_FILE = STATE_DIR / "langfuse_waiting.lock"
WAITING_LOCK_TIMEOUT_S = 60.0
SEND_WAITING_FLAG = "--send-waiting"

# The marker file of each session's named work item; see work_item.py.
WORK_ITEMS_DIR = STATE_DIR / "langfuse_work_items"
WORK_ITEM_FLAG = "--work-item"

# Session entries in the state file, and work item markers, are dropped at this age.
STATE_MAX_AGE = timedelta(days=30)

from hurt_annotations import process_pending_hurts  # noqa: E402
from langfuse_client import LangfuseClient, keys_rejected  # noqa: E402
from secret_masking import mask_secrets  # noqa: E402
from repo_checkout import Checkout, find_checkout, project_folder  # noqa: E402
from settings_file import (  # noqa: E402
    SETUP_COMMAND,
    SettingsFileError,
    env_holds_keys,
    read_env_keys,
    read_keys,
    repo_settings_env,
)
from waiting_turns import Target, send_waiting_turns, start_sender, waiting_files, write_waiting_batch  # noqa: E402
import work_item  # noqa: E402


# ----------------- Configuration -----------------
def _opt(name: str) -> str:
    """Read a config value: plain env var first, plugin userConfig (CLAUDE_PLUGIN_OPTION_<NAME>) as fallback.

    Not for the keys or the Langfuse address: those come only from repo_settings_env.
    """
    return os.environ.get(name) or os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name}") or ""

DEBUG = _opt("CC_LANGFUSE_DEBUG").lower() == "true"
SKILL_TAGS = (_opt("CC_LANGFUSE_SKILL_TAGS") or "true").lower() == "true"
CAPTURE_SKILL_CONTENT = _opt("CC_LANGFUSE_CAPTURE_SKILL_CONTENT").lower() == "true"
try:
    MAX_CHARS = int(_opt("CC_LANGFUSE_MAX_CHARS") or "20000")
except ValueError:
    MAX_CHARS = 20000

# Bound for unresolved task notifications kept in the state file between runs.
MAX_PENDING_TASK_NOTIFICATIONS = 50

@dataclass
class LangfuseConfig:
    public_key: str
    secret_key: str
    host: str
    user_id: Optional[str]
    # Every trace carries it as the tag `repo:<name>` and the metadata key `repo`.
    repo_name: str
    trace_seed: Optional[str] = None
    # Set when a source for the trace user exists but could not be read; shown to the user.
    user_warning: Optional[str] = None
    # read_plugin_versions for the session's repo; every trace lists the plugins it used with these.
    plugin_versions: Dict[str, Optional[str]] = field(default_factory=dict)
    # CC_LANGFUSE_WORK_ITEM_BRANCH_PATTERN; None when unset or refused.
    work_item_branch_pattern: Optional[re.Pattern] = None

NO_KEYS_WARNING = "Langfuse: no keys for this repo, this session is not traced."
OUTSIDE_KEYS_NOT_USED = (
    "Langfuse: keys set outside this repo's .claude/settings.local.json, such as in your "
    "terminal or the plugin's settings, are not used."
)
# Ends every text that tells the user to fix the repo's settings file.
SETTINGS_REREAD = "The hook reads that file again at the end of each turn."
ALREADY_CONFIGURED = "Langfuse: tracing is already configured for this repo."

# The manual steps shown at SessionStart without keys: one text per system. A repo's
# own text, at the root of its git checkout, wins over the plugin's for that system.
NO_KEYS_STEPS_DIR = Path(__file__).resolve().parent / "no_keys_steps"
REPO_NO_KEYS_STEPS_DIR = Path(".claude") / "mate-record" / "no-keys-steps"
NO_KEYS_STEPS_NAMES = {"darwin": "macos", "linux": "linux", "win32": "windows"}

EU_CLOUD_HOST = "https://cloud.langfuse.com"
# Langfuse Cloud's regions outside the EU, by hostname: https://langfuse.com/docs/data-regions
NON_EU_CLOUD_REGIONS = {
    "us.cloud.langfuse.com": "US",
    "hipaa.cloud.langfuse.com": "HIPAA (US)",
    "jp.cloud.langfuse.com": "Japan",
}

class RefusedHostError(Exception):
    """The Langfuse address is a Langfuse Cloud region outside the EU; str() is the user message."""

def _hostname(host: str) -> str:
    return urlsplit(host.strip()).hostname or ""

def check_host_allowed(host: str) -> None:
    """Raises RefusedHostError when host is Langfuse Cloud's US, HIPAA or Japan region.

    Any other address, a self-hosted one included, is allowed.
    """
    region = NON_EU_CLOUD_REGIONS.get(_hostname(host))
    if region is None:
        return
    raise RefusedHostError(
        f"Langfuse: {host} is Langfuse Cloud's {region} region. This plugin sends only to "
        f"the EU region or to your own Langfuse server, so this session is not traced and "
        f"nothing is sent. In this repo's .claude/settings.local.json, set LANGFUSE_BASE_URL "
        f"to {EU_CLOUD_HOST}, with keys from an EU project. {SETTINGS_REREAD}"
    )

class TraceUserError(Exception):
    """A source for the trace user exists but could not be read."""

def read_login_email() -> Optional[str]:
    """The Claude login email from CLAUDE_JSON_FILE; None when there is no file or no login.

    Raises TraceUserError when the file exists but cannot be read or parsed.
    """
    try:
        data = json.loads(CLAUDE_JSON_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise TraceUserError(f"cannot read the login email from {CLAUDE_JSON_FILE}: {e}") from e
    account = data.get("oauthAccount") if isinstance(data, dict) else None
    email = account.get("emailAddress") if isinstance(account, dict) else None
    return email if isinstance(email, str) and email else None

GIT_CONFIG_KEY_NOT_FOUND = 1

def read_git_email(cwd: Optional[str]) -> Optional[str]:
    """`git config user.email` as git sees it from cwd (None: the hook's own directory).

    None when the email is unset, git is not installed, or cwd does not exist.
    Raises TraceUserError, with git's error output, when git fails for another reason.
    """
    try:
        result = subprocess.run(
            ["git", "config", "user.email"], cwd=cwd, capture_output=True, text=True
        )
    except OSError as e:
        debug(f"git config user.email could not run: {type(e).__name__}: {e}")
        return None
    if result.returncode == GIT_CONFIG_KEY_NOT_FOUND:
        return None
    if result.returncode != 0:
        raise TraceUserError(
            f"`git config user.email` failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip() or None

def read_repo_name(checkout: Checkout) -> str:
    """The name of the repo of checkout.

    With a git remote: the last part of the address of `origin`, else of the first
    remote `git remote` lists, without a `.git` ending. Without a remote: the name of
    the main checkout's folder, the same for all its worktrees. Outside a git checkout,
    or without git installed: the name of checkout.root.
    Raises subprocess.CalledProcessError when git fails inside a checkout.
    """
    base = checkout.root
    main_checkout = checkout.main
    if main_checkout is None:
        return base.name

    def git(*args: str) -> str:
        # stderr is not captured, so git's own error reaches the hook's error output.
        return subprocess.run(
            ["git", *args], cwd=base, stdout=subprocess.PIPE, text=True, check=True
        ).stdout.strip()

    remotes = git("remote").split()
    if not remotes:
        return main_checkout.name
    remote = "origin" if "origin" in remotes else remotes[0]
    address = git("remote", "get-url", remote)
    # Covers both `host:owner/name` and URL forms; a user or token sits before the last part.
    last_part = address.rstrip("/").replace(":", "/").rsplit("/", 1)[-1]
    return last_part.removesuffix(".git")

class PluginsFileError(Exception):
    """INSTALLED_PLUGINS_FILE exists but cannot be read, or does not hold the shape Claude Code writes."""

def read_installed_plugins() -> Dict[str, List[Dict[str, Any]]]:
    """The installs of each plugin, keyed `<plugin>@<marketplace>`, from INSTALLED_PLUGINS_FILE.

    {} when the file does not exist. Raises PluginsFileError when it cannot be read or
    parsed, or when it holds no `plugins` object of install lists.
    """
    try:
        data = json.loads(INSTALLED_PLUGINS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise PluginsFileError(f"cannot read the installed plugins from {INSTALLED_PLUGINS_FILE}: {e}") from e
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        raise PluginsFileError(f"{INSTALLED_PLUGINS_FILE} holds no `plugins` object")
    for key, installs in plugins.items():
        if not isinstance(installs, list) or not all(isinstance(install, dict) for install in installs):
            raise PluginsFileError(f"{INSTALLED_PLUGINS_FILE}: the installs of {key} are not a list of objects")
    return plugins

# Claude Code uses a plugin's local setting over its project setting, and that over its user one.
INSTALL_SCOPES_MOST_SPECIFIC_FIRST = ("local", "project", "user")
REPO_INSTALL_SCOPES = ("local", "project")
VERSION_PATTERN = re.compile(r"[0-9.]+")  # digits and dots only, as 8.9.0

def plugin_version(raw: Any) -> Optional[str]:
    """An install's version as a trace shows it: cut at the first dash, as `8.9.0` from
    `8.9.0-0ae331d8a58b`; None when that part is not made of digits and dots only, such
    as a bare commit hash, `unknown` or no version at all."""
    if not isinstance(raw, str):
        return None
    version = raw.split("-", 1)[0]
    return version if VERSION_PATTERN.fullmatch(version) else None

def install_counts_for(install: Dict[str, Any], project_dir: Path) -> bool:
    """Whether an install applies to a session in project_dir: a user install always
    does, a local or project install only when it was made for project_dir."""
    scope = install.get("scope")
    if scope == "user":
        return True
    project_path = install.get("projectPath")
    return (
        scope in REPO_INSTALL_SCOPES
        and isinstance(project_path, str)
        and Path(project_path).resolve() == project_dir.resolve()
    )

def read_plugin_versions(project_dir: Path) -> Dict[str, Optional[str]]:
    """The version of each plugin installed for a session in project_dir, by plugin name.

    project_dir: the repo's main checkout, or the session folder outside git.
    The install that counts is the plugin's install for project_dir, local before
    project, else its user (machine-wide) install; its version is plugin_version().
    A plugin with no install that counts is left out.
    Fails as read_installed_plugins does.
    """
    versions: Dict[str, Optional[str]] = {}
    for key, installs in read_installed_plugins().items():
        counting = [install for install in installs if install_counts_for(install, project_dir)]
        if not counting:
            continue
        winner = min(counting, key=lambda install: INSTALL_SCOPES_MOST_SPECIFIC_FIRST.index(install["scope"]))
        versions[key.split("@", 1)[0]] = plugin_version(winner.get("version"))
    return versions

def get_langfuse_config(checkout: Checkout, cwd: Optional[str]) -> Optional[LangfuseConfig]:
    """The hook's Langfuse settings, or None when the repo's own settings file has no keys.

    checkout: the session's repo; cwd: the session's directory (None: the process's
    working directory), for the trace user's git email.
    The keys and the host come only from repo_settings_env(checkout), and fail as that does.
    The host is its LANGFUSE_BASE_URL or CC_LANGFUSE_BASE_URL, else EU_CLOUD_HOST.
    Raises RefusedHostError for a refused host, with or without keys.

    The trace user is, in order: LANGFUSE_USER_ID or CC_LANGFUSE_USER_ID (env var or
    plugin userConfig), the Claude login email, `git config user.email` in cwd, or None.
    When a source exists but cannot be read, the user is None and user_warning says why.
    The repo name is read_repo_name(checkout), and fails as that does. The plugin
    versions are read_plugin_versions() for the repo's main checkout, or for
    checkout.root outside git, and fail as that does. The work item branch pattern is
    CC_LANGFUSE_WORK_ITEM_BRANCH_PATTERN (env var or plugin userConfig); a refused
    pattern is logged and left unset.
    """
    env = repo_settings_env(checkout)
    host = env.get("LANGFUSE_BASE_URL") or env.get("CC_LANGFUSE_BASE_URL") or EU_CLOUD_HOST
    check_host_allowed(host)

    if not env_holds_keys(env):
        return None
    public_key, secret_key = read_env_keys(env)

    user_id = None
    user_warning = None
    try:
        user_id = (
            _opt("LANGFUSE_USER_ID")
            or _opt("CC_LANGFUSE_USER_ID")
            or read_login_email()
            or read_git_email(cwd)
        )
    except TraceUserError as e:
        user_warning = f"Langfuse: traces carry no user, because {e}"
        info(user_warning)
    trace_seed = _opt("CC_LANGFUSE_TRACE_SEED") or None

    return LangfuseConfig(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
        user_id=user_id,
        repo_name=read_repo_name(checkout),
        trace_seed=trace_seed,
        user_warning=user_warning,
        plugin_versions=read_plugin_versions(checkout.main or checkout.root),
        work_item_branch_pattern=read_work_item_branch_pattern(),
    )

def read_work_item_branch_pattern() -> Optional[re.Pattern]:
    text = _opt("CC_LANGFUSE_WORK_ITEM_BRANCH_PATTERN")
    if not text:
        return None
    try:
        return work_item.compile_branch_pattern(text)
    except ValueError as e:
        info(f"work item branch pattern refused: {text}: {e}")
        return None

class WaitingTurnExporter(SpanExporter):
    """Stores each batch of spans in WAITING_DIR for the background sender; sends nothing."""

    def __init__(self, config: LangfuseConfig) -> None:
        self._target = Target(config.host, config.public_key, config.secret_key)

    def export(self, spans: Any) -> Any:
        body = encode_spans(spans).SerializePartialToString()
        try:
            write_waiting_batch(WAITING_DIR, self._target, body)
        except OSError as e:
            info(f"could not store {len(spans)} span(s) to send: {type(e).__name__}: {e}")
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def create_langfuse_client(config: LangfuseConfig) -> Optional[Langfuse]:
    try:
        return Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            host=config.host,
            span_exporter=WaitingTurnExporter(config),
        )
    except Exception:
        return None


# ----------------- Logging -----------------
_logger: Optional[logging.Logger] = None

def _get_logger() -> Optional[logging.Logger]:
    global _logger
    if _logger is not None:
        return _logger
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        lg = logging.getLogger("langfuse_hook")
        lg.setLevel(logging.DEBUG if DEBUG else logging.INFO)
        if not lg.handlers:
            h = RotatingFileHandler(str(LOG_FILE), maxBytes=5_000_000, backupCount=3)
            h.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            lg.addHandler(h)
        _logger = lg
        return _logger
    except Exception:
        return None

def debug(msg: str) -> None:
    if not DEBUG:
        return
    lg = _get_logger()
    if lg is not None:
        try:
            lg.debug(msg)
        except Exception:
            pass

def info(msg: str) -> None:
    lg = _get_logger()
    if lg is not None:
        try:
            lg.info(msg)
        except Exception:
            pass


# ----------------- Hook payload -----------------
def read_hook_payload() -> Dict[str, Any]:
    """
    Claude Code hooks pass a JSON payload on stdin.
    This script tolerates missing/empty stdin by returning {}.
    """
    try:
        data = sys.stdin.read()
        debug(f"stdin received {len(data)} chars")
        if not data.strip():
            return {}
        parsed = json.loads(data)
        if isinstance(parsed, dict):
            debug(f"payload top-level keys: {sorted(parsed.keys())}")
            return parsed
        debug(f"payload is {type(parsed).__name__}, expected object; exiting.")
        return {}
    except Exception as e:
        debug(f"read_hook_payload exception: {e!r}")
        return {}

def extract_session_id_and_transcript_path(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[Path]]:
    """
    Tries a few plausible field names; exact keys can vary across hook types/versions.
    Prefer structured values from stdin over heuristics.
    """
    session_id = (
        payload.get("sessionId")
        or payload.get("session_id")
        or payload.get("session", {}).get("id")
    )

    transcript_path_raw = (
        payload.get("transcriptPath")
        or payload.get("transcript_path")
        or payload.get("transcript", {}).get("path")
    )

    if transcript_path_raw:
        try:
            transcript_path = Path(transcript_path_raw).expanduser().resolve()
        except Exception:
            transcript_path = None
    else:
        transcript_path = None

    return session_id, transcript_path

def get_session_id_and_transcript_path(payload: Dict[str, Any]) -> Optional[Tuple[str, Path]]:
    session_id, transcript_path = extract_session_id_and_transcript_path(payload)

    if not session_id or not transcript_path:
        # No structured payload; fail open (do not guess).
        debug("Missing session_id or transcript_path from hook payload; exiting.")
        return None

    if not transcript_path.exists():
        debug(f"Transcript path does not exist: {transcript_path}")
        return None

    return session_id, transcript_path

def hook_event_name(payload: Dict[str, Any]) -> Optional[str]:
    return payload.get("hook_event_name") or payload.get("hookEventName")

def is_session_end_hook_payload(payload: Dict[str, Any]) -> bool:
    return hook_event_name(payload) == "SessionEnd"

def is_session_start_hook_payload(payload: Dict[str, Any]) -> bool:
    return hook_event_name(payload) == "SessionStart"

def is_setup_check_hook_payload(payload: Dict[str, Any]) -> bool:
    """The setup skill's check: the UserPromptExpansion hook on its command, or the
    skill's own run of this hook with that event."""
    return hook_event_name(payload) == "UserPromptExpansion"


# ----------------- No-keys message -----------------
def read_no_keys_steps(checkout: Checkout) -> Optional[str]:
    """The manual steps for this system, or None on a system with no text.

    The repo's own texts are looked up at checkout.root.
    A text that exists but cannot be read gives a line naming the file instead.
    """
    name = NO_KEYS_STEPS_NAMES.get(sys.platform)
    if name is None:
        return None
    repo_text = checkout.root / REPO_NO_KEYS_STEPS_DIR / f"{name}.txt"
    path = repo_text if repo_text.exists() else NO_KEYS_STEPS_DIR / f"{name}.txt"
    try:
        return path.read_text(encoding="utf-8").rstrip("\n")
    except (OSError, UnicodeDecodeError) as e:
        return f"Langfuse: cannot read {path}: {e}"

def format_no_keys_message(checkout: Checkout) -> str:
    lines = [NO_KEYS_WARNING]
    if any(read_keys(_opt)):
        lines.append(OUTSIDE_KEYS_NOT_USED)
    lines.append(f"To add keys, run {SETUP_COMMAND}, or by hand:")
    steps = read_no_keys_steps(checkout)
    if steps is not None:
        lines.append(steps)
    return "\n".join(lines)

def format_broken_settings_message(error: SettingsFileError) -> str:
    return f"Langfuse: {error}. This session is not traced until the file is fixed. {SETTINGS_REREAD}"


# ----------------- State file reading and writing -----------------
def load_hook_state() -> Dict[str, Any]:
    try:
        return read_state(STATE_FILE)
    except Exception:
        return {}

@dataclass
class SessionState:
    offset: int = 0       # Last byte read from the transcript file.
    buffer: str = ""      # Partial JSONL line kept between hook runs.
    turn_count: int = 0   # Turns numbered so far in this session, sent or held back.
    # Trace id of the session's newest turn; a held-back turn's trace is not sent yet.
    latest_trace_id: Optional[str] = None
    pending_agent_turns: List[Dict[str, Any]] = field(default_factory=list)
    # Task-notification rows whose tool_use_id could not be resolved yet
    # (task-id-only and the subagent meta.json not on disk); retried each run.
    pending_task_notifications: List[Dict[str, Any]] = field(default_factory=list)

def get_session_state(global_state: Dict[str, Any], key: str) -> SessionState:
    s = global_state.get(key, {})
    pending_agent_turns = s.get("pending_agent_turns")
    if not isinstance(pending_agent_turns, list):
        pending_agent_turns = []
    pending_task_notifications = s.get("pending_task_notifications")
    if not isinstance(pending_task_notifications, list):
        pending_task_notifications = []
    latest_trace_id = s.get("latest_trace_id")
    return SessionState(
        offset=int(s.get("offset", 0)),
        buffer=str(s.get("buffer", "")),
        turn_count=int(s.get("turn_count", 0)),
        latest_trace_id=latest_trace_id if isinstance(latest_trace_id, str) else None,
        pending_agent_turns=pending_agent_turns,
        pending_task_notifications=pending_task_notifications,
    )

def update_session_state(global_state: Dict[str, Any], key: str, session_state: SessionState) -> None:
    global_state[key] = {
        "offset": session_state.offset,
        "buffer": session_state.buffer,
        "turn_count": session_state.turn_count,
        "latest_trace_id": session_state.latest_trace_id,
        "pending_agent_turns": session_state.pending_agent_turns or [],
        "pending_task_notifications": session_state.pending_task_notifications or [],
        "updated": datetime.now(timezone.utc).isoformat(),
    }


@dataclass
class TeammateProgress:
    """Per-teammate-transcript emission progress, stored in the same global state
    dict as SessionState (a separate entry keyed by the teammate's transcript
    path — same store, same lock, same 30-day pruning, not a parallel store)."""
    emitted_turns: int = 0        # Teammate turns already emitted.
    last_size: int = 0            # Transcript byte size seen at the previous firing.
    last_emitted_rowcount: int = 0  # Row count of the most-recently emitted turn.
    settled: bool = False         # Previous firing emitted every turn incl. the tail.

def get_teammate_progress(global_state: Dict[str, Any], key: str) -> TeammateProgress:
    s = global_state.get(key, {})
    if not isinstance(s, dict):
        s = {}
    return TeammateProgress(
        emitted_turns=int(s.get("emitted_turns", 0)),
        last_size=int(s.get("last_size", 0)),
        last_emitted_rowcount=int(s.get("last_emitted_rowcount", 0)),
        settled=bool(s.get("settled", False)),
    )

def update_teammate_progress(global_state: Dict[str, Any], key: str, progress: TeammateProgress) -> None:
    global_state[key] = {
        "emitted_turns": progress.emitted_turns,
        "last_size": progress.last_size,
        "last_emitted_rowcount": progress.last_emitted_rowcount,
        "settled": progress.settled,
        "updated": datetime.now(timezone.utc).isoformat(),
    }

# A run saves twice under the state lock, and both tries together must stay well
# under the 2 s another session waits for that lock.
STATE_SAVE_TIMEOUT_S = 0.5

def save_hook_state(state: Dict[str, Any]) -> None:
    try:
        # Drop old session entries to keep the file bounded.
        cutoff = datetime.now(timezone.utc) - STATE_MAX_AGE
        for k in list(state.keys()):
            entry = state.get(k)
            if not isinstance(entry, dict):
                continue
            updated = entry.get("updated")
            if not isinstance(updated, str):
                continue
            try:
                ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except Exception:
                continue
            if ts < cutoff:
                del state[k]
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        content = json.dumps(state, indent=2, sort_keys=True)

        def write_and_replace() -> bool:
            # PermissionError: another process, such as a virus scanner, has a file open.
            try:
                tmp.write_text(content, encoding="utf-8")
                os.replace(tmp, STATE_FILE)
            except PermissionError:
                return False
            return True

        if not retry_until(write_and_replace, STATE_SAVE_TIMEOUT_S):
            info(
                f"could not save {STATE_FILE}: it stayed in use for {STATE_SAVE_TIMEOUT_S}s; "
                "this run's turns will be sent again on the next run"
            )
    except Exception as e:
        debug(f"save_hook_state failed: {e}")


# ----------------- Transcript row parsing -----------------
def get_content_from_row(row: Dict[str, Any]) -> Any:
    if not isinstance(row, dict):
        return None
    message = row.get("message")
    if isinstance(message, dict):
        return message.get("content")
    return row.get("content")

def get_user_or_assistant_role_from_row(row: Dict[str, Any]) -> Optional[str]:
    # Claude Code transcript row format is internal. Prefer top-level row.type,
    # and fall back to nested message.role only for a row without one: a row of
    # another type, such as an api-request-blob copy of a request, carries a
    # message role but is no chat row.
    row_type = row.get("type")
    if row_type in ("user", "assistant"):
        return row_type
    if row_type is not None:
        return None

    message = row.get("message")
    if isinstance(message, dict):
        role = message.get("role")
        if role in ("user", "assistant"):
            return role
    return None

def get_message_id(row: Dict[str, Any]) -> Optional[str]:
    m = row.get("message")
    if isinstance(m, dict):
        mid = m.get("id")
        if isinstance(mid, str) and mid:
            return mid
    return None

def get_model(row: Dict[str, Any]) -> str:
    m = row.get("message")
    if isinstance(m, dict):
        return m.get("model") or "claude"
    return "claude"

def get_usage_details_from_row(row: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Extract Anthropic token usage from an assistant message, if present."""
    m = row.get("message")
    if not isinstance(m, dict):
        return None
    u = m.get("usage")
    if not isinstance(u, dict):
        return None
    details: Dict[str, int] = {}
    for src, dst in (
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("cache_read_input_tokens", "cache_read_input_tokens"),
        ("cache_creation_input_tokens", "cache_creation_input_tokens"),
    ):
        v = u.get(src)
        if isinstance(v, int) and v > 0:
            details[dst] = v
    return details or None

def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse a Claude Code jsonl row timestamp (ISO 8601 with trailing Z)."""
    if isinstance(value, dict):
        value = value.get("timestamp")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

def extract_text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for x in content:
            if isinstance(x, dict) and x.get("type") == "text":
                parts.append(x.get("text", ""))
            elif isinstance(x, str):
                parts.append(x)
        return "\n".join([p for p in parts if p])
    return ""

def truncate_text(s: str, max_chars: int = MAX_CHARS) -> Tuple[str, Dict[str, Any]]:
    if s is None:
        return "", {"truncated": False, "orig_len": 0}
    orig_len = len(s)
    if orig_len <= max_chars:
        return s, {"truncated": False, "orig_len": orig_len}
    head = s[:max_chars]
    return head, {"truncated": True, "orig_len": orig_len, "kept_len": len(head), "sha256": hashlib.sha256(s.encode("utf-8")).hexdigest()}

def mask_and_truncate(s: str) -> Tuple[str, Dict[str, Any]]:
    """The text of an observation's input or output: secrets masked, then cut to MAX_CHARS."""
    return truncate_text(mask_secrets(s))

def mask_and_truncate_tool_result(raw: Any) -> Tuple[str, Dict[str, Any]]:
    """A tool's output as text: secrets masked, a non-string written as JSON, then cut to MAX_CHARS."""
    masked = mask_secrets(raw)
    # The JSON text is not masked again: a password value would run on into its closing quote.
    text = masked if isinstance(masked, str) else json.dumps(masked, ensure_ascii=False)
    return truncate_text(text)

def get_tool_use_blocks(content: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(content, list):
        for x in content:
            if isinstance(x, dict) and x.get("type") == "tool_use":
                out.append(x)
    return out

def get_tool_result_blocks(content: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(content, list):
        for x in content:
            if isinstance(x, dict) and x.get("type") == "tool_result":
                out.append(x)
    return out

def is_tool_result(row: Dict[str, Any]) -> bool:
    role = get_user_or_assistant_role_from_row(row)
    if role != "user":
        return False
    content = get_content_from_row(row)
    if isinstance(content, list):
        return any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content)
    return False


# ----------------- Incremental transcript reading -----------------
def read_new_jsonl(transcript_path: Path, session_state: SessionState) -> Tuple[List[Dict[str, Any]], SessionState]:
    """
    Reads only new bytes since session_state.offset. Keeps session_state.buffer for partial last line.
    Returns parsed JSON lines and updated state.
    """
    if not transcript_path.exists():
        return [], session_state

    try:
        file_size = transcript_path.stat().st_size
        if file_size < session_state.offset:
            # Transcript was rotated or truncated — restart from the beginning.
            debug(f"transcript shrank ({file_size} < {session_state.offset}); restarting")
            session_state.offset = 0
            session_state.buffer = ""
        with open(transcript_path, "rb") as f:
            f.seek(session_state.offset)
            chunk = f.read()
            new_offset = f.tell()
    except Exception as e:
        debug(f"read_new_jsonl failed: {e}")
        return [], session_state

    if not chunk:
        return [], session_state

    try:
        text = chunk.decode("utf-8", errors="replace")
    except Exception:
        text = chunk.decode(errors="replace")

    combined = session_state.buffer + text
    lines = combined.split("\n")
    # last element may be incomplete
    session_state.buffer = lines[-1]
    session_state.offset = new_offset

    msgs: List[Dict[str, Any]] = []
    for line in lines[:-1]:
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except Exception:
            continue

    return msgs, session_state


# ----------------- Turn assembly -----------------
@dataclass
class Turn:
    user_msg: Dict[str, Any]
    assistant_msgs: List[Dict[str, Any]]
    tool_results_by_id: Dict[str, Any]
    tool_use_timestamps_by_id: Dict[str, Any]
    # Injected context (e.g. skill instructions) keyed by the tool_use id it
    # belongs to, taken from isMeta rows carrying sourceToolUseID.
    injected_by_tool_id: Dict[str, str]
    rows: List[Dict[str, Any]]
    # The turn's number in the session, given by number_new_turns.
    number: Optional[int] = None
    # The trace id a held-back turn is given before it is sent; once a turn is
    # sent, the id its trace got.
    trace_id: Optional[str] = None

@dataclass
class TurnAssemblyState:
    current_turn_user_row: Optional[Dict[str, Any]] = None
    assistant_message_ids: List[str] = field(default_factory=list)
    assistant_rows_by_message_id: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    tool_results_by_id: Dict[str, Any] = field(default_factory=dict)
    tool_use_timestamps_by_id: Dict[str, Any] = field(default_factory=dict)
    injected_by_tool_id: Dict[str, str] = field(default_factory=dict)
    current_rows: List[Dict[str, Any]] = field(default_factory=list)
    # Set at a turn-end row (is_turn_end_row); a later notification or hand-back then
    # starts a new turn instead of joining this one.
    turn_ended: bool = False
    # Each turn closed earlier in the batch, by each call it made, so a result that
    # arrives in a later turn still reaches the call's own turn.
    closed_turns_by_call_id: Dict[str, Turn] = field(default_factory=dict)


def _extract_xml_tag_value(text: str, tag: str) -> Optional[str]:
    start = f"<{tag}>"
    end = f"</{tag}>"
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i + len(start))
    if j < 0:
        return None
    return text[i + len(start):j]

def has_task_notification_origin(row: Dict[str, Any]) -> bool:
    origin = row.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "task-notification"

def is_task_notification_row(row: Dict[str, Any]) -> bool:
    if has_task_notification_origin(row):
        return True

    notification_text = extract_text_from_content(get_content_from_row(row)).lstrip()
    return notification_text.startswith("<task-notification>")

def get_tool_use_id_from_task_notification(row: Dict[str, Any]) -> Optional[str]:
    notification_text = extract_text_from_content(get_content_from_row(row))
    tool_use_id = _extract_xml_tag_value(notification_text, "tool-use-id")
    return tool_use_id.strip() if isinstance(tool_use_id, str) and tool_use_id.strip() else None

def get_task_id_from_task_notification(row: Dict[str, Any]) -> Optional[str]:
    notification_text = extract_text_from_content(get_content_from_row(row))
    task_id = _extract_xml_tag_value(notification_text, "task-id")
    return task_id.strip() if isinstance(task_id, str) and task_id.strip() else None

def get_tool_use_id_for_task_notification(
    row: Dict[str, Any],
    task_id_to_tool_use_id: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    if not is_task_notification_row(row):
        return None

    tool_use_id = get_tool_use_id_from_task_notification(row)
    if tool_use_id:
        return tool_use_id

    task_id = get_task_id_from_task_notification(row)
    if task_id and task_id_to_tool_use_id:
        return task_id_to_tool_use_id.get(task_id)
    return None

def get_result_from_task_notification(row: Dict[str, Any]) -> str:
    notification_text = extract_text_from_content(get_content_from_row(row))
    result = _extract_xml_tag_value(notification_text, "result")
    return result if result is not None else notification_text

def hand_back_agent_id(row: Dict[str, Any]) -> Optional[str]:
    """The id of the agent whose report the row hands back to the session, or None
    when the row is no hand-back."""
    origin = row.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "peer" or origin.get("handback") is not True:
        return None
    agent_id = origin.get("senderTaskId")
    return agent_id if isinstance(agent_id, str) and agent_id else None

@dataclass
class AgentCalls:
    """A launched agent's transcript, and each call that launched or continued it with
    the round that call started."""
    path: Any
    rounds_by_call_id: Dict[str, int]

def calls_ended_by_hand_back(row: Dict[str, Any], agent_calls: Dict[str, AgentCalls]) -> List[str]:
    """The calls whose round the hand-back row ends: the round holding the agent's last
    SubagentHandback call at or before the row, and every earlier round. Empty when the
    row is no hand-back, or its agent's transcript holds no such call."""
    agent_id = hand_back_agent_id(row)
    calls = agent_calls.get(agent_id) if agent_id else None
    hand_back_time = parse_timestamp(row)
    if calls is None or hand_back_time is None or not isinstance(calls.path, Path):
        return []
    agent_rows = read_jsonl_rows(calls.path) or []
    round_starts = agent_round_starts(agent_rows)
    ended_round = 0
    for index, agent_row in enumerate(agent_rows):
        handed_back = any(
            tool_use.get("name") == "SubagentHandback"
            for tool_use in get_tool_use_blocks(get_content_from_row(agent_row))
        )
        call_time = parse_timestamp(agent_row)
        if handed_back and call_time is not None and call_time <= hand_back_time:
            ended_round = sum(1 for start in round_starts if start <= index)
    return [call_id for call_id, round_number in calls.rounds_by_call_id.items() if round_number <= ended_round]

def _find_pending_agent_turn(
    session_state: SessionState,
    tool_use_id: str,
) -> Optional[Dict[str, Any]]:
    for pending_turn in session_state.pending_agent_turns:
        if not isinstance(pending_turn, dict):
            continue
        if not isinstance(pending_turn.get("rows"), list):
            continue
        pending_tool_use_ids = pending_turn.get("pending_tool_use_ids")
        resolved_tool_use_ids = pending_turn.get("resolved_tool_use_ids")
        # Notifications can arrive more than once per tool_use_id, so ids that
        # already received one keep matching until the whole turn resolves.
        if isinstance(pending_tool_use_ids, list) and tool_use_id in pending_tool_use_ids:
            return pending_turn
        if isinstance(resolved_tool_use_ids, list) and tool_use_id in resolved_tool_use_ids:
            return pending_turn
    return None

def resolve_deferred_agent_turns(
    rows: List[Dict[str, Any]],
    session_state: SessionState,
    task_id_to_tool_use_id: Optional[Dict[str, str]] = None,
    agent_calls: Optional[Dict[str, AgentCalls]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Copy each task-notification row of the batch to its deferred turn, and each
    hand-back row to every deferred turn that waits for a call whose round it ends
    (calls_ended_by_hand_back). Returns the deferred turns' entries that no longer
    wait, and the batch rows.

    agent_calls maps an agent's id to its AgentCalls.
    Deferred rows are never spliced into the batch (a user row mid-batch would
    cut the current turn in half); resolved turns are returned for isolated
    assembly. Notifications that cannot be attributed yet (task-id-only,
    subagent meta.json not on disk) are stashed in the session state and
    retried on later runs. Every notification and hand-back row also stays in
    the batch, which returns as the second value: there it gives its result to
    a call of the batch's own turns, or starts the session's reply to it.
    """
    remaining_rows: List[Dict[str, Any]] = []
    stashed_notifications: List[Dict[str, Any]] = []

    def route_to_pending_turn(pending_turn: Dict[str, Any], row: Dict[str, Any], tool_use_ids: List[str]) -> None:
        pending_turn["rows"].append(row)
        pending_tool_use_ids = pending_turn.get("pending_tool_use_ids")
        for tool_use_id in tool_use_ids:
            if isinstance(pending_tool_use_ids, list) and tool_use_id in pending_tool_use_ids:
                pending_tool_use_ids.remove(tool_use_id)
                pending_turn.setdefault("resolved_tool_use_ids", []).append(tool_use_id)

    def route_hand_back(row: Dict[str, Any]) -> None:
        ended_calls = calls_ended_by_hand_back(row, agent_calls or {})
        for pending_turn in session_state.pending_agent_turns:
            if not isinstance(pending_turn, dict) or not isinstance(pending_turn.get("rows"), list):
                continue
            pending_tool_use_ids = pending_turn.get("pending_tool_use_ids")
            if not isinstance(pending_tool_use_ids, list):
                continue
            waiting_calls = [tool_use_id for tool_use_id in ended_calls if tool_use_id in pending_tool_use_ids]
            if waiting_calls:
                route_to_pending_turn(pending_turn, row, waiting_calls)

    # Retry stashed notifications from earlier runs first (they are older than
    # anything in the batch); their task-id may resolve now.
    for row in session_state.pending_task_notifications:
        tool_use_id = get_tool_use_id_for_task_notification(row, task_id_to_tool_use_id)
        if tool_use_id is None:
            stashed_notifications.append(row)
            continue
        pending_turn = _find_pending_agent_turn(session_state, tool_use_id)
        if pending_turn is None:
            debug(f"Dropping stashed task notification for {tool_use_id}: no deferred turn waits for it")
            continue
        route_to_pending_turn(pending_turn, row, [tool_use_id])

    for row in rows:
        remaining_rows.append(row)
        if hand_back_agent_id(row) is not None:
            route_hand_back(row)
            continue
        if not is_task_notification_row(row):
            continue
        tool_use_id = get_tool_use_id_for_task_notification(row, task_id_to_tool_use_id)
        if tool_use_id is None:
            stashed_notifications.append(row)
            continue
        pending_turn = _find_pending_agent_turn(session_state, tool_use_id)
        if pending_turn is not None:
            route_to_pending_turn(pending_turn, row, [tool_use_id])

    session_state.pending_task_notifications = stashed_notifications[-MAX_PENDING_TASK_NOTIFICATIONS:]

    # Pop fully resolved turns in deferral (i.e. chronological) order.
    resolved_turns: List[Dict[str, Any]] = []
    still_pending: List[Dict[str, Any]] = []
    for pending_turn in session_state.pending_agent_turns:
        if not isinstance(pending_turn, dict) or not isinstance(pending_turn.get("rows"), list):
            continue
        if pending_turn.get("pending_tool_use_ids"):
            still_pending.append(pending_turn)
            continue
        resolved_turns.append(pending_turn)
    session_state.pending_agent_turns = still_pending

    return resolved_turns, remaining_rows

def pop_all_deferred_agent_turns(
    session_state: SessionState,
) -> List[Dict[str, Any]]:
    popped: List[Dict[str, Any]] = []
    for pending_turn in session_state.pending_agent_turns:
        if not isinstance(pending_turn, dict):
            continue
        rows = pending_turn.get("rows")
        if isinstance(rows, list) and rows:
            popped.append(pending_turn)
    session_state.pending_agent_turns = []
    return popped

def get_tool_result_text(tool_result_entry: Any) -> str:
    if not isinstance(tool_result_entry, dict):
        return ""
    tool_result_content = tool_result_entry.get("content")
    if isinstance(tool_result_content, str):
        return tool_result_content
    return json.dumps(tool_result_content, ensure_ascii=False)

def get_async_launch_flag_from_row(row: Dict[str, Any]) -> Optional[bool]:
    """Read the structured async marker Claude Code puts on tool_result rows.

    Returns None when the row carries no toolUseResult (older Claude Code
    versions), so callers can fall back to the launch-text heuristic.
    """
    tool_use_result = row.get("toolUseResult")
    if not isinstance(tool_use_result, dict):
        return None
    return tool_use_result.get("status") == "async_launched" or tool_use_result.get("isAsync") is True

LEGACY_USER_REJECTED_TOOL_USE_RESULT = "User rejected tool use"

def get_tool_denial_kind_from_row(row: Dict[str, Any]) -> Optional[str]:
    """Read the row-level toolDenialKind Claude Code writes for a denied tool call.

    Falls back to the legacy pre-toolDenialKind shape (Claude Code <=2.1.198):
    a plain toolUseResult string of "User rejected tool use" with no dedicated
    field. That legacy string only ever represents a user rejection — there is
    no legacy equivalent for a permission-rule block, so the fallback can only
    ever resolve to "user-rejected".
    """
    denial_kind = row.get("toolDenialKind")
    if denial_kind:
        return denial_kind
    if row.get("toolUseResult") == LEGACY_USER_REJECTED_TOOL_USE_RESULT:
        return "user-rejected"
    return None

def is_async_agent_launch_result(tool_result_entry: Any) -> bool:
    if not isinstance(tool_result_entry, dict):
        return False
    # Prefer the structured toolUseResult marker: launch-text matching also
    # fires on tool results that merely quote it (e.g. reading this file).
    is_async_launch = tool_result_entry.get("is_async_launch")
    if is_async_launch is not None:
        return bool(is_async_launch)
    tool_result_text = get_tool_result_text(tool_result_entry)
    return (
        "Async agent launched successfully" in tool_result_text
        or (
            "agentId:" in tool_result_text
            and "output_file:" in tool_result_text
            and "You will be notified automatically" in tool_result_text
        )
    )

def get_pending_agent_tool_use_ids(
    turn: Turn,
    continued_rounds: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """The ids of the turn's async agent launches and of its continued_rounds calls
    that have no final result yet."""
    tool_use_ids: List[str] = []
    for assistant_message in turn.assistant_msgs:
        for tool_use_block in get_tool_use_blocks(get_content_from_row(assistant_message)):
            tool_use_id = str(tool_use_block.get("id") or "")
            continues_an_agent = tool_use_id in (continued_rounds or {})
            if tool_use_block.get("name") not in ("Agent", "Task") and not continues_an_agent:
                continue
            if not tool_use_id:
                continue
            tool_result_entry = turn.tool_results_by_id.get(tool_use_id)
            if isinstance(tool_result_entry, dict) and tool_result_entry.get("final_content") is not None:
                continue
            # Defer only explicit async launches: sync agents also write a
            # subagent transcript but never notify, so deferring on transcript
            # existence would strand their turns.
            if continues_an_agent or is_async_agent_launch_result(tool_result_entry):
                tool_use_ids.append(tool_use_id)
    return tool_use_ids

def get_turns_to_emit(
    turns: List[Turn],
    session_state: SessionState,
    continued_rounds: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    flush_deferred_agent_turns: bool = False,
) -> List[Turn]:
    turns_to_emit: List[Turn] = []
    for turn in turns:
        pending_agent_tool_use_ids = get_pending_agent_tool_use_ids(turn, continued_rounds)
        if pending_agent_tool_use_ids:
            if flush_deferred_agent_turns:
                debug(f"Emitting async agent turn without task notification: {pending_agent_tool_use_ids}")
                turns_to_emit.append(turn)
                continue
            session_state.pending_agent_turns.append({
                "pending_tool_use_ids": pending_agent_tool_use_ids,
                "rows": turn.rows,
                "turn_number": turn.number,
                "trace_id": turn.trace_id,
            })
            debug(f"Deferred agent turn until task notification: {pending_agent_tool_use_ids}")
            continue
        turns_to_emit.append(turn)
    return turns_to_emit

def number_new_turns(
    turns: List[Turn],
    session_state: SessionState,
    continued_rounds: Optional[Dict[str, Dict[str, Any]]],
    trace_seed: Optional[str],
) -> List[Turn]:
    """Gives each turn that has no number yet the session's next turn number, in
    transcript order, and gives each of those that has an agent call still running
    its trace id now, so the turn is the session's latest before it is sent.

    Returns the turns that can go on; a turn whose seeded trace id cannot be
    derived is logged and left out, so it is never sent.
    """
    numbered: List[Turn] = []
    for turn in turns:
        if turn.number is None:
            session_state.turn_count += 1
            turn.number = session_state.turn_count
            if get_pending_agent_tool_use_ids(turn, continued_rounds):
                if trace_seed:
                    turn.trace_id = derive_turn_trace_id(trace_seed, turn.number)
                else:
                    turn.trace_id = Langfuse.create_trace_id()
                if turn.trace_id is None:
                    info(f"turn {turn.number} is not sent: no trace id could be derived from CC_LANGFUSE_TRACE_SEED")
                    continue
        numbered.append(turn)
    return numbered


def add_injected_context_row(row: Dict[str, Any], state: TurnAssemblyState) -> bool:
    # Injected user rows (slash-command expansions, caveats, skill instructions)
    # carry isMeta=true. They are not real prompts, so they must not start turns.
    if not row.get("isMeta"):
        return False

    # Skill invocations link their injected instructions to the originating
    # tool_use via sourceToolUseID; keep the text so emit can optionally attach
    # it to that tool span.
    source_tool_use_id = row.get("sourceToolUseID")
    if source_tool_use_id:
        text = extract_text_from_content(get_content_from_row(row))
        if text:
            state.injected_by_tool_id[str(source_tool_use_id)] = text
            state.current_rows.append(row)
    return True

def add_tool_result_row(row: Dict[str, Any], state: TurnAssemblyState) -> bool:
    # tool_result rows show up as role=user with content blocks of type tool_result.
    if not is_tool_result(row):
        return False

    state.current_rows.append(row)
    row_timestamp = row.get("timestamp")
    is_async_launch = get_async_launch_flag_from_row(row)
    # Permission denials (user-rejected or blocked by a permission rule) carry
    # a row-level toolDenialKind alongside the content block's is_error flag.
    tool_denial_kind = get_tool_denial_kind_from_row(row)
    for tool_result_block in get_tool_result_blocks(get_content_from_row(row)):
        tool_use_id = tool_result_block.get("tool_use_id")
        if tool_use_id:
            tool_result_entry: Dict[str, Any] = {
                "content": tool_result_block.get("content"),
                "timestamp": row_timestamp,
            }
            if tool_result_block.get("is_error"):
                tool_result_entry["is_error"] = True
            if tool_denial_kind:
                tool_result_entry["tool_denial_kind"] = tool_denial_kind
            if is_async_launch is not None:
                tool_result_entry["is_async_launch"] = is_async_launch
            state.tool_results_by_id[str(tool_use_id)] = tool_result_entry
    return True

def set_final_result(tool_result_entry: Dict[str, Any], content: Any, row: Dict[str, Any]) -> None:
    """Give a call's tool result the result its agent sent later, at row's time."""
    tool_result_entry["final_content"] = content
    tool_result_entry["final_timestamp"] = row.get("timestamp")

def add_hand_back_row(
    row: Dict[str, Any],
    state: TurnAssemblyState,
    agent_calls: Optional[Dict[str, AgentCalls]] = None,
) -> bool:
    """Take a hand-back row: each call whose round it ends (calls_ended_by_hand_back),
    made by the current turn or by a turn closed earlier in the batch, and that has no
    final result yet, gets the hand-back as its final result. The row joins the
    current turn's rows, and the rows of each closed turn it gave a result to.
    agent_calls maps an agent's id to its AgentCalls."""
    if hand_back_agent_id(row) is None:
        return False

    report = extract_text_from_content(get_content_from_row(row))
    closed_turns_given: Dict[int, Turn] = {}
    for tool_use_id in calls_ended_by_hand_back(row, agent_calls or {}):
        holder = holder_of_call(tool_use_id, state)
        tool_result_entry = holder.tool_results_by_id.get(tool_use_id)
        if isinstance(tool_result_entry, dict) and tool_result_entry.get("final_content") is None:
            set_final_result(tool_result_entry, report, row)
            tool_result_entry["final_from_hand_back"] = True
            if holder.closed_turn is not None:
                closed_turns_given[id(holder.closed_turn)] = holder.closed_turn
    for closed_turn in closed_turns_given.values():
        closed_turn.rows.append(row)
    state.current_rows.append(row)
    return True

@dataclass
class CallHolder:
    """The tool results and call times of the turn that holds a call's result, and
    that turn itself when it is a turn closed earlier in the batch (None for the open
    turn)."""
    tool_results_by_id: Dict[str, Any]
    tool_use_timestamps_by_id: Dict[str, Any]
    closed_turn: Optional[Turn]

def holder_of_call(call_id: str, state: TurnAssemblyState) -> CallHolder:
    """The turn that holds call_id's result: the turn closed earlier in the batch that
    made the call, else the open turn."""
    closed_turn = state.closed_turns_by_call_id.get(call_id)
    if closed_turn is not None:
        return CallHolder(closed_turn.tool_results_by_id, closed_turn.tool_use_timestamps_by_id, closed_turn)
    return CallHolder(state.tool_results_by_id, state.tool_use_timestamps_by_id, None)

def give_notification_result(row: Dict[str, Any], tool_use_id: str, holder: CallHolder) -> None:
    """Gives the notification row's result to the call tool_use_id in holder's turn.
    Does nothing when that turn did not make the call."""
    existing_result = holder.tool_results_by_id.get(tool_use_id)
    if isinstance(existing_result, dict):
        # A hand-back came first and ended the round; the notification after it
        # must not move the round's end or replace the agent's report.
        if not existing_result.get("final_from_hand_back"):
            set_final_result(existing_result, get_result_from_task_notification(row), row)
    elif tool_use_id in holder.tool_use_timestamps_by_id:
        holder.tool_results_by_id[tool_use_id] = {
            "content": get_result_from_task_notification(row),
            "timestamp": row.get("timestamp"),
        }

def add_task_notification_row(
    row: Dict[str, Any],
    state: TurnAssemblyState,
    task_id_to_tool_use_id: Optional[Dict[str, str]] = None,
) -> bool:
    """Take a task-notification row: the call it names gets its result when the current
    turn made that call. When a turn closed earlier in the batch made it, that turn
    gets the result and the row. The row also joins the current turn's rows. With no
    turn open, the row is taken and dropped."""
    if not is_task_notification_row(row):
        return False

    if state.current_turn_user_row is None:
        return True

    tool_use_id = get_tool_use_id_for_task_notification(row, task_id_to_tool_use_id)
    if not tool_use_id:
        state.current_rows.append(row)
        return True

    holder = holder_of_call(tool_use_id, state)
    give_notification_result(row, tool_use_id, holder)
    if holder.closed_turn is not None:
        holder.closed_turn.rows.append(row)
    state.current_rows.append(row)
    return True

def merge_assistant_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Claude Code can split one assistant message across multiple JSONL rows that
    share message.id. Merge them back into one logical message by concatenating
    content blocks in row order.
    """
    base: Dict[str, Any] = dict(rows[-1])
    last_message = rows[-1].get("message")
    merged_message: Dict[str, Any] = dict(last_message) if isinstance(last_message, dict) else {}

    merged_content: List[Any] = []
    for row in rows:
        message_obj = row.get("message")
        if not isinstance(message_obj, dict):
            continue

        content_blocks = message_obj.get("content")
        if isinstance(content_blocks, list):
            merged_content.extend(content_blocks)
        elif isinstance(content_blocks, str) and content_blocks:
            merged_content.append({"type": "text", "text": content_blocks})

    merged_message["content"] = merged_content
    base["message"] = merged_message
    return base

def build_turn_from_state(state: TurnAssemblyState) -> Optional[Turn]:
    if state.current_turn_user_row is None:
        return None
    if not state.assistant_rows_by_message_id:
        return None

    # Rebuild one assistant message per message.id, in the order the ids
    # first appeared. assistant_rows_by_message_id[message_id] holds all raw
    # rows that shared that id; merge_assistant_rows concatenates their content
    # blocks into one.
    merged_assistant_rows: List[Dict[str, Any]] = []
    for message_id in state.assistant_message_ids:
        rows_for_id = state.assistant_rows_by_message_id.get(message_id)
        if not rows_for_id:
            continue
        merged_assistant_rows.append(merge_assistant_rows(rows_for_id))

    return Turn(
        user_msg=state.current_turn_user_row,
        assistant_msgs=merged_assistant_rows,
        tool_results_by_id=dict(state.tool_results_by_id),
        tool_use_timestamps_by_id=dict(state.tool_use_timestamps_by_id),
        injected_by_tool_id=dict(state.injected_by_tool_id),
        rows=list(state.current_rows),
    )

def start_new_turn(row: Dict[str, Any], state: TurnAssemblyState) -> None:
    state.current_turn_user_row = row
    state.assistant_message_ids = []
    state.assistant_rows_by_message_id = {}
    state.tool_results_by_id = {}
    state.tool_use_timestamps_by_id = {}
    state.injected_by_tool_id = {}
    state.current_rows = [row]
    state.turn_ended = False


def add_assistant_row(row: Dict[str, Any], state: TurnAssemblyState) -> None:
    if state.current_turn_user_row is None:
        # Ignore assistant rows until we see a user message.
        return

    message_id = get_message_id(row) or f"noid:{len(state.assistant_message_ids)}"
    if message_id not in state.assistant_rows_by_message_id:
        state.assistant_message_ids.append(message_id)
        state.assistant_rows_by_message_id[message_id] = []
    state.assistant_rows_by_message_id[message_id].append(row)

    for tool_use_block in get_tool_use_blocks(get_content_from_row(row)):
        tool_use_id = tool_use_block.get("id")
        if tool_use_id:
            state.tool_use_timestamps_by_id.setdefault(str(tool_use_id), row.get("timestamp"))
    state.current_rows.append(row)


TURN_END_SUBTYPES = ("turn_duration", "stop_hook_summary")

def is_turn_end_row(row: Dict[str, Any]) -> bool:
    """True for the system row Claude Code writes when a turn of the session ends."""
    return row.get("type") == "system" and row.get("subtype") in TURN_END_SUBTYPES

def starts_turn_when_none_is_open(row: Dict[str, Any]) -> bool:
    """True for a row that starts a turn when no turn is open or the open one has
    ended: the user row of a task notification, or a hand-back row."""
    is_notification = row.get("type") == "user" and has_task_notification_origin(row)
    return is_notification or hand_back_agent_id(row) is not None

def close_and_start_turn(row: Dict[str, Any], state: TurnAssemblyState, turns: List[Turn]) -> None:
    """Add the open turn, if it can be built, to turns and to
    state.closed_turns_by_call_id, and start a new turn at row."""
    turn = build_turn_from_state(state)
    if turn is not None:
        turns.append(turn)
        for call_id in turn.tool_use_timestamps_by_id:
            state.closed_turns_by_call_id[call_id] = turn
    start_new_turn(row, state)


def build_turns(
    rows: List[Dict[str, Any]],
    task_id_to_tool_use_id: Optional[Dict[str, str]] = None,
    agent_calls: Optional[Dict[str, AgentCalls]] = None,
) -> List[Turn]:
    """
    Groups incremental transcript rows into turns:
    user (non-tool-result) -> assistant messages -> (tool_result rows, possibly interleaved)
    A task notification or hand-back also starts a turn when no turn is open or the
    open one has ended (starts_turn_when_none_is_open); the open turn first gets its
    result.
    Uses:
    - assistant rows merged by message.id (all content blocks concatenated)
    - tool results dedupe by tool_use_id (latest wins)
    agent_calls maps an agent's id to its AgentCalls, for its hand-back rows.
    """
    turns: List[Turn] = []
    state = TurnAssemblyState()

    for row in rows:
        # A turn-end row may be isMeta, so it goes before the injected-context check.
        if is_turn_end_row(row):
            state.turn_ended = True
            continue

        turn_is_open = state.current_turn_user_row is not None
        if starts_turn_when_none_is_open(row) and (not turn_is_open or state.turn_ended):
            if turn_is_open and not add_hand_back_row(row, state, agent_calls):
                add_task_notification_row(row, state, task_id_to_tool_use_id)
            close_and_start_turn(row, state, turns)
            continue

        # A hand-back row is also isMeta, so it goes before the injected-context check.
        if add_hand_back_row(row, state, agent_calls):
            continue

        if add_injected_context_row(row, state):
            continue

        if add_tool_result_row(row, state):
            continue

        if add_task_notification_row(row, state, task_id_to_tool_use_id):
            continue

        role = get_user_or_assistant_role_from_row(row)

        if role == "user":
            close_and_start_turn(row, state, turns)
            continue

        if role == "assistant":
            add_assistant_row(row, state)
            continue

        # ignore unknown rows

    turn = build_turn_from_state(state)
    if turn is not None:
        turns.append(turn)
    return turns


def get_new_turns_from_transcript(
    transcript_path: Path,
    session_state: SessionState,
    subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]] = None,
    continued_rounds: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    flush_deferred_agent_turns: bool = False,
) -> Tuple[List[Turn], SessionState]:
    rows, session_state = read_new_jsonl(transcript_path, session_state)
    task_id_to_tool_use_id = get_task_id_to_tool_use_id(subagent_transcripts_by_tool_use_id)
    agent_calls = get_agent_calls(subagent_transcripts_by_tool_use_id or {}, continued_rounds or {})

    deferred_turns, rows = resolve_deferred_agent_turns(
        rows, session_state, task_id_to_tool_use_id, agent_calls
    )

    if flush_deferred_agent_turns and session_state.pending_agent_turns:
        flushed_turns = pop_all_deferred_agent_turns(session_state)
        if flushed_turns:
            debug(f"Flushing {len(flushed_turns)} deferred agent turn(s) without task notification")
            deferred_turns = deferred_turns + flushed_turns

    if flush_deferred_agent_turns and session_state.pending_task_notifications:
        debug(f"Dropping {len(session_state.pending_task_notifications)} unresolved task notification(s) at session end")
        session_state.pending_task_notifications = []

    # Each deferred row list is a complete turn from an earlier hook run, so
    # it is rebuilt in isolation and emitted before the current batch (its
    # rows are always chronologically older than anything in the batch).
    turns: List[Turn] = []
    for deferred_turn in deferred_turns:
        rebuilt = build_turns(deferred_turn["rows"], task_id_to_tool_use_id, agent_calls)
        if rebuilt:
            rebuilt[0].number = deferred_turn.get("turn_number")
            rebuilt[0].trace_id = deferred_turn.get("trace_id")
        turns.extend(rebuilt)
    if rows:
        turns.extend(build_turns(rows, task_id_to_tool_use_id, agent_calls))

    return turns, session_state

def get_subagent_transcripts_by_tool_use_id(transcript_path: Path) -> Dict[str, Dict[str, Any]]:
    """Map launching Agent/Task tool_use ids to their subagent transcripts."""
    subagent_dir = transcript_path.with_suffix("") / "subagents"
    if not subagent_dir.is_dir():
        return {}

    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]] = {}
    for meta_path in subagent_dir.glob("*.meta.json"):
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        tool_use_id = metadata.get("toolUseId")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            # Named-teammate metas (agent teams) carry no toolUseId and cannot be
            # keyed here; they are discovered and emitted by
            # discover_teammate_transcripts / emit_teammate_transcripts instead,
            # so this skip drops nothing — it is not a silent data loss.
            continue

        jsonl_path = meta_path.with_name(meta_path.name[: -len(".meta.json")] + ".jsonl")
        if not jsonl_path.exists():
            continue

        agent_id = meta_path.name[: -len(".meta.json")]
        if agent_id.startswith("agent-"):
            agent_id = agent_id[len("agent-"):]

        subagent_transcripts_by_tool_use_id[tool_use_id] = {
            "path": jsonl_path,
            "agent_id": agent_id,
            "agent_type": metadata.get("agentType"),
            "description": metadata.get("description"),
        }
    return subagent_transcripts_by_tool_use_id

def get_task_id_to_tool_use_id(
    subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]],
) -> Dict[str, str]:
    task_id_to_tool_use_id: Dict[str, str] = {}
    if not subagent_transcripts_by_tool_use_id:
        return task_id_to_tool_use_id

    for tool_use_id, subagent in subagent_transcripts_by_tool_use_id.items():
        agent_id = subagent.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            task_id_to_tool_use_id[agent_id] = tool_use_id
    return task_id_to_tool_use_id


# ----------------- Rounds of a launched agent -----------------
# A round is an agent's work for one message: its launch prompt, or one message
# the main session sent it with SendMessage. All rounds go into the agent's one
# transcript, so each round's run reads only its own part of that file.

def is_coordinator_origin(origin: Any) -> bool:
    return isinstance(origin, dict) and origin.get("kind") == "coordinator"

def is_later_round_start_row(row: Dict[str, Any]) -> bool:
    """Whether a message from the main session starts a round at this agent transcript
    row: a user row when the agent had finished, a queued_command attachment when it
    was still busy."""
    if row.get("type") == "user":
        return is_coordinator_origin(row.get("origin"))
    attachment = row.get("attachment")
    return (
        row.get("type") == "attachment"
        and isinstance(attachment, dict)
        and attachment.get("type") == "queued_command"
        and is_coordinator_origin(attachment.get("origin"))
    )

def agent_round_starts(rows: List[Dict[str, Any]]) -> List[int]:
    """The position in rows where each round starts; round 1 starts at the first row."""
    if not rows:
        return []
    return [0] + [index for index in range(1, len(rows)) if is_later_round_start_row(rows[index])]

def round_prompt_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """A later round's start row as a prompt row that starts a turn."""
    attachment = row.get("attachment")
    if isinstance(attachment, dict):
        return {
            "type": "user",
            "timestamp": row.get("timestamp"),
            "message": {"role": "user", "content": attachment.get("prompt")},
        }
    prompt = dict(row)
    prompt.pop("isMeta", None)
    return prompt

def agent_round_rows(rows: List[Dict[str, Any]], round_number: int) -> Optional[List[Dict[str, Any]]]:
    """The rows of round round_number (from 1) of an agent transcript's rows, up to the
    next round's start; None when that round has not started in rows."""
    starts = agent_round_starts(rows)
    if round_number < 1 or round_number > len(starts):
        return None
    start = starts[round_number - 1]
    end = starts[round_number] if round_number < len(starts) else len(rows)
    first_row = rows[start] if round_number == 1 else round_prompt_row(rows[start])
    return [first_row] + rows[start + 1:end]

def sent_to_agent_id(row: Dict[str, Any]) -> Optional[str]:
    """The agent id a successful SendMessage result row names in pin.id, or None."""
    tool_use_result = row.get("toolUseResult")
    if not isinstance(tool_use_result, dict) or tool_use_result.get("success") is not True:
        return None
    pin = tool_use_result.get("pin")
    agent_id = pin.get("id") if isinstance(pin, dict) else None
    return agent_id if isinstance(agent_id, str) and agent_id else None

def get_continued_rounds_by_tool_use_id(
    transcript_path: Path,
    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Map each SendMessage call in the main transcript that reached a launched agent
    to {"subagent": that agent's entry of subagent_transcripts_by_tool_use_id, "round":
    the round it started}. The k-th such call to an agent starts its round k+1.

    Reads the whole main transcript; returns {} when no agent was launched or the
    transcript cannot be read."""
    agents_by_id = {
        subagent["agent_id"]: subagent
        for subagent in subagent_transcripts_by_tool_use_id.values()
        if isinstance(subagent.get("agent_id"), str)
    }
    if not agents_by_id:
        return {}

    send_message_call_ids: List[str] = []
    agent_id_by_call_id: Dict[str, str] = {}
    for row in read_jsonl_rows(transcript_path) or []:
        for tool_use in get_tool_use_blocks(get_content_from_row(row)):
            if tool_use.get("name") == "SendMessage" and tool_use.get("id"):
                send_message_call_ids.append(str(tool_use["id"]))
        agent_id = sent_to_agent_id(row)
        if agent_id not in agents_by_id:
            continue
        for tool_result in get_tool_result_blocks(get_content_from_row(row)):
            if tool_result.get("tool_use_id"):
                agent_id_by_call_id[str(tool_result["tool_use_id"])] = agent_id

    continued_rounds: Dict[str, Dict[str, Any]] = {}
    last_round_by_agent_id: Dict[str, int] = {}
    for call_id in dict.fromkeys(send_message_call_ids):
        agent_id = agent_id_by_call_id.get(call_id)
        if agent_id is None:
            continue
        last_round_by_agent_id[agent_id] = last_round_by_agent_id.get(agent_id, 1) + 1
        continued_rounds[call_id] = {"subagent": agents_by_id[agent_id], "round": last_round_by_agent_id[agent_id]}
    return continued_rounds

def get_agent_calls(
    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
    continued_rounds: Dict[str, Dict[str, Any]],
) -> Dict[str, AgentCalls]:
    """Map each launched agent's id to its AgentCalls: the call that launched it
    (round 1) and the calls in continued_rounds that continued it."""
    agent_calls: Dict[str, AgentCalls] = {}
    launches = [(tool_use_id, subagent, 1) for tool_use_id, subagent in subagent_transcripts_by_tool_use_id.items()]
    continuations = [
        (tool_use_id, continued["subagent"], continued["round"]) for tool_use_id, continued in continued_rounds.items()
    ]
    for tool_use_id, subagent, round_number in launches + continuations:
        agent_id = subagent.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            calls = agent_calls.setdefault(agent_id, AgentCalls(subagent.get("path"), {}))
            calls.rounds_by_call_id[tool_use_id] = round_number
    return agent_calls

@dataclass
class AgentRun:
    """One round of an agent: its entry of subagent_transcripts_by_tool_use_id, its
    number from 1, and its rows of the agent transcript."""
    subagent: Dict[str, Any]
    round_number: int
    rows: List[Dict[str, Any]]

def agent_run_of_call(
    tool_use_id: str,
    call_timestamp: Optional[datetime],
    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
    continued_rounds: Dict[str, Dict[str, Any]],
) -> Optional[AgentRun]:
    """The round the call launched (round 1) or continued, read from the agent's
    transcript now. None when the call starts no round, the transcript cannot be read,
    or the round has not started; for a continued round, also when its start row is
    older than call_timestamp, as then it is not this call's round."""
    launched = subagent_transcripts_by_tool_use_id.get(tool_use_id)
    continued = continued_rounds.get(tool_use_id)
    if launched is not None:
        subagent, round_number = launched, 1
    elif continued is not None:
        subagent, round_number = continued["subagent"], continued["round"]
    else:
        return None

    path = subagent.get("path")
    rows = read_jsonl_rows(path) if isinstance(path, Path) else None
    round_rows = agent_round_rows(rows, round_number) if rows else None
    if round_rows is None:
        return None
    if round_number > 1:
        round_start = parse_timestamp(round_rows[0])
        if round_start is None or call_timestamp is None or round_start < call_timestamp:
            return None
    return AgentRun(subagent, round_number, round_rows)

def agent_runs_in_rows(
    rows: List[Dict[str, Any]],
    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
    continued_rounds: Dict[str, Dict[str, Any]],
) -> List[AgentRun]:
    """The agent_run_of_call of each tool call in the rows that has one."""
    runs: List[AgentRun] = []
    for row in rows:
        for tool_use in get_tool_use_blocks(get_content_from_row(row)):
            run = agent_run_of_call(
                str(tool_use.get("id") or ""), parse_timestamp(row),
                subagent_transcripts_by_tool_use_id, continued_rounds,
            )
            if run is not None:
                runs.append(run)
    return runs


# ----------------- Named teammate (agent-team) discovery -----------------
# Claude Code's agent-teams feature spawns long-lived "teammate" subagents whose
# meta.json carries taskKind "in_process_teammate" and a `name`, but NO toolUseId.
# The classic toolUseId->transcript map therefore never sees them, and their
# transcripts — which grow across many parent turns as SendMessage resumes them —
# would be emitted nowhere. We discover them by name, link them back to their
# launching Agent tool_use, and emit their turns as their own top-level traces.
#
# Note on deferral: we deliberately do NOT route teammate completion rows
# (<teammate-message> / <agent-message>) through the async-launch deferral
# machinery (is_task_notification_row / pending_agent_turns). Deferral exists to
# hold a launching turn open until its single subagent result can be nested into
# the launching tool span. Teammates are a parallel, long-lived conversation
# captured directly from their own transcript here, so whatever the parent's
# launching turn does — emit immediately or defer to SessionEnd, depending on
# whether its tool result matches is_async_agent_launch_result — is orthogonal:
# the teammate's work is emitted as independent traces either way, and teammate
# messages need not be treated as task notifications.
TEAMMATE_TASK_KIND = "in_process_teammate"


def get_teammate_name_to_tool_use_id(transcript_path: Path) -> Tuple[Dict[str, str], set]:
    """Map a teammate `name` to the Agent tool_use id that launched it.

    Named teammates are spawned by an Agent tool_use whose input.name equals the
    teammate meta's `name` (the tool result corroborates via toolUseResult.name /
    an "agent_id: <name>@..." line). Scans the parent transcript in file order and
    keeps the first launch per name; names launched more than once are returned as
    ambiguous so the caller can flag the linkage deterministically.
    """
    name_to_tool_use_id: Dict[str, str] = {}
    ambiguous_names: set = set()
    try:
        lines = transcript_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return name_to_tool_use_id, ambiguous_names

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        for tool_use in get_tool_use_blocks(get_content_from_row(row)):
            if tool_use.get("name") not in ("Agent", "Task"):
                continue
            tool_input = tool_use.get("input")
            name = tool_input.get("name") if isinstance(tool_input, dict) else None
            tool_use_id = tool_use.get("id")
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            if name in name_to_tool_use_id:
                # Same name launched more than once: linkage is ambiguous. Keep
                # the first (earliest) launch deterministically and flag it.
                ambiguous_names.add(name)
                continue
            name_to_tool_use_id[name] = tool_use_id
    return name_to_tool_use_id, ambiguous_names


def discover_teammate_transcripts(transcript_path: Path) -> List[Dict[str, Any]]:
    """Discover named-teammate subagent transcripts (metas without a toolUseId).

    Mirrors get_subagent_transcripts_by_tool_use_id but for the teammate meta
    shape: no toolUseId, taskKind == "in_process_teammate" (or at least a `name`).
    Returns one entry per teammate with its transcript path and identifying
    metadata, in a stable (filename-sorted) order.
    """
    subagent_dir = transcript_path.with_suffix("") / "subagents"
    if not subagent_dir.is_dir():
        return []

    teammates: List[Dict[str, Any]] = []
    for meta_path in sorted(subagent_dir.glob("*.meta.json")):
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(metadata, dict):
            continue

        # Classic subagents are keyed by toolUseId elsewhere; only metas WITHOUT
        # one and shaped like a teammate are handled here.
        tool_use_id = metadata.get("toolUseId")
        if isinstance(tool_use_id, str) and tool_use_id:
            continue
        name = metadata.get("name")
        is_teammate = metadata.get("taskKind") == TEAMMATE_TASK_KIND or (
            isinstance(name, str) and name
        )
        if not is_teammate:
            # A meta with no toolUseId and no teammate shape fits neither
            # discovery path; surface it loudly rather than dropping it silently.
            info(
                f"Subagent meta with no toolUseId and no teammate shape "
                f"(name/taskKind): {meta_path.name}; not emitted"
            )
            continue

        jsonl_path = meta_path.with_name(meta_path.name[: -len(".meta.json")] + ".jsonl")
        if not jsonl_path.exists():
            continue

        agent_id = meta_path.name[: -len(".meta.json")]
        if agent_id.startswith("agent-"):
            agent_id = agent_id[len("agent-"):]

        teammates.append({
            "path": jsonl_path,
            "name": name if isinstance(name, str) and name else agent_id,
            "agent_id": agent_id,
            "agent_type": metadata.get("agentType"),
            "description": metadata.get("description"),
            "team_name": metadata.get("teamName"),
        })
    return teammates


# ----------------- Langfuse emit -----------------

# ---- Low-level Langfuse helpers ----
def to_otel_nanoseconds(ts: Optional[datetime]) -> Optional[int]:
    """Convert a datetime to OTel-style nanoseconds since epoch."""
    if ts is None:
        return None
    return int(ts.timestamp() * 1_000_000_000)

def _get_latest_timestamp(*timestamps: Optional[datetime]) -> Optional[datetime]:
    present_timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    return max(present_timestamps) if present_timestamps else None

# ---- Deterministic trace ids ----
def _is_valid_trace_id_hex(trace_id: Any) -> bool:
    if not isinstance(trace_id, str) or len(trace_id) != 32:
        return False
    try:
        return int(trace_id, 16) != 0
    except ValueError:
        return False

def derive_turn_trace_id(trace_seed: str, turn_number: int) -> Optional[str]:
    """Derive the deterministic W3C trace id for a turn from CC_LANGFUSE_TRACE_SEED.

    Formula: Langfuse.create_trace_id(seed=f"{trace_seed}:{turn_number}") — which
    is sha256(seed_string).hexdigest()[:32]. The explicit sha256 fallback keeps
    IDs identical to what external callers precompute with the SDK helper even
    if the helper itself is unavailable.
    """
    seed_string = f"{trace_seed}:{turn_number}"
    try:
        create_trace_id = getattr(Langfuse, "create_trace_id", None)
        if callable(create_trace_id):
            trace_id = create_trace_id(seed=seed_string)
            if _is_valid_trace_id_hex(trace_id):
                return trace_id.lower()
    except Exception as e:
        debug(f"Langfuse.create_trace_id failed for {seed_string!r}: {e}")
    try:
        return hashlib.sha256(seed_string.encode("utf-8")).hexdigest()[:32]
    except Exception:
        return None

def _build_forced_trace_context(trace_id_hex: str) -> Optional[Any]:
    """Build an OTel context whose remote parent carries the forced trace id.

    A root span started within this context adopts the trace id; its children
    keep inheriting it as usual. Returns None (caller falls back to an
    auto-generated id) when the context cannot be built.
    """
    try:
        trace_id = int(trace_id_hex, 16)
        if trace_id == 0:
            return None
        span_id = 0
        while span_id == 0:
            span_id = random.getrandbits(64)
        parent_span_context = otel_trace_api.SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=True,
            trace_flags=otel_trace_api.TraceFlags(otel_trace_api.TraceFlags.SAMPLED),
        )
        return otel_trace_api.set_span_in_context(
            otel_trace_api.NonRecordingSpan(parent_span_context)
        )
    except Exception as e:
        debug(f"forced trace context for {trace_id_hex!r} failed: {e}")
        return None

def _langfuse_sdk_version() -> str:
    try:
        return getattr(__import__("langfuse"), "__version__", "unknown")
    except Exception:
        return "unknown"

def _as_root_attribute() -> str:
    """The span attribute that makes Langfuse treat a span with a remote parent as
    its trace's root. Raises RuntimeError when the installed SDK does not have it."""
    try:
        from langfuse._client.attributes import LangfuseOtelSpanAttributes
        return LangfuseOtelSpanAttributes.AS_ROOT
    except (ImportError, AttributeError) as e:
        raise RuntimeError(
            f"Langfuse SDK {_langfuse_sdk_version()} has no "
            f"langfuse._client.attributes.LangfuseOtelSpanAttributes.AS_ROOT ({e}). This hook "
            f"targets SDK 4.x; pin with `pip install \"langfuse>=4.2,<5\"` or update the hook script."
        ) from e

def _start_root_otel_span(langfuse: Langfuse, name: str, start_ns: Optional[int],
                          forced_trace_id: Optional[str]) -> Any:
    if forced_trace_id:
        context = _build_forced_trace_context(forced_trace_id)
        if context is not None:
            as_root = _as_root_attribute()
            try:
                otel_span = langfuse._otel_tracer.start_span(name=name, start_time=start_ns, context=context)
                # Without it Langfuse treats the span as a child of the remote parent,
                # and the trace shows no input and output of its own.
                otel_span.set_attribute(as_root, True)
                return otel_span
            except Exception as e:
                debug(f"start_span with forced trace id {forced_trace_id!r} failed: {e}")
    return langfuse._otel_tracer.start_span(name=name, start_time=start_ns)

def _start_backdated(langfuse: Langfuse, *, name: str, as_type: str,
                     start_time: Optional[datetime],
                     parent_otel_span: Any = None,
                     forced_trace_id: Optional[str] = None,
                     **obs_kwargs: Any) -> Any:
    """Create a Langfuse observation with an explicit OTel start_time.

    Bypasses langfuse.start_observation() (which has no start_time kwarg in
    SDK 4.x) by talking to the underlying OTel tracer directly and then
    wrapping the resulting span with the Langfuse observation type.

    Depends on SDK 4.x internals: langfuse._otel_tracer and
    langfuse._create_observation_from_otel_span. If a future SDK version
    renames or removes these, raise a clear error instead of letting an
    AttributeError get swallowed by the broad emit_turn handler.
    """
    if not hasattr(langfuse, "_otel_tracer") or not hasattr(langfuse, "_create_observation_from_otel_span"):
        raise RuntimeError(
            f"Langfuse SDK {_langfuse_sdk_version()} is missing _otel_tracer or "
            f"_create_observation_from_otel_span. This hook targets SDK 4.x; "
            f"pin with `pip install \"langfuse>=4.2,<5\"` or update the hook script."
        )
    start_ns = to_otel_nanoseconds(start_time)
    if parent_otel_span is not None:
        with otel_trace_api.use_span(parent_otel_span, end_on_exit=False):
            otel_span = langfuse._otel_tracer.start_span(name=name, start_time=start_ns)
    else:
        otel_span = _start_root_otel_span(langfuse, name, start_ns, forced_trace_id)
    return langfuse._create_observation_from_otel_span(
        otel_span=otel_span,
        as_type=as_type,
        **obs_kwargs,
    )

# ---- Trace naming and tags ----
def skills_in_rows(rows: List[Dict[str, Any]]) -> List[str]:
    """The names of the skills the rows ran, each once, in the order first seen.

    Skills leave two different transcript trails: a tool_use block named
    "Skill" when Claude invokes the skill itself, and a top-level
    attributionSkill field on assistant rows when the user invokes it as a
    slash command (which never produces a Skill tool_use block).
    """
    skills: List[str] = []

    def add_skill(skill: Any) -> None:
        if isinstance(skill, str) and skill and skill not in skills:
            skills.append(skill)

    for row in rows:
        if not isinstance(row, dict):
            continue
        add_skill(row.get("attributionSkill"))
        for tool_use in get_tool_use_blocks(get_content_from_row(row)):
            if tool_use.get("name") != "Skill":
                continue
            tool_input = tool_use.get("input")
            add_skill(tool_input.get("skill") if isinstance(tool_input, dict) else None)
    return skills


def add_skill_tags_from_rows(rows: List[Dict[str, Any]], names: List[str], prefix: str) -> None:
    """Add a '<prefix><name>' tag to names for every skill the rows ran, each once."""
    for skill in skills_in_rows(rows):
        tag = f"{prefix}{skill}"
        if tag not in names:
            names.append(tag)


def collect_skill_tags(turn: Turn) -> List[str]:
    """Return 'skill:<name>' tags for every skill used in the turn itself."""
    names: List[str] = []
    add_skill_tags_from_rows(turn.assistant_msgs, names, "skill:")
    return names


def collect_subagent_skill_tags(
    turn: Turn,
    subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]] = None,
    continued_rounds: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """Return 'subagent-skill:<name>' tags for skills used inside the agent
    rounds this turn launched or continued.

    Kept in a separate tag namespace so 'skill:' keeps meaning "ran in the
    main conversation" and both dimensions stay filterable independently.
    """
    if not subagent_transcripts_by_tool_use_id:
        return []
    names: List[str] = []
    for run in agent_runs_in_rows(turn.assistant_msgs, subagent_transcripts_by_tool_use_id, continued_rounds or {}):
        add_skill_tags_from_rows(run.rows, names, "subagent-skill:")
    return names


# ---- Plugins a trace used ----
PLUGINS_METADATA_KEY = "plugins"

def plugin_of(component: Any) -> Optional[str]:
    """The plugin an agent, skill or command name comes from, as `mate-core` from
    `mate-core:build`; None for a name without a plugin, such as `general-purpose`."""
    if not isinstance(component, str):
        return None
    plugin, separator, _ = component.partition(":")
    return plugin if separator and plugin else None

def plugins_in_rows(rows: List[Dict[str, Any]]) -> List[Any]:
    """The plugins the rows name: each row's attributionPlugin, the plugin of each skill
    the rows ran, and the plugin of each agent an Agent or Task call in the rows asked
    for. Entries can be None or repeat."""
    plugins: List[Any] = [row.get("attributionPlugin") for row in rows if isinstance(row, dict)]
    plugins += [plugin_of(skill) for skill in skills_in_rows(rows)]
    for row in rows:
        for tool_use in get_tool_use_blocks(get_content_from_row(row)):
            tool_input = tool_use.get("input")
            if tool_use.get("name") in ("Agent", "Task") and isinstance(tool_input, dict):
                plugins.append(plugin_of(tool_input.get("subagent_type")))
    return plugins

def plugins_in_launched_agents(
    rows: List[Dict[str, Any]],
    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
    continued_rounds: Dict[str, Dict[str, Any]],
) -> List[Any]:
    """The plugins_in_rows of each agent round that tool calls in the rows launched or
    continued, and of the agents those rounds launched, at any depth. Entries can be
    None or repeat."""
    plugins: List[Any] = []
    pending = agent_runs_in_rows(rows, subagent_transcripts_by_tool_use_id, continued_rounds)
    seen_rounds = set()
    while pending:
        run = pending.pop()
        round_key = (run.subagent.get("path"), run.round_number)
        # A fork's transcript repeats the row with the call that launched it.
        if round_key in seen_rounds:
            continue
        seen_rounds.add(round_key)
        plugins += plugins_in_rows(run.rows)
        pending += agent_runs_in_rows(run.rows, subagent_transcripts_by_tool_use_id, {})
    return plugins

def plugins_used_in_turn(
    turn: Turn,
    subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]],
    continued_rounds: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Any]:
    """The plugins_in_rows of the turn and the plugins_in_launched_agents of its rows.
    Entries can be None or repeat."""
    plugins = plugins_in_rows(turn.rows)
    if subagent_transcripts_by_tool_use_id:
        plugins += plugins_in_launched_agents(turn.rows, subagent_transcripts_by_tool_use_id, continued_rounds or {})
    return plugins

def describe_plugins(plugins: List[Any], plugin_versions: Dict[str, Optional[str]]) -> List[Dict[str, str]]:
    """The trace's plugin list: each plugin named in plugins once, sorted by name, as
    {"name", "version"}, without "version" when plugin_versions has none for it."""
    entries: List[Dict[str, str]] = []
    for name in sorted({plugin for plugin in plugins if isinstance(plugin, str) and plugin}):
        entry = {"name": name}
        version = plugin_versions.get(name)
        if version is not None:
            entry["version"] = version
        entries.append(entry)
    return entries

# ---- The component that did an observation's work ----
SESSION_COMPONENT = "session"

@dataclass(frozen=True)
class Component:
    """What did an observation's work: SESSION_COMPONENT for the main session's own
    work, else an agent type, which can be None when the agent's meta file has none."""
    name: Any
    plugin: Optional[str]
    version: Optional[str]

def component_of(name: Any, plugin_versions: Dict[str, Optional[str]]) -> Component:
    """The component named name, with the plugin its name gives and that plugin's
    version from plugin_versions; plugin and version are None where it has none."""
    plugin = plugin_of(name)
    return Component(name, plugin, plugin_versions.get(plugin) if plugin else None)

def component_metadata(component: Component, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """The metadata of an observation of component's work: metadata with "component"
    added, "plugin" where it has a plugin, and "plugin_version" where that plugin has a
    version."""
    # Not the observation's version field: Langfuse copies a span's version onto its trace.
    with_component: Dict[str, Any] = {**metadata, "component": component.name}
    if component.plugin is not None:
        with_component["plugin"] = component.plugin
    if component.version is not None:
        with_component["plugin_version"] = component.version
    return with_component

def short_session_label(session_id: str, max_len: int = 12) -> str:
    """Return a compact session label for trace names."""
    sid = session_id.strip()
    if not sid:
        return "unknown"
    parts = sid.split("-")
    if len(parts) == 5 and len(parts[0]) == 8:
        return parts[0]
    return sid if len(sid) <= max_len else sid[:max_len].rstrip("-")

def trace_display_name(session_id: str, turn_num: int) -> str:
    return f"Claude Code - Turn {turn_num} ({short_session_label(session_id)})"

REPO_METADATA_KEY = "repo"

def repo_tag(repo_name: str) -> str:
    return f"repo:{repo_name}"

def read_work_items(session_id: str, branch_pattern: Optional[re.Pattern]) -> work_item.WorkItems:
    """The session's named work items and the branch pattern its turns take their item from.

    A marker that cannot be read is logged, and then no turn gets an item, not even
    from the branch.
    """
    try:
        path = work_item.marker_path(WORK_ITEMS_DIR, session_id)
    except ValueError as e:
        info(f"no work item for this session's traces: {e}")
        return work_item.WorkItems([], None)
    try:
        return work_item.WorkItems(work_item.read_entries(path), branch_pattern)
    except work_item.MarkerError as e:
        info(f"work item marker {path} unreadable, its session's traces carry no work item: {e}")
        return work_item.WorkItems([], None)

def work_item_tags(turn: Turn, work_items: work_item.WorkItems) -> List[str]:
    item = work_item.work_item_for(
        work_items.entries, work_items.branch_pattern, turn.user_msg.get("gitBranch"), get_turn_end_timestamp(turn)
    )
    return [f"work-item:{item}"] if item else []

def get_trace_tags(
    turn: Turn,
    subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]] = None,
    continued_rounds: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    tags = ["claude-code"]
    if SKILL_TAGS:
        tags += collect_skill_tags(turn)
        tags += collect_subagent_skill_tags(turn, subagent_transcripts_by_tool_use_id, continued_rounds)
    return tags

# ---- Generation payloads ----
def build_generation_input(
    assistant_index: int,
    user_text: str,
    previous_tool_results: List[Dict[str, Any]],
    ready_async_tool_results: List[Dict[str, Any]],
) -> Any:
    if assistant_index == 0:
        return {"role": "user", "content": user_text}
    # Both feed the next generation's context: results from the previous tool
    # batch AND async agent results that became ready since.
    tool_results = list(previous_tool_results)
    tool_results += [result["tool_result"] for result in ready_async_tool_results]
    if tool_results:
        return {"role": "tool", "tool_results": tool_results}
    return None

def build_generation_output(assistant_text: str, tool_uses: List[Dict[str, Any]]) -> Dict[str, Any]:
    output: Dict[str, Any] = {"role": "assistant"}
    if assistant_text:
        output["content"] = assistant_text
    if tool_uses:
        output["tool_calls"] = [
            {
                "id": tool_use.get("id"),
                "name": tool_use.get("name"),
            }
            for tool_use in tool_uses
        ]
    return output

# ---- Tool observations ----
@dataclass
class ToolResultForObservation:
    output: Any = None
    output_meta: Optional[Dict[str, Any]] = None
    result_timestamp: Optional[datetime] = None
    final_output: Any = None
    final_result_timestamp: Optional[datetime] = None
    is_error: bool = False
    tool_denial_kind: Optional[str] = None

@dataclass
class EmittedSingleToolObservation:
    handoff_timestamp: Optional[datetime]
    tool_result: Dict[str, Any]
    latest_end_timestamp: Optional[datetime]

@dataclass
class EmittedToolObservationBatch:
    result_timestamps: List[datetime]
    tool_results: List[Dict[str, Any]]
    latest_end_timestamp: Optional[datetime]

def get_tool_input_for_observation(tool_use: Dict[str, Any]) -> Tuple[Any, Optional[Dict[str, Any]]]:
    tool_input_raw = (
        tool_use.get("input")
        if isinstance(tool_use.get("input"), (dict, list, str, int, float, bool))
        else {}
    )
    if isinstance(tool_input_raw, str):
        return mask_and_truncate(tool_input_raw)
    return mask_secrets(tool_input_raw), None

def get_tool_result_for_observation(tool_result_entry: Any) -> ToolResultForObservation:
    if not isinstance(tool_result_entry, dict):
        return ToolResultForObservation()

    output, output_meta = mask_and_truncate_tool_result(tool_result_entry.get("content"))
    result_timestamp = parse_timestamp(tool_result_entry.get("timestamp"))
    is_error = bool(tool_result_entry.get("is_error"))
    tool_denial_kind = tool_result_entry.get("tool_denial_kind")

    final_output_raw = tool_result_entry.get("final_content")
    if final_output_raw is None:
        return ToolResultForObservation(
            output=output,
            output_meta=output_meta,
            result_timestamp=result_timestamp,
            is_error=is_error,
            tool_denial_kind=tool_denial_kind,
        )

    final_output, _ = mask_and_truncate_tool_result(final_output_raw)
    final_result_timestamp = parse_timestamp(tool_result_entry.get("final_timestamp"))
    return ToolResultForObservation(
        output=output,
        output_meta=output_meta,
        result_timestamp=result_timestamp,
        final_output=final_output,
        final_result_timestamp=final_result_timestamp,
        is_error=is_error,
        tool_denial_kind=tool_denial_kind,
    )

def get_short_transcript_path_for_metadata(path: Any) -> Optional[str]:
    if isinstance(path, Path):
        return path.name
    if isinstance(path, str) and path:
        return Path(path).name
    return None

def build_tool_metadata(
    tool_name: str,
    tool_use_id: str,
    tool_input_meta: Optional[Dict[str, Any]],
    tool_result: ToolResultForObservation,
    subagent: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    tool_metadata: Dict[str, Any] = {
        "tool_name": tool_name,
        "tool_id": tool_use_id,
        "input_meta": tool_input_meta,
        "output_meta": tool_result.output_meta,
    }
    if tool_result.tool_denial_kind:
        # Distinguishes permission denials from other tool failures without
        # requiring callers to parse statusMessage; queryable via the
        # Langfuse API without fetching output.
        tool_metadata["denial_kind"] = tool_result.tool_denial_kind
    if subagent:
        tool_metadata.update({
            "subagent_type": subagent.get("agent_type"),
            "subagent_description": subagent.get("description"),
            "subagent_transcript_path": get_short_transcript_path_for_metadata(subagent.get("path")),
        })
    return tool_metadata

def get_tool_observation_status(tool_result: ToolResultForObservation) -> Tuple[Optional[str], Optional[str]]:
    """Map a tool result's error/denial state to a Langfuse (level, statusMessage) pair.

    Successful calls return (None, None) so the observation keeps the
    platform default level. Permission denials get their own statusMessage;
    the specific denial kind (user-rejected vs. permission-rule) lives in
    tool_metadata['denial_kind'] rather than being encoded here.
    """
    if not tool_result.is_error:
        return None, None
    if tool_result.tool_denial_kind:
        return "ERROR", "Permission denied"
    return "ERROR", "Tool call failed"

def emit_single_tool_observation(
    langfuse: Langfuse,
    parent_otel_span: Any,
    turn: Turn,
    assistant_timestamp: Optional[datetime],
    tool_use: Dict[str, Any],
    component: Component,
    plugin_versions: Dict[str, Optional[str]],
    subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]],
    continued_rounds: Optional[Dict[str, Dict[str, Any]]],
    pending_subagents: List[Dict[str, Any]],
    pending_async_tool_results: List[Dict[str, Any]],
) -> EmittedSingleToolObservation:
    tool_use_id = str(tool_use.get("id") or "")
    tool_name = tool_use.get("name") or "unknown"
    tool_input, tool_input_meta = get_tool_input_for_observation(tool_use)

    tool_result_entry = turn.tool_results_by_id.get(tool_use_id) if tool_use_id else None
    tool_result = get_tool_result_for_observation(tool_result_entry)

    tool_output: Any = tool_result.output
    if CAPTURE_SKILL_CONTENT:
        injected = turn.injected_by_tool_id.get(tool_use_id) if tool_use_id else None
        if injected:
            injected_trunc, _ = mask_and_truncate(injected)
            tool_output = {"result": tool_result.output, "injected_instructions": injected_trunc}

    subagent = (
        subagent_transcripts_by_tool_use_id.get(tool_use_id)
        if subagent_transcripts_by_tool_use_id and tool_use_id
        else None
    )
    tool_metadata = build_tool_metadata(tool_name, tool_use_id, tool_input_meta, tool_result, subagent)
    tool_level, tool_status_message = get_tool_observation_status(tool_result)

    tool_use_timestamp = parse_timestamp(turn.tool_use_timestamps_by_id.get(tool_use_id)) or assistant_timestamp
    tool_span = _start_backdated(
        langfuse,
        name=f"Tool: {tool_name}",
        as_type="tool",
        start_time=tool_use_timestamp,
        parent_otel_span=parent_otel_span,
        input=tool_input,
        metadata=component_metadata(component, tool_metadata),
        **({"level": tool_level, "status_message": tool_status_message} if tool_level else {}),
    )
    tool_span.update(output=tool_output)

    subagent_end_timestamp = None
    if subagent or tool_use_id in (continued_rounds or {}):
        if tool_result.final_result_timestamp is not None:
            pending_subagents.append({
                "tool_use_id": tool_use_id,
                "start_timestamp": tool_use_timestamp,
                "ready_timestamp": tool_result.final_result_timestamp,
            })
        else:
            subagent_end_timestamp = emit_agent_run_of_call(
                langfuse,
                parent_otel_span,
                tool_use_id,
                tool_use_timestamp,
                tool_use_timestamp,
                plugin_versions,
                subagent_transcripts_by_tool_use_id or {},
                continued_rounds or {},
            )

    tool_end_timestamp = _get_latest_timestamp(tool_result.result_timestamp, tool_use_timestamp)
    handoff_timestamp = (
        tool_result.result_timestamp
        or tool_result.final_result_timestamp
        or subagent_end_timestamp
        or assistant_timestamp
    )
    tool_span.end(end_time=to_otel_nanoseconds(tool_end_timestamp))

    if tool_result.final_result_timestamp is not None and tool_result.final_output is not None:
        pending_async_tool_results.append({
            "timestamp": tool_result.final_result_timestamp,
            "tool_result": {
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "output": tool_result.final_output,
            },
        })

    return EmittedSingleToolObservation(
        handoff_timestamp=handoff_timestamp,
        tool_result={
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "output": tool_result.output,
        },
        latest_end_timestamp=_get_latest_timestamp(tool_end_timestamp, subagent_end_timestamp),
    )

def emit_tool_observation_batch(
    langfuse: Langfuse,
    parent_otel_span: Any,
    turn: Turn,
    assistant_message: Dict[str, Any],
    tool_uses: List[Dict[str, Any]],
    component: Component,
    plugin_versions: Dict[str, Optional[str]],
    subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]],
    continued_rounds: Optional[Dict[str, Dict[str, Any]]],
    pending_subagents: List[Dict[str, Any]],
    pending_async_tool_results: List[Dict[str, Any]],
) -> EmittedToolObservationBatch:
    assistant_timestamp = parse_timestamp(assistant_message)
    tool_result_timestamps: List[datetime] = []
    emitted_tool_results: List[Dict[str, Any]] = []
    latest_tool_end_timestamp: Optional[datetime] = None

    for tool_use in tool_uses:
        emitted_tool = emit_single_tool_observation(
            langfuse,
            parent_otel_span,
            turn,
            assistant_timestamp,
            tool_use,
            component,
            plugin_versions,
            subagent_transcripts_by_tool_use_id,
            continued_rounds,
            pending_subagents,
            pending_async_tool_results,
        )
        if emitted_tool.handoff_timestamp is not None:
            tool_result_timestamps.append(emitted_tool.handoff_timestamp)
        emitted_tool_results.append(emitted_tool.tool_result)
        latest_tool_end_timestamp = _get_latest_timestamp(
            latest_tool_end_timestamp,
            emitted_tool.latest_end_timestamp,
        )

    return EmittedToolObservationBatch(
        result_timestamps=tool_result_timestamps,
        tool_results=emitted_tool_results,
        latest_end_timestamp=latest_tool_end_timestamp,
    )

# ---- Turn and subagent observations ----
def get_ready_subagents(
    pending_subagents: List[Dict[str, Any]],
    assistant_timestamp: Optional[datetime],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ready_subagents: List[Dict[str, Any]] = []
    still_pending_subagents: List[Dict[str, Any]] = []
    for pending_subagent in pending_subagents:
        ready_timestamp = pending_subagent.get("ready_timestamp")
        if isinstance(ready_timestamp, datetime) and (
            assistant_timestamp is None or ready_timestamp <= assistant_timestamp
        ):
            ready_subagents.append(pending_subagent)
        else:
            still_pending_subagents.append(pending_subagent)
    return ready_subagents, still_pending_subagents

def get_ready_async_tool_results(
    pending_async_tool_results: List[Dict[str, Any]],
    assistant_timestamp: Optional[datetime],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[datetime]]:
    ready_async_tool_results: List[Dict[str, Any]] = []
    still_pending_tool_results: List[Dict[str, Any]] = []
    for async_tool_result in pending_async_tool_results:
        async_result_timestamp = async_tool_result.get("timestamp")
        if isinstance(async_result_timestamp, datetime) and (
            assistant_timestamp is None or async_result_timestamp <= assistant_timestamp
        ):
            ready_async_tool_results.append(async_tool_result)
        else:
            still_pending_tool_results.append(async_tool_result)

    latest_ready_timestamp = _get_latest_timestamp(*[
        result.get("timestamp")
        for result in ready_async_tool_results
        if isinstance(result.get("timestamp"), datetime)
    ])
    return ready_async_tool_results, still_pending_tool_results, latest_ready_timestamp

def update_pending_subagent_display_start_after_launch_response(
    pending_subagents: List[Dict[str, Any]],
    tool_results_used_as_generation_input: List[Dict[str, Any]],
    generation_start_timestamp: Optional[datetime],
) -> None:
    if generation_start_timestamp is None:
        return

    tool_use_ids = {
        str(tool_result.get("tool_use_id"))
        for tool_result in tool_results_used_as_generation_input
        if isinstance(tool_result, dict) and tool_result.get("tool_use_id")
    }
    if not tool_use_ids:
        return

    for pending_subagent in pending_subagents:
        if pending_subagent.get("display_start_timestamp") is not None:
            continue
        if pending_subagent.get("tool_use_id") in tool_use_ids:
            pending_subagent["display_start_timestamp"] = generation_start_timestamp + timedelta(
                microseconds=1
            )

def build_generation_kwargs(
    assistant_index: int,
    assistant_message: Dict[str, Any],
    user_text: str,
    previous_tool_results: List[Dict[str, Any]],
    ready_async_tool_results: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    assistant_text_raw = extract_text_from_content(get_content_from_row(assistant_message))
    assistant_text, assistant_text_meta = mask_and_truncate(assistant_text_raw)
    tool_uses = get_tool_use_blocks(get_content_from_row(assistant_message))

    generation_kwargs: Dict[str, Any] = dict(
        model=get_model(assistant_message),
        input=build_generation_input(
            assistant_index,
            user_text,
            previous_tool_results,
            ready_async_tool_results,
        ),
        output=build_generation_output(assistant_text, tool_uses),
        metadata={
            "assistant_index": assistant_index,
            "assistant_text": assistant_text_meta,
            "tool_count": len(tool_uses),
        },
    )
    usage_details = get_usage_details_from_row(assistant_message)
    if usage_details is not None:
        generation_kwargs["usage_details"] = usage_details
    return generation_kwargs, tool_uses

def emit_generation_observation(
    langfuse: Langfuse,
    parent_otel_span: Any,
    generation_prefix: str,
    assistant_index: int,
    start_timestamp: Optional[datetime],
    generation_kwargs: Dict[str, Any],
    component: Component,
) -> Any:
    # An assistant message without a usage record is not a billable LLM call;
    # emitting it as a generation would poison downstream token metering, which
    # treats every generation as meterable. It is emitted as a span instead,
    # with the model kept in metadata.
    kwargs = dict(generation_kwargs)
    as_type = "generation"
    if "usage_details" not in kwargs:
        as_type = "span"
        metadata = dict(kwargs.get("metadata") or {})
        metadata["model"] = kwargs.pop("model", None)
        metadata["usage_missing"] = True
        kwargs["metadata"] = metadata
    kwargs["metadata"] = component_metadata(component, kwargs["metadata"])
    return _start_backdated(
        langfuse,
        name=f"{generation_prefix} {assistant_index + 1}",
        as_type=as_type,
        start_time=start_timestamp,
        parent_otel_span=parent_otel_span,
        **kwargs,
    )

def emit_turn_observations(langfuse: Langfuse, parent_otel_span: Any, turn: Turn,
                           start_timestamp: Optional[datetime],
                           component: Component,
                           plugin_versions: Dict[str, Optional[str]],
                           generation_prefix: str = "LLM Call",
                           subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]] = None,
                           continued_rounds: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[datetime]:
    """Emit a turn's generations and tool observations under an existing span, as
    component's work. The agent round a tool call launched or continued is emitted as
    the work of its own agent type, with its version from plugin_versions."""
    user_text, _ = mask_and_truncate(extract_text_from_content(get_content_from_row(turn.user_msg)))
    previous_timestamp = start_timestamp
    previous_tool_results: List[Dict[str, Any]] = []
    pending_async_tool_results: List[Dict[str, Any]] = []
    pending_subagents: List[Dict[str, Any]] = []
    latest_end_timestamp = start_timestamp

    def emit_pending_run(pending_subagent: Dict[str, Any]) -> Optional[datetime]:
        return emit_agent_run_of_call(
            langfuse,
            parent_otel_span,
            pending_subagent["tool_use_id"],
            pending_subagent.get("start_timestamp"),
            pending_subagent.get("display_start_timestamp") or pending_subagent.get("start_timestamp"),
            plugin_versions,
            subagent_transcripts_by_tool_use_id or {},
            continued_rounds or {},
        )

    for assistant_index, assistant_message in enumerate(turn.assistant_msgs):
        assistant_timestamp = parse_timestamp(assistant_message)
        if assistant_index > 0 and pending_subagents:
            ready_subagents, pending_subagents = get_ready_subagents(
                pending_subagents,
                assistant_timestamp,
            )
            for ready_subagent in ready_subagents:
                subagent_end_timestamp = emit_pending_run(ready_subagent)
                latest_end_timestamp = _get_latest_timestamp(latest_end_timestamp, subagent_end_timestamp)

        ready_async_tool_results: List[Dict[str, Any]] = []
        if assistant_index > 0 and pending_async_tool_results:
            ready_async_tool_results, pending_async_tool_results, ready_async_result_timestamp = (
                get_ready_async_tool_results(pending_async_tool_results, assistant_timestamp)
            )
            previous_timestamp = _get_latest_timestamp(previous_timestamp, ready_async_result_timestamp)

        generation_kwargs, tool_uses = build_generation_kwargs(
            assistant_index,
            assistant_message,
            user_text,
            previous_tool_results,
            ready_async_tool_results,
        )
        generation_start_timestamp = previous_timestamp or assistant_timestamp
        generation_span = emit_generation_observation(
            langfuse,
            parent_otel_span=parent_otel_span,
            generation_prefix=generation_prefix,
            assistant_index=assistant_index,
            start_timestamp=generation_start_timestamp,
            generation_kwargs=generation_kwargs,
            component=component,
        )
        update_pending_subagent_display_start_after_launch_response(
            pending_subagents,
            previous_tool_results,
            generation_start_timestamp,
        )

        emitted_tools = emit_tool_observation_batch(
            langfuse,
            parent_otel_span,
            turn,
            assistant_message,
            tool_uses,
            component,
            plugin_versions,
            subagent_transcripts_by_tool_use_id,
            continued_rounds,
            pending_subagents,
            pending_async_tool_results,
        )
        latest_end_timestamp = _get_latest_timestamp(
            latest_end_timestamp,
            emitted_tools.latest_end_timestamp,
        )

        generation_end_timestamp = (
            max(emitted_tools.result_timestamps)
            if emitted_tools.result_timestamps
            else assistant_timestamp
        )
        generation_span.end(
            end_time=to_otel_nanoseconds(
                generation_end_timestamp or assistant_timestamp or previous_timestamp
            )
        )
        latest_end_timestamp = _get_latest_timestamp(latest_end_timestamp, generation_end_timestamp)

        previous_tool_results = emitted_tools.tool_results
        if emitted_tools.result_timestamps:
            previous_timestamp = max(emitted_tools.result_timestamps)
        elif assistant_timestamp is not None:
            previous_timestamp = assistant_timestamp

    for pending_subagent in pending_subagents:
        subagent_end_timestamp = emit_pending_run(pending_subagent)
        latest_end_timestamp = _get_latest_timestamp(latest_end_timestamp, subagent_end_timestamp)

    return latest_end_timestamp

def emit_agent_run_of_call(langfuse: Langfuse, parent_otel_span: Any,
                           tool_use_id: str,
                           call_timestamp: Optional[datetime],
                           start_timestamp: Optional[datetime],
                           plugin_versions: Dict[str, Optional[str]],
                           subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
                           continued_rounds: Dict[str, Dict[str, Any]]) -> Optional[datetime]:
    """Emit the agent_run_of_call of the call made at call_timestamp, as a run starting
    at start_timestamp, and return its end. Where the call continued an agent but its
    round is not in the agent transcript, nothing is emitted and the hook log gets a
    line naming the agent, the call and the round."""
    run = agent_run_of_call(tool_use_id, call_timestamp, subagent_transcripts_by_tool_use_id, continued_rounds)
    if run is not None:
        return emit_subagent_observations(langfuse, parent_otel_span, run, start_timestamp, plugin_versions)
    continued = continued_rounds.get(tool_use_id)
    if continued is not None:
        info(
            f"No run for SendMessage call {tool_use_id}: agent {continued['subagent'].get('agent_id')} "
            f"has no round {continued['round']} that starts at or after the call"
        )
    return start_timestamp

def emit_subagent_observations(langfuse: Langfuse, parent_otel_span: Any,
                               run: AgentRun,
                               start_timestamp: Optional[datetime],
                               plugin_versions: Dict[str, Optional[str]]) -> Optional[datetime]:
    subagent = run.subagent
    path = subagent.get("path")
    turns = build_turns(run.rows)
    if not turns:
        return start_timestamp

    first_turn = turns[0]
    subagent_start_timestamp = start_timestamp or parse_timestamp(first_turn.user_msg)
    subagent_input_text, subagent_input_meta = mask_and_truncate(extract_text_from_content(get_content_from_row(first_turn.user_msg)))

    last_turn = turns[-1]
    last_assistant = last_turn.assistant_msgs[-1]
    subagent_output_text, _ = mask_and_truncate(extract_text_from_content(get_content_from_row(last_assistant)))

    component = component_of(subagent.get("agent_type"), plugin_versions)
    description = subagent.get("description")
    subagent_name = f"Subagent: {description}" if isinstance(description, str) and description else "Subagent"
    if run.round_number > 1:
        subagent_name += f" - round {run.round_number}"
    subagent_span = _start_backdated(
        langfuse,
        name=subagent_name,
        as_type="span",
        start_time=subagent_start_timestamp,
        parent_otel_span=parent_otel_span,
        input={"role": "user", "content": subagent_input_text},
        metadata=component_metadata(component, {
            "agent_type": subagent.get("agent_type"),
            "description": description,
            "round": run.round_number,
            "transcript_path": get_short_transcript_path_for_metadata(path),
            "user_text": subagent_input_meta,
        }),
    )

    latest_end_timestamp = subagent_start_timestamp
    previous_start_timestamp = subagent_start_timestamp
    for turn in turns:
        latest_turn_timestamp = emit_turn_observations(
            langfuse,
            subagent_span._otel_span,
            turn,
            previous_start_timestamp,
            component,
            plugin_versions,
            generation_prefix="Subagent LLM Call",
            subagent_transcripts_by_tool_use_id=None,
        )
        latest_end_timestamp = _get_latest_timestamp(latest_end_timestamp, latest_turn_timestamp)
        if latest_turn_timestamp is not None:
            previous_start_timestamp = latest_turn_timestamp

    subagent_span.update(output={"role": "assistant", "content": subagent_output_text})
    subagent_span.end(
        end_time=to_otel_nanoseconds(
            _get_latest_timestamp(latest_end_timestamp, subagent_start_timestamp)
        )
    )

    return latest_end_timestamp

def read_jsonl_rows(path: Path) -> Optional[List[Dict[str, Any]]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        info(f"transcript read failed ({path}): {type(e).__name__}: {e}")
        return None

    rows: List[Dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception as e:
            info(f"transcript line skipped ({path}:{line_number}): {type(e).__name__}: {e}")
            continue
        if not isinstance(row, dict):
            info(f"transcript line skipped ({path}:{line_number}): expected JSON object")
            continue
        rows.append(row)
    return rows

def get_turn_end_timestamp(turn: Turn) -> Optional[datetime]:
    last_assistant_timestamp = parse_timestamp(turn.assistant_msgs[-1]) if turn.assistant_msgs else None
    candidate_end_timestamps = [
        timestamp
        for timestamp in [last_assistant_timestamp]
        if timestamp is not None
    ]
    for tool_result_entry in turn.tool_results_by_id.values():
        timestamp = parse_timestamp(tool_result_entry)
        if timestamp is not None:
            candidate_end_timestamps.append(timestamp)
    return max(candidate_end_timestamps) if candidate_end_timestamps else None

def build_trace_metadata(
    session_id: str,
    turn_num: int,
    turn: Turn,
    transcript_path: Path,
    user_text_meta: Dict[str, Any],
) -> Dict[str, Any]:
    trace_metadata: Dict[str, Any] = {
        "source": "claude-code",
        "session_id": session_id,
        "turn_number": turn_num,
        "transcript_path": get_short_transcript_path_for_metadata(transcript_path),
        "user_text": user_text_meta,
        "assistant_message_count": len(turn.assistant_msgs),
    }
    # Transcript rows carry the project dir and git branch so traces from
    # different projects/worktrees are distinguishable in Langfuse.
    for src_key, dst_key in (("cwd", "cwd"), ("gitBranch", "git_branch")):
        value = turn.user_msg.get(src_key)
        if isinstance(value, str) and value:
            trace_metadata[dst_key] = value
    return trace_metadata

def emit_turn(langfuse: Langfuse, session_id: str, turn_num: int, turn: Turn, transcript_path: Path,
              repo_name: str,
              plugin_versions: Dict[str, Optional[str]],
              user_id: Optional[str] = None,
              subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]] = None,
              trace_seed: Optional[str] = None,
              *,
              work_items: work_item.WorkItems,
              continued_rounds: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[str]:
    user_text_raw = extract_text_from_content(get_content_from_row(turn.user_msg))
    user_text, user_text_meta = mask_and_truncate(user_text_raw)

    last_assistant = turn.assistant_msgs[-1]
    final_assistant_text, _ = mask_and_truncate(extract_text_from_content(get_content_from_row(last_assistant)))

    user_ts = parse_timestamp(turn.user_msg)
    last_assistant_ts = parse_timestamp(last_assistant)
    turn_end_ts = get_turn_end_timestamp(turn)
    trace_metadata = build_trace_metadata(session_id, turn_num, turn, transcript_path, user_text_meta)
    trace_metadata[PLUGINS_METADATA_KEY] = describe_plugins(
        plugins_used_in_turn(turn, subagent_transcripts_by_tool_use_id, continued_rounds), plugin_versions
    )
    tags = get_trace_tags(turn, subagent_transcripts_by_tool_use_id, continued_rounds) + [repo_tag(repo_name)]
    tags += work_item_tags(turn, work_items)

    trace_name = trace_display_name(session_id, turn_num)
    session = component_of(SESSION_COMPONENT, plugin_versions)
    root_observation_name = "Conversational Turn"

    # A held-back turn brings the trace id it was given; otherwise opt-in
    # deterministic trace ids, failing open to the auto-generated id.
    forced_trace_id: Optional[str] = turn.trace_id
    if forced_trace_id is None and trace_seed:
        try:
            forced_trace_id = derive_turn_trace_id(trace_seed, turn_num)
        except Exception as e:
            debug(f"trace id derivation failed for turn {turn_num}: {e}")

    with propagate_attributes(
        session_id=session_id,
        user_id=user_id,
        trace_name=trace_name,
        tags=tags,
        metadata={REPO_METADATA_KEY: repo_name},
    ):
        trace_span = _start_backdated(
            langfuse,
            name=root_observation_name,
            as_type="span",
            start_time=user_ts,
            forced_trace_id=forced_trace_id,
            input={"role": "user", "content": user_text},
            metadata=component_metadata(session, trace_metadata),
        )
        obs_end_ts = emit_turn_observations(
            langfuse,
            trace_span._otel_span,
            turn,
            user_ts,
            session,
            plugin_versions,
            subagent_transcripts_by_tool_use_id=subagent_transcripts_by_tool_use_id,
            continued_rounds=continued_rounds,
        )
        trace_span.update(output={"role": "assistant", "content": final_assistant_text})
        trace_span.end(end_time=to_otel_nanoseconds(_get_latest_timestamp(turn_end_ts, last_assistant_ts, obs_end_ts, user_ts)))
        # trace_span.trace_id reads the id actually attached to the root span's
        # otel context — the forced id when trace_seed produced one, otherwise
        # whatever the SDK auto-generated. Same attribute, either path.
        return trace_span.trace_id


# ---- Named teammate (agent-team) emission ----
def teammate_trace_display_name(name: str, teammate_turn_num: int, session_id: str) -> str:
    return f"Teammate {name} - Turn {teammate_turn_num} ({short_session_label(session_id)})"


def derive_teammate_trace_id(trace_seed: str, agent_id: str, teammate_turn_num: int) -> Optional[str]:
    """Deterministic trace id for a teammate turn.

    Namespaced by the per-meta agent_id (unique per teammate transcript, hash
    included), NOT the display name: two teammates can share a name, and keying
    on the name would derive identical ids so the second teammate's turns would
    upsert over — silently clobber — the first's token stream. It is also
    namespaced away from main-conversation turns that share a number.
    """
    return derive_turn_trace_id(f"{trace_seed}:teammate:{agent_id}", teammate_turn_num)


def is_teammate_turn_complete(turn: Turn) -> bool:
    """Whether a teammate turn can no longer grow: its final assistant message
    ended the turn rather than awaiting a tool result.

    Used only together with a "transcript unchanged since the previous firing"
    check, so a momentary lull mid-tool-loop never counts as complete.
    """
    if not turn.assistant_msgs:
        return False
    last = turn.assistant_msgs[-1]
    message = last.get("message")
    stop_reason = message.get("stop_reason") if isinstance(message, dict) else None
    if stop_reason == "tool_use":
        return False
    # Even without a stop_reason, a tool_use whose result has not landed means
    # the turn is still in flight.
    for tool_use in get_tool_use_blocks(get_content_from_row(last)):
        tool_use_id = str(tool_use.get("id") or "")
        if tool_use_id and tool_use_id not in turn.tool_results_by_id:
            return False
    return True


def emit_teammate_turn(
    langfuse: Langfuse,
    session_id: str,
    teammate: Dict[str, Any],
    teammate_turn_num: int,
    turn: Turn,
    transcript_path: Path,
    *,
    repo_name: str,
    plugin_versions: Dict[str, Optional[str]],
    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
    user_id: Optional[str] = None,
    launching_tool_use_id: Optional[str] = None,
    ambiguous_name: bool = False,
    trace_seed: Optional[str] = None,
    work_items: work_item.WorkItems,
) -> None:
    """Emit one teammate turn as its own top-level trace.

    A teammate is a parallel, long-lived conversation, so its turns become their
    own traces (like main-conversation turns) rather than nesting under the
    launching turn — the launch happened many parent turns earlier and its trace
    is long closed. Linkage to the launching Agent tool_use is carried as trace
    metadata/tags (launching_tool_use_id, teammate:<name>), which is the
    filterable, cross-process-safe form of "linking".
    """
    name = teammate.get("name") or "teammate"
    agent_id = teammate.get("agent_id") or name
    user_text_raw = extract_text_from_content(get_content_from_row(turn.user_msg))
    user_text, user_text_meta = mask_and_truncate(user_text_raw)

    last_assistant = turn.assistant_msgs[-1]
    final_assistant_text, _ = mask_and_truncate(extract_text_from_content(get_content_from_row(last_assistant)))

    user_ts = parse_timestamp(turn.user_msg)
    last_assistant_ts = parse_timestamp(last_assistant)
    turn_end_ts = get_turn_end_timestamp(turn)

    trace_metadata = build_trace_metadata(session_id, teammate_turn_num, turn, transcript_path, user_text_meta)
    trace_metadata.update({
        "teammate_name": name,
        "teammate_turn_number": teammate_turn_num,
        "team_name": teammate.get("team_name"),
        "agent_type": teammate.get("agent_type"),
        "launching_tool_use_id": launching_tool_use_id,
        PLUGINS_METADATA_KEY: describe_plugins(
            plugins_used_in_turn(turn, subagent_transcripts_by_tool_use_id)
            + [plugin_of(teammate.get("agent_type"))],
            plugin_versions,
        ),
    })

    tags = ["claude-code", f"teammate:{name}", repo_tag(repo_name)]
    if launching_tool_use_id is None:
        tags.append("teammate-unattributed")
    if ambiguous_name:
        tags.append("teammate-ambiguous-name")
    if SKILL_TAGS:
        tags += collect_skill_tags(turn)
    tags += work_item_tags(turn, work_items)

    trace_name = teammate_trace_display_name(name, teammate_turn_num, session_id)
    teammate_component = component_of(teammate.get("agent_type"), plugin_versions)

    forced_trace_id: Optional[str] = None
    if trace_seed:
        try:
            forced_trace_id = derive_teammate_trace_id(trace_seed, agent_id, teammate_turn_num)
        except Exception as e:
            debug(f"teammate trace id derivation failed for {agent_id} turn {teammate_turn_num}: {e}")

    with propagate_attributes(
        session_id=session_id,
        user_id=user_id,
        trace_name=trace_name,
        tags=tags,
        metadata={REPO_METADATA_KEY: repo_name},
    ):
        trace_span = _start_backdated(
            langfuse,
            name="Teammate Turn",
            as_type="span",
            start_time=user_ts,
            forced_trace_id=forced_trace_id,
            input={"role": "user", "content": user_text},
            metadata=component_metadata(teammate_component, trace_metadata),
        )
        obs_end_ts = emit_turn_observations(
            langfuse,
            trace_span._otel_span,
            turn,
            user_ts,
            teammate_component,
            plugin_versions,
            generation_prefix="Teammate LLM Call",
            # Agents a teammate launches get no spans of their own here. Where
            # Claude Code writes their transcripts is not checked.
            subagent_transcripts_by_tool_use_id=None,
        )
        trace_span.update(output={"role": "assistant", "content": final_assistant_text})
        trace_span.end(end_time=to_otel_nanoseconds(_get_latest_timestamp(turn_end_ts, last_assistant_ts, obs_end_ts, user_ts)))


def emit_teammate_transcripts(
    langfuse: Langfuse,
    config: LangfuseConfig,
    session_id: str,
    transcript_path: Path,
    state: Dict[str, Any],
    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
    *,
    work_items: work_item.WorkItems,
    flush_deferred_agent_turns: bool = False,
) -> int:
    """Emit newly-closed turns for every named teammate under this session.

    Runs on every hook firing (Stop and SessionEnd), independent of whether the
    parent produced new turns. Each teammate is processed in isolation so one
    teammate's failure never aborts the others (and never propagates to the
    classic path, whose progress is already persisted before this runs).
    """
    teammates = discover_teammate_transcripts(transcript_path)
    if not teammates:
        return 0

    name_to_tool_use_id, ambiguous_names = get_teammate_name_to_tool_use_id(transcript_path)

    emitted_total = 0
    for teammate in teammates:
        try:
            emitted_total += _emit_one_teammate(
                langfuse,
                config,
                session_id,
                transcript_path,
                state,
                teammate,
                name_to_tool_use_id,
                ambiguous_names,
                subagent_transcripts_by_tool_use_id,
                work_items=work_items,
                flush=flush_deferred_agent_turns,
            )
        except Exception as e:
            info(f"teammate capture failed ({teammate.get('name')}): {type(e).__name__}: {e}")
    return emitted_total


def _emit_one_teammate(
    langfuse: Langfuse,
    config: LangfuseConfig,
    session_id: str,
    transcript_path: Path,
    state: Dict[str, Any],
    teammate: Dict[str, Any],
    name_to_tool_use_id: Dict[str, str],
    ambiguous_names: set,
    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
    *,
    work_items: work_item.WorkItems,
    flush: bool,
) -> int:
    """Emit one teammate's newly-closed turns and persist its progress.

    Growth and idempotency reuse the shared state store (a TeammateProgress entry
    keyed by the teammate's transcript path): emitted_turns only ever advances.

    A teammate is usually mid-turn when a parent Stop fires, so the still-growing
    final turn is held back until (a) a newer turn closes it, (b) the transcript
    is unchanged since the previous firing and the final turn has ended — a
    one-shot background teammate that finished and is never resumed, which would
    otherwise wait for SessionEnd — or (c) SessionEnd flushes. A turn is never
    emitted while it can still grow, so nothing is ever double-counted.
    """
    path = teammate.get("path")
    if not isinstance(path, Path):
        return 0

    name = teammate.get("name")
    launching_tool_use_id = name_to_tool_use_id.get(name) if isinstance(name, str) else None
    ambiguous = isinstance(name, str) and name in ambiguous_names
    if launching_tool_use_id is None:
        info(
            f"Teammate transcript without a matched launching Agent tool_use: "
            f"{name} ({path.name}); emitting as unattributed"
        )

    key = get_session_state_key(session_id, str(path))
    progress = get_teammate_progress(state, key)

    try:
        current_size = path.stat().st_size
    except Exception:
        current_size = 0

    # Perf guard: once we have caught up to the whole transcript (settled) and it
    # has not changed since the previous firing, there is nothing to do — skip the
    # whole-file reread so the global state lock is not held reparsing idle
    # teammates on every Stop of a long session.
    if not flush and progress.settled and current_size > 0 and current_size == progress.last_size:
        update_teammate_progress(state, key, progress)  # keep "updated" fresh
        return 0

    rows = read_jsonl_rows(path)
    if not rows:
        return 0
    turns = build_turns(rows)

    stable = current_size > 0 and current_size == progress.last_size
    already = progress.emitted_turns

    # In-place growth of the most-recently emitted turn. Transcripts are
    # append-only, so only the last emitted turn can grow after emission (a resume
    # starts a new turn). Re-emitting is a safe corrective upsert only under a
    # trace seed (identical deterministic id); without one it would duplicate, so
    # log the undercount loudly instead.
    start = already
    if 0 < already <= len(turns):
        grown_rowcount = len(turns[already - 1].rows)
        if grown_rowcount > progress.last_emitted_rowcount:
            if config.trace_seed:
                start = already - 1  # re-emit the grown turn as an upsert
            else:
                info(
                    f"Teammate {name} turn {already} grew after emission "
                    f"({progress.last_emitted_rowcount} -> {grown_rowcount} rows); "
                    f"undercount — set CC_LANGFUSE_TRACE_SEED to enable corrective re-emit"
                )
                progress.last_emitted_rowcount = grown_rowcount

    if flush:
        closable = len(turns)
    elif stable and turns and is_teammate_turn_complete(turns[-1]):
        closable = len(turns)
    else:
        closable = max(len(turns) - 1, 0)

    last_emitted_rowcount = progress.last_emitted_rowcount
    emitted = 0
    for index in range(start, closable):
        teammate_turn_num = index + 1
        try:
            emit_teammate_turn(
                langfuse,
                session_id,
                teammate,
                teammate_turn_num,
                turns[index],
                transcript_path,
                repo_name=config.repo_name,
                plugin_versions=config.plugin_versions,
                subagent_transcripts_by_tool_use_id=subagent_transcripts_by_tool_use_id,
                user_id=config.user_id,
                launching_tool_use_id=launching_tool_use_id,
                ambiguous_name=ambiguous,
                trace_seed=config.trace_seed,
                work_items=work_items,
            )
            emitted += 1
            last_emitted_rowcount = len(turns[index].rows)
        except Exception as e:
            info(f"emit_teammate_turn failed ({name} turn {teammate_turn_num}): {type(e).__name__}: {e}")

    progress.emitted_turns = max(already, closable)
    if emitted:
        progress.last_emitted_rowcount = last_emitted_rowcount
    progress.last_size = current_size
    # "Settled" means the perf guard may skip the next reread. It requires every
    # turn emitted AND no un-re-emitted in-place growth on the last emitted turn,
    # so a pending corrective upsert is never skipped over.
    tail_grew = (
        0 < progress.emitted_turns <= len(turns)
        and len(turns[progress.emitted_turns - 1].rows) > progress.last_emitted_rowcount
    )
    progress.settled = progress.emitted_turns >= len(turns) and not tail_grew
    update_teammate_progress(state, key, progress)
    return emitted


# ---- New turn emission orchestration ----
def emit_ready_turns(
    langfuse: Langfuse,
    session_id: str,
    transcript_path: Path,
    turns_to_emit: List[Turn],
    *,
    repo_name: str,
    plugin_versions: Dict[str, Optional[str]],
    user_id: Optional[str],
    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]],
    continued_rounds: Dict[str, Dict[str, Any]],
    trace_seed: Optional[str] = None,
    work_items: work_item.WorkItems,
) -> int:
    emitted = 0
    for turn in turns_to_emit:
        emitted += 1
        assert turn.number is not None, "number_new_turns numbers every turn before it is sent"
        try:
            trace_id = emit_turn(
                langfuse,
                session_id,
                turn.number,
                turn,
                transcript_path,
                repo_name,
                plugin_versions,
                user_id=user_id,
                subagent_transcripts_by_tool_use_id=subagent_transcripts_by_tool_use_id,
                trace_seed=trace_seed,
                work_items=work_items,
                continued_rounds=continued_rounds,
            )
        except Exception as e:
            # Log at INFO so SDK incompatibilities (and other emit failures)
            # are visible without needing CC_LANGFUSE_DEBUG=true.
            info(f"emit_turn failed: {type(e).__name__}: {e}")
        else:
            turn.trace_id = trace_id
    return emitted

def emit_new_turns_from_transcript(
    langfuse: Langfuse,
    config: LangfuseConfig,
    session_id: str,
    transcript_path: Path,
    *,
    flush_deferred_agent_turns: bool = False,
) -> int:
    with FileLock(LOCK_FILE):
        state = load_hook_state()
        key = get_session_state_key(session_id, str(transcript_path))
        session_state = get_session_state(state, key)

        subagent_transcripts_by_tool_use_id = get_subagent_transcripts_by_tool_use_id(transcript_path)
        if subagent_transcripts_by_tool_use_id:
            debug(f"Discovered {len(subagent_transcripts_by_tool_use_id)} subagent transcript(s)")
        continued_rounds = get_continued_rounds_by_tool_use_id(transcript_path, subagent_transcripts_by_tool_use_id)
        work_items = read_work_items(session_id, config.work_item_branch_pattern)

        turns, session_state = get_new_turns_from_transcript(
            transcript_path,
            session_state,
            subagent_transcripts_by_tool_use_id,
            continued_rounds,
            flush_deferred_agent_turns=flush_deferred_agent_turns,
        )

        emitted = 0
        if turns:
            new_turns = [turn for turn in turns if turn.number is None]
            turns = number_new_turns(turns, session_state, continued_rounds, config.trace_seed)
            turns_to_emit = get_turns_to_emit(
                turns,
                session_state,
                continued_rounds,
                flush_deferred_agent_turns=flush_deferred_agent_turns,
            )
            emitted = emit_ready_turns(
                langfuse,
                session_id,
                transcript_path,
                turns_to_emit,
                repo_name=config.repo_name,
                plugin_versions=config.plugin_versions,
                user_id=config.user_id,
                subagent_transcripts_by_tool_use_id=subagent_transcripts_by_tool_use_id,
                continued_rounds=continued_rounds,
                trace_seed=config.trace_seed,
                work_items=work_items,
            )
            if new_turns and new_turns[-1].trace_id:
                session_state.latest_trace_id = new_turns[-1].trace_id

        # Persist classic progress immediately: the teammate pass below shares the
        # same state dict, and a failure there must never discard the classic
        # offsets already emitted this run (which would re-emit classic turns).
        update_session_state(state, key, session_state)
        save_hook_state(state)

        # Named teammates (agent teams) grow their own transcripts independently
        # of the parent, so capture them on every firing — even when the parent
        # produced no new turns this run. Guarded so teammate capture can never
        # break classic capture; a second save persists teammate progress.
        try:
            emitted += emit_teammate_transcripts(
                langfuse,
                config,
                session_id,
                transcript_path,
                state,
                subagent_transcripts_by_tool_use_id,
                work_items=work_items,
                flush_deferred_agent_turns=flush_deferred_agent_turns,
            )
        except Exception as e:
            info(f"teammate capture pass failed: {type(e).__name__}: {e}")
        finally:
            save_hook_state(state)
        return emitted


# ----------------- Delivery health -----------------
# The hook itself only stores turns; the background sender sends them. So the
# hook learns whether Langfuse takes these keys from one REST call on the
# endpoint the SDK authenticates against, made with LangfuseClient rather than
# the SDK's blocking, retrying auth_check().

DELIVERY_HEALTH_STATE_KEY = "_delivery_health"
# Re-verified at most this often: a blocking round trip on every
# Stop/SessionEnd firing is not acceptable, but a rejected or revoked
# keypair must not go undetected for long either.
DELIVERY_HEALTH_CHECK_INTERVAL = timedelta(minutes=15)


def _credential_fingerprint(config: LangfuseConfig) -> str:
    raw = f"{config.public_key}:{config.secret_key}:{config.host}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def classify_delivery_health(config: LangfuseConfig) -> Tuple[str, str]:
    """Probes whether traces can currently reach Langfuse under these
    credentials. Returns (status, detail):

    - "ok": credentials accepted.
    - "rejected": the host answered but refused the keypair (401/403) —
      diagnosable and worth naming the credential as the cause.
    - "unreachable": no answer (network/DNS/timeout) or any other non-2xx
      response — the host or route is the more likely cause.

    Never raises: LangfuseClient._request() catches every exception itself
    and reports it as status 0.
    """
    response = LangfuseClient(config, timeout=5).get("/api/public/projects")
    if response.status == 200:
        return "ok", ""
    if keys_rejected(response.status):
        return "rejected", f"HTTP {response.status} from {config.host}"
    if response.status == 0:
        return "unreachable", f"{config.host} did not respond"
    return "unreachable", f"HTTP {response.status} from {config.host}"


def _cached_delivery_status(state: Dict[str, Any], fingerprint: str) -> Optional[Tuple[str, str]]:
    cached = state.get(DELIVERY_HEALTH_STATE_KEY)
    if not isinstance(cached, dict) or cached.get("fingerprint") != fingerprint:
        return None
    checked_at = parse_timestamp(cached.get("updated"))
    status = cached.get("status")
    if (
        checked_at is None
        or not isinstance(status, str)
        or datetime.now(timezone.utc) - checked_at >= DELIVERY_HEALTH_CHECK_INTERVAL
    ):
        return None
    detail = cached.get("detail")
    return status, detail if isinstance(detail, str) else ""


def get_delivery_status(config: LangfuseConfig) -> Tuple[str, str]:
    """Cached, rate-limited wrapper around classify_delivery_health.

    Cached under the same state file and lock as everything else, keyed by
    a fingerprint of the credentials in use so a key rotation is picked up
    on the very next firing instead of waiting out the interval. Fails open
    on lock contention: assume healthy rather than block or misreport.

    The network probe itself deliberately runs outside the lock: holding
    LOCK_FILE for the probe's (bounded but non-trivial) duration would stall
    every other session's turn emission, which shares the same lock, for as
    long as the probe takes. Losing the race and probing twice in the rare
    case of two firings both finding a stale cache is a fine trade for that.
    """
    fingerprint = _credential_fingerprint(config)
    try:
        with FileLock(LOCK_FILE):
            cached = _cached_delivery_status(load_hook_state(), fingerprint)
        if cached is not None:
            return cached
    except TimeoutError as e:
        debug(f"delivery health lock timeout, assuming healthy: {e}")
        return "ok", ""

    status, detail = classify_delivery_health(config)

    try:
        with FileLock(LOCK_FILE):
            state = load_hook_state()
            state[DELIVERY_HEALTH_STATE_KEY] = {
                "fingerprint": fingerprint,
                "status": status,
                "detail": detail,
                "updated": datetime.now(timezone.utc).isoformat(),
            }
            save_hook_state(state)
    except TimeoutError as e:
        debug(f"delivery health lock timeout while caching result: {e}")

    return status, detail


def peek_delivery_status(config: LangfuseConfig) -> Optional[Tuple[str, str]]:
    """Reads whatever delivery status was last recorded for this credential
    fingerprint, however stale — never probes and never refreshes the cache.

    For the firings that have nothing new to send: emitting nothing new is
    not the same as the plugin working, and a known-bad status must still
    surface every time, not just on the (rate-limited) firings that
    re-probe. Returns None only when there is no verdict yet at all (never
    checked, or checked under different credentials) — silence in that case
    is correct, not a gap: an unverified plugin must not warn speculatively.
    """
    fingerprint = _credential_fingerprint(config)
    try:
        with FileLock(LOCK_FILE):
            state = load_hook_state()
    except TimeoutError:
        return None
    cached = state.get(DELIVERY_HEALTH_STATE_KEY)
    if not isinstance(cached, dict) or cached.get("fingerprint") != fingerprint:
        return None
    status = cached.get("status")
    if not isinstance(status, str):
        return None
    detail = cached.get("detail")
    return status, detail if isinstance(detail, str) else ""


def format_processed_log(emitted: int, dur: float, session_id: str, status: str, detail: str) -> str:
    """Renders the one line main() logs after each firing.

    A run that delivered nothing never claims "Processed N turns" — that
    phrasing is reserved for a run confirmed (or, when emitted is 0,
    trivially) not to have lost anything. Rejection and unreachability get
    their own wording naming the likely cause, since collapsing them back
    into one generic failure message would recreate the bug this reports.
    """
    if emitted > 0 and status == "rejected":
        return (
            f"Delivery rejected: Langfuse did not accept these credentials "
            f"({detail}); {emitted} turn(s) were not delivered (session={session_id}). "
            f"Check LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY."
        )
    if emitted > 0 and status == "unreachable":
        return (
            f"Delivery waiting: {detail}; {emitted} turn(s) wait to be sent "
            f"(session={session_id})."
        )
    return f"Processed {emitted} turns in {dur:.2f}s (session={session_id})"


def format_delivery_warning(status: str, detail: str, host: str) -> Optional[str]:
    """The user-visible warning for a known-bad delivery status, surfaced via
    the hook's JSON `systemMessage` field — not the log file nobody opens.

    A repair recipe, not just a diagnosis: each branch names what to change
    in the repo's .claude/settings.local.json, and ends on SETTINGS_REREAD.

    Returns None for "ok" or any status this session has no verdict on yet;
    a working (or unverified) session must stay completely silent, or the
    warning gets ignored within a day. A bad status gets a message every
    time this is called, deliberately not rate-limited like the probe that
    produced it: staying quiet between probes is exactly how the original
    bug went unnoticed for three days.
    """
    if status == "rejected" and _hostname(host) == _hostname(EU_CLOUD_HOST):
        return (
            f"Langfuse tracing is broken: credentials rejected ({detail}) — "
            "no traces are being recorded. This plugin sends to Langfuse "
            f"Cloud's EU region ({EU_CLOUD_HOST}) by default. Keys from a "
            "project in another region do not work there: you need keys from "
            "an EU project. A rotated key fails the same way.\n"
            "Fix: in this repo's .claude/settings.local.json, set "
            "LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY to the EU project's keys. "
            f"{SETTINGS_REREAD}"
        )
    if status == "rejected":
        return (
            f"Langfuse tracing is broken: credentials rejected ({detail}) — "
            "no traces are being recorded. Likely a rotated key.\n"
            "Fix: in this repo's .claude/settings.local.json, compare "
            "LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY to the current keypair "
            f"and correct them. {SETTINGS_REREAD}"
        )
    if status == "unreachable":
        return (
            f"Langfuse cannot be reached: {detail}. The turns wait on this "
            "computer and are sent once Langfuse answers again. Check LANGFUSE_BASE_URL "
            "in this repo's .claude/settings.local.json: unset defaults to "
            f"{EU_CLOUD_HOST}, and a self-hosted Langfuse needs its own "
            "address.\n"
            "Fix the value or your network/VPN access to that host. "
            f"{SETTINGS_REREAD}"
        )
    return None


def flush_and_shutdown_langfuse_client(langfuse: Optional[Langfuse]) -> None:
    if langfuse is None:
        return

    # Cap flush+shutdown at 5 s, so a stuck write to disk cannot stall Claude Code.
    try:
        def _flush_and_shutdown():
            try:
                langfuse.flush()
            except Exception:
                pass
            langfuse.shutdown()

        t = threading.Thread(target=_flush_and_shutdown, daemon=True)
        t.start()
        t.join(5.0)
    except Exception:
        pass


# ----------------- Main -----------------
def main() -> int:
    start = time.time()
    debug("Hook started")

    payload = read_hook_payload()
    is_session_start = is_session_start_hook_payload(payload)
    is_setup_check = is_setup_check_hook_payload(payload)
    checkout = find_checkout(project_folder(payload.get("cwd")), log=debug)
    try:
        config = get_langfuse_config(checkout, payload.get("cwd"))
    except RefusedHostError as e:
        info(str(e))
        if is_session_start:
            print(json.dumps({"systemMessage": str(e)}))
        elif is_setup_check:
            print(json.dumps({"decision": "block", "reason": str(e)}))
        return 0
    except SettingsFileError as e:
        info(str(e))
        if is_setup_check:
            raise
        if is_session_start:
            print(json.dumps({"systemMessage": format_broken_settings_message(e)}))
        return 0

    # The setup skill reads "no output" as "the hook cannot run", so this always answers.
    if is_setup_check:
        if config is not None:
            print(json.dumps({"decision": "block", "reason": ALREADY_CONFIGURED}))
        else:
            print(json.dumps({}))
        return 0

    # SessionStart exists only to say, once, that this session goes untraced.
    if is_session_start:
        if config is None:
            print(json.dumps({"systemMessage": format_no_keys_message(checkout)}))
        return 0

    if config is not None:
        trace_this_run(config, payload, start)
    start_waiting_turns_sender()
    return 0


def trace_this_run(config: LangfuseConfig, payload: Dict[str, Any], start: float) -> None:
    """Attaches pending /hurt notes, stores this run's new turns to be sent, and
    reports the delivery status in the log and, when it is bad, to the user."""
    # Runs on every firing, independent of this run's hook_context/payload —
    # the pending queue is global to the machine, not scoped to this session,
    # so a healthy hook run in any project can drain hurts left behind by a
    # session whose hook never runs again. Never allowed to block turn
    # emission below.
    try:
        with FileLock(HURT_PENDING_LOCK_FILE):
            attached = process_pending_hurts(config, HURT_PENDING_FILE, log=info)
        if attached:
            info(f"Attached {attached} pending /hurt annotation(s)")
    except TimeoutError as e:
        debug(f"hurt pending lock timeout, skipping: {e}")
    except Exception as e:
        debug(f"pending hurt processing failed: {e}")

    hook_context = get_session_id_and_transcript_path(payload)
    if hook_context is None:
        return

    session_id, transcript_path = hook_context
    flush_deferred_agent_turns = is_session_end_hook_payload(payload)

    langfuse = create_langfuse_client(config)
    if langfuse is None:
        return

    try:
        emitted = emit_new_turns_from_transcript(
            langfuse,
            config,
            session_id,
            transcript_path,
            flush_deferred_agent_turns=flush_deferred_agent_turns,
        )
        # Stores the turns on disk now: a SessionEnd hook can be stopped during
        # the delivery check below, and the turn position is already saved.
        langfuse.flush()
        work_item.prune(WORK_ITEMS_DIR, datetime.now(timezone.utc) - STATE_MAX_AGE, info)

        dur = time.time() - start
        # Nothing was even attempted to be sent, so "Processed 0" needs no
        # fresh delivery probe — skipping it here also keeps the probe off
        # the (common) firings that had nothing new to emit. A firing with
        # nothing to send still checks the cache (never probes) so a
        # known-bad plugin keeps warning even between turns.
        if emitted == 0:
            status, detail = peek_delivery_status(config) or ("ok", "")
        else:
            status, detail = get_delivery_status(config)
        info(format_processed_log(emitted, dur, session_id, status, detail))

        # User-visible, unlike the line above: printed on every firing while the
        # trace user cannot be read or the delivery status is bad (not
        # rate-limited with the probe that produced it), and silent otherwise.
        warnings = [w for w in (config.user_warning, format_delivery_warning(status, detail, config.host)) if w]
        if warnings:
            print(json.dumps({"systemMessage": "\n".join(warnings)}))

        return

    except TimeoutError as e:
        debug(f"lock timeout, skipping: {e}")
        return

    except Exception as e:
        debug(f"Unexpected failure: {e}")
        return

    finally:
        flush_and_shutdown_langfuse_client(langfuse)


def start_waiting_turns_sender() -> None:
    """Starts this script with SEND_WAITING_FLAG in the background, when trace data waits."""
    if not waiting_files(WAITING_DIR):
        return
    try:
        start_sender([sys.executable, str(Path(__file__).resolve()), SEND_WAITING_FLAG])
    except OSError as e:
        info(f"could not start the sender of waiting turns: {type(e).__name__}: {e}")


def send_waiting_turns_main() -> int:
    """Sends the trace data in WAITING_DIR, as the only sender on this machine."""
    try:
        with FileLock(WAITING_LOCK_FILE, timeout_s=WAITING_LOCK_TIMEOUT_S):
            sent = send_waiting_turns(WAITING_DIR, importlib.metadata.version("langfuse"), info)
    except TimeoutError as e:
        info(f"another sender kept the waiting turns busy: {e}")
        return 0
    except Exception as e:
        # The sender's stderr goes nowhere, so the log is the only place this shows.
        info(f"the sender of waiting turns stopped: {type(e).__name__}: {e}")
        return 1
    if sent:
        info(f"Sent {sent} waiting batch(es) to Langfuse")
    return 0


def work_item_command_main() -> int:
    """Runs `langfuse-work-item` with the arguments after WORK_ITEM_FLAG."""
    return work_item.run_command(
        sys.argv[2:], WORK_ITEMS_DIR, os.environ.get("CLAUDE_CODE_SESSION_ID"), datetime.now(timezone.utc)
    )


if __name__ == "__main__":
    if sys.argv[1:2] == [WORK_ITEM_FLAG]:
        sys.exit(work_item_command_main())
    sys.exit(send_waiting_turns_main() if sys.argv[1:] == [SEND_WAITING_FLAG] else main())
