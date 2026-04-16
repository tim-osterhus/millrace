"""State persistence and stale-state reconciliation helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    RecoveryCounterEntry,
    RecoveryCounters,
    RuntimeSnapshot,
    StageName,
    WorkItemKind,
)
from millrace_ai.paths import WorkspacePaths, workspace_paths

_IDLE_MARKER = "### IDLE"
_INVALID_MARKER = "### INVALID_STATUS_MARKER"
_STALE_ACTIVE_FAILURE_CLASS = "stale_active_ownership"
_IMPOSSIBLE_STATUS_FAILURE_CLASS = "impossible_status_marker"
_ORPHANED_COUNTER_FAILURE_CLASS = "stale_recovery_without_active_stage"

_EXECUTION_STATUS_MARKERS = frozenset(
    {_IDLE_MARKER, *(f"### {value.value}" for value in ExecutionTerminalResult)}
)
_PLANNING_STATUS_MARKERS = frozenset(
    {_IDLE_MARKER, *(f"### {value.value}" for value in PlanningTerminalResult)}
)

_STAGE_ALLOWED_MARKERS: dict[str, frozenset[str]] = {
    ExecutionStageName.BUILDER.value: frozenset(
        {
            "### BUILDER_COMPLETE",
            "### BLOCKED",
        }
    ),
    ExecutionStageName.CHECKER.value: frozenset(
        {
            "### CHECKER_PASS",
            "### FIX_NEEDED",
            "### BLOCKED",
        }
    ),
    ExecutionStageName.FIXER.value: frozenset(
        {
            "### FIXER_COMPLETE",
            "### BLOCKED",
        }
    ),
    ExecutionStageName.DOUBLECHECKER.value: frozenset(
        {
            "### DOUBLECHECK_PASS",
            "### FIX_NEEDED",
            "### BLOCKED",
        }
    ),
    ExecutionStageName.UPDATER.value: frozenset(
        {
            "### UPDATE_COMPLETE",
            "### BLOCKED",
        }
    ),
    ExecutionStageName.TROUBLESHOOTER.value: frozenset(
        {
            "### TROUBLESHOOT_COMPLETE",
            "### BLOCKED",
        }
    ),
    ExecutionStageName.CONSULTANT.value: frozenset(
        {
            "### CONSULT_COMPLETE",
            "### NEEDS_PLANNING",
            "### BLOCKED",
        }
    ),
    PlanningStageName.PLANNER.value: frozenset(
        {
            "### PLANNER_COMPLETE",
            "### BLOCKED",
        }
    ),
    PlanningStageName.MANAGER.value: frozenset(
        {
            "### MANAGER_COMPLETE",
            "### BLOCKED",
        }
    ),
    PlanningStageName.MECHANIC.value: frozenset(
        {
            "### MECHANIC_COMPLETE",
            "### BLOCKED",
        }
    ),
    PlanningStageName.AUDITOR.value: frozenset(
        {
            "### AUDITOR_COMPLETE",
            "### BLOCKED",
        }
    ),
}

_STAGE_INBOUND_MARKERS: dict[str, frozenset[str]] = {
    ExecutionStageName.BUILDER.value: frozenset(
        {
            "### TROUBLESHOOT_COMPLETE",
            "### CONSULT_COMPLETE",
        }
    ),
    ExecutionStageName.CHECKER.value: frozenset(
        {
            "### BUILDER_COMPLETE",
            "### TROUBLESHOOT_COMPLETE",
            "### CONSULT_COMPLETE",
        }
    ),
    ExecutionStageName.FIXER.value: frozenset(
        {
            "### FIX_NEEDED",
            "### TROUBLESHOOT_COMPLETE",
            "### CONSULT_COMPLETE",
        }
    ),
    ExecutionStageName.DOUBLECHECKER.value: frozenset(
        {
            "### FIXER_COMPLETE",
            "### TROUBLESHOOT_COMPLETE",
            "### CONSULT_COMPLETE",
        }
    ),
    ExecutionStageName.UPDATER.value: frozenset(
        {
            "### CHECKER_PASS",
            "### DOUBLECHECK_PASS",
            "### TROUBLESHOOT_COMPLETE",
            "### CONSULT_COMPLETE",
        }
    ),
    ExecutionStageName.TROUBLESHOOTER.value: _EXECUTION_STATUS_MARKERS - {_IDLE_MARKER},
    ExecutionStageName.CONSULTANT.value: _EXECUTION_STATUS_MARKERS - {_IDLE_MARKER},
    PlanningStageName.PLANNER.value: frozenset(
        {
            "### AUDITOR_COMPLETE",
            "### MECHANIC_COMPLETE",
        }
    ),
    PlanningStageName.MANAGER.value: frozenset({"### PLANNER_COMPLETE"}),
    PlanningStageName.MECHANIC.value: _PLANNING_STATUS_MARKERS - {_IDLE_MARKER},
    PlanningStageName.AUDITOR.value: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ReconciliationSignal:
    """Signal emitted when runtime state is stale or impossible."""

    code: str
    failure_class: str
    plane: Plane | None
    recommended_stage: StageName | None
    message: str


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object payload in {path}")
    return payload


def _save_model(path: Path, model: RuntimeSnapshot | RecoveryCounters) -> None:
    _atomic_write_text(path, model.model_dump_json(indent=2) + "\n")


def _normalize_marker(marker: str, *, label: str) -> str:
    normalized = marker.strip()
    if not normalized:
        raise ValueError(f"{label} marker cannot be empty")
    lines = normalized.splitlines()
    if len(lines) != 1:
        raise ValueError(f"{label} marker must be a single line")
    return lines[0]


def _validate_marker(marker: str, allowed: frozenset[str], *, label: str) -> str:
    normalized = _normalize_marker(marker, label=label)
    if normalized not in allowed:
        raise ValueError(f"Unknown {label} marker: {normalized}")
    return normalized


def load_snapshot(target: WorkspacePaths | Path | str) -> RuntimeSnapshot:
    paths = _resolve_paths(target)
    return RuntimeSnapshot.model_validate(_load_json(paths.runtime_snapshot_file))


def save_snapshot(target: WorkspacePaths | Path | str, snapshot: RuntimeSnapshot) -> None:
    paths = _resolve_paths(target)
    validated = RuntimeSnapshot.model_validate(snapshot.model_dump(mode="python"))
    _save_model(paths.runtime_snapshot_file, validated)


def load_recovery_counters(target: WorkspacePaths | Path | str) -> RecoveryCounters:
    paths = _resolve_paths(target)
    return RecoveryCounters.model_validate(_load_json(paths.recovery_counters_file))


def save_recovery_counters(
    target: WorkspacePaths | Path | str,
    counters: RecoveryCounters,
) -> None:
    paths = _resolve_paths(target)
    validated = RecoveryCounters.model_validate(counters.model_dump(mode="python"))
    _save_model(paths.recovery_counters_file, validated)


def load_execution_status(target: WorkspacePaths | Path | str) -> str:
    paths = _resolve_paths(target)
    marker = paths.execution_status_file.read_text(encoding="utf-8")
    return _validate_marker(
        marker,
        _EXECUTION_STATUS_MARKERS,
        label="execution status",
    )


def load_planning_status(target: WorkspacePaths | Path | str) -> str:
    paths = _resolve_paths(target)
    marker = paths.planning_status_file.read_text(encoding="utf-8")
    return _validate_marker(
        marker,
        _PLANNING_STATUS_MARKERS,
        label="planning status",
    )


def set_execution_status(target: WorkspacePaths | Path | str, marker: str) -> str:
    paths = _resolve_paths(target)
    normalized = _validate_marker(
        marker,
        _EXECUTION_STATUS_MARKERS,
        label="execution status",
    )
    _atomic_write_text(paths.execution_status_file, normalized + "\n")
    return normalized


def set_planning_status(target: WorkspacePaths | Path | str, marker: str) -> str:
    paths = _resolve_paths(target)
    normalized = _validate_marker(
        marker,
        _PLANNING_STATUS_MARKERS,
        label="planning status",
    )
    _atomic_write_text(paths.planning_status_file, normalized + "\n")
    return normalized


def _update_counter_entries(
    entries: tuple[RecoveryCounterEntry, ...],
    *,
    failure_class: str,
    work_item_kind: WorkItemKind,
    work_item_id: str,
    now: datetime,
) -> tuple[tuple[RecoveryCounterEntry, ...], RecoveryCounterEntry]:
    updated_entry: RecoveryCounterEntry | None = None
    mutable_entries = list(entries)

    for index, entry in enumerate(mutable_entries):
        if (
            entry.failure_class == failure_class
            and entry.work_item_kind == work_item_kind
            and entry.work_item_id == work_item_id
        ):
            updated_entry = entry.model_copy(
                update={
                    "troubleshoot_attempt_count": entry.troubleshoot_attempt_count + 1,
                    "last_updated_at": now,
                }
            )
            mutable_entries[index] = updated_entry
            break

    if updated_entry is None:
        updated_entry = RecoveryCounterEntry(
            failure_class=failure_class,
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            troubleshoot_attempt_count=1,
            last_updated_at=now,
        )
        mutable_entries.append(updated_entry)

    return tuple(mutable_entries), updated_entry


def increment_troubleshoot_attempt(
    target: WorkspacePaths | Path | str,
    *,
    failure_class: str,
    work_item_kind: WorkItemKind | str,
    work_item_id: str,
    now: datetime | None = None,
) -> RecoveryCounterEntry:
    paths = _resolve_paths(target)
    counters = load_recovery_counters(paths)
    timestamp = now or datetime.now(timezone.utc)
    kind = WorkItemKind(work_item_kind)

    updated_entries, updated_entry = _update_counter_entries(
        counters.entries,
        failure_class=failure_class,
        work_item_kind=kind,
        work_item_id=work_item_id,
        now=timestamp,
    )
    save_recovery_counters(paths, RecoveryCounters(entries=updated_entries))
    return updated_entry


def reset_forward_progress_counters(
    target: WorkspacePaths | Path | str,
    *,
    work_item_kind: WorkItemKind | str,
    work_item_id: str,
) -> None:
    paths = _resolve_paths(target)
    counters = load_recovery_counters(paths)
    kind = WorkItemKind(work_item_kind)

    remaining = tuple(
        entry
        for entry in counters.entries
        if not (entry.work_item_kind == kind and entry.work_item_id == work_item_id)
    )
    save_recovery_counters(paths, RecoveryCounters(entries=remaining))


def _has_impossible_marker_for_active_stage(snapshot: RuntimeSnapshot, marker: str) -> bool:
    if snapshot.active_stage is None:
        return False
    allowed = _STAGE_ALLOWED_MARKERS[snapshot.active_stage.value]
    inbound = _STAGE_INBOUND_MARKERS[snapshot.active_stage.value]
    if marker == _IDLE_MARKER:
        return False
    return marker not in allowed and marker not in inbound


def _stale_signal_recommended_stage(
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
) -> StageName:
    if snapshot.active_plane == Plane.PLANNING:
        return PlanningStageName.MECHANIC

    attempts = 0
    if snapshot.active_work_item_kind and snapshot.active_work_item_id:
        for entry in counters.entries:
            if (
                entry.failure_class == _STALE_ACTIVE_FAILURE_CLASS
                and entry.work_item_kind == snapshot.active_work_item_kind
                and entry.work_item_id == snapshot.active_work_item_id
            ):
                attempts = max(attempts, entry.troubleshoot_attempt_count)

    if attempts >= 2:
        return ExecutionStageName.CONSULTANT
    return ExecutionStageName.TROUBLESHOOTER


def _signal_for_orphaned_counters(counters: RecoveryCounters) -> ReconciliationSignal | None:
    for entry in counters.entries:
        if (
            entry.troubleshoot_attempt_count > 0
            or entry.mechanic_attempt_count > 0
            or entry.fix_cycle_count > 0
            or entry.consultant_invocations > 0
        ):
            if entry.work_item_kind == WorkItemKind.TASK:
                plane = Plane.EXECUTION
                stage: StageName = ExecutionStageName.TROUBLESHOOTER
            else:
                plane = Plane.PLANNING
                stage = PlanningStageName.MECHANIC

            return ReconciliationSignal(
                code="orphaned_recovery_counters",
                failure_class=_ORPHANED_COUNTER_FAILURE_CLASS,
                plane=plane,
                recommended_stage=stage,
                message=(
                    "recovery counters indicate in-flight work while runtime snapshot "
                    "has no active stage"
                ),
            )
    return None


def collect_reconciliation_signals(
    *,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    execution_status_marker: str,
    planning_status_marker: str,
) -> tuple[ReconciliationSignal, ...]:
    execution_marker = _normalize_marker_or_invalid(execution_status_marker, label="execution status")
    planning_marker = _normalize_marker_or_invalid(planning_status_marker, label="planning status")

    signals: list[ReconciliationSignal] = []

    if snapshot.active_stage is not None and not snapshot.process_running:
        plane = snapshot.active_plane
        signals.append(
            ReconciliationSignal(
                code="stale_active_ownership",
                failure_class=_STALE_ACTIVE_FAILURE_CLASS,
                plane=plane,
                recommended_stage=_stale_signal_recommended_stage(snapshot, counters),
                message="runtime snapshot has active ownership while process is not running",
            )
        )

    if snapshot.active_stage is not None and snapshot.active_plane == Plane.EXECUTION:
        if execution_marker not in _EXECUTION_STATUS_MARKERS or _has_impossible_marker_for_active_stage(
            snapshot,
            execution_marker,
        ):
            signals.append(
                ReconciliationSignal(
                    code="impossible_execution_status_marker",
                    failure_class=_IMPOSSIBLE_STATUS_FAILURE_CLASS,
                    plane=Plane.EXECUTION,
                    recommended_stage=ExecutionStageName.TROUBLESHOOTER,
                    message="execution status marker is impossible for current active stage",
                )
            )

    if snapshot.active_stage is not None and snapshot.active_plane == Plane.PLANNING:
        if planning_marker not in _PLANNING_STATUS_MARKERS or _has_impossible_marker_for_active_stage(
            snapshot,
            planning_marker,
        ):
            signals.append(
                ReconciliationSignal(
                    code="impossible_planning_status_marker",
                    failure_class=_IMPOSSIBLE_STATUS_FAILURE_CLASS,
                    plane=Plane.PLANNING,
                    recommended_stage=PlanningStageName.MECHANIC,
                    message="planning status marker is impossible for current active stage",
                )
            )

    if snapshot.active_stage is None:
        orphaned = _signal_for_orphaned_counters(counters)
        if orphaned is not None:
            signals.append(orphaned)

    return tuple(signals)


def _normalize_marker_or_invalid(marker: str, *, label: str) -> str:
    try:
        return _normalize_marker(marker, label=label)
    except ValueError:
        return _INVALID_MARKER


__all__ = [
    "ReconciliationSignal",
    "collect_reconciliation_signals",
    "increment_troubleshoot_attempt",
    "load_execution_status",
    "load_planning_status",
    "load_recovery_counters",
    "load_snapshot",
    "reset_forward_progress_counters",
    "save_recovery_counters",
    "save_snapshot",
    "set_execution_status",
    "set_planning_status",
]
