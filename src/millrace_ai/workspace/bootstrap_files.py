"""Default runtime files for newly initialized workspaces."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.config import render_bootstrap_runtime_config
from millrace_ai.contracts import Plane, RecoveryCounters, RuntimeMode, RuntimeSnapshot, WatcherMode

from .paths import WorkspacePaths

_IDLE_MARKER = "### IDLE\n"


def default_file_payloads(paths: WorkspacePaths) -> dict[Path, str]:
    """Return default file payloads for bootstrap-created workspace files."""

    return {
        paths.outline_file: "",
        paths.historylog_file: "",
        paths.runtime_root / "millrace.toml": render_bootstrap_runtime_config(),
        paths.execution_status_file: _IDLE_MARKER,
        paths.planning_status_file: _IDLE_MARKER,
        paths.learning_status_file: _IDLE_MARKER,
        paths.learning_events_file: "",
        paths.runtime_snapshot_file: _default_runtime_snapshot_payload(paths),
        paths.recovery_counters_file: _default_recovery_counters_payload(),
    }


def _default_runtime_snapshot_payload(paths: WorkspacePaths) -> str:
    snapshot = RuntimeSnapshot(
        runtime_mode=RuntimeMode.DAEMON,
        process_running=False,
        paused=False,
        active_mode_id="default_codex",
        execution_loop_id="execution.standard",
        planning_loop_id="planning.standard",
        loop_ids_by_plane={
            Plane.EXECUTION: "execution.standard",
            Plane.PLANNING: "planning.standard",
        },
        compiled_plan_id="bootstrap",
        compiled_plan_path=str((paths.state_dir / "compiled_plan.json").relative_to(paths.root)),
        execution_status_marker=_IDLE_MARKER.strip(),
        planning_status_marker=_IDLE_MARKER.strip(),
        learning_status_marker=_IDLE_MARKER.strip(),
        status_markers_by_plane={
            Plane.EXECUTION: _IDLE_MARKER.strip(),
            Plane.PLANNING: _IDLE_MARKER.strip(),
            Plane.LEARNING: _IDLE_MARKER.strip(),
        },
        queue_depths_by_plane={
            Plane.EXECUTION: 0,
            Plane.PLANNING: 0,
            Plane.LEARNING: 0,
        },
        config_version="bootstrap",
        watcher_mode=WatcherMode.OFF,
        updated_at=datetime.now(timezone.utc),
    )
    return snapshot.model_dump_json(indent=2) + "\n"


def _default_recovery_counters_payload() -> str:
    counters = RecoveryCounters()
    return counters.model_dump_json(indent=2) + "\n"


__all__ = ["default_file_payloads"]
