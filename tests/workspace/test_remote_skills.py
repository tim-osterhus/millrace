from __future__ import annotations

from pathlib import Path
from typing import Any

from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.workspace.remote_skills import install_remote_skill, parse_remote_skill_index

REMOTE_INDEX = """# Millrace Skills Index

## Optional Skills

| Skill | Description | Tags | Path | Status |
| --- | --- | --- | --- | --- |
| `browser-local-qa` | Browser QA guardrail. | `browser`, `qa` | `skills/browser-local-qa/SKILL.md` | available |
| `draft-skill` | Draft skill. | `draft` | `skills/draft-skill/SKILL.md` | draft |
"""


def test_parse_remote_skill_index_extracts_available_entries() -> None:
    entries = parse_remote_skill_index(REMOTE_INDEX)

    assert [entry.skill_id for entry in entries] == ["browser-local-qa", "draft-skill"]
    assert entries[0].description == "Browser QA guardrail."
    assert entries[0].tags == ("browser", "qa")
    assert entries[0].path == "skills/browser-local-qa/SKILL.md"
    assert entries[0].status == "available"


def test_install_remote_skill_downloads_package_tree_and_updates_index(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    fetched_urls: list[str] = []

    def fetch_text(url: str, *, timeout_seconds: float) -> str:
        del timeout_seconds
        fetched_urls.append(url)
        if url.endswith("/index.md"):
            return REMOTE_INDEX
        raise AssertionError(f"unexpected text URL: {url}")

    def fetch_bytes(url: str, *, timeout_seconds: float) -> bytes:
        del timeout_seconds
        fetched_urls.append(url)
        if url.endswith("/skills/browser-local-qa/SKILL.md"):
            return b"# Browser Local QA\n"
        if url.endswith("/skills/browser-local-qa/references/evidence.md"):
            return b"# Evidence\n"
        raise AssertionError(f"unexpected bytes URL: {url}")

    def fetch_json(url: str, *, timeout_seconds: float) -> dict[str, Any]:
        del timeout_seconds
        fetched_urls.append(url)
        assert "git/trees/main?recursive=1" in url
        return {
            "sha": "tree-123",
            "tree": [
                {"path": "skills/browser-local-qa", "type": "tree"},
                {"path": "skills/browser-local-qa/SKILL.md", "type": "blob"},
                {"path": "skills/browser-local-qa/references", "type": "tree"},
                {"path": "skills/browser-local-qa/references/evidence.md", "type": "blob"},
                {"path": "skills/other-skill/SKILL.md", "type": "blob"},
            ],
        }

    result = install_remote_skill(
        paths.skills_dir,
        "browser-local-qa",
        fetch_text=fetch_text,
        fetch_json=fetch_json,
        fetch_bytes=fetch_bytes,
    )

    assert result.skill_id == "browser-local-qa"
    assert result.installed_files == (
        "SKILL.md",
        "references/evidence.md",
    )
    assert paths.skills_dir.joinpath("browser-local-qa", "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# Browser Local QA\n"
    assert paths.skills_dir.joinpath(
        "browser-local-qa", "references", "evidence.md"
    ).read_text(encoding="utf-8") == "# Evidence\n"
    assert "- browser-local-qa: browser-local-qa/SKILL.md" in paths.skills_dir.joinpath(
        "skills_index.md"
    ).read_text(encoding="utf-8")
    assert "\"operation\": \"install_remote\"" in paths.skills_dir.joinpath(
        "skill_operations.jsonl"
    ).read_text(encoding="utf-8")
    assert paths.skills_dir.joinpath("browser-local-qa", "remote_source.json").is_file()
    assert any(url.endswith("/index.md") for url in fetched_urls)
