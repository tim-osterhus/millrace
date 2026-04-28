"""Deterministic runtime tick orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.errors import WorkspaceStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.router import RouterDecision
from millrace_ai.runners import normalize_stage_result
from millrace_ai.runtime.outcomes import RuntimeTickOutcome
from millrace_ai.state_store import save_snapshot

from .error_recovery import schedule_post_stage_exception_recovery
from .learning_promotions import (
    apply_deferred_learning_promotions_if_safe,
    handle_learning_curator_promotion_boundary,
)
from .learning_triggers import enqueue_learning_requests_for_stage_result

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def run_tick(engine: RuntimeEngine) -> RuntimeTickOutcome:
    """Run one deterministic runtime tick."""

    if engine.snapshot is None or engine.counters is None or engine.compiled_plan is None:
        engine.startup()
    assert engine.snapshot is not None
    assert engine.counters is not None
    assert engine.compiled_plan is not None

    # Deterministic tick order: mailbox/control intake, reconciliation, then stage execution.
    engine._drain_mailbox()
    engine._consume_watcher_events()
    engine._refresh_runtime_queue_depths()

    if engine.snapshot.stop_requested:
        engine._reset_runtime_to_idle(
            process_running=False,
            clear_stop_requested=True,
            clear_paused=True,
        )
        engine._close_watcher_session()
        engine._release_daemon_ownership_lock(force=False)
        write_runtime_event(engine.paths, event_type="runtime_tick_stopped")
        engine._emit_monitor_event("runtime_stopped", reason="stop_requested")
        return engine._idle_tick_outcome(reason="stop_requested")

    engine._evaluate_usage_governance()

    if engine.snapshot.paused:
        save_snapshot(engine.paths, engine.snapshot)
        write_runtime_event(engine.paths, event_type="runtime_tick_paused")
        engine._emit_monitor_event("runtime_paused", reason="paused")
        return engine._idle_tick_outcome(reason="paused")

    engine._run_reconciliation_if_needed()
    engine._refresh_runtime_queue_depths(process_running=True)

    if engine.snapshot.active_stage is None:
        engine._claim_next_work_item()

    if engine.snapshot.active_stage is None:
        engine._maybe_activate_completion_stage()

    if (
        engine.snapshot.active_stage is not None
        and engine.snapshot.active_plane is not None
        and (
            engine.snapshot.active_work_item_kind is None
            or engine.snapshot.active_work_item_id is None
        )
        and not engine._is_completion_stage_active()
    ):
        write_runtime_event(
            engine.paths,
            event_type="runtime_tick_invalid_active_state",
            data={"reason": "missing_active_work_item_identity"},
        )
        engine._clear_stale_state()
        save_snapshot(engine.paths, engine.snapshot)
        return engine._idle_tick_outcome(reason="missing_active_work_item_identity")

    if engine.snapshot.active_stage is None or engine.snapshot.active_plane is None:
        save_snapshot(engine.paths, engine.snapshot)
        write_runtime_event(engine.paths, event_type="runtime_tick_idle")
        engine._emit_monitor_event("runtime_idle", reason="no_work")
        return engine._idle_tick_outcome(reason="no_work")

    engine._evaluate_usage_governance()
    if engine.snapshot.paused:
        save_snapshot(engine.paths, engine.snapshot)
        write_runtime_event(engine.paths, event_type="runtime_tick_paused")
        engine._emit_monitor_event("runtime_paused", reason="paused")
        return engine._idle_tick_outcome(reason="paused")

    stage_plan = engine._stage_plan_for(
        engine.snapshot.active_plane,
        engine.snapshot.active_stage,
        node_id=engine.snapshot.active_node_id,
    )
    if engine._is_completion_stage_active():
        closure_target = engine._active_closure_target()
        if closure_target is None:
            raise WorkspaceStateError("completion stage is active without an open closure target")
        request = engine._build_closure_target_stage_run_request(stage_plan, closure_target)
    else:
        request = engine._build_stage_run_request(stage_plan)
    engine._mark_active_stage_running(
        plane=request.plane,
        stage=request.stage,
        running_status_marker=request.running_status_marker,
        run_id=request.run_id,
    )
    engine._emit_monitor_event(
        "stage_started",
        plane=request.plane.value,
        stage=request.stage.value,
        node_id=request.node_id,
        stage_kind_id=request.stage_kind_id,
        run_id=request.run_id,
        work_item_kind=(
            request.active_work_item_kind.value if request.active_work_item_kind else None
        ),
        work_item_id=request.active_work_item_id,
        status_marker=request.running_status_marker,
    )
    write_runtime_event(
        engine.paths,
        event_type="stage_started",
        data={
            "request_id": request.request_id,
            "stage": request.stage.value,
            "node_id": request.node_id,
            "stage_kind_id": request.stage_kind_id,
            "plane": request.plane.value,
            "run_id": request.run_id,
            "work_item_kind": (
                request.active_work_item_kind.value if request.active_work_item_kind else None
            ),
            "work_item_id": request.active_work_item_id,
            "troubleshoot_report_path": request.preferred_troubleshoot_report_path,
        },
    )

    try:
        raw_result = engine.stage_runner(request)
    except Exception as exc:  # pragma: no cover - defensive path
        raw_result = engine._runner_failure_result(request, failure_class="runner_error", error=str(exc))

    stage_result = normalize_stage_result(request, raw_result)
    stage_result_path: Path | None = None
    router_decision: RouterDecision | None = None
    try:
        stage_result_path = engine._write_stage_result(request, stage_result)
        enqueue_learning_requests_for_stage_result(
            engine,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
        )
        router_decision = engine._route_stage_result(stage_result)
        engine._write_plane_status(stage_result)
        engine._apply_router_decision(router_decision, stage_result)
        handle_learning_curator_promotion_boundary(engine, stage_result=stage_result)
        apply_deferred_learning_promotions_if_safe(engine)
    except Exception as exc:
        recovery_decision = schedule_post_stage_exception_recovery(
            engine,
            stage_result=stage_result,
            error=exc,
            router_decision=router_decision,
            stage_result_path=stage_result_path,
        )
        return RuntimeTickOutcome(
            stage=stage_result.stage,
            stage_result=stage_result,
            stage_result_path=stage_result_path
            or (engine.paths.logs_dir / f"{request.request_id}.stage_result.unavailable.json"),
            router_decision=recovery_decision,
            snapshot=engine.snapshot,
        )

    assert stage_result_path is not None
    assert router_decision is not None
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "last_terminal_result": stage_result.terminal_result,
            "last_stage_result_path": str(stage_result_path.relative_to(engine.paths.root)),
            "queue_depth_execution": engine._execution_queue_depth(),
            "queue_depth_planning": engine._planning_queue_depth(),
            "updated_at": engine._now(),
        }
    )
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="stage_completed",
        data={
            "request_id": request.request_id,
            "stage": stage_result.stage.value,
            "node_id": stage_result.node_id,
            "stage_kind_id": stage_result.stage_kind_id,
            "plane": stage_result.plane.value,
            "run_id": request.run_id,
            "work_item_kind": stage_result.work_item_kind.value,
            "work_item_id": stage_result.work_item_id,
            "terminal_result": stage_result.terminal_result.value,
            "failure_class": stage_result.metadata.get("failure_class"),
            "troubleshoot_report_path": (
                stage_result.report_artifact or request.preferred_troubleshoot_report_path
            ),
        },
    )
    engine._emit_monitor_event(
        "stage_completed",
        plane=stage_result.plane.value,
        stage=stage_result.stage.value,
        node_id=stage_result.node_id,
        stage_kind_id=stage_result.stage_kind_id,
        run_id=stage_result.run_id,
        work_item_kind=stage_result.work_item_kind.value,
        work_item_id=stage_result.work_item_id,
        terminal_result=stage_result.terminal_result.value,
        summary_status_marker=stage_result.summary_status_marker,
        started_at=stage_result.started_at.isoformat(),
        completed_at=stage_result.completed_at.isoformat(),
        duration_seconds=stage_result.duration_seconds,
        token_usage=(
            stage_result.token_usage.model_dump(mode="json")
            if stage_result.token_usage is not None
            else None
        ),
    )
    write_runtime_event(
        engine.paths,
        event_type="router_decision",
        data={
            "action": router_decision.action.value,
            "plane": stage_result.plane.value,
            "run_id": request.run_id,
            "work_item_kind": stage_result.work_item_kind.value,
            "work_item_id": stage_result.work_item_id,
            "stage": stage_result.stage.value,
            "node_id": stage_result.node_id,
            "stage_kind_id": stage_result.stage_kind_id,
            "terminal_result": stage_result.terminal_result.value,
            "failure_class": stage_result.metadata.get("failure_class"),
            "troubleshoot_report_path": (
                stage_result.report_artifact or request.preferred_troubleshoot_report_path
            ),
            "next_stage": router_decision.next_stage.value if router_decision.next_stage else None,
            "next_node_id": router_decision.next_node_id,
            "next_stage_kind_id": router_decision.next_stage_kind_id,
            "reason": router_decision.reason,
        },
    )
    engine._emit_monitor_event(
        "router_decision",
        action=router_decision.action.value,
        plane=stage_result.plane.value,
        run_id=stage_result.run_id,
        work_item_kind=stage_result.work_item_kind.value,
        work_item_id=stage_result.work_item_id,
        stage=stage_result.stage.value,
        node_id=stage_result.node_id,
        stage_kind_id=stage_result.stage_kind_id,
        terminal_result=stage_result.terminal_result.value,
        failure_class=stage_result.metadata.get("failure_class"),
        next_stage=router_decision.next_stage.value if router_decision.next_stage else None,
        next_node_id=router_decision.next_node_id,
        next_stage_kind_id=router_decision.next_stage_kind_id,
        reason=router_decision.reason,
    )
    engine._evaluate_usage_governance(
        stage_result=stage_result,
        stage_result_path=stage_result_path,
    )

    return RuntimeTickOutcome(
        stage=stage_result.stage,
        stage_result=stage_result,
        stage_result_path=stage_result_path,
        router_decision=router_decision,
        snapshot=engine.snapshot,
    )


__all__ = ["run_tick"]
