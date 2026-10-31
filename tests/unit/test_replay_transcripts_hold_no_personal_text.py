"""The committed replay transcripts hold only replacement text, ids, timestamps, names at
the positions where Claude Code writes them, and fixed texts the hook reads."""

from __future__ import annotations

import json
from pathlib import Path

from transcript_cleaning import FIXTURES, personal_text_findings


def test_the_committed_replay_transcripts_hold_no_personal_text() -> None:
    assert any(FIXTURES.rglob("*.jsonl")), "no replay transcripts found"

    assert personal_text_findings(FIXTURES) == []


def test_a_home_folder_path_is_reported_with_its_file_and_line(tmp_path: Path) -> None:
    rows = [
        {"type": "user", "cwd": "text-1"},
        {"type": "user", "cwd": "/Users/someone/repo"},
        {"type": "user", "cwd": "C:\\Users\\someone\\repo"},
    ]
    (tmp_path / "s.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    findings = personal_text_findings(tmp_path)

    assert "s.jsonl line 2: a home folder path at cwd" in findings
    assert "s.jsonl line 3: a home folder path at cwd" in findings
    assert not any(finding.startswith("s.jsonl line 1:") for finding in findings)


def test_a_name_inside_a_tool_input_is_reported(tmp_path: Path) -> None:
    row = {
        "type": "assistant",
        "message": {"id": "msg_1", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"name": "Alice"}},
        ]},
    }
    (tmp_path / "s.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert personal_text_findings(tmp_path) == [
        "s.jsonl line 1: text that is not replacement text at message/content/[]/input/name",
    ]
