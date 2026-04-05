"""Persistence helpers for the research runtime state facade."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from ..contracts import _normalize_datetime
from ..markdown import write_text_atomic


def _isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_stub_runtime_state(*, mode_reason: str):
    from .state import ResearchRuntimeMode, ResearchRuntimeState

    return ResearchRuntimeState(
        current_mode=ResearchRuntimeMode.STUB,
        last_mode=ResearchRuntimeMode.STUB,
        mode_reason=mode_reason,
    )


def _load_deferred_request_from_breadcrumb(path: Path):
    from .state import DeferredResearchRequest

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
):
    """Load persisted research state or migrate the legacy breadcrumb queue."""

    from .state import ResearchRuntimeState

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


def write_research_runtime_state(state_path: Path, state) -> None:
    """Persist a deterministic research runtime snapshot."""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(state.model_dump_json(exclude_none=True))
    write_text_atomic(state_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _rebind_deferred_request(request, *, deferred_dir: Path):
    breadcrumb_path = request.breadcrumb_path
    if breadcrumb_path is None:
        return request

    rebound_path = deferred_dir / breadcrumb_path.name
    if breadcrumb_path.exists() and not rebound_path.exists():
        write_text_atomic(rebound_path, breadcrumb_path.read_text(encoding="utf-8"))
    return request.model_copy(update={"breadcrumb_path": rebound_path})


def rebind_research_runtime_state(state, *, deferred_dir: Path):
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


def clear_research_runtime_lock(state):
    """Drop persisted lock metadata that cannot survive a process boundary."""

    if state.lock_state is None:
        return state
    return state.model_copy(update={"lock_state": None})


class ResearchStateStore:
    """Persistence seam for the research plane runtime snapshot."""

    def __init__(self, state_path: Path, *, deferred_dir: Path | None = None) -> None:
        self.state_path = state_path
        self.deferred_dir = deferred_dir

    def load(self):
        return load_research_runtime_state(self.state_path, deferred_dir=self.deferred_dir)

    def save(self, state) -> None:
        write_research_runtime_state(self.state_path, state)

    def bootstrap(self, *, mode_reason: str = "stub-plane-initialized"):
        state = self.load()
        if state is None:
            return _default_stub_runtime_state(mode_reason=mode_reason)
        state = clear_research_runtime_lock(state)
        self.save(state)
        if not self.state_path.exists():
            self.save(state)
        return state
