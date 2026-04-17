"""Stage-request construction and runtime clock/id helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    FrozenStagePlan,
    Plane,
    ResultClass,
    StageName,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runners import RunnerRawResult, StageRunRequest
from millrace_ai.state_store import save_snapshot

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine, RuntimeTickOutcome

from .error_recovery import build_runtime_error_request_fields

_STATUS_IDLE = "### IDLE"


def build_stage_run_request(engine: RuntimeEngine, stage_plan: FrozenStagePlan) -> StageRunRequest:
    assert engine.snapshot is not None
    active_path = active_work_item_path(
        engine,
        engine.snapshot.active_work_item_kind,
        engine.snapshot.active_work_item_id,
    )
    run_id = engine.snapshot.active_run_id or new_run_id()
    run_dir = engine.paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    request = StageRunRequest(
        request_id=new_request_id(),
        run_id=run_id,
        plane=stage_plan.plane,
        stage=stage_plan.stage,
        mode_id=engine.snapshot.active_mode_id,
        compiled_plan_id=engine.snapshot.compiled_plan_id,
        entrypoint_path=str(engine.paths.runtime_root / stage_plan.entrypoint_path),
        entrypoint_contract_id=stage_plan.entrypoint_contract_id,
        required_skill_paths=tuple(
            str(engine.paths.runtime_root / path) for path in stage_plan.required_skills
        ),
        attached_skill_paths=tuple(
            str(engine.paths.runtime_root / path) for path in stage_plan.attached_skill_additions
        ),
        active_work_item_kind=engine.snapshot.active_work_item_kind,
        active_work_item_id=engine.snapshot.active_work_item_id,
        active_work_item_path=str(active_path) if active_path is not None else None,
        run_dir=str(run_dir),
        summary_status_path=str(
            engine.paths.execution_status_file
            if stage_plan.plane is Plane.EXECUTION
            else engine.paths.planning_status_file
        ),
        runtime_snapshot_path=str(engine.paths.runtime_snapshot_file),
        recovery_counters_path=str(engine.paths.recovery_counters_file),
        preferred_troubleshoot_report_path=str(run_dir / "troubleshoot_report.md"),
        **build_runtime_error_request_fields(engine),
        runner_name=stage_plan.runner_name,
        model_name=stage_plan.model_name,
        timeout_seconds=stage_plan.timeout_seconds,
    )
    engine.snapshot = engine.snapshot.model_copy(update={"active_run_id": request.run_id})
    save_snapshot(engine.paths, engine.snapshot)
    return request


def stage_plan_for(engine: RuntimeEngine, plane: Plane, stage: StageName) -> FrozenStagePlan:
    assert engine.compiled_plan is not None
    for stage_plan in engine.compiled_plan.stage_plans:
        if stage_plan.plane is plane and stage_plan.stage is stage:
            return stage_plan
    raise KeyError(f"No compiled stage plan for {plane.value}:{stage.value}")


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
    return engine.paths.incidents_active_dir / f"{work_item_id}.md"


def execution_queue_depth(engine: RuntimeEngine) -> int:
    return len(list(engine.paths.tasks_queue_dir.glob("*.md")))


def planning_queue_depth(engine: RuntimeEngine) -> int:
    spec_depth = len(list(engine.paths.specs_queue_dir.glob("*.md")))
    incident_depth = len(list(engine.paths.incidents_incoming_dir.glob("*.md")))
    return spec_depth + incident_depth


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


__all__ = [
    "active_work_item_path",
    "build_stage_run_request",
    "execution_queue_depth",
    "idle_stage_for_no_work",
    "idle_tick_outcome",
    "new_request_id",
    "new_run_id",
    "now",
    "planning_queue_depth",
    "runner_failure_result",
    "stage_plan_for",
]
