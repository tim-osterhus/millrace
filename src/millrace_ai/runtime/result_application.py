"""Stage-result routing, counter updates, and persisted result helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    IncidentDecision,
    IncidentDocument,
    IncidentSeverity,
    Plane,
    PlanningStageName,
    RecoveryCounterEntry,
    RecoveryCounters,
    RuntimeSnapshot,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterAction, RouterDecision, next_execution_step, next_planning_step
from millrace_ai.runner import StageRunRequest
from millrace_ai.state_store import (
    load_recovery_counters,
    reset_forward_progress_counters,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
    set_planning_status,
)

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def route_stage_result(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> RouterDecision:
    assert engine.snapshot is not None
    assert engine.counters is not None
    if stage_result.plane is Plane.EXECUTION:
        return next_execution_step(
            engine.snapshot,
            stage_result,
            engine.counters,
            max_fix_cycles=engine.config.recovery.max_fix_cycles if engine.config else 2,
            max_troubleshoot_attempts_before_consult=(
                engine.config.recovery.max_troubleshoot_attempts_before_consult if engine.config else 2
            ),
        )
    return next_planning_step(
        engine.snapshot,
        stage_result,
        engine.counters,
        max_mechanic_attempts=engine.config.recovery.max_mechanic_attempts if engine.config else 2,
    )


def apply_router_decision(engine: RuntimeEngine, decision: RouterDecision, stage_result: StageResultEnvelope) -> None:
    assert engine.snapshot is not None
    assert engine.counters is not None

    if decision.action is RouterAction.RUN_STAGE:
        next_stage = decision.next_stage
        assert next_stage is not None
        updated = engine.snapshot.model_copy(
            update={
                "active_plane": Plane.EXECUTION if isinstance(next_stage, ExecutionStageName) else Plane.PLANNING,
                "active_stage": next_stage,
                "active_since": engine._now(),
                "current_failure_class": decision.failure_class,
                "updated_at": engine._now(),
            }
        )
        engine.snapshot = increment_route_counters(engine, updated, decision, stage_result)
        return

    if decision.action is RouterAction.IDLE:
        mark_active_work_item_complete(engine, stage_result)
        engine.snapshot = engine.snapshot.model_copy(
            update={
                "active_plane": None,
                "active_stage": None,
                "active_run_id": None,
                "active_work_item_kind": None,
                "active_work_item_id": None,
                "active_since": None,
                "current_failure_class": None,
                "troubleshoot_attempt_count": 0,
                "mechanic_attempt_count": 0,
                "fix_cycle_count": 0,
                "consultant_invocations": 0,
                "execution_status_marker": "### IDLE",
                "planning_status_marker": "### IDLE",
                "updated_at": engine._now(),
            }
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
        return

    if decision.action is RouterAction.HANDOFF:
        if decision.create_incident:
            enqueue_handoff_incident(engine, decision=decision, stage_result=stage_result)
        mark_active_work_item_blocked_with_recovery(
            engine,
            stage_result,
            reason="handoff",
        )
        engine.snapshot = engine.snapshot.model_copy(
            update={
                "active_plane": None,
                "active_stage": None,
                "active_run_id": None,
                "active_work_item_kind": None,
                "active_work_item_id": None,
                "active_since": None,
                "current_failure_class": decision.failure_class,
                "troubleshoot_attempt_count": 0,
                "mechanic_attempt_count": 0,
                "fix_cycle_count": 0,
                "consultant_invocations": 0,
                "updated_at": engine._now(),
            }
        )
        save_snapshot(engine.paths, engine.snapshot)
        reset_forward_progress_counters(
            engine.paths,
            work_item_kind=stage_result.work_item_kind,
            work_item_id=stage_result.work_item_id,
        )
        engine.counters = load_recovery_counters(engine.paths)
        return

    if decision.action is RouterAction.BLOCKED:
        mark_active_work_item_blocked_with_recovery(
            engine,
            stage_result,
            reason="blocked",
        )
        engine.snapshot = engine.snapshot.model_copy(
            update={
                "active_plane": None,
                "active_stage": None,
                "active_run_id": None,
                "active_work_item_kind": None,
                "active_work_item_id": None,
                "active_since": None,
                "current_failure_class": decision.failure_class,
                "troubleshoot_attempt_count": 0,
                "mechanic_attempt_count": 0,
                "fix_cycle_count": 0,
                "consultant_invocations": 0,
                "updated_at": engine._now(),
            }
        )
        save_snapshot(engine.paths, engine.snapshot)
        reset_forward_progress_counters(
            engine.paths,
            work_item_kind=stage_result.work_item_kind,
            work_item_id=stage_result.work_item_id,
        )
        engine.counters = load_recovery_counters(engine.paths)


def increment_route_counters(
    engine: RuntimeEngine,
    snapshot: RuntimeSnapshot,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
) -> RuntimeSnapshot:
    assert engine.counters is not None
    work_item_kind = snapshot.active_work_item_kind
    work_item_id = snapshot.active_work_item_id
    if work_item_kind is None or work_item_id is None:
        return snapshot
    if decision.next_stage is ExecutionStageName.TROUBLESHOOTER:
        snapshot = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class=decision.failure_class or "recoverable_failure",
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            field="troubleshoot_attempt_count",
        )
    elif decision.next_stage is PlanningStageName.MECHANIC:
        snapshot = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class=decision.failure_class or "recoverable_failure",
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            field="mechanic_attempt_count",
        )
    elif decision.next_stage is ExecutionStageName.CONSULTANT:
        snapshot = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class=decision.failure_class or "recoverable_failure",
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            field="consultant_invocations",
        )
    elif stage_result.terminal_result is ExecutionTerminalResult.FIX_NEEDED:
        snapshot = increment_counter_field(
            engine,
            snapshot,
            engine.counters,
            failure_class=decision.failure_class or "fix_cycle",
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            field="fix_cycle_count",
        )
    return snapshot


def increment_counter_field(
    engine: RuntimeEngine,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    *,
    failure_class: str,
    work_item_kind: WorkItemKind,
    work_item_id: str,
    field: str,
) -> RuntimeSnapshot:
    timestamp = engine._now()
    mutable_entries = list(counters.entries)
    for index, entry in enumerate(mutable_entries):
        if (
            entry.failure_class == failure_class
            and entry.work_item_kind is work_item_kind
            and entry.work_item_id == work_item_id
        ):
            mutable_entries[index] = entry.model_copy(
                update={field: getattr(entry, field) + 1, "last_updated_at": timestamp}
            )
            break
    else:
        mutable_entries.append(
            RecoveryCounterEntry(
                failure_class=failure_class,
                work_item_kind=work_item_kind,
                work_item_id=work_item_id,
                troubleshoot_attempt_count=1 if field == "troubleshoot_attempt_count" else 0,
                mechanic_attempt_count=1 if field == "mechanic_attempt_count" else 0,
                fix_cycle_count=1 if field == "fix_cycle_count" else 0,
                consultant_invocations=1 if field == "consultant_invocations" else 0,
                last_updated_at=timestamp,
            )
        )
    updated_counters = RecoveryCounters(entries=tuple(mutable_entries))
    engine.counters = updated_counters
    save_recovery_counters(engine.paths, updated_counters)
    updated_snapshot = snapshot.model_copy(
        update={field: getattr(snapshot, field) + 1, "updated_at": engine._now()}
    )
    engine.snapshot = updated_snapshot
    return updated_snapshot


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


def enqueue_handoff_incident(
    engine: RuntimeEngine,
    *,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
) -> Path:
    queue = QueueStore(engine.paths)
    incident_id = f"incident-{stage_result.work_item_id}-{uuid4().hex[:8]}"
    source_task_id = (
        stage_result.work_item_id if stage_result.work_item_kind is WorkItemKind.TASK else None
    )
    source_spec_id = (
        stage_result.work_item_id if stage_result.work_item_kind is WorkItemKind.SPEC else None
    )
    incident = IncidentDocument(
        incident_id=incident_id,
        title=f"Planning handoff for {stage_result.work_item_kind.value} {stage_result.work_item_id}",
        summary=(
            f"Stage {stage_result.stage.value} returned {stage_result.terminal_result.value}; "
            "planning remediation required."
        ),
        source_task_id=source_task_id,
        source_spec_id=source_spec_id,
        source_stage=stage_result.stage,
        source_plane=stage_result.plane,
        failure_class=decision.failure_class or "consultant_needs_planning",
        severity=IncidentSeverity.HIGH,
        needs_planning=True,
        trigger_reason=decision.reason,
        observed_symptoms=stage_result.notes,
        failed_attempts=(),
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        evidence_paths=stage_result.artifact_paths,
        related_run_ids=(stage_result.run_id,),
        related_stage_results=(
            engine.snapshot.last_stage_result_path,
        )
        if engine.snapshot is not None and engine.snapshot.last_stage_result_path is not None
        else (),
        references=(),
        opened_at=engine._now(),
        opened_by="runtime",
    )
    destination = queue.enqueue_incident(incident)
    write_runtime_event(
        engine.paths,
        event_type="runtime_handoff_incident_enqueued",
        data={
            "incident_id": incident_id,
            "source_work_item_kind": stage_result.work_item_kind.value,
            "source_work_item_id": stage_result.work_item_id,
            "destination": str(destination.relative_to(engine.paths.root)),
        },
    )
    return destination


def write_stage_result(
    engine: RuntimeEngine,
    request: StageRunRequest,
    stage_result: StageResultEnvelope,
) -> Path:
    del engine
    run_dir = Path(request.run_dir)
    stage_result_dir = run_dir / "stage_results"
    stage_result_dir.mkdir(parents=True, exist_ok=True)
    stage_result_path = stage_result_dir / f"{request.request_id}.json"
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return stage_result_path


def write_plane_status(engine: RuntimeEngine, stage_result: StageResultEnvelope) -> None:
    assert engine.snapshot is not None
    if stage_result.plane is Plane.EXECUTION:
        set_execution_status(engine.paths, stage_result.summary_status_marker)
        engine.snapshot = engine.snapshot.model_copy(
            update={"execution_status_marker": stage_result.summary_status_marker}
        )
        return
    set_planning_status(engine.paths, stage_result.summary_status_marker)
    engine.snapshot = engine.snapshot.model_copy(
        update={"planning_status_marker": stage_result.summary_status_marker}
    )


__all__ = [
    "apply_router_decision",
    "enqueue_handoff_incident",
    "increment_counter_field",
    "increment_route_counters",
    "mark_active_work_item_blocked",
    "mark_active_work_item_blocked_with_recovery",
    "mark_active_work_item_complete",
    "route_stage_result",
    "write_plane_status",
    "write_stage_result",
]
