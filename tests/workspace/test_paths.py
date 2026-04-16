from __future__ import annotations

import importlib
import json
from pathlib import Path

from millrace_ai.config import load_runtime_config
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
        runtime_root / "loops",
        runtime_root / "loops" / "execution",
        runtime_root / "loops" / "planning",
        runtime_root / "modes",
        runtime_root / "logs",
        runtime_root / "entrypoints",
        runtime_root / "skills",
    ]


def test_paths_module_is_workspace_facade() -> None:
    paths_module = importlib.import_module("millrace_ai.paths")
    workspace_paths_module = importlib.import_module("millrace_ai.workspace.paths")

    assert Path(paths_module.__file__).as_posix().endswith("/paths.py")
    assert paths_module.WorkspacePaths.__module__ == "millrace_ai.workspace.paths"
    assert paths_module.workspace_paths is workspace_paths_module.workspace_paths
    assert paths_module.bootstrap_workspace is workspace_paths_module.bootstrap_workspace


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
    assert paths.execution_loops_dir == root / "millrace-agents" / "loops" / "execution"
    assert paths.planning_loops_dir == root / "millrace-agents" / "loops" / "planning"
    assert paths.outline_file == root / "millrace-agents" / "outline.md"
    assert paths.historylog_file == root / "millrace-agents" / "historylog.md"
    assert paths.execution_status_file == root / "millrace-agents" / "state" / "execution_status.md"
    assert paths.planning_status_file == root / "millrace-agents" / "state" / "planning_status.md"
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
        root / "millrace-agents" / "state" / "execution_status.md",
        root / "millrace-agents" / "state" / "planning_status.md",
        root / "millrace-agents" / "state" / "runtime_snapshot.json",
        root / "millrace-agents" / "state" / "recovery_counters.json",
    ]
    for file_path in expected_files:
        assert file_path.is_file(), f"Missing file: {file_path}"

    # Runtime bootstrap should not create legacy root-level runtime surfaces.
    for legacy in ("state", "runs", "tasks", "specs", "incidents", "loops", "logs", "entrypoints", "skills", "roles"):
        assert not (root / legacy).exists(), f"Unexpected root-level runtime artifact: {legacy}"

    assert not (root / "millrace-agents" / "roles").exists()


def test_bootstrap_initializes_status_and_state_defaults(tmp_path: Path) -> None:
    paths = workspace_paths(tmp_path / "workspace")

    bootstrap_workspace(paths)

    assert paths.execution_status_file.read_text(encoding="utf-8") == "### IDLE\n"
    assert paths.planning_status_file.read_text(encoding="utf-8") == "### IDLE\n"

    snapshot_payload = json.loads(paths.runtime_snapshot_file.read_text(encoding="utf-8"))
    snapshot = RuntimeSnapshot.model_validate(snapshot_payload)
    assert snapshot.runtime_mode == "daemon"
    assert snapshot.execution_status_marker == "### IDLE"
    assert snapshot.planning_status_marker == "### IDLE"
    assert snapshot.active_stage is None

    counters_payload = json.loads(paths.recovery_counters_file.read_text(encoding="utf-8"))
    counters = RecoveryCounters.model_validate(counters_payload)
    assert counters.entries == ()

    config = load_runtime_config(paths.runtime_root / "millrace.toml")
    assert config.runtime.default_mode == "standard_plain"
    assert config.runtime.run_style == "daemon"


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
