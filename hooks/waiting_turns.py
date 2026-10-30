"""Turns that wait on this computer until Langfuse has accepted them.

A waiting folder holds one JSON file per batch of trace data:

  host         the Langfuse address the batch is for
  public_key   the public key of the repo the batch came from
  secret_key   the secret key of the repo the batch came from
  body         the batch as OTLP protobuf bytes, in base64

Any session may send the batch, with these keys and never its own.

write_waiting_batch adds a file. send_waiting_turns sends every file and
deletes it once Langfuse answers 2xx, or 401/403 (keys rejected). Any other
answer keeps the file for a later run, and so does a file that cannot be
read or deleted. start_sender runs a command in the
background, cut loose from the hook, so the hook does not wait for it.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Set, Tuple

from langfuse_client import LangfuseClient, keys_rejected

TRACES_PATH = "/api/public/otel/v1/traces"
NO_ANSWER = 0


@dataclass(frozen=True)
class Target:
    host: str
    public_key: str
    secret_key: str


def write_waiting_batch(waiting_dir: Path, target: Target, body: bytes) -> None:
    """Adds one batch to waiting_dir. The file appears whole or not at all, and on
    macOS and Linux only its owner can read it, because it holds the secret key."""
    waiting_dir.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{secrets.token_hex(4)}.json"
    content = json.dumps(
        {
            "host": target.host,
            "public_key": target.public_key,
            "secret_key": target.secret_key,
            "body": base64.b64encode(body).decode("ascii"),
        }
    )
    tmp = waiting_dir / f"{name}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        # On disk before the rename, so a power loss cannot leave an empty file in place.
        os.fsync(fh.fileno())
    os.replace(tmp, waiting_dir / name)


def waiting_files(waiting_dir: Path) -> List[Path]:
    return sorted(waiting_dir.glob("*.json"))


def send_waiting_turns(waiting_dir: Path, sdk_version: str, log: Callable[[str], None]) -> int:
    """Sends every waiting batch, also batches written while it runs; returns how
    many Langfuse accepted. Run only one at a time: two would send a batch twice."""
    tried: Set[str] = set()
    unanswered: Set[Tuple[str, str]] = set()
    sent = 0
    while True:
        new = [path for path in waiting_files(waiting_dir) if path.name not in tried]
        if not new:
            return sent
        for path in new:
            tried.add(path.name)
            try:
                sent += _send_batch(path, sdk_version, unanswered, log)
            except (OSError, ValueError, KeyError, TypeError) as e:
                log(f"Skipped waiting file {path.name} this run: {type(e).__name__}: {e}")


def _send_batch(
    path: Path, sdk_version: str, unanswered: Set[Tuple[str, str]], log: Callable[[str], None]
) -> int:
    batch = json.loads(path.read_text(encoding="utf-8"))
    target = Target(batch["host"], batch["public_key"], batch["secret_key"])
    destination = (target.host, target.public_key)
    # One batch that got no answer stands for the rest: each would wait out its own timeout.
    if destination in unanswered:
        return 0

    headers: Dict[str, str] = {
        "Content-Type": "application/x-protobuf",
        "x-langfuse-sdk-name": "python",
        "x-langfuse-sdk-version": sdk_version,
        "x-langfuse-public-key": target.public_key,
        "x-langfuse-ingestion-version": "4",
    }
    response = LangfuseClient(target).post_bytes(TRACES_PATH, base64.b64decode(batch["body"]), headers)
    if 200 <= response.status < 300:
        path.unlink()
        return 1
    if keys_rejected(response.status):
        log(f"Dropped a waiting batch: {target.host} rejected its keys (HTTP {response.status})")
        path.unlink()
        return 0
    if response.status == NO_ANSWER:
        unanswered.add(destination)
        log(f"Waiting batches kept: {target.host} did not respond")
    else:
        log(f"Waiting batch kept: HTTP {response.status} from {target.host}")
    return 0


def start_sender(command: List[str]) -> None:
    """Starts `command` in the background and returns at once. It keeps running
    after the caller exits, and it does not hold the caller's stdin, stdout or
    stderr, which Claude Code waits on until they close."""
    if sys.platform == "win32":
        # No CREATE_BREAKAWAY_FROM_JOB: it fails inside a job that lacks
        # BREAKAWAY_OK, such as uv's, which lets a child leave without it.
        detach = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        detach = {"start_new_session": True}
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detach,
    )
