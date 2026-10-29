"""Pins the repo name every trace carries, as the tag `repo:<name>` and the metadata
key `repo`, so a Langfuse dashboard can total token use per repo.

Each test builds real git checkouts in a temp folder; the git config they see is a
temp one, never the machine's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import git, write_settings


def make_repo(path: Path, remotes: dict[str, str]) -> Path:
    path.mkdir()
    git(path, "init", "-q")
    for name, address in remotes.items():
        git(path, "remote", "add", name, address)
    return path


def test_origin_names_the_repo(hook_module, isolated_hook_state, tmp_path):
    repo = make_repo(
        tmp_path / "checkout",
        {"aaa": "git@github.com:acme/till.git", "origin": "git@github.com:acme/shop.git"},
    )

    assert hook_module.read_repo_name(str(repo)) == "shop"


def test_without_origin_the_first_remote_by_name_names_the_repo(
    hook_module, isolated_hook_state, tmp_path
):
    repo = make_repo(
        tmp_path / "checkout",
        {"work": "git@github.com:acme/till.git", "upstream": "git@github.com:acme/shop.git"},
    )

    assert hook_module.read_repo_name(str(repo)) == "shop"


@pytest.mark.parametrize(
    "address",
    ["git@github.com:acme/shop.git", "https://github.com/jaap/shop"],
    ids=["scp-with-dot-git", "https-fork-without-dot-git"],
)
def test_the_name_is_the_last_part_of_the_address_without_dot_git(
    hook_module, isolated_hook_state, tmp_path, address
):
    repo = make_repo(tmp_path / "checkout", {"origin": address})

    assert hook_module.read_repo_name(str(repo)) == "shop"


def test_a_folder_outside_git_is_named_after_the_folder(hook_module, isolated_hook_state, tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()

    assert hook_module.read_repo_name(str(folder)) == "shop"


def test_all_worktrees_of_a_repo_without_remote_share_the_main_folder_name(
    hook_module, isolated_hook_state, tmp_path
):
    main = make_repo(tmp_path / "shop", {})
    git(main, "commit", "-q", "--allow-empty", "-m", "first")
    worktree = tmp_path / "shop-feature"
    git(main, "worktree", "add", "-q", str(worktree))

    assert hook_module.read_repo_name(str(main)) == "shop"
    assert hook_module.read_repo_name(str(worktree)) == "shop"


def test_every_observation_carries_the_repo_and_never_the_address(
    hook_module, isolated_hook_state, fake_langfuse, fixture_transcript_path, tmp_path, monkeypatch
):
    token = "ghp_0123456789abcdefSECRET"
    repo = make_repo(
        tmp_path / "checkout", {"origin": f"https://jaap:{token}@github.com/acme/shop.git"}
    )
    write_settings(repo, {"LANGFUSE_PUBLIC_KEY": "public", "LANGFUSE_SECRET_KEY": "secret"})
    config = hook_module.get_langfuse_config(str(repo))

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-repo", fixture_transcript_path("async_agent_completed")
    )

    assert emitted > 0
    assert len({obs.name for obs in fake_langfuse.observations}) > 1
    for obs in fake_langfuse.observations:
        assert obs.propagated["metadata"]["repo"] == "shop", obs.name
        assert "repo:shop" in obs.propagated["tags"], obs.name
    sent = json.dumps(
        [[obs.kwargs, obs.propagated] for obs in fake_langfuse.observations], default=str
    )
    assert token not in sent
    assert "github.com" not in sent
