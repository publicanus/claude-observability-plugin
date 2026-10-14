from __future__ import annotations

from typing import Any


# ----------------- helpers -----------------

class FakeClient:
    """Stand-in for LangfuseClient. Routes are registered as
    (method, path-or-prefix) -> HttpResponse; calls are recorded so tests can
    assert on exact payloads sent."""

    def __init__(self, module: Any) -> None:
        self._module = module
        self.calls: list[tuple[str, str, dict | None]] = []
        self._get_routes: dict[str, Any] = {}
        self._post_routes: dict[str, Any] = {}

    def when_get(self, path: str, response: Any) -> None:
        self._get_routes[path] = response

    def when_post(self, path: str, response: Any) -> None:
        self._post_routes[path] = response

    def get(self, path: str) -> Any:
        self.calls.append(("GET", path, None))
        for prefix, response in self._get_routes.items():
            if path == prefix or path.startswith(prefix):
                return response
        return self._module.HttpResponse(status=404, body=None)

    def post(self, path: str, body: dict) -> Any:
        self.calls.append(("POST", path, body))
        return self._post_routes.get(path, self._module.HttpResponse(status=404, body=None))


def score_config_page(module: Any, categories=None, is_archived: bool = False, data_type: str = "CATEGORICAL"):
    cats = categories if categories is not None else list(module.CATEGORIES)
    return module.HttpResponse(
        status=200,
        body={
            "data": [
                {
                    "id": "cfg-existing",
                    "name": "hurt",
                    "dataType": data_type,
                    "isArchived": is_archived,
                    # Langfuse's shape: a numeric value and a string label.
                    "categories": [{"value": float(i), "label": c} for i, c in enumerate(cats)],
                }
            ],
            "meta": {"totalPages": 1},
        },
    )


# ----------------- trace_exists -----------------

def test_trace_exists_false_on_404(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_get("/api/public/traces/tid", module.HttpResponse(status=404, body=None))
    assert module.trace_exists(client, "tid") is False


def test_trace_exists_true_on_200(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_get("/api/public/traces/tid", module.HttpResponse(status=200, body={"id": "tid"}))
    assert module.trace_exists(client, "tid") is True


# ----------------- score-config idempotency -----------------

def test_ensure_score_config_reuses_existing(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_get("/api/public/score-configs", score_config_page(module))

    config_id = module.ensure_score_config(client)

    assert config_id == "cfg-existing"
    assert not any(method == "POST" for method, _, _ in client.calls)


def test_ensure_score_config_creates_when_absent(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_get(
        "/api/public/score-configs",
        module.HttpResponse(status=200, body={"data": [], "meta": {"totalPages": 1}}),
    )
    client.when_post("/api/public/score-configs", module.HttpResponse(status=200, body={"id": "cfg-new"}))

    config_id = module.ensure_score_config(client)

    assert config_id == "cfg-new"
    post_calls = [call for call in client.calls if call[0] == "POST"]
    assert len(post_calls) == 1
    _, _, body = post_calls[0]
    assert body["name"] == "hurt"
    assert body["dataType"] == "CATEGORICAL"
    assert body["categories"] == [
        {"value": i, "label": c} for i, c in enumerate(module.CATEGORIES)
    ]
    # Langfuse rejects a category whose value is not a number.
    assert all(isinstance(c["value"], (int, float)) for c in body["categories"])


def test_ensure_score_config_matches_on_labels_not_values(hurt_annotations_module: Any) -> None:
    """A config whose labels are the hurt categories is reused whatever
    numbers Langfuse holds as values."""
    module = hurt_annotations_module
    client = FakeClient(module)
    page = score_config_page(module)
    for offset, category in enumerate(page.body["data"][0]["categories"]):
        category["value"] = 10.0 + offset
    client.when_get("/api/public/score-configs", page)

    assert module.ensure_score_config(client) == "cfg-existing"
    assert not any(method == "POST" for method, _, _ in client.calls)


def test_ensure_score_config_ignores_config_with_other_labels(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_get("/api/public/score-configs", score_config_page(module, categories=["a", "b"]))
    client.when_post("/api/public/score-configs", module.HttpResponse(status=200, body={"id": "cfg-new"}))

    assert module.ensure_score_config(client) == "cfg-new"


def test_ensure_score_config_logs_status_when_create_is_refused(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_get(
        "/api/public/score-configs",
        module.HttpResponse(status=200, body={"data": [], "meta": {"totalPages": 1}}),
    )
    client.when_post(
        "/api/public/score-configs",
        module.HttpResponse(status=400, body={"message": "Invalid request data"}),
    )
    logged: list[str] = []

    assert module.ensure_score_config(client, logged.append) is None
    assert len(logged) == 1
    assert "score-config" in logged[0]
    assert "HTTP 400" in logged[0]
    assert "Invalid request data" in logged[0]


def test_ensure_score_config_ignores_non_matching_existing_config(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_get(
        "/api/public/score-configs",
        score_config_page(module, categories=[], data_type="NUMERIC"),
    )
    client.when_post("/api/public/score-configs", module.HttpResponse(status=200, body={"id": "cfg-new"}))

    assert module.ensure_score_config(client) == "cfg-new"


def test_ensure_score_config_ignores_archived_matching_config(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_get("/api/public/score-configs", score_config_page(module, is_archived=True))
    client.when_post("/api/public/score-configs", module.HttpResponse(status=200, body={"id": "cfg-new"}))

    assert module.ensure_score_config(client) == "cfg-new"


# ----------------- individual writes -----------------

def test_tag_trace_sends_expected_ingestion_event(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_post("/api/public/ingestion", module.HttpResponse(status=200, body={}))

    assert module.tag_trace(client, "tid-123") is True
    _, _, body = client.calls[0]
    event = body["batch"][0]
    assert event["type"] == "trace-create"
    assert event["body"] == {"id": "tid-123", "tags": ["hurt"]}


def test_post_score_sends_expected_payload(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_post("/api/public/scores", module.HttpResponse(status=201, body={"id": "score-1"}))

    assert module.post_score(client, "tid-123", "stalled-loop", "cfg-1") is True
    _, _, body = client.calls[0]
    assert body == {
        "traceId": "tid-123",
        "name": "hurt",
        "dataType": "CATEGORICAL",
        "value": "stalled-loop",
        "configId": "cfg-1",
    }


def test_post_score_sends_id_and_timestamp_when_given(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_post("/api/public/scores", module.HttpResponse(status=200, body={"id": "hurt-1"}))

    module.post_score(
        client, "tid", "other", "cfg-1", score_id="hurt-1", timestamp="2026-10-01T12:00:00+00:00"
    )

    _, _, body = client.calls[0]
    assert body["id"] == "hurt-1"
    assert body["timestamp"] == "2026-10-01T12:00:00+00:00"


def test_post_comment_sends_expected_payload(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_post("/api/public/comments", module.HttpResponse(status=200, body={"id": "c-1"}))

    assert module.post_comment(client, "proj-1", "tid-123", "went in circles") is True
    _, _, body = client.calls[0]
    assert body == {
        "projectId": "proj-1",
        "objectType": "TRACE",
        "objectId": "tid-123",
        "content": "went in circles",
    }


# ----------------- a refused write is logged -----------------

def test_each_refused_write_logs_its_step_and_status(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_post("/api/public/ingestion", module.HttpResponse(status=401, body={"message": "no auth"}))
    client.when_post("/api/public/scores", module.HttpResponse(status=400, body={"message": "bad value"}))
    client.when_post("/api/public/comments", module.HttpResponse(status=500, body=None))
    logged: list[str] = []

    assert module.tag_trace(client, "tid", logged.append) is False
    assert module.post_score(client, "tid", "other", "cfg-1", logged.append) is False
    assert module.post_comment(client, "proj-1", "tid", "x", logged.append) is False

    assert len(logged) == 3
    assert "tag" in logged[0] and "HTTP 401" in logged[0] and "no auth" in logged[0]
    assert "score" in logged[1] and "HTTP 400" in logged[1] and "bad value" in logged[1]
    assert "comment" in logged[2] and "HTTP 500" in logged[2]


def test_long_error_body_is_cut_short_in_the_log(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_post("/api/public/scores", module.HttpResponse(status=400, body={"message": "x" * 5000}))
    logged: list[str] = []

    module.post_score(client, "tid", "other", "cfg-1", logged.append)

    assert len(logged) == 1
    assert len(logged[0]) < 400


def test_successful_writes_log_nothing(hurt_annotations_module: Any) -> None:
    module = hurt_annotations_module
    client = FakeClient(module)
    client.when_post("/api/public/ingestion", module.HttpResponse(status=207, body={}))
    client.when_post("/api/public/scores", module.HttpResponse(status=200, body={"id": "s"}))
    client.when_post("/api/public/comments", module.HttpResponse(status=201, body={"id": "c"}))
    logged: list[str] = []

    assert module.tag_trace(client, "tid", logged.append) is True
    assert module.post_score(client, "tid", "other", "cfg-1", logged.append) is True
    assert module.post_comment(client, "proj-1", "tid", "x", logged.append) is True
    assert logged == []
