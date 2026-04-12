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

    @field_validator("queued_at", "last_observed_progress_at", mode="before")
    @classmethod
    def normalize_datetime_fields(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator(
        "queued_recovery_request_id",
        "incident_id",
        "incident_path",
        "last_observed_status_snapshot_hash",
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
    route_target: SentinelRouteTarget = "none"
    monitoring_active: bool = False
    acknowledgment_required: bool = False
    current_interval_seconds: int = Field(default=0, ge=0)
    soft_cap_count: int = Field(default=0, ge=0)
    hard_cap_count: int = Field(default=0, ge=0)
    queued_recovery_request_id: str = ""

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: str | Path | None) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("last_check_at", "next_check_at", mode="before")
    @classmethod
    def normalize_datetime_fields(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("queued_recovery_request_id", mode="before")
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
    latest_check_id: str = ""
    latest_report_path: str = ""
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

    @field_validator("latest_check_id", "latest_report_path", mode="before")
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


__all__ = [
    "SENTINEL_ARTIFACT_SCHEMA_VERSION",
    "SentinelAcknowledgmentState",
    "SentinelCadenceState",
    "SentinelCapState",
    "SentinelCheckRecord",
    "SentinelMonitoringState",
    "SentinelReport",
    "SentinelState",
    "SentinelSummary",
]
