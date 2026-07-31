from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.body = None


class FakeDeliveryClient:
    """Stand-in for hurt_annotations.LangfuseClient: returns a canned
    response to every GET instead of making a real request."""

    calls: list[str] = []

    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    def get(self, path: str) -> FakeResponse:
        FakeDeliveryClient.calls.append(path)
        return self._response


def install_fake_delivery_client(hook_module: Any, monkeypatch: Any, response: FakeResponse) -> None:
    FakeDeliveryClient.calls = []
    monkeypatch.setattr(
        hook_module,
        "LangfuseClient",
        lambda config, timeout=30: FakeDeliveryClient(response),
    )


def make_config(hook_module: Any) -> Any:
    return hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")


# ----------------- classify_delivery_health -----------------

def test_classify_delivery_health_ok_on_200(hook_module, monkeypatch):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(200))

    status, detail = hook_module.classify_delivery_health(make_config(hook_module))

    assert status == "ok"
    assert detail == ""
    assert FakeDeliveryClient.calls == ["/api/public/projects"]


def test_classify_delivery_health_rejected_on_401(hook_module, monkeypatch):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(401))

    status, detail = hook_module.classify_delivery_health(make_config(hook_module))

    assert status == "rejected"
    assert "401" in detail
    assert "example.test" in detail


def test_classify_delivery_health_rejected_on_403(hook_module, monkeypatch):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(403))

    status, detail = hook_module.classify_delivery_health(make_config(hook_module))

    assert status == "rejected"
    assert "403" in detail


def test_classify_delivery_health_unreachable_on_no_response(hook_module, monkeypatch):
    # LangfuseClient._request reports every network-level failure (DNS,
    # connection refused, timeout) as status 0 — it never raises.
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(0))

    status, detail = hook_module.classify_delivery_health(make_config(hook_module))

    assert status == "unreachable"
    assert "did not respond" in detail


def test_classify_delivery_health_unreachable_on_other_status(hook_module, monkeypatch):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(500))

    status, detail = hook_module.classify_delivery_health(make_config(hook_module))

    assert status == "unreachable"
    assert "500" in detail


# ----------------- get_delivery_status caching -----------------

def test_get_delivery_status_caches_within_the_interval(hook_module, isolated_hook_state, monkeypatch):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(200))
    config = make_config(hook_module)

    first = hook_module.get_delivery_status(config)
    second = hook_module.get_delivery_status(config)

    assert first == ("ok", "")
    assert second == ("ok", "")
    assert len(FakeDeliveryClient.calls) == 1


def test_get_delivery_status_rechecks_when_credentials_change(hook_module, isolated_hook_state, monkeypatch):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(200))
    config_a = hook_module.LangfuseConfig("public-a", "secret-a", "https://example.test", "user-1")
    config_b = hook_module.LangfuseConfig("public-b", "secret-b", "https://example.test", "user-1")

    hook_module.get_delivery_status(config_a)
    hook_module.get_delivery_status(config_b)

    # A different keypair is a different fingerprint: cached "ok" for the
    # old key must never vouch for a rotated one.
    assert len(FakeDeliveryClient.calls) == 2


def test_get_delivery_status_rechecks_once_the_interval_has_elapsed(hook_module, isolated_hook_state, monkeypatch):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(200))
    config = make_config(hook_module)

    hook_module.get_delivery_status(config)

    # Backdate the cached check past the re-verification interval, as if a
    # long-lived session's last probe happened well over 15 minutes ago.
    state_file = isolated_hook_state / "langfuse_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    stale = datetime.now(timezone.utc) - hook_module.DELIVERY_HEALTH_CHECK_INTERVAL - timedelta(seconds=1)
    state[hook_module.DELIVERY_HEALTH_STATE_KEY]["updated"] = stale.isoformat()
    state_file.write_text(json.dumps(state), encoding="utf-8")

    hook_module.get_delivery_status(config)

    assert len(FakeDeliveryClient.calls) == 2


# ----------------- peek_delivery_status: cache-only, never probes -----------------

def test_peek_delivery_status_returns_none_with_no_cache(hook_module, isolated_hook_state, monkeypatch):
    config = make_config(hook_module)

    assert hook_module.peek_delivery_status(config) is None


def test_peek_delivery_status_never_calls_the_network(hook_module, isolated_hook_state, monkeypatch):
    monkeypatch.setattr(
        hook_module,
        "LangfuseClient",
        lambda config, timeout=30: (_ for _ in ()).throw(AssertionError("peek must never probe")),
    )
    config = make_config(hook_module)

    assert hook_module.peek_delivery_status(config) is None


def test_peek_delivery_status_returns_a_verdict_however_stale(hook_module, isolated_hook_state, monkeypatch):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(401))
    config = make_config(hook_module)
    hook_module.get_delivery_status(config)

    # Backdate well past the probe's own re-verification interval — peek
    # must still report it, unlike get_delivery_status.
    state_file = isolated_hook_state / "langfuse_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    stale = datetime.now(timezone.utc) - hook_module.DELIVERY_HEALTH_CHECK_INTERVAL * 10
    state[hook_module.DELIVERY_HEALTH_STATE_KEY]["updated"] = stale.isoformat()
    state_file.write_text(json.dumps(state), encoding="utf-8")

    status, detail = hook_module.peek_delivery_status(config)

    assert status == "rejected"
    assert "401" in detail


def test_peek_delivery_status_ignores_a_verdict_for_different_credentials(hook_module, isolated_hook_state, monkeypatch):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(401))
    hook_module.get_delivery_status(hook_module.LangfuseConfig("old-pub", "old-secret", "https://example.test", "user-1"))

    new_config = hook_module.LangfuseConfig("new-pub", "new-secret", "https://example.test", "user-1")

    assert hook_module.peek_delivery_status(new_config) is None


# ----------------- format_processed_log: what the log says -----------------

def test_log_when_nothing_was_emitted(hook_module):
    line = hook_module.format_processed_log(0, 0.01, "sess-1", "rejected", "HTTP 401 from https://example.test")

    # Nothing was even attempted, so no delivery claim either way is made.
    assert line == "Processed 0 turns in 0.01s (session=sess-1)"


def test_log_on_confirmed_delivery(hook_module):
    line = hook_module.format_processed_log(2, 0.05, "sess-1", "ok", "")

    assert line == "Processed 2 turns in 0.05s (session=sess-1)"


def test_log_on_authentication_rejection(hook_module):
    line = hook_module.format_processed_log(1, 0.05, "sess-1", "rejected", "HTTP 401 from https://example.test")

    assert line.startswith("Delivery rejected:")
    assert "not delivered" in line
    assert "1 turn(s)" in line
    assert "session=sess-1" in line
    assert "LANGFUSE_PUBLIC_KEY" in line and "LANGFUSE_SECRET_KEY" in line
    # Never claims turns were processed when nothing was delivered.
    assert "Processed" not in line


def test_log_on_unreachable_host(hook_module):
    line = hook_module.format_processed_log(3, 0.05, "sess-1", "unreachable", "https://example.test did not respond")

    assert line.startswith("Delivery failed:")
    assert "did not respond" in line
    assert "3 turn(s)" in line
    assert "session=sess-1" in line
    assert "Processed" not in line


# ----------------- format_delivery_warning: what the user is shown -----------------

def test_warning_is_silent_when_healthy(hook_module):
    assert hook_module.format_delivery_warning("ok", "") is None


def test_warning_is_silent_with_no_verdict_yet(hook_module):
    # peek_delivery_status's "no cache" case is represented the same way
    # main() treats it: an "ok" default, not a distinct unknown status.
    assert hook_module.format_delivery_warning("ok", "") is None


def test_warning_on_authentication_rejection_is_unambiguous_and_names_the_fix(hook_module):
    warning = hook_module.format_delivery_warning("rejected", "HTTP 401 from https://example.test")

    assert "no traces are being recorded" in warning
    assert "HTTP 401 from https://example.test" in warning
    assert "rotated key" in warning
    assert "LANGFUSE_PUBLIC_KEY" in warning and "LANGFUSE_SECRET_KEY" in warning
    assert "new terminal session" in warning


def test_warning_on_unreachable_host_is_unambiguous_and_names_the_fix(hook_module):
    warning = hook_module.format_delivery_warning("unreachable", "https://example.test did not respond")

    assert "no traces are being recorded" in warning
    assert "https://example.test did not respond" in warning
    assert "LANGFUSE_BASE_URL" in warning


# ----------------- end-to-end through main(): the actual log line written -----------------

def write_one_turn_transcript(tmp_path: Path, session_id: str) -> Path:
    rows = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "sessionId": session_id,
            "uuid": "user-1",
            "message": {"role": "user", "content": "A question."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "sessionId": session_id,
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


def write_empty_transcript(tmp_path: Path) -> Path:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    return transcript


def run_main(
    hook_module: Any,
    monkeypatch: Any,
    capsys: Any,
    fake_langfuse: Any,
    session_id: str,
    transcript: Path,
    *,
    hook_event_name: str = "Stop",
) -> tuple[str, Optional[dict]]:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://example.test")
    monkeypatch.setattr(
        hook_module,
        "read_hook_payload",
        lambda: {
            "hook_event_name": hook_event_name,
            "sessionId": session_id,
            "transcriptPath": str(transcript),
        },
    )
    monkeypatch.setattr(hook_module, "create_langfuse_client", lambda config: fake_langfuse)

    rc = hook_module.main()

    assert rc == 0
    log_contents = (hook_module.LOG_FILE).read_text(encoding="utf-8")
    stdout = capsys.readouterr().out
    system_message = json.loads(stdout) if stdout.strip() else None
    return log_contents, system_message


def test_main_logs_processed_when_delivery_is_confirmed_healthy(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, capsys, tmp_path
):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(200))
    transcript = write_one_turn_transcript(tmp_path, "sess-healthy")

    log_contents, system_message = run_main(hook_module, monkeypatch, capsys, fake_langfuse, "sess-healthy", transcript)

    assert "Processed 1 turns" in log_contents
    assert "Delivery rejected" not in log_contents
    assert "Delivery failed" not in log_contents
    # A working session prints nothing at all — no stray systemMessage.
    assert system_message is None


def test_main_logs_rejection_instead_of_processed_on_bad_credentials(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, capsys, tmp_path
):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(401))
    transcript = write_one_turn_transcript(tmp_path, "sess-rejected")

    log_contents, system_message = run_main(hook_module, monkeypatch, capsys, fake_langfuse, "sess-rejected", transcript)

    assert "Delivery rejected" in log_contents
    assert "1 turn(s) were not delivered" in log_contents
    assert "Processed 1 turns" not in log_contents

    assert system_message is not None
    warning = system_message["systemMessage"]
    assert "no traces are being recorded" in warning
    assert "LANGFUSE_PUBLIC_KEY" in warning and "LANGFUSE_SECRET_KEY" in warning
    assert "rotated key" in warning


def test_main_logs_unreachable_instead_of_processed_on_dead_host(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, capsys, tmp_path
):
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(0))
    transcript = write_one_turn_transcript(tmp_path, "sess-unreachable")

    log_contents, system_message = run_main(hook_module, monkeypatch, capsys, fake_langfuse, "sess-unreachable", transcript)

    assert "Delivery failed" in log_contents
    assert "did not respond" in log_contents
    assert "1 turn(s) were not delivered" in log_contents
    assert "Processed 1 turns" not in log_contents

    assert system_message is not None
    warning = system_message["systemMessage"]
    assert "no traces are being recorded" in warning
    assert "LANGFUSE_BASE_URL" in warning


def test_main_warns_on_session_end_too(hook_module, isolated_hook_state, fake_langfuse, monkeypatch, capsys, tmp_path):
    """The operator asked for a warning on every hook firing; SessionEnd
    fires this same hook just as Stop does, and must not be a blind spot."""
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(401))
    transcript = write_one_turn_transcript(tmp_path, "sess-end-rejected")

    _, system_message = run_main(
        hook_module, monkeypatch, capsys, fake_langfuse, "sess-end-rejected", transcript,
        hook_event_name="SessionEnd",
    )

    assert system_message is not None
    assert "no traces are being recorded" in system_message["systemMessage"]


def test_main_warns_every_firing_even_with_nothing_new_to_send(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, capsys, tmp_path
):
    """A rejected keypair must keep warning on firings with nothing new to
    emit too — silence there is exactly how the original bug went unnoticed
    between the (rare) firings that actually re-probe."""
    install_fake_delivery_client(hook_module, monkeypatch, FakeResponse(401))
    transcript = write_one_turn_transcript(tmp_path, "sess-repeat-rejected")

    run_main(hook_module, monkeypatch, capsys, fake_langfuse, "sess-repeat-rejected", transcript)
    assert len(FakeDeliveryClient.calls) == 1

    # Second firing on the same (already fully consumed) transcript: nothing
    # new to emit, so the probe must not run again — only the cache is used.
    _, system_message = run_main(hook_module, monkeypatch, capsys, fake_langfuse, "sess-repeat-rejected", transcript)

    assert len(FakeDeliveryClient.calls) == 1
    assert system_message is not None
    assert "no traces are being recorded" in system_message["systemMessage"]


def test_main_stays_silent_with_nothing_new_and_no_verdict_yet(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, capsys, tmp_path
):
    """A brand new session with nothing to emit yet has no verdict on the
    plugin at all — it must not probe, and must not warn speculatively."""
    transcript = write_empty_transcript(tmp_path)
    monkeypatch.setattr(
        hook_module,
        "LangfuseClient",
        lambda config, timeout=30: (_ for _ in ()).throw(AssertionError("must not probe when nothing was emitted and no cache exists")),
    )

    _, system_message = run_main(hook_module, monkeypatch, capsys, fake_langfuse, "sess-fresh-empty", transcript)

    assert system_message is None
