"""Non-closure work-item queue and snapshot transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.contracts import StageResultEnvelope, WorkItemKind
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterDecision
from millrace_ai.state_store import (
    load_recovery_counters,
    reset_forward_progress_counters,
    save_snapshot,
    set_execution_status,
    set_planning_status,
)

from .handoff_incidents import enqueue_handoff_incident

if TYPE_CHECKING:
    from millrace_ai.contracts import RuntimeSnapshot
    from millrace_ai.runtime.engine import RuntimeEngine


def mark_active_work_item_complete(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> None:
    queue = QueueStore(engine.paths)
    if stage_result.work_item_kind is WorkItemKind.TASK:
        queue.mark_task_done(stage_result.work_item_id)
        return
    if stage_result.work_item_kind is WorkItemKind.SPEC:
        queue.mark_spec_done(stage_result.work_item_id)
        return
    if stage_result.work_item_kind is WorkItemKind.INCIDENT:
        queue.mark_incident_resolved(stage_result.work_item_id)


def mark_active_work_item_blocked(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> None:
    queue = QueueStore(engine.paths)
    if stage_result.work_item_kind is WorkItemKind.TASK:
        queue.mark_task_blocked(stage_result.work_item_id)
        return
    if stage_result.work_item_kind is WorkItemKind.SPEC:
        queue.mark_spec_blocked(stage_result.work_item_id)
        return
    if stage_result.work_item_kind is WorkItemKind.INCIDENT:
        queue.mark_incident_blocked(stage_result.work_item_id)


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
                "work_item_kind": stage_result.work_item_kind.value,
                "work_item_id": stage_result.work_item_id,
                "error": str(exc),
            },
        )


def apply_idle_router_decision(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> None:
    mark_active_work_item_complete(engine, stage_result)
    engine.snapshot = _cleared_active_snapshot(
        engine,
        current_failure_class=None,
        execution_status_marker="### IDLE",
        planning_status_marker="### IDLE",
    )
    save_snapshot(engine.paths, engine.snapshot)
    set_execution_status(engine.paths, "### IDLE")
    set_planning_status(engine.paths, "### IDLE")
    reset_forward_progress_counters(
        engine.paths,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
    )
    engine.counters = load_recovery_counters(engine.paths)


def apply_handoff_router_decision(
    engine: RuntimeEngine,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
) -> None:
    if decision.create_incident:
        enqueue_handoff_incident(engine, decision=decision, stage_result=stage_result)
    mark_active_work_item_blocked_with_recovery(
        engine,
        stage_result,
        reason="handoff",
    )
    engine.snapshot = _cleared_active_snapshot(
        engine,
        current_failure_class=decision.failure_class,
    )
    save_snapshot(engine.paths, engine.snapshot)
    reset_forward_progress_counters(
        engine.paths,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
    )
    engine.counters = load_recovery_counters(engine.paths)


def apply_blocked_router_decision(
    engine: RuntimeEngine,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
) -> None:
    mark_active_work_item_blocked_with_recovery(
        engine,
        stage_result,
        reason="blocked",
    )
    engine.snapshot = _cleared_active_snapshot(
        engine,
        current_failure_class=decision.failure_class,
    )
    save_snapshot(engine.paths, engine.snapshot)
    reset_forward_progress_counters(
        engine.paths,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
    )
    engine.counters = load_recovery_counters(engine.paths)


def _cleared_active_snapshot(
    engine: RuntimeEngine,
    *,
    current_failure_class: str | None,
    execution_status_marker: str | None = None,
    planning_status_marker: str | None = None,
) -> RuntimeSnapshot:
    assert engine.snapshot is not None
    update = {
        "active_plane": None,
        "active_stage": None,
        "active_run_id": None,
        "active_work_item_kind": None,
        "active_work_item_id": None,
        "active_since": None,
        "current_failure_class": current_failure_class,
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
    return engine.snapshot.model_copy(update=update)


__all__ = [
    "apply_blocked_router_decision",
    "apply_handoff_router_decision",
    "apply_idle_router_decision",
    "mark_active_work_item_blocked",
    "mark_active_work_item_blocked_with_recovery",
    "mark_active_work_item_complete",
]
