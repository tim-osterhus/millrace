"""Runtime-owned recovery helpers for post-stage exceptions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

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
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.state_store import save_snapshot
from millrace_ai.workspace.paths import WorkspacePaths

from .active_runs import (
    active_run_for_plane,
    snapshot_with_active_run,
    snapshot_with_next_stage_for_plane,
)
from .compiled_plans import CompiledPlanAuthorityError
from .failure_policy import RuntimeFailureBoundary, classify_failure_origin
from .lanes import compiled_plan_fingerprint_for_runtime, lane_id_for_plane

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


_BLOCKED_MARKER = "### BLOCKED"
_ERROR_CATALOG_RELATIVE_PATH = Path("docs/runtime/millrace-runtime-error-codes.md")


def build_runtime_error_request_fields(
    engine: RuntimeEngine,
    *,
    plane: Plane | None = None,
) -> dict[str, str | None]:
    """Return request fields for recovery-stage prompts when runtime error context is active."""

    fields: dict[str, str | None] = {
        "runtime_error_code": None,
        "runtime_error_report_path": None,
        "runtime_error_catalog_path": None,
    }

    snapshot = engine.snapshot
    if snapshot is None:
        return fields

    context = load_runtime_error_context(engine.paths)
    if context is None:
        return fields

    if plane is not None:
        active_run = active_run_for_plane(snapshot, plane)
        if active_run is None or not _context_matches_active_run(context, active_run):
            return fields
    elif not _context_matches_snapshot(context, snapshot):
        return fields

    catalog_path = runtime_error_catalog_path(engine.paths)
    fields["runtime_error_code"] = context.error_code.value
    fields["runtime_error_report_path"] = context.report_path
    fields["runtime_error_catalog_path"] = str(catalog_path) if catalog_path is not None else None
    return fields


def runtime_error_catalog_path(paths: WorkspacePaths) -> Path | None:
    """Return the repo-visible runtime error catalog path when available."""

    catalog_path = paths.root / _ERROR_CATALOG_RELATIVE_PATH
    if not catalog_path.is_file():
        return None
    return catalog_path


def load_runtime_error_context(paths: WorkspacePaths) -> RuntimeErrorContext | None:
    """Load persisted runtime error context when present."""

    path = paths.runtime_error_context_file
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RuntimeErrorContext.model_validate(payload)


def clear_runtime_error_context(paths: WorkspacePaths) -> None:
    """Remove persisted runtime error context after recovery consumes it."""

    if paths.runtime_error_context_file.exists():
        paths.runtime_error_context_file.unlink()


def schedule_post_stage_exception_recovery(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    error: Exception,
    router_decision: RouterDecision | None,
    stage_result_path: Path | None,
) -> RouterDecision:
    """Persist runtime exception evidence and reroute into the default repair stage."""

    assert engine.snapshot is not None

    captured_at = engine._now()
    repair_stage = _repair_stage_for_plane(stage_result.plane)
    error_code = classify_post_stage_exception(
        plane=stage_result.plane,
        error=error,
        router_decision=router_decision,
    )
    repair_node_id, repair_stage_kind_id = _compiled_identity_for_stage(
        engine,
        plane=stage_result.plane,
        stage=repair_stage,
    )
    context = record_post_stage_exception_context(
        engine,
        stage_result=stage_result,
        error=error,
        router_decision=router_decision,
        stage_result_path=stage_result_path,
        error_code=error_code,
        repair_stage=repair_stage,
        captured_at=captured_at,
    )

    if stage_result.plane is Plane.EXECUTION:
        execution_marker = engine._set_plane_status_marker(
            plane=Plane.EXECUTION,
            marker=_BLOCKED_MARKER,
            run_id=stage_result.run_id,
            source="runtime_recovery_blocked",
        )
        planning_marker = engine.snapshot.planning_status_marker
    else:
        planning_marker = engine._set_plane_status_marker(
            plane=Plane.PLANNING,
            marker=_BLOCKED_MARKER,
            run_id=stage_result.run_id,
            source="runtime_recovery_blocked",
        )
        execution_marker = engine.snapshot.execution_status_marker

    updated_snapshot = snapshot_with_next_stage_for_plane(
        engine.snapshot,
        plane=stage_result.plane,
        stage=repair_stage,
        node_id=repair_node_id,
        stage_kind_id=repair_stage_kind_id,
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
            "execution_status_marker": execution_marker,
            "planning_status_marker": planning_marker,
            **queue_depths,
            "queue_depths_by_plane": {
                Plane.EXECUTION: queue_depths["queue_depth_execution"],
                Plane.PLANNING: queue_depths["queue_depth_planning"],
                Plane.LEARNING: queue_depths["queue_depth_learning"],
            },
            "last_terminal_result": stage_result.terminal_result,
            "last_stage_result_path": _path_relative_to_root(engine.paths, stage_result_path),
            "updated_at": captured_at,
        }
    )
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="runtime_post_stage_recovery_scheduled",
        data={
            "error_code": error_code.value,
            "plane": stage_result.plane.value,
            "failed_stage": stage_result.stage.value,
            "repair_stage": repair_stage.value,
            "repair_node_id": repair_node_id,
            "repair_stage_kind_id": repair_stage_kind_id,
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
        next_node_id=repair_node_id,
        next_stage_kind_id=repair_stage_kind_id,
        reason=f"runtime_exception:{error_code.value}",
        failure_class=error_code.value,
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
    repair_stage = _repair_stage_for_plane(plane)
    failed = failed_stage or repair_stage
    repair_node_id, repair_stage_kind_id = _compiled_identity_for_stage(
        engine,
        plane=plane,
        stage=repair_stage,
    )
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
        report_path=str(_report_path_for(paths=engine.paths, run_id=recovery_run_id)),
        exception_type=type(error).__name__,
        exception_message=str(error),
        captured_at=captured_at,
    )
    _write_runtime_error_report(engine.paths, context)
    _save_runtime_error_context(engine.paths, context)

    marker = engine._set_plane_status_marker(
        plane=plane,
        marker=_BLOCKED_MARKER,
        run_id=recovery_run_id,
        source="runtime_pre_dispatch_recovery_blocked",
    )
    active_run = ActiveRunState(
        plane=plane,
        lane_id=lane_id_for_plane(engine.compiled_plan, plane),
        stage=repair_stage,
        node_id=repair_node_id,
        stage_kind_id=repair_stage_kind_id,
        run_id=recovery_run_id,
        compiled_plan_id=engine.snapshot.compiled_plan_id,
        compiled_plan_fingerprint=_compiled_plan_fingerprint(engine),
        request_kind=(
            "learning_request"
            if plane is Plane.LEARNING and family_id == WorkItemKind.LEARNING_REQUEST.value
            else "active_work_item"
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
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="runtime_pre_dispatch_recovery_scheduled",
        data={
            "error_code": error_code.value,
            "plane": plane.value,
            "failed_stage": failed.value,
            "repair_stage": repair_stage.value,
            "repair_node_id": repair_node_id,
            "repair_stage_kind_id": repair_stage_kind_id,
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
        next_node_id=repair_node_id,
        next_stage_kind_id=repair_stage_kind_id,
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
    repair_stage: PlanningStageName | ExecutionStageName,
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
        stage_result_path=_path_relative_to_root(engine.paths, stage_result_path),
        report_path=str(_report_path_for(paths=engine.paths, run_id=stage_result.run_id)),
        exception_type=type(error).__name__,
        exception_message=str(error),
        captured_at=captured,
    )
    _write_runtime_error_report(engine.paths, context)
    _save_runtime_error_context(engine.paths, context)
    return context


def classify_post_stage_exception(
    *,
    plane: Plane,
    error: Exception,
    router_decision: RouterDecision | None,
) -> RuntimeErrorCode:
    """Map post-stage exceptions onto stable runtime-owned error codes."""

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


def _repair_stage_for_plane(plane: Plane) -> PlanningStageName | ExecutionStageName | LearningStageName:
    if plane is Plane.PLANNING:
        return PlanningStageName.MECHANIC
    if plane is Plane.LEARNING:
        return LearningStageName.LIBRARIAN
    return ExecutionStageName.TROUBLESHOOTER


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
        return WorkItemKind.SPEC.value, WorkItemKind.SPEC, closure_target_root_spec_id
    if plane is Plane.EXECUTION:
        return WorkItemKind.TASK.value, WorkItemKind.TASK, "runtime-pre-dispatch"
    if plane is Plane.LEARNING:
        return (
            WorkItemKind.LEARNING_REQUEST.value,
            WorkItemKind.LEARNING_REQUEST,
            "runtime-pre-dispatch",
        )
    return WorkItemKind.SPEC.value, WorkItemKind.SPEC, "runtime-pre-dispatch"


def _compiled_plan_fingerprint(engine: RuntimeEngine) -> str:
    if engine.compiled_plan is None:
        assert engine.snapshot is not None
        return engine.snapshot.compiled_plan_fingerprint
    return compiled_plan_fingerprint_for_runtime(engine.compiled_plan)


def _compiled_identity_for_stage(
    engine: RuntimeEngine,
    *,
    plane: Plane,
    stage: PlanningStageName | ExecutionStageName | LearningStageName,
) -> tuple[str, str]:
    try:
        stage_plan = engine._stage_plan_for(plane, stage)
    except KeyError:
        return stage.value, stage.value
    return stage_plan.node_id, stage_plan.stage_kind_id


def _save_runtime_error_context(paths: WorkspacePaths, context: RuntimeErrorContext) -> None:
    _atomic_write_text(paths.runtime_error_context_file, context.model_dump_json(indent=2) + "\n")


def _write_runtime_error_report(paths: WorkspacePaths, context: RuntimeErrorContext) -> None:
    lines = [
        "# Runtime Error Report",
        "",
        f"Error-Code: {context.error_code.value}",
        f"Plane: {context.plane.value}",
        f"Failed-Stage: {context.failed_stage.value}",
        f"Repair-Stage: {context.repair_stage.value}",
        f"Run-ID: {context.run_id}",
        f"Work-Item: {context.work_item_family_id} {context.work_item_id}",
        f"Router-Action: {context.router_action or 'none'}",
        f"Terminal-Result: {context.terminal_result.value if context.terminal_result else 'none'}",
        f"Stage-Result-Path: {context.stage_result_path or 'none'}",
        f"Exception-Type: {context.exception_type}",
        f"Exception-Message: {context.exception_message}",
        f"Failure-Origin: {context.failure_origin.value if context.failure_origin else 'none'}",
        f"Captured-At: {context.captured_at.isoformat()}",
        "",
        "Summary:",
        "- The runtime hit an exception after a stage returned a legal terminal result.",
        "- Runtime-owned handling either stopped this work item or rerouted it according to the error code.",
        "- Consult the runtime error catalog when the error code needs interpretation.",
    ]
    _atomic_write_text(Path(context.report_path), "\n".join(lines) + "\n")


def _report_path_for(*, paths: WorkspacePaths, run_id: str) -> Path:
    return paths.runs_dir / run_id / "runtime_error_report.md"


def _context_matches_snapshot(context: RuntimeErrorContext, snapshot: RuntimeSnapshot) -> bool:
    return (
        snapshot.current_failure_class == context.error_code.value
        and snapshot.active_plane is context.plane
        and snapshot.active_stage == context.repair_stage
        and snapshot.active_run_id == context.run_id
        and snapshot.active_work_item_family_id == context.work_item_family_id
        and snapshot.active_work_item_id == context.work_item_id
    )


def _context_matches_active_run(
    context: RuntimeErrorContext,
    active_run: ActiveRunState,
) -> bool:
    return (
        active_run.plane is context.plane
        and active_run.stage == context.repair_stage
        and active_run.run_id == context.run_id
        and active_run.work_item_family_id == context.work_item_family_id
        and active_run.work_item_id == context.work_item_id
    )


def _path_relative_to_root(paths: WorkspacePaths, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(paths.root))
    except ValueError:
        return str(path)


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


__all__ = [
    "build_runtime_error_request_fields",
    "classify_pre_dispatch_exception",
    "classify_post_stage_exception",
    "clear_runtime_error_context",
    "load_runtime_error_context",
    "record_post_stage_exception_context",
    "runtime_error_catalog_path",
    "schedule_post_stage_exception_recovery",
    "schedule_pre_dispatch_exception_recovery",
]
