"""Typed audit contracts and parsing helpers."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..contracts import ContractModel, ResearchStatus, _normalize_datetime, _normalize_path

_WHITESPACE_RE = re.compile(r"\s+")
_AUDIT_ARTIFACT_SCHEMA_VERSION = "1.0"


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", value.strip())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = _WHITESPACE_RE.sub(" ", value.strip())
    if not normalized:
        return None
    return normalized


class AuditTrigger(str, Enum):
    """Supported audit trigger vocabulary."""

    QUEUE_EMPTY = "queue_empty"
    MANUAL = "manual"
    INCIDENT_FOLLOWUP = "incident_followup"
    OTHER = "other"


class AuditLifecycleStatus(str, Enum):
    """Supported audit queue lifecycle locations."""

    INCOMING = "incoming"
    WORKING = "working"
    PASSED = "passed"
    FAILED = "failed"


class AuditQueueRecord(ContractModel):
    """Validated audit queue document loaded from one markdown file."""

    source_path: Path
    audit_id: str
    title: str
    scope: str
    trigger: AuditTrigger
    lifecycle_status: AuditLifecycleStatus
    owner: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("source_path", mode="before")
    @classmethod
    def normalize_source_path(cls, value: str | Path) -> Path:
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError("source_path may not be empty")
        return normalized

    @field_validator("audit_id", "title", "scope")
    @classmethod
    def normalize_required_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("owner")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        return _normalize_optional_text(value, field_name=field_name)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @field_validator("trigger", mode="before")
    @classmethod
    def normalize_trigger(cls, value: AuditTrigger | str) -> AuditTrigger:
        return AuditTrigger(str(value).strip().lower())

    @field_validator("lifecycle_status", mode="before")
    @classmethod
    def normalize_lifecycle_status(
        cls,
        value: AuditLifecycleStatus | str,
    ) -> AuditLifecycleStatus:
        return AuditLifecycleStatus(str(value).strip().lower())

    @model_validator(mode="after")
    def validate_timestamps(self) -> "AuditQueueRecord":
        if self.created_at is not None and self.updated_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at may not be earlier than created_at")
        return self


class AuditIntakeRecord(ContractModel):
    """Durable intake record for one audit queue item."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["audit_intake_record"] = "audit_intake_record"
    run_id: str
    emitted_at: datetime
    audit_id: str
    title: str
    trigger: AuditTrigger
    scope: str
    source_path: str
    working_path: str

    @field_validator("run_id", "audit_id", "title", "scope", "source_path", "working_path")
    @classmethod
    def normalize_required_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class AuditValidateRecord(ContractModel):
    """Durable validation report for one audit run."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["audit_validate_report"] = "audit_validate_report"
    run_id: str
    emitted_at: datetime
    audit_id: str
    title: str
    trigger: AuditTrigger
    scope: str
    working_path: str
    execution_report_path: str
    finding_count: int = Field(default=0, ge=0)
    findings: tuple[str, ...] = ()
    summary: str
    recommended_decision: Literal["pass", "fail"]

    @field_validator(
        "run_id",
        "audit_id",
        "title",
        "scope",
        "working_path",
        "execution_report_path",
        "summary",
    )
    @classmethod
    def normalize_required_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("findings", mode="before")
    @classmethod
    def normalize_findings(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if value is None:
            return ()
        normalized: list[str] = []
        for item in value:
            text = _normalize_optional_text(item, field_name="finding")
            if text is not None:
                normalized.append(text)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_finding_count(self) -> "AuditValidateRecord":
        if self.finding_count != len(self.findings):
            raise ValueError("finding_count must match findings")
        return self


class AuditGatekeeperRecord(ContractModel):
    """Durable terminal decision record for one audit run."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["audit_gate_decision"] = "audit_gate_decision"
    run_id: str
    emitted_at: datetime
    audit_id: str
    title: str
    source_path: str
    terminal_path: str
    validate_record_path: str
    decision: Literal["audit_pass", "audit_fail"]
    final_status: ResearchStatus
    rationale: str
    gate_decision_path: str
    completion_decision_path: str
    remediation_record_path: str | None = None
    remediation_spec_id: str | None = None
    remediation_task_id: str | None = None

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "audit_id",
        "title",
        "source_path",
        "terminal_path",
        "validate_record_path",
        "rationale",
        "gate_decision_path",
        "completion_decision_path",
        "remediation_record_path",
        "remediation_spec_id",
        "remediation_task_id",
    )
    @classmethod
    def normalize_required_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        if field_name in {"remediation_record_path", "remediation_spec_id", "remediation_task_id"}:
            return _normalize_optional_text(value, field_name=field_name)
        return _normalize_required_text(value, field_name=field_name)


class AuditSummaryLastOutcome(ContractModel):
    """Compact latest-outcome snapshot mirrored into the workspace summary file."""

    status: Literal["AUDIT_PASS", "AUDIT_FAIL", "none"] = "none"
    details: str = "none"
    at: datetime | None = None
    audit_id: str | None = None
    title: str | None = None
    scope: str | None = None
    trigger: AuditTrigger | None = None
    decision: Literal["PASS", "FAIL"] | None = None
    reason_count: int = Field(default=0, ge=0)
    source_path: str | None = None
    terminal_path: str | None = None
    gate_decision_path: str | None = None
    completion_decision_path: str | None = None
    remediation_record_path: str | None = None
    remediation_spec_id: str | None = None
    remediation_task_id: str | None = None

    @field_validator("at", mode="before")
    @classmethod
    def normalize_at(cls, value: datetime | str | None) -> datetime | None:
        if value in (None, ""):
            return None
        return _normalize_datetime(value)

    @field_validator(
        "details",
        "audit_id",
        "title",
        "scope",
        "source_path",
        "terminal_path",
        "gate_decision_path",
        "completion_decision_path",
        "remediation_spec_id",
        "remediation_task_id",
        "remediation_record_path",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        if field_name == "details":
            return _normalize_required_text(value or "none", field_name=field_name)
        return _normalize_optional_text(value, field_name=field_name)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str | None) -> Literal["AUDIT_PASS", "AUDIT_FAIL", "none"]:
        if value is None:
            return "none"
        normalized = value.strip().upper()
        if normalized in {"AUDIT_PASS", "AUDIT_FAIL"}:
            return normalized
        return "none"

    @field_validator("decision", mode="before")
    @classmethod
    def normalize_decision(cls, value: str | None) -> Literal["PASS", "FAIL"] | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"PASS", "FAIL"}:
            raise ValueError("decision must be PASS or FAIL")
        return normalized


class AuditSummary(ContractModel):
    """Durable operator-facing audit summary."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    updated_at: datetime | None = None
    last_outcome: AuditSummaryLastOutcome | None = None
    counts: dict[str, int] = Field(default_factory=lambda: {"total": 0, "pass": 0, "fail": 0})

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        if value in (None, ""):
            return None
        return _normalize_datetime(value)

    @field_validator("counts", mode="before")
    @classmethod
    def normalize_counts(cls, value: dict[str, int] | None) -> dict[str, int]:
        payload = {"total": 0, "pass": 0, "fail": 0}
        if value:
            for key in payload:
                try:
                    parsed = int(value.get(key, 0))
                except (TypeError, ValueError):
                    parsed = 0
                payload[key] = parsed if parsed >= 0 else 0
        return payload


class AuditRemediationRecord(ContractModel):
    """Durable audit-failure remediation selection and enqueue record."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["audit_remediation"] = "audit_remediation"
    run_id: str
    emitted_at: datetime
    audit_id: str
    title: str
    scope: str
    trigger: AuditTrigger
    source_path: str
    terminal_path: str
    gate_decision_path: str
    completion_decision_path: str
    validate_record_path: str
    execution_report_path: str
    selected_action: Literal["enqueue_backlog_task", "reuse_existing_task"]
    remediation_spec_id: str
    remediation_task_id: str
    remediation_task_title: str
    backlog_depth_after_enqueue: int = Field(ge=0)
    reasons: tuple[str, ...] = ()
    recovery_latch_updated: bool = False

    @field_validator(
        "run_id",
        "audit_id",
        "title",
        "scope",
        "source_path",
        "terminal_path",
        "gate_decision_path",
        "completion_decision_path",
        "validate_record_path",
        "execution_report_path",
        "remediation_spec_id",
        "remediation_task_id",
        "remediation_task_title",
        mode="before",
    )
    @classmethod
    def normalize_required_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("reasons", mode="before")
    @classmethod
    def normalize_reasons(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if value is None:
            return ()
        normalized: list[str] = []
        for item in value:
            text = _normalize_optional_text(item, field_name="reason")
            if text is not None:
                normalized.append(text)
        return tuple(normalized)


class AuditIntakeExecutionResult(ContractModel):
    """Minimal intake result returned to the research plane."""

    record_path: str
    working_path: str
    audit_record: AuditQueueRecord

    @field_validator("record_path", "working_path")
    @classmethod
    def normalize_paths(cls, value: str | Path, info: object) -> str:
        field_name = getattr(info, "field_name", "path")
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError(f"{field_name} may not be empty")
        return normalized.as_posix()


class AuditValidateExecutionResult(ContractModel):
    """Minimal validate result returned to the research plane."""

    record_path: str
    working_path: str
    audit_record: AuditQueueRecord
    validate_record: AuditValidateRecord

    @field_validator("record_path", "working_path")
    @classmethod
    def normalize_paths(cls, value: str | Path, info: object) -> str:
        field_name = getattr(info, "field_name", "path")
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError(f"{field_name} may not be empty")
        return normalized.as_posix()


class AuditGatekeeperExecutionResult(ContractModel):
    """Minimal gatekeeper result returned to the research plane."""

    record_path: str
    terminal_path: str
    gate_decision_path: str
    completion_decision_path: str
    audit_record: AuditQueueRecord
    final_status: ResearchStatus
    remediation_record_path: str | None = None

    @field_validator(
        "record_path",
        "terminal_path",
        "gate_decision_path",
        "completion_decision_path",
        "remediation_record_path",
    )
    @classmethod
    def normalize_paths(cls, value: str | Path, info: object) -> str:
        field_name = getattr(info, "field_name", "path")
        if value is None and field_name == "remediation_record_path":
            return None
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError(f"{field_name} may not be empty")
        return normalized.as_posix()


class AuditExecutionError(RuntimeError):
    """Raised when an audit stage cannot continue safely."""


__all__ = [
    "AuditExecutionError",
    "AuditGatekeeperExecutionResult",
    "AuditGatekeeperRecord",
    "AuditIntakeExecutionResult",
    "AuditIntakeRecord",
    "AuditLifecycleStatus",
    "AuditQueueRecord",
    "AuditRemediationRecord",
    "AuditSummary",
    "AuditSummaryLastOutcome",
    "AuditTrigger",
    "AuditValidateExecutionResult",
    "AuditValidateRecord",
    "_AUDIT_ARTIFACT_SCHEMA_VERSION",
    "_normalize_optional_text",
    "_normalize_required_text",
]
