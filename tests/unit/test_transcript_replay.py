"""The replay counts each reply's model calls, names a lost or doubled reply, and cuts
the transcripts at the moment each Stop hook started."""

from __future__ import annotations

import json

from transcript_replay import (
    ModelCall,
    ReplayResult,
    Reply,
    ReplyId,
    Transcript,
    count_model_calls,
    fault_lines,
    rows_written_by,
    stop_points,
    timestamp_ms,
)

SESSION = "s.jsonl"
AGENT = "s/subagents/agent-a.jsonl"


def transcript(name: str, rows: list) -> Transcript:
    return Transcript(name, [json.dumps(row).encode() for row in rows], rows)


def test_a_model_call_that_ends_at_an_api_error_row_changes_no_count() -> None:
    reply_id = ReplyId(SESSION, "msg_1")
    replies = {reply_id: Reply(line=3, timestamps_ms={1_000})}
    calls = [ModelCall(1_000, frozenset()), ModelCall(2_000, frozenset())]

    assert count_model_calls(replies, {2_000}, calls) == {reply_id: 1}


def test_a_lost_and_a_doubled_reply_are_named_with_their_transcript_and_line() -> None:
    lost, doubled, fine = ReplyId(SESSION, "msg_lost"), ReplyId(AGENT, "msg_twice"), ReplyId(SESSION, "msg_ok")
    result = ReplayResult(
        replies={lost: Reply(line=4), doubled: Reply(line=9), fine: Reply(line=12)},
        counts={lost: 0, doubled: 2, fine: 1},
    )

    assert fault_lines(result, {}, prefix="late-hand-back/") == [
        "late-hand-back/s.jsonl line 4, reply msg_lost: lost (0 model calls)",
        "late-hand-back/s/subagents/agent-a.jsonl line 9, reply msg_twice: doubled (2 model calls)",
    ]


def test_a_known_fault_passes_and_one_that_no_longer_happens_fails() -> None:
    known, fixed = ReplyId(SESSION, "msg_known"), ReplyId(SESSION, "msg_fixed")
    result = ReplayResult(replies={known: Reply(line=2), fixed: Reply(line=5)}, counts={known: 0, fixed: 1})

    lines = fault_lines(result, {(SESSION, "msg_known"): "lost", (SESSION, "msg_fixed"): "lost"})

    assert lines == ["s.jsonl line 5, reply msg_fixed: 1 model call; known fault no longer happens: remove its entry"]


def test_each_stop_sees_the_agent_rows_written_before_its_stop_hooks_started() -> None:
    main = transcript(SESSION, [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z"},
        {"type": "system", "subtype": "stop_hook_summary", "timestamp": "2026-01-01T00:00:10.000Z",
         "hookInfos": [{"durationMs": 300}, {"durationMs": 2000}]},
    ])
    agent = transcript(AGENT, [
        {"type": "user", "timestamp": "2026-01-01T00:00:01.000Z"},
        {"type": "last-prompt"},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:08.500Z"},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:07.000Z"},
    ])

    [point] = stop_points(main)

    assert point.main_rows == 1
    assert point.moment_ms == timestamp_ms("2026-01-01T00:00:08.000Z")
    assert rows_written_by(agent, point.moment_ms) == 2


def test_a_main_row_written_after_the_stop_hooks_started_is_not_seen_at_that_stop() -> None:
    main = transcript(SESSION, [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z"},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:07.000Z"},
        {"type": "queue-operation", "timestamp": "2026-01-01T00:00:09.000Z"},
        {"type": "system", "subtype": "stop_hook_summary", "timestamp": "2026-01-01T00:00:10.000Z",
         "hookInfos": [{"durationMs": 2000}]},
    ])

    [point] = stop_points(main)

    assert point.main_rows == 2
