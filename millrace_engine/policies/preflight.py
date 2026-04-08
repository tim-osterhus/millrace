"""Execution-stage preflight policy evaluation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import field_validator

from ..config import EngineConfig
from ..contracts import ContractModel, ExecutionStatus, StageType
from .evidence_helpers import _policy_evidence_details, _require_bool_detail
from .hooks import (
    PolicyDecision,
    PolicyEvaluationRecord,
    PolicyEvidence,
    PolicyEvidenceKind,
    PolicyFactSnapshot,
    PolicyHook,
    PolicyHookError,
    PolicyHookRuntime,
)
from .integration import ExecutionIntegrationEvaluator, ExecutionIntegrationSnapshot
from .network_guard import ExecutionNetworkGuardDecision, evaluate_execution_network_guard
from .pacing import ExecutionPacingEvaluator, ExecutionPacingSnapshot
from .transport import (
    DefaultTransportProbe,
    TransportProbe,
    TransportProbeContext,
    TransportProbeResult,
    TransportReadiness,
)
from .usage_budget import ExecutionUsageBudgetEvaluator, ExecutionUsageBudgetSnapshot

if TYPE_CHECKING:
    from ..paths import RuntimePaths
    from ..stages.base import ExecutionStage


class ExecutionPolicySnapshot(ContractModel):
    """Config-derived execution policy snapshot for one run."""

    preflight_enabled: bool
    transport_check_enabled: bool
    execution_search_enabled: bool
    execution_search_exception: bool
    network_guard_enabled: bool
    execution_network_policy: str
    execution_network_exception: bool

    @field_validator("execution_network_policy")
    @classmethod
    def normalize_execution_network_policy(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"allow", "deny"}:
            raise ValueError("execution_network_policy must be allow or deny")
        return normalized

    @classmethod
    def from_config(cls, config: EngineConfig) -> "ExecutionPolicySnapshot":
        return cls(
            preflight_enabled=config.policies.preflight.enabled,
            transport_check_enabled=config.policies.preflight.transport_check,
            execution_search_enabled=config.policies.search.execution_enabled,
            execution_search_exception=config.policies.search.execution_exception,
            network_guard_enabled=config.policies.network_guard.enabled,
            execution_network_policy=config.policies.network_guard.execution_policy,
            execution_network_exception=config.policies.network_guard.execution_exception,
        )


class StageRuntimePolicyContext(ContractModel):
    """Runtime stage context that is not part of the frozen plan."""

    command: tuple[str, ...] = ()

    @field_validator("command", mode="before")
    @classmethod
    def normalize_command(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return tuple(str(item).strip() for item in value if str(item).strip())


class ExecutionPreflightContext(ContractModel):
    """Effective preflight decision for one execution stage."""

    outcome: PolicyDecision
    allow_search: bool
    allow_network: bool
    block_status: ExecutionStatus | None = None
    reason: str
    transport: TransportProbeResult

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, value: PolicyDecision) -> PolicyDecision:
        if value not in {
            PolicyDecision.PASS,
            PolicyDecision.ENV_BLOCKED,
            PolicyDecision.POLICY_BLOCKED,
            PolicyDecision.NET_WAIT,
        }:
            raise ValueError("execution preflight uses normalized outcomes only")
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("reason may not be empty")
        return normalized


class ExecutionPreflightEvaluator:
    """Concrete execution preflight evaluator for pre-stage hooks."""

    evaluator_name = "execution_preflight_policy"

    def __init__(
        self,
        policy: ExecutionPolicySnapshot,
        *,
        stage_runtime: Callable[[StageType], StageRuntimePolicyContext],
        transport_probe: TransportProbe | None = None,
    ) -> None:
        self._policy = policy
        self._stage_runtime = stage_runtime
        self._transport_probe = transport_probe or DefaultTransportProbe()

    def __call__(self, facts: PolicyFactSnapshot) -> PolicyEvaluationRecord:
        if facts.hook is not PolicyHook.PRE_STAGE:
            raise PolicyHookError("execution preflight evaluator only supports pre_stage hooks")
        if facts.stage is None or facts.plan is None:
            raise PolicyHookError("execution preflight evaluator requires stage and frozen plan facts")

        stage_runtime = self._stage_runtime(facts.stage.stage)
        if facts.stage.runner is None:
            raise PolicyHookError(f"execution preflight requires a resolved runner for {facts.stage.stage.value}")
        transport = self._transport_probe.check(
            TransportProbeContext(
                runner=facts.stage.runner,
                model=facts.stage.model,
                command=stage_runtime.command,
            )
        )

        requested_search = bool(facts.stage.allow_search)
        mode_allows_search = (
            facts.plan.policy_toggles.allow_execution_search
            if facts.plan.policy_toggles is not None and facts.plan.policy_toggles.allow_execution_search is not None
            else True
        )
        access = evaluate_execution_network_guard(
            requested_search=requested_search,
            mode_allows_search=mode_allows_search,
            search_enabled=self._policy.execution_search_enabled,
            search_exception=self._policy.execution_search_exception,
            network_guard_enabled=self._policy.network_guard_enabled,
            execution_network_policy=self._policy.execution_network_policy,
            execution_network_exception=self._policy.execution_network_exception,
        )
        context = self._context(access=access, transport=transport)
        return PolicyEvaluationRecord(
            evaluator=self.evaluator_name,
            hook=facts.hook,
            decision=context.outcome,
            facts=facts,
            evidence=self._evidence(
                context=context,
                facts=facts,
                access=access,
                transport=transport,
                stage_runtime=stage_runtime,
            ),
            notes=(context.reason,),
        )

    def _context(
        self,
        *,
        access: ExecutionNetworkGuardDecision,
        transport: TransportProbeResult,
    ) -> ExecutionPreflightContext:
        allow_search = access.allow_search
        allow_network = access.allow_network
        if self._policy.preflight_enabled and self._policy.transport_check_enabled:
            if transport.readiness is TransportReadiness.ENV_BLOCKED:
                return ExecutionPreflightContext(
                    outcome=PolicyDecision.ENV_BLOCKED,
                    allow_search=False,
                    allow_network=False,
                    block_status=ExecutionStatus.BLOCKED,
                    reason=transport.summary,
                    transport=transport,
                )
            if transport.readiness is TransportReadiness.NET_WAIT:
                return ExecutionPreflightContext(
                    outcome=PolicyDecision.NET_WAIT,
                    allow_search=False,
                    allow_network=False,
                    block_status=ExecutionStatus.NET_WAIT,
                    reason=transport.summary,
                    transport=transport,
                )
        if access.policy_blocked:
            return ExecutionPreflightContext(
                outcome=PolicyDecision.POLICY_BLOCKED,
                allow_search=False,
                allow_network=allow_network,
                block_status=ExecutionStatus.BLOCKED,
                reason=access.reason,
                transport=transport,
            )
        return ExecutionPreflightContext(
            outcome=PolicyDecision.PASS,
            allow_search=allow_search,
            allow_network=allow_network,
            block_status=None,
            reason=access.reason,
            transport=transport,
        )

    def _evidence(
        self,
        *,
        context: ExecutionPreflightContext,
        facts: PolicyFactSnapshot,
        access: ExecutionNetworkGuardDecision,
        transport: TransportProbeResult,
        stage_runtime: StageRuntimePolicyContext,
    ) -> tuple[PolicyEvidence, ...]:
        return (
            PolicyEvidence(
                kind=PolicyEvidenceKind.POLICY_STATE,
                summary="Execution preflight evaluated the config-derived policy snapshot.",
                details={
                    "preflight_enabled": self._policy.preflight_enabled,
                    "transport_check_enabled": self._policy.transport_check_enabled,
                    "execution_search_enabled": self._policy.execution_search_enabled,
                    "execution_search_exception": self._policy.execution_search_exception,
                    "network_guard_enabled": self._policy.network_guard_enabled,
                    "execution_network_policy": self._policy.execution_network_policy,
                    "execution_network_exception": self._policy.execution_network_exception,
                    "mode_allow_execution_search": (
                        facts.plan.policy_toggles.allow_execution_search
                        if facts.plan is not None and facts.plan.policy_toggles is not None
                        else None
                    ),
                },
            ),
            PolicyEvidence(
                kind=PolicyEvidenceKind.TRANSPORT_CHECK,
                summary=transport.summary,
                details={
                    **transport.model_dump(mode="json"),
                    "command": list(stage_runtime.command),
                },
            ),
            PolicyEvidence(
                kind=PolicyEvidenceKind.NETWORK_GUARD,
                summary=context.reason,
                details={
                    **access.model_dump(mode="json"),
                    "preflight_outcome": context.outcome.value,
                    "block_status": context.block_status.value if context.block_status is not None else None,
                    "effective_allow_search": context.allow_search,
                    "effective_allow_network": context.allow_network,
                },
            ),
        )


def stage_runtime_from_execution_stage(stage: "ExecutionStage") -> StageRuntimePolicyContext:
    """Build runtime-stage facts from the concrete execution stage object."""

    return StageRuntimePolicyContext(command=stage.command)


def build_execution_policy_runtime(
    config: EngineConfig,
    *,
    stage_runtime: Callable[[StageType], StageRuntimePolicyContext],
    paths: "RuntimePaths",
    transport_probe: TransportProbe | None = None,
) -> PolicyHookRuntime:
    """Build the default execution policy hook runtime for one plane."""

    evaluator = ExecutionPreflightEvaluator(
        ExecutionPolicySnapshot.from_config(config),
        stage_runtime=stage_runtime,
        transport_probe=transport_probe,
    )
    return PolicyHookRuntime(
        evaluators={
            PolicyHook.CYCLE_BOUNDARY: (
                ExecutionIntegrationEvaluator(ExecutionIntegrationSnapshot.from_config(config)),
                ExecutionUsageBudgetEvaluator(
                    ExecutionUsageBudgetSnapshot.from_config(config),
                    paths=paths,
                ),
            ),
            PolicyHook.PRE_STAGE: (evaluator,),
            PolicyHook.POST_STAGE: (ExecutionPacingEvaluator(ExecutionPacingSnapshot.from_config(config)),),
        }
    )


def execution_preflight_context(record: PolicyEvaluationRecord | None) -> ExecutionPreflightContext | None:
    """Parse the persisted preflight context from one policy evaluation record."""

    if record is None or record.evaluator != ExecutionPreflightEvaluator.evaluator_name:
        return None
    details = _policy_evidence_details(record, kind=PolicyEvidenceKind.NETWORK_GUARD)
    if details is None:
        return None
    transport_details = _policy_evidence_details(record, kind=PolicyEvidenceKind.TRANSPORT_CHECK) or {}
    return ExecutionPreflightContext(
        outcome=PolicyDecision(str(details.get("preflight_outcome") or record.decision.value)),
        allow_search=_require_bool_detail(
            details,
            "effective_allow_search",
            error_prefix="persisted preflight evidence",
        ),
        allow_network=_require_bool_detail(
            details,
            "effective_allow_network",
            error_prefix="persisted preflight evidence",
        ),
        block_status=(
            ExecutionStatus(str(details["block_status"]))
            if details.get("block_status")
            else None
        ),
        reason=record.notes[0] if record.notes else str(details.get("reason") or "Execution preflight decision recorded."),
        transport=TransportProbeResult.model_validate(transport_details),
    )


def execution_preflight_context_from_records(
    records: tuple[PolicyEvaluationRecord, ...] | list[PolicyEvaluationRecord],
) -> ExecutionPreflightContext | None:
    """Return the last persisted execution preflight context from one hook batch."""

    for record in reversed(tuple(records)):
        context = execution_preflight_context(record)
        if context is not None:
            return context
    return None


__all__ = [
    "ExecutionPolicySnapshot",
    "ExecutionPreflightContext",
    "ExecutionPreflightEvaluator",
    "StageRuntimePolicyContext",
    "build_execution_policy_runtime",
    "execution_preflight_context",
    "execution_preflight_context_from_records",
    "stage_runtime_from_execution_stage",
]
