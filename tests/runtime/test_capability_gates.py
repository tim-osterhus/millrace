from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import (
    ApprovalPolicyRef,
    CapabilityDecisionState,
    CapabilityEnforcementMode,
    CapabilityScope,
    ExecutionCapabilityGrant,
    Plane,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import StageRunRequest
from millrace_ai.runners.base import default_capability_support_decision
from millrace_ai.runtime.approvals import (
    approve_execution_capability_request,
    list_execution_capability_approvals,
)
from millrace_ai.runtime.capability_gates import evaluate_stage_request_capabilities


def _grant(
    capability_id: str,
    *,
    decision: CapabilityDecisionState = CapabilityDecisionState.GRANTED,
    required: bool = True,
    approval: ApprovalPolicyRef | None = None,
) -> ExecutionCapabilityGrant:
    return ExecutionCapabilityGrant(
        grant_id=f"grant-{capability_id.replace('.', '-')}",
        request_id=f"request-{capability_id.replace('.', '-')}",
        capability_id=capability_id,
        access="execute",
        scope=CapabilityScope(kind="workspace", value="workspace"),
        required=required,
        decision_state=decision,
        enforcement_mode=(
            CapabilityEnforcementMode.NOT_APPLICABLE
            if decision is not CapabilityDecisionState.GRANTED
            else CapabilityEnforcementMode.RUNTIME_ENFORCED
        ),
        approval_policy_ref=approval,
        evidence_requirements=("runner_invocation",),
        decision_reason="test grant",
        resolved_by="test",
    )


def _request(tmp_path: Path, grant: ExecutionCapabilityGrant) -> StageRunRequest:
    return StageRunRequest(
        request_id="req-001",
        run_id="run-001",
        plane=Plane.EXECUTION,
        stage="builder",
        mode_id="default_codex",
        compiled_plan_id="plan-001",
        entrypoint_path=str(tmp_path / "entrypoint.md"),
        active_work_item_kind=WorkItemKind.TASK,
        active_work_item_id="task-001",
        active_work_item_path=str(tmp_path / "task.md"),
        run_dir=str(tmp_path),
        summary_status_path=str(tmp_path / "execution_status.md"),
        runtime_snapshot_path=str(tmp_path / "runtime_snapshot.json"),
        recovery_counters_path=str(tmp_path / "recovery_counters.json"),
        runner_name="unit-runner",
        timeout_seconds=60,
        execution_capability_grants=(grant,),
    )


def test_denied_required_grant_blocks_dispatch(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    request = _request(tmp_path, _grant("network.access", decision=CapabilityDecisionState.DENIED))

    result = evaluate_stage_request_capabilities(
        paths,
        request=request,
        support_evaluator=default_capability_support_decision,
        now=lambda: datetime(2026, 5, 15, tzinfo=timezone.utc),
    )

    assert result.allowed is False
    assert result.failure_class == "capability_grant_denied"
    assert result.blocked_grant_ids == ("grant-network-access",)


def test_approval_required_grant_blocks_until_approved(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    policy = ApprovalPolicyRef(policy_id="operator", gate_scope="stage", required_decision="approved")
    request = _request(
        tmp_path,
        _grant(
            "package.install",
            decision=CapabilityDecisionState.APPROVAL_REQUIRED,
            approval=policy,
        ),
    )
    def now() -> datetime:
        return datetime(2026, 5, 15, tzinfo=timezone.utc)

    blocked = evaluate_stage_request_capabilities(
        paths,
        request=request,
        support_evaluator=default_capability_support_decision,
        now=now,
    )

    assert blocked.allowed is False
    assert blocked.failure_class == "capability_approval_required"
    approvals = list_execution_capability_approvals(paths)
    assert len(approvals.pending) == 1

    approve_execution_capability_request(
        paths,
        approvals.pending[0].approval_id,
        decided_by="operator",
        reason="approved for test",
        now=now(),
    )
    allowed = evaluate_stage_request_capabilities(
        paths,
        request=request,
        support_evaluator=default_capability_support_decision,
        now=now,
    )

    assert allowed.allowed is True
    assert allowed.approval_ids == (approvals.pending[0].approval_id,)
