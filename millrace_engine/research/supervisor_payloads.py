"""Event and report payload helpers for the research supervisor."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..research.state import ResearchQueueFamily


def discovery_payload(
    plane: Any,
    discovery: Any,
    *,
    observed_at: datetime,
    selected_family: ResearchQueueFamily | None = None,
) -> dict[str, Any]:
    return {
        "scanned_at": observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "configured_mode": plane.config.research.mode.value,
        "ready_families": [scan.family.value for scan in discovery.families if scan.ready],
        "family_counts": {scan.family.value: len(scan.items) for scan in discovery.families},
        "selected_family": None if selected_family is None else selected_family.value,
    }


def research_deferred_payload(
    request: Any,
    *,
    breadcrumb_path: Path | None,
    pending_count: int,
) -> dict[str, Any]:
    return {
        "source_event": request.event_type.value,
        "breadcrumb_path": breadcrumb_path,
        "storage_kind": "breadcrumb+state" if breadcrumb_path is not None else "state_only",
        "pending_count": pending_count,
        "queue_family": None if request.queue_family is None else request.queue_family.value,
        "handoff_id": None if request.handoff is None else request.handoff.handoff_id,
        "parent_run_id": (
            None
            if request.handoff is None or request.handoff.parent_run is None
            else request.handoff.parent_run.run_id
        ),
    }


def checkpoint_resumed_payload(
    plane: Any,
    checkpoint: Any,
    *,
    queue_snapshot: Any,
    reason: str,
) -> dict[str, Any]:
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "configured_mode": plane.config.research.mode.value,
        "runtime_mode": checkpoint.mode.value,
        "reason": reason,
        "status": checkpoint.status.value,
        "selected_family": (
            None if queue_snapshot.selected_family is None else queue_snapshot.selected_family.value
        ),
        "owned_queues": [item.model_dump(mode="json") for item in checkpoint.owned_queues],
        "active_request_event": (
            None if checkpoint.active_request is None else checkpoint.active_request.event_type.value
        ),
        "parent_handoff": (
            None if checkpoint.parent_handoff is None else checkpoint.parent_handoff.model_dump(mode="json")
        ),
    }


def lock_payload(lock_state: Any) -> dict[str, Any]:
    return {
        "lock_key": lock_state.lock_key,
        "owner_id": lock_state.owner_id,
        "scope": lock_state.scope.value,
        "lock_path": lock_state.lock_path,
    }


def blocked_payload(
    plane: Any,
    *,
    queue_snapshot: Any,
    checkpoint: Any,
    reason: str,
    failure_kind: str,
) -> dict[str, Any]:
    return {
        "configured_mode": plane.config.research.mode.value,
        "current_mode": plane.state.current_mode.value,
        "reason": reason,
        "failure_kind": failure_kind,
        "selected_family": (
            None if queue_snapshot.selected_family is None else queue_snapshot.selected_family.value
        ),
        "owned_queues": (
            [] if checkpoint is None else [item.model_dump(mode="json") for item in checkpoint.owned_queues]
        ),
        "next_poll_at": plane.state.next_poll_at,
    }


def retry_scheduled_payload(retry_state: Any) -> dict[str, Any]:
    return {
        "attempt": retry_state.attempt,
        "max_attempts": retry_state.max_attempts,
        "backoff_seconds": retry_state.backoff_seconds,
        "next_retry_at": retry_state.next_retry_at,
        "exhausted": retry_state.exhausted(),
        "failure_signature": retry_state.last_failure_signature,
    }


def idle_payload(
    plane: Any,
    discovery: Any,
    *,
    observed_at: datetime,
    reason: str,
) -> dict[str, Any]:
    return {
        **discovery_payload(plane, discovery, observed_at=observed_at),
        "current_mode": plane.state.current_mode.value,
        "reason": reason,
        "deferred_request_count": len(plane.state.deferred_requests),
        "next_poll_at": plane.state.next_poll_at,
    }
