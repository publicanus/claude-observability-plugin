"""Pins the plugin list every trace carries in its metadata key `plugins`: each plugin
the turn used, with the version of its install for the session's repo, so a plugin's
maintainer can compare plugins version against version.

The version tests write a fake Claude Code installed_plugins.json in the test's home
folder (see isolated_hook_state); the git checkouts they use are real, in a temp folder.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


# ----------------- installed_plugins.json -----------------

def install(scope: str, version: Optional[str], project: Optional[Path] = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"scope": scope, "installPath": "/cache/somewhere"}
    if version is not None:
        entry["version"] = version
    if project is not None:
        entry["projectPath"] = str(project)
    return entry


def write_installed_plugins(hook_module: Any, plugins: Dict[str, List[Dict[str, Any]]]) -> None:
    path = hook_module.INSTALLED_PLUGINS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "plugins": plugins}), encoding="utf-8")


def test_the_repo_install_counts_over_the_machine_wide_one_cut_at_the_first_dash(
    hook_module, isolated_hook_state, tmp_path
):
    repo = tmp_path / "shop"
    write_installed_plugins(hook_module, {
        "mate-core@mate-marketplace": [
            install("user", "8.8.0"),
            install("project", "8.9.0-0ae331d8a58b", repo),
        ],
    })

    assert hook_module.read_plugin_versions(repo) == {"mate-core": "8.9.0"}


def test_without_a_repo_install_the_machine_wide_install_counts(
    hook_module, isolated_hook_state, tmp_path
):
    write_installed_plugins(hook_module, {
        "mate-core@mate-marketplace": [
            install("project", "9.0.0", tmp_path / "other-repo"),
            install("user", "8.8.0-1234abcd"),
        ],
    })

    assert hook_module.read_plugin_versions(tmp_path / "shop") == {"mate-core": "8.8.0"}


def test_the_local_install_counts_over_the_project_install_of_the_same_repo(
    hook_module, isolated_hook_state, tmp_path
):
    repo = tmp_path / "shop"
    write_installed_plugins(hook_module, {
        "pymate@mate-marketplace": [
            install("project", "2.0.0", repo),
            install("local", "6.0.0", repo),
        ],
    })

    assert hook_module.read_plugin_versions(repo) == {"pymate": "6.0.0"}


@pytest.mark.parametrize(
    "version",
    ["dbd4588f9e10", "41f619d61b99", "unknown", None],
    ids=["bare-commit-hash", "bare-commit-hash-starting-with-a-digit", "unknown", "missing"],
)
def test_a_version_not_made_of_digits_and_dots_counts_as_no_version(
    hook_module, isolated_hook_state, tmp_path, version
):
    repo = tmp_path / "shop"
    write_installed_plugins(hook_module, {
        "document-skills@anthropic-agent-skills": [
            install("user", "1.0.0"),
            install("project", version, repo),
        ],
    })

    assert hook_module.read_plugin_versions(repo) == {"document-skills": None}


def test_a_machine_without_installed_plugins_has_no_versions(hook_module, isolated_hook_state, tmp_path):
    assert hook_module.read_plugin_versions(tmp_path / "shop") == {}


def test_an_unreadable_plugins_file_fails_naming_the_file(hook_module, isolated_hook_state, tmp_path):
    path = hook_module.INSTALLED_PLUGINS_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(hook_module.PluginsFileError, match="installed_plugins.json"):
        hook_module.read_plugin_versions(tmp_path / "shop")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_a_session_in_a_worktree_uses_the_installs_of_the_main_checkout(
    hook_module, isolated_hook_state, tmp_path, monkeypatch
):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    main = tmp_path / "shop"
    main.mkdir()
    git(main, "init", "-q")
    git(main, "commit", "-q", "--allow-empty", "-m", "first")
    worktree = tmp_path / "shop-feature"
    git(main, "worktree", "add", "-q", str(worktree))
    write_installed_plugins(hook_module, {
        "mate-core@mate-marketplace": [install("project", "8.9.0-0ae331d8a58b", main)],
    })

    config = hook_module.get_langfuse_config(str(worktree))

    assert config.plugin_versions == {"mate-core": "8.9.0"}


# ----------------- The plugins a turn used -----------------

def user_row(text: str, uuid: str = "user-1") -> Dict[str, Any]:
    return {
        "type": "user",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "uuid": uuid,
        "message": {"role": "user", "content": text},
    }


def assistant_row(uuid: str, content: List[Dict[str, Any]], **fields: Any) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:01.000Z",
        "uuid": uuid,
        **fields,
        "message": {"id": f"msg-{uuid}", "role": "assistant", "model": "claude-test", "content": content},
    }


def tool_use(tool_use_id: str, name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}


def text(value: str) -> Dict[str, Any]:
    return {"type": "text", "text": value}


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_subagent(transcript: Path, agent_id: str, meta: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    subagents = transcript.with_suffix("") / "subagents"
    write_jsonl(subagents / f"agent-{agent_id}.jsonl", rows)
    (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def make_config(hook_module: Any, plugin_versions: Dict[str, Optional[str]]) -> Any:
    return hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", repo_name="shop",
        plugin_versions=plugin_versions,
    )


def trace_plugins(fake_langfuse: Any, root_name: str = "Conversational Turn") -> List[Any]:
    roots = [obs for obs in fake_langfuse.observations if obs.name == root_name]
    return [root.kwargs["metadata"]["plugins"] for root in roots]


def test_a_trace_lists_the_plugins_whose_skills_and_commands_ran_in_the_turn(
    hook_module, isolated_hook_state, fake_langfuse
):
    transcript = isolated_hook_state / "transcript.jsonl"
    write_jsonl(transcript, [
        user_row("<command-name>/mate-core:build</command-name>"),
        assistant_row("a1", [text("Building.")],
                      attributionSkill="mate-core:build", attributionPlugin="mate-core"),
        assistant_row("a2", [tool_use("toolu_skill", "Skill", {"skill": "pymate:lint"})]),
        assistant_row("a3", [tool_use("toolu_repo_skill", "Skill", {"skill": "repo-skill"})]),
        assistant_row("a4", [text("Done.")], attributionPlugin="sfmate"),
    ])
    config = make_config(hook_module, {"mate-core": "8.9.0", "pymate": "7.1.0", "sfmate": "3.0.0"})

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-1", transcript)

    assert trace_plugins(fake_langfuse) == [[
        {"name": "mate-core", "version": "8.9.0"},
        {"name": "pymate", "version": "7.1.0"},
        {"name": "sfmate", "version": "3.0.0"},
    ]]


def test_a_trace_lists_the_plugins_of_its_agents_and_of_what_ran_in_them(
    hook_module, isolated_hook_state, fake_langfuse
):
    transcript = isolated_hook_state / "transcript.jsonl"
    write_jsonl(transcript, [
        user_row("Review it."),
        assistant_row("a1", [
            tool_use("toolu_reviewer", "Agent", {"subagent_type": "mate-core:reviewer", "prompt": "go"}),
            tool_use("toolu_plain", "Agent", {"prompt": "go"}),
        ]),
    ])
    write_subagent(
        transcript, "reviewer",
        {"agentType": "mate-core:reviewer", "toolUseId": "toolu_reviewer"},
        [
            user_row("Review.", uuid="r-user"),
            assistant_row("r1", [
                tool_use("toolu_nested", "Agent", {"subagent_type": "pymate:developer", "prompt": "fix"}),
            ]),
        ],
    )
    write_subagent(
        transcript, "nested",
        {"agentType": "pymate:developer", "toolUseId": "toolu_nested"},
        [
            user_row("Fix.", uuid="n-user"),
            assistant_row("n1", [tool_use("toolu_n_skill", "Skill", {"skill": "bashmate:shellcheck"})]),
        ],
    )
    write_subagent(
        transcript, "plain",
        {"agentType": "general-purpose", "toolUseId": "toolu_plain"},
        [user_row("Look.", uuid="p-user"), assistant_row("p1", [text("Looked.")], attributionPlugin="mate-hurt")],
    )
    config = make_config(
        hook_module, {"mate-core": "8.9.0", "pymate": "7.1.0", "bashmate": "2.0.0", "mate-hurt": "1.0.0"}
    )

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-1", transcript)

    assert trace_plugins(fake_langfuse) == [[
        {"name": "bashmate", "version": "2.0.0"},
        {"name": "mate-core", "version": "8.9.0"},
        {"name": "mate-hurt", "version": "1.0.0"},
        {"name": "pymate", "version": "7.1.0"},
    ]]


def test_a_plugin_with_no_version_is_listed_without_one(hook_module, isolated_hook_state, fake_langfuse):
    transcript = isolated_hook_state / "transcript.jsonl"
    write_jsonl(transcript, [
        user_row("Draw it."),
        assistant_row("a1", [tool_use("toolu_skill", "Skill", {"skill": "document-skills:pdf"})]),
        assistant_row("a2", [tool_use("toolu_skill2", "Skill", {"skill": "local-only:thing"})]),
    ])
    config = make_config(hook_module, {"document-skills": None})

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-1", transcript)

    assert trace_plugins(fake_langfuse) == [[{"name": "document-skills"}, {"name": "local-only"}]]


def test_a_teammate_trace_lists_the_plugins_its_own_turn_used(
    hook_module, isolated_hook_state, fake_langfuse
):
    transcript = isolated_hook_state / "transcript.jsonl"
    write_jsonl(transcript, [
        user_row("Spawn the team."),
        assistant_row("a1", [
            tool_use("toolu_mate", "Agent", {"name": "builder", "subagent_type": "mate-core:developer", "prompt": "go"}),
        ]),
    ])
    write_subagent(
        transcript, "builder-hash",
        {"agentType": "mate-core:developer", "name": "builder", "taskKind": "in_process_teammate"},
        [
            user_row("Build.", uuid="t-user"),
            assistant_row("t1", [tool_use("toolu_t_skill", "Skill", {"skill": "pymate:lint"})]),
        ],
    )
    config = make_config(hook_module, {"mate-core": "8.9.0", "pymate": "7.1.0"})

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-1", transcript, flush_deferred_agent_turns=True
    )

    assert trace_plugins(fake_langfuse, "Teammate Turn") == [[
        {"name": "mate-core", "version": "8.9.0"},
        {"name": "pymate", "version": "7.1.0"},
    ]]
