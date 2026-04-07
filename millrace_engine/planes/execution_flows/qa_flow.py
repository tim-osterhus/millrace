"""QA-outcome flow family for the execution plane."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ...contracts import ExecutionStatus, StageResult, TaskCard

ExecutionOutcome = tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]


class RecoveryResultLike(Protocol):
    action: str
    diagnostics_dir: Path
    quarantined_task: TaskCard | None


class QAFlowPlane(Protocol):
    def _record_stage_transition(self, result: StageResult, **kwargs: object) -> None: ...

    def _complete_success_path(
        self,
        task: TaskCard,
        run_id: str,
        stage_results: list[StageResult],
    ) -> TaskCard: ...

    def _mark_quickfix_artifact_active(self) -> None: ...

    def _run_quickfix_loop(
        self,
        task: TaskCard,
        run_id: str,
        stage_results: list[StageResult],
        *,
        recovery_rounds: int,
    ) -> ExecutionOutcome: ...

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


def handle_qa_outcome(
    plane: QAFlowPlane,
    task: TaskCard,
    *,
    run_id: str,
    stage_results: list[StageResult],
    qa_result: StageResult,
    stage_label: str,
    recovery_rounds: int,
    routing_mode: str,
) -> ExecutionOutcome:
    """Route the QA stage outcome into success, quickfix, or escalation."""

    qa_status = ExecutionStatus(qa_result.status)
    plane._record_stage_transition(
        qa_result,
        task_before=task,
        task_after=(None if qa_status is ExecutionStatus.QA_COMPLETE else task),
        routing_mode=routing_mode,
        selected_edge_id=(
            "execution.qa.success.update"
            if qa_status is ExecutionStatus.QA_COMPLETE
            else (
                "execution.qa.quickfix.hotfix"
                if qa_status is ExecutionStatus.QUICKFIX_NEEDED
                else "execution.qa.failure.escalate"
            )
        ),
        selected_edge_reason=(
            "qa passed, so update can finalize the task"
            if qa_status is ExecutionStatus.QA_COMPLETE
            else (
                "qa requested a quickfix loop"
                if qa_status is ExecutionStatus.QUICKFIX_NEEDED
                else f"qa ended with {qa_status.value}, so escalation is required"
            )
        ),
        condition_inputs={"status": qa_status.value},
        condition_result=qa_status is ExecutionStatus.QA_COMPLETE,
    )
    if qa_status is ExecutionStatus.QA_COMPLETE:
        archived = plane._complete_success_path(task, run_id, stage_results)
        return ExecutionStatus.IDLE, archived, None, None, 0
    if qa_status is ExecutionStatus.QUICKFIX_NEEDED:
        plane._mark_quickfix_artifact_active()
        return plane._run_quickfix_loop(
            task,
            run_id,
            stage_results,
            recovery_rounds=recovery_rounds,
        )

    recovery = plane._recover_or_quarantine(
        task,
        run_id=run_id,
        stage_label=stage_label,
        why=f"exit={qa_result.exit_code} status={qa_status.value}",
        stage_results=stage_results,
        failing_result=qa_result,
    )
    if recovery.action == "quarantine":
        return ExecutionStatus.IDLE, None, recovery.quarantined_task, recovery.diagnostics_dir, 0
    return plane._resume_after_recovery(
        task,
        run_id=run_id,
        stage_results=stage_results,
        recovery_rounds=recovery_rounds + 1,
        diagnostics_dir=recovery.diagnostics_dir,
    )


__all__ = ["handle_qa_outcome"]
