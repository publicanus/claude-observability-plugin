#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyrage==1.4.0"]
# ///
"""Shares a GitHub repo's Langfuse keys through its branch mate-record-keys, locked so
that only the laptops let in, and the repo's CI job, can open them.

    sh hooks/run_hook.sh repo_keys.py setup --project-dir <dir>
    sh hooks/run_hook.sh repo_keys.py wait --project-dir <dir>

    --project-dir  the folder the Claude Code session started in
    needs          uv (for pyrage) and git; gh, logged in, only for the first person
                   on a repo; on Windows, run it with
                   UV_PYTHON=cpython-3.13-windows-x86_64-none
    reads          the repo's .claude/settings.local.json, git's settings, and this
                   laptop's key in <home>/.config/mate-record/laptop-key.txt
    setup stdout   {"result": R}; R is one of
                   not-github           the repo has no github.com remote
                   ready                this laptop was let in; the settings file now
                                        holds the repo's values
                   joining              this laptop asked to be let in
                   not-admin            no keys on GitHub yet, and gh does not show that
                                        the consultant manages the repo; the object also
                                        has "settings_hold_keys": true or false
                   need-workflow-scope  gh's login lacks the workflow scope
                   need-keys            the settings file holds no keys to share
                   locked               the keys in the settings file are on GitHub now
    wait stdout    one line, after at most about 10 minutes: READY once this laptop was
                   let in and the settings file holds the repo's values, else NOT_READY
    exit           0 done; 1 a step failed: stderr says which in one plain sentence
                   (with the tool's own message on the first person's path), and the
                   hook's log gets the details; wait prints NOT_READY instead and
                   leaves stderr empty
    side effects   makes this laptop's key when it is missing; pushes to the branch
                   mate-record-keys; the first person also stores the repo secret
                   MATE_RECORD_CI_KEY; writes the settings file
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Dict, List, Optional, Sequence

from pyrage import DecryptError, decrypt, encrypt, x25519

HERE = Path(__file__).resolve().parent
# The plugin's hooks folder and this folder, so the shared modules import however this script is run.
sys.path.insert(0, str(HERE.parents[1] / "hooks"))
sys.path.insert(0, str(HERE))
import state_files  # noqa: E402
from prepare_keys_file import SetupError, write_keys  # noqa: E402
from repo_checkout import Remote, find_checkout, find_remote, github_repo  # noqa: E402
from settings_file import (  # noqa: E402
    KEYS_BRANCH,
    SettingsFileError,
    env_holds_keys,
    read_env_host,
    read_env_keys,
    repo_settings_env,
)

READY = "Tracing is ready. Your next session in this repo is traced."
NOT_READY = "Tracing is not ready yet. Run /mate-record:langfuse-setup again later, or ask whoever manages this repo."

LOCK_PATH = "mate-record/langfuse-keys.age"
CI_PUB_PATH = "mate-record/ci.pub"
LAPTOPS_DIR = "mate-record/laptops"
BRANCH_TEMPLATES = {
    "mate-record/relock.py": HERE / "keys_branch" / "relock.py",
    ".github/workflows/mate-record-keys.yml": HERE / "keys_branch" / "mate-record-keys.yml",
}
CI_SECRET = "MATE_RECORD_CI_KEY"
VALUE_NAMES = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")

POLL_SECONDS = 5
LIMIT_SECONDS = 600
PUSH_TRIES = 3
LS_REMOTE_NO_MATCH = 2
# Only gh's login, so the first push is made with the login whose workflow scope was checked.
GH_LOGIN = ("-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential")

# What a failed step says. A next consultant sees these, so none names a key, a lock, CI or the branch.
REACH = "Setup could not reach this repo on GitHub."
READ = "Setup could not read what this repo holds on GitHub."
SEND = "Setup could not send this laptop's request to GitHub."
EMAIL = "Setup could not read your git e-mail address."
WRITE = "Setup could not write the settings file."
LAPTOP = "Setup could not find this laptop's own setup file."
READ_SETTINGS = "Setup could not read the settings file."
SECRET = "Setup could not store the GitHub secret."
FIRST_PUSH = "Setup could not push the new branch to GitHub."


class StepFailed(Exception):
    """A step that failed: sentence is shown, detail goes to the log (and is shown too when show_detail)."""

    def __init__(self, sentence: str, detail: str, show_detail: bool = False) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.detail = detail
        self.show_detail = show_detail


# ----------------- git and gh -----------------
def git(root: Path, *args: str, input: Optional[bytes] = None, env: Optional[Dict[str, str]] = None):
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    try:
        return subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, env=full_env)
    except OSError as e:
        raise StepFailed(REACH, f"git could not run: {e}") from e


def git_checked(root: Path, sentence: str, *args: str, show_detail: bool = False, **kwargs) -> str:
    result = git(root, *args, **kwargs)
    if result.returncode != 0:
        detail = f"git {args[0]} exited {result.returncode}: {result.stderr.decode(errors='replace').strip()}"
        raise StepFailed(sentence, detail, show_detail)
    return result.stdout.decode().strip()


def run_gh(args: List[str], input: Optional[str] = None) -> Optional[subprocess.CompletedProcess]:
    """gh's result, or None when gh cannot run."""
    try:
        return subprocess.run(["gh", *args], input=input, capture_output=True, text=True)
    except OSError:
        return None


def is_admin(repo: str) -> bool:
    result = run_gh(["repo", "view", repo, "--json", "viewerPermission"])
    if result is None or result.returncode != 0:
        return False
    return json.loads(result.stdout)["viewerPermission"] == "ADMIN"


def has_workflow_scope() -> bool:
    result = run_gh(["auth", "status", "--json", "hosts"])
    if result is None or result.returncode != 0:
        return False
    accounts = json.loads(result.stdout)["hosts"].get("github.com", [])
    active = [account for account in accounts if account.get("active")]
    if not active:
        return False
    scopes = [scope.strip() for scope in (active[0].get("scopes") or "").split(",")]
    return "workflow" in scopes


def store_ci_secret(repo: str, ci: x25519.Identity) -> None:
    result = run_gh(["secret", "set", CI_SECRET, "-R", repo], input=str(ci))
    if result is None or result.returncode != 0:
        detail = "gh could not run" if result is None else result.stderr.strip()
        raise StepFailed(SECRET, detail, show_detail=True)


# ----------------- The branch -----------------
def tracking_ref(remote: Remote) -> str:
    return f"refs/remotes/{remote.name}/{KEYS_BRANCH}"


def fetch_keys_branch(root: Path, remote: Remote) -> bool:
    """Fetches the branch into tracking_ref(remote); False when the remote has no such branch."""
    asked = git(root, "ls-remote", "--exit-code", "--heads", remote.name, KEYS_BRANCH)
    if asked.returncode == LS_REMOTE_NO_MATCH:
        return False
    if asked.returncode != 0:
        raise StepFailed(REACH, f"git ls-remote exited {asked.returncode}: {asked.stderr.decode(errors='replace').strip()}")
    git_checked(root, READ, "fetch", "--quiet", remote.name, f"+refs/heads/{KEYS_BRANCH}:{tracking_ref(remote)}")
    return True


def read_lock_file(root: Path, remote: Remote) -> Optional[bytes]:
    """The branch's lock file, freshly fetched; None when the remote has no branch."""
    if not fetch_keys_branch(root, remote):
        return None
    spec = f"{tracking_ref(remote)}:{LOCK_PATH}"
    if git(root, "cat-file", "-e", spec).returncode != 0:
        raise StepFailed(READ, f"{spec} does not exist")
    return git(root, "cat-file", "blob", spec).stdout


def build_commit(root: Path, files: Dict[str, bytes], message: str, parent: Optional[str], sentence: str) -> str:
    """A commit of parent's files with files added or replaced, made without touching the checkout."""
    with tempfile.TemporaryDirectory() as folder:
        index = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
        if parent is not None:
            git_checked(root, sentence, "read-tree", parent, env=index)
        for path, data in files.items():
            blob = git_checked(root, sentence, "hash-object", "-w", "--stdin", input=data)
            git_checked(root, sentence, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=index)
        tree = git_checked(root, sentence, "write-tree", env=index)
    parents = ["-p", parent] if parent is not None else []
    return git_checked(root, sentence, "commit-tree", tree, *parents, "-m", message)


def push_commit(
    root: Path,
    remote: Remote,
    files: Dict[str, bytes],
    message: str,
    *,
    target: str,
    on_branch: bool,
    sentence: str,
    config: Sequence[str] = (),
    show_detail: bool = False,
) -> None:
    """Pushes one commit adding files to the branch: on top of its newest commit when
    on_branch, else as its first commit. A refused push is tried again, fetching first."""
    detail = ""
    for attempt in range(PUSH_TRIES):
        if on_branch and attempt > 0:
            fetch_keys_branch(root, remote)
        parent = tracking_ref(remote) if on_branch else None
        commit = build_commit(root, files, message, parent, sentence)
        pushed = git(root, *config, "push", "--quiet", target, f"{commit}:refs/heads/{KEYS_BRANCH}")
        if pushed.returncode == 0:
            return
        detail = f"git push exited {pushed.returncode}: {pushed.stderr.decode(errors='replace').strip()}"
    raise StepFailed(sentence, detail, show_detail)


# ----------------- Keys -----------------
def laptop_key_path() -> Path:
    return Path.home() / ".config" / "mate-record" / "laptop-key.txt"


def read_laptop_key() -> Optional[x25519.Identity]:
    path = laptop_key_path()
    if not path.exists():
        return None
    return x25519.Identity.from_str(path.read_text(encoding="utf-8").strip())


def make_laptop_key() -> x25519.Identity:
    identity = x25519.Identity.generate()
    path = laptop_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Readable by its owner only, from the moment it exists.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as f:
        f.write(f"{identity}\n")
    return identity


def lock(values: Dict[str, str], recipients: List[x25519.Recipient]) -> bytes:
    locked = encrypt(json.dumps(values).encode("utf-8"), recipients, armored=True)
    return locked if isinstance(locked, bytes) else locked.encode("ascii")


def unlock(data: bytes, identity: x25519.Identity) -> Optional[Dict[str, str]]:
    """The values in data, or None when identity cannot open it."""
    try:
        plain = decrypt(data, [identity])
    except DecryptError:
        return None
    values = json.loads(plain)
    if not (isinstance(values, dict) and all(isinstance(values.get(name), str) for name in VALUE_NAMES)):
        raise StepFailed(READ, f"{LOCK_PATH} does not hold the values {', '.join(VALUE_NAMES)}")
    return {name: values[name] for name in VALUE_NAMES}


def file_name_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.@-]", "-", text)


def laptop_file(root: Path, identity: x25519.Identity) -> str:
    email = git_checked(root, EMAIL, "config", "user.email")
    if not email:
        raise StepFailed(EMAIL, "git config user.email is empty")
    public_half = str(identity.to_public())
    host = file_name_part(socket.gethostname())
    return f"{LAPTOPS_DIR}/{file_name_part(email)}/{host}-{public_half[4:12]}.pub"


def write_settings(project_dir: Path, values: Dict[str, str]) -> None:
    try:
        write_keys(project_dir, values)
    except (SetupError, SettingsFileError, OSError) as e:
        raise StepFailed(WRITE, str(e)) from e


# ----------------- setup and wait -----------------
def github_remote(project_dir: Path):
    """(checkout root, remote, "<owner>/<name>"), or None when the repo is not on GitHub."""
    checkout = find_checkout(project_dir)
    if checkout.main is None:
        return None
    try:
        remote = find_remote(checkout.root)
    except subprocess.CalledProcessError as e:
        raise StepFailed(REACH, f"git {' '.join(e.cmd[1:])} exited {e.returncode}") from e
    repo = github_repo(remote.address) if remote is not None else None
    if repo is None:
        return None
    return checkout.root, remote, repo


def join(project_dir: Path, root: Path, remote: Remote, lock_file: bytes) -> Dict[str, object]:
    identity = read_laptop_key()
    values = unlock(lock_file, identity) if identity is not None else None
    if values is not None:
        write_settings(project_dir, values)
        return {"result": "ready"}
    identity = identity or make_laptop_key()
    path = laptop_file(root, identity)
    files = {path: f"{identity.to_public()}\n".encode("ascii")}
    push_commit(root, remote, files, f"Let in {path}", target=remote.name, on_branch=True, sentence=SEND)
    return {"result": "joining"}


def lock_in(project_dir: Path, root: Path, remote: Remote, repo: str) -> Dict[str, object]:
    try:
        env = repo_settings_env(find_checkout(project_dir))
    except SettingsFileError as e:
        raise StepFailed(READ_SETTINGS, str(e), show_detail=True) from e
    holds_keys = env_holds_keys(env)
    if not is_admin(repo):
        return {"result": "not-admin", "settings_hold_keys": holds_keys}
    if not has_workflow_scope():
        return {"result": "need-workflow-scope"}
    if not holds_keys:
        return {"result": "need-keys"}

    public_key, secret_key = read_env_keys(env)
    values = dict(zip(VALUE_NAMES, (public_key, secret_key, read_env_host(env))))
    laptop = read_laptop_key() or make_laptop_key()
    ci = x25519.Identity.generate()
    files = {path: template.read_bytes() for path, template in BRANCH_TEMPLATES.items()}
    files[LOCK_PATH] = lock(values, [laptop.to_public(), ci.to_public()])
    files[CI_PUB_PATH] = f"{ci.to_public()}\n".encode("ascii")
    files[laptop_file(root, laptop)] = f"{laptop.to_public()}\n".encode("ascii")
    # The secret comes first: the first push starts the CI job, which needs it.
    store_ci_secret(repo, ci)
    push_commit(
        root,
        remote,
        files,
        "Share the repo's Langfuse keys with the laptops let in",
        target=f"https://github.com/{repo}.git",
        on_branch=False,
        sentence=FIRST_PUSH,
        config=GH_LOGIN,
        show_detail=True,
    )
    return {"result": "locked"}


def setup(project_dir: Path) -> Dict[str, object]:
    found = github_remote(project_dir)
    if found is None:
        return {"result": "not-github"}
    root, remote, repo = found
    lock_file = read_lock_file(root, remote)
    if lock_file is not None:
        return join(project_dir, root, remote, lock_file)
    return lock_in(project_dir, root, remote, repo)


def wait(project_dir: Path) -> None:
    """Prints READY or NOT_READY; raises StepFailed for a failure other than a failed fetch."""
    found = github_remote(project_dir)
    identity = read_laptop_key()
    if found is None or identity is None:
        raise StepFailed(LAPTOP, "wait runs only on a GitHub repo, after setup made this laptop's key")
    root, remote, _ = found
    deadline = monotonic() + LIMIT_SECONDS
    while True:
        try:
            lock_file = read_lock_file(root, remote)
        except StepFailed as e:
            log_failure(e)
            lock_file = None
        values = unlock(lock_file, identity) if lock_file is not None else None
        if values is not None:
            write_settings(project_dir, values)
            print(READY)
            return
        if monotonic() >= deadline:
            print(NOT_READY)
            return
        sleep(POLL_SECONDS)


def log_failure(error: StepFailed) -> None:
    path = state_files.LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [ERROR] repo_keys: {error.sentence} {error.detail}\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Share this GitHub repo's Langfuse keys with the laptops let in.")
    parser.add_argument("command", choices=["setup", "wait"])
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    project_dir = args.project_dir.resolve()
    try:
        if args.command == "setup":
            print(json.dumps(setup(project_dir)))
        else:
            wait(project_dir)
    except StepFailed as e:
        log_failure(e)
        if args.command == "wait":
            print(NOT_READY)
            return 1
        shown = f"{e.sentence} {e.detail}" if e.show_detail else e.sentence
        print(shown, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
