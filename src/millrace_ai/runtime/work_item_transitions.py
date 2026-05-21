"""Non-closure work-item queue and snapshot transitions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import Plane, StageResultEnvelope
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.router import RouterDecision
from millrace_ai.state_store import (
    load_recovery_counters,
    reset_forward_progress_counters,
    save_snapshot,
)

from .active_runs import snapshot_without_active_plane
from .blocked_recovery import write_blocked_item_metadata
from .effects import SourceLifecycleAction, SourceLifecycleIntent
from .handoff_incidents import enqueue_handoff_incident
from .lifecycle_interpreter import apply_source_lifecycle_intent

if TYPE_CHECKING:
    from millrace_ai.contracts import RuntimeSnapshot
    from millrace_ai.runtime.engine import RuntimeEngine


def mark_active_work_item_complete(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> None:
    apply_source_lifecycle_intent(
        engine.paths,
        SourceLifecycleIntent(
            lifecycle_plan_id="complete_work_item",
            action=SourceLifecycleAction.COMPLETE,
            work_item_family_id=stage_result.work_item_family_id,
            work_item_kind=stage_result.work_item_kind,
            work_item_id=stage_result.work_item_id,
        ),
        compiled_plan=engine.compiled_plan,
    )


def mark_active_work_item_blocked(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> None:
    apply_source_lifecycle_intent(
        engine.paths,
        SourceLifecycleIntent(
            lifecycle_plan_id="block_work_item",
            action=SourceLifecycleAction.BLOCK,
            work_item_family_id=stage_result.work_item_family_id,
            work_item_kind=stage_result.work_item_kind,
            work_item_id=stage_result.work_item_id,
        ),
        compiled_plan=engine.compiled_plan,
    )


def mark_active_work_item_blocked_with_recovery(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
    *,
    reason: str,
) -> None:
    try:
        mark_active_work_item_blocked(engine, stage_result)
    except QueueStateError as exc:
        write_runtime_event(
            engine.paths,
            event_type="runtime_blocked_mark_failed",
            data={
                "reason": reason,
                "work_item_family_id": stage_result.work_item_family_id,
                "work_item_kind": (
                    stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
                ),
                "work_item_id": stage_result.work_item_id,
                "lifecycle_plan_id": "block_work_item",
                "error": str(exc),
            },
        )


def apply_idle_router_decision(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> None:
    mark_active_work_item_complete(engine, stage_result)
    engine.snapshot = _cleared_active_snapshot(
        engine,
        plane=stage_result.plane,
        current_failure_class=None,
    )
    save_snapshot(engine.paths, engine.snapshot)
    planes_to_idle = (
        (stage_result.plane,) if engine.snapshot.active_runs_by_plane else (Plane.EXECUTION, Plane.PLANNING, Plane.LEARNING)
    )
    for plane in planes_to_idle:
        engine._set_plane_status_marker(
            plane=plane,
            marker="### IDLE",
            run_id=stage_result.run_id if plane is stage_result.plane else None,
            source="router_idle",
        )
    reset_forward_progress_counters(
        engine.paths,
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
    )
    engine.counters = load_recovery_counters(engine.paths)


def apply_handoff_router_decision(
    engine: RuntimeEngine,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
    *,
    stage_result_path: Path | None = None,
) -> tuple[Path, ...]:
    spawned_paths: list[Path] = []
    if decision.create_incident:
        spawned_paths.append(enqueue_handoff_incident(engine, decision=decision, stage_result=stage_result))
    mark_active_work_item_blocked_with_recovery(
        engine,
        stage_result,
        reason="handoff",
    )
    write_blocked_item_metadata(
        engine.paths,
        stage_result=stage_result,
        decision=decision,
        stage_result_path=stage_result_path,
    )
    engine.snapshot = _cleared_active_snapshot(
        engine,
        plane=stage_result.plane,
        current_failure_class=decision.failure_class,
    )
    save_snapshot(engine.paths, engine.snapshot)
    reset_forward_progress_counters(
        engine.paths,
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
    )
    engine.counters = load_recovery_counters(engine.paths)
    return tuple(spawned_paths)


def apply_blocked_router_decision(
    engine: RuntimeEngine,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
    *,
    stage_result_path: Path | None = None,
) -> None:
    mark_active_work_item_blocked_with_recovery(
        engine,
        stage_result,
        reason="blocked",
    )
    write_blocked_item_metadata(
        engine.paths,
        stage_result=stage_result,
        decision=decision,
        stage_result_path=stage_result_path,
    )
    engine.snapshot = _cleared_active_snapshot(
        engine,
        plane=stage_result.plane,
        current_failure_class=decision.failure_class,
    )
    save_snapshot(engine.paths, engine.snapshot)
    reset_forward_progress_counters(
        engine.paths,
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
    )
    engine.counters = load_recovery_counters(engine.paths)


def _cleared_active_snapshot(
    engine: RuntimeEngine,
    *,
    plane: Plane,
    current_failure_class: str | None,
    execution_status_marker: str | None = None,
    planning_status_marker: str | None = None,
    learning_status_marker: str | None = None,
) -> RuntimeSnapshot:
    assert engine.snapshot is not None
    snapshot = snapshot_without_active_plane(
        engine.snapshot,
        plane=plane,
        now=engine._now(),
        current_failure_class=current_failure_class,
    )
    update = {
        "troubleshoot_attempt_count": 0,
        "mechanic_attempt_count": 0,
        "fix_cycle_count": 0,
        "consultant_invocations": 0,
        "updated_at": engine._now(),
    }
    if execution_status_marker is not None:
        update["execution_status_marker"] = execution_status_marker
    if planning_status_marker is not None:
        update["planning_status_marker"] = planning_status_marker
    if learning_status_marker is not None:
        update["learning_status_marker"] = learning_status_marker
    return snapshot.model_copy(update=update)


__all__ = [
    "apply_blocked_router_decision",
    "apply_handoff_router_decision",
    "apply_idle_router_decision",
    "mark_active_work_item_blocked",
    "mark_active_work_item_blocked_with_recovery",
    "mark_active_work_item_complete",
]
