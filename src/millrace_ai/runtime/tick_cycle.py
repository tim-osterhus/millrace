"""Deterministic runtime tick orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from millrace_ai.contracts import (
    ExecutionStageName,
    LearningStageName,
    Plane,
    PlanningStageName,
    StageName,
)
from millrace_ai.errors import StageWorkItemOwnershipError, WorkspaceStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.router import RouterDecision
from millrace_ai.runners import normalize_stage_result
from millrace_ai.runtime.outcomes import RuntimeTickOutcome
from millrace_ai.state_store import save_snapshot

from .activation import activate_claim_for_plane, claim_next_work_item_for_plane
from .capability_gates import (
    capability_gate_failure_result,
    evaluate_stage_request_capabilities,
    record_capability_gate_result,
    support_evaluator_for_request,
)
from .effect_execution import apply_runtime_effect_for_stage_result
from .error_recovery import (
    schedule_post_stage_exception_recovery,
    schedule_pre_dispatch_exception_recovery,
)
from .failure_policy import RuntimeFailureBoundary, classify_failure_origin
from .learning_promotions import (
    apply_deferred_learning_promotions_if_safe,
    handle_learning_curator_promotion_boundary,
)
from .learning_triggers import enqueue_learning_requests_for_stage_result
from .monitoring import runtime_effect_monitor_payload
from .result_application import compiled_plan_for_stage_result
from .run_traces import record_router_decision_trace, spawned_work_ref_from_path
from .stage_requests import handle_stage_work_item_ownership_error

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
    if (
        engine.snapshot.active_work_item_family_id is None
        and engine.snapshot.active_work_item_kind is not None
    ):
        engine.snapshot = engine.snapshot.model_copy(
            update={"active_work_item_family_id": engine.snapshot.active_work_item_kind.value}
        )

    if engine.snapshot.active_stage is None:
        recovery_decision = _claim_next_work_item_or_recover(engine)
        if recovery_decision is not None:
            return engine._idle_tick_outcome(reason=recovery_decision.reason)

    if engine.snapshot.active_stage is None:
        try:
            engine._maybe_activate_completion_stage()
        except Exception as exc:
            recovery_decision = schedule_pre_dispatch_exception_recovery(
                engine,
                error=exc,
                plane=Plane.PLANNING,
                failed_stage=PlanningStageName.ARBITER,
                closure_target_root_spec_id=_closure_target_root_spec_id(engine),
            )
            return engine._idle_tick_outcome(reason=recovery_decision.reason)

    if (
        engine.snapshot.active_stage is not None
        and engine.snapshot.active_plane is not None
        and (
            engine.snapshot.active_work_item_family_id is None
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
    try:
        if engine._is_completion_stage_active():
            closure_target = engine._active_closure_target()
            if closure_target is None:
                raise WorkspaceStateError("completion stage is active without an open closure target")
            request = engine._build_closure_target_stage_run_request(stage_plan, closure_target)
        else:
            request = engine._build_stage_run_request(stage_plan)
    except StageWorkItemOwnershipError as exc:
        handle_stage_work_item_ownership_error(engine, error=exc)
        return engine._idle_tick_outcome(reason="stage_work_item_ownership_invalid")
    except Exception as exc:
        recovery_decision = schedule_pre_dispatch_exception_recovery(
            engine,
            error=exc,
            plane=engine.snapshot.active_plane or Plane.PLANNING,
            failed_stage=_pre_dispatch_failed_stage(
                engine.snapshot.active_plane or Plane.PLANNING,
                engine.snapshot.active_stage,
            ),
            closure_target_root_spec_id=_closure_target_root_spec_id(engine),
        )
        return engine._idle_tick_outcome(reason=recovery_decision.reason)
    gate_result = evaluate_stage_request_capabilities(
        engine.paths,
        request=request,
        support_evaluator=support_evaluator_for_request(engine.stage_runner, request),
        now=engine._now,
    )
    request = gate_result.request
    record_capability_gate_result(engine.paths, request=request, gate_result=gate_result)

    if gate_result.allowed:
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
            work_item_family_id=request.active_work_item_family_id,
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
                "work_item_family_id": request.active_work_item_family_id,
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
            failure_origin = classify_failure_origin(
                exc,
                boundary=RuntimeFailureBoundary.RUNNER_INVOCATION,
            )
            write_runtime_event(
                engine.paths,
                event_type="runtime_worker_exception",
                data={
                    "failure_origin": failure_origin.value,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "plane": request.plane.value,
                    "run_id": request.run_id,
                    "stage": request.stage.value,
                },
            )
            raw_result = engine._runner_failure_result(
                request,
                failure_class=failure_origin.value,
                error=str(exc),
            )
    else:
        raw_result = capability_gate_failure_result(
            request=request,
            gate_result=gate_result,
            now=engine._now(),
        )

    stage_result = normalize_stage_result(request, raw_result)
    stage_result_path: Path | None = None
    router_decision: RouterDecision | None = None
    try:
        stage_result_path = engine._write_stage_result(request, stage_result)
        learning_request_paths = enqueue_learning_requests_for_stage_result(
            engine,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
        )
        result_compiled_plan = compiled_plan_for_stage_result(engine, stage_result)
        router_decision = engine._route_stage_result(
            stage_result,
            compiled_plan=result_compiled_plan,
        )
        engine._write_plane_status(stage_result)
        effect_application = apply_runtime_effect_for_stage_result(
            engine,
            request=request,
            stage_result=stage_result,
            router_decision=router_decision,
            stage_result_path=stage_result_path,
            compiled_plan=result_compiled_plan,
        )
        router_decision = effect_application.router_decision
        spawned_paths = effect_application.spawned_paths
        if not effect_application.source_lifecycle_applied:
            spawned_paths = (
                *spawned_paths,
                *engine._apply_router_decision(
                    router_decision,
                    stage_result,
                    stage_result_path=stage_result_path,
                    compiled_plan=result_compiled_plan,
                ),
            )
        effect_spawned_paths = frozenset(effect_application.spawned_paths)
        spawned_work = tuple(
            spawned_work_ref_from_path(
                path,
                source_stage_result=stage_result,
                reason=(
                    "learning_trigger"
                    if path in learning_request_paths
                    else "runtime_effect"
                    if path in effect_spawned_paths
                    else router_decision.reason
                ),
                compiled_plan=result_compiled_plan,
            )
            for path in (*learning_request_paths, *spawned_paths)
        )
        record_router_decision_trace(
            engine.paths,
            run_dir=Path(stage_result_path).parents[1],
            stage_result=stage_result,
            decision=router_decision,
            spawned_work=spawned_work,
        )
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
        if stage_result_path is not None:
            record_router_decision_trace(
                engine.paths,
                run_dir=Path(stage_result_path).parents[1],
                stage_result=stage_result,
                decision=recovery_decision,
                spawned_work=(),
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
    queue_depths = {
        "queue_depth_execution": engine._execution_queue_depth(),
        "queue_depth_planning": engine._planning_queue_depth(),
        "queue_depth_learning": engine._learning_queue_depth(),
    }
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "last_terminal_result": stage_result.terminal_result,
            "last_stage_result_path": str(stage_result_path.relative_to(engine.paths.root)),
            **queue_depths,
            "queue_depths_by_plane": {
                Plane.EXECUTION: queue_depths["queue_depth_execution"],
                Plane.PLANNING: queue_depths["queue_depth_planning"],
                Plane.LEARNING: queue_depths["queue_depth_learning"],
            },
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
            "work_item_family_id": stage_result.work_item_family_id,
            "work_item_kind": (
                stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
            ),
            "work_item_id": stage_result.work_item_id,
            "terminal_result": stage_result.terminal_result.value,
            "failure_class": stage_result.metadata.get("failure_class"),
            **cast(dict[str, JsonValue], runtime_effect_monitor_payload(stage_result.metadata)),
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
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=(
            stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
        ),
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
        **runtime_effect_monitor_payload(stage_result.metadata),
    )
    write_runtime_event(
        engine.paths,
        event_type="router_decision",
        data={
            "action": router_decision.action.value,
            "plane": stage_result.plane.value,
            "run_id": request.run_id,
            "work_item_family_id": stage_result.work_item_family_id,
            "work_item_kind": (
                stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
            ),
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
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=(
            stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
        ),
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


def _closure_target_root_spec_id(engine: RuntimeEngine) -> str | None:
    snapshot = engine.snapshot
    if snapshot is not None:
        active_run = snapshot.active_runs_by_plane.get(Plane.PLANNING)
        if active_run is not None and active_run.closure_target_root_spec_id is not None:
            return active_run.closure_target_root_spec_id
    try:
        target = engine._active_closure_target()
    except Exception:
        return None
    return target.root_spec_id if target is not None else None


def _pre_dispatch_failed_stage(plane: Plane, active_stage: StageName | None) -> StageName:
    if active_stage is not None:
        return active_stage
    if plane is Plane.PLANNING:
        return PlanningStageName.ARBITER
    if plane is Plane.LEARNING:
        return LearningStageName.ANALYST
    return ExecutionStageName.BUILDER


def _claim_next_work_item_or_recover(engine: RuntimeEngine) -> RouterDecision | None:
    try:
        open_target = engine._active_closure_target()
    except Exception as exc:
        return schedule_pre_dispatch_exception_recovery(
            engine,
            error=exc,
            plane=Plane.PLANNING,
            failed_stage=PlanningStageName.ARBITER,
            closure_target_root_spec_id=None,
        )

    planes = (
        (Plane.EXECUTION, Plane.PLANNING)
        if open_target is not None
        else (Plane.PLANNING, Plane.EXECUTION, Plane.LEARNING)
    )
    for plane in planes:
        try:
            claim = claim_next_work_item_for_plane(engine, plane)
        except Exception as exc:
            return schedule_pre_dispatch_exception_recovery(
                engine,
                error=exc,
                plane=plane,
                failed_stage=_pre_dispatch_failed_stage(plane, None),
                closure_target_root_spec_id=(
                    open_target.root_spec_id if open_target is not None else None
                ),
            )
        if claim is None:
            continue
        try:
            activate_claim_for_plane(engine, claim, plane)
        except RuntimeError:
            return None
        except Exception as exc:
            return schedule_pre_dispatch_exception_recovery(
                engine,
                error=exc,
                plane=plane,
                failed_stage=_pre_dispatch_failed_stage(plane, None),
                work_item_family_id=claim.family_id,
                work_item_kind=claim.work_item_kind,
                work_item_id=claim.work_item_id,
                closure_target_root_spec_id=(
                    open_target.root_spec_id if open_target is not None else None
                ),
            )
        return None
    return None


__all__ = ["run_tick"]
