from __future__ import annotations

import importlib
import json
from pathlib import Path
import shutil

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
from millrace_engine.research.state import load_research_runtime_state


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

    runtime_owned_paths = set(iter_runtime_owned_workspace_directories())
    for relative in (
        "agents/compounding",
        "agents/compounding/procedures",
        "agents/compounding/context_facts",
        "agents/compounding/harness_candidates",
        "agents/compounding/harness_recommendations",
        "agents/lab",
        "agents/lab/harness_requests",
        "agents/lab/harness_proposals",
        "agents/lab/harness_comparisons",
    ):
        assert relative in runtime_owned_paths, relative


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


def test_workspace_upgrade_preview_reports_missing_runtime_owned_paths_without_mutation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    shutil.rmtree(workspace / "agents" / "compounding")
    shutil.rmtree(workspace / "agents" / "lab")
    (workspace / "agents" / "gaps.md").unlink()

    report = preview_workspace_upgrade(workspace)

    assert "agents/compounding" in report.would_materialize_runtime_owned
    assert "agents/compounding/procedures" in report.would_materialize_runtime_owned
    assert "agents/lab" in report.would_materialize_runtime_owned
    assert "agents/lab/harness_requests" in report.would_materialize_runtime_owned
    assert "agents/gaps.md" in report.would_materialize_runtime_owned
    assert not (workspace / "agents" / "compounding").exists()
    assert not (workspace / "agents" / "lab").exists()
    assert not (workspace / "agents" / "gaps.md").exists()


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


def test_workspace_upgrade_preview_reports_noop_persisted_state_migration_when_research_state_is_absent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)

    report = preview_workspace_upgrade(workspace)

    migration = report.persisted_state_migrations[0]
    assert migration.state_family == "research_runtime_state"
    assert migration.action == "none"
    assert migration.would_write_state_file is False
    assert migration.breadcrumb_file_count == 0
    assert migration.summary == "No persisted research runtime state or deferred breadcrumbs require migration."


def test_workspace_upgrade_rewrites_legacy_research_state_through_explicit_migration_seam(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    state_path = workspace / "agents" / "research_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "current_mode": "STUB",
                "previous_mode": "STUB",
                "reason": "legacy bootstrap",
                "cycle_count": 0,
                "transition_count": 0,
                "pending": [
                    {
                        "event_type": "handoff.idea_submitted",
                        "received_at": "2026-04-04T12:00:00Z",
                        "payload": {"idea_id": "IDEA-LEGACY-001"},
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    preview = preview_workspace_upgrade(workspace)
    preview_migration = preview.persisted_state_migrations[0]
    assert preview_migration.action == "rewrite_state"
    assert preview_migration.would_write_state_file is True

    report = apply_workspace_upgrade(workspace)
    migration = report.persisted_state_migrations[0]
    assert migration.action == "rewrite_state"
    assert migration.wrote_state_file is True

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["mode_reason"] == "legacy bootstrap"
    assert payload["last_mode"] == "STUB"
    assert payload["deferred_requests"][0]["event_type"] == "handoff.idea_submitted"
    assert payload["updated_at"] == "2026-04-04T12:00:00Z"
    assert "pending" not in payload
    assert "previous_mode" not in payload
    assert "reason" not in payload


def test_workspace_upgrade_materializes_research_state_from_breadcrumbs_without_deleting_breadcrumbs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    breadcrumb_path = workspace / "agents" / ".deferred" / "idea-submitted.json"
    breadcrumb_path.parent.mkdir(parents=True, exist_ok=True)
    breadcrumb_path.write_text(
        json.dumps(
            {
                "event_type": "handoff.idea_submitted",
                "received_at": "2026-04-04T12:05:00Z",
                "payload": {"idea_id": "IDEA-BREADCRUMB-001"},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    preview = preview_workspace_upgrade(workspace)
    preview_migration = preview.persisted_state_migrations[0]
    assert preview_migration.action == "materialize_from_breadcrumbs"
    assert preview_migration.would_write_state_file is True
    assert preview_migration.breadcrumb_file_count == 1

    report = apply_workspace_upgrade(workspace)
    migration = report.persisted_state_migrations[0]
    assert migration.action == "materialize_from_breadcrumbs"
    assert migration.wrote_state_file is True
    assert breadcrumb_path.exists()

    state = load_research_runtime_state(
        workspace / "agents" / "research_state.json",
        deferred_dir=workspace / "agents" / ".deferred",
    )
    assert state is not None
    assert state.current_mode.value == "STUB"
    assert len(state.deferred_requests) == 1
    assert state.deferred_requests[0].event_type.value == "handoff.idea_submitted"


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


def test_workspace_upgrade_apply_materializes_missing_runtime_owned_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    shutil.rmtree(workspace / "agents" / "compounding")
    shutil.rmtree(workspace / "agents" / "lab")
    (workspace / "agents" / "gaps.md").unlink()

    report = apply_workspace_upgrade(workspace)

    assert "agents/compounding" in report.materialized_runtime_owned
    assert "agents/compounding/procedures" in report.materialized_runtime_owned
    assert "agents/lab" in report.materialized_runtime_owned
    assert "agents/lab/harness_requests" in report.materialized_runtime_owned
    assert "agents/gaps.md" in report.materialized_runtime_owned
    assert (workspace / "agents" / "compounding" / "procedures").is_dir()
    assert (workspace / "agents" / "lab" / "harness_requests").is_dir()
    assert (workspace / "agents" / "gaps.md").read_text(encoding="utf-8") == (
        "# Gaps\n\nNo active gaps recorded.\n"
    )


def test_workspace_upgrade_apply_fails_before_mutation_on_conflicting_manifest_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    readme_before = (workspace / "README.md").read_text(encoding="utf-8")
    guide_path = workspace / "OPERATOR_GUIDE.md"
    guide_path.unlink()
    guide_path.mkdir()

    with pytest.raises(WorkspaceInitError, match="conflicting managed paths"):
        apply_workspace_upgrade(workspace)

    assert (workspace / "README.md").read_text(encoding="utf-8") == readme_before
    assert guide_path.is_dir()


def test_workspace_upgrade_apply_fails_before_mutation_on_conflicting_runtime_owned_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"

    initialize_workspace(workspace)
    readme_before = (workspace / "README.md").read_text(encoding="utf-8")
    shutil.rmtree(workspace / "agents" / "compounding")
    (workspace / "agents" / "compounding").write_text("not a directory\n", encoding="utf-8")

    preview = preview_workspace_upgrade(workspace)

    assert "agents/compounding" in preview.conflicting_paths
    with pytest.raises(WorkspaceInitError, match="conflicting managed paths"):
        apply_workspace_upgrade(workspace)

    assert (workspace / "README.md").read_text(encoding="utf-8") == readme_before
    assert (workspace / "agents" / "compounding").is_file()
