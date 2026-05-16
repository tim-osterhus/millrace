from __future__ import annotations

import pytest
from pydantic import ValidationError

from millrace_ai.contracts import (
    ApprovalPolicyRef,
    CapabilityDecisionState,
    CapabilityEnforcementMode,
    CapabilityRequest,
    CapabilityScope,
    ExecutionCapabilityGrant,
    capability_grant_fingerprint,
)


def test_capability_request_accepts_known_workspace_path_scope() -> None:
    request = CapabilityRequest(
        request_id="builder-workspace-write",
        capability_id="workspace.write",
        access="write",
        scope={"kind": "workspace_path", "value": "src/millrace_ai"},
        reason="Builder edits implementation files.",
    )

    assert request.capability_id == "workspace.write"
    assert request.scope.kind == "workspace_path"
    assert request.scope.value == "src/millrace_ai"
    assert request.required is True


def test_capability_request_rejects_workspace_path_traversal() -> None:
    with pytest.raises(ValidationError, match="workspace_path scope must stay inside workspace"):
        CapabilityRequest(
            request_id="bad-path",
            capability_id="workspace.read",
            access="read",
            scope={"kind": "workspace_path", "value": "../secrets"},
        )


def test_required_unknown_capability_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown capability_id"):
        CapabilityRequest(
            request_id="unknown",
            capability_id="external.magic",
            access="execute",
            scope={"kind": "runner", "value": "codex_cli"},
        )


def test_approval_required_grant_requires_policy_ref() -> None:
    with pytest.raises(ValidationError, match="approval_required grants require approval_policy_ref"):
        ExecutionCapabilityGrant(
            grant_id="grant-package-install",
            request_id="package-install",
            capability_id="package.install",
            access="execute",
            scope=CapabilityScope(kind="package_manager", value="uv"),
            decision_state=CapabilityDecisionState.APPROVAL_REQUIRED,
            enforcement_mode=CapabilityEnforcementMode.NOT_APPLICABLE,
            evidence_requirements=(),
            decision_reason="package installs require approval",
            resolved_by="runtime_config",
        )


def test_grant_fingerprint_is_stable_for_equivalent_payloads() -> None:
    grant = ExecutionCapabilityGrant(
        grant_id="grant-shell-run",
        request_id="shell-run",
        capability_id="shell.run",
        access="execute",
        scope=CapabilityScope(kind="command_class", value="test"),
        decision_state=CapabilityDecisionState.GRANTED,
        enforcement_mode=CapabilityEnforcementMode.ADVISORY_ONLY,
        approval_policy_ref=None,
        evidence_requirements=("runner_invocation", "runner_completion"),
        decision_reason="allowed by default policy",
        resolved_by="default_policy",
    )

    assert grant.fingerprint == capability_grant_fingerprint(grant)
    assert grant.model_copy().fingerprint == grant.fingerprint


def test_approval_policy_ref_accepts_operator_approval() -> None:
    ref = ApprovalPolicyRef(policy_id="operator", gate_scope="stage", required_decision="approved")

    assert ref.policy_id == "operator"
