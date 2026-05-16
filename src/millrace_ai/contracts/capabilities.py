"""Execution capability request and grant contracts."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from .base import ContractModel

BASE_EXECUTION_CAPABILITY_IDS = frozenset(
    {
        "workspace.read",
        "workspace.write",
        "artifact.read",
        "artifact.write",
        "runner.invoke",
        "shell.run",
        "git.read",
        "git.mutate",
        "package.install",
        "network.access",
        "approval.request",
        "evidence.emit",
        "runtime.control",
    }
)

CAPABILITY_KEY_ALIASES = {
    "workspace_read": "workspace.read",
    "workspace_write": "workspace.write",
    "artifact_read": "artifact.read",
    "artifact_write": "artifact.write",
    "runner_invoke": "runner.invoke",
    "shell_run": "shell.run",
    "git_read": "git.read",
    "git_mutate": "git.mutate",
    "package_install": "package.install",
    "network_access": "network.access",
    "approval_request": "approval.request",
    "evidence_emit": "evidence.emit",
    "runtime_control": "runtime.control",
}


class CapabilityDecisionState(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    UNSUPPORTED = "unsupported"


class CapabilityEnforcementMode(str, Enum):
    RUNNER_ENFORCED = "runner_enforced"
    RUNTIME_ENFORCED = "runtime_enforced"
    ADAPTER_ENFORCED = "adapter_enforced"
    EXTERNAL_API_ENFORCED = "external_api_enforced"
    ADVISORY_ONLY = "advisory_only"
    NOT_APPLICABLE = "not_applicable"


class CapabilityEvidenceStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    SATISFIED = "satisfied"
    MISSING = "missing"
    WAIVED = "waived"
    VIOLATED = "violated"


class CapabilitySupportState(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    PARTIALLY_SUPPORTED = "partially_supported"


class CapabilityPolicyDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class CapabilityScope(ContractModel):
    kind: str
    value: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        normalized = value.strip()
        allowed = {
            "workspace",
            "workspace_path",
            "artifact_kind",
            "artifact_ref",
            "runner",
            "command_class",
            "git_action",
            "package_manager",
            "network_class",
            "approval_policy_ref",
            "runtime_action",
        }
        if normalized not in allowed:
            raise ValueError(f"unknown capability scope kind: {value}")
        return normalized

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("capability scope value is required")
        return normalized

    @model_validator(mode="after")
    def validate_scope_shape(self) -> "CapabilityScope":
        if self.kind == "workspace_path":
            path = PurePosixPath(self.value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("workspace_path scope must stay inside workspace")
        if self.kind == "runtime_action":
            allowed = {
                "enqueue",
                "pause",
                "resume",
                "cancel",
                "retry",
                "repair",
                "approve",
                "deny",
                "reload_config",
            }
            if self.value not in allowed:
                raise ValueError(f"unknown runtime_action scope: {self.value}")
        return self


class ApprovalPolicyRef(ContractModel):
    policy_id: str
    gate_scope: Literal["stage", "run", "work_item"] = "stage"
    expiration_seconds: int | None = Field(default=None, gt=0)
    required_decision: Literal["approved"] = "approved"

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("policy_id is required")
        return normalized


class CapabilityRequest(ContractModel):
    request_id: str
    capability_id: str
    access: Literal["read", "write", "execute", "mutate", "request", "emit"]
    scope: CapabilityScope
    required: bool = True
    requires_enforcement: bool = False
    reason: str = ""
    requested_by: str = "stage"
    policy_source: str | None = None

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id is required")
        return normalized

    @field_validator("capability_id")
    @classmethod
    def validate_capability_id(cls, value: str) -> str:
        normalized = normalize_capability_id(value)
        if normalized not in BASE_EXECUTION_CAPABILITY_IDS:
            raise ValueError(f"unknown capability_id: {value}")
        return normalized


class CapabilityPolicyOverride(ContractModel):
    capability_id: str
    decision: CapabilityPolicyDecision
    scope: CapabilityScope | None = None
    reason: str = ""
    requires_enforcement: bool | None = None

    @field_validator("capability_id")
    @classmethod
    def validate_capability_id(cls, value: str) -> str:
        normalized = normalize_capability_id(value)
        if normalized not in BASE_EXECUTION_CAPABILITY_IDS:
            raise ValueError(f"unknown capability_id: {value}")
        return normalized


class ExecutionCapabilityGrant(ContractModel):
    grant_id: str
    request_id: str
    capability_id: str
    access: Literal["read", "write", "execute", "mutate", "request", "emit"]
    scope: CapabilityScope
    required: bool = True
    decision_state: CapabilityDecisionState
    enforcement_mode: CapabilityEnforcementMode
    approval_policy_ref: ApprovalPolicyRef | None = None
    evidence_requirements: tuple[str, ...] = ()
    evidence_status: CapabilityEvidenceStatus | None = None
    decision_reason: str
    resolved_by: str
    fingerprint: str = ""

    @field_validator("capability_id")
    @classmethod
    def validate_capability_id(cls, value: str) -> str:
        normalized = normalize_capability_id(value)
        if normalized not in BASE_EXECUTION_CAPABILITY_IDS:
            raise ValueError(f"unknown capability_id: {value}")
        return normalized

    @field_validator("grant_id", "request_id", "decision_reason", "resolved_by")
    @classmethod
    def validate_nonempty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be empty")
        return normalized

    @field_validator("evidence_requirements", mode="before")
    @classmethod
    def normalize_evidence_requirements(
        cls,
        value: tuple[str, ...] | list[str] | str | None,
    ) -> tuple[str, ...]:
        if value is None:
            return ()
        raw_values = [value] if isinstance(value, str) else list(value)
        normalized: list[str] = []
        for item in raw_values:
            evidence = str(item).strip()
            if not evidence:
                raise ValueError("evidence requirement values must not be empty")
            if evidence not in normalized:
                normalized.append(evidence)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_grant_shape(self) -> "ExecutionCapabilityGrant":
        if self.decision_state is CapabilityDecisionState.APPROVAL_REQUIRED and self.approval_policy_ref is None:
            raise ValueError("approval_required grants require approval_policy_ref")
        if (
            self.decision_state is not CapabilityDecisionState.GRANTED
            and self.enforcement_mode is not CapabilityEnforcementMode.NOT_APPLICABLE
        ):
            raise ValueError("non-granted capability decisions must use not_applicable enforcement")
        if self.evidence_status is None:
            self.evidence_status = (
                CapabilityEvidenceStatus.PENDING
                if self.evidence_requirements
                and self.decision_state is CapabilityDecisionState.GRANTED
                else CapabilityEvidenceStatus.NOT_REQUIRED
            )
        if not self.fingerprint:
            self.fingerprint = capability_grant_fingerprint(self)
        return self


class CapabilitySupportDecision(ContractModel):
    runner_id: str
    invocation_context_ref: str = ""
    grant_id: str
    support_state: CapabilitySupportState
    enforcement_mode: CapabilityEnforcementMode
    limitations: tuple[str, ...] = ()
    evidence_available: tuple[str, ...] = ()
    reason: str


def normalize_capability_id(value: str) -> str:
    normalized = value.strip()
    return CAPABILITY_KEY_ALIASES.get(normalized, normalized)


def capability_grant_fingerprint(grant: ExecutionCapabilityGrant) -> str:
    payload = grant.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"grant-{hashlib.sha256(encoded).hexdigest()[:12]}"


__all__ = [
    "ApprovalPolicyRef",
    "BASE_EXECUTION_CAPABILITY_IDS",
    "CAPABILITY_KEY_ALIASES",
    "CapabilityDecisionState",
    "CapabilityEnforcementMode",
    "CapabilityEvidenceStatus",
    "CapabilityPolicyDecision",
    "CapabilityPolicyOverride",
    "CapabilityRequest",
    "CapabilityScope",
    "CapabilitySupportDecision",
    "CapabilitySupportState",
    "ExecutionCapabilityGrant",
    "capability_grant_fingerprint",
    "normalize_capability_id",
]
