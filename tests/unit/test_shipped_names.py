"""The plugin's own files never name the plugin's former name, langfuse-observability."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_DIRS = [".claude-plugin", "bin", "hooks", "skills"]
FORMER_NAME = "langfuse-observability"


def test_no_shipped_file_names_the_former_plugin_name() -> None:
    shipped = [
        path
        for folder in SHIPPED_DIRS
        for path in sorted((REPO_ROOT / folder).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ]
    assert shipped, "no shipped files found"

    naming_it = [
        str(path.relative_to(REPO_ROOT))
        for path in shipped
        if FORMER_NAME in path.read_text(encoding="utf-8")
    ]

    assert naming_it == []
