"""Typed research runtime state and checkpoint contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
import json

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    ContractModel,
    ExecutionResearchHandoff,
    PersistedObjectKind,
    RegistryObjectRef,
    ResearchMode,
    ResearchStatus,
    _normalize_datetime,
    _normalize_path,
)
from ..events import EventType
from .audit import AuditQueueRecord
from .blockers import BlockerQueueRecord
from .incidents import IncidentDocument


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    return normalized


class ResearchRuntimeMode(str, Enum):
    """Persisted active research mode tokens."""

    STUB = "STUB"
    AUTO = "AUTO"
    GOALSPEC = "GOALSPEC"
    INCIDENT = "INCIDENT"
    AUDIT = "AUDIT"

    @classmethod
    def from_value(
        cls,
        value: "ResearchRuntimeMode | ResearchMode | str",
    ) -> "ResearchRuntimeMode":
        if isinstance(value, cls):
            return value
        if isinstance(value, ResearchMode):
            return cls(value.value.upper())
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("research runtime mode may not be empty")
        return cls(normalized.upper())

    @property
    def config_mode(self) -> ResearchMode:
        return ResearchMode(self.value.lower())


class ResearchQueueFamily(str, Enum):
    GOALSPEC = "goalspec"
    INCIDENT = "incident"
    BLOCKER = "blocker"
    AUDIT = "audit"


class ResearchLockScope(str, Enum):
    PLANE_RUN = "plane_run"
    QUEUE_SCAN = "queue_scan"
    STAGE_DISPATCH = "stage_dispatch"


class DeferredResearchRequest(ContractModel):
    """Deferred research follow-on captured before full dispatch exists."""

    event_type: EventType
    received_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    breadcrumb_path: Path | None = None
    queue_family: ResearchQueueFamily | None = None
    handoff: ExecutionResearchHandoff | None = None
    incident_document: IncidentDocument | None = None
    blocker_record: BlockerQueueRecord | None = None
    audit_record: AuditQueueRecord | None = None

    @field_validator("received_at", mode="before")
    @classmethod
    def normalize_received_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("breadcrumb_path", mode="before")
    @classmethod
    def normalize_breadcrumb_path(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @model_validator(mode="after")
    def validate_queue_contracts(self) -> "DeferredResearchRequest":
        if self.incident_document is not None and self.queue_family not in {
            None,
            ResearchQueueFamily.INCIDENT,
        }:
            raise ValueError("incident_document requires incident queue_family")
        if self.blocker_record is not None and self.queue_family not in {
            None,
            ResearchQueueFamily.BLOCKER,
        }:
            raise ValueError("blocker_record requires blocker queue_family")
        if self.audit_record is not None and self.queue_family not in {
            None,
            ResearchQueueFamily.AUDIT,
        }:
            raise ValueError("audit_record requires audit queue_family")
        return self


class ResearchQueueOwnership(ContractModel):
    """One claimed queue family/item pair for deterministic restart."""

    family: ResearchQueueFamily
    queue_path: Path
    item_path: Path | None = None
    owner_token: str
    acquired_at: datetime

    @field_validator("queue_path", "item_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("owner_token")
    @classmethod
    def validate_owner_token(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="owner_token")

    @field_validator("acquired_at", mode="before")
    @classmethod
    def normalize_acquired_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class ResearchQueueSnapshot(ContractModel):
    """Serializable queue-readiness and ownership snapshot."""

    goalspec_ready: bool = False
    incident_ready: bool = False
    blocker_ready: bool = False
    audit_ready: bool = False
    selected_family: ResearchQueueFamily | None = None
    ownerships: tuple[ResearchQueueOwnership, ...] = ()
    last_scanned_at: datetime | None = None

    @field_validator("last_scanned_at", mode="before")
    @classmethod
    def normalize_last_scanned_at(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @model_validator(mode="after")
    def validate_selected_family(self) -> "ResearchQueueSnapshot":
        if self.selected_family is None:
            return self
        ready_map = {
            ResearchQueueFamily.GOALSPEC: self.goalspec_ready,
            ResearchQueueFamily.INCIDENT: self.incident_ready,
            ResearchQueueFamily.BLOCKER: self.blocker_ready,
            ResearchQueueFamily.AUDIT: self.audit_ready,
        }
        if self.selected_family is ResearchQueueFamily.BLOCKER and self.incident_ready:
            return self
        if not ready_map[self.selected_family]:
            raise ValueError("selected_family must be ready inside queue_snapshot")
        return self


class ResearchStageRetryState(ContractModel):
    """Retry bookkeeping for one research stage/checkpoint boundary."""

    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=0, ge=0)
    backoff_seconds: float = Field(default=0.0, ge=0.0)
    next_retry_at: datetime | None = None
    last_failure_reason: str | None = None
    last_failure_signature: str | None = None

    @field_validator("next_retry_at", mode="before")
    @classmethod
    def normalize_next_retry_at(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @field_validator("last_failure_reason", "last_failure_signature")
    @classmethod
    def normalize_failure_text(
        cls,
        value: str | None,
        info: object,
    ) -> str | None:
        field_name = getattr(info, "field_name", "text")
        return _normalize_optional_text(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_attempt_range(self) -> "ResearchStageRetryState":
        if self.attempt > self.max_attempts:
            raise ValueError("retry attempt may not exceed max_attempts")
        return self

    def exhausted(self) -> bool:
        """Return True when the retry budget has been consumed."""

        return self.attempt >= self.max_attempts


class ResearchLockState(ContractModel):
    """Restart-safe lock ownership snapshot for future dispatcher work."""

    lock_key: str
    owner_id: str
    scope: ResearchLockScope
    lock_path: Path
    acquired_at: datetime
    heartbeat_at: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("lock_key", "owner_id")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("lock_path", mode="before")
    @classmethod
    def normalize_lock_path(cls, value: str | Path) -> Path:
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError("lock_path may not be empty")
        return normalized

    @field_validator("acquired_at", "heartbeat_at", "expires_at", mode="before")
    @classmethod
    def normalize_datetimes(
        cls,
        value: datetime | str | None,
    ) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "ResearchLockState":
        if self.heartbeat_at is not None and self.heartbeat_at < self.acquired_at:
            raise ValueError("heartbeat_at may not be earlier than acquired_at")
        if self.expires_at is not None and self.expires_at < self.acquired_at:
            raise ValueError("expires_at may not be earlier than acquired_at")
        return self


class ResearchCheckpoint(ContractModel):
    """One restart checkpoint for the active research loop/node."""

    checkpoint_id: str
    mode: ResearchRuntimeMode
    status: ResearchStatus
    loop_ref: RegistryObjectRef | None = None
    node_id: str | None = None
    stage_kind_id: str | None = None
    attempt: int = Field(default=0, ge=0)
    started_at: datetime
    updated_at: datetime
    owned_queues: tuple[ResearchQueueOwnership, ...] = ()
    active_request: DeferredResearchRequest | None = None
    parent_handoff: ExecutionResearchHandoff | None = None
    deferred_follow_ons: tuple[DeferredResearchRequest, ...] = ()

    @field_validator("checkpoint_id")
    @classmethod
    def validate_checkpoint_id(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="checkpoint_id")

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(
        cls,
        value: ResearchRuntimeMode | ResearchMode | str,
    ) -> ResearchRuntimeMode:
        return ResearchRuntimeMode.from_value(value)

    @field_validator("node_id", "stage_kind_id")
    @classmethod
    def normalize_checkpoint_text(
        cls,
        value: str | None,
        info: object,
    ) -> str | None:
        field_name = getattr(info, "field_name", "text")
        return _normalize_optional_text(value, field_name=field_name)

    @field_validator("started_at", "updated_at", mode="before")
    @classmethod
    def normalize_checkpoint_datetimes(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @model_validator(mode="after")
    def validate_checkpoint(self) -> "ResearchCheckpoint":
        if self.loop_ref is not None and self.loop_ref.kind is not PersistedObjectKind.LOOP_CONFIG:
            raise ValueError("checkpoint loop_ref must reference a loop_config object")
        if self.updated_at < self.started_at:
            raise ValueError("checkpoint updated_at may not be earlier than started_at")
        if self.stage_kind_id is not None and self.node_id is None:
            raise ValueError("checkpoint stage_kind_id requires node_id")
        if (
            self.parent_handoff is not None
            and self.active_request is not None
            and self.active_request.handoff is not None
            and self.parent_handoff != self.active_request.handoff
        ):
            raise ValueError("checkpoint parent_handoff must align with active_request.handoff")
        return self


class ResearchRuntimeState(ContractModel):
    """Top-level persisted research runtime snapshot."""

    schema_version: Literal["1.0"] = "1.0"
    updated_at: datetime = Field(default_factory=_utcnow)
    current_mode: ResearchRuntimeMode = ResearchRuntimeMode.STUB
    last_mode: ResearchRuntimeMode = ResearchRuntimeMode.STUB
    mode_reason: str = "bootstrap"
    cycle_count: int = Field(default=0, ge=0)
    transition_count: int = Field(default=0, ge=0)
    queue_snapshot: ResearchQueueSnapshot = Field(default_factory=ResearchQueueSnapshot)
    deferred_requests: tuple[DeferredResearchRequest, ...] = ()
    retry_state: ResearchStageRetryState | None = None
    lock_state: ResearchLockState | None = None
    checkpoint: ResearchCheckpoint | None = None
    next_poll_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_payload(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "deferred_requests" not in payload and "pending" in payload:
            payload["deferred_requests"] = payload.pop("pending")
        if "mode_reason" not in payload and "reason" in payload:
            payload["mode_reason"] = payload.pop("reason")
        if "last_mode" not in payload and "previous_mode" in payload:
            payload["last_mode"] = payload.pop("previous_mode")
        return payload

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime:
        if value is None:
            raise ValueError("updated_at may not be null")
        return _normalize_datetime(value)

    @field_validator("next_poll_at", mode="before")
    @classmethod
    def normalize_next_poll_at(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @field_validator("current_mode", "last_mode", mode="before")
    @classmethod
    def normalize_runtime_modes(
        cls,
        value: ResearchRuntimeMode | ResearchMode | str,
    ) -> ResearchRuntimeMode:
        return ResearchRuntimeMode.from_value(value)

    @field_validator("mode_reason")
    @classmethod
    def validate_mode_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="mode_reason")

    @model_validator(mode="after")
    def validate_runtime_state(self) -> "ResearchRuntimeState":
        if self.transition_count > self.cycle_count:
            raise ValueError("transition_count may not exceed cycle_count")
        if self.checkpoint is not None and self.checkpoint.mode is not self.current_mode:
            raise ValueError("checkpoint mode must match current_mode")
        if self.next_poll_at is not None and self.next_poll_at < self.updated_at:
            raise ValueError("next_poll_at may not be earlier than updated_at")
        return self

    def retry_due(self, observed_at: datetime) -> bool:
        """Return True when any persisted retry window has opened."""

        retry_state = self.retry_state
        if retry_state is None:
            return True
        if retry_state.exhausted() and retry_state.next_retry_at is None:
            return False
        if retry_state.next_retry_at is None:
            return True
        return observed_at >= retry_state.next_retry_at

    def poll_due(self, observed_at: datetime) -> bool:
        """Return True when the next deterministic poll checkpoint has opened."""

        if self.next_poll_at is None:
            return True
        return observed_at >= self.next_poll_at

from .research_state_store import (
    PersistedStateMigrationApplyReport,
    PersistedStateMigrationPreviewReport,
    ResearchStateStore,
    apply_research_runtime_state_migration,
    clear_research_runtime_lock,
    load_research_runtime_state,
    preview_research_runtime_state_migration,
    rebind_research_runtime_state,
    write_research_runtime_state,
)


__all__ = [
    "DeferredResearchRequest",
    "PersistedStateMigrationApplyReport",
    "PersistedStateMigrationPreviewReport",
    "ResearchStateStore",
    "ResearchCheckpoint",
    "ResearchLockScope",
    "ResearchLockState",
    "ResearchQueueFamily",
    "ResearchQueueOwnership",
    "ResearchQueueSnapshot",
    "ResearchRuntimeMode",
    "ResearchRuntimeState",
    "ResearchStageRetryState",
    "apply_research_runtime_state_migration",
    "clear_research_runtime_lock",
    "load_research_runtime_state",
    "preview_research_runtime_state_migration",
    "rebind_research_runtime_state",
    "write_research_runtime_state",
]
