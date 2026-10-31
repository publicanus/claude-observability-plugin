"""Pins skills/langfuse-setup/repo_keys.py: the repo's Langfuse keys shared through
the branch mate-record-keys, against real git repositories.

A bare repo under tmp_path stands in for GitHub: git's url.<base>.insteadOf sends
https://github.com/ there, so each checkout's remote still reads as a GitHub address.
gh is replaced by FakeGh. The CI job is relock.py, run as the workflow runs it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from pyrage import DecryptError, decrypt, encrypt, x25519


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "langfuse-setup"
BRANCH = "mate-record-keys"
LOCK = "mate-record/langfuse-keys.age"
ADDRESS = "https://github.com/acme/widgets.git"
SETTINGS = Path(".claude") / "settings.local.json"
KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-FAKE-1", "LANGFUSE_SECRET_KEY": "sk-lf-FAKE-1"}
EU_CLOUD = "https://cloud.langfuse.com"
READY = "Tracing is ready. Your next session in this repo is traced."
NOT_READY = (
    "Tracing is not ready yet. Run /mate-record:langfuse-setup again later, or ask whoever manages this repo."
)
BRANCH_FILES = [
    ".github/workflows/mate-record-keys.yml",
    "mate-record/ci.pub",
    "mate-record/langfuse-keys.age",
    "mate-record/relock.py",
]


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def repo_keys() -> Any:
    return load("repo_keys_under_test", SKILL_DIR / "repo_keys.py")


@pytest.fixture(scope="module")
def relock() -> Any:
    return load("relock_under_test", SKILL_DIR / "keys_branch" / "relock.py")


def host_name() -> str:
    """This machine's name as a laptop file names it: other characters than a letter,
    digit, `.`, `@` or `-` become `-`."""
    return re.sub(r"[^A-Za-z0-9.@-]", "-", socket.gethostname())


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


class FakeGh:
    """Stands in for gh: answers repo view, auth status and secret set as GitHub would."""

    def __init__(self) -> None:
        self.installed = True
        self.logged_in = True
        self.permission = "ADMIN"
        self.scopes = "gist, read:org, repo, workflow"
        self.secrets: Dict[str, str] = {}

    def __call__(self, args: List[str], input: Optional[str] = None) -> Optional[subprocess.CompletedProcess]:
        if not self.installed:
            return None
        if not self.logged_in:
            return subprocess.CompletedProcess(args, 4, "", "not logged in")
        if args[:2] == ["repo", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps({"viewerPermission": self.permission}), "")
        if args[:2] == ["auth", "status"]:
            hosts = {"github.com": [{"active": True, "host": "github.com", "scopes": self.scopes}]}
            return subprocess.CompletedProcess(args, 0, json.dumps({"hosts": hosts}), "")
        if args[:2] == ["secret", "set"]:
            self.secrets[args[2]] = input or ""
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected gh call: {args}")


class World:
    """GitHub (a bare repo), laptops (each its own home folder and checkout) and the CI job."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relock: Any) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.relock = relock
        self.github = tmp_path / "github"
        self.bare = self.github / "acme" / "widgets.git"
        self.bare.mkdir(parents=True)
        git(self.bare, "init", "-q", "--bare")
        gitconfig = tmp_path / "gitconfig"
        gitconfig.write_text(
            "[user]\n\tname = Test\n\temail = anna@example.com\n"
            f'[url "{self.github.as_posix()}/"]\n\tinsteadOf = https://github.com/\n'
            "[init]\n\tdefaultBranch = main\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        seed = self.checkout("seed")
        git(seed, "commit", "-q", "--allow-empty", "-m", "first")
        git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
        self.use_laptop("laptop-1")

    def checkout(self, name: str) -> Path:
        path = (self.tmp_path / name).resolve()
        subprocess.run(["git", "clone", "-q", ADDRESS, str(path)], check=True, capture_output=True)
        return path

    def use_laptop(self, name: str) -> Path:
        home = self.tmp_path / "homes" / name
        home.mkdir(parents=True, exist_ok=True)
        self.monkeypatch.setenv("HOME", str(home))
        self.monkeypatch.setenv("USERPROFILE", str(home))
        return home

    def branch_files(self) -> List[str]:
        out = git(self.bare, "ls-tree", "-r", "--name-only", BRANCH)
        return out.splitlines()

    def has_branch(self) -> bool:
        return bool(git(self.bare, "branch", "--list", BRANCH).strip())

    def tip(self) -> str:
        return git(self.bare, "rev-parse", BRANCH).strip()

    def lock_file(self) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self.bare), "show", f"{BRANCH}:{LOCK}"], check=True, capture_output=True
        ).stdout

    def run_ci(self, ci_key: str) -> None:
        """What the workflow does: lock the file again for every laptop, commit and push."""
        work = (self.tmp_path / "ci").resolve()
        if work.exists():
            git(work, "fetch", "-q", "origin", BRANCH)
            git(work, "checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}")
        else:
            subprocess.run(
                ["git", "clone", "-q", "--branch", BRANCH, ADDRESS, str(work)], check=True, capture_output=True
            )
        self.monkeypatch.chdir(work)
        self.monkeypatch.setenv("MATE_RECORD_CI_KEY", ci_key)
        with contextlib.redirect_stdout(io.StringIO()):
            assert self.relock.main() == 0
        git(work, "add", LOCK)
        git(work, "commit", "-q", "-m", "Lock the Langfuse keys for every laptop")
        git(work, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relock: Any) -> World:
    return World(tmp_path, monkeypatch, relock)


@pytest.fixture
def gh(repo_keys: Any, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(repo_keys, "run_gh", fake)
    return fake


@pytest.fixture
def log_file(repo_keys: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state" / "langfuse_hook.log"
    monkeypatch.setattr(repo_keys.state_files, "LOG_FILE", path)
    return path


def write_settings(checkout: Path, env: Dict[str, str]) -> Path:
    path = checkout / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"env": env}), encoding="utf-8")
    return path


def read_env(checkout: Path) -> Dict[str, str]:
    return json.loads((checkout / SETTINGS).read_text(encoding="utf-8"))["env"]


def run(repo_keys: Any, capsys: Any, command: str, checkout: Path) -> tuple:
    code = repo_keys.main([command, "--project-dir", str(checkout)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def setup(repo_keys: Any, capsys: Any, checkout: Path) -> Dict[str, Any]:
    code, out, err = run(repo_keys, capsys, "setup", checkout)
    assert (code, err) == (0, "")
    return json.loads(out)


def opens(data: bytes, private_half: str) -> Optional[Dict[str, str]]:
    try:
        return json.loads(decrypt(data, [x25519.Identity.from_str(private_half.strip())]))
    except DecryptError:
        return None


def laptop_key(home: Path) -> str:
    return (home / ".config" / "mate-record" / "laptop-key.txt").read_text(encoding="utf-8")


def first_person_locks(world: World, repo_keys: Any, gh: FakeGh, capsys: Any) -> Path:
    """The first person, an admin with the keys in their settings file, runs setup on laptop-1."""
    checkout = world.checkout("first")
    write_settings(checkout, KEYS)
    assert setup(repo_keys, capsys, checkout) == {"result": "locked"}
    return checkout


# ----------------- First person -----------------


def test_an_admin_with_keys_locks_them_into_the_branch_for_their_laptop_and_ci(world, repo_keys, gh, capsys):
    main_before = git(world.bare, "rev-parse", "main")

    first_person_locks(world, repo_keys, gh, capsys)

    files = world.branch_files()
    laptops = [f for f in files if f.startswith("mate-record/laptops/")]
    assert [f for f in files if f not in laptops] == BRANCH_FILES
    host = host_name()
    public_half = str(x25519.Identity.from_str(laptop_key(world.use_laptop("laptop-1")).strip()).to_public())
    assert laptops == [f"mate-record/laptops/anna@example.com/{host}-{public_half[4:12]}.pub"]
    expected = dict(KEYS, LANGFUSE_BASE_URL=EU_CLOUD)
    assert opens(world.lock_file(), gh.secrets["MATE_RECORD_CI_KEY"]) == expected
    assert opens(world.lock_file(), laptop_key(world.use_laptop("laptop-1"))) == expected
    assert git(world.bare, "rev-parse", "main") == main_before
    assert git(world.bare, "rev-list", "--count", BRANCH).strip() == "1"


def test_the_lock_file_carries_the_settings_base_url(world, repo_keys, gh, capsys):
    checkout = world.checkout("first")
    write_settings(checkout, dict(KEYS, LANGFUSE_BASE_URL="https://langfuse.example.eu"))

    assert setup(repo_keys, capsys, checkout) == {"result": "locked"}

    values = opens(world.lock_file(), gh.secrets["MATE_RECORD_CI_KEY"])
    assert values is not None and values["LANGFUSE_BASE_URL"] == "https://langfuse.example.eu"


def test_an_admin_without_keys_is_asked_for_them_and_nothing_reaches_github(world, repo_keys, gh, capsys):
    checkout = world.checkout("first")

    assert setup(repo_keys, capsys, checkout) == {"result": "need-keys"}

    assert not world.has_branch()
    assert gh.secrets == {}


def test_an_admin_whose_gh_lacks_the_workflow_scope_is_told_and_nothing_reaches_github(
    world, repo_keys, gh, capsys
):
    gh.scopes = "gist, read:org, repo"
    checkout = world.checkout("first")
    write_settings(checkout, KEYS)

    assert setup(repo_keys, capsys, checkout) == {"result": "need-workflow-scope"}

    assert not world.has_branch()
    assert gh.secrets == {}


@pytest.mark.parametrize("gh_state", ["missing", "logged-out", "WRITE"])
@pytest.mark.parametrize("with_keys", [True, False], ids=["keys", "no-keys"])
def test_someone_who_cannot_be_shown_to_manage_the_repo_gets_not_admin_and_nothing_reaches_github(
    world, repo_keys, gh, capsys, gh_state, with_keys
):
    gh.installed = gh_state != "missing"
    gh.logged_in = gh_state != "logged-out"
    gh.permission = gh_state
    checkout = world.checkout("first")
    if with_keys:
        write_settings(checkout, KEYS)

    assert setup(repo_keys, capsys, checkout) == {"result": "not-admin", "settings_hold_keys": with_keys}

    assert not world.has_branch()
    assert gh.secrets == {}


@pytest.mark.parametrize("remote", [None, "/some/local/path.git", "git@gitlab.com:acme/widgets.git"])
def test_a_repo_not_on_github_gets_not_github(world, repo_keys, gh, capsys, tmp_path, remote):
    checkout = (tmp_path / "elsewhere").resolve()
    checkout.mkdir()
    git(checkout, "init", "-q")
    if remote is not None:
        git(checkout, "remote", "add", "origin", remote)

    assert setup(repo_keys, capsys, checkout) == {"result": "not-github"}


# ----------------- Next consultants and laptops -----------------


def test_a_new_laptop_asks_to_join_with_its_public_half_and_keeps_its_settings(world, repo_keys, gh, capsys):
    first_person_locks(world, repo_keys, gh, capsys)
    world.use_laptop("laptop-2")
    checkout = world.checkout("second")
    write_settings(checkout, {"OTHER": "1"})

    assert setup(repo_keys, capsys, checkout) == {"result": "joining"}

    public_half = str(x25519.Identity.from_str(laptop_key(world.use_laptop("laptop-2")).strip()).to_public())
    pub = f"mate-record/laptops/anna@example.com/{host_name()}-{public_half[4:12]}.pub"
    assert pub in world.branch_files()
    assert git(world.bare, "show", f"{BRANCH}:{pub}").strip() == public_half
    assert read_env(checkout) == {"OTHER": "1"}


def test_asking_again_pushes_a_new_commit_with_the_same_files(world, repo_keys, gh, capsys):
    first_person_locks(world, repo_keys, gh, capsys)
    world.use_laptop("laptop-2")
    checkout = world.checkout("second")
    assert setup(repo_keys, capsys, checkout) == {"result": "joining"}
    files, tip = world.branch_files(), world.tip()

    assert setup(repo_keys, capsys, checkout) == {"result": "joining"}

    assert world.branch_files() == files
    assert world.tip() != tip
    assert git(world.bare, "rev-parse", f"{world.tip()}^").strip() == tip


def test_a_laptop_let_in_gets_the_repos_values_without_waiting(world, repo_keys, gh, capsys):
    first_person_locks(world, repo_keys, gh, capsys)
    world.use_laptop("laptop-2")
    assert setup(repo_keys, capsys, world.checkout("second")) == {"result": "joining"}
    world.run_ci(gh.secrets["MATE_RECORD_CI_KEY"])
    world.use_laptop("laptop-2")
    fresh = world.checkout("second-again")

    assert setup(repo_keys, capsys, fresh) == {"result": "ready"}

    assert read_env(fresh) == dict(KEYS, LANGFUSE_BASE_URL=EU_CLOUD)
    assert ".claude/" not in git(fresh, "status", "--porcelain", "--untracked-files=all")


def test_the_repos_values_replace_other_keys_in_the_settings_file(world, repo_keys, gh, capsys):
    first_person_locks(world, repo_keys, gh, capsys)
    fresh = world.checkout("first-again")
    write_settings(fresh, {"LANGFUSE_PUBLIC_KEY": "pk-lf-OLD", "LANGFUSE_SECRET_KEY": "sk-lf-OLD", "OTHER": "1"})

    assert setup(repo_keys, capsys, fresh) == {"result": "ready"}

    assert read_env(fresh) == dict(KEYS, LANGFUSE_BASE_URL=EU_CLOUD, OTHER="1")


def test_the_first_laptop_still_opens_the_file_after_a_second_joined(world, repo_keys, gh, capsys):
    first_person_locks(world, repo_keys, gh, capsys)
    world.use_laptop("laptop-2")
    assert setup(repo_keys, capsys, world.checkout("second")) == {"result": "joining"}
    world.run_ci(gh.secrets["MATE_RECORD_CI_KEY"])

    assert opens(world.lock_file(), laptop_key(world.use_laptop("laptop-1"))) is not None
    assert opens(world.lock_file(), laptop_key(world.use_laptop("laptop-2"))) is not None


def test_a_push_refused_once_is_tried_again(world, repo_keys, gh, capsys):
    first_person_locks(world, repo_keys, gh, capsys)
    marker = world.tmp_path / "refused-once"
    hook = world.bare / "hooks" / "pre-receive"
    hook.write_text(f'#!/bin/sh\nif [ ! -e "{marker.as_posix()}" ]; then touch "{marker.as_posix()}"; exit 1; fi\n')
    hook.chmod(0o755)
    world.use_laptop("laptop-2")

    assert setup(repo_keys, capsys, world.checkout("second")) == {"result": "joining"}

    assert marker.exists()
    assert len([f for f in world.branch_files() if f.startswith("mate-record/laptops/")]) == 2


# ----------------- Failures -----------------


def test_a_remote_that_cannot_be_reached_fails_in_plain_words_and_logs_the_detail(
    world, repo_keys, gh, capsys, log_file
):
    checkout = world.checkout("second")
    world.bare.rename(world.bare.with_name("moved.git"))

    code, out, err = run(repo_keys, capsys, "setup", checkout)

    assert (code, out) == (1, "")
    assert len(err.strip().splitlines()) == 1
    for word in ("key", "lock", "ci", BRANCH):
        assert word not in err.lower()
    assert log_file.read_text(encoding="utf-8").strip()


def test_a_branch_without_the_lock_file_fails(world, repo_keys, gh, capsys, log_file):
    seed = world.tmp_path / "seed"
    git(seed, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    checkout = world.checkout("second")

    code, out, err = run(repo_keys, capsys, "setup", checkout)

    assert (code, out) == (1, "")
    assert BRANCH not in err


# ----------------- The wait -----------------


def joining_laptop(world: World, repo_keys: Any, gh: FakeGh, capsys: Any) -> Path:
    first_person_locks(world, repo_keys, gh, capsys)
    world.use_laptop("laptop-2")
    checkout = world.checkout("second")
    assert setup(repo_keys, capsys, checkout) == {"result": "joining"}
    return checkout


def test_the_wait_says_ready_once_ci_has_run_and_writes_the_settings(world, repo_keys, gh, capsys, monkeypatch):
    checkout = joining_laptop(world, repo_keys, gh, capsys)

    def ci_runs_meanwhile(seconds: float) -> None:
        world.run_ci(gh.secrets["MATE_RECORD_CI_KEY"])
        world.use_laptop("laptop-2")

    monkeypatch.setattr(repo_keys, "sleep", ci_runs_meanwhile)

    code, out, err = run(repo_keys, capsys, "wait", checkout)

    assert (code, out, err) == (0, READY + "\n", "")
    assert read_env(checkout) == dict(KEYS, LANGFUSE_BASE_URL=EU_CLOUD)


def test_the_wait_tries_again_after_a_failed_fetch(world, repo_keys, gh, capsys, monkeypatch, log_file):
    checkout = joining_laptop(world, repo_keys, gh, capsys)
    moved = world.bare.with_name("moved.git")
    world.bare.rename(moved)

    def github_comes_back_and_ci_runs(seconds: float) -> None:
        if moved.exists():
            moved.rename(world.bare)
            world.run_ci(gh.secrets["MATE_RECORD_CI_KEY"])
            world.use_laptop("laptop-2")

    monkeypatch.setattr(repo_keys, "sleep", github_comes_back_and_ci_runs)

    code, out, _ = run(repo_keys, capsys, "wait", checkout)

    assert (code, out) == (0, READY + "\n")


def test_the_wait_says_not_ready_at_its_limit_and_leaves_the_settings(world, repo_keys, gh, capsys, monkeypatch):
    checkout = joining_laptop(world, repo_keys, gh, capsys)
    clock = iter([0, 300, 700])
    monkeypatch.setattr(repo_keys, "monotonic", lambda: next(clock))
    monkeypatch.setattr(repo_keys, "sleep", lambda seconds: None)

    code, out, err = run(repo_keys, capsys, "wait", checkout)

    assert (code, out, err) == (0, NOT_READY + "\n", "")
    assert not (checkout / SETTINGS).exists()


# ----------------- The CI job -----------------


def test_relock_locks_the_file_for_ci_and_every_laptop_only(relock, tmp_path, monkeypatch):
    ci, laptop_a, laptop_b, outsider = (x25519.Identity.generate() for _ in range(4))
    root = tmp_path / "mate-record"
    (root / "laptops" / "anna@example.com").mkdir(parents=True)
    (root / "laptops" / "bob@example.com").mkdir(parents=True)
    (root / "ci.pub").write_text(f"{ci.to_public()}\n")
    (root / "laptops" / "anna@example.com" / "mac-1.pub").write_text(f"{laptop_a.to_public()}\n")
    (root / "laptops" / "bob@example.com" / "pc-2.pub").write_text(f"{laptop_b.to_public()}\n")
    (root / "langfuse-keys.age").write_bytes(encrypt(json.dumps(KEYS).encode(), [ci.to_public()]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MATE_RECORD_CI_KEY", str(ci))

    assert relock.main() == 0

    data = (root / "langfuse-keys.age").read_bytes()
    assert data.startswith(b"-----BEGIN AGE ENCRYPTED FILE-----")
    for identity in (ci, laptop_a, laptop_b):
        assert opens(data, str(identity)) == KEYS
    assert opens(data, str(outsider)) is None
