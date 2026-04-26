"""Explicit workspace initialization and validation helpers."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.workspace.baseline import build_baseline_manifest, write_baseline_manifest
from millrace_ai.workspace.paths import (
    WorkspacePaths,
    _default_file_payloads,
    _deploy_runtime_assets,
    workspace_paths,
)

_RUNTIME_STATE_FILES = (
    "execution_status_file",
    "planning_status_file",
    "learning_status_file",
    "runtime_snapshot_file",
    "recovery_counters_file",
    "learning_events_file",
)
_BASELINE_PATHS = (
    "runtime_root",
    "state_dir",
    "tasks_queue_dir",
    "specs_queue_dir",
    "incidents_incoming_dir",
    "learning_requests_queue_dir",
    "entrypoints_dir",
    "skills_dir",
    "outline_file",
    "historylog_file",
)


def initialize_workspace(
    target: WorkspacePaths | Path | str,
    *,
    assets_root: Path | str | None = None,
) -> WorkspacePaths:
    """Create the canonical workspace baseline."""

    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)

    for directory in paths.directories():
        directory.mkdir(parents=True, exist_ok=True)

    defaults = _default_file_payloads(paths)
    for file_path, payload in defaults.items():
        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(payload, encoding="utf-8")

    _deploy_runtime_assets(paths, assets_root=assets_root)
    if not paths.baseline_manifest_file.exists():
        write_baseline_manifest(paths, build_baseline_manifest(paths))
    return paths


def require_initialized_workspace(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    """Resolve a workspace and reject targets missing the canonical baseline."""

    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    required_paths = tuple(getattr(paths, attribute_name) for attribute_name in _BASELINE_PATHS) + (
        paths.runtime_root / "millrace.toml",
    )
    missing = tuple(path for path in required_paths if not path.exists())
    if missing:
        raise ValueError(
            "workspace is not initialized: "
            f"{paths.root}. Run `millrace init --workspace {paths.root}` first."
        )
    return paths


def ensure_runtime_state_surfaces(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    """Create missing runtime state files for an initialized workspace."""

    paths = require_initialized_workspace(target)
    defaults = _default_file_payloads(paths)
    for attribute_name in _RUNTIME_STATE_FILES:
        file_path = getattr(paths, attribute_name)
        if file_path.exists():
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(defaults[file_path], encoding="utf-8")
    return paths


__all__ = [
    "ensure_runtime_state_surfaces",
    "initialize_workspace",
    "require_initialized_workspace",
]
