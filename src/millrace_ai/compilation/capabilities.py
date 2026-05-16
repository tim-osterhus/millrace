"""Compile execution capability requests into sealed grants."""

from __future__ import annotations

from collections import Counter

from millrace_ai.architecture import GraphLoopNodeDefinition, RegisteredStageKindDefinition
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ApprovalPolicyRef,
    CapabilityDecisionState,
    CapabilityEnforcementMode,
    CapabilityPolicyDecision,
    CapabilityPolicyOverride,
    CapabilityRequest,
    CapabilityScope,
    ExecutionCapabilityGrant,
    ModeDefinition,
)

from .outcomes import CompilerValidationError


def default_capability_requests_for_stage_kind(
    stage_kind: RegisteredStageKindDefinition,
) -> tuple[CapabilityRequest, ...]:
    """Return default framework execution requests for shipped stage kinds."""

    return (
        CapabilityRequest(
            request_id=f"{stage_kind.stage_kind_id}-runner-invoke",
            capability_id="runner.invoke",
            access="execute",
            scope=CapabilityScope(kind="runner", value="stage_runner"),
            reason="Stage execution requires invoking the configured runner.",
            requested_by="stage_kind_default",
        ),
        CapabilityRequest(
            request_id=f"{stage_kind.stage_kind_id}-workspace-read",
            capability_id="workspace.read",
            access="read",
            scope=CapabilityScope(kind="workspace", value="workspace"),
            reason="Stages need read access to the active workspace context.",
            requested_by="stage_kind_default",
        ),
        CapabilityRequest(
            request_id=f"{stage_kind.stage_kind_id}-artifact-write",
            capability_id="artifact.write",
            access="write",
            scope=CapabilityScope(kind="artifact_kind", value="stage_artifact"),
            reason="Stages need to emit runtime-visible artifacts.",
            requested_by="stage_kind_default",
        ),
    )


def compile_execution_capability_grants(
    *,
    node: GraphLoopNodeDefinition,
    stage_kind: RegisteredStageKindDefinition,
    mode: ModeDefinition,
    config: RuntimeConfig,
) -> tuple[tuple[ExecutionCapabilityGrant, ...], tuple[str, ...]]:
    if not config.execution_capabilities.enabled:
        return (), ()

    requests = _dedupe_requests(
        (
            *default_capability_requests_for_stage_kind(stage_kind),
            *stage_kind.execution_capability_requests,
            *node.execution_capability_requests,
            *mode.execution_capability_requests,
        )
    )
    warnings: list[str] = []
    grants: list[ExecutionCapabilityGrant] = []
    for request in requests:
        decision, resolved_by, reason = _resolve_decision(
            request=request,
            node=node,
            mode=mode,
            config=config,
        )
        enforcement_mode = _enforcement_mode_for_request(request, decision)
        if (
            request.required
            and enforcement_mode is CapabilityEnforcementMode.ADVISORY_ONLY
            and (request.requires_enforcement or config.execution_capabilities.fail_required_advisory)
        ):
            raise CompilerValidationError(
                f"Capability request {request.request_id} for {node.node_id} requires enforcement "
                "but resolves to advisory_only"
            )
        if request.required and enforcement_mode is CapabilityEnforcementMode.ADVISORY_ONLY:
            warnings.append(
                f"{node.node_id}:{request.capability_id} is required but advisory_only"
            )
        approval_policy_ref = (
            ApprovalPolicyRef(policy_id="operator", gate_scope="stage")
            if decision is CapabilityDecisionState.APPROVAL_REQUIRED
            else None
        )
        grants.append(
            ExecutionCapabilityGrant(
                grant_id=f"grant-{node.node_id}-{request.request_id}",
                request_id=request.request_id,
                capability_id=request.capability_id,
                access=request.access,
                scope=request.scope,
                required=request.required,
                decision_state=decision,
                enforcement_mode=enforcement_mode,
                approval_policy_ref=approval_policy_ref,
                evidence_requirements=_default_evidence_requirements(request.capability_id),
                decision_reason=reason,
                resolved_by=resolved_by,
            )
        )
    return tuple(grants), tuple(warnings)


def summarize_execution_capability_grants(
    grants: tuple[ExecutionCapabilityGrant, ...],
) -> dict[str, int | dict[str, int]]:
    decision_counts = Counter(grant.decision_state.value for grant in grants)
    enforcement_counts = Counter(grant.enforcement_mode.value for grant in grants)
    return {
        "total_grants": len(grants),
        "by_decision": dict(sorted(decision_counts.items())),
        "by_enforcement": dict(sorted(enforcement_counts.items())),
    }


def merge_execution_capability_summaries(
    summaries: tuple[dict[str, int | dict[str, int]], ...],
) -> dict[str, int | dict[str, int]]:
    total = 0
    by_decision: Counter[str] = Counter()
    by_enforcement: Counter[str] = Counter()
    for summary in summaries:
        total += int(summary.get("total_grants", 0))
        for key, value in _nested_counts(summary, "by_decision").items():
            by_decision[key] += value
        for key, value in _nested_counts(summary, "by_enforcement").items():
            by_enforcement[key] += value
    return {
        "total_grants": total,
        "by_decision": dict(sorted(by_decision.items())),
        "by_enforcement": dict(sorted(by_enforcement.items())),
    }


def _dedupe_requests(requests: tuple[CapabilityRequest, ...]) -> tuple[CapabilityRequest, ...]:
    deduped: dict[tuple[str, str, str, str], CapabilityRequest] = {}
    for request in requests:
        key = (
            request.capability_id,
            request.access,
            request.scope.kind,
            request.scope.value,
        )
        deduped[key] = request
    return tuple(deduped.values())


def _resolve_decision(
    *,
    request: CapabilityRequest,
    node: GraphLoopNodeDefinition,
    mode: ModeDefinition,
    config: RuntimeConfig,
) -> tuple[CapabilityDecisionState, str, str]:
    decision = CapabilityPolicyDecision.ALLOW
    resolved_by = "default_policy"
    reason = "allowed by default policy"

    policy = _matching_policy(mode.execution_capability_policies, request)
    if policy is not None:
        decision = policy.decision
        resolved_by = "mode"
        reason = policy.reason or "mode execution capability policy"

    policy = _matching_policy(node.execution_capability_policies, request)
    if policy is not None:
        decision = policy.decision
        resolved_by = "graph_node"
        reason = policy.reason or "graph node execution capability policy"

    config_decision = config.execution_capabilities.defaults.get(request.capability_id)
    if config_decision is not None:
        decision = config_decision
        resolved_by = "runtime_config"
        reason = "runtime config execution capability policy"

    if decision is CapabilityPolicyDecision.DENY:
        return CapabilityDecisionState.DENIED, resolved_by, reason
    if decision is CapabilityPolicyDecision.APPROVAL_REQUIRED:
        return CapabilityDecisionState.APPROVAL_REQUIRED, resolved_by, reason
    return CapabilityDecisionState.GRANTED, resolved_by, reason


def _matching_policy(
    policies: tuple[CapabilityPolicyOverride, ...],
    request: CapabilityRequest,
) -> CapabilityPolicyOverride | None:
    for policy in policies:
        if policy.capability_id != request.capability_id:
            continue
        if policy.scope is not None and policy.scope != request.scope:
            continue
        return policy
    return None


def _enforcement_mode_for_request(
    request: CapabilityRequest,
    decision: CapabilityDecisionState,
) -> CapabilityEnforcementMode:
    if decision is not CapabilityDecisionState.GRANTED:
        return CapabilityEnforcementMode.NOT_APPLICABLE
    if request.capability_id in {
        "runner.invoke",
        "artifact.read",
        "artifact.write",
        "evidence.emit",
        "runtime.control",
    }:
        return CapabilityEnforcementMode.RUNTIME_ENFORCED
    return CapabilityEnforcementMode.ADVISORY_ONLY


def _default_evidence_requirements(capability_id: str) -> tuple[str, ...]:
    if capability_id == "runner.invoke":
        return ("runner_invocation", "runner_completion")
    if capability_id.startswith("artifact."):
        return ("artifact_ref", "stage_result")
    return ("runtime_event",)


def _nested_counts(summary: dict[str, int | dict[str, int]], key: str) -> dict[str, int]:
    value = summary.get(key, {})
    if not isinstance(value, dict):
        return {}
    return {str(name): int(count) for name, count in value.items()}


__all__ = [
    "compile_execution_capability_grants",
    "default_capability_requests_for_stage_kind",
    "merge_execution_capability_summaries",
    "summarize_execution_capability_grants",
]
