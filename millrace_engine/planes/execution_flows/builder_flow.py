"""Builder-success flow family for the execution plane."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ...contracts import ExecutionStatus, StageResult, StageType, TaskCard
from ...stages.base import StageExecutionError

ExecutionOutcome = tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]


class RecoveryResultLike(Protocol):
    action: str
    diagnostics_dir: Path
    quarantined_task: TaskCard | None


class BuilderFlowPlane(Protocol):
    def _selected_builder_sequence(self, task: TaskCard | None = None) -> tuple[StageType, ...]: ...

    def _integration_context(self, task: TaskCard | None = None): ...

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

    def _handle_qa_outcome(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_results: list[StageResult],
        qa_result: StageResult,
        stage_label: str,
        recovery_rounds: int,
    ) -> ExecutionOutcome: ...


def run_builder_success_sequence(
    plane: BuilderFlowPlane,
    task: TaskCard,
    run_id: str,
    stage_results: list[StageResult],
    *,
    recovery_rounds: int,
    routing_mode: str,
) -> ExecutionOutcome:
    """Run the post-builder sequence until QA finishes or recovery is required."""

    for stage_type in plane._selected_builder_sequence(task):
        if stage_type is StageType.INTEGRATION:
            integration_result = plane._run_stage(stage_type, task, run_id)
            stage_results.append(integration_result)
            integration_status = ExecutionStatus(integration_result.status)
            plane._record_stage_transition(
                integration_result,
                task_before=task,
                task_after=task,
                routing_mode=routing_mode,
                selected_edge_id=(
                    "execution.integration.success.qa"
                    if integration_status is ExecutionStatus.INTEGRATION_COMPLETE
                    else "execution.integration.failure.escalate"
                ),
                selected_edge_reason=(
                    "integration completed, so qa can continue"
                    if integration_status is ExecutionStatus.INTEGRATION_COMPLETE
                    else f"integration ended with {integration_status.value}, so escalation is required"
                ),
                condition_inputs={"status": integration_status.value},
                condition_result=integration_status is ExecutionStatus.INTEGRATION_COMPLETE,
            )
            if integration_status is not ExecutionStatus.INTEGRATION_COMPLETE:
                recovery = plane._recover_or_quarantine(
                    task,
                    run_id=run_id,
                    stage_label=stage_type.value.title(),
                    why=f"exit={integration_result.exit_code} status={integration_status.value}",
                    stage_results=stage_results,
                    failing_result=integration_result,
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
            continue

        if stage_type is StageType.QA:
            qa_result = plane._run_stage(stage_type, task, run_id)
            stage_results.append(qa_result)
            return plane._handle_qa_outcome(
                task,
                run_id=run_id,
                stage_results=stage_results,
                qa_result=qa_result,
                stage_label=stage_type.value.upper(),
                recovery_rounds=recovery_rounds,
            )

        raise StageExecutionError(
            f"unsupported builder success stage in routing config: {stage_type.value}"
        )

    raise StageExecutionError("builder success sequence must include qa")


def run_full_task_path(
    plane: BuilderFlowPlane,
    task: TaskCard,
    run_id: str,
    stage_results: list[StageResult],
    *,
    recovery_rounds: int,
    routing_mode: str,
) -> ExecutionOutcome:
    """Run builder then delegate the successful path into the builder-success family."""

    builder_result = plane._run_stage(StageType.BUILDER, task, run_id)
    stage_results.append(builder_result)
    builder_status = ExecutionStatus(builder_result.status)
    next_edge = "execution.builder.failure.escalate"
    next_reason = f"builder ended with {builder_status.value}, so escalation is required"
    condition_inputs: dict[str, object] = {"status": builder_status.value}
    if builder_status is ExecutionStatus.BUILDER_COMPLETE:
        next_stage = plane._integration_context(task).builder_success_target
        next_edge = f"execution.builder.success.{next_stage}"
        next_reason = f"builder completed, so {next_stage} is the next stage"
        condition_inputs["builder_success_target"] = next_stage
    plane._record_stage_transition(
        builder_result,
        task_before=task,
        task_after=task,
        routing_mode=routing_mode,
        selected_edge_id=next_edge,
        selected_edge_reason=next_reason,
        condition_inputs=condition_inputs,
        condition_result=builder_status is ExecutionStatus.BUILDER_COMPLETE,
    )
    if builder_status is not ExecutionStatus.BUILDER_COMPLETE:
        recovery = plane._recover_or_quarantine(
            task,
            run_id=run_id,
            stage_label="Builder",
            why=f"exit={builder_result.exit_code} status={builder_status.value}",
            stage_results=stage_results,
            failing_result=builder_result,
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

    return run_builder_success_sequence(
        plane,
        task,
        run_id,
        stage_results,
        recovery_rounds=recovery_rounds,
        routing_mode=routing_mode,
    )


__all__ = ["run_builder_success_sequence", "run_full_task_path"]
