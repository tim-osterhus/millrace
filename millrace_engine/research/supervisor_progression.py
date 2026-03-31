"""Checkpoint and queue-ownership progression helpers for the research supervisor."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import ResearchStatus
from ..events import EventType
from .goalspec import next_stage_for_success
from .incidents import incident_source_exists, incident_source_on_disk
from .queues import ResearchQueueDiscovery
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership
from .supervisor_payloads import checkpoint_resumed_payload


def selection_ownerships(
    self: Any,
    *,
    discovery: ResearchQueueDiscovery,
    selected_family: ResearchQueueFamily | None,
    owner_token: str,
    acquired_at: datetime,
) -> tuple[ResearchQueueOwnership, ...]:
    if selected_family is None:
        return ()
    scan = discovery.family_scan(selected_family)
    item = scan.first_item
    if item is None:
        return ()
    return (
        ResearchQueueOwnership(
            family=selected_family,
            queue_path=item.queue_path,
            item_path=item.item_path,
            owner_token=owner_token,
            acquired_at=acquired_at,
        ),
    )


def resume_selected_family(self: Any, checkpoint: ResearchCheckpoint) -> ResearchQueueFamily | None:
    if checkpoint.owned_queues:
        return checkpoint.owned_queues[0].family
    return self.state.queue_snapshot.selected_family


def persist_resume_state(
    self: Any,
    *,
    checkpoint: ResearchCheckpoint,
    observed_at: datetime,
    reason: str,
) -> None:
    self.status_store.write_raw(checkpoint.status)
    queue_snapshot = self.state.queue_snapshot.model_copy(
        update={
            "ownerships": checkpoint.owned_queues,
            "last_scanned_at": observed_at,
            "selected_family": self._resume_selected_family(checkpoint),
        }
    )
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "mode_reason": reason,
            "queue_snapshot": queue_snapshot,
            "retry_state": None,
            "checkpoint": checkpoint.model_copy(update={"updated_at": observed_at}),
            "next_poll_at": None,
        }
    )
    self._persist_state()
    self._emit(
        EventType.RESEARCH_CHECKPOINT_RESUMED,
        checkpoint_resumed_payload(self, checkpoint, queue_snapshot=queue_snapshot, reason=reason),
    )


def supports_goalspec_stage_execution(self: Any, checkpoint: ResearchCheckpoint | None) -> bool:
    if checkpoint is None:
        return False
    if self._resume_selected_family(checkpoint) is not ResearchQueueFamily.GOALSPEC:
        return False
    return checkpoint.node_id in {
        "goal_intake",
        "objective_profile_sync",
        "spec_synthesis",
        "spec_review",
        "taskmaster",
    }


def supports_incident_stage_execution(self: Any, checkpoint: ResearchCheckpoint | None) -> bool:
    if checkpoint is None:
        return False
    if self._resume_selected_family(checkpoint) not in {
        ResearchQueueFamily.INCIDENT,
        ResearchQueueFamily.BLOCKER,
    }:
        return False
    source_on_disk = incident_source_on_disk(self.paths, checkpoint)
    if not source_on_disk:
        if checkpoint.node_id != "incident_intake":
            return False
        mode_reason = self.state.mode_reason or ""
        if mode_reason.startswith("resume-from-checkpoint") and self.state.retry_state is None:
            return False
    if not incident_source_exists(self.paths, checkpoint):
        return False
    return checkpoint.node_id in {
        "incident_intake",
        "incident_resolve",
        "incident_archive",
    }


def supports_audit_stage_execution(self: Any, checkpoint: ResearchCheckpoint | None) -> bool:
    if checkpoint is None:
        return False
    if self._resume_selected_family(checkpoint) is not ResearchQueueFamily.AUDIT:
        return False
    return checkpoint.node_id in {
        "audit_intake",
        "audit_validate",
        "audit_gatekeeper",
    }


def queue_ownership_for_audit_path(
    self: Any,
    *,
    audit_path: Path,
    run_id: str,
    emitted_at: datetime,
) -> ResearchQueueOwnership:
    return ResearchQueueOwnership(
        family=ResearchQueueFamily.AUDIT,
        queue_path=audit_path.parent,
        item_path=audit_path,
        owner_token=run_id,
        acquired_at=emitted_at,
    )


def next_goalspec_stage(self: Any, dispatch: Any, checkpoint: ResearchCheckpoint) -> Any:
    return next_stage_for_success(dispatch.research_plan, checkpoint.node_id)


def advance_goalspec_checkpoint(
    self: Any,
    checkpoint: ResearchCheckpoint,
    *,
    next_stage: Any,
    queue_ownership: ResearchQueueOwnership,
    observed_at: datetime,
) -> ResearchCheckpoint | None:
    if next_stage is None:
        return None
    next_status = (
        ResearchStatus(next_stage.running_status)
        if next_stage.node_id in {
            "goal_intake",
            "objective_profile_sync",
            "spec_synthesis",
            "spec_review",
            "taskmaster",
        }
        else ResearchStatus.GOALSPEC_RUNNING
    )
    updated = checkpoint.model_copy(
        update={
            "status": next_status,
            "node_id": next_stage.node_id,
            "stage_kind_id": next_stage.kind_id,
            "updated_at": observed_at,
            "owned_queues": (queue_ownership,),
        }
    )
    queue_snapshot = self.state.queue_snapshot.model_copy(
        update={
            "ownerships": updated.owned_queues,
            "last_scanned_at": observed_at,
            "selected_family": ResearchQueueFamily.GOALSPEC,
        }
    )
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "queue_snapshot": queue_snapshot,
            "retry_state": None,
            "checkpoint": updated,
        }
    )
    self._persist_state()
    self._set_research_status(next_status)
    return updated


def complete_goalspec_checkpoint(
    self: Any,
    checkpoint: ResearchCheckpoint,
    *,
    observed_at: datetime,
) -> None:
    configured_mode = self._configured_runtime_mode()
    previous_mode = self.state.current_mode
    queue_snapshot = self.state.queue_snapshot.model_copy(
        update={
            "ownerships": (),
            "last_scanned_at": observed_at,
            "selected_family": None,
        }
    )
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "current_mode": configured_mode,
            "last_mode": previous_mode,
            "queue_snapshot": queue_snapshot,
            "retry_state": None,
            "checkpoint": None,
        }
    )
    self._persist_state()
    self._set_research_status(ResearchStatus.IDLE)


def advance_audit_checkpoint(
    self: Any,
    checkpoint: ResearchCheckpoint,
    *,
    next_stage: Any,
    queue_ownership: ResearchQueueOwnership,
    audit_record: Any,
    observed_at: datetime,
) -> ResearchCheckpoint | None:
    if next_stage is None:
        return None
    next_status = (
        ResearchStatus(next_stage.running_status)
        if next_stage.node_id in {"audit_intake", "audit_validate", "audit_gatekeeper"}
        else ResearchStatus.AUDIT_RUNNING
    )
    active_request = checkpoint.active_request
    if active_request is not None:
        payload = dict(active_request.payload)
        if queue_ownership.item_path is not None:
            payload["path"] = queue_ownership.item_path.as_posix()
        active_request = active_request.model_copy(
            update={
                "payload": payload,
                "audit_record": audit_record,
                "queue_family": ResearchQueueFamily.AUDIT,
            }
        )
    updated = checkpoint.model_copy(
        update={
            "status": next_status,
            "node_id": next_stage.node_id,
            "stage_kind_id": next_stage.kind_id,
            "updated_at": observed_at,
            "owned_queues": (queue_ownership,),
            "active_request": active_request,
        }
    )
    queue_snapshot = self.state.queue_snapshot.model_copy(
        update={
            "ownerships": updated.owned_queues,
            "last_scanned_at": observed_at,
            "selected_family": ResearchQueueFamily.AUDIT,
        }
    )
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "queue_snapshot": queue_snapshot,
            "retry_state": None,
            "checkpoint": updated,
        }
    )
    self._persist_state()
    self._set_research_status(next_status)
    return updated


def complete_audit_checkpoint(
    self: Any,
    checkpoint: ResearchCheckpoint,
    *,
    final_status: ResearchStatus,
    observed_at: datetime,
) -> None:
    configured_mode = self._configured_runtime_mode()
    previous_mode = self.state.current_mode
    queue_snapshot = self.state.queue_snapshot.model_copy(
        update={
            "ownerships": (),
            "last_scanned_at": observed_at,
            "selected_family": None,
        }
    )
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "current_mode": configured_mode,
            "last_mode": previous_mode,
            "queue_snapshot": queue_snapshot,
            "retry_state": None,
            "checkpoint": None,
        }
    )
    self._persist_state()
    self._set_research_status(final_status)


def advance_incident_checkpoint(
    self: Any,
    checkpoint: ResearchCheckpoint,
    *,
    next_stage: Any,
    queue_ownership: ResearchQueueOwnership | None,
    observed_at: datetime,
) -> ResearchCheckpoint | None:
    if next_stage is None:
        return None
    next_status = (
        ResearchStatus(next_stage.running_status)
        if next_stage.node_id in {"incident_intake", "incident_resolve", "incident_archive"}
        else ResearchStatus.INCIDENT_RUNNING
    )
    updated_owned_queues = checkpoint.owned_queues
    if queue_ownership is not None:
        updated_owned_queues = (queue_ownership,)
    updated = checkpoint.model_copy(
        update={
            "status": next_status,
            "node_id": next_stage.node_id,
            "stage_kind_id": next_stage.kind_id,
            "updated_at": observed_at,
            "owned_queues": updated_owned_queues,
        }
    )
    selected_family = None if not updated.owned_queues else updated.owned_queues[0].family
    queue_snapshot = self.state.queue_snapshot.model_copy(
        update={
            "ownerships": updated.owned_queues,
            "last_scanned_at": observed_at,
            "selected_family": selected_family,
        }
    )
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "queue_snapshot": queue_snapshot,
            "retry_state": None,
            "checkpoint": updated,
        }
    )
    self._persist_state()
    self._set_research_status(next_status)
    return updated


def complete_incident_checkpoint(
    self: Any,
    checkpoint: ResearchCheckpoint,
    *,
    observed_at: datetime,
) -> None:
    configured_mode = self._configured_runtime_mode()
    previous_mode = self.state.current_mode
    queue_snapshot = self.state.queue_snapshot.model_copy(
        update={
            "ownerships": (),
            "last_scanned_at": observed_at,
            "selected_family": None,
        }
    )
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "current_mode": configured_mode,
            "last_mode": previous_mode,
            "queue_snapshot": queue_snapshot,
            "retry_state": None,
            "checkpoint": None,
        }
    )
    self._persist_state()
    self._set_research_status(ResearchStatus.IDLE)


def next_incident_stage(self: Any, dispatch: Any, checkpoint: ResearchCheckpoint) -> Any:
    return next_stage_for_success(dispatch.research_plan, checkpoint.node_id)
