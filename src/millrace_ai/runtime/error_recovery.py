"""Runtime-owned recovery helpers for post-stage exceptions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from millrace_ai.contracts import (
    ExecutionStageName,
    Plane,
    PlanningStageName,
    RuntimeErrorCode,
    RuntimeErrorContext,
    RuntimeSnapshot,
    StageResultEnvelope,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.state_store import save_snapshot
from millrace_ai.workspace.paths import WorkspacePaths

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


_BLOCKED_MARKER = "### BLOCKED"
_ERROR_CATALOG_RELATIVE_PATH = Path("docs/runtime/millrace-runtime-error-codes.md")


def build_runtime_error_request_fields(engine: RuntimeEngine) -> dict[str, str | None]:
    """Return request fields for recovery-stage prompts when runtime error context is active."""

    fields: dict[str, str | None] = {
        "runtime_error_code": None,
        "runtime_error_report_path": None,
        "runtime_error_catalog_path": None,
    }

    snapshot = engine.snapshot
    if snapshot is None or snapshot.active_stage not in {
        ExecutionStageName.TROUBLESHOOTER,
        PlanningStageName.MECHANIC,
    }:
        return fields

    context = load_runtime_error_context(engine.paths)
    if context is None:
        return fields
    if not _context_matches_snapshot(context, snapshot):
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
    report_path = _report_path_for(paths=engine.paths, run_id=stage_result.run_id)
    repair_node_id, repair_stage_kind_id = _compiled_identity_for_stage(
        engine,
        plane=stage_result.plane,
        stage=repair_stage,
    )
    context = RuntimeErrorContext(
        error_code=error_code,
        plane=stage_result.plane,
        failed_stage=stage_result.stage,
        repair_stage=repair_stage,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
        run_id=stage_result.run_id,
        router_action=router_decision.action.value if router_decision is not None else None,
        terminal_result=stage_result.terminal_result,
        stage_result_path=_path_relative_to_root(engine.paths, stage_result_path),
        report_path=str(report_path),
        exception_type=type(error).__name__,
        exception_message=str(error),
        captured_at=captured_at,
    )
    _write_runtime_error_report(engine.paths, context)
    _save_runtime_error_context(engine.paths, context)

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

    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_plane": stage_result.plane,
            "active_stage": repair_stage,
            "active_node_id": repair_node_id,
            "active_stage_kind_id": repair_stage_kind_id,
            "active_run_id": stage_result.run_id,
            "active_work_item_kind": stage_result.work_item_kind,
            "active_work_item_id": stage_result.work_item_id,
            "active_since": captured_at,
            "current_failure_class": error_code.value,
            "execution_status_marker": execution_marker,
            "planning_status_marker": planning_marker,
            "queue_depth_execution": engine._execution_queue_depth(),
            "queue_depth_planning": engine._planning_queue_depth(),
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
            "work_item_kind": stage_result.work_item_kind.value,
            "work_item_id": stage_result.work_item_id,
            "exception_type": type(error).__name__,
            "exception_message": str(error),
            "report_path": str(report_path),
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

    if plane is Plane.PLANNING:
        return RuntimeErrorCode.PLANNING_POST_STAGE_APPLY_FAILED
    return RuntimeErrorCode.EXECUTION_POST_STAGE_APPLY_FAILED


def _repair_stage_for_plane(plane: Plane) -> PlanningStageName | ExecutionStageName:
    if plane is Plane.PLANNING:
        return PlanningStageName.MECHANIC
    return ExecutionStageName.TROUBLESHOOTER


def _compiled_identity_for_stage(
    engine: RuntimeEngine,
    *,
    plane: Plane,
    stage: PlanningStageName | ExecutionStageName,
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
        f"Work-Item: {context.work_item_kind.value} {context.work_item_id}",
        f"Router-Action: {context.router_action or 'none'}",
        f"Terminal-Result: {context.terminal_result.value if context.terminal_result else 'none'}",
        f"Stage-Result-Path: {context.stage_result_path or 'none'}",
        f"Exception-Type: {context.exception_type}",
        f"Exception-Message: {context.exception_message}",
        f"Captured-At: {context.captured_at.isoformat()}",
        "",
        "Summary:",
        "- The runtime hit an exception after a stage returned a legal terminal result.",
        "- Forward progress was rerouted into the default recovery stage instead of exiting the daemon.",
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
        and snapshot.active_work_item_kind == context.work_item_kind
        and snapshot.active_work_item_id == context.work_item_id
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
    "classify_post_stage_exception",
    "clear_runtime_error_context",
    "load_runtime_error_context",
    "runtime_error_catalog_path",
    "schedule_post_stage_exception_recovery",
]
