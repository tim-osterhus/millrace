from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from millrace_ai.config import CodexPermissionLevel, load_runtime_config
from millrace_ai.contracts import RecoveryCounters, RuntimeSnapshot
from millrace_ai.paths import bootstrap_workspace, workspace_paths


def _expected_directories(root: Path) -> list[Path]:
    runtime_root = root / "millrace-agents"
    return [
        runtime_root,
        runtime_root / "state",
        runtime_root / "state" / "mailbox",
        runtime_root / "state" / "mailbox" / "incoming",
        runtime_root / "state" / "mailbox" / "processed",
        runtime_root / "state" / "mailbox" / "failed",
        runtime_root / "runs",
        runtime_root / "tasks",
        runtime_root / "tasks" / "queue",
        runtime_root / "tasks" / "active",
        runtime_root / "tasks" / "done",
        runtime_root / "tasks" / "blocked",
        runtime_root / "specs",
        runtime_root / "specs" / "queue",
        runtime_root / "specs" / "active",
        runtime_root / "specs" / "done",
        runtime_root / "specs" / "blocked",
        runtime_root / "incidents",
        runtime_root / "incidents" / "incoming",
        runtime_root / "incidents" / "active",
        runtime_root / "incidents" / "resolved",
        runtime_root / "incidents" / "blocked",
        runtime_root / "learning",
        runtime_root / "learning" / "requests",
        runtime_root / "learning" / "requests" / "queue",
        runtime_root / "learning" / "requests" / "active",
        runtime_root / "learning" / "requests" / "done",
        runtime_root / "learning" / "requests" / "blocked",
        runtime_root / "learning" / "research-packets",
        runtime_root / "learning" / "skill-candidates",
        runtime_root / "learning" / "update-candidates",
        runtime_root / "loops",
        runtime_root / "loops" / "execution",
        runtime_root / "loops" / "planning",
        runtime_root / "loops" / "learning",
        runtime_root / "graphs",
        runtime_root / "graphs" / "execution",
        runtime_root / "graphs" / "planning",
        runtime_root / "graphs" / "learning",
        runtime_root / "registry",
        runtime_root / "registry" / "stage_kinds",
        runtime_root / "registry" / "stage_kinds" / "execution",
        runtime_root / "registry" / "stage_kinds" / "planning",
        runtime_root / "registry" / "stage_kinds" / "learning",
        runtime_root / "modes",
        runtime_root / "logs",
        runtime_root / "entrypoints",
        runtime_root / "skills",
        runtime_root / "arbiter",
        runtime_root / "arbiter" / "contracts",
        runtime_root / "arbiter" / "contracts" / "ideas",
        runtime_root / "arbiter" / "contracts" / "root-specs",
        runtime_root / "arbiter" / "targets",
        runtime_root / "arbiter" / "rubrics",
        runtime_root / "arbiter" / "verdicts",
        runtime_root / "arbiter" / "reports",
    ]


def test_paths_module_is_workspace_facade() -> None:
    paths_module = importlib.import_module("millrace_ai.paths")
    workspace_paths_module = importlib.import_module("millrace_ai.workspace.paths")
    initialization_module = importlib.import_module("millrace_ai.workspace.initialization")

    assert Path(paths_module.__file__).as_posix().endswith("/paths.py")
    assert paths_module.WorkspacePaths.__module__ == "millrace_ai.workspace.paths"
    assert paths_module.workspace_paths is workspace_paths_module.workspace_paths
    assert paths_module.initialize_workspace is initialization_module.initialize_workspace
    assert paths_module.bootstrap_workspace is initialization_module.bootstrap_workspace
    assert paths_module.require_initialized_workspace is initialization_module.require_initialized_workspace
    assert paths_module.ensure_runtime_state_surfaces is initialization_module.ensure_runtime_state_surfaces


def test_workspace_paths_resolves_canonical_model(tmp_path: Path) -> None:
    root = tmp_path / "workspace"

    paths = workspace_paths(root)

    assert paths.root == root.resolve()
    assert paths.runtime_root == root / "millrace-agents"
    assert paths.state_dir == root / "millrace-agents" / "state"
    assert paths.mailbox_incoming_dir == root / "millrace-agents" / "state" / "mailbox" / "incoming"
    assert paths.tasks_queue_dir == root / "millrace-agents" / "tasks" / "queue"
    assert paths.specs_active_dir == root / "millrace-agents" / "specs" / "active"
    assert paths.incidents_resolved_dir == root / "millrace-agents" / "incidents" / "resolved"
    assert paths.learning_requests_queue_dir == root / "millrace-agents" / "learning" / "requests" / "queue"
    assert paths.learning_research_packets_dir == root / "millrace-agents" / "learning" / "research-packets"
    assert paths.execution_loops_dir == root / "millrace-agents" / "loops" / "execution"
    assert paths.planning_loops_dir == root / "millrace-agents" / "loops" / "planning"
    assert paths.learning_loops_dir == root / "millrace-agents" / "loops" / "learning"
    assert paths.execution_graphs_dir == root / "millrace-agents" / "graphs" / "execution"
    assert paths.planning_graphs_dir == root / "millrace-agents" / "graphs" / "planning"
    assert paths.learning_graphs_dir == root / "millrace-agents" / "graphs" / "learning"
    assert (
        paths.execution_stage_kind_registry_dir
        == root / "millrace-agents" / "registry" / "stage_kinds" / "execution"
    )
    assert (
        paths.planning_stage_kind_registry_dir
        == root / "millrace-agents" / "registry" / "stage_kinds" / "planning"
    )
    assert paths.arbiter_dir == root / "millrace-agents" / "arbiter"
    assert paths.arbiter_idea_contracts_dir == root / "millrace-agents" / "arbiter" / "contracts" / "ideas"
    assert paths.arbiter_root_spec_contracts_dir == root / "millrace-agents" / "arbiter" / "contracts" / "root-specs"
    assert paths.arbiter_targets_dir == root / "millrace-agents" / "arbiter" / "targets"
    assert paths.arbiter_rubrics_dir == root / "millrace-agents" / "arbiter" / "rubrics"
    assert paths.arbiter_verdicts_dir == root / "millrace-agents" / "arbiter" / "verdicts"
    assert paths.arbiter_reports_dir == root / "millrace-agents" / "arbiter" / "reports"
    assert paths.outline_file == root / "millrace-agents" / "outline.md"
    assert paths.historylog_file == root / "millrace-agents" / "historylog.md"
    assert paths.execution_status_file == root / "millrace-agents" / "state" / "execution_status.md"
    assert paths.planning_status_file == root / "millrace-agents" / "state" / "planning_status.md"
    assert paths.learning_status_file == root / "millrace-agents" / "state" / "learning_status.md"
    assert paths.baseline_manifest_file == root / "millrace-agents" / "state" / "baseline_manifest.json"
    assert not hasattr(paths, "roles_dir")


def test_bootstrap_creates_canonical_workspace_surfaces(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    paths = workspace_paths(root)

    bootstrap_workspace(paths)

    for directory in _expected_directories(root):
        assert directory.is_dir(), f"Missing directory: {directory}"

    expected_files = [
        root / "millrace-agents" / "outline.md",
        root / "millrace-agents" / "historylog.md",
        root / "millrace-agents" / "millrace.toml",
        root / "millrace-agents" / "state" / "baseline_manifest.json",
        root / "millrace-agents" / "state" / "execution_status.md",
        root / "millrace-agents" / "state" / "planning_status.md",
        root / "millrace-agents" / "state" / "learning_status.md",
        root / "millrace-agents" / "state" / "runtime_snapshot.json",
        root / "millrace-agents" / "state" / "recovery_counters.json",
        root / "millrace-agents" / "learning" / "events.jsonl",
    ]
    for file_path in expected_files:
        assert file_path.is_file(), f"Missing file: {file_path}"

    # Runtime bootstrap should not create legacy root-level runtime surfaces.
    for legacy in (
        "state",
        "runs",
        "tasks",
        "specs",
        "incidents",
        "loops",
        "graphs",
        "registry",
        "logs",
        "entrypoints",
        "skills",
        "roles",
    ):
        assert not (root / legacy).exists(), f"Unexpected root-level runtime artifact: {legacy}"

    assert not (root / "millrace-agents" / "roles").exists()

    expected_runtime_assets = [
        root / "millrace-agents" / "graphs" / "execution" / "standard.json",
        root / "millrace-agents" / "graphs" / "planning" / "standard.json",
        root / "millrace-agents" / "registry" / "stage_kinds" / "execution" / "builder.json",
        root / "millrace-agents" / "registry" / "stage_kinds" / "planning" / "arbiter.json",
    ]
    for asset_path in expected_runtime_assets:
        assert asset_path.is_file(), f"Missing runtime asset: {asset_path}"


def test_require_initialized_workspace_rejects_uninitialized_target(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    paths_module = importlib.import_module("millrace_ai.paths")

    with pytest.raises(ValueError, match="workspace is not initialized"):
        paths_module.require_initialized_workspace(root)

    assert not root.exists()


def test_initialize_workspace_creates_canonical_workspace_surfaces(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    paths_module = importlib.import_module("millrace_ai.paths")

    paths = paths_module.initialize_workspace(root)

    assert paths == workspace_paths(root)
    for directory in _expected_directories(root):
        assert directory.is_dir(), f"Missing directory: {directory}"

    expected_files = [
        root / "millrace-agents" / "outline.md",
        root / "millrace-agents" / "historylog.md",
        root / "millrace-agents" / "millrace.toml",
        root / "millrace-agents" / "state" / "baseline_manifest.json",
        root / "millrace-agents" / "state" / "execution_status.md",
        root / "millrace-agents" / "state" / "planning_status.md",
        root / "millrace-agents" / "state" / "learning_status.md",
        root / "millrace-agents" / "state" / "runtime_snapshot.json",
        root / "millrace-agents" / "state" / "recovery_counters.json",
        root / "millrace-agents" / "learning" / "events.jsonl",
    ]
    for file_path in expected_files:
        assert file_path.is_file(), f"Missing file: {file_path}"


def test_ensure_runtime_state_surfaces_restores_missing_runtime_state_defaults(tmp_path: Path) -> None:
    paths_module = importlib.import_module("millrace_ai.paths")
    paths = paths_module.initialize_workspace(tmp_path / "workspace")

    paths.execution_status_file.unlink()
    paths.planning_status_file.unlink()
    paths.learning_status_file.unlink()
    paths.runtime_snapshot_file.unlink()
    paths.recovery_counters_file.unlink()
    paths.learning_events_file.unlink()

    ensured = paths_module.ensure_runtime_state_surfaces(paths)

    assert ensured == paths
    assert paths.execution_status_file.read_text(encoding="utf-8") == "### IDLE\n"
    assert paths.planning_status_file.read_text(encoding="utf-8") == "### IDLE\n"
    assert paths.learning_status_file.read_text(encoding="utf-8") == "### IDLE\n"
    assert json.loads(paths.runtime_snapshot_file.read_text(encoding="utf-8"))["compiled_plan_id"] == "bootstrap"
    assert json.loads(paths.recovery_counters_file.read_text(encoding="utf-8"))["entries"] == []
    assert paths.learning_events_file.read_text(encoding="utf-8") == ""


def test_ensure_runtime_state_surfaces_does_not_seed_baseline_manifest(tmp_path: Path) -> None:
    paths_module = importlib.import_module("millrace_ai.paths")
    paths = paths_module.initialize_workspace(tmp_path / "workspace")

    paths.baseline_manifest_file.unlink()

    with pytest.raises(ValueError, match="workspace is not initialized"):
        paths_module.ensure_runtime_state_surfaces(paths)

    assert not paths.baseline_manifest_file.exists()


def test_require_initialized_workspace_rejects_missing_baseline_manifest(tmp_path: Path) -> None:
    paths_module = importlib.import_module("millrace_ai.paths")
    paths = paths_module.initialize_workspace(tmp_path / "workspace")

    paths.baseline_manifest_file.unlink()

    with pytest.raises(ValueError, match="workspace is not initialized"):
        paths_module.require_initialized_workspace(paths)


def test_bootstrap_initializes_status_and_state_defaults(tmp_path: Path) -> None:
    paths = workspace_paths(tmp_path / "workspace")

    bootstrap_workspace(paths)

    assert paths.execution_status_file.read_text(encoding="utf-8") == "### IDLE\n"
    assert paths.planning_status_file.read_text(encoding="utf-8") == "### IDLE\n"
    assert paths.learning_status_file.read_text(encoding="utf-8") == "### IDLE\n"

    snapshot_payload = json.loads(paths.runtime_snapshot_file.read_text(encoding="utf-8"))
    snapshot = RuntimeSnapshot.model_validate(snapshot_payload)
    assert snapshot.runtime_mode == "daemon"
    assert snapshot.execution_status_marker == "### IDLE"
    assert snapshot.planning_status_marker == "### IDLE"
    assert snapshot.learning_status_marker == "### IDLE"
    assert snapshot.queue_depth_learning == 0
    assert snapshot.active_stage is None

    counters_payload = json.loads(paths.recovery_counters_file.read_text(encoding="utf-8"))
    counters = RecoveryCounters.model_validate(counters_payload)
    assert counters.entries == ()

    config = load_runtime_config(paths.runtime_root / "millrace.toml")
    assert config.runtime.default_mode == "default_codex"
    assert config.runtime.run_style == "daemon"
    assert config.runners.codex.permission_default is CodexPermissionLevel.MAXIMUM
    config_text = paths.runtime_root.joinpath("millrace.toml").read_text(encoding="utf-8")
    assert "[runners.codex]" in config_text
    assert '[runtime]\ndefault_mode = "default_codex"' in config_text
    assert 'permission_default = "maximum"' in config_text


def test_bootstrap_is_idempotent_and_preserves_existing_files(tmp_path: Path) -> None:
    paths = workspace_paths(tmp_path / "workspace")
    bootstrap_workspace(paths)

    paths.execution_status_file.write_text("### CHECKER_PASS\n", encoding="utf-8")
    paths.planning_status_file.write_text("### PLANNER_COMPLETE\n", encoding="utf-8")
    paths.outline_file.write_text("# Existing Outline\n", encoding="utf-8")
    paths.runtime_snapshot_file.write_text('{"custom": true}\n', encoding="utf-8")

    bootstrap_workspace(paths)
    bootstrap_workspace(paths)

    assert paths.execution_status_file.read_text(encoding="utf-8") == "### CHECKER_PASS\n"
    assert paths.planning_status_file.read_text(encoding="utf-8") == "### PLANNER_COMPLETE\n"
    assert paths.outline_file.read_text(encoding="utf-8") == "# Existing Outline\n"
    assert paths.runtime_snapshot_file.read_text(encoding="utf-8") == '{"custom": true}\n'


def test_bootstrap_preserves_existing_runtime_config_customizations(tmp_path: Path) -> None:
    paths = workspace_paths(tmp_path / "workspace")
    bootstrap_workspace(paths)

    custom_config = "\n".join(
        [
            "[runtime]",
            'default_mode = "standard_plain"',
            'run_style = "daemon"',
            "",
            "[runners.codex]",
            'permission_default = "basic"',
            'permission_by_stage = { builder = "elevated" }',
            'permission_by_model = { "gpt-5" = "maximum" }',
            "",
        ]
    )
    paths.runtime_root.joinpath("millrace.toml").write_text(custom_config, encoding="utf-8")

    bootstrap_workspace(paths)

    assert paths.runtime_root.joinpath("millrace.toml").read_text(encoding="utf-8") == custom_config

    config = load_runtime_config(paths.runtime_root / "millrace.toml")
    assert config.runners.codex.permission_default is CodexPermissionLevel.BASIC
    assert config.runners.codex.permission_by_stage["builder"] is CodexPermissionLevel.ELEVATED
    assert config.runners.codex.permission_by_model["gpt-5"] is CodexPermissionLevel.MAXIMUM
