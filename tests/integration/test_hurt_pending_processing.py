from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest


class FakeConfig:
    def __init__(self) -> None:
        self.public_key = "pk"
        self.secret_key = "sk"
        self.host = "https://lf.example"


class RoutedClient:
    """Stand-in for LangfuseClient, installed via monkeypatch so
    process_pending_hurts talks to it instead of the network. Routes are
    keyed by path prefix; a route may be a response, a list of responses
    consumed in order, a function of the full path, or an exception
    instance to raise."""

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
                if callable(response):
                    return response(path)
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


OBSERVATIONS_V2 = "/api/public/v2/observations"


class FakeV4Observations:
    """Answers GET /api/public/v2/observations the way a Langfuse v4 project
    does (ObservationsV2Response in Langfuse's OpenAPI spec): a `data` list
    of observation rows and a `meta` object whose `cursor` is absent when
    there is no next page. A trace counts as uploaded once it is in
    `uploaded`; its rows are only returned for the trace asked for in the
    `traceId` query parameter."""

    def __init__(self, module: Any, uploaded: set[str] | None = None) -> None:
        self._module = module
        self.uploaded = set(uploaded or ())
        self.queries: list[dict[str, list[str]]] = []

    def __call__(self, path: str) -> Any:
        query = parse_qs(urlsplit(path).query)
        self.queries.append(query)
        trace_id = query.get("traceId", [None])[0]
        rows = []
        if trace_id in self.uploaded:
            rows.append(
                {
                    "id": f"obs-{trace_id}",
                    "traceId": trace_id,
                    "startTime": "2026-10-23T10:00:00.000Z",
                    "endTime": "2026-10-23T10:00:05.000Z",
                    "projectId": "proj-1",
                    "parentObservationId": None,
                    "type": "SPAN",
                }
            )
        return self._module.HttpResponse(status=200, body={"data": rows, "meta": {}})


def full_success_routes(module: Any, client: RoutedClient, trace_id: str = "tid-123") -> FakeV4Observations:
    observations = FakeV4Observations(module, {trace_id})
    client.when_get(OBSERVATIONS_V2, observations)
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
                        "categories": [
                            {"value": float(i), "label": c} for i, c in enumerate(module.CATEGORIES)
                        ],
                    }
                ],
                "meta": {"totalPages": 1},
            },
        ),
    )
    client.when_post("/api/public/scores", module.HttpResponse(status=201, body={"id": "score-1"}))
    return observations


# ----------------- happy path -----------------

def test_on_a_v4_project_a_queued_hurt_attaches_its_score_with_the_words_and_leaves_the_queue(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """Langfuse v4 replies only: the trace is found through the v2
    Observations API, and nothing calls the legacy trace or ingestion API
    that Langfuse Cloud removes."""
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    write_pending(pending_file, [make_record(module, category="redone-work", comment="had to redo the export")])

    client = RoutedClient(module)
    full_success_routes(module, client)
    install_fake_client(module, monkeypatch, client)
    logged: list[str] = []

    attached = module.process_pending_hurts(FakeConfig(), pending_file, log=logged.append)

    assert attached == 1
    assert json.loads(pending_file.read_text(encoding="utf-8")) == []
    score_bodies = [body for method, path, body in client.calls if path == "/api/public/scores"]
    assert len(score_bodies) == 1
    assert score_bodies[0]["traceId"] == "tid-123"
    assert score_bodies[0]["name"] == "hurt"
    assert score_bodies[0]["value"] == "redone-work"
    assert score_bodies[0]["comment"] == "had to redo the export"
    paths = [path for _, path, _ in client.calls]
    assert not any(path.startswith("/api/public/traces") for path in paths)
    assert "/api/public/ingestion" not in paths
    assert "/api/public/comments" not in paths
    assert not any("failed" in line for line in logged)


def test_process_pending_hurts_looks_up_the_score_config_once_per_run(
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
    observations = full_success_routes(module, client, trace_id="tid-1")
    observations.uploaded.add("tid-2")
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 2
    config_gets = [c for c in client.calls if c[0] == "GET" and c[1].startswith("/api/public/score-configs")]
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
    observations = full_success_routes(module, client)
    observations.uploaded.clear()
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 0
    remaining = json.loads(pending_file.read_text(encoding="utf-8"))
    assert remaining == [record]
    assert not any(method == "POST" for method, _, _ in client.calls)

    # The trace arrives in the project; a later run attaches the hurt.
    observations.uploaded.add("tid-123")
    assert module.process_pending_hurts(FakeConfig(), pending_file) == 1
    assert json.loads(pending_file.read_text(encoding="utf-8")) == []


def test_a_refused_score_is_logged_and_leaves_the_record_queued_as_it_was(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    record = make_record(module)
    write_pending(pending_file, [record])

    client = RoutedClient(module)
    full_success_routes(module, client)
    client.when_post("/api/public/scores", module.HttpResponse(status=429, body=None))
    install_fake_client(module, monkeypatch, client)
    logged: list[str] = []

    attached = module.process_pending_hurts(FakeConfig(), pending_file, log=logged.append)

    assert attached == 0
    assert json.loads(pending_file.read_text(encoding="utf-8")) == [record]
    assert any("hurt score failed" in line and "HTTP 429" in line for line in logged)


def test_process_pending_hurts_logs_a_refused_score_config_and_keeps_the_record(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """A refused score-config create shows in the hook log with its status,
    instead of the hurt silently waiting until it is dropped."""
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    record = make_record(module)
    write_pending(pending_file, [record])

    client = RoutedClient(module)
    full_success_routes(module, client)
    client.when_get(
        "/api/public/score-configs",
        module.HttpResponse(status=200, body={"data": [], "meta": {"totalPages": 1}}),
    )
    client.when_post(
        "/api/public/score-configs",
        module.HttpResponse(status=400, body={"message": "Invalid request data"}),
    )
    install_fake_client(module, monkeypatch, client)
    logged: list[str] = []

    attached = module.process_pending_hurts(FakeConfig(), pending_file, log=logged.append)

    assert attached == 0
    assert json.loads(pending_file.read_text(encoding="utf-8")) == [record]
    assert any("score-config" in line and "HTTP 400" in line for line in logged)


# ----------------- a retried score replaces the one sent before -----------------

def test_retried_score_carries_the_same_id_and_timestamp(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    created = datetime.now(timezone.utc) - timedelta(days=1)
    record = make_record(module, id="hurt-abc", created=created.isoformat().replace("+00:00", "Z"))
    write_pending(pending_file, [record])
    client = RoutedClient(module)
    full_success_routes(module, client)
    client.when_post("/api/public/scores", module.HttpResponse(status=500, body=None))
    install_fake_client(module, monkeypatch, client)

    module.process_pending_hurts(FakeConfig(), pending_file)
    client.when_post("/api/public/scores", module.HttpResponse(status=200, body={"id": "hurt-abc"}))
    module.process_pending_hurts(FakeConfig(), pending_file)

    score_bodies = [body for method, path, body in client.calls if path == "/api/public/scores"]
    assert len(score_bodies) == 2
    for body in score_bodies:
        assert body["id"] == "hurt-abc"
        assert body["timestamp"] == created.isoformat()


# ----------------- hurts queued by earlier versions -----------------

@pytest.mark.parametrize(
    "done",
    [["tag", "score"], ["score"], ["tag"], ["comment"], ["tag", "comment"]],
)
def test_hurt_queued_by_an_earlier_version_gets_one_score_with_the_words_and_leaves_the_queue(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any, done: list[str]
) -> None:
    """Earlier versions recorded tag, score and trace-comment writes in
    `done`. A score already sent carried no words; it is sent again with the
    same id and timestamp, so Langfuse replaces it and the trace keeps one
    hurt score."""
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    record = make_record(module, id="hurt-old", created="2026-10-20T09:00:00Z", comment="testing 123", done=done)
    write_pending(pending_file, [record])
    client = RoutedClient(module)
    full_success_routes(module, client)
    install_fake_client(module, monkeypatch, client)

    assert module.process_pending_hurts(FakeConfig(), pending_file) == 1

    writes = [(path, body) for method, path, body in client.calls if method == "POST" and "score-configs" not in path]
    assert len(writes) == 1
    path, body = writes[0]
    assert path == "/api/public/scores"
    assert body["id"] == "hurt-old"
    assert body["timestamp"] == "2026-10-20T09:00:00+00:00"
    assert body["comment"] == "testing 123"
    assert json.loads(pending_file.read_text(encoding="utf-8")) == []


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
    client.when_get(OBSERVATIONS_V2, ConnectionError("network unreachable"))
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
    client.when_get(OBSERVATIONS_V2, FakeV4Observations(module))
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


# ----------------- a hurt belongs to the project its trace went to -----------------

def test_a_hurt_queued_for_another_project_is_not_attempted_with_these_keys(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    other = module.queue_target("https://other.example", "pk-other")
    record = make_record(module, target=other)
    write_pending(pending_file, [record])
    client = RoutedClient(module)
    full_success_routes(module, client)
    install_fake_client(module, monkeypatch, client)

    attached = module.process_pending_hurts(FakeConfig(), pending_file)

    assert attached == 0
    assert client.calls == []
    assert json.loads(pending_file.read_text(encoding="utf-8")) == [record]


def test_a_hurt_queued_for_this_project_is_attached(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    config = FakeConfig()
    write_pending(pending_file, [make_record(module, target=module.queue_target(config.host, config.public_key))])
    client = RoutedClient(module)
    full_success_routes(module, client)
    install_fake_client(module, monkeypatch, client)

    assert module.process_pending_hurts(config, pending_file) == 1


def test_an_old_hurt_queued_for_another_project_is_still_dropped_after_the_age_bound(
    hurt_annotations_module: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    module = hurt_annotations_module
    pending_file = tmp_path / "hurt_pending.json"
    old = datetime.now(timezone.utc) - timedelta(days=module.PENDING_MAX_AGE_DAYS + 1)
    write_pending(
        pending_file,
        [make_record(module, target=module.queue_target("https://other.example", "pk-other"), created=old.isoformat())],
    )
    client = RoutedClient(module)
    install_fake_client(module, monkeypatch, client)

    module.process_pending_hurts(FakeConfig(), pending_file)

    assert json.loads(pending_file.read_text(encoding="utf-8")) == []
