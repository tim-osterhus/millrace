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
    LearningStageName,
    Plane,
    PlanningStageName,
    ResultClass,
    StageName,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runners import RunnerRawResult, StageRunRequest
from millrace_ai.runners.requests import RequestKind
from millrace_ai.state_store import save_snapshot

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine, RuntimeTickOutcome

from .error_recovery import build_runtime_error_request_fields
from .skill_evidence import write_skill_revision_evidence

_STATUS_IDLE = "### IDLE"


def build_stage_run_request(
    engine: RuntimeEngine,
    stage_plan: MaterializedGraphNodePlan,
) -> StageRunRequest:
    assert engine.snapshot is not None
    active_path = active_work_item_path(
        engine,
        engine.snapshot.active_work_item_kind,
        engine.snapshot.active_work_item_id,
    )
    run_id = engine.snapshot.active_run_id or new_run_id()
    run_dir = engine.paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    runtime_error_fields = build_runtime_error_request_fields(engine)
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
        compiled_plan_id=engine.snapshot.compiled_plan_id,
        node_id=stage_plan.node_id,
        stage_kind_id=stage_plan.stage_kind_id,
        running_status_marker=stage_plan.running_status_marker,
        legal_terminal_markers=_legal_terminal_markers_for_stage_plan(stage_plan),
        allowed_result_classes_by_outcome=stage_plan.allowed_result_classes_by_outcome,
        request_kind=_request_kind_for_active_kind(engine.snapshot.active_work_item_kind),
        entrypoint_path=str(engine.paths.runtime_root / stage_plan.entrypoint_path),
        entrypoint_contract_id=stage_plan.entrypoint_contract_id,
        required_skill_paths=required_skill_paths,
        attached_skill_paths=attached_skill_paths,
        active_work_item_kind=engine.snapshot.active_work_item_kind,
        active_work_item_id=engine.snapshot.active_work_item_id,
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
        timeout_seconds=stage_plan.timeout_seconds,
    )
    engine.snapshot = engine.snapshot.model_copy(update={"active_run_id": request.run_id})
    save_snapshot(engine.paths, engine.snapshot)
    return request


def build_closure_target_stage_run_request(
    engine: RuntimeEngine,
    stage_plan: MaterializedGraphNodePlan,
    target_state: ClosureTargetState,
) -> StageRunRequest:
    assert engine.snapshot is not None
    run_id = engine.snapshot.active_run_id or new_run_id()
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
        compiled_plan_id=engine.snapshot.compiled_plan_id,
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
        timeout_seconds=stage_plan.timeout_seconds,
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
    raise KeyError(f"No compiled graph node plan for {plane.value}:{stage.value}")


def idle_stage_for_no_work() -> StageName:
    return ExecutionStageName.UPDATER


def idle_tick_outcome(engine: RuntimeEngine, *, reason: str) -> RuntimeTickOutcome:
    from millrace_ai.runtime.engine import RuntimeTickOutcome

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
) -> Path | None:
    if work_item_kind is None or work_item_id is None:
        return None
    if work_item_kind is WorkItemKind.TASK:
        return engine.paths.tasks_active_dir / f"{work_item_id}.md"
    if work_item_kind is WorkItemKind.SPEC:
        return engine.paths.specs_active_dir / f"{work_item_id}.md"
    if work_item_kind is WorkItemKind.LEARNING_REQUEST:
        return engine.paths.learning_requests_active_dir / f"{work_item_id}.md"
    return engine.paths.incidents_active_dir / f"{work_item_id}.md"


def execution_queue_depth(engine: RuntimeEngine) -> int:
    return len(list(engine.paths.tasks_queue_dir.glob("*.md")))


def planning_queue_depth(engine: RuntimeEngine) -> int:
    spec_depth = len(list(engine.paths.specs_queue_dir.glob("*.md")))
    incident_depth = len(list(engine.paths.incidents_incoming_dir.glob("*.md")))
    return spec_depth + incident_depth


def learning_queue_depth(engine: RuntimeEngine) -> int:
    return len(list(engine.paths.learning_requests_queue_dir.glob("*.md")))


def runner_failure_result(
    request: StageRunRequest,
    *,
    failure_class: str,
    error: str,
) -> RunnerRawResult:
    del failure_class, error
    current_time = now()
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "runtime",
        model_name=request.model_name,
        exit_kind="runner_error",
        exit_code=1,
        stdout_path=None,
        stderr_path=None,
        terminal_result_path=None,
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
    if stage_plan.plane is Plane.EXECUTION:
        return ExecutionStageName(stage_plan.stage_kind_id)
    if stage_plan.plane is Plane.LEARNING:
        return LearningStageName(stage_plan.stage_kind_id)
    return PlanningStageName(stage_plan.stage_kind_id)


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


def _request_kind_for_active_kind(work_item_kind: WorkItemKind | None) -> RequestKind:
    if work_item_kind is WorkItemKind.LEARNING_REQUEST:
        return "learning_request"
    return "active_work_item"


__all__ = [
    "active_work_item_path",
    "build_closure_target_stage_run_request",
    "build_stage_run_request",
    "execution_queue_depth",
    "idle_stage_for_no_work",
    "idle_tick_outcome",
    "learning_queue_depth",
    "new_request_id",
    "new_run_id",
    "now",
    "planning_queue_depth",
    "runner_failure_result",
    "stage_plan_for",
]
