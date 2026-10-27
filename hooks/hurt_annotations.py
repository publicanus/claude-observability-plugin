"""
Attach pending /hurt annotations (mate-hurt plugin) onto their target traces.

mate-hurt queues an annotation locally instead of contacting Langfuse itself
— see its README's "Local queue: the contract with langfuse-observability".
This module is the consumer: it uses this hook's own already-resolved
Langfuse credentials, so a hurt is always attached using the same
credentials already writing this machine's traces — it can never land in
the wrong Langfuse project, and mate-hurt never needs credentials of its
own.

Queue file: ~/.claude/state/hurt_pending.json — a JSON array of objects:
    id          str, uuid4, unique per queued hurt
    trace_id    str, the target trace, resolved once by mate-hurt
    category    str, one of CATEGORIES
    comment     str, the operator's own words
    created     str, ISO 8601 UTC timestamp
    session_id  str, for log correlation only — never read here
    done        list of str, written by earlier versions of this hook only;
                never read here

A hurt is attached as one `hurt` score on its trace: the category is the
score's value and the operator's words are the score's comment. Hurts are
found in Langfuse by that score. The entry is removed once Langfuse accepts
the score. The score carries the entry's id and creation time, so a score
sent again replaces the earlier one instead of adding a second.

Whether the trace is there is asked of the v2 Observations API, which
Langfuse Cloud and self-hosted Langfuse v4 serve. A trace not there yet
keeps its hurt queued for a later run.

A hurt that can't yet be attached — Langfuse unreachable, the trace not
yet uploaded, credentials misconfigured — stays queued and is
retried the next time any Claude Code session's hook fires; it is never
dropped optimistically. Entries older than PENDING_MAX_AGE_DAYS that still
can't resolve are dropped so the queue doesn't grow without bound — logged,
not silent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from langfuse_client import HttpResponse, LangfuseClient

CATEGORIES = (
    "correction-repeated",
    "redone-work",
    "stalled-loop",
    "missed-trigger",
    "ci-escape",
    "other",
)

SCORE_CONFIG_NAME = "hurt"

# Bound on how long an unresolvable pending hurt is retried before it is
# dropped, mirroring the hook's own 30-day session-state pruning.
PENDING_MAX_AGE_DAYS = 30

# The trace lookup needs a time range. A hurt targets the session's latest
# turn, which started before the hurt was queued — days before when an old
# session is resumed — so the range reaches this far back from that moment.
TRACE_LOOKBACK_DAYS = 30


Log = Optional[Callable[[str], None]]


def _report_failure(log: Log, step: str, resp: HttpResponse) -> None:
    """Say in the hook log which write Langfuse refused, and why, so a
    rejected request is not mistaken for a trace that is not ready yet."""
    if log is None:
        return
    body = json.dumps(resp.body) if resp.body is not None else ""
    log(f"hurt {step} failed: HTTP {resp.status} {body[:300]}".rstrip())


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trace_exists(
    client: LangfuseClient,
    trace_id: str,
    queued_at: datetime,
    now: datetime,
    log: Log = None,
) -> bool:
    """True once at least one observation of the trace is in the project.

    queued_at is when the hurt was queued; the trace is looked for from
    TRACE_LOOKBACK_DAYS before it up to now. A refused lookup is logged;
    an unreachable Langfuse is not."""
    query = urlencode(
        {
            "traceId": trace_id,
            "fields": "core",
            "limit": 1,
            "fromStartTime": _iso_utc(queued_at - timedelta(days=TRACE_LOOKBACK_DAYS)),
            "toStartTime": _iso_utc(now),
        }
    )
    resp = client.get(f"/api/public/v2/observations?{query}")
    if resp.status != 200 or not isinstance(resp.body, dict):
        if resp.status != 0:
            _report_failure(log, "trace lookup", resp)
        return False
    rows = resp.body.get("data")
    if not isinstance(rows, list):
        _report_failure(log, "trace lookup", resp)
        return False
    return len(rows) > 0


def _find_matching_score_config(client: LangfuseClient) -> Optional[str]:
    page = 1
    while True:
        resp = client.get(f"/api/public/score-configs?page={page}&limit=100")
        if resp.status != 200 or not isinstance(resp.body, dict):
            return None
        for config in resp.body.get("data") or []:
            if not isinstance(config, dict):
                continue
            if config.get("name") != SCORE_CONFIG_NAME:
                continue
            if config.get("dataType") != "CATEGORICAL":
                continue
            if config.get("isArchived"):
                # An archived config can't take new scores in Langfuse —
                # treat it as absent so a fresh, usable one gets created.
                continue
            # Langfuse stores each category as a numeric value and a label;
            # the label is the category name a score is sent with.
            labels = {
                c.get("label")
                for c in config.get("categories") or []
                if isinstance(c, dict)
            }
            if labels == set(CATEGORIES):
                config_id = config.get("id")
                if isinstance(config_id, str):
                    return config_id
        meta = resp.body.get("meta") or {}
        total_pages = meta.get("totalPages", page)
        if page >= total_pages:
            return None
        page += 1


def ensure_score_config(client: LangfuseClient, log: Log = None) -> Optional[str]:
    """Idempotent: reuse an existing 'hurt' categorical config with the
    right taxonomy, or create one. Returns the config's id, or None on
    failure. Provisioning lives here — done once by the component that
    holds the connection — rather than in mate-hurt."""
    existing = _find_matching_score_config(client)
    if existing is not None:
        return existing

    resp = client.post(
        "/api/public/score-configs",
        {
            "name": SCORE_CONFIG_NAME,
            "dataType": "CATEGORICAL",
            # Langfuse requires a number as each category's value.
            "categories": [
                {"value": index, "label": category}
                for index, category in enumerate(CATEGORIES)
            ],
        },
    )
    if resp.status not in (200, 201) or not isinstance(resp.body, dict):
        _report_failure(log, "score-config create", resp)
        return None
    config_id = resp.body.get("id")
    return config_id if isinstance(config_id, str) else None


def post_score(
    client: LangfuseClient,
    trace_id: str,
    category: str,
    comment: str,
    config_id: str,
    log: Log = None,
    *,
    score_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> bool:
    body = {
        "traceId": trace_id,
        "name": SCORE_CONFIG_NAME,
        "dataType": "CATEGORICAL",
        "value": category,
        "comment": comment,
        "configId": config_id,
    }
    # Langfuse replaces a score sent again with the same id, name and
    # timestamp instead of adding a second one.
    if score_id:
        body["id"] = score_id
    if timestamp:
        body["timestamp"] = timestamp
    resp = client.post("/api/public/scores", body)
    if resp.status in (200, 201):
        return True
    _report_failure(log, "score", resp)
    return False


# ----------------- Pending queue I/O -----------------

def load_pending_hurts(pending_file: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(pending_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_pending_hurts(pending_file: Path, records: List[Dict[str, Any]]) -> None:
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = pending_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2), encoding="utf-8")
    os.replace(tmp, pending_file)


def _is_well_formed(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("trace_id"), str)
        and bool(record.get("trace_id"))
        and isinstance(record.get("category"), str)
        and isinstance(record.get("comment"), str)
    )


def _is_expired(record: Dict[str, Any], now: datetime) -> bool:
    created = record.get("created")
    if not isinstance(created, str):
        return True  # unparseable -- can never be aged out otherwise
    try:
        created_ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except Exception:
        return True
    return now - created_ts > timedelta(days=PENDING_MAX_AGE_DAYS)


def _created_at(record: Dict[str, Any]) -> Optional[datetime]:
    created = record.get("created")
    if not isinstance(created, str):
        return None
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None


def _score_timestamp(record: Dict[str, Any]) -> Optional[str]:
    created = _created_at(record)
    return created.isoformat() if created is not None else None


def _attempt_annotation(
    client: LangfuseClient,
    record: Dict[str, Any],
    config_id_cache: Dict[str, Optional[str]],
    now: datetime,
    log: Log = None,
) -> bool:
    """Try to attach one record. Returns True once Langfuse accepted its score."""
    queued_at = _created_at(record) or now
    if not trace_exists(client, record["trace_id"], queued_at, now, log):
        return False

    if "value" not in config_id_cache:
        config_id_cache["value"] = ensure_score_config(client, log)
    config_id = config_id_cache["value"]
    if config_id is None:
        return False

    return post_score(
        client,
        record["trace_id"],
        record["category"],
        record["comment"],
        config_id,
        log,
        score_id=record.get("id") if isinstance(record.get("id"), str) else None,
        timestamp=_score_timestamp(record),
    )


def process_pending_hurts(
    config: Any,
    pending_file: Path,
    *,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """Attach every queued /hurt annotation whose trace is ready.

    Returns the number of hurts fully attached this run. Never raises: a
    failure for one record leaves it queued for the next run instead of
    propagating and blocking turn emission (call under FileLock like the
    hook's own state — this function does not lock itself).
    """
    def _log(msg: str) -> None:
        if log is not None:
            log(msg)

    records = load_pending_hurts(pending_file)
    if not records:
        return 0

    client = LangfuseClient(config)
    now = datetime.now(timezone.utc)
    # Resolved once per run and reused for every queued hurt.
    config_id_cache: Dict[str, Optional[str]] = {}

    remaining: List[Dict[str, Any]] = []
    attached = 0
    dropped = 0

    for record in records:
        if not _is_well_formed(record):
            dropped += 1
            continue

        completed = False
        try:
            completed = _attempt_annotation(client, record, config_id_cache, now, log)
        except Exception as e:
            _log(f"hurt annotation attempt failed for {record.get('id')}: {type(e).__name__}: {e}")

        if completed:
            attached += 1
        elif _is_expired(record, now):
            dropped += 1
            _log(f"Dropping pending hurt {record.get('id')}: unresolved after {PENDING_MAX_AGE_DAYS} days")
        else:
            remaining.append(record)

    if attached or dropped:
        save_pending_hurts(pending_file, remaining)
    return attached
