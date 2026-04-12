"""Typed Sentinel config-adjacent contracts and persisted artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .contract_core import ContractModel

SENTINEL_ARTIFACT_SCHEMA_VERSION: Literal["1.0"] = "1.0"

SentinelHealthStatus = Literal[
    "healthy",
    "degraded",
    "monitoring",
    "recovery_queued",
    "suppressed",
    "escalated",
    "disabled",
]
SentinelRouteTarget = Literal["none", "troubleshoot", "mechanic", "notify", "halt"]
SentinelCheckTrigger = Literal["manual", "watch", "startup", "unknown"]
SentinelMonitorResolution = Literal["none", "pending", "resolved", "stalled", "escalated"]
SentinelLifecycleStatus = Literal["idle", "monitoring", "suppressed", "escalated", "disabled"]
SentinelProgressState = Literal["unknown", "progressing", "stale"]
SentinelIncidentRoutingTarget = Literal["troubleshoot", "mechanic"]


def _normalize_datetime_or_none(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = value.strip()
        if not text:
            return None
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _normalize_optional_text(value: str | Path | None) -> str:
    if isinstance(value, Path):
        return value.as_posix()
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _normalize_required_text(value: str | Path | None, *, field_name: str) -> str:
    text = _normalize_optional_text(value)
    if not text:
        raise ValueError(f"{field_name} may not be empty")
    return text


class SentinelCadenceState(ContractModel):
    schedule_started_at: datetime | None = None
    last_check_at: datetime | None = None
    next_check_at: datetime | None = None
    elapsed_seconds: int = Field(default=0, ge=0)
    current_interval_seconds: int = Field(default=0, ge=0)
    current_step_index: int = Field(default=0, ge=0)
    reset_on_recovery: bool = True

    @field_validator("schedule_started_at", "last_check_at", "next_check_at", mode="before")
    @classmethod
    def normalize_datetime_fields(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)


class SentinelCapState(ContractModel):
    soft_cap_threshold: int = Field(default=2, ge=1)
    hard_cap_threshold: int = Field(default=3, ge=1)
    soft_cap_count: int = Field(default=0, ge=0)
    hard_cap_count: int = Field(default=0, ge=0)
    acknowledgment_required: bool = False
    halt_on_hard_cap: bool = False
    last_soft_cap_at: datetime | None = None
    last_hard_cap_at: datetime | None = None

    @field_validator("last_soft_cap_at", "last_hard_cap_at", mode="before")
    @classmethod
    def normalize_datetime_fields(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("hard_cap_threshold")
    @classmethod
    def validate_threshold_order(cls, value: int, info: object) -> int:
        soft_cap_threshold = getattr(info, "data", {}).get("soft_cap_threshold")
        if isinstance(soft_cap_threshold, int) and value < soft_cap_threshold:
            raise ValueError("hard_cap_threshold must be greater than or equal to soft_cap_threshold")
        return value


class SentinelAcknowledgmentState(ContractModel):
    required: bool = False
    last_acknowledged_at: datetime | None = None
    last_acknowledged_by: str = ""
    last_acknowledgment_reason: str = ""

    @field_validator("last_acknowledged_at", mode="before")
    @classmethod
    def normalize_acknowledged_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("last_acknowledged_by", "last_acknowledgment_reason", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)


class SentinelMonitoringState(ContractModel):
    active: bool = False
    route_target: SentinelRouteTarget = "none"
    queued_recovery_request_id: str = ""
    incident_id: str = ""
    incident_path: str = ""
    queued_at: datetime | None = None
    last_observed_progress_at: datetime | None = None
    last_observed_status_snapshot_hash: str = ""
    resolution: SentinelMonitorResolution = "none"
    suppression_active: bool = False
    suppression_reason: str = ""
    resolution_changed_at: datetime | None = None

    @field_validator("queued_at", "last_observed_progress_at", "resolution_changed_at", mode="before")
    @classmethod
    def normalize_datetime_fields(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator(
        "queued_recovery_request_id",
        "incident_id",
        "incident_path",
        "last_observed_status_snapshot_hash",
        "suppression_reason",
        mode="before",
    )
    @classmethod
    def normalize_optional_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)


class SentinelSummary(ContractModel):
    status: SentinelHealthStatus = "healthy"
    reason: str
    last_check_at: datetime | None = None
    next_check_at: datetime | None = None
    checks_performed: int = Field(default=0, ge=0)
    route_target: SentinelRouteTarget = "none"
    monitoring_active: bool = False
    acknowledgment_required: bool = False
    current_interval_seconds: int = Field(default=0, ge=0)
    soft_cap_count: int = Field(default=0, ge=0)
    hard_cap_count: int = Field(default=0, ge=0)
    queued_recovery_request_id: str = ""
    last_incident_id: str = ""
    last_incident_path: str = ""

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: str | Path | None) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("last_check_at", "next_check_at", mode="before")
    @classmethod
    def normalize_datetime_fields(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("queued_recovery_request_id", "last_incident_id", "last_incident_path", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)


class SentinelCheckRecord(ContractModel):
    schema_version: Literal["1.0"] = SENTINEL_ARTIFACT_SCHEMA_VERSION
    check_id: str
    checked_at: datetime
    trigger: SentinelCheckTrigger = "manual"
    status: SentinelHealthStatus = "healthy"
    reason: str
    route_target: SentinelRouteTarget = "none"
    auto_queue_allowed: bool = False
    status_snapshot_hash: str = ""
    report_path: str = ""
    summary: SentinelSummary

    @field_validator("check_id", "reason", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: str | Path | None, info: object) -> str:
        return _normalize_required_text(value, field_name=getattr(info, "field_name", "value"))

    @field_validator("status_snapshot_hash", "report_path", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("checked_at", mode="before")
    @classmethod
    def normalize_checked_at(cls, value: datetime | str) -> datetime:
        normalized = _normalize_datetime_or_none(value)
        if normalized is None:
            raise ValueError("checked_at may not be empty")
        return normalized


class SentinelState(ContractModel):
    schema_version: Literal["1.0"] = SENTINEL_ARTIFACT_SCHEMA_VERSION
    updated_at: datetime
    enabled: bool = True
    lifecycle_status: SentinelLifecycleStatus = "idle"
    reason: str
    last_healthy_at: datetime | None = None
    checks_performed: int = Field(default=0, ge=0)
    latest_check_id: str = ""
    latest_report_path: str = ""
    last_incident_id: str = ""
    last_incident_path: str = ""
    last_recovery_request_id: str = ""
    cadence: SentinelCadenceState
    caps: SentinelCapState
    monitoring: SentinelMonitoringState | None = None
    acknowledgment: SentinelAcknowledgmentState = Field(default_factory=SentinelAcknowledgmentState)

    @field_validator("updated_at", "last_healthy_at", mode="before")
    @classmethod
    def normalize_datetime_fields(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: str | Path | None) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator(
        "latest_check_id",
        "latest_report_path",
        "last_incident_id",
        "last_incident_path",
        "last_recovery_request_id",
        mode="before",
    )
    @classmethod
    def normalize_optional_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)


class SentinelReport(ContractModel):
    schema_version: Literal["1.0"] = SENTINEL_ARTIFACT_SCHEMA_VERSION
    generated_at: datetime
    status: SentinelHealthStatus = "healthy"
    reason: str
    state_path: str = ""
    summary_path: str = ""
    latest_check_path: str = ""
    summary: SentinelSummary
    cadence: SentinelCadenceState
    caps: SentinelCapState
    monitoring: SentinelMonitoringState | None = None
    evidence: SentinelEvidenceSnapshot | None = None
    progress: SentinelProgressAssessment | None = None

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at(cls, value: datetime | str) -> datetime:
        normalized = _normalize_datetime_or_none(value)
        if normalized is None:
            raise ValueError("generated_at may not be empty")
        return normalized

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: str | Path | None) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("state_path", "summary_path", "latest_check_path", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)


class SentinelStatusMarkerEvidence(ContractModel):
    plane: Literal["execution", "research"]
    marker: str = ""
    observed_at: datetime | None = None
    source_path: str = ""

    @field_validator("marker", "source_path", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def normalize_observed_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)


class SentinelSupervisorEvidence(ContractModel):
    generated_at: datetime | None = None
    process_running: bool = False
    paused: bool = False
    execution_status: str = ""
    research_status: str = ""
    active_task_id: str = ""
    next_task_id: str = ""
    backlog_depth: int = Field(default=0, ge=0)
    deferred_queue_size: int = Field(default=0, ge=0)
    current_run_id: str = ""
    current_stage: str = ""
    attention_reason: str = ""
    attention_summary: str = ""

    @field_validator(
        "execution_status",
        "research_status",
        "active_task_id",
        "next_task_id",
        "current_run_id",
        "current_stage",
        "attention_reason",
        "attention_summary",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at_field(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)


class SentinelEventEvidence(ContractModel):
    timestamp: datetime
    event_type: str
    source: str
    progress_class: str
    signature: str
    summary: str = ""

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: datetime | str) -> datetime:
        normalized = _normalize_datetime_or_none(value)
        if normalized is None:
            raise ValueError("timestamp may not be empty")
        return normalized

    @field_validator("event_type", "source", "progress_class", "signature", "summary", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        text = _normalize_optional_text(value)
        if field_name in {"event_type", "source", "progress_class", "signature"} and not text:
            raise ValueError(f"{field_name} may not be empty")
        return text


class SentinelHistoryEvidence(ContractModel):
    timestamp: datetime | None = None
    event_type: str = ""
    task_id: str = ""
    detail_path: str = ""
    detail_exists: bool = False
    line: str = ""

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("event_type", "task_id", "detail_path", "line", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)


class SentinelArtifactEvidence(ContractModel):
    category: Literal["diagnostic", "run"]
    relative_path: str
    modified_at: datetime | None = None
    size_bytes: int = Field(default=0, ge=0)
    entry_count: int = Field(default=0, ge=0)
    signature: str

    @field_validator("relative_path", "signature", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: str | Path | None, info: object) -> str:
        return _normalize_required_text(value, field_name=getattr(info, "field_name", "value"))

    @field_validator("modified_at", mode="before")
    @classmethod
    def normalize_modified_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)


class SentinelIncidentQueueEvidence(ContractModel):
    incoming_count: int = Field(default=0, ge=0)
    working_count: int = Field(default=0, ge=0)
    resolved_count: int = Field(default=0, ge=0)
    archived_count: int = Field(default=0, ge=0)
    latest_change_at: datetime | None = None
    signature: str = ""

    @field_validator("latest_change_at", mode="before")
    @classmethod
    def normalize_latest_change_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("signature", mode="before")
    @classmethod
    def normalize_signature(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)


class SentinelProgressWatchdogEvidence(ContractModel):
    updated_at: datetime | None = None
    status: str = ""
    reason: str = ""
    batch_id: str = ""
    remediation_spec_id: str = ""
    visible_recovery_task_count: int = Field(default=0, ge=0)
    escalation_action: str = ""
    signature: str = ""

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at_field(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator(
        "status",
        "reason",
        "batch_id",
        "remediation_spec_id",
        "escalation_action",
        "signature",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)


class SentinelProgressComponent(ContractModel):
    component: str
    signature: str
    observed_at: datetime | None = None
    summary: str = ""

    @field_validator("component", "signature", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: str | Path | None, info: object) -> str:
        return _normalize_required_text(value, field_name=getattr(info, "field_name", "value"))

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def normalize_observed_at_field(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)


class SentinelEvidenceSnapshot(ContractModel):
    schema_version: Literal["1.0"] = SENTINEL_ARTIFACT_SCHEMA_VERSION
    collected_at: datetime
    execution_status: SentinelStatusMarkerEvidence
    research_status: SentinelStatusMarkerEvidence
    supervisor: SentinelSupervisorEvidence | None = None
    incidents: SentinelIncidentQueueEvidence
    progress_watchdog: SentinelProgressWatchdogEvidence | None = None
    recent_events: tuple[SentinelEventEvidence, ...] = ()
    recent_history: tuple[SentinelHistoryEvidence, ...] = ()
    diagnostics: tuple[SentinelArtifactEvidence, ...] = ()
    runs: tuple[SentinelArtifactEvidence, ...] = ()
    progress_components: tuple[SentinelProgressComponent, ...] = ()
    progress_signature: str
    latest_progress_at: datetime | None = None

    @field_validator("collected_at", "latest_progress_at", mode="before")
    @classmethod
    def normalize_datetime_fields(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("progress_signature", mode="before")
    @classmethod
    def normalize_progress_signature(cls, value: str | Path | None) -> str:
        return _normalize_required_text(value, field_name="progress_signature")


class SentinelProgressAssessment(ContractModel):
    checked_at: datetime
    state: SentinelProgressState = "unknown"
    reason: str
    progress_signature: str
    latest_progress_at: datetime | None = None
    changed_sources: tuple[str, ...] = ()
    evidence_summaries: tuple[str, ...] = ()

    @field_validator("checked_at", "latest_progress_at", mode="before")
    @classmethod
    def normalize_datetime_fields(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("reason", "progress_signature", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: str | Path | None, info: object) -> str:
        return _normalize_required_text(value, field_name=getattr(info, "field_name", "value"))

    @field_validator("changed_sources", "evidence_summaries", mode="before")
    @classmethod
    def normalize_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return tuple(_normalize_optional_text(item) for item in value if _normalize_optional_text(item))


class SentinelIncidentPayload(ContractModel):
    failure_signature: str
    summary: str
    severity: Literal["S1", "S2", "S3", "S4"] = "S2"
    routing_target: SentinelIncidentRoutingTarget
    evidence_pointers: tuple[str, ...] = ()
    observed_status_markers: tuple[SentinelStatusMarkerEvidence, ...] = ()
    elapsed_since_last_progress_seconds: int = Field(ge=0)
    source: Literal["sentinel"] = "sentinel"
    suggested_recovery: str = ""
    recovery_request_id: str = ""
    sentinel_check_id: str = ""
    sentinel_report_path: str = ""
    sentinel_state_path: str = ""
    report_status: SentinelHealthStatus = "healthy"
    report_reason: str = ""

    @field_validator(
        "failure_signature",
        "summary",
        "suggested_recovery",
        "recovery_request_id",
        "sentinel_check_id",
        "sentinel_report_path",
        "sentinel_state_path",
        "report_reason",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if field_name in {"failure_signature", "summary"}:
            return _normalize_required_text(value, field_name=field_name)
        return _normalize_optional_text(value)

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value: str) -> str:
        normalized = _normalize_required_text(value, field_name="severity").upper()
        if normalized not in {"S1", "S2", "S3", "S4"}:
            raise ValueError("severity must be one of S1, S2, S3, or S4")
        return normalized

    @field_validator("evidence_pointers", mode="before")
    @classmethod
    def normalize_evidence_pointers(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        return tuple(_normalize_optional_text(item) for item in value if _normalize_optional_text(item))


class SentinelIncidentBundle(ContractModel):
    schema_version: Literal["1.0"] = SENTINEL_ARTIFACT_SCHEMA_VERSION
    emitted_at: datetime
    incident_id: str
    incident_path: str
    bundle_path: str
    issuer: str
    command_id: str = ""
    payload: SentinelIncidentPayload
    linked_to_persisted_sentinel_state: bool = False

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        normalized = _normalize_datetime_or_none(value)
        if normalized is None:
            raise ValueError("emitted_at may not be empty")
        return normalized

    @field_validator("incident_id", "incident_path", "bundle_path", "issuer", "command_id", mode="before")
    @classmethod
    def normalize_required_or_optional_text(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if field_name in {"incident_id", "incident_path", "bundle_path", "issuer"}:
            return _normalize_required_text(value, field_name=field_name)
        return _normalize_optional_text(value)


__all__ = [
    "SENTINEL_ARTIFACT_SCHEMA_VERSION",
    "SentinelAcknowledgmentState",
    "SentinelCadenceState",
    "SentinelCapState",
    "SentinelCheckRecord",
    "SentinelEvidenceSnapshot",
    "SentinelEventEvidence",
    "SentinelHistoryEvidence",
    "SentinelIncidentBundle",
    "SentinelIncidentPayload",
    "SentinelIncidentRoutingTarget",
    "SentinelArtifactEvidence",
    "SentinelIncidentQueueEvidence",
    "SentinelProgressAssessment",
    "SentinelProgressComponent",
    "SentinelProgressState",
    "SentinelProgressWatchdogEvidence",
    "SentinelMonitoringState",
    "SentinelReport",
    "SentinelState",
    "SentinelStatusMarkerEvidence",
    "SentinelSummary",
    "SentinelSupervisorEvidence",
]
