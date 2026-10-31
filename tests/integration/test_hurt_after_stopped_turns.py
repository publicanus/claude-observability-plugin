"""Pins where a /hurt note lands when the turn before it was stopped with Esc or ended
in an API error: on that turn's trace, which the hook sends at the /hurt turn's Stop.

Each test writes a session transcript in a temp folder, runs /hurt (hurt.run) from a
repo whose settings file holds Langfuse keys, runs the hook over the transcript, and
reads which trace the note was given in the /hurt queue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

import hurt
from conftest import write_settings
from test_continued_agent_runs import agent_says, append_jsonl, assistant_row, at, text, tool_use, user_row

SESSION_ID = "session-stopped"
INTERRUPTED = "[Request interrupted by user]"


# ----------------- Transcript rows -----------------

def interrupted(uuid: str, seconds: float) -> Dict[str, Any]:
    """The row Claude Code writes when Esc stops a turn."""
    return {"type": "user", "timestamp": at(seconds), "uuid": uuid,
            "message": {"role": "user", "content": [text(INTERRUPTED)]}}


def api_error(uuid: str, seconds: float) -> Dict[str, Any]:
    return assistant_row(uuid, [text("API Error: 400 injected")], seconds, isApiErrorMessage=True)


def normal_turn(prompt: str, start: float) -> List[Dict[str, Any]]:
    return [user_row(f"u-{start}", prompt, start), agent_says(f"a-{start}", "Done.", start + 1)]


def hurt_turn(start: float) -> List[Dict[str, Any]]:
    """The /hurt turn up to and including its script's run."""
    return [
        user_row("hurt-command", "<command-message>mate-record:hurt</command-message>\n"
                 "<command-name>/mate-record:hurt</command-name>\n<command-args>it kept looping</command-args>", start),
        {"type": "user", "timestamp": at(start), "uuid": "hurt-expansion", "isMeta": True,
         "message": {"role": "user", "content": [text("Base directory for this skill")]}},
        assistant_row("hurt-bash", [tool_use("toolu_hurt", "Bash", {"command": "hurt.py"})], start + 1),
    ]


# ----------------- The session -----------------

class Session:
    def __init__(self, hook_module: Any, fake_langfuse: Any, transcript: Path) -> None:
        self.hook_module = hook_module
        self.fake_langfuse = fake_langfuse
        self.transcript = transcript

    def add(self, *rows: Dict[str, Any]) -> None:
        append_jsonl(self.transcript, list(rows))

    def run_hook(self) -> None:
        config = self.hook_module.LangfuseConfig("pk", "sk", "https://lf.example", "user-1", repo_name="shop")
        self.hook_module.emit_new_turns_from_transcript(self.fake_langfuse, config, SESSION_ID, self.transcript)

    def run_hurt(self) -> int:
        return hurt.run(["--category", "stalled-loop", "--comment", "it kept looping"],
                        env={"CLAUDE_CODE_SESSION_ID": SESSION_ID})

    def trace_of(self, prompt: str) -> str:
        [trace_id] = [o.trace_id for o in self.roots() if o.kwargs["input"]["content"] == prompt]
        return trace_id

    def roots(self) -> List[Any]:
        return [o for o in self.fake_langfuse.observations if o.name == "Conversational Turn"]

    def pending(self) -> List[Dict[str, Any]]:
        return read_queue(self.hook_module.HURT_PENDING_FILE)

    def unresolved(self) -> List[Dict[str, Any]]:
        return read_queue(self.hook_module.HURT_UNRESOLVED_FILE)

    def log(self) -> str:
        return self.hook_module.LOG_FILE.read_text(encoding="utf-8")


def read_queue(path: Path) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


@pytest.fixture
def session(hook_module: Any, fake_langfuse: Any, isolated_hook_state: Path, repo: Path,
            tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_langfuse_env: None) -> Session:
    """A session in a repo whose settings file holds keys, with /hurt and the hook on one state folder."""
    write_settings(repo, {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk",
                          "LANGFUSE_BASE_URL": "https://lf.example"})
    monkeypatch.chdir(repo)
    monkeypatch.setattr(hurt, "HURT_UNRESOLVED_FILE", hook_module.HURT_UNRESOLVED_FILE)
    monkeypatch.setattr(hurt, "HURT_PENDING_LOCK_FILE", hook_module.HURT_PENDING_LOCK_FILE)
    transcript = tmp_path / "session.jsonl"
    transcript.touch()
    return Session(hook_module, fake_langfuse, transcript)


# ----------------- Where the note lands -----------------

def test_a_hurt_after_a_turn_stopped_mid_reply_lands_on_that_turn(session: Session) -> None:
    session.add(*normal_turn("First question.", 0))
    session.run_hook()
    session.add(user_row("u-2", "Write an essay.", 10), agent_says("a-2", "Rivers are", 11), interrupted("i-2", 12))
    session.add(*hurt_turn(20))
    assert session.run_hurt() == 0
    session.run_hook()

    [note] = session.pending()
    assert note["trace_id"] == session.trace_of("Write an essay.")
    assert note["trace_id"] != session.trace_of("First question.")
    assert session.unresolved() == []


def test_a_hurt_after_a_turn_stopped_before_any_reply_lands_on_that_turn(session: Session) -> None:
    session.add(*normal_turn("First question.", 0))
    session.run_hook()
    session.add(user_row("u-2", "Write an essay.", 10), interrupted("i-2", 11))
    session.add(*hurt_turn(20))
    session.run_hurt()
    session.run_hook()

    [note] = session.pending()
    assert note["trace_id"] == session.trace_of("Write an essay.")


def test_a_hurt_after_a_turn_that_ended_in_an_api_error_lands_on_that_turn(session: Session) -> None:
    session.add(*normal_turn("First question.", 0))
    session.run_hook()
    session.add(user_row("u-2", "Say yes.", 10), api_error("e-2", 11))
    session.add(*hurt_turn(20))
    session.run_hurt()
    session.run_hook()

    [note] = session.pending()
    assert note["trace_id"] == session.trace_of("Say yes.")


def test_a_hurt_after_a_first_turn_stopped_before_any_reply_is_queued_and_lands_on_it(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    session.add(user_row("u-1", "Write an essay.", 0), interrupted("i-1", 1))
    session.add(*hurt_turn(10))

    assert session.run_hurt() == 0
    assert "/hurt: queued 'stalled-loop' for the turn before this one." in capsys.readouterr().out
    session.run_hook()

    [note] = session.pending()
    assert note["trace_id"] == session.trace_of("Write an essay.")


def test_a_hurt_after_a_normal_turn_lands_on_that_turn(session: Session) -> None:
    session.add(*normal_turn("First question.", 0))
    session.run_hook()
    session.add(*hurt_turn(10))
    session.run_hurt()
    session.run_hook()

    [note] = session.pending()
    assert note["trace_id"] == session.trace_of("First question.")


def test_a_hurt_as_the_sessions_first_prompt_is_removed_and_logged(session: Session) -> None:
    session.add(*hurt_turn(0))
    session.run_hurt()
    [queued] = session.unresolved()
    session.run_hook()

    assert session.pending() == []
    assert session.unresolved() == []
    assert queued["id"] in session.log()


# ----------------- What is sent -----------------

def test_a_prompt_stopped_before_any_reply_is_sent_as_a_trace_with_an_empty_output(session: Session) -> None:
    session.add(user_row("u-1", "Write an essay.", 0), interrupted("i-1", 1))
    session.add(*normal_turn("Next question.", 10))
    session.run_hook()

    inputs = [o.kwargs["input"]["content"] for o in session.roots()]
    assert inputs == ["Write an essay.", "Next question."]
    assert session.roots()[0].output == {"role": "assistant", "content": ""}


# ----------------- The unresolved queue stays bounded -----------------

def test_an_unresolved_note_older_than_30_days_is_removed(session: Session) -> None:
    old = {"id": "old-note", "category": "other", "comment": "x", "created": "2020-01-01T00:00:00+00:00",
           "session_id": "another-session", "target": "t"}
    session.hook_module.HURT_UNRESOLVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    session.hook_module.HURT_UNRESOLVED_FILE.write_text(json.dumps([old]), encoding="utf-8")
    session.add(*normal_turn("First question.", 0))
    session.run_hook()

    assert session.unresolved() == []
    assert "old-note" in session.log()
