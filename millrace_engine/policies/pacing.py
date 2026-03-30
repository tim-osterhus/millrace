"""Execution inter-task pacing policy helpers."""

from __future__ import annotations

from pydantic import Field, field_validator

from ..config import EngineConfig
from ..contracts import ContractModel, ExecutionStatus, StageType
from .hooks import (
    PolicyDecision,
    PolicyEvaluationRecord,
    PolicyEvidence,
    PolicyEvidenceKind,
    PolicyFactSnapshot,
    PolicyHook,
    PolicyHookError,
)


class ExecutionPacingSnapshot(ContractModel):
    """Config-derived execution pacing snapshot."""

    delay_seconds: int = Field(ge=0)

    @classmethod
    def from_config(cls, config: EngineConfig) -> "ExecutionPacingSnapshot":
        return cls(delay_seconds=config.engine.inter_task_delay_seconds)


class ExecutionPacingContext(ContractModel):
    """Typed execution pacing decision."""

    apply_delay: bool
    delay_seconds: int = Field(ge=0)
    reason: str

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("reason may not be empty")
        return normalized


class ExecutionPacingEvaluator:
    """Post-stage pacing evaluator for successful task completion."""

    evaluator_name = "execution_pacing_policy"

    def __init__(self, policy: ExecutionPacingSnapshot) -> None:
        self._policy = policy

    def __call__(self, facts: PolicyFactSnapshot) -> PolicyEvaluationRecord | None:
        if facts.hook is not PolicyHook.POST_STAGE:
            raise PolicyHookError("execution pacing evaluator only supports post_stage hooks")
        if facts.stage is None:
            raise PolicyHookError("execution pacing evaluator requires stage facts")
        if facts.stage.stage is not StageType.UPDATE:
            return None
        if facts.task is None:
            return None
        if facts.stage_result_status != ExecutionStatus.UPDATE_COMPLETE.value:
            return None
        context = self._context()
        return PolicyEvaluationRecord(
            evaluator=self.evaluator_name,
            hook=facts.hook,
            decision=PolicyDecision.PASS,
            facts=facts,
            evidence=(
                PolicyEvidence(
                    kind=PolicyEvidenceKind.PACING_POLICY,
                    summary=(
                        f"Execution inter-task delay will sleep for {context.delay_seconds} seconds."
                        if context.apply_delay
                        else "Execution inter-task delay is skipped because it is not configured."
                    ),
                    details={
                        "apply_delay": context.apply_delay,
                        "delay_seconds": context.delay_seconds,
                    },
                ),
            ),
            notes=(context.reason,),
        )

    def _context(self) -> ExecutionPacingContext:
        if self._policy.delay_seconds <= 0:
            return ExecutionPacingContext(
                apply_delay=False,
                delay_seconds=0,
                reason="Execution inter-task delay skipped because engine.inter_task_delay_seconds is 0.",
            )
        return ExecutionPacingContext(
            apply_delay=True,
            delay_seconds=self._policy.delay_seconds,
            reason=f"Execution inter-task delay scheduled for {self._policy.delay_seconds} seconds.",
        )


def execution_pacing_context(record: PolicyEvaluationRecord | None) -> ExecutionPacingContext | None:
    """Parse one persisted execution pacing policy record."""

    if record is None or record.evaluator != ExecutionPacingEvaluator.evaluator_name:
        return None
    details = next(
        (item.details for item in record.evidence if item.kind is PolicyEvidenceKind.PACING_POLICY),
        None,
    )
    if details is None:
        return None
    return ExecutionPacingContext(
        apply_delay=bool(details.get("apply_delay")),
        delay_seconds=int(details.get("delay_seconds") or 0),
        reason=record.notes[0] if record.notes else "Execution inter-task pacing evaluated.",
    )


def execution_pacing_context_from_records(
    records: tuple[PolicyEvaluationRecord, ...] | list[PolicyEvaluationRecord],
) -> ExecutionPacingContext | None:
    """Return the last execution pacing context from one record batch."""

    for record in reversed(tuple(records)):
        context = execution_pacing_context(record)
        if context is not None:
            return context
    return None


__all__ = [
    "ExecutionPacingContext",
    "ExecutionPacingEvaluator",
    "ExecutionPacingSnapshot",
    "execution_pacing_context",
    "execution_pacing_context_from_records",
]
