"""Quickfix-loop flow family for the execution plane."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol

from ...config import EngineConfig
from ...contracts import ExecutionStatus, StageResult, StageType, TaskCard
from ...events import EventType

ExecutionOutcome = tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]


class RecoveryResultLike(Protocol):
    action: Literal["resume", "quarantine"]
    diagnostics_dir: Path
    quarantined_task: TaskCard | None


class QuickfixFlowPlane(Protocol):
    config: EngineConfig

    def _emit_event(self, event_type: EventType, payload: dict[str, Any] | None = None) -> None: ...

    def _mark_quickfix_artifact_active(self) -> None: ...

    def _run_stage(
        self,
        stage_type: StageType,
        task: TaskCard | None,
        run_id: str,
        *,
        node_id: str | None = None,
    ) -> StageResult: ...

    def _record_stage_transition(self, result: StageResult, **kwargs: object) -> None: ...

    def _recover_or_quarantine(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_label: str,
        why: str,
        stage_results: list[StageResult],
        failing_result: StageResult | None,
    ) -> RecoveryResultLike: ...

    def _resume_after_recovery(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_results: list[StageResult],
        recovery_rounds: int,
        diagnostics_dir: Path | None = None,
    ) -> ExecutionOutcome: ...

    def _complete_success_path(
        self,
        task: TaskCard,
        run_id: str,
        stage_results: list[StageResult],
    ) -> TaskCard: ...


def run_quickfix_loop(
    plane: QuickfixFlowPlane,
    task: TaskCard,
    run_id: str,
    stage_results: list[StageResult],
    *,
    recovery_rounds: int,
    routing_mode: str,
) -> ExecutionOutcome:
    """Run the hotfix/doublecheck retry family until success or escalation."""

    max_attempts = plane.config.execution.quickfix_max_attempts
    last_result: StageResult | None = stage_results[-1] if stage_results else None
    quickfix_stage = plane.config.routing.quickfix_stage
    verification_stage = plane.config.routing.quickfix_verification_stage
    plane._mark_quickfix_artifact_active()

    for attempt in range(1, max_attempts + 1):
        plane._emit_event(
            EventType.QUICKFIX_ATTEMPT,
            {
                "attempt": attempt,
                "max_attempts": max_attempts,
                "run_id": run_id,
                "task_id": task.task_id,
                "title": task.title,
            },
        )
        hotfix_result = plane._run_stage(quickfix_stage, task, run_id)
        stage_results.append(hotfix_result)
        last_result = hotfix_result
        hotfix_status = ExecutionStatus(hotfix_result.status)
        plane._record_stage_transition(
            hotfix_result,
            task_before=task,
            task_after=task,
            routing_mode=routing_mode,
            selected_edge_id=(
                "execution.hotfix.success.doublecheck"
                if hotfix_status is ExecutionStatus.BUILDER_COMPLETE
                else "execution.hotfix.failure.escalate"
            ),
            selected_edge_reason=(
                "hotfix completed, so doublecheck can verify the patch"
                if hotfix_status is ExecutionStatus.BUILDER_COMPLETE
                else f"hotfix ended with {hotfix_status.value}, so escalation is required"
            ),
            condition_inputs={"status": hotfix_status.value, "attempt": attempt},
            condition_result=hotfix_status is ExecutionStatus.BUILDER_COMPLETE,
        )
        if hotfix_status is not ExecutionStatus.BUILDER_COMPLETE:
            recovery = plane._recover_or_quarantine(
                task,
                run_id=run_id,
                stage_label=quickfix_stage.value.title(),
                why=f"attempt={attempt} exit={hotfix_result.exit_code} status={hotfix_status.value}",
                stage_results=stage_results,
                failing_result=hotfix_result,
            )
            if recovery.action == "quarantine":
                return ExecutionStatus.IDLE, None, recovery.quarantined_task, recovery.diagnostics_dir, attempt
            return plane._resume_after_recovery(
                task,
                run_id=run_id,
                stage_results=stage_results,
                recovery_rounds=recovery_rounds + 1,
                diagnostics_dir=recovery.diagnostics_dir,
            )

        doublecheck_result = plane._run_stage(verification_stage, task, run_id)
        stage_results.append(doublecheck_result)
        last_result = doublecheck_result
        doublecheck_status = ExecutionStatus(doublecheck_result.status)
        plane._record_stage_transition(
            doublecheck_result,
            task_before=task,
            task_after=(None if doublecheck_status is ExecutionStatus.QA_COMPLETE else task),
            routing_mode=routing_mode,
            selected_edge_id=(
                "execution.doublecheck.success.update"
                if doublecheck_status is ExecutionStatus.QA_COMPLETE
                else (
                    "execution.doublecheck.quickfix.retry"
                    if doublecheck_status is ExecutionStatus.QUICKFIX_NEEDED
                    else "execution.doublecheck.failure.escalate"
                )
            ),
            selected_edge_reason=(
                "doublecheck passed, so the task can finish through update"
                if doublecheck_status is ExecutionStatus.QA_COMPLETE
                else (
                    "doublecheck still needs a quickfix attempt"
                    if doublecheck_status is ExecutionStatus.QUICKFIX_NEEDED
                    else f"doublecheck ended with {doublecheck_status.value}, so escalation is required"
                )
            ),
            condition_inputs={"status": doublecheck_status.value, "attempt": attempt},
            condition_result=doublecheck_status is ExecutionStatus.QA_COMPLETE,
        )
        if doublecheck_status is ExecutionStatus.QA_COMPLETE:
            archived = plane._complete_success_path(task, run_id, stage_results)
            return ExecutionStatus.IDLE, archived, None, None, attempt
        if doublecheck_status is ExecutionStatus.QUICKFIX_NEEDED:
            continue

        recovery = plane._recover_or_quarantine(
            task,
            run_id=run_id,
            stage_label=verification_stage.value.title(),
            why=f"attempt={attempt} exit={doublecheck_result.exit_code} status={doublecheck_status.value}",
            stage_results=stage_results,
            failing_result=doublecheck_result,
        )
        if recovery.action == "quarantine":
            return ExecutionStatus.IDLE, None, recovery.quarantined_task, recovery.diagnostics_dir, attempt
        return plane._resume_after_recovery(
            task,
            run_id=run_id,
            stage_results=stage_results,
            recovery_rounds=recovery_rounds + 1,
            diagnostics_dir=recovery.diagnostics_dir,
        )

    plane._emit_event(
        EventType.QUICKFIX_EXHAUSTED,
        {
            "attempts": max_attempts,
            "max_attempts": max_attempts,
            "run_id": run_id,
            "task_id": task.task_id,
            "title": task.title,
        },
    )
    recovery = plane._recover_or_quarantine(
        task,
        run_id=run_id,
        stage_label="Quickfix",
        why="Quickfix attempts exhausted (still QUICKFIX_NEEDED)",
        stage_results=stage_results,
        failing_result=last_result,
    )
    if recovery.action == "quarantine":
        return ExecutionStatus.IDLE, None, recovery.quarantined_task, recovery.diagnostics_dir, max_attempts
    return plane._resume_after_recovery(
        task,
        run_id=run_id,
        stage_results=stage_results,
        recovery_rounds=recovery_rounds + 1,
        diagnostics_dir=recovery.diagnostics_dir,
    )


__all__ = ["run_quickfix_loop"]
