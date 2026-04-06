from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from millrace_engine.baseline_assets import (
    iter_packaged_baseline_directories,
    iter_packaged_baseline_files,
    packaged_baseline_asset,
)
from millrace_engine.workspace_init import (
    apply_workspace_upgrade,
    initialize_workspace,
    iter_runtime_owned_workspace_directories,
    iter_runtime_owned_workspace_files,
    preview_workspace_upgrade,
    WorkspaceInitError,
)


MILLRACE_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_modules_import() -> None:
    module_names = [
        "millrace_engine",
        "millrace_engine.engine",
        "millrace_engine.control",
        "millrace_engine.cli",
        "millrace_engine.config",
        "millrace_engine.contracts",
        "millrace_engine.events",
        "millrace_engine.status",
        "millrace_engine.queue",
        "millrace_engine.workspace_init",
        "millrace_engine.runner",
        "millrace_engine.telemetry",
        "millrace_engine.diagnostics",
        "millrace_engine.markdown",
        "millrace_engine.registry",
        "millrace_engine.paths",
        "millrace_engine.planes",
        "millrace_engine.stages",
        "millrace_engine.adapters",
        "millrace_engine.policies",
    ]

    for name in module_names:
        assert importlib.import_module(name) is not None


def test_required_runtime_scaffold_paths_exist() -> None:
    required_paths = [
        "millrace_engine",
        "tests",
    ]

    for relative in required_paths:
        assert (MILLRACE_ROOT / relative).exists(), relative

    packaged_scaffold_paths = [
        "agents",
        "agents/outline.md",
        "agents/staging_manifest.yml",
        "agents/status_contract.md",
        "agents/registry",
        "agents/registry/stages",
        "agents/registry/loops/execution",
        "agents/registry/loops/research",
        "agents/registry/modes",
        "agents/registry/task_authoring",
        "agents/registry/model_profiles",
        "agents/.locks",
        "agents/.deferred",
    ]

    manifest_paths = {
        entry["path"]
        for entry in (*iter_packaged_baseline_directories(), *iter_packaged_baseline_files())
        if isinstance(entry.get("path"), str)
    }

    for relative in packaged_scaffold_paths:
        assert relative in manifest_paths, relative

    for relative in iter_runtime_owned_workspace_directories():
        assert relative not in manifest_paths, relative
    for relative in iter_runtime_owned_workspace_files():
        assert relative not in manifest_paths, relative


def test_packaged_runtime_command_mailbox_paths_exist() -> None:
    for relative in (
        "agents/.runtime",
        "agents/.runtime/commands",
        "agents/.runtime/commands/incoming",
        "agents/.runtime/commands/processed",
        "agents/.runtime/commands/failed",
    ):
        assert packaged_baseline_asset(relative).is_dir(), relative


def test_workspace_init_creates_runtime_owned_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)

    for relative in iter_runtime_owned_workspace_directories():
        assert (workspace / relative).is_dir(), relative


def test_workspace_init_creates_runtime_owned_files_with_expected_bootstrap_contents(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)

    expected_contents = {
        "agents/status.md": "### IDLE\n",
        "agents/research_status.md": "### IDLE\n",
        "agents/size_status.md": "### SMALL\n",
        "agents/tasks.md": "# Active Task\n",
        "agents/tasksbacklog.md": "# Task Backlog\n",
        "agents/tasksarchive.md": "# Task Archive\n",
        "agents/tasksbackburner.md": "# Task Backburner\n",
        "agents/tasksblocker.md": "# Task Blockers\n",
        "agents/taskspending.md": "# Tasks Pending\n",
        "agents/historylog.md": (
            "# History Log\n\n"
            "This file is the short human-readable index for runtime history.\n\n"
            "Detailed entries belong under `historylog/` and use UTC filenames such as "
            "`2026-03-16T21-05-33Z__stage-qa__task-123.md`.\n"
        ),
        "agents/engine_events.log": "",
        "agents/research_events.md": "# Research Events\n",
        "agents/expectations.md": "# Expectations\n",
        "agents/gaps.md": "# Gaps\n\nNo active gaps recorded.\n",
        "agents/iterations.md": "# Iterations\n\nNo recorded iterations yet.\n",
        "agents/quickfix.md": "# Quickfix\n",
        "agents/retrospect.md": "# Retrospect\n\n## Entries (newest first)\n",
        "agents/roadmap.md": "# Project Roadmap\n\nNo roadmap entries yet.\n",
        "agents/roadmapchecklist.md": "# Roadmap Checklist\n\nNo checklist entries yet.\n",
        "agents/audit_history.md": (
            "# Audit History\n\n"
            "Local audit outcomes recorded by `millrace_engine.research.audit` (newest first).\n"
        ),
    }

    for relative in iter_runtime_owned_workspace_files():
        path = workspace / relative
        assert path.is_file(), relative

    for relative, contents in expected_contents.items():
        assert (workspace / relative).read_text(encoding="utf-8") == contents


def test_workspace_upgrade_preview_classifies_manifest_and_preserved_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    (workspace / "README.md").write_text("locally customized readme\n", encoding="utf-8")
    (workspace / "agents" / "status.md").write_text("### BLOCKED\n", encoding="utf-8")
    (workspace / "agents" / "local-notes.md").write_text("keep me\n", encoding="utf-8")
    (workspace / "OPERATOR_GUIDE.md").unlink()

    report = preview_workspace_upgrade(workspace)

    assert report.workspace_root == workspace.resolve()
    assert report.bundle_version == "baseline-bundle-v1"
    assert "OPERATOR_GUIDE.md" in report.would_create
    assert "README.md" in report.would_update
    assert "millrace.toml" in report.unchanged
    assert "agents/status.md" in report.preserved_runtime_owned
    assert "agents/local-notes.md" in report.preserved_operator_owned
    assert report.conflicting_paths == ()


def test_workspace_upgrade_preview_does_not_mutate_workspace_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    readme_path = workspace / "README.md"
    status_path = workspace / "agents" / "status.md"
    readme_path.write_text("custom readme\n", encoding="utf-8")
    status_path.write_text("### QUICKFIX_NEEDED\n", encoding="utf-8")

    preview_workspace_upgrade(workspace)

    assert readme_path.read_text(encoding="utf-8") == "custom readme\n"
    assert status_path.read_text(encoding="utf-8") == "### QUICKFIX_NEEDED\n"


def test_workspace_upgrade_apply_refreshes_manifest_files_and_preserves_owned_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    (workspace / "README.md").write_text("custom readme\n", encoding="utf-8")
    (workspace / "agents" / "status.md").write_text("### BLOCKED\n", encoding="utf-8")
    (workspace / "notes.md").write_text("keep me\n", encoding="utf-8")
    (workspace / "OPERATOR_GUIDE.md").unlink()

    preview = preview_workspace_upgrade(workspace)
    report = apply_workspace_upgrade(workspace)

    assert report.workspace_root == workspace.resolve()
    assert report.created_files == preview.would_create
    assert report.updated_files == preview.would_update
    assert report.created_file_count == len(report.created_files)
    assert report.updated_file_count == len(report.updated_files)
    assert report.created_file_count == 1
    assert report.updated_file_count >= 1
    assert "OPERATOR_GUIDE.md" in report.created_files
    assert "README.md" in report.updated_files
    assert report.preserved_runtime_owned == preview.preserved_runtime_owned
    assert report.preserved_operator_owned == preview.preserved_operator_owned
    assert (workspace / "README.md").read_text(encoding="utf-8") == packaged_baseline_asset("README.md").read_text(
        encoding="utf-8"
    )
    assert (workspace / "OPERATOR_GUIDE.md").read_text(encoding="utf-8") == packaged_baseline_asset(
        "OPERATOR_GUIDE.md"
    ).read_text(encoding="utf-8")
    assert (workspace / "agents" / "status.md").read_text(encoding="utf-8") == "### BLOCKED\n"
    assert (workspace / "notes.md").read_text(encoding="utf-8") == "keep me\n"


def test_workspace_upgrade_apply_fails_before_mutation_on_conflicting_manifest_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    readme_before = (workspace / "README.md").read_text(encoding="utf-8")
    guide_path = workspace / "OPERATOR_GUIDE.md"
    guide_path.unlink()
    guide_path.mkdir()

    with pytest.raises(WorkspaceInitError, match="conflicting manifest paths"):
        apply_workspace_upgrade(workspace)

    assert (workspace / "README.md").read_text(encoding="utf-8") == readme_before
    assert guide_path.is_dir()
