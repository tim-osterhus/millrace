"""Canonical workspace path model and bootstrap helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from millrace_ai.config import render_bootstrap_runtime_config
from millrace_ai.contracts import RecoveryCounters, RuntimeMode, RuntimeSnapshot, WatcherMode

_IDLE_MARKER = "### IDLE\n"
_RUNTIME_ASSET_DIRS: tuple[str, ...] = ("entrypoints", "skills", "modes", "loops")


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    """Resolved canonical workspace paths rooted at one workspace directory."""

    root: Path
    runtime_root: Path

    state_dir: Path
    mailbox_dir: Path
    mailbox_incoming_dir: Path
    mailbox_processed_dir: Path
    mailbox_failed_dir: Path

    runs_dir: Path

    tasks_dir: Path
    tasks_queue_dir: Path
    tasks_active_dir: Path
    tasks_done_dir: Path
    tasks_blocked_dir: Path

    specs_dir: Path
    specs_queue_dir: Path
    specs_active_dir: Path
    specs_done_dir: Path
    specs_blocked_dir: Path

    incidents_dir: Path
    incidents_incoming_dir: Path
    incidents_active_dir: Path
    incidents_resolved_dir: Path
    incidents_blocked_dir: Path

    loops_dir: Path
    execution_loops_dir: Path
    planning_loops_dir: Path

    modes_dir: Path
    logs_dir: Path
    entrypoints_dir: Path
    skills_dir: Path

    outline_file: Path
    historylog_file: Path
    execution_status_file: Path
    planning_status_file: Path
    runtime_snapshot_file: Path
    recovery_counters_file: Path
    runtime_error_context_file: Path
    runtime_lock_file: Path

    def directories(self) -> tuple[Path, ...]:
        """Return all directories that must exist for a canonical workspace."""

        return (
            self.runtime_root,
            self.state_dir,
            self.mailbox_dir,
            self.mailbox_incoming_dir,
            self.mailbox_processed_dir,
            self.mailbox_failed_dir,
            self.runs_dir,
            self.tasks_dir,
            self.tasks_queue_dir,
            self.tasks_active_dir,
            self.tasks_done_dir,
            self.tasks_blocked_dir,
            self.specs_dir,
            self.specs_queue_dir,
            self.specs_active_dir,
            self.specs_done_dir,
            self.specs_blocked_dir,
            self.incidents_dir,
            self.incidents_incoming_dir,
            self.incidents_active_dir,
            self.incidents_resolved_dir,
            self.incidents_blocked_dir,
            self.loops_dir,
            self.execution_loops_dir,
            self.planning_loops_dir,
            self.modes_dir,
            self.logs_dir,
            self.entrypoints_dir,
            self.skills_dir,
        )


def workspace_paths(root: Union[str, Path]) -> WorkspacePaths:
    """Resolve canonical workspace paths from a root workspace directory."""

    resolved_root = Path(root).expanduser().resolve()
    runtime_root = resolved_root / "millrace-agents"
    state_dir = runtime_root / "state"
    mailbox_dir = state_dir / "mailbox"
    tasks_dir = runtime_root / "tasks"
    specs_dir = runtime_root / "specs"
    incidents_dir = runtime_root / "incidents"
    loops_dir = runtime_root / "loops"

    return WorkspacePaths(
        root=resolved_root,
        runtime_root=runtime_root,
        state_dir=state_dir,
        mailbox_dir=mailbox_dir,
        mailbox_incoming_dir=mailbox_dir / "incoming",
        mailbox_processed_dir=mailbox_dir / "processed",
        mailbox_failed_dir=mailbox_dir / "failed",
        runs_dir=runtime_root / "runs",
        tasks_dir=tasks_dir,
        tasks_queue_dir=tasks_dir / "queue",
        tasks_active_dir=tasks_dir / "active",
        tasks_done_dir=tasks_dir / "done",
        tasks_blocked_dir=tasks_dir / "blocked",
        specs_dir=specs_dir,
        specs_queue_dir=specs_dir / "queue",
        specs_active_dir=specs_dir / "active",
        specs_done_dir=specs_dir / "done",
        specs_blocked_dir=specs_dir / "blocked",
        incidents_dir=incidents_dir,
        incidents_incoming_dir=incidents_dir / "incoming",
        incidents_active_dir=incidents_dir / "active",
        incidents_resolved_dir=incidents_dir / "resolved",
        incidents_blocked_dir=incidents_dir / "blocked",
        loops_dir=loops_dir,
        execution_loops_dir=loops_dir / "execution",
        planning_loops_dir=loops_dir / "planning",
        modes_dir=runtime_root / "modes",
        logs_dir=runtime_root / "logs",
        entrypoints_dir=runtime_root / "entrypoints",
        skills_dir=runtime_root / "skills",
        outline_file=runtime_root / "outline.md",
        historylog_file=runtime_root / "historylog.md",
        execution_status_file=state_dir / "execution_status.md",
        planning_status_file=state_dir / "planning_status.md",
        runtime_snapshot_file=state_dir / "runtime_snapshot.json",
        recovery_counters_file=state_dir / "recovery_counters.json",
        runtime_error_context_file=state_dir / "runtime_error_context.json",
        runtime_lock_file=state_dir / "runtime_daemon.lock.json",
    )


def bootstrap_workspace(
    target: WorkspacePaths | Path | str,
    *,
    assets_root: Path | str | None = None,
) -> WorkspacePaths:
    """Create canonical workspace directories and default files if missing."""

    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)

    for directory in paths.directories():
        directory.mkdir(parents=True, exist_ok=True)

    defaults = _default_file_payloads(paths)
    for file_path, payload in defaults.items():
        if not file_path.exists():
            file_path.write_text(payload, encoding="utf-8")

    _deploy_runtime_assets(paths, assets_root=assets_root)
    return paths


def _deploy_runtime_assets(paths: WorkspacePaths, *, assets_root: Path | str | None) -> None:
    source_root = _resolve_asset_source_root(assets_root)

    for directory_name in _RUNTIME_ASSET_DIRS:
        source_dir = source_root / directory_name
        if not source_dir.exists():
            continue

        destination_dir = paths.runtime_root / directory_name
        for source_file in source_dir.rglob("*"):
            if source_file.is_dir():
                continue

            if any(part.startswith(".") for part in source_file.relative_to(source_dir).parts):
                continue

            relative_path = source_file.relative_to(source_dir)
            destination = destination_dir / relative_path
            if destination.exists():
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source_file.read_bytes())


def _resolve_asset_source_root(assets_root: Path | str | None) -> Path:
    if assets_root is not None:
        return Path(assets_root).expanduser().resolve()

    from millrace_ai.modes import ASSETS_ROOT

    return ASSETS_ROOT


def _default_file_payloads(paths: WorkspacePaths) -> dict[Path, str]:
    return {
        paths.outline_file: "",
        paths.historylog_file: "",
        paths.runtime_root / "millrace.toml": render_bootstrap_runtime_config(),
        paths.execution_status_file: _IDLE_MARKER,
        paths.planning_status_file: _IDLE_MARKER,
        paths.runtime_snapshot_file: _default_runtime_snapshot_payload(paths),
        paths.recovery_counters_file: _default_recovery_counters_payload(),
    }


def _default_runtime_snapshot_payload(paths: WorkspacePaths) -> str:
    snapshot = RuntimeSnapshot(
        runtime_mode=RuntimeMode.DAEMON,
        process_running=False,
        paused=False,
        active_mode_id="standard_plain",
        execution_loop_id="execution.standard",
        planning_loop_id="planning.standard",
        compiled_plan_id="bootstrap",
        compiled_plan_path=str((paths.state_dir / "compiled_plan.json").relative_to(paths.root)),
        execution_status_marker=_IDLE_MARKER.strip(),
        planning_status_marker=_IDLE_MARKER.strip(),
        config_version="bootstrap",
        watcher_mode=WatcherMode.OFF,
        updated_at=datetime.now(timezone.utc),
    )
    return snapshot.model_dump_json(indent=2) + "\n"


def _default_recovery_counters_payload() -> str:
    counters = RecoveryCounters()
    return counters.model_dump_json(indent=2) + "\n"


__all__ = ["WorkspacePaths", "bootstrap_workspace", "workspace_paths"]
