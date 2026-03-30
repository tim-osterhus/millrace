from __future__ import annotations

import json
from pathlib import Path

from millrace_engine.control import EngineControl
from millrace_engine.config import load_engine_config
from millrace_engine.contracts import StageType


MILLRACE_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_EXECUTION_STAGES = (
    StageType.BUILDER,
    StageType.INTEGRATION,
    StageType.QA,
    StageType.HOTFIX,
    StageType.DOUBLECHECK,
    StageType.TROUBLESHOOT,
    StageType.CONSULT,
    StageType.UPDATE,
)
EXTERNAL_FIXTURE_PATH = "/".join(
    ("ref-framework", "millrace-temp-main", "agents", "tools", "fixtures")
)


def test_packaged_docs_and_operator_assets_exist() -> None:
    for relative in (
        "README.md",
        "docs/RUNTIME_DEEP_DIVE.md",
        "ADVISOR.md",
        "OPERATOR_GUIDE.md",
        "docs/TUI_DOCUMENTATION.md",
    ):
        assert (MILLRACE_ROOT / relative).exists(), relative

    assets_root = MILLRACE_ROOT / "millrace_engine" / "assets"
    for relative in (
        "README.md",
        "ADVISOR.md",
        "OPERATOR_GUIDE.md",
        "docs/RUNTIME_DEEP_DIVE.md",
        "docs/TUI_DOCUMENTATION.md",
        "millrace.toml",
    ):
        assert (assets_root / relative).exists(), relative

    manifest = json.loads((assets_root / "manifest.json").read_text(encoding="utf-8"))
    manifest_paths = {entry["path"] for entry in manifest["files"]}
    assert "README.md" in manifest_paths
    assert "OPERATOR_GUIDE.md" in manifest_paths
    assert "docs/RUNTIME_DEEP_DIVE.md" in manifest_paths
    assert "docs/TUI_DOCUMENTATION.md" in manifest_paths

    readme = (MILLRACE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "autonomous" in readme.lower()
    assert "## Why Millrace Exists" in readme
    assert "## How Millrace Is Different" in readme
    assert "## Design Philosophy" in readme
    assert "## Initialized Workspace Layout" in readme
    assert "they are not expected at the public repo root" in readme.lower()
    assert "python3 -m millrace_engine init /absolute/path/to/workspace" in readme
    assert "OPERATOR_GUIDE.md" in readme
    assert "ADVISOR.md" in readme
    assert "millrace_engine init" in readme

    advisor = (MILLRACE_ROOT / "ADVISOR.md").read_text(encoding="utf-8")
    assert "This file is for agents acting as the operator shell" in advisor
    assert "This prompt assumes you are operating inside an initialized Millrace workspace" in advisor
    assert "millrace_engine init" in advisor
    assert "health --json" in advisor
    assert "publish preflight --json" in advisor


def test_default_public_stage_prompt_assets_exist(tmp_path: Path) -> None:
    workspace = tmp_path / "public-stage-workspace"
    init_result = EngineControl.init_workspace(workspace)

    assert init_result.applied is True
    assert (workspace / "docs" / "TUI_DOCUMENTATION.md").exists()

    loaded = load_engine_config(workspace / "millrace.toml")
    live_agents_root = (workspace / "agents").resolve()

    for stage in PUBLIC_EXECUTION_STAGES:
        prompt_path = loaded.config.stages[stage].prompt_file
        assert prompt_path is not None, stage.value
        assert prompt_path.exists(), prompt_path
        assert prompt_path.is_relative_to(live_agents_root), prompt_path


def test_packaged_tests_do_not_reference_repo_external_fixtures() -> None:
    for path in sorted((MILLRACE_ROOT / "tests").glob("test_*.py")):
        contents = path.read_text(encoding="utf-8")
        assert EXTERNAL_FIXTURE_PATH not in contents, path.name
