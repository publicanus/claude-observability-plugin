"""Pins the work item each trace names, as the tag `work-item:<id>`: the item a tool
named with `langfuse-work-item`, else the one the configured branch pattern gives.

Each test writes a transcript whose turns run a minute apart, and names items with
the command's own writer, timed inside a chosen turn.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import work_item

SESSION = "session-46"
PATTERN = r"^feat/(\d+)-"


def at_minute(minute: int, second: int = 0) -> str:
    return f"2026-01-01T00:{minute:02d}:{second:02d}.000Z"


def inside_turn(number: int) -> datetime:
    """A time between turn `number`'s prompt and its reply."""
    return datetime(2026, 1, 1, 0, number, 10, tzinfo=timezone.utc)


def turn_rows(number: int, branch: str, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Turn `number`: a prompt at minute `number` and a reply 30 s later."""
    extra = {"agentId": agent_id} if agent_id else {}
    prefix = agent_id or "main"
    return [
        {
            "type": "user",
            "timestamp": at_minute(number),
            "sessionId": SESSION,
            "uuid": f"{prefix}-user-{number}",
            "cwd": "/repo",
            "gitBranch": branch,
            "message": {"role": "user", "content": f"Prompt {number}"},
            **extra,
        },
        {
            "type": "assistant",
            "timestamp": at_minute(number, 30),
            "sessionId": SESSION,
            "uuid": f"{prefix}-assistant-{number}",
            "message": {
                "id": f"{prefix}-msg-{number}",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": f"Reply {number}"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            **extra,
        },
    ]


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def write_transcript(folder: Path, turns: int, branch: str = "main") -> Path:
    rows = [row for number in range(1, turns + 1) for row in turn_rows(number, branch)]
    return write_jsonl(folder / "transcript.jsonl", rows)


def write_teammate(transcript: Path, turns: int) -> None:
    """A named teammate of the session whose turns run at the same minutes as the main turns."""
    subagents = transcript.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    meta = {"agentType": "builder", "name": "builder", "taskKind": "in_process_teammate", "teamName": "team"}
    (subagents / "agent-abuilder.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    rows = [row for number in range(1, turns + 1) for row in turn_rows(number, "main", agent_id="abuilder")]
    write_jsonl(subagents / "agent-abuilder.jsonl", rows)


def name_item(hook_module: Any, argv: List[str], when: datetime) -> None:
    assert work_item.run_command(argv, hook_module.WORK_ITEMS_DIR, SESSION, when) == 0


def configure(hook_module: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pattern: Optional[str]) -> Any:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://example.test")
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_WORK_ITEM_BRANCH_PATTERN", raising=False)
    if pattern is None:
        monkeypatch.delenv("CC_LANGFUSE_WORK_ITEM_BRANCH_PATTERN", raising=False)
    else:
        monkeypatch.setenv("CC_LANGFUSE_WORK_ITEM_BRANCH_PATTERN", pattern)
    cwd = tmp_path / "shop"
    cwd.mkdir(exist_ok=True)
    return hook_module.get_langfuse_config(str(cwd))


def emit(hook_module: Any, fake_langfuse: Any, config: Any, transcript: Path) -> None:
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)


def work_item_tags(fake_langfuse: Any, trace: str = "Conversational Turn") -> List[List[str]]:
    """The work-item tags of each trace whose root observation is named `trace`, in emit order."""
    return [
        [tag for tag in obs.propagated["tags"] if tag.startswith("work-item:")]
        for obs in fake_langfuse.observations
        if obs.name == trace
    ]


def test_from_the_turn_a_tool_names_the_item_each_trace_carries_it(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path
):
    transcript = write_transcript(tmp_path, turns=2)
    write_teammate(transcript, turns=3)
    name_item(hook_module, ["set", "37"], inside_turn(2))

    emit(hook_module, fake_langfuse, configure(hook_module, monkeypatch, tmp_path, None), transcript)

    assert work_item_tags(fake_langfuse) == [[], ["work-item:37"]]
    # Teammate turns 1 and 2 close on this firing; turn 2 ends after the item was named.
    assert work_item_tags(fake_langfuse, "Teammate Turn") == [[], ["work-item:37"]]


def test_naming_another_item_replaces_it_and_clearing_brings_back_the_branch(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path
):
    transcript = write_transcript(tmp_path, turns=4, branch="feat/12-other")
    name_item(hook_module, ["set", "37"], inside_turn(1))
    name_item(hook_module, ["set", "38"], inside_turn(3))
    name_item(hook_module, ["clear"], inside_turn(4))

    emit(hook_module, fake_langfuse, configure(hook_module, monkeypatch, tmp_path, PATTERN), transcript)

    assert work_item_tags(fake_langfuse) == [
        ["work-item:37"],
        ["work-item:37"],
        ["work-item:38"],
        ["work-item:12"],
    ]


def test_of_two_items_named_in_one_turn_the_turn_carries_the_last(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path
):
    transcript = write_transcript(tmp_path, turns=1)
    name_item(hook_module, ["set", "37"], inside_turn(1))
    name_item(hook_module, ["set", "38"], inside_turn(1))

    emit(hook_module, fake_langfuse, configure(hook_module, monkeypatch, tmp_path, None), transcript)

    assert work_item_tags(fake_langfuse) == [["work-item:38"]]


def test_with_no_named_item_a_matching_branch_gives_the_first_group(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path
):
    transcript = write_transcript(tmp_path, turns=1, branch="feat/37-plugins-on-trace")

    emit(hook_module, fake_langfuse, configure(hook_module, monkeypatch, tmp_path, PATTERN), transcript)

    assert work_item_tags(fake_langfuse) == [["work-item:37"]]


def test_a_named_item_wins_over_the_branch(hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path):
    transcript = write_transcript(tmp_path, turns=1, branch="feat/37-plugins-on-trace")
    name_item(hook_module, ["set", "40"], inside_turn(1))

    emit(hook_module, fake_langfuse, configure(hook_module, monkeypatch, tmp_path, PATTERN), transcript)

    assert work_item_tags(fake_langfuse) == [["work-item:40"]]


@pytest.mark.parametrize(
    "pattern, branch",
    [(PATTERN, "main"), ("(.*)", "HEAD"), (None, "feat/37-plugins-on-trace")],
    ids=["branch-does-not-match", "detached-checkout", "no-pattern-set"],
)
def test_with_nothing_to_give_an_item_the_trace_has_no_work_item_tag(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path, pattern, branch
):
    transcript = write_transcript(tmp_path, turns=1, branch=branch)

    emit(hook_module, fake_langfuse, configure(hook_module, monkeypatch, tmp_path, pattern), transcript)

    assert work_item_tags(fake_langfuse) == [[]]


@pytest.mark.parametrize("pattern", ["feat/(", r"feat/\d+-"], ids=["invalid", "no-group"])
def test_a_refused_pattern_is_logged_and_every_trace_is_still_sent(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path, pattern
):
    transcript = write_transcript(tmp_path, turns=1, branch="feat/37-plugins-on-trace")

    emit(hook_module, fake_langfuse, configure(hook_module, monkeypatch, tmp_path, pattern), transcript)

    assert work_item_tags(fake_langfuse) == [[]]
    assert f"work item branch pattern refused: {pattern}: " in hook_module.LOG_FILE.read_text(encoding="utf-8")


def marker_file(hook_module: Any) -> Path:
    return hook_module.WORK_ITEMS_DIR / f"{SESSION}.jsonl"


def test_set_appends_one_line_to_the_sessions_marker_and_prints_nothing(hook_module, isolated_hook_state, capsys):
    when = inside_turn(1)

    code = work_item.run_command(["set", "37"], hook_module.WORK_ITEMS_DIR, SESSION, when)

    assert code == 0
    assert capsys.readouterr() == ("", "")
    lines = marker_file(hook_module).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"at": when.isoformat(), "id": "37"}]


@pytest.mark.parametrize(
    "argv, session_id",
    [(["set", "37"], None), (["set"], SESSION), (["set", "3 7"], SESSION), (["set", "37"], "a/b")],
    ids=["no-session-id", "no-item", "item-with-space", "session-id-with-slash"],
)
def test_a_wrong_call_exits_2_with_one_line_and_writes_nothing(
    hook_module, isolated_hook_state, capsys, argv, session_id
):
    code = work_item.run_command(argv, hook_module.WORK_ITEMS_DIR, session_id, inside_turn(1))

    out, err = capsys.readouterr()
    assert (code, out) == (2, "")
    assert len(err.splitlines()) == 1 and err.startswith("langfuse-work-item: ")
    assert not hook_module.WORK_ITEMS_DIR.exists() or not any(hook_module.WORK_ITEMS_DIR.iterdir())


def test_a_last_line_without_newline_is_ignored(hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path):
    transcript = write_transcript(tmp_path, turns=1)
    name_item(hook_module, ["set", "37"], inside_turn(1))
    with marker_file(hook_module).open("a", encoding="utf-8") as marker:
        marker.write('{"at": "2026-01-01T00:01:20+00:00", "id": "3')

    emit(hook_module, fake_langfuse, configure(hook_module, monkeypatch, tmp_path, None), transcript)

    assert work_item_tags(fake_langfuse) == [["work-item:37"]]


def test_a_corrupt_marker_is_logged_and_gives_no_tag_not_even_the_branch_item(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path
):
    transcript = write_transcript(tmp_path, turns=1, branch="feat/12-other")
    marker = marker_file(hook_module)
    marker.parent.mkdir(parents=True)
    marker.write_text("not json\n", encoding="utf-8")
    name_item(hook_module, ["set", "37"], inside_turn(1))

    emit(hook_module, fake_langfuse, configure(hook_module, monkeypatch, tmp_path, PATTERN), transcript)

    assert work_item_tags(fake_langfuse) == [[]]
    assert str(marker) in hook_module.LOG_FILE.read_text(encoding="utf-8")


def test_a_hook_run_deletes_markers_older_than_the_state_bound(
    hook_module, isolated_hook_state, fake_langfuse, monkeypatch, tmp_path, capsys
):
    folder = hook_module.WORK_ITEMS_DIR
    folder.mkdir(parents=True)
    old, new = folder / "old-session.jsonl", folder / "new-session.jsonl"
    for marker in (old, new):
        marker.write_text("", encoding="utf-8")
    long_ago = time.time() - hook_module.STATE_MAX_AGE.total_seconds() - 3600
    os.utime(old, (long_ago, long_ago))
    configure(hook_module, monkeypatch, tmp_path, None)
    transcript = write_transcript(tmp_path, turns=0)
    monkeypatch.setattr(
        hook_module,
        "read_hook_payload",
        lambda: {"hook_event_name": "Stop", "session_id": SESSION, "transcript_path": str(transcript)},
    )
    monkeypatch.setattr(hook_module, "create_langfuse_client", lambda config: fake_langfuse)

    assert hook_module.main() == 0

    assert sorted(path.name for path in folder.iterdir()) == ["new-session.jsonl"]
