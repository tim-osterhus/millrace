"""Stage-request construction and runtime clock/id helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from millrace_ai.architecture import MaterializedGraphNodePlan
from millrace_ai.contracts import (
    ClosureTargetState,
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    ResultClass,
    StageName,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.errors import StageWorkItemOwnershipError
from millrace_ai.events import write_runtime_event
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runners import RunnerRawResult, StageRunRequest
from millrace_ai.runners.requests import RequestKind
from millrace_ai.runtime.outcomes import RuntimeTickOutcome
from millrace_ai.state_store import save_snapshot
from millrace_ai.workspace.work_inventory import queue_depths_by_plane

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

from .active_runs import active_run_for_plane
from .error_recovery import build_runtime_error_request_fields
from .graph_authority.stage_mapping import stage_for_stage_kind
from .request_context import attach_default_request_context
from .skill_evidence import write_skill_revision_evidence

_STATUS_IDLE = "### IDLE"
_STAGE_WORK_ITEM_OWNERSHIP_INVALID = "stage_work_item_ownership_invalid"


def build_stage_run_request(
    engine: RuntimeEngine,
    stage_plan: MaterializedGraphNodePlan,
) -> StageRunRequest:
    assert engine.snapshot is not None
    active_run = active_run_for_plane(engine.snapshot, stage_plan.plane)
    active_work_item_kind = (
        active_run.work_item_kind if active_run is not None else engine.snapshot.active_work_item_kind
    )
    active_work_item_family_id = (
        active_run.work_item_family_id
        if active_run is not None
        else engine.snapshot.active_work_item_family_id
    )
    active_work_item_id = (
        active_run.work_item_id if active_run is not None else engine.snapshot.active_work_item_id
    )
    request_kind = (
        active_run.request_kind
        if active_run is not None
        else _request_kind_for_active_family(engine.snapshot.active_work_item_family_id)
    )
    active_path = active_work_item_path(
        engine,
        active_work_item_kind,
        active_work_item_id,
        work_item_family_id=active_work_item_family_id,
    )
    validate_stage_work_item_ownership(
        stage_plan,
        request_kind=request_kind,
        active_work_item_family_id=active_work_item_family_id,
    )
    run_id = active_run.run_id if active_run is not None else engine.snapshot.active_run_id or new_run_id()
    run_dir = engine.paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    runtime_error_fields = build_runtime_error_request_fields(
        engine,
        plane=stage_plan.plane,
    )
    stage_name = _stage_name_for_node_plan(stage_plan)
    request_id = new_request_id()
    required_skill_paths = tuple(
        str(engine.paths.runtime_root / path) for path in stage_plan.required_skill_paths
    )
    attached_skill_paths = tuple(
        str(engine.paths.runtime_root / path) for path in stage_plan.attached_skill_additions
    )
    skill_revision_evidence_path = _write_skill_revision_evidence_if_enabled(
        engine,
        run_dir=run_dir,
        request_id=request_id,
        run_id=run_id,
        skill_paths=(*required_skill_paths, *attached_skill_paths),
    )
    request = StageRunRequest(
        request_id=request_id,
        run_id=run_id,
        plane=stage_plan.plane,
        stage=stage_name,
        mode_id=engine.snapshot.active_mode_id,
        compiled_plan_id=(
            active_run.compiled_plan_id if active_run is not None else engine.snapshot.compiled_plan_id
        ),
        node_id=stage_plan.node_id,
        stage_kind_id=stage_plan.stage_kind_id,
        running_status_marker=stage_plan.running_status_marker,
        legal_terminal_markers=_legal_terminal_markers_for_stage_plan(stage_plan),
        allowed_result_classes_by_outcome=stage_plan.allowed_result_classes_by_outcome,
        request_kind=request_kind,
        entrypoint_path=str(engine.paths.runtime_root / stage_plan.entrypoint_path),
        entrypoint_contract_id=stage_plan.entrypoint_contract_id,
        required_skill_paths=required_skill_paths,
        attached_skill_paths=attached_skill_paths,
        active_work_item_family_id=active_work_item_family_id,
        active_work_item_kind=active_work_item_kind,
        active_work_item_id=active_work_item_id,
        active_work_item_path=str(active_path) if active_path is not None else None,
        run_dir=str(run_dir),
        summary_status_path=str(_status_file_for_plane(engine, stage_plan.plane)),
        runtime_snapshot_path=str(engine.paths.runtime_snapshot_file),
        recovery_counters_path=str(engine.paths.recovery_counters_file),
        preferred_troubleshoot_report_path=str(run_dir / "troubleshoot_report.md"),
        runtime_error_code=runtime_error_fields["runtime_error_code"],
        runtime_error_report_path=runtime_error_fields["runtime_error_report_path"],
        runtime_error_catalog_path=runtime_error_fields["runtime_error_catalog_path"],
        skill_revision_evidence_path=str(skill_revision_evidence_path)
        if skill_revision_evidence_path is not None
        else None,
        runner_name=stage_plan.runner_name,
        model_name=stage_plan.model_name,
        thinking_level=stage_plan.thinking_level,
        model_reasoning_effort=stage_plan.model_reasoning_effort,
        timeout_seconds=stage_plan.timeout_seconds,
        execution_capability_grants=stage_plan.execution_capability_grants,
    )
    request = attach_default_request_context(
        workspace_root=engine.paths.root,
        request=request,
        compiled_plan=engine.compiled_plan,
    )
    engine.snapshot = engine.snapshot.model_copy(update={"active_run_id": request.run_id})
    save_snapshot(engine.paths, engine.snapshot)
    return request


def validate_stage_work_item_ownership(
    stage_plan: MaterializedGraphNodePlan,
    *,
    request_kind: RequestKind,
    active_work_item_family_id: str | None = None,
    active_work_item_kind: WorkItemKind | None = None,
) -> None:
    """Fail before runner invocation if a stage would receive the wrong work item."""

    if request_kind != "active_work_item":
        return
    allowed = set(stage_plan.allowed_work_item_families)
    active_family = active_work_item_family_id or (
        active_work_item_kind.value if active_work_item_kind is not None else None
    )
    if active_family in allowed:
        return
    actual = active_family or "none"
    expected = ", ".join(sorted(allowed)) if allowed else "none"
    raise StageWorkItemOwnershipError(
        f"stage kind {stage_plan.stage_kind_id} requires work item family "
        f"{expected}; active work item family is {actual}"
    )


def handle_stage_work_item_ownership_error(
    engine: RuntimeEngine,
    *,
    error: StageWorkItemOwnershipError,
) -> None:
    """Record, requeue active work, and leave a classified idle snapshot."""

    assert engine.snapshot is not None
    write_runtime_event(
        engine.paths,
        event_type="runtime_stage_work_item_ownership_invalid",
        data={
            "failure_class": _STAGE_WORK_ITEM_OWNERSHIP_INVALID,
            "active_plane": engine.snapshot.active_plane.value
            if engine.snapshot.active_plane is not None
            else None,
            "active_stage": engine.snapshot.active_stage.value
            if engine.snapshot.active_stage is not None
            else None,
            "active_stage_kind_id": engine.snapshot.active_stage_kind_id,
            "active_work_item_kind": engine.snapshot.active_work_item_kind.value
            if engine.snapshot.active_work_item_kind is not None
            else None,
            "active_work_item_family_id": engine.snapshot.active_work_item_family_id,
            "active_work_item_id": engine.snapshot.active_work_item_id,
            "error": str(error),
        },
    )
    engine._clear_stale_state(reason="stage work-item ownership guard")
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "current_failure_class": _STAGE_WORK_ITEM_OWNERSHIP_INVALID,
            "updated_at": engine._now(),
        }
    )
    save_snapshot(engine.paths, engine.snapshot)


def build_closure_target_stage_run_request(
    engine: RuntimeEngine,
    stage_plan: MaterializedGraphNodePlan,
    target_state: ClosureTargetState,
) -> StageRunRequest:
    assert engine.snapshot is not None
    active_run = active_run_for_plane(engine.snapshot, stage_plan.plane)
    run_id = active_run.run_id if active_run is not None else engine.snapshot.active_run_id or new_run_id()
    run_dir = engine.paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stage_name = _stage_name_for_node_plan(stage_plan)
    request_id = new_request_id()
    required_skill_paths = tuple(
        str(engine.paths.runtime_root / path) for path in stage_plan.required_skill_paths
    )
    attached_skill_paths = tuple(
        str(engine.paths.runtime_root / path) for path in stage_plan.attached_skill_additions
    )
    skill_revision_evidence_path = _write_skill_revision_evidence_if_enabled(
        engine,
        run_dir=run_dir,
        request_id=request_id,
        run_id=run_id,
        skill_paths=(*required_skill_paths, *attached_skill_paths),
    )
    request = StageRunRequest(
        request_id=request_id,
        run_id=run_id,
        plane=stage_plan.plane,
        stage=stage_name,
        request_kind="closure_target",
        mode_id=engine.snapshot.active_mode_id,
        compiled_plan_id=(
            active_run.compiled_plan_id if active_run is not None else engine.snapshot.compiled_plan_id
        ),
        node_id=stage_plan.node_id,
        stage_kind_id=stage_plan.stage_kind_id,
        running_status_marker=stage_plan.running_status_marker,
        legal_terminal_markers=_legal_terminal_markers_for_stage_plan(stage_plan),
        allowed_result_classes_by_outcome=stage_plan.allowed_result_classes_by_outcome,
        entrypoint_path=str(engine.paths.runtime_root / stage_plan.entrypoint_path),
        entrypoint_contract_id=stage_plan.entrypoint_contract_id,
        required_skill_paths=required_skill_paths,
        attached_skill_paths=attached_skill_paths,
        closure_target_path=str(engine.paths.arbiter_targets_dir / f"{target_state.root_spec_id}.json"),
        closure_target_root_spec_id=target_state.root_spec_id,
        closure_target_root_idea_id=target_state.root_idea_id,
        canonical_root_spec_path=target_state.root_spec_path,
        canonical_seed_idea_path=target_state.root_idea_path,
        preferred_rubric_path=target_state.rubric_path,
        preferred_verdict_path=target_state.latest_verdict_path
        or str(engine.paths.arbiter_verdicts_dir / f"{target_state.root_spec_id}.json"),
        preferred_report_path=str(run_dir / "arbiter_report.md"),
        run_dir=str(run_dir),
        summary_status_path=str(engine.paths.planning_status_file),
        runtime_snapshot_path=str(engine.paths.runtime_snapshot_file),
        recovery_counters_path=str(engine.paths.recovery_counters_file),
        skill_revision_evidence_path=str(skill_revision_evidence_path)
        if skill_revision_evidence_path is not None
        else None,
        runner_name=stage_plan.runner_name,
        model_name=stage_plan.model_name,
        thinking_level=stage_plan.thinking_level,
        model_reasoning_effort=stage_plan.model_reasoning_effort,
        timeout_seconds=stage_plan.timeout_seconds,
        execution_capability_grants=stage_plan.execution_capability_grants,
    )
    request = attach_default_request_context(
        workspace_root=engine.paths.root,
        request=request,
        compiled_plan=engine.compiled_plan,
    )
    engine.snapshot = engine.snapshot.model_copy(update={"active_run_id": request.run_id})
    save_snapshot(engine.paths, engine.snapshot)
    return request


def stage_plan_for(
    engine: RuntimeEngine,
    plane: Plane,
    stage: StageName,
    *,
    node_id: str | None = None,
) -> MaterializedGraphNodePlan:
    assert engine.compiled_plan is not None
    graph = (
        engine.compiled_plan.execution_graph
        if plane is Plane.EXECUTION
        else engine.compiled_plan.learning_graph
        if plane is Plane.LEARNING
        else engine.compiled_plan.planning_graph
    )
    if graph is None:
        raise KeyError(f"No compiled graph for plane {plane.value}")
    if node_id is not None:
        for node in graph.nodes:
            if node.plane is plane and node.node_id == node_id:
                return node
    for node in graph.nodes:
        if node.plane is plane and node.stage_kind_id == stage.value:
            return node
    for node in graph.nodes:
        if node.plane is plane and stage_for_stage_kind(plane, node.stage_kind_id) == stage:
            return node
    raise KeyError(f"No compiled graph node plan for {plane.value}:{stage.value}")


def idle_stage_for_no_work() -> StageName:
    return ExecutionStageName.UPDATER


def idle_tick_outcome(engine: RuntimeEngine, *, reason: str) -> RuntimeTickOutcome:
    assert engine.snapshot is not None
    idle_stage = idle_stage_for_no_work()
    stage_result = StageResultEnvelope(
        run_id="idle",
        plane=Plane.EXECUTION,
        stage=idle_stage,
        work_item_kind=WorkItemKind.TASK,
        work_item_id="idle",
        terminal_result=ExecutionTerminalResult.UPDATE_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker=f"### {ExecutionTerminalResult.UPDATE_COMPLETE.value}",
        success=True,
        retryable=False,
        exit_code=0,
        duration_seconds=0,
        started_at=now(),
        completed_at=now(),
    )
    return RuntimeTickOutcome(
        stage=idle_stage,
        stage_result=stage_result,
        stage_result_path=engine.paths.logs_dir / "idle-stage-result.json",
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason=reason,
        ),
        snapshot=engine.snapshot,
    )


def active_work_item_path(
    engine: RuntimeEngine,
    work_item_kind: WorkItemKind | None,
    work_item_id: str | None,
    *,
    work_item_family_id: str | None = None,
) -> Path | None:
    if work_item_id is None:
        return None
    if work_item_family_id is not None and engine.compiled_plan is not None:
        family = engine.compiled_plan.work_item_families_by_id.get(work_item_family_id)
        if family is not None:
            return engine.paths.runtime_root / family.queue_dirs.active / f"{work_item_id}{family.file_extension}"
    if work_item_kind is None:
        return None
    if work_item_kind is WorkItemKind.TASK:
        return engine.paths.tasks_active_dir / f"{work_item_id}.md"
    if work_item_kind is WorkItemKind.PROBE:
        return engine.paths.probes_active_dir / f"{work_item_id}.md"
    if work_item_kind is WorkItemKind.SPEC:
        return engine.paths.specs_active_dir / f"{work_item_id}.md"
    if work_item_kind is WorkItemKind.LEARNING_REQUEST:
        return engine.paths.learning_requests_active_dir / f"{work_item_id}.md"
    if work_item_kind is WorkItemKind.BLUEPRINT_DRAFT:
        return engine.paths.runtime_root / "blueprints" / "drafts" / "active" / f"{work_item_id}.json"
    return engine.paths.incidents_active_dir / f"{work_item_id}.md"


def execution_queue_depth(engine: RuntimeEngine) -> int:
    return queue_depths_by_plane(
        engine.paths,
        compiled_plan=getattr(engine, "compiled_plan", None),
    )[Plane.EXECUTION]


def planning_queue_depth(engine: RuntimeEngine) -> int:
    return queue_depths_by_plane(
        engine.paths,
        compiled_plan=getattr(engine, "compiled_plan", None),
    )[Plane.PLANNING]


def learning_queue_depth(engine: RuntimeEngine) -> int:
    return queue_depths_by_plane(
        engine.paths,
        compiled_plan=getattr(engine, "compiled_plan", None),
    )[Plane.LEARNING]


def runner_failure_result(
    request: StageRunRequest,
    *,
    failure_class: str,
    error: str,
) -> RunnerRawResult:
    del error
    current_time = now()
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "runtime",
        model_name=request.model_name,
        thinking_level=request.thinking_level,
        model_reasoning_effort=request.model_reasoning_effort,
        exit_kind="runner_error",
        exit_code=1,
        stdout_path=None,
        stderr_path=None,
        terminal_result_path=None,
        failure_class=failure_class,
        started_at=current_time,
        ended_at=current_time,
    )


def new_run_id() -> str:
    return f"run-{uuid4().hex}"


def new_request_id() -> str:
    return f"request-{uuid4().hex}"


def now() -> datetime:
    return datetime.now(timezone.utc)


def _stage_name_for_node_plan(stage_plan: MaterializedGraphNodePlan) -> StageName:
    return stage_for_stage_kind(stage_plan.plane, stage_plan.stage_kind_id)


def _status_file_for_plane(engine: RuntimeEngine, plane: Plane) -> Path:
    if plane is Plane.EXECUTION:
        return engine.paths.execution_status_file
    if plane is Plane.LEARNING:
        return engine.paths.learning_status_file
    return engine.paths.planning_status_file


def _legal_terminal_markers_for_stage_plan(
    stage_plan: MaterializedGraphNodePlan,
) -> tuple[str, ...]:
    return tuple(
        f"### {outcome}" for outcome in stage_plan.allowed_result_classes_by_outcome
    )


def _write_skill_revision_evidence_if_enabled(
    engine: RuntimeEngine,
    *,
    run_dir: Path,
    request_id: str,
    run_id: str,
    skill_paths: tuple[str, ...],
) -> Path | None:
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None
    if engine.compiled_plan.learning_graph is None:
        return None
    return write_skill_revision_evidence(
        run_dir=run_dir,
        request_id=request_id,
        run_id=run_id,
        mode_id=engine.snapshot.active_mode_id,
        compiled_plan_id=engine.snapshot.compiled_plan_id,
        skill_paths=skill_paths,
    )


def _request_kind_for_active_family(work_item_family_id: str | None) -> RequestKind:
    if work_item_family_id == WorkItemKind.LEARNING_REQUEST.value:
        return "learning_request"
    return "active_work_item"


__all__ = [
    "active_work_item_path",
    "build_closure_target_stage_run_request",
    "build_stage_run_request",
    "execution_queue_depth",
    "handle_stage_work_item_ownership_error",
    "idle_stage_for_no_work",
    "idle_tick_outcome",
    "learning_queue_depth",
    "new_request_id",
    "new_run_id",
    "now",
    "planning_queue_depth",
    "runner_failure_result",
    "stage_plan_for",
    "validate_stage_work_item_ownership",
]
