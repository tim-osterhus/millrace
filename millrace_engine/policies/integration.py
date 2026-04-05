"""Execution integration-gating policy evaluation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from ..config import EngineConfig
from ..contracts import ContractModel, StageType, TaskCard
from .evidence_helpers import _policy_evidence_details, _require_bool_detail
from .hooks import (
    PolicyDecision,
    PolicyEvaluationRecord,
    PolicyEvidence,
    PolicyEvidenceKind,
    PolicyFactSnapshot,
    PolicyHook,
    PolicyHookError,
    PolicyTaskFacts,
)


class ExecutionIntegrationSnapshot(ContractModel):
    """Config-derived integration routing snapshot for one run."""

    default_mode: Literal["always", "large_only", "never"]
    builder_success_sequence: tuple[StageType, ...]
    builder_success_sequence_with_integration: tuple[StageType, ...]

    @field_validator("builder_success_sequence", "builder_success_sequence_with_integration", mode="before")
    @classmethod
    def normalize_stage_sequences(
        cls,
        value: tuple[StageType, ...] | list[StageType | str] | None,
    ) -> tuple[StageType, ...]:
        if not value:
            return ()
        normalized: list[StageType] = []
        for item in value:
            normalized.append(item if isinstance(item, StageType) else StageType(str(item).strip()))
        return tuple(normalized)

    @classmethod
    def from_config(cls, config: EngineConfig) -> "ExecutionIntegrationSnapshot":
        return cls(
            default_mode=config.execution.integration_mode,
            builder_success_sequence=config.routing.builder_success_sequence,
            builder_success_sequence_with_integration=config.routing.builder_success_sequence_with_integration,
        )


class ExecutionIntegrationContext(ContractModel):
    """Effective integration-routing decision for one execution cycle."""

    effective_mode: Literal["always", "large_only", "never"]
    builder_success_target: str
    should_run_integration: bool
    task_gate_required: bool = False
    task_integration_preference: Literal["force", "skip", "inherit"] | None = None
    requested_sequence: tuple[StageType, ...]
    effective_sequence: tuple[StageType, ...]
    available_execution_nodes: tuple[str, ...]
    reason: str

    @field_validator("builder_success_target", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("integration context text fields may not be empty")
        return normalized

    @field_validator("requested_sequence", "effective_sequence", mode="before")
    @classmethod
    def normalize_stage_sequences(
        cls,
        value: tuple[StageType, ...] | list[StageType | str] | None,
    ) -> tuple[StageType, ...]:
        if not value:
            return ()
        normalized: list[StageType] = []
        for item in value:
            normalized.append(item if isinstance(item, StageType) else StageType(str(item).strip()))
        return tuple(normalized)

    @field_validator("available_execution_nodes", mode="before")
    @classmethod
    def normalize_available_execution_nodes(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            node_id = str(item).strip()
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            normalized.append(node_id)
        return tuple(normalized)


def _effective_mode(
    snapshot: ExecutionIntegrationSnapshot,
    *,
    policy_toggle_integration_mode: str | None,
) -> Literal["always", "large_only", "never"]:
    if policy_toggle_integration_mode in {"always", "large_only", "never"}:
        return policy_toggle_integration_mode
    return snapshot.default_mode


def _task_gate_required(task: PolicyTaskFacts | TaskCard | None) -> bool:
    if task is None:
        return False
    return "INTEGRATION" in {str(item).strip().upper() for item in getattr(task, "gates", ())}


def _task_integration_preference(
    task: PolicyTaskFacts | TaskCard | None,
) -> Literal["force", "skip", "inherit"] | None:
    if task is None:
        return None
    preference = getattr(task, "integration_preference", None)
    if preference in {"force", "skip", "inherit"}:
        return preference
    return None


def resolve_execution_integration_context(
    snapshot: ExecutionIntegrationSnapshot,
    *,
    task: PolicyTaskFacts | TaskCard | None,
    policy_toggle_integration_mode: str | None,
    execution_node_ids: tuple[str, ...] | list[str],
) -> ExecutionIntegrationContext:
    """Resolve one deterministic builder-success routing decision."""

    available_nodes: list[str] = []
    seen_nodes: set[str] = set()
    for item in execution_node_ids:
        node_id = str(item).strip()
        if not node_id or node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        available_nodes.append(node_id)

    effective_mode = _effective_mode(
        snapshot,
        policy_toggle_integration_mode=policy_toggle_integration_mode,
    )
    task_gate_required = _task_gate_required(task)
    task_preference = _task_integration_preference(task)

    if task_preference == "skip":
        requested_sequence = snapshot.builder_success_sequence
        base_reason = "Task integration override suppresses integration."
    elif task_preference == "force":
        requested_sequence = snapshot.builder_success_sequence_with_integration
        base_reason = "Task integration override requires integration."
    elif task_gate_required:
        requested_sequence = snapshot.builder_success_sequence_with_integration
        base_reason = "Task gate requires integration."
    elif effective_mode == "always":
        requested_sequence = snapshot.builder_success_sequence_with_integration
        base_reason = "Effective integration mode always routes through the integration path."
    elif effective_mode == "never":
        requested_sequence = snapshot.builder_success_sequence
        base_reason = "Effective integration mode suppresses the integration path."
    else:
        requested_sequence = snapshot.builder_success_sequence
        base_reason = "Effective integration mode large_only keeps integration off for this standard execution plan."

    effective_sequence = tuple(stage for stage in requested_sequence if stage.value in seen_nodes)
    if not effective_sequence:
        fallback_sequence = tuple(
            stage for stage in snapshot.builder_success_sequence if stage.value in seen_nodes
        )
        if fallback_sequence:
            effective_sequence = fallback_sequence
            reason = (
                f"{base_reason} The requested builder-success route is absent from the compiled execution plan, "
                f"so builder falls back to {effective_sequence[0].value}."
            )
        elif available_nodes:
            synthetic_stage = StageType(available_nodes[0])
            effective_sequence = (synthetic_stage,)
            reason = (
                f"{base_reason} The compiled execution plan omits the standard builder-success nodes, "
                f"so routing facts fall back to {effective_sequence[0].value}."
            )
        else:
            raise PolicyHookError("integration policy could not resolve any available builder-success route")
    elif StageType.INTEGRATION in requested_sequence and StageType.INTEGRATION not in effective_sequence:
        reason = (
            f"{base_reason} The compiled execution plan does not expose an integration node, "
            f"so builder routes to {effective_sequence[0].value} instead."
        )
    else:
        reason = f"{base_reason} Builder routes to {effective_sequence[0].value}."

    return ExecutionIntegrationContext(
        effective_mode=effective_mode,
        builder_success_target=effective_sequence[0].value,
        should_run_integration=StageType.INTEGRATION in effective_sequence,
        task_gate_required=task_gate_required,
        task_integration_preference=task_preference,
        requested_sequence=requested_sequence,
        effective_sequence=effective_sequence,
        available_execution_nodes=tuple(available_nodes),
        reason=reason,
    )


class ExecutionIntegrationEvaluator:
    """Concrete integration-routing evaluator for cycle-boundary hooks."""

    evaluator_name = "execution_integration_policy"

    def __init__(self, snapshot: ExecutionIntegrationSnapshot) -> None:
        self._snapshot = snapshot

    def __call__(self, facts: PolicyFactSnapshot) -> PolicyEvaluationRecord:
        if facts.hook is not PolicyHook.CYCLE_BOUNDARY:
            raise PolicyHookError("execution integration evaluator only supports cycle_boundary hooks")
        if facts.plan is None:
            raise PolicyHookError("execution integration evaluator requires frozen plan facts")

        context = resolve_execution_integration_context(
            self._snapshot,
            task=facts.task,
            policy_toggle_integration_mode=(
                facts.plan.policy_toggles.integration_mode
                if facts.plan.policy_toggles is not None
                else None
            ),
            execution_node_ids=facts.plan.execution_node_ids,
        )
        return PolicyEvaluationRecord(
            evaluator=self.evaluator_name,
            hook=facts.hook,
            decision=PolicyDecision.PASS,
            facts=facts,
            evidence=(
                PolicyEvidence(
                    kind=PolicyEvidenceKind.POLICY_STATE,
                    summary="Execution integration policy evaluated the config-derived routing snapshot.",
                    details={
                        "default_mode": self._snapshot.default_mode,
                        "builder_success_sequence": [stage.value for stage in self._snapshot.builder_success_sequence],
                        "builder_success_sequence_with_integration": [
                            stage.value for stage in self._snapshot.builder_success_sequence_with_integration
                        ],
                        "policy_toggle_integration_mode": (
                            facts.plan.policy_toggles.integration_mode
                            if facts.plan.policy_toggles is not None
                            else None
                        ),
                    },
                ),
                PolicyEvidence(
                    kind=PolicyEvidenceKind.INTEGRATION_POLICY,
                    summary=context.reason,
                    details=context.model_dump(mode="json"),
                ),
            ),
            notes=(context.reason,),
        )


def execution_integration_context(record: PolicyEvaluationRecord | None) -> ExecutionIntegrationContext | None:
    """Parse one persisted integration-routing context."""

    if record is None or record.evaluator != ExecutionIntegrationEvaluator.evaluator_name:
        return None
    details = _policy_evidence_details(record, kind=PolicyEvidenceKind.INTEGRATION_POLICY)
    if details is None:
        return None
    sanitized_details = dict(details)
    sanitized_details["should_run_integration"] = _require_bool_detail(
        details,
        "should_run_integration",
        error_prefix="persisted integration evidence",
    )
    sanitized_details["task_gate_required"] = _require_bool_detail(
        details,
        "task_gate_required",
        error_prefix="persisted integration evidence",
    )
    return ExecutionIntegrationContext.model_validate(sanitized_details)


def execution_integration_context_from_records(
    records: tuple[PolicyEvaluationRecord, ...] | list[PolicyEvaluationRecord],
) -> ExecutionIntegrationContext | None:
    """Return the last persisted integration-routing context from one hook batch."""

    for record in reversed(tuple(records)):
        context = execution_integration_context(record)
        if context is not None:
            return context
    return None


__all__ = [
    "ExecutionIntegrationContext",
    "ExecutionIntegrationEvaluator",
    "ExecutionIntegrationSnapshot",
    "execution_integration_context",
    "execution_integration_context_from_records",
    "resolve_execution_integration_context",
]
