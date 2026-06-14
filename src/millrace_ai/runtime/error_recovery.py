"""Runtime-owned recovery helpers for post-stage exceptions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import (
    ActiveRunState,
    ExecutionStageName,
    LearningStageName,
    Plane,
    PlanningStageName,
    RuntimeErrorCode,
    RuntimeErrorContext,
    RuntimeSnapshot,
    StageName,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.contracts.router import RouterAction, RouterDecision
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.state_store import save_snapshot
from millrace_ai.workspace.queue_transitions import mark_learning_request_blocked

from .active_runs import (
    snapshot_with_active_run,
    snapshot_with_next_stage_for_plane,
    snapshot_without_active_plane,
)
from .compiled_plans import CompiledPlanAuthorityError
from .failure_policy import RuntimeFailureBoundary, classify_failure_origin
from .graph_authority.terminal_actions import (
    decision_from_runtime_failure_recovery_exhaustion,
)
from .lanes import compiled_plan_fingerprint_for_runtime, lane_id_for_plane
from .recovery.error_context import (
    clear_runtime_error_context,
    load_runtime_error_context,
    report_path_for,
    save_runtime_error_context,
)
from .recovery.repair_routes import (
    RuntimeRepairRoute,
)
from .recovery.repair_routes import (
    runtime_repair_attempts_exhausted as _runtime_repair_attempts_exhausted,
)
from .recovery.repair_routes import (
    runtime_repair_route_for_plane as _runtime_repair_route_for_plane,
)
from .recovery.reports import (
    BLOCKED_MARKER,
    build_runtime_error_request_fields,
    path_relative_to_root,
    runtime_error_catalog_path,
    write_runtime_error_report,
)
from .result_counters import increment_counter_field

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan
    from millrace_ai.runtime.engine import RuntimeEngine


def schedule_post_stage_exception_recovery(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    error: Exception,
    router_decision: RouterDecision | None,
    stage_result_path: Path | None,
    error_code: RuntimeErrorCode | None = None,
) -> RouterDecision:
    """Persist runtime exception evidence and reroute into the default repair stage."""

    assert engine.snapshot is not None

    captured_at = engine._now()
    repair_route = runtime_repair_route_for_plane(
        engine,
        stage_result.plane,
        stage_result=stage_result,
    )
    repair_stage = repair_route.stage if repair_route is not None else stage_result.stage
    resolved_error_code = (
        error_code
        or classify_post_stage_exception(
            plane=stage_result.plane,
            error=error,
            router_decision=router_decision,
        )
    )
    context = record_post_stage_exception_context(
        engine,
        stage_result=stage_result,
        error=error,
        router_decision=router_decision,
        stage_result_path=stage_result_path,
        error_code=resolved_error_code,
        repair_stage=repair_stage,
        captured_at=captured_at,
    )
    if repair_route is None or runtime_repair_attempts_exhausted(engine, repair_route):
        reason_suffix = "repair_unavailable" if repair_route is None else "repair_attempts_exhausted"
        reason = f"runtime_exception:{resolved_error_code.value}:{reason_suffix}"
        blocked_decision = (
            decision_from_runtime_failure_recovery_exhaustion(
                engine.compiled_plan,
                stage_result.plane,
                reason=reason,
                failure_class=resolved_error_code.value,
            )
            if repair_route is not None
            else None
        ) or RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason=reason,
            failure_class=resolved_error_code.value,
        )
        engine._set_plane_status_marker(
            plane=stage_result.plane,
            marker=BLOCKED_MARKER,
            run_id=stage_result.run_id,
            source="runtime_recovery_exhausted",
        )
        from .work_item_transitions import apply_blocked_router_decision

        apply_blocked_router_decision(
            engine,
            blocked_decision,
            stage_result,
            stage_result_path=stage_result_path,
        )
        write_runtime_event(
            engine.paths,
            event_type="runtime_post_stage_recovery_exhausted",
            data={
                "error_code": resolved_error_code.value,
                "plane": stage_result.plane.value,
                "repair_stage": repair_stage.value,
                "repair_node_id": repair_route.node_id if repair_route is not None else None,
                "counter_name": repair_route.counter_name if repair_route is not None else None,
                "threshold": repair_route.threshold if repair_route is not None else None,
                "reason": reason_suffix,
                "report_path": context.report_path,
            },
        )
        return blocked_decision

    if stage_result.plane is Plane.EXECUTION:
        execution_marker = engine._set_plane_status_marker(
            plane=Plane.EXECUTION,
            marker=BLOCKED_MARKER,
            run_id=stage_result.run_id,
            source="runtime_recovery_blocked",
        )
        planning_marker = engine.snapshot.planning_status_marker
    else:
        planning_marker = engine._set_plane_status_marker(
            plane=Plane.PLANNING,
            marker=BLOCKED_MARKER,
            run_id=stage_result.run_id,
            source="runtime_recovery_blocked",
        )
        execution_marker = engine.snapshot.execution_status_marker

    updated_snapshot = snapshot_with_next_stage_for_plane(
        engine.snapshot,
        plane=stage_result.plane,
        stage=repair_stage,
        node_id=repair_route.node_id,
        stage_kind_id=repair_route.stage_kind_id,
        now=captured_at,
        current_failure_class=resolved_error_code.value,
    )
    queue_depths = {
        "queue_depth_execution": engine._execution_queue_depth(),
        "queue_depth_planning": engine._planning_queue_depth(),
        "queue_depth_learning": engine._learning_queue_depth(),
    }
    engine.snapshot = updated_snapshot.model_copy(
        update={
            "execution_status_marker": execution_marker,
            "planning_status_marker": planning_marker,
            **queue_depths,
            "queue_depths_by_plane": {
                Plane.EXECUTION: queue_depths["queue_depth_execution"],
                Plane.PLANNING: queue_depths["queue_depth_planning"],
                Plane.LEARNING: queue_depths["queue_depth_learning"],
            },
            "last_terminal_result": stage_result.terminal_result,
            "last_stage_result_path": path_relative_to_root(engine.paths, stage_result_path),
            "updated_at": captured_at,
        }
    )
    _increment_generic_counter(
        engine,
        snapshot=engine.snapshot,
        repair_route=repair_route,
        failure_class=resolved_error_code.value,
    )
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="runtime_post_stage_recovery_scheduled",
        data={
            "error_code": resolved_error_code.value,
            "plane": stage_result.plane.value,
            "failed_stage": stage_result.stage.value,
            "repair_stage": repair_stage.value,
            "repair_node_id": repair_route.node_id,
            "repair_stage_kind_id": repair_route.stage_kind_id,
            "router_action": router_decision.action.value if router_decision is not None else None,
            "terminal_result": stage_result.terminal_result.value,
            "work_item_family_id": stage_result.work_item_family_id,
            "work_item_kind": (
                stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
            ),
            "work_item_id": stage_result.work_item_id,
            "exception_type": type(error).__name__,
            "exception_message": str(error),
            "failure_origin": context.failure_origin.value if context.failure_origin else None,
            "report_path": context.report_path,
        },
    )
    return RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=stage_result.plane,
        next_stage=repair_stage,
        next_node_id=repair_route.node_id,
        next_stage_kind_id=repair_route.stage_kind_id,
        reason=f"runtime_exception:{resolved_error_code.value}",
        failure_class=resolved_error_code.value,
    )


def schedule_pre_dispatch_exception_recovery(
    engine: RuntimeEngine,
    *,
    error: Exception,
    plane: Plane,
    failed_stage: StageName | None = None,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | None = None,
    work_item_id: str | None = None,
    run_id: str | None = None,
    closure_target_root_spec_id: str | None = None,
) -> RouterDecision:
    """Persist pre-dispatch exception evidence and route the plane to repair."""

    assert engine.snapshot is not None

    captured_at = engine._now()
    error_code = classify_pre_dispatch_exception(plane=plane)
    repair_route = runtime_repair_route_for_plane(engine, plane)
    repair_stage = repair_route.stage if repair_route is not None else (
        failed_stage or _context_stage_for_plane(plane, compiled_plan=engine.compiled_plan)
    )
    failed = failed_stage or repair_stage
    family_id, kind, item_id = _pre_dispatch_work_identity(
        engine,
        plane=plane,
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        closure_target_root_spec_id=closure_target_root_spec_id,
    )
    recovery_run_id = run_id or engine.snapshot.active_run_id or engine._new_run_id()

    context = RuntimeErrorContext(
        error_code=error_code,
        failure_origin=classify_failure_origin(
            error,
            boundary=RuntimeFailureBoundary.RUNTIME_PRIMITIVE,
        ),
        plane=plane,
        failed_stage=failed,
        repair_stage=repair_stage,
        work_item_family_id=family_id,
        work_item_kind=kind,
        work_item_id=item_id,
        run_id=recovery_run_id,
        router_action=None,
        terminal_result=None,
        stage_result_path=None,
        report_path=str(report_path_for(paths=engine.paths, run_id=recovery_run_id)),
        exception_type=type(error).__name__,
        exception_message=str(error),
        captured_at=captured_at,
    )
    write_runtime_error_report(engine.paths, context)
    save_runtime_error_context(engine.paths, context)

    repair_exhausted = (
        _pre_dispatch_repair_attempts_exhausted(
            engine,
            repair_route,
            failure_class=error_code.value,
            work_item_family_id=family_id,
            work_item_id=item_id,
        )
        if repair_route is not None
        else False
    )
    if repair_route is None or repair_exhausted:
        reason_suffix = "repair_unavailable" if repair_route is None else "repair_attempts_exhausted"
        reason = f"runtime_exception:{error_code.value}:{reason_suffix}"
        blocked_decision = (
            decision_from_runtime_failure_recovery_exhaustion(
                engine.compiled_plan,
                plane,
                reason=reason,
                failure_class=error_code.value,
            )
            if repair_route is not None
            else None
        ) or RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason=reason,
            failure_class=error_code.value,
        )
        marker = engine._set_plane_status_marker(
            plane=plane,
            marker=BLOCKED_MARKER,
            run_id=recovery_run_id,
            source=f"runtime_pre_dispatch_{reason_suffix}",
        )
        if _is_learning_family(engine.compiled_plan, family_id=family_id, plane=plane):
            try:
                mark_learning_request_blocked(engine.paths, item_id)
            except QueueStateError as exc:
                write_runtime_event(
                    engine.paths,
                    event_type="runtime_pre_dispatch_blocked_mark_failed",
                    data={
                        "reason": reason_suffix,
                        "work_item_family_id": family_id,
                        "work_item_kind": kind.value if kind is not None else None,
                        "work_item_id": item_id,
                        "error": str(exc),
                    },
                )
        queue_depths = {
            "queue_depth_execution": engine._execution_queue_depth(),
            "queue_depth_planning": engine._planning_queue_depth(),
            "queue_depth_learning": engine._learning_queue_depth(),
        }
        cleared = snapshot_without_active_plane(
            engine.snapshot,
            plane=plane,
            now=captured_at,
            current_failure_class=error_code.value,
        )
        engine.snapshot = cleared.model_copy(
            update={
                "execution_status_marker": (
                    marker if plane is Plane.EXECUTION else engine.snapshot.execution_status_marker
                ),
                "planning_status_marker": (
                    marker if plane is Plane.PLANNING else engine.snapshot.planning_status_marker
                ),
                "learning_status_marker": (
                    marker if plane is Plane.LEARNING else engine.snapshot.learning_status_marker
                ),
                **queue_depths,
                "queue_depths_by_plane": {
                    Plane.EXECUTION: queue_depths["queue_depth_execution"],
                    Plane.PLANNING: queue_depths["queue_depth_planning"],
                    Plane.LEARNING: queue_depths["queue_depth_learning"],
                },
                "updated_at": captured_at,
            }
        )
        save_snapshot(engine.paths, engine.snapshot)
        write_runtime_event(
            engine.paths,
            event_type="runtime_pre_dispatch_recovery_blocked",
            data={
                "error_code": error_code.value,
                "plane": plane.value,
                "failed_stage": failed.value,
                "repair_stage": repair_stage.value,
                "repair_node_id": None,
                "counter_name": None,
                "threshold": None,
                "reason": reason_suffix,
                "work_item_family_id": family_id,
                "work_item_kind": kind.value if kind is not None else None,
                "work_item_id": item_id,
                "run_id": recovery_run_id,
                "report_path": context.report_path,
            },
        )
        return blocked_decision

    marker = engine._set_plane_status_marker(
        plane=plane,
        marker=BLOCKED_MARKER,
        run_id=recovery_run_id,
        source="runtime_pre_dispatch_recovery_blocked",
    )
    active_run = ActiveRunState(
        plane=plane,
        lane_id=lane_id_for_plane(engine.compiled_plan, plane),
        stage=repair_stage,
        node_id=repair_route.node_id,
        stage_kind_id=repair_route.stage_kind_id,
        run_id=recovery_run_id,
        compiled_plan_id=engine.snapshot.compiled_plan_id,
        compiled_plan_fingerprint=_compiled_plan_fingerprint(engine),
        request_kind=_request_kind_for_family(
            engine.compiled_plan,
            family_id=family_id,
            plane=plane,
        ),
        work_item_family_id=family_id,
        work_item_kind=kind,
        work_item_id=item_id,
        active_since=captured_at,
    )
    updated_snapshot = snapshot_with_active_run(
        engine.snapshot,
        active_run,
        now=captured_at,
        current_failure_class=error_code.value,
    )
    queue_depths = {
        "queue_depth_execution": engine._execution_queue_depth(),
        "queue_depth_planning": engine._planning_queue_depth(),
        "queue_depth_learning": engine._learning_queue_depth(),
    }
    engine.snapshot = updated_snapshot.model_copy(
        update={
            "execution_status_marker": (
                marker if plane is Plane.EXECUTION else engine.snapshot.execution_status_marker
            ),
            "planning_status_marker": (
                marker if plane is Plane.PLANNING else engine.snapshot.planning_status_marker
            ),
            "learning_status_marker": (
                marker if plane is Plane.LEARNING else engine.snapshot.learning_status_marker
            ),
            **queue_depths,
            "queue_depths_by_plane": {
                Plane.EXECUTION: queue_depths["queue_depth_execution"],
                Plane.PLANNING: queue_depths["queue_depth_planning"],
                Plane.LEARNING: queue_depths["queue_depth_learning"],
            },
            "updated_at": captured_at,
        }
    )
    _increment_generic_counter(
        engine,
        snapshot=engine.snapshot,
        repair_route=repair_route,
        failure_class=error_code.value,
    )
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="runtime_pre_dispatch_recovery_scheduled",
        data={
            "error_code": error_code.value,
            "plane": plane.value,
            "failed_stage": failed.value,
            "repair_stage": repair_stage.value,
            "repair_node_id": repair_route.node_id,
            "repair_stage_kind_id": repair_route.stage_kind_id,
            "work_item_family_id": family_id,
            "work_item_kind": kind.value if kind is not None else None,
            "work_item_id": item_id,
            "run_id": recovery_run_id,
            "exception_type": type(error).__name__,
            "exception_message": str(error),
            "failure_origin": context.failure_origin.value if context.failure_origin else None,
            "report_path": context.report_path,
        },
    )
    return RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=plane,
        next_stage=repair_stage,
        next_node_id=repair_route.node_id,
        next_stage_kind_id=repair_route.stage_kind_id,
        reason=f"runtime_exception:{error_code.value}",
        failure_class=error_code.value,
    )


def record_post_stage_exception_context(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    error: Exception,
    router_decision: RouterDecision | None,
    stage_result_path: Path | None,
    error_code: RuntimeErrorCode,
    repair_stage: StageName,
    captured_at: datetime | None = None,
) -> RuntimeErrorContext:
    """Persist runtime exception context without choosing a recovery route."""

    assert engine.snapshot is not None
    captured = captured_at if captured_at is not None else engine._now()
    context = RuntimeErrorContext(
        error_code=error_code,
        failure_origin=classify_failure_origin(
            error,
            boundary=RuntimeFailureBoundary.RESULT_APPLICATION,
        ),
        plane=stage_result.plane,
        failed_stage=stage_result.stage,
        repair_stage=repair_stage,
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
        run_id=stage_result.run_id,
        router_action=router_decision.action.value if router_decision is not None else None,
        terminal_result=stage_result.terminal_result,
        stage_result_path=path_relative_to_root(engine.paths, stage_result_path),
        report_path=str(report_path_for(paths=engine.paths, run_id=stage_result.run_id)),
        exception_type=type(error).__name__,
        exception_message=str(error),
        captured_at=captured,
    )
    write_runtime_error_report(engine.paths, context)
    save_runtime_error_context(engine.paths, context)
    return context


def classify_post_stage_exception(
    *,
    plane: Plane,
    error: Exception,
    router_decision: RouterDecision | None,
) -> RuntimeErrorCode:
    """Map post-stage exceptions onto stable runtime-owned error codes."""

    # Recon exception classification through runtime type identity
    # rather than direct Recon domain import.  This preserves
    # generic runtime error classification without eagerly loading
    # Recon domain modules (recon_transitions, recon_packets).
    if type(error).__name__ == "ReconHandoffInvalidError":
        return RuntimeErrorCode.RECON_HANDOFF_INVALID
    if isinstance(error, QueueStateError) and router_decision is not None and router_decision.action is RouterAction.IDLE:
        if plane is Plane.PLANNING:
            return RuntimeErrorCode.PLANNING_WORK_ITEM_COMPLETION_CONFLICT
        return RuntimeErrorCode.EXECUTION_WORK_ITEM_COMPLETION_CONFLICT
    if isinstance(error, CompiledPlanAuthorityError):
        if error.stale:
            return RuntimeErrorCode.COMPILED_PLAN_STALE
        return RuntimeErrorCode.WORKSPACE_INTEGRITY_FAILURE

    if plane is Plane.PLANNING:
        return RuntimeErrorCode.PLANNING_POST_STAGE_APPLY_FAILED
    return RuntimeErrorCode.EXECUTION_POST_STAGE_APPLY_FAILED


def classify_pre_dispatch_exception(*, plane: Plane) -> RuntimeErrorCode:
    """Map pre-dispatch exceptions onto stable runtime-owned error codes."""

    if plane is Plane.PLANNING:
        return RuntimeErrorCode.PLANNING_PRE_DISPATCH_FAILED
    if plane is Plane.LEARNING:
        return RuntimeErrorCode.LEARNING_PRE_DISPATCH_FAILED
    return RuntimeErrorCode.EXECUTION_PRE_DISPATCH_FAILED


def _context_stage_for_plane(
    plane: Plane,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> PlanningStageName | ExecutionStageName | LearningStageName:
    """Resolve a fallback repair stage for a plane.

    When *compiled_plan* is available, derives the repair stage from the
    compiled graph's runtime failure recovery default repair node.
    Otherwise raises ValueError to prevent hardwired domain fallback.
    """
    if compiled_plan is not None:
        graph = compiled_plan.graphs_by_plane.get(plane)
        if graph is not None and graph.runtime_failure_recovery is not None:
            from .graph_authority.stage_mapping import node_plan_by_id, stage_for_node

            repair_node = node_plan_by_id(graph, graph.runtime_failure_recovery.default_repair_node_id)
            return stage_for_node(graph, repair_node.node_id)
    raise ValueError(f"compiled plan is required to resolve repair stage for plane {plane.value}")


def runtime_repair_route_for_plane(
    engine: RuntimeEngine,
    plane: Plane,
    *,
    compiled_plan: CompiledRunPlan | None = None,
    stage_result: StageResultEnvelope | None = None,
) -> RuntimeRepairRoute | None:
    return _runtime_repair_route_for_plane(
        engine,
        plane,
        compiled_plan=compiled_plan,
        stage_result=stage_result,
    )


def runtime_repair_attempts_exhausted(engine: RuntimeEngine, repair_route: RuntimeRepairRoute) -> bool:
    return _runtime_repair_attempts_exhausted(engine, repair_route)


def _increment_generic_counter(
    engine: RuntimeEngine,
    *,
    snapshot: RuntimeSnapshot,
    repair_route: RuntimeRepairRoute,
    failure_class: str,
) -> None:
    """Increment the generic RecoveryCounters store for exception recovery.

    This updates both engine.counters (persisted to disk) and engine.snapshot
    (legacy compatibility fields) in one call.  Must be called after the
    snapshot model_copy so the legacy field update lands on the final snapshot.
    """
    if repair_route.counter_name is None:
        return
    if engine.counters is None:
        return
    family_id = snapshot.active_work_item_family_id
    work_item_id = snapshot.active_work_item_id
    if family_id is None or work_item_id is None:
        return
    engine.snapshot = increment_counter_field(
        engine,
        snapshot,
        engine.counters,
        failure_class=failure_class,
        work_item_family_id=family_id,
        work_item_kind=snapshot.active_work_item_kind,
        work_item_id=work_item_id,
        counter_id=repair_route.counter_name,
    )


def _pre_dispatch_repair_attempts_exhausted(
    engine: RuntimeEngine,
    repair_route: RuntimeRepairRoute,
    *,
    failure_class: str,
    work_item_family_id: str,
    work_item_id: str,
) -> bool:
    if repair_route.counter_name is None or repair_route.threshold is None:
        return False
    if engine.counters is None:
        return False
    for entry in engine.counters.entries:
        if (
            entry.failure_class == failure_class
            and entry.work_item_family_id == work_item_family_id
            and entry.work_item_id == work_item_id
        ):
            return entry.counters.get(repair_route.counter_name, 0) >= repair_route.threshold
    return False


def _pre_dispatch_work_identity(
    engine: RuntimeEngine,
    *,
    plane: Plane,
    work_item_family_id: str | None,
    work_item_kind: WorkItemKind | None,
    work_item_id: str | None,
    closure_target_root_spec_id: str | None,
) -> tuple[str, WorkItemKind | None, str]:
    snapshot = engine.snapshot
    assert snapshot is not None
    if work_item_family_id is not None and work_item_id is not None:
        return work_item_family_id, work_item_kind, work_item_id
    active_run = snapshot.active_runs_by_plane.get(plane)
    if active_run is not None and active_run.work_item_family_id and active_run.work_item_id:
        return active_run.work_item_family_id, active_run.work_item_kind, active_run.work_item_id
    if snapshot.active_plane is plane and snapshot.active_work_item_family_id and snapshot.active_work_item_id:
        return (
            snapshot.active_work_item_family_id,
            snapshot.active_work_item_kind,
            snapshot.active_work_item_id,
        )
    if closure_target_root_spec_id is not None:
        # Resolve closure-target family from compiled plan metadata.
        # The "spec" family is the canonical family for closure-target
        # work items; verify it exists in the compiled plan rather than
        # falling back to a hardwired WorkItemKind enum branch.
        if engine.compiled_plan is not None and "spec" in engine.compiled_plan.work_item_families_by_id:
            return "spec", None, closure_target_root_spec_id
        raise ValueError(
            f"cannot resolve closure-target family for "
            f"{closure_target_root_spec_id}: compiled plan missing spec family"
        )
    # Resolve identity from compiled plan lane metadata when available.
    if engine.compiled_plan is not None:
        lane_id = _lane_id_for_plane(engine, plane)
        if lane_id is not None:
            return lane_id, None, "runtime-pre-dispatch"
    raise ValueError(f"cannot resolve pre-dispatch work identity for plane {plane.value}")


def _is_learning_family(
    compiled_plan: CompiledRunPlan | None,
    *,
    family_id: str | None,
    plane: Plane | None = None,
) -> bool:
    """Determine whether a family is a Learning-domain family."""
    compiled_plan = _require_compiled_plan(compiled_plan, "derive learning family authority")
    if family_id is None:
        return plane is Plane.LEARNING
    family = compiled_plan.work_item_families_by_id.get(family_id)
    if family is None:
        if plane is not None and family_id == lane_id_for_plane(compiled_plan, plane):
            return plane is Plane.LEARNING
        raise CompiledPlanAuthorityError(
            f"compiled plan missing work item family `{family_id}`",
            stale=False,
        )
    return family.plane is Plane.LEARNING


def _request_kind_for_family(
    compiled_plan: CompiledRunPlan | None,
    *,
    family_id: str | None,
    plane: Plane | None = None,
) -> str:
    """Derive request_kind from compiled plan family metadata."""
    if _is_learning_family(compiled_plan, family_id=family_id, plane=plane):
        return "learning_request"
    return "active_work_item"


def _resolve_family_id_from_compiled_plan(
    compiled_plan: CompiledRunPlan | None,
    *,
    fallback_family_id: str,
) -> str:
    """Resolve a family id from compiled plan metadata."""
    compiled_plan = _require_compiled_plan(compiled_plan, "resolve work item family")
    if fallback_family_id in compiled_plan.work_item_families_by_id:
        return fallback_family_id
    raise CompiledPlanAuthorityError(
        f"compiled plan missing work item family `{fallback_family_id}`",
        stale=False,
    )


def _lane_id_for_plane(engine: RuntimeEngine, plane: Plane) -> str | None:
    """Resolve lane id for a plane from the compiled plan."""
    return lane_id_for_plane(
        _require_compiled_plan(engine.compiled_plan, f"resolve lane for {plane.value}"),
        plane,
    )


def _compiled_plan_fingerprint(engine: RuntimeEngine) -> str:
    return compiled_plan_fingerprint_for_runtime(
        _require_compiled_plan(engine.compiled_plan, "resolve compiled plan fingerprint")
    )


def _require_compiled_plan(
    compiled_plan: CompiledRunPlan | None,
    action: str,
) -> CompiledRunPlan:
    if compiled_plan is None:
        raise CompiledPlanAuthorityError(
            f"compiled plan is required to {action}",
            stale=False,
        )
    return compiled_plan


__all__ = [
    "build_runtime_error_request_fields",
    "classify_pre_dispatch_exception",
    "classify_post_stage_exception",
    "clear_runtime_error_context",
    "load_runtime_error_context",
    "record_post_stage_exception_context",
    "runtime_repair_attempts_exhausted",
    "runtime_repair_route_for_plane",
    "runtime_error_catalog_path",
    "schedule_post_stage_exception_recovery",
    "schedule_pre_dispatch_exception_recovery",
]
