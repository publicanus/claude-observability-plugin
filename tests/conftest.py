from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import logging
import random
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Iterator

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "transcripts"

# The attributes of every propagate_attributes context now open, outermost first.
# Mirrors langfuse v4: every observation created inside a context carries them.
_open_propagated_attributes: list[dict[str, Any]] = []


def _install_langfuse_stubs() -> None:
    langfuse_module = types.ModuleType("langfuse")

    class Langfuse:
        @staticmethod
        def create_trace_id(*, seed: str | None = None) -> str:
            # Mirrors langfuse v4: seeded trace ids are sha256(seed)[:32].
            if seed:
                return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
            return format(random.getrandbits(128), "032x")

    @contextlib.contextmanager
    def propagate_attributes(**attributes: Any) -> Iterator[None]:
        _open_propagated_attributes.append(attributes)
        try:
            yield
        finally:
            _open_propagated_attributes.pop()

    class LangfuseOtelSpanAttributes:
        AS_ROOT = "langfuse.internal.as_root"

    langfuse_module.Langfuse = Langfuse
    langfuse_module.propagate_attributes = propagate_attributes
    sys.modules["langfuse"] = langfuse_module
    attributes_module = types.ModuleType("langfuse._client.attributes")
    attributes_module.LangfuseOtelSpanAttributes = LangfuseOtelSpanAttributes
    sys.modules["langfuse._client"] = types.ModuleType("langfuse._client")
    sys.modules["langfuse._client.attributes"] = attributes_module

    opentelemetry_module = types.ModuleType("opentelemetry")
    trace_module = types.ModuleType("opentelemetry.trace")

    @contextlib.contextmanager
    def use_span(*_: Any, **__: Any) -> Iterator[None]:
        yield

    class TraceFlags(int):
        SAMPLED = 0x01

    class SpanContext:
        def __init__(self, trace_id: int, span_id: int, is_remote: bool, trace_flags: Any = None) -> None:
            self.trace_id = trace_id
            self.span_id = span_id
            self.is_remote = is_remote
            self.trace_flags = trace_flags

    class NonRecordingSpan:
        def __init__(self, span_context: SpanContext) -> None:
            self._span_context = span_context

        def get_span_context(self) -> SpanContext:
            return self._span_context

    def set_span_in_context(span: Any, context: Any = None) -> dict[str, Any]:
        return {"current_span": span}

    trace_module.use_span = use_span
    trace_module.TraceFlags = TraceFlags
    trace_module.SpanContext = SpanContext
    trace_module.NonRecordingSpan = NonRecordingSpan
    trace_module.set_span_in_context = set_span_in_context
    opentelemetry_module.trace = trace_module
    sys.modules["opentelemetry"] = opentelemetry_module
    sys.modules["opentelemetry.trace"] = trace_module

    # The hook's WaitingTurnExporter is built on these; the stub client never calls it.
    export_module = types.ModuleType("opentelemetry.sdk.trace.export")

    class SpanExporter:
        pass

    class SpanExportResult:
        SUCCESS = "success"
        FAILURE = "failure"

    export_module.SpanExporter = SpanExporter
    export_module.SpanExportResult = SpanExportResult
    encoder_module = types.ModuleType("opentelemetry.exporter.otlp.proto.common.trace_encoder")
    encoder_module.encode_spans = lambda spans: None
    for name in (
        "opentelemetry.sdk",
        "opentelemetry.sdk.trace",
        "opentelemetry.exporter",
        "opentelemetry.exporter.otlp",
        "opentelemetry.exporter.otlp.proto",
        "opentelemetry.exporter.otlp.proto.common",
    ):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["opentelemetry.sdk.trace.export"] = export_module
    sys.modules["opentelemetry.exporter.otlp.proto.common.trace_encoder"] = encoder_module


def load_hook_module() -> Any:
    """Loads hooks/langfuse_hook.py with langfuse and opentelemetry stubbed out.

    Replaces sys.modules["langfuse"], ["langfuse._client.attributes"], ["opentelemetry"]
    and ["opentelemetry.trace"] with stubs, and registers the hook as sys.modules["langfuse_hook_under_test"].
    """
    _install_langfuse_stubs()
    module_path = REPO_ROOT / "hooks" / "langfuse_hook.py"
    spec = importlib.util.spec_from_file_location("langfuse_hook_under_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_settings(checkout: Path, env: dict) -> Path:
    """Writes {"env": env} as checkout's .claude/settings.local.json; returns its path."""
    path = checkout / ".claude" / "settings.local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"env": env}), encoding="utf-8")
    return path


def git(cwd: Path, *args: str) -> None:
    """Runs git with args in cwd, as a test user; raises when git fails."""
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with one commit, at tmp_path/shop."""
    path = (tmp_path / "shop").resolve()
    path.mkdir()
    git(path, "init", "-q")
    git(path, "commit", "-q", "--allow-empty", "-m", "first")
    return path


@pytest.fixture
def worktree(repo: Path, tmp_path: Path) -> Path:
    """A worktree of repo, at tmp_path/shop-feature."""
    path = (tmp_path / "shop-feature").resolve()
    git(repo, "worktree", "add", "-q", str(path))
    return path


def config_for(hook_module: Any, folder: Any) -> Any:
    """get_langfuse_config for a session in folder (None: the working directory)."""
    from repo_checkout import session_dir  # not at the top: state_writer.py imports this file without hooks/ on its path

    cwd = str(folder) if folder is not None else None
    return hook_module.get_langfuse_config(hook_module.find_checkout(session_dir(cwd)), cwd)


@pytest.fixture(scope="session")
def hook_module() -> Any:
    return load_hook_module()


# The names the keys and the Langfuse address go by, in a settings file or elsewhere.
LANGFUSE_CONFIG_VARS = [
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "CC_LANGFUSE_PUBLIC_KEY",
    "CC_LANGFUSE_SECRET_KEY",
    "CC_LANGFUSE_BASE_URL",
]


@pytest.fixture
def no_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removes every LANGFUSE_CONFIG_VARS name, plain and as CLAUDE_PLUGIN_OPTION_<name>,
    from the environment."""
    for name in LANGFUSE_CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{name}", raising=False)


@pytest.fixture
def nothing_may_be_sent(hook_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every path in main() that talks to Langfuse fails the test when reached."""
    monkeypatch.setattr(hook_module, "create_langfuse_client", lambda config: pytest.fail("trace client built"))
    monkeypatch.setattr(hook_module, "LangfuseClient", lambda *a, **k: pytest.fail("delivery probe sent"))
    monkeypatch.setattr(hook_module, "process_pending_hurts", lambda *a, **k: pytest.fail("/hurt notes sent"))


class AcceptingLangfuse:
    """Answers the delivery probe as a Langfuse that accepts the keys."""

    def get(self, path: str) -> Any:
        return type("Response", (), {"status": 200, "body": None})()


def run_main(hook_module: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, payload: dict) -> str:
    """Runs the hook's main() on payload; asserts it returns 0 and returns what it printed."""
    monkeypatch.setattr(hook_module, "read_hook_payload", lambda: payload)
    assert hook_module.main() == 0
    return capsys.readouterr().out


def write_one_turn_transcript(tmp_path: Path) -> Path:
    """Writes tmp_path/transcript.jsonl with one turn of session "sess"; returns its path."""
    rows = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "sessionId": "sess",
            "uuid": "user-1",
            "message": {"role": "user", "content": "A question."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "sessionId": "sess",
            "uuid": "assistant-1",
            "message": {
                "id": "msg-1",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "An answer."}],
            },
        },
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return transcript


@pytest.fixture
def fixture_transcript_path() -> Any:
    def _path(name: str) -> Path:
        return FIXTURE_ROOT / name / "transcript.jsonl"

    return _path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def read_fixture_jsonl() -> Any:
    return read_jsonl


class FakeSpanContext:
    def __init__(self, trace_id: int) -> None:
        self.trace_id = trace_id


class FakeOtelSpan:
    def __init__(self, name: str, start_time: int | None, context: Any = None) -> None:
        self.name = name
        self.start_time = start_time
        self.context = context
        # Simulates what a real (unforced) OTel span gets from its provider:
        # a random 128-bit trace id, only used when no remote parent context
        # carries a forced one.
        self._auto_span_context = FakeSpanContext(random.getrandbits(128))
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def get_span_context(self) -> FakeSpanContext:
        if self.context is not None:
            return self.context["current_span"].get_span_context()
        return self._auto_span_context


class FakeTracer:
    def start_span(self, *, name: str, start_time: int | None = None, context: Any = None) -> FakeOtelSpan:
        return FakeOtelSpan(name, start_time, context)


class FakeObservation:
    def __init__(self, otel_span: FakeOtelSpan, as_type: str, kwargs: dict[str, Any]) -> None:
        self._otel_span = otel_span
        self.name = otel_span.name
        self.as_type = as_type
        self.kwargs = kwargs
        self.output: Any = None
        self.end_time: int | None = None
        self.propagated: dict[str, Any] = {}
        for attributes in _open_propagated_attributes:
            self.propagated.update(attributes)
        # Mirrors Langfuse._get_otel_trace_id: format(trace_id_int, "032x").
        self.trace_id = format(otel_span.get_span_context().trace_id, "032x")

    def update(self, **kwargs: Any) -> None:
        if "output" in kwargs:
            self.output = kwargs["output"]
        self.kwargs.update(kwargs)

    def end(self, *, end_time: int | None = None) -> None:
        self.end_time = end_time


class FakeLangfuse:
    def __init__(self) -> None:
        self._otel_tracer = FakeTracer()
        self.observations: list[FakeObservation] = []

    def flush(self) -> None:
        # No-op: real flush()'s (lack of a) return signal is exactly what
        # the delivery-health tests exist to route around, not exercise here.
        pass

    def shutdown(self) -> None:
        pass

    def _create_observation_from_otel_span(
        self,
        *,
        otel_span: FakeOtelSpan,
        as_type: str,
        # Named params mirror langfuse-python 4.14's fixed signature (no
        # **kwargs catch-all there) so a misspelled or renamed kwarg fails
        # the test the same way it would fail against the real SDK.
        input: Any = None,
        output: Any = None,
        metadata: Any = None,
        version: str | None = None,
        level: str | None = None,
        status_message: str | None = None,
        completion_start_time: Any = None,
        model: str | None = None,
        model_parameters: Any = None,
        usage_details: Any = None,
        cost_details: Any = None,
        prompt: Any = None,
    ) -> FakeObservation:
        kwargs = {
            "input": input,
            "output": output,
            "metadata": metadata,
            "version": version,
            "level": level,
            "status_message": status_message,
            "completion_start_time": completion_start_time,
            "model": model,
            "model_parameters": model_parameters,
            "usage_details": usage_details,
            "cost_details": cost_details,
            "prompt": prompt,
        }
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        observation = FakeObservation(otel_span, as_type, kwargs)
        self.observations.append(observation)
        return observation


@pytest.fixture
def fake_langfuse() -> FakeLangfuse:
    return FakeLangfuse()


@pytest.fixture
def isolated_hook_state(tmp_path: Path, hook_module: Any, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_dir = tmp_path / "claude-state"
    monkeypatch.setattr(hook_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(hook_module, "STATE_FILE", state_dir / "langfuse_state.json")
    monkeypatch.setattr(hook_module, "LOCK_FILE", state_dir / "langfuse_state.lock")
    monkeypatch.setattr(hook_module, "LOG_FILE", state_dir / "langfuse_hook.log")
    monkeypatch.setattr(hook_module, "HURT_PENDING_FILE", state_dir / "hurt_pending.json")
    monkeypatch.setattr(hook_module, "HURT_PENDING_LOCK_FILE", state_dir / "hurt_pending.lock")
    monkeypatch.setattr(hook_module, "WAITING_DIR", state_dir / "langfuse_waiting")
    monkeypatch.setattr(hook_module, "WAITING_LOCK_FILE", state_dir / "langfuse_waiting.lock")
    monkeypatch.setattr(hook_module, "WORK_ITEMS_DIR", state_dir / "langfuse_work_items")
    # The trace-user lookup reads ~/.claude.json and git config, and the plugin
    # versions come from Claude Code's installed_plugins.json; keep all off the
    # real machine. The chdir covers a payload without a cwd, where git
    # runs in the hook's own directory.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(hook_module, "CLAUDE_JSON_FILE", home / ".claude.json")
    monkeypatch.setattr(
        hook_module, "INSTALLED_PLUGINS_FILE", home / ".claude" / "plugins" / "installed_plugins.json"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(home)
    # _get_logger() caches its handler in this module-level global on first
    # use, and logging.getLogger("langfuse_hook") is itself a process-wide
    # singleton keyed by name — resetting only the module global still
    # leaves the old RotatingFileHandler (pointed at a previous test's, by
    # then deleted, LOG_FILE) attached. hook_module is session-scoped, so
    # both must be cleared for every test to log to its own LOG_FILE.
    monkeypatch.setattr(hook_module, "_logger", None)
    logging.getLogger("langfuse_hook").handlers.clear()
    return state_dir


@pytest.fixture
def hurt_annotations_module() -> Any:
    module_path = REPO_ROOT / "hooks" / "hurt_annotations.py"
    spec = importlib.util.spec_from_file_location("hurt_annotations_under_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
