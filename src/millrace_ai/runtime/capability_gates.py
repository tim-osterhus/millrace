"""Runtime pre-dispatch gates for execution capability grants."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from millrace_ai.contracts import (
    CapabilityDecisionState,
    CapabilityEnforcementMode,
    CapabilitySupportDecision,
    CapabilitySupportState,
    ExecutionCapabilityGrant,
)
from millrace_ai.events import write_runtime_event
from millrace_ai.paths import WorkspacePaths
from millrace_ai.runners.base import default_capability_support_decision
from millrace_ai.runners.requests import RunnerRawResult, StageRunRequest

from .approvals import ensure_execution_capability_approval, find_approval_for_grant

CapabilitySupportEvaluator = Callable[
    [ExecutionCapabilityGrant, Mapping[str, object]],
    CapabilitySupportDecision,
]


@dataclass(frozen=True, slots=True)
class CapabilityGateResult:
    allowed: bool
    request: StageRunRequest
    support_decisions: tuple[CapabilitySupportDecision, ...]
    approval_ids: tuple[str, ...] = ()
    blocked_grant_ids: tuple[str, ...] = ()
    failure_class: str | None = None
    reason: str | None = None


def evaluate_stage_request_capabilities(
    paths: WorkspacePaths,
    *,
    request: StageRunRequest,
    support_evaluator: CapabilitySupportEvaluator,
    now: Callable[[], datetime],
) -> CapabilityGateResult:
    support_decisions: list[CapabilitySupportDecision] = []
    approval_ids: list[str] = []
    blocked_grant_ids: list[str] = []

    for grant in request.execution_capability_grants:
        if not grant.required:
            continue
        if grant.decision_state is CapabilityDecisionState.DENIED:
            blocked_grant_ids.append(grant.grant_id)
            continue
        if grant.decision_state is CapabilityDecisionState.UNSUPPORTED:
            blocked_grant_ids.append(grant.grant_id)
            continue
        if grant.decision_state is CapabilityDecisionState.APPROVAL_REQUIRED:
            approval = _approval_for_request(paths, request=request, grant=grant, now=now)
            approval_ids.append(approval.approval_id)
            if approval.status != "approved":
                blocked_grant_ids.append(grant.grant_id)
            continue

        support = support_evaluator(grant, {"request": request, "stage": request.stage.value})
        support_decisions.append(support)
        if support.support_state is CapabilitySupportState.UNSUPPORTED:
            blocked_grant_ids.append(grant.grant_id)
            continue
        if (
            grant.enforcement_mode is not CapabilityEnforcementMode.ADVISORY_ONLY
            and support.enforcement_mode is CapabilityEnforcementMode.ADVISORY_ONLY
        ):
            blocked_grant_ids.append(grant.grant_id)

    failure_class = _failure_class_for_block(request, blocked_grant_ids)
    allowed = not blocked_grant_ids
    updated_request = request.model_copy(
        update={"capability_support_decisions": tuple(support_decisions)}
    )
    return CapabilityGateResult(
        allowed=allowed,
        request=updated_request,
        support_decisions=tuple(support_decisions),
        approval_ids=tuple(approval_ids),
        blocked_grant_ids=tuple(blocked_grant_ids),
        failure_class=failure_class,
        reason=(
            "all required capability grants satisfied"
            if allowed
            else f"blocked capability grants: {', '.join(blocked_grant_ids)}"
        ),
    )


def capability_gate_failure_result(
    *,
    request: StageRunRequest,
    gate_result: CapabilityGateResult,
    now: datetime,
) -> RunnerRawResult:
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "runtime",
        model_name=request.model_name,
        thinking_level=request.thinking_level,
        model_reasoning_effort=request.model_reasoning_effort,
        exit_kind="runner_error",
        exit_code=1,
        failure_class=gate_result.failure_class or "capability_gate_blocked",
        capability_support_decisions=gate_result.support_decisions,
        missing_capability_evidence_refs=gate_result.blocked_grant_ids,
        started_at=now,
        ended_at=now,
    )


def record_capability_gate_result(
    paths: WorkspacePaths,
    *,
    request: StageRunRequest,
    gate_result: CapabilityGateResult,
) -> Path:
    run_dir = Path(request.run_dir)
    artifact_path = run_dir / f"capability_gate.{request.request_id}.json"
    artifact_path.write_text(
        _gate_artifact_json(request=request, gate_result=gate_result),
        encoding="utf-8",
    )
    write_runtime_event(
        paths,
        event_type="capability_gate_evaluated",
        data={
            "request_id": request.request_id,
            "run_id": request.run_id,
            "stage": request.stage.value,
            "node_id": request.node_id,
            "stage_kind_id": request.stage_kind_id,
            "allowed": gate_result.allowed,
            "failure_class": gate_result.failure_class,
            "blocked_grant_ids": list(gate_result.blocked_grant_ids),
            "approval_ids": list(gate_result.approval_ids),
        },
    )
    return artifact_path


def support_evaluator_for_request(
    stage_runner: object,
    request: StageRunRequest,
) -> CapabilitySupportEvaluator:
    def _evaluate(
        grant: ExecutionCapabilityGrant,
        invocation_context: Mapping[str, object],
    ) -> CapabilitySupportDecision:
        evaluator = getattr(stage_runner, "evaluate_capability_grant", None)
        if evaluator is None:
            return default_capability_support_decision(grant, invocation_context)
        try:
            return evaluator(grant, request)
        except TypeError:
            return evaluator(grant, invocation_context)

    return _evaluate


def _approval_for_request(
    paths: WorkspacePaths,
    *,
    request: StageRunRequest,
    grant: ExecutionCapabilityGrant,
    now: Callable[[], datetime],
):
    existing = find_approval_for_grant(
        paths,
        run_id=request.run_id,
        request_id=request.request_id,
        grant_id=grant.grant_id,
    )
    if existing is not None:
        return existing
    return ensure_execution_capability_approval(
        paths,
        request_id=request.request_id,
        run_id=request.run_id,
        plane=request.plane,
        node_id=request.node_id,
        stage_kind_id=request.stage_kind_id,
        work_item_kind=request.active_work_item_kind,
        work_item_id=request.active_work_item_id,
        grant=grant,
        now=now(),
    )


def _failure_class_for_block(
    request: StageRunRequest,
    blocked_grant_ids: list[str],
) -> str | None:
    if not blocked_grant_ids:
        return None
    grants_by_id = {grant.grant_id: grant for grant in request.execution_capability_grants}
    decisions = {
        grants_by_id[grant_id].decision_state
        for grant_id in blocked_grant_ids
        if grant_id in grants_by_id
    }
    if CapabilityDecisionState.DENIED in decisions:
        return "capability_grant_denied"
    if CapabilityDecisionState.APPROVAL_REQUIRED in decisions:
        return "capability_approval_required"
    return "capability_grant_unsupported"


def _gate_artifact_json(
    *,
    request: StageRunRequest,
    gate_result: CapabilityGateResult,
) -> str:
    import json

    payload = {
        "schema_version": "1.0",
        "kind": "capability_gate_result",
        "request_id": request.request_id,
        "run_id": request.run_id,
        "stage": request.stage.value,
        "node_id": request.node_id,
        "allowed": gate_result.allowed,
        "failure_class": gate_result.failure_class,
        "blocked_grant_ids": list(gate_result.blocked_grant_ids),
        "approval_ids": list(gate_result.approval_ids),
        "support_decisions": [
            decision.model_dump(mode="json")
            for decision in gate_result.support_decisions
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


__all__ = [
    "CapabilityGateResult",
    "CapabilitySupportEvaluator",
    "capability_gate_failure_result",
    "evaluate_stage_request_capabilities",
    "record_capability_gate_result",
    "support_evaluator_for_request",
]
