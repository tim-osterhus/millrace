"""Typed contracts for the research governance surface."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..contracts import ContractModel
from .governance_support import (
    DEFAULT_PINNED_FAMILY_POLICY_FIELDS,
    GOVERNANCE_REPORT_SCHEMA_VERSION,
    _normalize_bool,
    _normalize_datetime_or_none,
    _normalize_optional_text,
    _normalize_required_text,
    _normalize_token_sequence,
)


class GoalSpecGovernanceError(RuntimeError):
    """Raised when GoalSpec governance cannot admit the requested mutation."""


class InitialFamilyPlanGuardDecision(ContractModel):
    """Explainable result for the initial-family governance guard."""

    action: Literal["allow", "freeze", "validate", "block"]
    reason: str
    frozen: bool = False
    applied_family_max_specs: int = Field(default=0, ge=0)
    proposed_spec_count: int = Field(default=0, ge=0)
    added_spec_ids: tuple[str, ...] = ()
    removed_spec_ids: tuple[str, ...] = ()
    mutated_spec_ids: tuple[str, ...] = ()
    violation_codes: tuple[str, ...] = ()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("added_spec_ids", "removed_spec_ids", "mutated_spec_ids", "violation_codes", mode="before")
    @classmethod
    def normalize_sequences(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


class SpecSynthesisIdempotencyDecision(ContractModel):
    """Explainable reuse decision for spec-synthesis restart safety."""

    action: Literal["reuse", "rewrite"]
    reason: str
    mismatch_fields: tuple[str, ...] = ()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("mismatch_fields", mode="before")
    @classmethod
    def normalize_mismatch_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


class InitialFamilyPolicyPinDecision(ContractModel):
    """Explainable queue-governor decision for frozen-family policy pinning."""

    active: bool = False
    action: Literal["none", "pin"] = "none"
    reason: str
    pinned_fields: tuple[str, ...] = ()
    family_policy_path: str = ""
    spec_family_state_path: str = ""

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("pinned_fields", mode="before")
    @classmethod
    def normalize_pinned_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))

    @field_validator("family_policy_path", "spec_family_state_path", mode="before")
    @classmethod
    def normalize_optional_paths(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            return value.as_posix()
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_active_fields(self) -> "InitialFamilyPolicyPinDecision":
        if self.active and self.action != "pin":
            raise ValueError("active policy pin decisions must use action=pin")
        if self.active and not self.pinned_fields:
            raise ValueError("active policy pin decisions must record pinned_fields")
        if self.action == "none" and self.active:
            raise ValueError("inactive action mismatch")
        return self


class QueueGovernorReport(ContractModel):
    """Operator-facing queue-governor outcome for frozen-family policy pinning."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    updated_at: datetime | None = None
    goal_id: str = ""
    report_path: str = ""
    status: Literal["not_applicable", "compliant", "pinned"] = "not_applicable"
    reason: str
    initial_family_policy_pin: InitialFamilyPolicyPinDecision | None = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("goal_id", "report_path", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            return value.as_posix()
        return _normalize_optional_text(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")


class DriftControlPolicy(ContractModel):
    """Typed drift-detector policy for frozen-family policy drift."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    watched_family_policy_fields: tuple[str, ...] = DEFAULT_PINNED_FAMILY_POLICY_FIELDS
    hard_latch_on_policy_drift: bool = False

    @field_validator("watched_family_policy_fields", mode="before")
    @classmethod
    def normalize_watched_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return DEFAULT_PINNED_FAMILY_POLICY_FIELDS
        normalized = _normalize_token_sequence(tuple(str(item) for item in value))
        return normalized or DEFAULT_PINNED_FAMILY_POLICY_FIELDS

    @field_validator("hard_latch_on_policy_drift", mode="before")
    @classmethod
    def normalize_hard_latch(cls, value: object) -> bool:
        return _normalize_bool(value, default=False)


class DriftStatusReport(ContractModel):
    """Operator-facing drift-detector status for frozen-family policy drift."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    report_path: str = ""
    policy_path: str = ""
    family_policy_path: str = ""
    spec_family_state_path: str = ""
    status: Literal["not_applicable", "missing_policy", "clear", "warning", "hard_latch"] = "not_applicable"
    reason: str
    watched_fields: tuple[str, ...] = ()
    drift_fields: tuple[str, ...] = ()
    warning_active: bool = False
    hard_latch_active: bool = False

    @field_validator("report_path", "policy_path", "family_policy_path", "spec_family_state_path", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            return value.as_posix()
        return _normalize_optional_text(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("watched_fields", "drift_fields", mode="before")
    @classmethod
    def normalize_field_sequences(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


class GovernanceCanaryReport(ContractModel):
    """Operator-facing comparison of current vs baseline drift-control policy."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    report_path: str = ""
    baseline_policy_path: str = ""
    current_policy_path: str = ""
    status: Literal["not_configured", "baseline_missing", "match", "drifted"] = "not_configured"
    reason: str
    changed_fields: tuple[str, ...] = ()

    @field_validator("report_path", "baseline_policy_path", "current_policy_path", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            return value.as_posix()
        return _normalize_optional_text(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("changed_fields", mode="before")
    @classmethod
    def normalize_changed_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


class ResearchGovernanceReport(ContractModel):
    """Aggregated governance visibility for research/operator reporting."""

    queue_governor: QueueGovernorReport
    governance_canary: GovernanceCanaryReport
    drift: DriftStatusReport
    goalspec_delivery_integrity: "GoalSpecDeliveryIntegrityReport"
    progress_watchdog: "ProgressWatchdogReport"


class RecoveryTaskRegenerationReport(ContractModel):
    """Bounded recovery-task regeneration visibility for one frozen batch."""

    status: Literal["not_applicable", "not_needed", "regenerated", "manual_only"] = "not_applicable"
    reason: str
    decision_type: Literal["regenerated_backlog_work", "durable_remediation_decision", ""] = ""
    remediation_spec_id: str = ""
    remediation_record_path: str = ""
    taskaudit_record_path: str = ""
    task_provenance_path: str = ""
    lineage_path: str = ""
    visible_task_count_before: int = Field(default=0, ge=0)
    visible_task_count_after: int = Field(default=0, ge=0)
    regenerated_task_id: str = ""
    regenerated_task_title: str = ""
    family_policy_path: str = ""
    spec_family_state_path: str = ""
    family_policy_history_preserved: bool | None = None
    family_policy_history_reason: str = ""

    @field_validator(
        "reason",
        "remediation_spec_id",
        "remediation_record_path",
        "taskaudit_record_path",
        "task_provenance_path",
        "lineage_path",
        "regenerated_task_id",
        "regenerated_task_title",
        "family_policy_path",
        "spec_family_state_path",
        "family_policy_history_reason",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if isinstance(value, Path):
            return value.as_posix()
        text = _normalize_optional_text(value)
        if field_name == "reason" and not text:
            raise ValueError("reason may not be empty")
        return text


class ProgressWatchdogReport(ContractModel):
    """Explainable progress-watchdog view over the recovery-latch seam."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    updated_at: datetime | None = None
    report_path: str = ""
    state_path: str = ""
    latch_path: str = ""
    status: Literal["not_active", "waiting", "ready", "regenerated", "stalled"] = "not_active"
    reason: str
    batch_id: str = ""
    recovery_decision_type: Literal["regenerated_backlog_work", "durable_remediation_decision", ""] = ""
    remediation_spec_id: str = ""
    visible_recovery_task_count: int = Field(default=0, ge=0)
    expected_pending_card_count: int = Field(default=0, ge=0)
    expected_backlog_card_count: int = Field(default=0, ge=0)
    escalation_action: Literal["none", "monitor", "regenerate_task", "manual_review"] = "none"
    recovery_regeneration: RecoveryTaskRegenerationReport | None = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator(
        "report_path",
        "state_path",
        "latch_path",
        "reason",
        "batch_id",
        "remediation_spec_id",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if isinstance(value, Path):
            return value.as_posix()
        text = _normalize_optional_text(value)
        if field_name == "reason" and not text:
            raise ValueError("reason may not be empty")
        return text


class ProgressWatchdogState(ContractModel):
    """Persisted progress-watchdog state written by the engine."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    updated_at: datetime | None = None
    batch_id: str = ""
    status: Literal["not_active", "waiting", "ready", "regenerated", "stalled"] = "not_active"
    reason: str
    remediation_spec_id: str = ""
    visible_recovery_task_count: int = Field(default=0, ge=0)
    escalation_action: Literal["none", "monitor", "regenerate_task", "manual_review"] = "none"

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("batch_id", "reason", "remediation_spec_id", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        text = _normalize_optional_text(value)
        if field_name == "reason" and not text:
            raise ValueError("reason may not be empty")
        return text


class GoalSpecDeliveryIntegrityReport(ContractModel):
    """Explainable delivery-integrity view for emitted GoalSpec families."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    updated_at: datetime | None = None
    report_path: str = ""
    state_path: str = ""
    spec_family_state_path: str = ""
    status: Literal["not_applicable", "healthy", "failed"] = "not_applicable"
    reason: str
    goal_id: str = ""
    active_spec_id: str = ""
    emitted_spec_ids: tuple[str, ...] = ()
    pending_shard_count: int = Field(default=0, ge=0)
    merged_backlog_handoff: bool = False
    taskaudit_record_path: str = ""
    taskaudit_record_status: str = ""
    queue_item_path: str = ""
    queue_path: str = ""
    queue_goal_id: str = ""
    entry_node_id: str = ""
    violation_codes: tuple[str, ...] = ()

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator(
        "report_path",
        "state_path",
        "spec_family_state_path",
        "reason",
        "goal_id",
        "active_spec_id",
        "taskaudit_record_path",
        "taskaudit_record_status",
        "queue_item_path",
        "queue_path",
        "queue_goal_id",
        "entry_node_id",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if isinstance(value, Path):
            return value.as_posix()
        text = _normalize_optional_text(value)
        if field_name == "reason" and not text:
            raise ValueError("reason may not be empty")
        return text

    @field_validator("emitted_spec_ids", "violation_codes", mode="before")
    @classmethod
    def normalize_sequences(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


class GoalSpecDeliveryIntegrityState(ContractModel):
    """Persisted delivery-integrity state written by the engine/report surface."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    updated_at: datetime | None = None
    status: Literal["not_applicable", "healthy", "failed"] = "not_applicable"
    reason: str
    goal_id: str = ""
    active_spec_id: str = ""
    emitted_spec_ids: tuple[str, ...] = ()
    pending_shard_count: int = Field(default=0, ge=0)
    merged_backlog_handoff: bool = False
    taskaudit_record_path: str = ""
    taskaudit_record_status: str = ""
    violation_codes: tuple[str, ...] = ()

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator(
        "reason",
        "goal_id",
        "active_spec_id",
        "taskaudit_record_path",
        "taskaudit_record_status",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if isinstance(value, Path):
            return value.as_posix()
        text = _normalize_optional_text(value)
        if field_name == "reason" and not text:
            raise ValueError("reason may not be empty")
        return text

    @field_validator("emitted_spec_ids", "violation_codes", mode="before")
    @classmethod
    def normalize_sequences(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


__all__ = [
    "DriftControlPolicy",
    "DriftStatusReport",
    "GoalSpecDeliveryIntegrityReport",
    "GoalSpecDeliveryIntegrityState",
    "GoalSpecGovernanceError",
    "GovernanceCanaryReport",
    "InitialFamilyPlanGuardDecision",
    "InitialFamilyPolicyPinDecision",
    "ProgressWatchdogReport",
    "ProgressWatchdogState",
    "QueueGovernorReport",
    "RecoveryTaskRegenerationReport",
    "ResearchGovernanceReport",
    "SpecSynthesisIdempotencyDecision",
]
