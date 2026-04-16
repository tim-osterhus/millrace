"""Runtime stale-state reconciliation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import ExecutionStageName, Plane, PlanningStageName, RecoveryCounters, RuntimeSnapshot, StageName
from millrace_ai.state_store import ReconciliationSignal, collect_reconciliation_signals, load_recovery_counters, save_snapshot

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

_INVALID_RECONCILIATION_MARKER = "### INVALID_STATUS_MARKER"


def refresh_runtime_queue_depths(engine: RuntimeEngine, *, process_running: bool | None = None) -> None:
    assert engine.snapshot is not None
    update: dict[str, object] = {
        "queue_depth_execution": engine._execution_queue_depth(),
        "queue_depth_planning": engine._planning_queue_depth(),
        "updated_at": engine._now(),
    }
    if process_running is not None:
        update["process_running"] = process_running
    engine.snapshot = engine.snapshot.model_copy(update=update)


def run_reconciliation_if_needed(engine: RuntimeEngine) -> tuple[ReconciliationSignal, ...]:
    assert engine.snapshot is not None
    assert engine.counters is not None

    signals = collect_reconciliation_signals(
        snapshot=engine.snapshot,
        counters=engine.counters,
        execution_status_marker=status_marker_for_reconciliation(engine.paths.execution_status_file),
        planning_status_marker=status_marker_for_reconciliation(engine.paths.planning_status_file),
    )
    if not signals:
        return signals

    engine.snapshot = apply_reconciliation_signals(engine, engine.snapshot, engine.counters, signals)
    engine.counters = load_recovery_counters(engine.paths)
    refresh_runtime_queue_depths(engine)
    save_snapshot(engine.paths, engine.snapshot)
    from millrace_ai.events import write_runtime_event

    write_runtime_event(
        engine.paths,
        event_type="runtime_reconciled",
        data={
            "signal_count": len(signals),
            "primary_signal": signals[0].code,
            "recovery_stage": (
                signals[0].recommended_stage.value if signals[0].recommended_stage is not None else None
            ),
        },
    )
    return signals


def status_marker_for_reconciliation(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return _INVALID_RECONCILIATION_MARKER

    normalized = raw.strip()
    lines = normalized.splitlines()
    if len(lines) != 1 or not lines[0]:
        return _INVALID_RECONCILIATION_MARKER
    return lines[0]


def apply_reconciliation_signals(
    engine: RuntimeEngine,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    signals: tuple[ReconciliationSignal, ...],
) -> RuntimeSnapshot:
    signal = signals[0]
    plane = signal.plane or Plane.EXECUTION
    stage = signal.recommended_stage
    if stage is None:
        return snapshot
    updated = snapshot.model_copy(
        update={
            "active_plane": plane,
            "active_stage": stage,
            "active_run_id": engine._new_run_id(),
            "active_since": engine._now(),
            "current_failure_class": signal.failure_class,
        }
    )
    return set_recovery_counters(engine, updated, counters, signal.failure_class, stage)


def set_recovery_counters(
    engine: RuntimeEngine,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    failure_class: str,
    stage: StageName,
) -> RuntimeSnapshot:
    if snapshot.active_work_item_kind is None or snapshot.active_work_item_id is None:
        return snapshot
    if isinstance(stage, ExecutionStageName) and stage is ExecutionStageName.TROUBLESHOOTER:
        return engine._increment_counter_field(
            snapshot,
            counters,
            failure_class=failure_class,
            work_item_kind=snapshot.active_work_item_kind,
            work_item_id=snapshot.active_work_item_id,
            field="troubleshoot_attempt_count",
        )
    if isinstance(stage, PlanningStageName) and stage is PlanningStageName.MECHANIC:
        return engine._increment_counter_field(
            snapshot,
            counters,
            failure_class=failure_class,
            work_item_kind=snapshot.active_work_item_kind,
            work_item_id=snapshot.active_work_item_id,
            field="mechanic_attempt_count",
        )
    return snapshot


__all__ = [
    "apply_reconciliation_signals",
    "refresh_runtime_queue_depths",
    "run_reconciliation_if_needed",
    "set_recovery_counters",
    "status_marker_for_reconciliation",
]
