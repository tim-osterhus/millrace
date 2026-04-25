"""Queue mutation and lifecycle transition helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from millrace_ai.contracts import IncidentDocument, LearningRequestDocument, SpecDocument, TaskDocument, WorkItemKind
from millrace_ai.errors import QueueStateError

from .paths import WorkspacePaths
from .work_documents import render_work_document

_DocT = TypeVar("_DocT", TaskDocument, SpecDocument, IncidentDocument, LearningRequestDocument)


def enqueue_task(paths: WorkspacePaths, doc: TaskDocument) -> Path:
    _ensure_unique_task_id(paths, doc.task_id)
    destination = paths.tasks_queue_dir / f"{doc.task_id}.md"
    _write_model(destination, doc)
    return destination


def enqueue_spec(paths: WorkspacePaths, doc: SpecDocument) -> Path:
    _ensure_unique_spec_id(paths, doc.spec_id)
    destination = paths.specs_queue_dir / f"{doc.spec_id}.md"
    _write_model(destination, doc)
    return destination


def enqueue_incident(paths: WorkspacePaths, doc: IncidentDocument) -> Path:
    _ensure_unique_incident_id(paths, doc.incident_id)
    destination = paths.incidents_incoming_dir / f"{doc.incident_id}.md"
    _write_model(destination, doc)
    return destination


def enqueue_learning_request(paths: WorkspacePaths, doc: LearningRequestDocument) -> Path:
    _ensure_unique_learning_request_id(paths, doc.learning_request_id)
    destination = paths.learning_requests_queue_dir / f"{doc.learning_request_id}.md"
    _write_model(destination, doc)
    return destination


def mark_task_done(paths: WorkspacePaths, task_id: str) -> Path:
    return _move_item(
        source_dir=paths.tasks_active_dir,
        destination_dir=paths.tasks_done_dir,
        item_id=task_id,
        kind=WorkItemKind.TASK,
    )


def mark_task_blocked(paths: WorkspacePaths, task_id: str) -> Path:
    return _move_item(
        source_dir=paths.tasks_active_dir,
        destination_dir=paths.tasks_blocked_dir,
        item_id=task_id,
        kind=WorkItemKind.TASK,
    )


def mark_spec_done(paths: WorkspacePaths, spec_id: str) -> Path:
    return _move_item(
        source_dir=paths.specs_active_dir,
        destination_dir=paths.specs_done_dir,
        item_id=spec_id,
        kind=WorkItemKind.SPEC,
    )


def mark_spec_blocked(paths: WorkspacePaths, spec_id: str) -> Path:
    return _move_item(
        source_dir=paths.specs_active_dir,
        destination_dir=paths.specs_blocked_dir,
        item_id=spec_id,
        kind=WorkItemKind.SPEC,
    )


def mark_incident_resolved(paths: WorkspacePaths, incident_id: str) -> Path:
    return _move_item(
        source_dir=paths.incidents_active_dir,
        destination_dir=paths.incidents_resolved_dir,
        item_id=incident_id,
        kind=WorkItemKind.INCIDENT,
    )


def mark_incident_blocked(paths: WorkspacePaths, incident_id: str) -> Path:
    return _move_item(
        source_dir=paths.incidents_active_dir,
        destination_dir=paths.incidents_blocked_dir,
        item_id=incident_id,
        kind=WorkItemKind.INCIDENT,
    )


def mark_learning_request_done(paths: WorkspacePaths, learning_request_id: str) -> Path:
    return _move_item(
        source_dir=paths.learning_requests_active_dir,
        destination_dir=paths.learning_requests_done_dir,
        item_id=learning_request_id,
        kind=WorkItemKind.LEARNING_REQUEST,
    )


def mark_learning_request_blocked(paths: WorkspacePaths, learning_request_id: str) -> Path:
    return _move_item(
        source_dir=paths.learning_requests_active_dir,
        destination_dir=paths.learning_requests_blocked_dir,
        item_id=learning_request_id,
        kind=WorkItemKind.LEARNING_REQUEST,
    )


def requeue_task(paths: WorkspacePaths, task_id: str, *, reason: str) -> Path:
    destination = _move_item(
        source_dir=paths.tasks_active_dir,
        destination_dir=paths.tasks_queue_dir,
        item_id=task_id,
        kind=WorkItemKind.TASK,
    )
    _append_requeue_reason(paths.tasks_queue_dir, task_id, WorkItemKind.TASK, reason)
    return destination


def requeue_spec(paths: WorkspacePaths, spec_id: str, *, reason: str) -> Path:
    destination = _move_item(
        source_dir=paths.specs_active_dir,
        destination_dir=paths.specs_queue_dir,
        item_id=spec_id,
        kind=WorkItemKind.SPEC,
    )
    _append_requeue_reason(paths.specs_queue_dir, spec_id, WorkItemKind.SPEC, reason)
    return destination


def requeue_incident(paths: WorkspacePaths, incident_id: str, *, reason: str) -> Path:
    destination = _move_item(
        source_dir=paths.incidents_active_dir,
        destination_dir=paths.incidents_incoming_dir,
        item_id=incident_id,
        kind=WorkItemKind.INCIDENT,
    )
    _append_requeue_reason(paths.incidents_incoming_dir, incident_id, WorkItemKind.INCIDENT, reason)
    return destination


def requeue_learning_request(paths: WorkspacePaths, learning_request_id: str, *, reason: str) -> Path:
    destination = _move_item(
        source_dir=paths.learning_requests_active_dir,
        destination_dir=paths.learning_requests_queue_dir,
        item_id=learning_request_id,
        kind=WorkItemKind.LEARNING_REQUEST,
    )
    _append_requeue_reason(
        paths.learning_requests_queue_dir,
        learning_request_id,
        WorkItemKind.LEARNING_REQUEST,
        reason,
    )
    return destination


def _move_item(
    *,
    source_dir: Path,
    destination_dir: Path,
    item_id: str,
    kind: WorkItemKind,
) -> Path:
    source = source_dir / f"{item_id}.md"
    if not source.exists():
        raise QueueStateError(f"{kind.value} {item_id} is not active")

    destination = destination_dir / source.name
    if destination.exists():
        raise QueueStateError(f"{kind.value} {item_id} already exists at destination")

    source.replace(destination)
    return destination


def _append_requeue_reason(
    destination_dir: Path,
    item_id: str,
    kind: WorkItemKind,
    reason: str,
) -> None:
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise QueueStateError("requeue reason is required")

    log_path = destination_dir / f"{item_id}.requeue.jsonl"
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind.value,
        "reason": cleaned_reason,
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


def _ensure_unique_task_id(paths: WorkspacePaths, task_id: str) -> None:
    _ensure_unique_id(
        work_item_id=task_id,
        directories=(
            paths.tasks_queue_dir,
            paths.tasks_active_dir,
            paths.tasks_done_dir,
            paths.tasks_blocked_dir,
        ),
        kind=WorkItemKind.TASK,
    )


def _ensure_unique_spec_id(paths: WorkspacePaths, spec_id: str) -> None:
    _ensure_unique_id(
        work_item_id=spec_id,
        directories=(
            paths.specs_queue_dir,
            paths.specs_active_dir,
            paths.specs_done_dir,
            paths.specs_blocked_dir,
        ),
        kind=WorkItemKind.SPEC,
    )


def _ensure_unique_incident_id(paths: WorkspacePaths, incident_id: str) -> None:
    _ensure_unique_id(
        work_item_id=incident_id,
        directories=(
            paths.incidents_incoming_dir,
            paths.incidents_active_dir,
            paths.incidents_resolved_dir,
            paths.incidents_blocked_dir,
        ),
        kind=WorkItemKind.INCIDENT,
    )


def _ensure_unique_learning_request_id(paths: WorkspacePaths, learning_request_id: str) -> None:
    _ensure_unique_id(
        work_item_id=learning_request_id,
        directories=(
            paths.learning_requests_queue_dir,
            paths.learning_requests_active_dir,
            paths.learning_requests_done_dir,
            paths.learning_requests_blocked_dir,
        ),
        kind=WorkItemKind.LEARNING_REQUEST,
    )


def _ensure_unique_id(
    *,
    work_item_id: str,
    directories: tuple[Path, ...],
    kind: WorkItemKind,
) -> None:
    filename = f"{work_item_id}.md"
    for directory in directories:
        if (directory / filename).exists():
            raise QueueStateError(f"{kind.value} {work_item_id} already exists")


def _write_model(destination: Path, document: _DocT) -> None:
    destination.write_text(render_work_document(document), encoding="utf-8")


__all__ = [
    "enqueue_incident",
    "enqueue_learning_request",
    "enqueue_spec",
    "enqueue_task",
    "mark_incident_blocked",
    "mark_incident_resolved",
    "mark_learning_request_blocked",
    "mark_learning_request_done",
    "mark_spec_blocked",
    "mark_spec_done",
    "mark_task_blocked",
    "mark_task_done",
    "requeue_incident",
    "requeue_learning_request",
    "requeue_spec",
    "requeue_task",
]
