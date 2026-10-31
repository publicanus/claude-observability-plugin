"""Cleans a real Claude Code session into a replay fixture, and checks that the fixtures
hold no personal text.

    uv run --frozen python tests/transcript_cleaning.py <session .jsonl> <name>

    <session .jsonl>  a real session's transcript; its agents' transcripts are read from
                      <session id>/subagents/ beside it. Nothing there is changed.
    <name>            the fixture's folder under tests/fixtures/replay/; it must not exist
    stdout            the session's number of replies, and whether the replay gives the
                      same result for the original and the cleaned copy
    stderr            what failed
    exit              0 when the cleaned copy is written and passes every check; 1 when a
                      check fails, and the cleaned copy is deleted; 2 for a bad argument
    needs             git on the PATH; the dev packages of uv.lock

Every string in the copy is replaced by a replacement text `text-<n>`, except blank
strings, the fixed texts in hooks/claude_code_texts.py, the token-shaped values at
KEPT_POSITIONS, and the tags, ids and status of a task notification. Dict keys that are
not name-shaped are replaced too.

personal_text_findings(folder) is the check the tests run on the committed fixtures.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "hooks"))
from claude_code_texts import INTERRUPTED_TEXT, LEGACY_USER_REJECTED_TOOL_USE_RESULT  # noqa: E402
from secret_masking import mask_secrets  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "replay"
FIXED_TEXTS = frozenset({INTERRUPTED_TEXT, LEGACY_USER_REJECTED_TOOL_USE_RESULT})

ROW = ("row",)
META = ("meta",)
# The keys whose values stay, per position: a path of dict keys from the row or the
# .meta.json file, where "[]" stands for any item of a list.
KEPT_POSITIONS: Dict[Tuple[str, ...], FrozenSet[str]] = {
    ROW: frozenset({
        "uuid", "parentUuid", "logicalParentUuid", "leafUuid", "sessionId", "promptId", "requestId",
        "timestamp", "type", "subtype", "toolUseID", "sourceToolUseID", "sourceToolAssistantUUID",
        "agentId", "toolDenialKind", "attributionSkill", "attributionPlugin",
    }),
    ROW + ("origin",): frozenset({"kind", "senderTaskId"}),
    ROW + ("message",): frozenset({"id", "role", "model", "stop_reason", "type"}),
    ROW + ("message", "content", "[]"): frozenset({"type", "id", "name", "tool_use_id"}),
    ROW + ("attachment",): frozenset({"type"}),
    ROW + ("attachment", "origin"): frozenset({"kind"}),
    ROW + ("toolUseResult", "pin"): frozenset({"id"}),
    META: frozenset({"agentType", "toolUseId", "taskKind", "name"}),
}
NOTIFICATION_OPEN = "<task-notification>"
NOTIFICATION_CLOSE = "</task-notification>"
NOTIFICATION_KEPT_TAGS = frozenset({"task-id", "tool-use-id", "status"})

TOKEN = re.compile(r"^[A-Za-z0-9_.:<>-]{1,100}$")
NAME_SHAPED_KEY = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$-]*$")
REPLACEMENT = re.compile(r"^text-\d+$")
# /Users/<name>, /home/<name>, C:\Users\<name>, or Claude Code's folder name -Users-<name>-
HOME_FOLDER = re.compile(r"(?:^|[\\/])(?:Users|home)[\\/]+[^\\/\s]+|(?:^|-)(?:Users|home)-[^-\s]+-")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class CleaningError(Exception):
    pass


def is_kept_free(text: str) -> bool:
    """True for a string that stays wherever it is: blank, or a fixed text."""
    return not text.strip() or text.strip() in FIXED_TEXTS


def is_kept_at(path: Tuple[str, ...], key: Any, value: str) -> bool:
    return key in KEPT_POSITIONS.get(path, frozenset()) and bool(TOKEN.match(value))


def notification_bounds(text: str) -> Tuple[int, int]:
    """Where the task notification in text starts and ends; (-1, -1) when it holds none."""
    start, close = text.find(NOTIFICATION_OPEN), text.rfind(NOTIFICATION_CLOSE)
    if start < 0 or close < start:
        return -1, -1
    return start, close + len(NOTIFICATION_CLOSE)


# ----------------- Cleaning -----------------

class Cleaner:
    """Cleans the rows of one session; one original string gets one replacement text."""

    def __init__(self) -> None:
        self.replacements: Dict[str, str] = {}

    def replace(self, text: str) -> str:
        if is_kept_free(text):
            return text
        return self.replacements.setdefault(text, f"text-{len(self.replacements) + 1}")

    def notification(self, text: str, start: int, end: int) -> str:
        element = ET.fromstring(text[start:end])
        for child in element.iter():
            if child is element:
                continue
            if child.text and child.tag not in NOTIFICATION_KEPT_TAGS:
                child.text = self.replace(child.text)
            if child.tail:
                child.tail = self.replace(child.tail)
        return self.replace(text[:start]) + ET.tostring(element, encoding="unicode") + self.replace(text[end:])

    def value(self, value: Any, path: Tuple[str, ...], key: Any) -> Any:
        if isinstance(value, dict):
            return {
                (k if NAME_SHAPED_KEY.match(k) else self.replace(k)): self.value(v, path + (k,), k)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.value(item, path + ("[]",), key) for item in value]
        if isinstance(value, str):
            if is_kept_at(path[:-1], key, value):
                return value
            start, end = notification_bounds(value)
            if start >= 0:
                return self.notification(value, start, end)
            return self.replace(value)
        return value

    def document(self, document: Any, root: Tuple[str, ...], where: str) -> Any:
        try:
            return self.value(document, root, None)
        except ET.ParseError as error:
            raise CleaningError(f"{where}: a task notification does not parse: {error}") from error


def clean_session(session: Path, out: Path) -> None:
    """Writes the cleaned copy of session and its agents' transcripts into out, in Claude
    Code's layout."""
    cleaner = Cleaner()
    subagents = session.with_suffix("") / "subagents"
    files = [session] + (sorted(subagents.iterdir()) if subagents.is_dir() else [])
    for path in files:
        name = path.relative_to(session.parent).as_posix()
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".jsonl":
            lines = [line for line in path.read_bytes().split(b"\n") if line.strip()]
            rows = [
                cleaner.document(json.loads(line), ROW, f"{name} line {number}")
                for number, line in enumerate(lines, start=1)
            ]
            target.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        elif path.name.endswith(".meta.json"):
            meta = cleaner.document(json.loads(path.read_text(encoding="utf-8")), META, name)
            target.write_text(json.dumps(meta) + "\n", encoding="utf-8")
        else:
            raise CleaningError(f"{name}: not a transcript or .meta.json file")


# ----------------- The personal-text check -----------------

def notification_is_clean(text: str) -> bool:
    start, end = notification_bounds(text)
    if start < 0:
        return False
    try:
        element = ET.fromstring(text[start:end])
    except ET.ParseError:
        return False
    parts = [text[:start], text[end:]]
    for child in element.iter():
        if child is element:
            continue
        if child.tag in NOTIFICATION_KEPT_TAGS:
            if child.text and not TOKEN.match(child.text) and not is_kept_free(child.text):
                return False
        else:
            parts.append(child.text or "")
        parts.append(child.tail or "")
    return all(is_kept_free(part) or REPLACEMENT.match(part) for part in parts)


def strings_in(value: Any, path: Tuple[str, ...], key: Any) -> Iterator[Tuple[Tuple[str, ...], Any, str, bool]]:
    """Every string in value with its position: (path of the dict holding it, its key,
    the string, whether the string is a dict key)."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k, k, True
            yield from strings_in(v, path + (k,), k)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item, path + ("[]",), key)
    elif isinstance(value, str):
        yield path[:-1], key, value, False


def findings_in_document(document: Any, root: Tuple[str, ...]) -> List[str]:
    found = []
    for path, key, text, is_key in strings_in(document, root, None):
        place = "/".join(path[1:] + ((key,) if not is_key and key is not None else ()))
        if HOME_FOLDER.search(text):
            found.append(f"a home folder path at {place or 'the top'}")
        if EMAIL.search(text):
            found.append(f"an e-mail address at {place or 'the top'}")
        if is_key:
            if not NAME_SHAPED_KEY.match(text) and not REPLACEMENT.match(text):
                found.append(f"a dict key that is not name-shaped at {place or 'the top'}")
        elif not (
            is_kept_free(text) or REPLACEMENT.match(text) or is_kept_at(path, key, text)
            or notification_is_clean(text)
        ):
            found.append(f"text that is not replacement text at {place}")
    if mask_secrets(document) != document:
        found.append("a string shaped like a key or token")
    return found


def personal_text_findings(folder: Path) -> List[str]:
    """One line per finding, as `<file> line <n>: <what>`, for every transcript and
    .meta.json file under folder."""
    findings = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(folder).as_posix()
        root = META if path.name.endswith(".meta.json") else ROW
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                findings.append(f"{name} line {number}: not JSON")
                continue
            findings += [f"{name} line {number}: {what}" for what in findings_in_document(document, root)]
    return findings


# ----------------- The script -----------------

def git_config(name: str) -> str:
    result = subprocess.run(["git", "config", "--global", "--get", name], capture_output=True, text=True)
    return result.stdout.strip()


def owner_names() -> List[str]:
    """This machine's home folder name, and the global git user name and e-mail."""
    return [name for name in (Path.home().name, git_config("user.name"), git_config("user.email")) if name]


def owner_name_findings(folder: Path) -> List[str]:
    names = [name.lower() for name in owner_names()]
    findings = []
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8").lower()
            findings += [f"{path.relative_to(folder).as_posix()}: holds {name!r}" for name in names if name in text]
    return findings


def replay_differences(original: Path, cleaned: Path) -> Tuple[int, List[str]]:
    """The number of replies, and one line per reply whose count differs between the
    original session and its cleaned copy."""
    from transcript_replay import replay_session  # not at the top: it needs pytest, which the check does not

    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        before = replay_session(original, Path(first))
        after = replay_session(cleaned, Path(second))
    differences = [
        f"{reply_id.file}, reply {reply_id.message_id}: {before.counts.get(reply_id)} before, "
        f"{after.counts.get(reply_id)} after"
        for reply_id in sorted(set(before.counts) | set(after.counts), key=lambda r: (r.file, r.message_id))
        if before.counts.get(reply_id) != after.counts.get(reply_id)
    ]
    return len(before.counts), differences


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("usage: transcript_cleaning.py <session .jsonl> <name>", file=sys.stderr)
        return 2
    session, name = Path(argv[0]).resolve(), argv[1]
    out = FIXTURES / name
    if not session.is_file() or session.suffix != ".jsonl":
        print(f"not a session transcript: {session}", file=sys.stderr)
        return 2
    if out.exists():
        print(f"the fixture folder already exists: {out}", file=sys.stderr)
        return 2

    clean_session(session, out)
    cleaned = out / session.name
    problems = personal_text_findings(out) + owner_name_findings(out)
    replies, differences = (0, []) if problems else replay_differences(session, cleaned)
    problems += differences
    if problems:
        shutil.rmtree(out)
        print(f"{name}: not kept", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"{name}: {session.stem}, {replies} replies, same replay result: yes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
