"""Cycle orchestration flow family for the execution plane."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ...compiler import CompileStatus
from ...contracts import ExecutionStatus, TaskCard
from ...events import EventType
from ...policies import (
    execution_integration_context_from_records,
    execution_usage_budget_context_from_records,
)
from ...queue import load_research_recovery_latch
from ...stages.base import StageExecutionError
from ...standard_runtime import compile_execution_runtime_selection
from ..execution_routing import (
    execution_plan as execution_plan_helper,
)
from ..execution_routing import (
    resume_from_completed_status as resume_from_completed_status_helper,
)
from ..execution_routing import (
    run_frozen_plan as run_frozen_plan_helper,
)
from ..execution_runtime import start_transition_history as start_transition_history_helper

if TYPE_CHECKING:
    from ..execution import ExecutionCycleResult


class CycleRunnerPlane(Protocol):
    transition_history: object | None
    _active_frozen_plan: object | None
    _runtime_parameter_binder: object | None
    _resolved_frozen_stages: dict[str, object]
    runtime_provenance: object
    _policy_routing_mode: str | None
    _cycle_integration_context: object | None
    _last_research_handoff: object | None
    _quickfix_artifact_active_for_cycle: bool
    policy_evaluations: list[object]

    config: object
    paths: object
    queue: object
    status_store: object

    def reconfigure(self, config, paths) -> None: ...

    def _refresh_size_status(self, task: TaskCard | None): ...

    def _emit_event(self, event_type: EventType, payload: dict[str, object] | None = None) -> None: ...

    def _new_run_id(self, task: TaskCard | None, label: str) -> str: ...

    def _run_empty_backlog_sequence(self, run_id: str, stage_results: list) -> ExecutionStatus: ...

    def _maybe_adaptive_upscope_small_task(
        self,
        *,
        active_task: TaskCard,
        current_status: ExecutionStatus,
        size_view,
    ): ...

    def _quarantine_task(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_label: str,
        why: str,
        diagnostics_dir: Path,
        consult_result,
    ) -> TaskCard: ...

    def _initialize_parameter_binder(self) -> None: ...

    def _evaluate_cycle_boundary_policy(
        self,
        *,
        run_id: str,
        current_status: ExecutionStatus,
        active_task: TaskCard | None,
    ) -> tuple: ...

    def _apply_inter_task_delay(self, stage_results: list) -> int: ...


def _cycle_result(**kwargs: object) -> ExecutionCycleResult:
    from ..execution import ExecutionCycleResult

    return ExecutionCycleResult(**kwargs)


def run_execution_cycle(plane: CycleRunnerPlane) -> ExecutionCycleResult:
    """Run one execution-plane cycle in `--once` mode."""

    plane.reconfigure(plane.config, plane.paths)
    current_status = plane.status_store.read()
    if not isinstance(current_status, ExecutionStatus):
        raise StageExecutionError("execution plane requires execution status markers")

    stage_results: list = []
    promoted_task: TaskCard | None = None
    archived_task: TaskCard | None = None
    quarantined_task: TaskCard | None = None
    diagnostics_dir: Path | None = None
    plane.transition_history = None
    plane._active_frozen_plan = None
    plane._runtime_parameter_binder = None
    plane._resolved_frozen_stages = {}
    plane.runtime_provenance = plane.runtime_provenance.__class__()
    plane._policy_routing_mode = None
    plane._cycle_integration_context = None
    plane._last_research_handoff = None
    plane._quickfix_artifact_active_for_cycle = current_status is ExecutionStatus.QUICKFIX_NEEDED
    plane.policy_evaluations = []

    active_task = plane.queue.active_task()
    if current_status is ExecutionStatus.IDLE and active_task is None:
        if plane.queue.backlog_empty():
            plane._refresh_size_status(None)
            plane._emit_event(
                EventType.BACKLOG_EMPTY,
                {
                    "backlog_depth": 0,
                    "run_update_on_empty": plane.config.execution.run_update_on_empty,
                },
            )
            if plane.config.execution.run_update_on_empty:
                run_id = plane._new_run_id(None, "update-empty")
                history = start_transition_history_helper(plane, run_id)
                final_status = plane._run_empty_backlog_sequence(run_id, stage_results)
                return _cycle_result(
                    run_id=run_id,
                    final_status=final_status,
                    stage_results=stage_results,
                    update_only=True,
                    transition_history_path=history.history_path,
                    research_handoff=plane._last_research_handoff,
                )
            return _cycle_result(run_id=None, final_status=ExecutionStatus.IDLE)

        promoted_task = plane.queue.promote_next()
        if promoted_task is not None:
            plane._emit_event(
                EventType.TASK_PROMOTED,
                {
                    "task_id": promoted_task.task_id,
                    "title": promoted_task.title,
                },
            )
        active_task = promoted_task

    size_view = plane._refresh_size_status(active_task)

    if current_status is ExecutionStatus.NEEDS_RESEARCH and active_task is None:
        latch = load_research_recovery_latch(plane.paths.research_recovery_latch_file)
        plane.status_store.transition(ExecutionStatus.IDLE)
        return _cycle_result(
            run_id=None,
            final_status=ExecutionStatus.IDLE,
            diagnostics_dir=latch.diag_dir if latch is not None else None,
            research_handoff=plane._last_research_handoff,
        )

    if active_task is None:
        raise StageExecutionError("execution plane cannot continue without an active task")

    active_task, size_view = plane._maybe_adaptive_upscope_small_task(
        active_task=active_task,
        current_status=current_status,
        size_view=size_view,
    )

    if current_status is ExecutionStatus.NEEDS_RESEARCH:
        quarantined_task = plane._quarantine_task(
            active_task,
            run_id=plane._new_run_id(active_task, "resume-needs-research"),
            stage_label="Resume",
            why="status marker NEEDS_RESEARCH at loop start",
            diagnostics_dir=plane.paths.diagnostics_dir,
            consult_result=None,
        )
        return _cycle_result(
            run_id=None,
            final_status=ExecutionStatus.IDLE,
            promoted_task=promoted_task,
            quarantined_task=quarantined_task,
            diagnostics_dir=plane.paths.diagnostics_dir,
            research_handoff=plane._last_research_handoff,
        )

    run_id = plane._new_run_id(active_task, "task")
    compile_result = compile_execution_runtime_selection(
        plane.config,
        plane.paths,
        run_id=run_id,
        size_latch=size_view.latched_as.value,
        current_status=current_status,
        task_complexity=active_task.complexity,
        resolve_assets=True,
    )
    if compile_result.status is not CompileStatus.OK or compile_result.plan is None or compile_result.snapshot is None:
        diagnostics = "; ".join(diagnostic.message for diagnostic in compile_result.diagnostics) or (
            "standard frozen-plan compile failed without diagnostics"
        )
        raise StageExecutionError(f"execution-plane frozen plan compile failed: {diagnostics}")
    plane._active_frozen_plan = compile_result.plan
    plane._initialize_parameter_binder()
    plane.runtime_provenance = compile_result.snapshot.runtime_provenance_context()
    history = start_transition_history_helper(plane, run_id)

    if current_status in {ExecutionStatus.IDLE, ExecutionStatus.BLOCKED, ExecutionStatus.NET_WAIT}:
        plane._policy_routing_mode = "frozen_plan"
        cycle_records = plane._evaluate_cycle_boundary_policy(
            run_id=run_id,
            current_status=current_status,
            active_task=active_task,
        )
        plane._cycle_integration_context = execution_integration_context_from_records(cycle_records)
        usage_context = execution_usage_budget_context_from_records(cycle_records)
        if usage_context is not None and usage_context.pause_requested:
            return _cycle_result(
                run_id=run_id,
                final_status=current_status,
                stage_results=stage_results,
                promoted_task=promoted_task,
                transition_history_path=history.history_path,
                pause_requested=True,
                pause_reason=usage_context.reason,
            )
        final_status, archived_task, quarantined_task, diagnostics_dir, quickfix_attempts = run_frozen_plan_helper(
            plane,
            active_task,
            run_id=run_id,
            stage_results=stage_results,
            start_node_id=execution_plan_helper(plane).entry_node_id,
            transition_reason_prefix="frozen execution plan",
            routing_mode="frozen_plan",
        )
        pacing_delay_seconds = 0
        if archived_task is not None:
            pacing_delay_seconds = plane._apply_inter_task_delay(stage_results)
        return _cycle_result(
            run_id=run_id,
            final_status=final_status,
            stage_results=stage_results,
            promoted_task=promoted_task,
            archived_task=archived_task,
            quarantined_task=quarantined_task,
            diagnostics_dir=diagnostics_dir,
            quickfix_attempts=quickfix_attempts,
            transition_history_path=history.history_path,
            research_handoff=plane._last_research_handoff,
            pacing_delay_seconds=pacing_delay_seconds,
        )

    if current_status in {
        ExecutionStatus.BUILDER_COMPLETE,
        ExecutionStatus.INTEGRATION_COMPLETE,
        ExecutionStatus.QA_COMPLETE,
        ExecutionStatus.QUICKFIX_NEEDED,
        ExecutionStatus.TROUBLESHOOT_COMPLETE,
        ExecutionStatus.CONSULT_COMPLETE,
        ExecutionStatus.UPDATE_COMPLETE,
        ExecutionStatus.LARGE_PLAN_COMPLETE,
        ExecutionStatus.LARGE_EXECUTE_COMPLETE,
        ExecutionStatus.LARGE_REASSESS_COMPLETE,
        ExecutionStatus.LARGE_REFACTOR_COMPLETE,
    }:
        plane._policy_routing_mode = "frozen_plan_legacy_resume"
        cycle_records = plane._evaluate_cycle_boundary_policy(
            run_id=run_id,
            current_status=current_status,
            active_task=active_task,
        )
        plane._cycle_integration_context = execution_integration_context_from_records(cycle_records)
        usage_context = execution_usage_budget_context_from_records(cycle_records)
        if usage_context is not None and usage_context.pause_requested:
            return _cycle_result(
                run_id=run_id,
                final_status=current_status,
                promoted_task=promoted_task,
                stage_results=stage_results,
                transition_history_path=history.history_path,
                pause_requested=True,
                pause_reason=usage_context.reason,
            )
        final_status, archived_task, quarantined_task, diagnostics_dir, quickfix_attempts = (
            resume_from_completed_status_helper(
                plane,
                active_task,
                run_id=run_id,
                stage_results=stage_results,
                status=current_status,
                routing_mode_frozen_plan_legacy_resume="frozen_plan_legacy_resume",
            )
        )
        pacing_delay_seconds = 0
        if archived_task is not None:
            pacing_delay_seconds = plane._apply_inter_task_delay(stage_results)
        return _cycle_result(
            run_id=run_id,
            final_status=final_status,
            promoted_task=promoted_task,
            stage_results=stage_results,
            archived_task=archived_task,
            quarantined_task=quarantined_task,
            diagnostics_dir=diagnostics_dir,
            quickfix_attempts=quickfix_attempts,
            transition_history_path=history.history_path,
            research_handoff=plane._last_research_handoff,
            pacing_delay_seconds=pacing_delay_seconds,
        )

    raise StageExecutionError(f"execution plane does not support resume from {current_status.value}")


__all__ = ["run_execution_cycle"]
