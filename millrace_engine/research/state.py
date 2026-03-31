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
from ..markdown import write_text_atomic
from .audit import AuditQueueRecord
from .blockers import BlockerQueueRecord
from .incidents import IncidentDocument


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _default_stub_runtime_state(*, mode_reason: str) -> ResearchRuntimeState:
    return ResearchRuntimeState(
        current_mode=ResearchRuntimeMode.STUB,
        last_mode=ResearchRuntimeMode.STUB,
        mode_reason=mode_reason,
    )


def _load_deferred_request_from_breadcrumb(path: Path) -> DeferredResearchRequest:
    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    return DeferredResearchRequest.model_validate(
        {
            "event_type": raw_payload["event_type"],
            "received_at": raw_payload["received_at"],
            "payload": raw_payload.get("payload", {}),
            "breadcrumb_path": path,
            "queue_family": raw_payload.get("queue_family"),
            "handoff": raw_payload.get("handoff"),
            "audit_record": raw_payload.get("audit_record"),
        }
    )


def _append_timestamp_candidate(candidates: list[datetime], value: Any) -> None:
    if value is None:
        return
    candidates.append(_normalize_datetime(value))


def _repair_runtime_state_payload(payload: Any, *, state_path: Path) -> Any:
    if not isinstance(payload, dict) or payload.get("updated_at") is not None:
        return payload

    candidates: list[datetime] = []
    _append_timestamp_candidate(candidates, payload.get("next_poll_at"))

    queue_snapshot = payload.get("queue_snapshot")
    if isinstance(queue_snapshot, dict):
        _append_timestamp_candidate(candidates, queue_snapshot.get("last_scanned_at"))
        for ownership in queue_snapshot.get("ownerships", ()):
            if isinstance(ownership, dict):
                _append_timestamp_candidate(candidates, ownership.get("acquired_at"))

    for request in payload.get("deferred_requests") or payload.get("pending") or ():
        if isinstance(request, dict):
            _append_timestamp_candidate(candidates, request.get("received_at"))
            audit_record = request.get("audit_record")
            if isinstance(audit_record, dict):
                _append_timestamp_candidate(candidates, audit_record.get("created_at"))
                _append_timestamp_candidate(candidates, audit_record.get("updated_at"))

    retry_state = payload.get("retry_state")
    if isinstance(retry_state, dict):
        _append_timestamp_candidate(candidates, retry_state.get("next_retry_at"))

    lock_state = payload.get("lock_state")
    if isinstance(lock_state, dict):
        for field_name in ("acquired_at", "heartbeat_at", "expires_at"):
            _append_timestamp_candidate(candidates, lock_state.get(field_name))

    checkpoint = payload.get("checkpoint")
    if isinstance(checkpoint, dict):
        for field_name in ("started_at", "updated_at"):
            _append_timestamp_candidate(candidates, checkpoint.get(field_name))
        active_request = checkpoint.get("active_request")
        if isinstance(active_request, dict):
            _append_timestamp_candidate(candidates, active_request.get("received_at"))
            audit_record = active_request.get("audit_record")
            if isinstance(audit_record, dict):
                _append_timestamp_candidate(candidates, audit_record.get("created_at"))
                _append_timestamp_candidate(candidates, audit_record.get("updated_at"))
        for request in checkpoint.get("deferred_follow_ons", ()):
            if isinstance(request, dict):
                _append_timestamp_candidate(candidates, request.get("received_at"))
                audit_record = request.get("audit_record")
                if isinstance(audit_record, dict):
                    _append_timestamp_candidate(candidates, audit_record.get("created_at"))
                    _append_timestamp_candidate(candidates, audit_record.get("updated_at"))
        for ownership in checkpoint.get("owned_queues", ()):
            if isinstance(ownership, dict):
                _append_timestamp_candidate(candidates, ownership.get("acquired_at"))

    repaired = dict(payload)
    if candidates:
        repaired["updated_at"] = _isoformat_z(max(candidates))
    else:
        repaired["updated_at"] = _isoformat_z(
            datetime.fromtimestamp(state_path.stat().st_mtime, tz=timezone.utc)
        )
    return repaired


def load_research_runtime_state(
    state_path: Path,
    *,
    deferred_dir: Path | None = None,
) -> ResearchRuntimeState | None:
    """Load persisted research state or migrate the legacy breadcrumb queue."""

    if state_path.exists():
        raw_payload = json.loads(state_path.read_text(encoding="utf-8"))
        return ResearchRuntimeState.model_validate(
            _repair_runtime_state_payload(raw_payload, state_path=state_path)
        )
    if deferred_dir is None or not deferred_dir.exists():
        return None

    deferred_requests = tuple(
        _load_deferred_request_from_breadcrumb(path)
        for path in sorted(deferred_dir.glob("*.json"))
    )
    if not deferred_requests:
        return None

    return _default_stub_runtime_state(mode_reason="stub-plane-restored-from-breadcrumbs").model_copy(
        update={
            "updated_at": max(request.received_at for request in deferred_requests),
            "deferred_requests": deferred_requests,
        }
    )


def write_research_runtime_state(state_path: Path, state: ResearchRuntimeState) -> None:
    """Persist a deterministic research runtime snapshot."""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(state.model_dump_json(exclude_none=True))
    write_text_atomic(state_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _rebind_deferred_request(
    request: DeferredResearchRequest,
    *,
    deferred_dir: Path,
) -> DeferredResearchRequest:
    breadcrumb_path = request.breadcrumb_path
    if breadcrumb_path is None:
        return request

    rebound_path = deferred_dir / breadcrumb_path.name
    if breadcrumb_path.exists() and not rebound_path.exists():
        write_text_atomic(rebound_path, breadcrumb_path.read_text(encoding="utf-8"))
    return request.model_copy(update={"breadcrumb_path": rebound_path})


def rebind_research_runtime_state(
    state: ResearchRuntimeState,
    *,
    deferred_dir: Path,
) -> ResearchRuntimeState:
    """Re-anchor persisted breadcrumb paths under one deferred directory."""

    checkpoint = state.checkpoint
    if checkpoint is not None:
        active_request = checkpoint.active_request
        checkpoint = checkpoint.model_copy(
            update={
                "active_request": (
                    None
                    if active_request is None
                    else _rebind_deferred_request(active_request, deferred_dir=deferred_dir)
                ),
                "deferred_follow_ons": tuple(
                    _rebind_deferred_request(request, deferred_dir=deferred_dir)
                    for request in checkpoint.deferred_follow_ons
                ),
            }
        )

    return state.model_copy(
        update={
            "deferred_requests": tuple(
                _rebind_deferred_request(request, deferred_dir=deferred_dir)
                for request in state.deferred_requests
            ),
            "checkpoint": checkpoint,
        }
    )


def clear_research_runtime_lock(state: ResearchRuntimeState) -> ResearchRuntimeState:
    """Drop persisted lock metadata that cannot survive a process boundary."""

    if state.lock_state is None:
        return state
    return state.model_copy(update={"lock_state": None})


class ResearchStateStore:
    """Persistence seam for the research plane runtime snapshot."""

    def __init__(self, state_path: Path, *, deferred_dir: Path | None = None) -> None:
        self.state_path = state_path
        self.deferred_dir = deferred_dir

    def load(self) -> ResearchRuntimeState | None:
        return load_research_runtime_state(self.state_path, deferred_dir=self.deferred_dir)

    def save(self, state: ResearchRuntimeState) -> None:
        write_research_runtime_state(self.state_path, state)

    def bootstrap(self, *, mode_reason: str = "stub-plane-initialized") -> ResearchRuntimeState:
        state = self.load()
        if state is None:
            return _default_stub_runtime_state(mode_reason=mode_reason)
        state = clear_research_runtime_lock(state)
        self.save(state)
        if not self.state_path.exists():
            self.save(state)
        return state


__all__ = [
    "DeferredResearchRequest",
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
    "clear_research_runtime_lock",
    "load_research_runtime_state",
    "rebind_research_runtime_state",
    "write_research_runtime_state",
]
