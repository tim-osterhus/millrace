"""Queue mutation and lifecycle transition helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import (
    IncidentDocument,
    LearningRequestDocument,
    ProbeDocument,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError

from .paths import WorkspacePaths
from .work_item_adapters import (
    adapter_for_kind,
    enqueue_with_adapter,
    move_active_with_adapter,
    move_with_adapter,
)


def enqueue_task(paths: WorkspacePaths, doc: TaskDocument) -> Path:
    return enqueue_with_adapter(paths, adapter_for_kind(WorkItemKind.TASK), doc)


def enqueue_spec(paths: WorkspacePaths, doc: SpecDocument) -> Path:
    return enqueue_with_adapter(paths, adapter_for_kind(WorkItemKind.SPEC), doc)


def enqueue_probe(paths: WorkspacePaths, doc: ProbeDocument) -> Path:
    return enqueue_with_adapter(paths, adapter_for_kind(WorkItemKind.PROBE), doc)


def enqueue_incident(paths: WorkspacePaths, doc: IncidentDocument) -> Path:
    return enqueue_with_adapter(paths, adapter_for_kind(WorkItemKind.INCIDENT), doc)


def enqueue_learning_request(paths: WorkspacePaths, doc: LearningRequestDocument) -> Path:
    return enqueue_with_adapter(paths, adapter_for_kind(WorkItemKind.LEARNING_REQUEST), doc)


def mark_task_done(paths: WorkspacePaths, task_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.TASK),
        task_id,
        target_state="done",
    )


def mark_task_blocked(paths: WorkspacePaths, task_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.TASK),
        task_id,
        target_state="blocked",
    )


def mark_spec_done(paths: WorkspacePaths, spec_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.SPEC),
        spec_id,
        target_state="done",
    )


def mark_spec_blocked(paths: WorkspacePaths, spec_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.SPEC),
        spec_id,
        target_state="blocked",
    )


def mark_probe_done(paths: WorkspacePaths, probe_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.PROBE),
        probe_id,
        target_state="done",
    )


def mark_probe_blocked(paths: WorkspacePaths, probe_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.PROBE),
        probe_id,
        target_state="blocked",
    )


def mark_incident_resolved(paths: WorkspacePaths, incident_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.INCIDENT),
        incident_id,
        target_state="done",
    )


def mark_incident_blocked(paths: WorkspacePaths, incident_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.INCIDENT),
        incident_id,
        target_state="blocked",
    )


def mark_learning_request_done(paths: WorkspacePaths, learning_request_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.LEARNING_REQUEST),
        learning_request_id,
        target_state="done",
    )


def mark_learning_request_blocked(paths: WorkspacePaths, learning_request_id: str) -> Path:
    return move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.LEARNING_REQUEST),
        learning_request_id,
        target_state="blocked",
    )


def requeue_task(paths: WorkspacePaths, task_id: str, *, reason: str) -> Path:
    destination = move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.TASK),
        task_id,
        target_state="queue",
    )
    _append_requeue_reason(paths.tasks_queue_dir, task_id, WorkItemKind.TASK, reason)
    return destination


def requeue_blocked_task(
    paths: WorkspacePaths,
    task_id: str,
    *,
    reason: str,
    actor: str,
    auto: bool,
    failure_class: str | None = None,
    attempt_number: int | None = None,
) -> Path:
    adapter = adapter_for_kind(WorkItemKind.TASK)
    destination = move_with_adapter(
        adapter,
        source_dir=paths.tasks_blocked_dir,
        destination_dir=paths.tasks_queue_dir,
        item_id=task_id,
        source_state="blocked",
    )
    _append_requeue_reason(
        paths.tasks_queue_dir,
        task_id,
        WorkItemKind.TASK,
        reason,
        actor=actor,
        auto=auto,
        source_state="blocked",
        destination_state="queue",
        failure_class=failure_class,
        attempt_number=attempt_number,
    )
    return destination


def requeue_blocked_work_item(
    paths: WorkspacePaths,
    *,
    work_item_family_id: str,
    work_item_kind: WorkItemKind | None,
    work_item_id: str,
    blocked_dir: Path,
    queue_dir: Path,
    file_extension: str,
    reason: str,
    actor: str,
    auto: bool,
    failure_class: str | None = None,
    attempt_number: int | None = None,
) -> Path:
    source = blocked_dir / f"{work_item_id}{file_extension}"
    if not source.exists():
        raise QueueStateError(f"{work_item_family_id} {work_item_id} is not blocked")
    destination = queue_dir / source.name
    if destination.exists():
        raise QueueStateError(f"{work_item_family_id} {work_item_id} already exists at destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)
    _append_requeue_reason(
        queue_dir,
        work_item_id,
        work_item_kind,
        reason,
        actor=actor,
        auto=auto,
        source_state="blocked",
        destination_state="queue",
        failure_class=failure_class,
        attempt_number=attempt_number,
        work_item_family_id=work_item_family_id,
        work_item_id=work_item_id,
    )
    return destination


def requeue_spec(paths: WorkspacePaths, spec_id: str, *, reason: str) -> Path:
    destination = move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.SPEC),
        spec_id,
        target_state="queue",
    )
    _append_requeue_reason(paths.specs_queue_dir, spec_id, WorkItemKind.SPEC, reason)
    return destination


def requeue_probe(paths: WorkspacePaths, probe_id: str, *, reason: str) -> Path:
    destination = move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.PROBE),
        probe_id,
        target_state="queue",
    )
    _append_requeue_reason(paths.probes_queue_dir, probe_id, WorkItemKind.PROBE, reason)
    return destination


def requeue_incident(paths: WorkspacePaths, incident_id: str, *, reason: str) -> Path:
    destination = move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.INCIDENT),
        incident_id,
        target_state="queue",
    )
    _append_requeue_reason(paths.incidents_incoming_dir, incident_id, WorkItemKind.INCIDENT, reason)
    return destination


def requeue_learning_request(paths: WorkspacePaths, learning_request_id: str, *, reason: str) -> Path:
    destination = move_active_with_adapter(
        paths,
        adapter_for_kind(WorkItemKind.LEARNING_REQUEST),
        learning_request_id,
        target_state="queue",
    )
    _append_requeue_reason(
        paths.learning_requests_queue_dir,
        learning_request_id,
        WorkItemKind.LEARNING_REQUEST,
        reason,
    )
    return destination


def _append_requeue_reason(
    destination_dir: Path,
    item_id: str,
    kind: WorkItemKind | None,
    reason: str,
    *,
    actor: str | None = None,
    auto: bool | None = None,
    source_state: str | None = None,
    destination_state: str | None = None,
    failure_class: str | None = None,
    attempt_number: int | None = None,
    work_item_family_id: str | None = None,
    work_item_id: str | None = None,
) -> None:
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise QueueStateError("requeue reason is required")

    log_path = destination_dir / f"{item_id}.requeue.jsonl"
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "reason": cleaned_reason,
    }
    if kind is not None:
        payload["kind"] = kind.value
    if work_item_family_id is not None:
        payload["work_item_family_id"] = work_item_family_id
    if work_item_id is not None:
        payload["work_item_id"] = work_item_id
    if actor is not None:
        payload["actor"] = actor
    if auto is not None:
        payload["auto"] = auto
    if source_state is not None:
        payload["source_state"] = source_state
    if destination_state is not None:
        payload["destination_state"] = destination_state
    if failure_class is not None:
        payload["failure_class"] = failure_class
    if attempt_number is not None:
        payload["attempt_number"] = attempt_number
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
__all__ = [
    "enqueue_incident",
    "enqueue_learning_request",
    "enqueue_probe",
    "enqueue_spec",
    "enqueue_task",
    "mark_incident_blocked",
    "mark_incident_resolved",
    "mark_learning_request_blocked",
    "mark_learning_request_done",
    "mark_probe_blocked",
    "mark_probe_done",
    "mark_spec_blocked",
    "mark_spec_done",
    "mark_task_blocked",
    "mark_task_done",
    "requeue_blocked_task",
    "requeue_blocked_work_item",
    "requeue_incident",
    "requeue_learning_request",
    "requeue_probe",
    "requeue_spec",
    "requeue_task",
]
