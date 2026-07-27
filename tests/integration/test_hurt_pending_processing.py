from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class FakeConfig:
    def __init__(self) -> None:
        self.public_key = "pk"
        self.secret_key = "sk"
        self.host = "https://lf.example"


class RoutedClient:
    """Stand-in for LangfuseClient, installed via monkeypatch so
    process_pending_hurts talks to it instead of the network. Routes are
    keyed by exact path; a route may be a response, a list of responses
    consumed in order, or an exception instance to raise."""

    def __init__(self, module: Any) -> None:
        self._module = module
        self.calls: list[tuple[str, str, dict | None]] = []
        self._get_routes: dict[str, Any] = {}
        self._post_routes: dict[str, Any] = {}

    def when_get(self, path: str, response: Any) -> None:
        self._get_routes[path] = response

    def when_post(self, path: str, response: Any) -> None:
        self._post_routes[path] = response

    def _resolve(self, routes: dict[str, Any], path: str) -> Any:
        for prefix, response in routes.items():
            if path == prefix or path.startswith(prefix):
                if isinstance(response, list):
                    return response.pop(0) if response else self._module.HttpResponse(status=404, body=None)
                return response
        return self._module.HttpResponse(status=404, body=None)

    def get(self, path: str) -> Any:
        self.calls.append(("GET", path, None))
        result = self._resolve(self._get_routes, path)
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, path: str, body: dict) -> Any:
        self.calls.append(("POST", path, body))
        result = self._resolve(self._post_routes, path)
        if isinstance(result, Exception):
            raise result
        return result


def install_fake_client(module: Any, monkeypatch: Any, client: RoutedClient) -> None:
    monkeypatch.setattr(module, "LangfuseClient", lambda config: client)


def write_pending(pending_file: Path, records: list[dict]) -> None:
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.write_text(json.dumps(records), encoding="utf-8")


def make_record(module: Any, **overrides: Any) -> dict:
    record = {
        "id": "hurt-1",
        "trace_id": "tid-123",
        "category": "stalled-loop",
        "comment": "went in circles",
        "created": datetime.now(timezone.utc).isoformat(),
        "session_id": "sess-1",
    }
    record.update(overrides)
    return record


def full_success_routes(module: Any, client: RoutedClient, trace_id: str = "tid-123") -> None:
    client.when_get(f"/api/public/traces/{trace_id}", module.HttpResponse(status=200, body={"id": trace_id}))
    client.when_get("/api/public/projects", module.HttpResponse(status=200, body={"data": [{"id": "proj-1"}]}))
    client.when_get(
        "/api/public/score-configs",
        module.HttpResponse(
            status=200,
            body={
                "data": [
                    {
                        "id": "cfg-1",
                        "name": "hurt",
                        "dataType": "CATEGORICAL",
                        "categories": [{"value": c, "label": c} for c in module.CATEGORIES],
                    }
                ],
                "meta": {"totalPages": 1},
            },
        ),
    )
    client.when_post("/api/public/ingestion", module.HttpResponse(status=200, body={}))
    client.when_post("/api/public/scores", module.HttpResponse(status=201, body={"id": "score-1"}))
    client.when_post("/api/public/comments", module.HttpResponse(status=200, body={"id": "comment-1"}))


# ----------------- happy path -----------------

def test_process_pending_hurts_attaches_and_removes_a_complete_record(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    write_pending(pending_file, [make_record(module)])

    client = RoutedClient(module)
    full_success_routes(module, client)
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 1
    assert json.loads(pending_file.read_text(encoding="utf-8")) == []


def test_process_pending_hurts_caches_project_and_config_across_records(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    write_pending(
        pending_file,
        [
            make_record(module, id="hurt-1", trace_id="tid-1"),
            make_record(module, id="hurt-2", trace_id="tid-2"),
        ],
    )

    client = RoutedClient(module)
    full_success_routes(module, client, trace_id="tid-1")
    client.when_get("/api/public/traces/tid-2", module.HttpResponse(status=200, body={"id": "tid-2"}))
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 2
    project_gets = [c for c in client.calls if c[0] == "GET" and c[1] == "/api/public/projects"]
    config_gets = [c for c in client.calls if c[0] == "GET" and c[1].startswith("/api/public/score-configs")]
    assert len(project_gets) == 1
    assert len(config_gets) == 1


# ----------------- never lost: stays queued on any failure -----------------

def test_process_pending_hurts_keeps_record_queued_when_trace_not_yet_uploaded(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    record = make_record(module)
    write_pending(pending_file, [record])

    client = RoutedClient(module)
    client.when_get("/api/public/traces/tid-123", module.HttpResponse(status=404, body=None))
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 0
    remaining = json.loads(pending_file.read_text(encoding="utf-8"))
    assert remaining == [record]
    assert not any(method == "POST" for method, _, _ in client.calls)


def test_process_pending_hurts_keeps_record_queued_on_partial_write_failure(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    record = make_record(module)
    write_pending(pending_file, [record])

    client = RoutedClient(module)
    full_success_routes(module, client)
    client.when_post("/api/public/scores", module.HttpResponse(status=500, body=None))
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 0
    assert json.loads(pending_file.read_text(encoding="utf-8")) == [record]


def test_process_pending_hurts_keeps_record_queued_when_client_raises(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """Langfuse being unreachable (network error) must never lose the hurt
    or crash the caller — it stays queued for the next hook run."""
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    record = make_record(module)
    write_pending(pending_file, [record])

    client = RoutedClient(module)
    client.when_get("/api/public/traces/tid-123", ConnectionError("network unreachable"))
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 0
    assert json.loads(pending_file.read_text(encoding="utf-8")) == [record]


def test_process_pending_hurts_never_raises_when_pending_file_is_corrupt(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    pending_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(module, "LangfuseClient", lambda config: RoutedClient(module))

    assert module.process_pending_hurts(FakeConfig(), pending_file) == 0


# ----------------- bounded retry -----------------

def test_process_pending_hurts_drops_malformed_record(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    write_pending(pending_file, [{"id": "broken", "category": "other"}])  # no trace_id/comment
    monkeypatch.setattr(module, "LangfuseClient", lambda config: RoutedClient(module))

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 0
    assert json.loads(pending_file.read_text(encoding="utf-8")) == []


def test_process_pending_hurts_drops_expired_unresolvable_record(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    old_created = (datetime.now(timezone.utc) - timedelta(days=module.PENDING_MAX_AGE_DAYS + 1)).isoformat()
    record = make_record(module, created=old_created)
    write_pending(pending_file, [record])

    client = RoutedClient(module)
    client.when_get("/api/public/traces/tid-123", module.HttpResponse(status=404, body=None))
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 0
    assert json.loads(pending_file.read_text(encoding="utf-8")) == []


def test_process_pending_hurts_still_attaches_an_old_record_once_resolvable(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """Age alone never drops a record — only age combined with still being
    unresolvable does. A trace that finally uploaded is still attached."""
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    old_created = (datetime.now(timezone.utc) - timedelta(days=module.PENDING_MAX_AGE_DAYS + 1)).isoformat()
    write_pending(pending_file, [make_record(module, created=old_created)])

    client = RoutedClient(module)
    full_success_routes(module, client)
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 1
    assert json.loads(pending_file.read_text(encoding="utf-8")) == []


# ----------------- no-op when queue is empty -----------------

def test_process_pending_hurts_no_op_on_missing_file(
    hurt_annotations_module: Any, tmp_path: Path
) -> None:
    module = hurt_annotations_module
    assert module.process_pending_hurts(FakeConfig(), tmp_path / "missing.json") == 0
