"""Deferred-request and handoff helpers for the research supervisor."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from ..contracts import ExecutionResearchHandoff, ResearchMode
from ..events import EventRecord, EventType
from ..markdown import write_text_atomic
from ..queue import load_research_recovery_latch
from ..run_ids import stable_slug
from .audit import AuditTrigger, ensure_backlog_empty_audit_ticket, load_audit_queue_record
from .state import DeferredResearchRequest, ResearchCheckpoint, ResearchQueueFamily
from .supervisor_payloads import research_deferred_payload

def breadcrumb_name(self: Any, received_at: datetime, event_type: EventType) -> str:
    timestamp = received_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{timestamp}__{stable_slug(event_type.value, fallback='event')}.json"


def breadcrumb_path(self: Any, request: DeferredResearchRequest) -> Path:
    base_name = self._breadcrumb_name(request.received_at, request.event_type)
    path = self.paths.deferred_dir / base_name
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem}__{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def write_breadcrumb(self: Any, request: DeferredResearchRequest) -> Path:
    self.paths.deferred_dir.mkdir(parents=True, exist_ok=True)
    path = self._breadcrumb_path(request)
    write_text_atomic(
        path,
        json.dumps(
            {
                "event_type": request.event_type.value,
                "received_at": request.received_at.isoformat().replace("+00:00", "Z"),
                "payload": request.payload,
                **({"queue_family": request.queue_family.value} if request.queue_family is not None else {}),
                **({"handoff": request.handoff.model_dump(mode="json")} if request.handoff is not None else {}),
                **(
                    {"audit_record": request.audit_record.model_dump(mode="json")}
                    if request.audit_record is not None
                    else {}
                ),
                "status": "deferred",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return path


def enqueue_request(self: Any, request: DeferredResearchRequest) -> DeferredResearchRequest:
    breadcrumb_path: Path | None = None
    if self.config.research.mode is ResearchMode.STUB:
        breadcrumb_path = self._write_breadcrumb(request)
    persisted_request = request.model_copy(update={"breadcrumb_path": breadcrumb_path})
    self.state = self.state.model_copy(
        update={
            "updated_at": persisted_request.received_at,
            "deferred_requests": self.state.deferred_requests + (persisted_request,),
        }
    )
    self._persist_state()
    self._emit(
        EventType.RESEARCH_DEFERRED,
        research_deferred_payload(
            request,
            breadcrumb_path=breadcrumb_path,
            pending_count=len(self.state.deferred_requests),
        ),
    )
    return persisted_request


def claim_deferred_request(
    self: Any,
    selected_family: ResearchQueueFamily | None,
) -> tuple[DeferredResearchRequest | None, tuple[DeferredResearchRequest, ...]]:
    if selected_family is None:
        return None, self.state.deferred_requests

    active_request: DeferredResearchRequest | None = None
    remaining: list[DeferredResearchRequest] = []
    for request in self.state.deferred_requests:
        if active_request is None and request.queue_family is selected_family:
            active_request = request
            continue
        remaining.append(request)
    return active_request, tuple(remaining)


def synthetic_blocker_request(
    self: Any,
    handoff: ExecutionResearchHandoff,
    *,
    received_at: datetime,
) -> DeferredResearchRequest:
    return DeferredResearchRequest.model_validate(
        {
            "event_type": EventType.NEEDS_RESEARCH,
            "received_at": received_at,
            "payload": {
                "task_id": handoff.task_id,
                "title": handoff.task_title,
            },
            "queue_family": ResearchQueueFamily.BLOCKER,
            "handoff": handoff,
        }
    )


def request_task_id(self: Any, request: DeferredResearchRequest) -> str | None:
    if request.handoff is not None:
        return request.handoff.task_id
    task_id = request.payload.get("task_id")
    if task_id is None:
        return None
    normalized = str(task_id).strip()
    return normalized or None


def handoffs_match(self: Any, left: ExecutionResearchHandoff, right: ExecutionResearchHandoff) -> bool:
    left_batch_id = left.recovery_batch_id
    right_batch_id = right.recovery_batch_id
    if left_batch_id is not None and right_batch_id is not None:
        return left_batch_id == right_batch_id
    return left.task_id == right.task_id


def request_matches_handoff(
    self: Any,
    request: DeferredResearchRequest,
    handoff: ExecutionResearchHandoff,
) -> bool:
    request_handoff = request.handoff
    if request_handoff is not None:
        return self._handoffs_match(request_handoff, handoff)
    return self._request_task_id(request) == handoff.task_id


def claim_blocker_request_from_latch(
    self: Any,
    handoff: ExecutionResearchHandoff,
    *,
    received_at: datetime,
) -> tuple[DeferredResearchRequest, tuple[DeferredResearchRequest, ...]]:
    blocker_requests: list[DeferredResearchRequest] = []
    remaining: list[DeferredResearchRequest] = []
    for request in self.state.deferred_requests:
        if request.queue_family is ResearchQueueFamily.BLOCKER:
            blocker_requests.append(request)
            continue
        remaining.append(request)

    active_request: DeferredResearchRequest | None = None
    selected_request: DeferredResearchRequest | None = None
    for request in blocker_requests:
        if self._request_matches_handoff(request, handoff):
            selected_request = request
            active_request = request
            break
    if active_request is None:
        active_request = self._synthetic_blocker_request(handoff, received_at=received_at)
    else:
        active_request = active_request.model_copy(
            update={
                "queue_family": ResearchQueueFamily.BLOCKER,
                "handoff": handoff,
            }
        )

    remaining.extend(request for request in blocker_requests if request is not selected_request)
    return active_request, tuple(remaining)


def resume_checkpoint_handoff(self: Any, checkpoint: ResearchCheckpoint) -> ResearchCheckpoint:
    if self._resume_selected_family(checkpoint) is not ResearchQueueFamily.BLOCKER:
        return checkpoint

    handoff = self._latch_handoff()
    if handoff is None:
        return checkpoint

    active_request = checkpoint.active_request
    active_request_handoff = None if active_request is None else active_request.handoff
    canonical_handoff = checkpoint.parent_handoff or active_request_handoff
    if canonical_handoff is None:
        canonical_handoff = handoff
    elif not self._handoffs_match(canonical_handoff, handoff):
        return checkpoint

    if active_request is None:
        active_request = self._synthetic_blocker_request(canonical_handoff, received_at=checkpoint.started_at)
    elif active_request.handoff is None and self._request_matches_handoff(active_request, canonical_handoff):
        active_request = active_request.model_copy(
            update={
                "queue_family": ResearchQueueFamily.BLOCKER,
                "handoff": canonical_handoff,
            }
        )
    elif active_request.handoff is not None and not self._handoffs_match(
        active_request.handoff,
        canonical_handoff,
    ):
        return checkpoint

    return checkpoint.model_copy(
        update={
            "active_request": active_request,
            "parent_handoff": canonical_handoff,
        }
    )


def claim_dispatch_request(
    self: Any,
    selected_family: ResearchQueueFamily | None,
    *,
    discovery: Any,
) -> tuple[DeferredResearchRequest | None, tuple[DeferredResearchRequest, ...], ExecutionResearchHandoff | None]:
    active_request, remaining = self._claim_deferred_request(selected_family)
    parent_handoff = None if active_request is None else active_request.handoff
    latch = load_research_recovery_latch(self.paths.research_recovery_latch_file)
    latch_handoff = None if latch is None else latch.handoff

    if selected_family is not ResearchQueueFamily.BLOCKER:
        incident_item = (
            None
            if selected_family is not ResearchQueueFamily.INCIDENT
            else discovery.family_scan(ResearchQueueFamily.INCIDENT).first_item
        )
        if (
            selected_family is ResearchQueueFamily.INCIDENT
            and latch is not None
            and latch_handoff is not None
            and incident_item is not None
            and incident_item.item_path is not None
            and self._incident_path_matches_handoff(incident_item.item_path, latch_handoff)
        ):
            active_request, remaining = self._claim_blocker_request_from_latch(
                latch_handoff,
                received_at=latch.frozen_at,
            )
            parent_handoff = (
                active_request.handoff
                if active_request is not None and active_request.handoff is not None
                else latch_handoff
            )
        return active_request, remaining, parent_handoff

    if latch_handoff is not None:
        active_request, remaining = self._claim_blocker_request_from_latch(
            latch_handoff,
            received_at=latch.frozen_at,
        )
    parent_handoff = (
        active_request.handoff if active_request is not None and active_request.handoff is not None else latch_handoff
    )
    return active_request, remaining, parent_handoff


def incident_path_matches_handoff(
    self: Any,
    incident_path: Path,
    handoff: ExecutionResearchHandoff,
) -> bool:
    handoff_path = handoff.incident_path
    if handoff_path is None:
        return False
    return self._paths_match(incident_path, handoff_path)


def paths_match(self: Any, left: Path, right: Path) -> bool:
    if not left.is_absolute():
        left = self.paths.root / left
    if not right.is_absolute():
        right = self.paths.root / right
    return left == right


def bind_queue_context_to_request(
    self: Any,
    *,
    active_request: DeferredResearchRequest | None,
    parent_handoff: ExecutionResearchHandoff | None,
    discovery: Any,
    selected_family: ResearchQueueFamily | None,
    observed_at: datetime,
) -> tuple[DeferredResearchRequest | None, ExecutionResearchHandoff | None]:
    if selected_family is None:
        return active_request, parent_handoff
    item = discovery.family_scan(selected_family).first_item
    if item is None:
        return active_request, parent_handoff

    if selected_family is ResearchQueueFamily.BLOCKER:
        blocker_record = item.blocker_record
        if blocker_record is None:
            return active_request, parent_handoff
        if active_request is None:
            payload: dict[str, object] = {}
            if blocker_record.source_task is not None:
                payload["task_id"] = blocker_record.source_task
            if blocker_record.incident_path is not None:
                payload["path"] = blocker_record.incident_path.as_posix()
            active_request = DeferredResearchRequest.model_validate(
                {
                    "event_type": EventType.NEEDS_RESEARCH,
                    "received_at": blocker_record.occurred_at or observed_at,
                    "payload": payload,
                    "queue_family": ResearchQueueFamily.BLOCKER,
                    "blocker_record": blocker_record,
                }
            )
        else:
            payload = dict(active_request.payload)
            if blocker_record.incident_path is not None and "path" not in payload:
                payload["path"] = blocker_record.incident_path.as_posix()
            active_request = active_request.model_copy(
                update={
                    "payload": payload,
                    "blocker_record": (
                        blocker_record if active_request.blocker_record is None else active_request.blocker_record
                    ),
                }
            )
        if parent_handoff is None and active_request.handoff is not None:
            parent_handoff = active_request.handoff
        return active_request, parent_handoff

    if selected_family is ResearchQueueFamily.AUDIT:
        audit_record = item.audit_record
        if audit_record is None:
            return active_request, parent_handoff
        if active_request is None:
            payload: dict[str, object] = {}
            if item.item_path is not None:
                payload["path"] = item.item_path.as_posix()
            active_request = DeferredResearchRequest.model_validate(
                {
                    "event_type": (
                        EventType.BACKLOG_EMPTY_AUDIT
                        if audit_record.trigger is AuditTrigger.QUEUE_EMPTY
                        else EventType.AUDIT_REQUESTED
                    ),
                    "received_at": audit_record.created_at or observed_at,
                    "payload": payload,
                    "queue_family": ResearchQueueFamily.AUDIT,
                    "audit_record": audit_record,
                }
            )
            return active_request, parent_handoff
        payload = dict(active_request.payload)
        if item.item_path is not None and "path" not in payload:
            payload["path"] = item.item_path.as_posix()
        update: dict[str, object] = {"payload": payload}
        if active_request.queue_family is None:
            update["queue_family"] = ResearchQueueFamily.AUDIT
        if active_request.audit_record is None:
            update["audit_record"] = audit_record
        active_request = active_request.model_copy(update=update)
        return active_request, parent_handoff

    if selected_family is ResearchQueueFamily.INCIDENT and item.item_path is not None and active_request is not None:
        payload = dict(active_request.payload)
        payload.setdefault("path", item.item_path.as_posix())
        update: dict[str, object] = {"payload": payload}
        blocker_item = discovery.family_scan(ResearchQueueFamily.BLOCKER).first_item
        if (
            blocker_item is not None
            and blocker_item.blocker_record is not None
            and blocker_item.incident_path is not None
            and self._paths_match(item.item_path, blocker_item.incident_path)
            and active_request.blocker_record is None
        ):
            update["blocker_record"] = blocker_item.blocker_record
        if active_request.queue_family in {None, ResearchQueueFamily.INCIDENT} and active_request.incident_document is None:
            update["incident_document"] = item.incident_document
            if active_request.queue_family is None:
                update["queue_family"] = ResearchQueueFamily.INCIDENT
        active_request = active_request.model_copy(update=update)
    return active_request, parent_handoff


def handoff_from_event(self: Any, event: EventRecord) -> ExecutionResearchHandoff | None:
    raw_handoff = event.payload.get("handoff")
    if raw_handoff is not None:
        return ExecutionResearchHandoff.model_validate(raw_handoff)
    if event.type is not EventType.NEEDS_RESEARCH:
        return None
    return self._latch_handoff()


def latch_handoff(self: Any) -> ExecutionResearchHandoff | None:
    latch = load_research_recovery_latch(self.paths.research_recovery_latch_file)
    if latch is None:
        return None
    return latch.handoff


def audit_record_from_event(self: Any, event: EventRecord) -> Any:
    if event.type not in {EventType.BACKLOG_EMPTY_AUDIT, EventType.AUDIT_REQUESTED}:
        return None
    raw_path = event.payload.get("path")
    if raw_path is not None:
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = self.paths.root / path
        return load_audit_queue_record(path)
    if event.type is EventType.AUDIT_REQUESTED:
        return None
    raw_backlog_depth = event.payload.get("backlog_depth", 0)
    try:
        backlog_depth = int(raw_backlog_depth)
    except (TypeError, ValueError):
        backlog_depth = 0
    return ensure_backlog_empty_audit_ticket(
        self.paths,
        observed_at=event.timestamp,
        backlog_depth=backlog_depth,
    )
