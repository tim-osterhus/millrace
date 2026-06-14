"""Runtime stale-state reconciliation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from millrace_ai.contracts import ActiveRunState, Plane, RecoveryCounters, RuntimeSnapshot, StageName
from millrace_ai.state_store import ReconciliationSignal, collect_reconciliation_signals, load_recovery_counters, save_snapshot
from millrace_ai.workspace.queue_family_interpreter import QueueFamilyInterpreter

from .active_runs import active_run_for_plane, snapshot_with_active_run
from .status_projections import (
    build_active_run_projections,
    build_queue_projections,
    families_by_plane_from_interpreter,
)

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

_INVALID_RECONCILIATION_MARKER = "### INVALID_STATUS_MARKER"


def refresh_runtime_queue_depths(engine: RuntimeEngine, *, process_running: bool | None = None) -> None:
    assert engine.snapshot is not None

    # Build canonical family-keyed depths via the family interpreter.
    families = engine._work_item_families_for_lifecycle()
    family_interpreter = QueueFamilyInterpreter(
        engine.paths,
        families=families,
    )
    families_fp = families_by_plane_from_interpreter(family_interpreter)
    queue_proj = build_queue_projections(
        family_interpreter=family_interpreter,
        families_by_plane=families_fp,
    )

    # Derive lane-keyed active runs from the current active_runs_by_plane.
    ar_proj = build_active_run_projections(
        active_runs_by_plane=engine.snapshot.active_runs_by_plane,
    )

    update: dict[str, object] = {
        "queue_depth_execution": queue_proj.queue_depths_by_plane.get(Plane.EXECUTION, 0),
        "queue_depth_planning": queue_proj.queue_depths_by_plane.get(Plane.PLANNING, 0),
        "queue_depth_learning": queue_proj.queue_depths_by_plane.get(Plane.LEARNING, 0),
        "queue_depths_by_plane": dict(queue_proj.queue_depths_by_plane),
        "queue_depths_by_family": dict(queue_proj.queue_depths_by_family),
        "active_runs_by_lane": ar_proj.active_runs_by_lane,
        "updated_at": engine._now(),
    }
    if process_running is not None:
        update["process_running"] = process_running
    engine.snapshot = engine.snapshot.model_copy(update=update)


def run_reconciliation_if_needed(
    engine: RuntimeEngine,
    *,
    active_worker_runs_by_lane: Mapping[str, ActiveRunState] | None = None,
    active_worker_run_ids_by_lane: Mapping[str, str] | None = None,
) -> tuple[ReconciliationSignal, ...]:
    assert engine.snapshot is not None
    assert engine.counters is not None

    signals = collect_reconciliation_signals(
        snapshot=engine.snapshot,
        counters=engine.counters,
        execution_status_marker=status_marker_for_reconciliation(engine.paths.execution_status_file),
        planning_status_marker=status_marker_for_reconciliation(engine.paths.planning_status_file),
        learning_status_marker=status_marker_for_reconciliation(engine.paths.learning_status_file),
        compiled_plan=engine.compiled_plan,
    )
    if not signals:
        return signals

    primary_context = _signal_active_worker_context(
        engine.snapshot,
        signals[0],
        active_worker_runs_by_lane=active_worker_runs_by_lane,
        active_worker_run_ids_by_lane=active_worker_run_ids_by_lane,
    )
    if primary_context["action"] == "deferred":
        from millrace_ai.events import write_runtime_event

        write_runtime_event(
            engine.paths,
            event_type="runtime_reconciliation_deferred",
            data={
                **_signal_event_data(
                    signals[0],
                    engine=engine,
                    snapshot=engine.snapshot,
                    active_worker_runs_by_lane=active_worker_runs_by_lane,
                    active_worker_run_ids_by_lane=active_worker_run_ids_by_lane,
                    action="deferred_active_worker",
                    counter_incremented=False,
                ),
                "signal_count": len(signals),
            },
        )
        return signals

    signal_snapshot = engine.snapshot
    counters_before = engine.counters
    engine.snapshot = apply_reconciliation_signals(engine, signal_snapshot, counters_before, signals)
    engine.counters = load_recovery_counters(engine.paths)
    counter_incremented = engine.counters != counters_before
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
            "available_signals": [
                _signal_event_data(
                    signal,
                    engine=engine,
                    snapshot=signal_snapshot,
                    active_worker_runs_by_lane=active_worker_runs_by_lane,
                    active_worker_run_ids_by_lane=active_worker_run_ids_by_lane,
                    action="available",
                    counter_incremented=None,
                )
                for signal in signals
            ],
            "failure_class": signals[0].failure_class,
            "plane": signals[0].plane.value if signals[0].plane is not None else None,
            "lane": primary_context["lane"],
            "lane_id": primary_context["lane"],
            "snapshot_active_run_stage": primary_context["snapshot_active_run_stage"],
            "snapshot_active_run_node_id": primary_context["snapshot_active_run_node_id"],
            "snapshot_active_run_stage_kind_id": primary_context["snapshot_active_run_stage_kind_id"],
            "snapshot_active_run_id": primary_context["snapshot_active_run_id"],
            "active_worker_present": primary_context["active_worker_present"],
            "active_worker_stage": primary_context["active_worker_stage"],
            "active_worker_node_id": primary_context["active_worker_node_id"],
            "active_worker_stage_kind_id": primary_context["active_worker_stage_kind_id"],
            "active_worker_run_id": primary_context["active_worker_run_id"],
            "action": "applied",
            "counter_incremented": counter_incremented,
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
    node_id, stage_kind_id = _compiled_identity_for_stage(engine, plane=plane, stage=stage)
    active_run = active_run_for_plane(snapshot, plane)
    if active_run is None:
        return snapshot
    now = engine._now()
    updated_active_run = active_run.model_copy(
        update={
            "stage": stage,
            "node_id": node_id,
            "stage_kind_id": stage_kind_id,
            "run_id": engine._new_run_id(),
            "active_since": now,
            "running_status_marker": None,
        }
    )
    updated = snapshot_with_active_run(
        snapshot,
        updated_active_run,
        now=now,
        current_failure_class=signal.failure_class,
    )
    return set_recovery_counters(
        engine,
        updated,
        counters,
        signal.failure_class,
        stage,
        plane=plane,
    )


def set_recovery_counters(
    engine: RuntimeEngine,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    failure_class: str,
    stage: StageName,
    *,
    plane: Plane | None = None,
) -> RuntimeSnapshot:
    if snapshot.active_work_item_family_id is None or snapshot.active_work_item_id is None:
        return snapshot
    counter_id = _runtime_failure_counter_id(engine, plane=plane or snapshot.active_plane, stage=stage)
    if counter_id is not None:
        return engine._increment_counter_field(
            snapshot,
            counters,
            failure_class=failure_class,
            work_item_family_id=snapshot.active_work_item_family_id,
            work_item_kind=snapshot.active_work_item_kind,
            work_item_id=snapshot.active_work_item_id,
            counter_id=counter_id,
        )
    return snapshot


def _signal_active_worker_context(
    snapshot: RuntimeSnapshot,
    signal: ReconciliationSignal,
    *,
    active_worker_runs_by_lane: Mapping[str, ActiveRunState] | None,
    active_worker_run_ids_by_lane: Mapping[str, str] | None,
) -> dict[str, object]:
    plane = signal.plane or Plane.EXECUTION
    active_run = active_run_for_plane(snapshot, plane)
    lane = active_run.lane_id if active_run is not None else None
    active_worker_run = (
        active_worker_runs_by_lane.get(lane)
        if lane is not None and active_worker_runs_by_lane is not None
        else None
    )
    active_worker_run_id = (
        active_worker_run.run_id
        if active_worker_run is not None
        else active_worker_run_ids_by_lane.get(lane)
        if lane is not None and active_worker_run_ids_by_lane is not None
        else None
    )
    return {
        "lane": lane,
        "snapshot_active_run_stage": active_run.stage.value if active_run is not None else None,
        "snapshot_active_run_node_id": active_run.node_id if active_run is not None else None,
        "snapshot_active_run_stage_kind_id": active_run.stage_kind_id if active_run is not None else None,
        "snapshot_active_run_id": active_run.run_id if active_run is not None else None,
        "active_worker_present": active_worker_run is not None or active_worker_run_id is not None,
        "active_worker_stage": active_worker_run.stage.value if active_worker_run is not None else None,
        "active_worker_node_id": active_worker_run.node_id if active_worker_run is not None else None,
        "active_worker_stage_kind_id": active_worker_run.stage_kind_id if active_worker_run is not None else None,
        "active_worker_run_id": active_worker_run_id,
        "action": "deferred" if active_worker_run_id is not None else "applied",
        "counter_incremented": signal.recommended_stage is not None and active_run is not None,
    }


def _signal_event_data(
    signal: ReconciliationSignal,
    *,
    engine: RuntimeEngine,
    snapshot: RuntimeSnapshot,
    active_worker_runs_by_lane: Mapping[str, ActiveRunState] | None,
    active_worker_run_ids_by_lane: Mapping[str, str] | None,
    action: str,
    counter_incremented: bool | None,
) -> dict[str, object]:
    context = _signal_active_worker_context(
        snapshot,
        signal,
        active_worker_runs_by_lane=active_worker_runs_by_lane,
        active_worker_run_ids_by_lane=active_worker_run_ids_by_lane,
    )
    recovery_node_id = None
    recovery_stage_kind_id = None
    if signal.recommended_stage is not None:
        recovery_node_id, recovery_stage_kind_id = _compiled_identity_for_stage(
            engine,
            plane=signal.plane or Plane.EXECUTION,
            stage=signal.recommended_stage,
        )
    return {
        "signal": signal.code,
        "failure_class": signal.failure_class,
        "plane": signal.plane.value if signal.plane is not None else None,
        "lane": context["lane"],
        "lane_id": context["lane"],
        "snapshot_active_run_stage": context["snapshot_active_run_stage"],
        "snapshot_active_run_node_id": context["snapshot_active_run_node_id"],
        "snapshot_active_run_stage_kind_id": context["snapshot_active_run_stage_kind_id"],
        "snapshot_active_run_id": context["snapshot_active_run_id"],
        "active_worker_present": context["active_worker_present"],
        "active_worker_stage": context["active_worker_stage"],
        "active_worker_node_id": context["active_worker_node_id"],
        "active_worker_stage_kind_id": context["active_worker_stage_kind_id"],
        "active_worker_run_id": context["active_worker_run_id"],
        "action": action,
        "recovery_stage": signal.recommended_stage.value if signal.recommended_stage is not None else None,
        "recommended_recovery_stage": signal.recommended_stage.value if signal.recommended_stage is not None else None,
        "recommended_recovery_node_id": recovery_node_id,
        "recommended_recovery_stage_kind_id": recovery_stage_kind_id,
        "counter_incremented": counter_incremented,
    }


def _compiled_identity_for_stage(
    engine: RuntimeEngine,
    *,
    plane: Plane,
    stage: StageName,
) -> tuple[str, str]:
    try:
        stage_plan = engine._stage_plan_for(plane, stage)
    except KeyError:
        raise ValueError(f"compiled graph is missing stage identity for {plane.value}:{stage.value}") from None
    return stage_plan.node_id, stage_plan.stage_kind_id


def _runtime_failure_counter_id(
    engine: RuntimeEngine,
    *,
    plane: Plane | None,
    stage: StageName,
) -> str | None:
    if engine.compiled_plan is None or plane is None:
        raise ValueError("compiled plan is required to resolve reconciliation recovery counter")
    graph = engine.compiled_plan.graphs_by_plane.get(plane)
    if graph is None or graph.runtime_failure_recovery is None:
        return None
    repair_node_id = graph.runtime_failure_recovery.default_repair_node_id
    try:
        stage_plan = engine._stage_plan_for(plane, stage)
    except KeyError:
        return None
    if stage_plan.node_id != repair_node_id:
        return None
    return graph.runtime_failure_recovery.counter_name.value


__all__ = [
    "apply_reconciliation_signals",
    "refresh_runtime_queue_depths",
    "run_reconciliation_if_needed",
    "set_recovery_counters",
    "status_marker_for_reconciliation",
]
