"""Deterministic queue selection and lineage-scanning helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from millrace_ai.contracts import IncidentDocument, LearningRequestDocument, SpecDocument, TaskDocument, WorkItemKind
from millrace_ai.errors import QueueStateError

from .paths import WorkspacePaths
from .work_documents import parse_work_document_as

_DocT = TypeVar("_DocT", TaskDocument, SpecDocument, IncidentDocument, LearningRequestDocument)


@dataclass(frozen=True, slots=True)
class QueueClaim:
    """Represents ownership of a newly-claimed work item."""

    work_item_kind: WorkItemKind
    work_item_id: str
    path: Path


def claim_next_execution_task(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
) -> QueueClaim | None:
    active = _list_markdown_files(paths.tasks_active_dir)
    if len(active) > 1:
        raise QueueStateError("Multiple active execution tasks found")
    if active:
        return None

    while True:
        candidate = _select_oldest_eligible_task(paths, root_spec_id=root_spec_id)
        if candidate is None:
            return None

        task_id, source = candidate
        destination = paths.tasks_active_dir / source.name
        try:
            source.replace(destination)
        except FileNotFoundError:
            continue
        return QueueClaim(work_item_kind=WorkItemKind.TASK, work_item_id=task_id, path=destination)


def claim_next_planning_item(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
) -> QueueClaim | None:
    active_specs = _list_markdown_files(paths.specs_active_dir)
    active_incidents = _list_markdown_files(paths.incidents_active_dir)
    if len(active_specs) + len(active_incidents) > 1:
        raise QueueStateError("Multiple active planning items found")
    if active_specs or active_incidents:
        return None

    while True:
        incident_candidate = _select_oldest_incident(
            paths.incidents_incoming_dir,
            root_spec_id=root_spec_id,
        )
        if incident_candidate is not None:
            incident_id, source = incident_candidate
            destination = paths.incidents_active_dir / source.name
            try:
                source.replace(destination)
            except FileNotFoundError:
                continue
            return QueueClaim(
                work_item_kind=WorkItemKind.INCIDENT,
                work_item_id=incident_id,
                path=destination,
            )

        spec_candidate = _select_oldest_spec(paths.specs_queue_dir, root_spec_id=root_spec_id)
        if spec_candidate is None:
            return None

        spec_id, source = spec_candidate
        destination = paths.specs_active_dir / source.name
        try:
            source.replace(destination)
        except FileNotFoundError:
            continue
        return QueueClaim(work_item_kind=WorkItemKind.SPEC, work_item_id=spec_id, path=destination)


def claim_next_learning_request(paths: WorkspacePaths) -> QueueClaim | None:
    active = _list_markdown_files(paths.learning_requests_active_dir)
    if len(active) > 1:
        raise QueueStateError("Multiple active learning requests found")
    if active:
        return None

    while True:
        candidate = _select_oldest_learning_request(paths.learning_requests_queue_dir)
        if candidate is None:
            return None

        learning_request_id, source = candidate
        destination = paths.learning_requests_active_dir / source.name
        try:
            source.replace(destination)
        except FileNotFoundError:
            continue
        return QueueClaim(
            work_item_kind=WorkItemKind.LEARNING_REQUEST,
            work_item_id=learning_request_id,
            path=destination,
        )


def _select_oldest_task(directory: Path) -> tuple[str, Path] | None:
    return _select_oldest_document(
        directory=directory,
        model=TaskDocument,
        id_attr="task_id",
        timestamp_attr="created_at",
    )


def _select_oldest_eligible_task(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
) -> tuple[str, Path] | None:
    completed_task_ids = {path.stem for path in _list_markdown_files(paths.tasks_done_dir)}
    candidates: list[tuple[datetime, str, Path]] = []
    for path in _list_markdown_files(paths.tasks_queue_dir):
        try:
            document = parse_work_document_as(
                path.read_text(encoding="utf-8"),
                model=TaskDocument,
                path=path,
            )
        except FileNotFoundError:
            continue
        except (ValidationError, ValueError) as exc:
            _quarantine_invalid_artifact(paths.tasks_queue_dir, path, str(exc))
            continue
        task_id = document.task_id
        if path.stem != task_id:
            _quarantine_invalid_artifact(
                paths.tasks_queue_dir,
                path,
                f"filename stem does not match task_id: expected {task_id}, found {path.stem}",
            )
            continue
        if root_spec_id is not None and _effective_root_spec_id(document) != root_spec_id:
            continue
        if not _task_dependencies_satisfied(document, completed_task_ids):
            continue
        candidates.append((document.created_at, task_id, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    _timestamp, task_id, path = candidates[0]
    return task_id, path


def _select_oldest_spec(
    directory: Path,
    *,
    root_spec_id: str | None = None,
) -> tuple[str, Path] | None:
    return _select_oldest_document(
        directory=directory,
        model=SpecDocument,
        id_attr="spec_id",
        timestamp_attr="created_at",
        root_spec_id=root_spec_id,
    )


def _select_oldest_incident(
    directory: Path,
    *,
    root_spec_id: str | None = None,
) -> tuple[str, Path] | None:
    return _select_oldest_document(
        directory=directory,
        model=IncidentDocument,
        id_attr="incident_id",
        timestamp_attr="opened_at",
        root_spec_id=root_spec_id,
    )


def _select_oldest_learning_request(directory: Path) -> tuple[str, Path] | None:
    return _select_oldest_document(
        directory=directory,
        model=LearningRequestDocument,
        id_attr="learning_request_id",
        timestamp_attr="created_at",
    )


def _select_oldest_document(
    *,
    directory: Path,
    model: type[_DocT],
    id_attr: str,
    timestamp_attr: str,
    root_spec_id: str | None = None,
) -> tuple[str, Path] | None:
    candidates: list[tuple[datetime, str, Path]] = []
    for path in _list_markdown_files(directory):
        try:
            document = parse_work_document_as(
                path.read_text(encoding="utf-8"),
                model=model,
                path=path,
            )
        except FileNotFoundError:
            continue
        except (ValidationError, ValueError) as exc:
            _quarantine_invalid_artifact(directory, path, str(exc))
            continue
        item_id = str(getattr(document, id_attr))
        if path.stem != item_id:
            _quarantine_invalid_artifact(
                directory,
                path,
                f"filename stem does not match {id_attr}: expected {item_id}, found {path.stem}",
            )
            continue
        if (
            root_spec_id is not None
            and isinstance(document, (TaskDocument, SpecDocument, IncidentDocument))
            and _effective_root_spec_id(document) != root_spec_id
        ):
            continue
        timestamp = getattr(document, timestamp_attr)
        candidates.append((timestamp, item_id, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    _timestamp, item_id, path = candidates[0]
    return item_id, path


def _list_markdown_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.md") if path.is_file())


def _task_dependencies_satisfied(task: TaskDocument, completed_task_ids: set[str]) -> bool:
    return all(dependency in completed_task_ids for dependency in task.depends_on)


def list_open_lineage_work_ids(
    paths: WorkspacePaths,
    *,
    root_spec_id: str,
) -> tuple[str, ...]:
    seen: set[str] = set()
    work_item_ids: list[str] = []
    for directory, model, id_attr in _lineage_scan_specs(paths):
        for path in _list_markdown_files(directory):
            try:
                raw = path.read_text(encoding="utf-8")
                document: TaskDocument | SpecDocument | IncidentDocument
                if model is TaskDocument:
                    document = parse_work_document_as(raw, model=TaskDocument, path=path)
                elif model is SpecDocument:
                    document = parse_work_document_as(raw, model=SpecDocument, path=path)
                else:
                    document = parse_work_document_as(raw, model=IncidentDocument, path=path)
            except FileNotFoundError:
                continue
            except (ValidationError, ValueError):
                continue
            if _effective_root_spec_id(document) != root_spec_id:
                continue
            work_item_id = str(getattr(document, id_attr))
            if work_item_id in seen:
                continue
            seen.add(work_item_id)
            work_item_ids.append(work_item_id)
    return tuple(work_item_ids)


def list_deferred_root_spec_ids(
    paths: WorkspacePaths,
    *,
    open_root_spec_id: str,
) -> tuple[str, ...]:
    """Return queued root specs deferred by the current workspace-global closure target."""

    deferred: list[tuple[datetime, str]] = []
    for path in _list_markdown_files(paths.specs_queue_dir):
        try:
            document = parse_work_document_as(
                path.read_text(encoding="utf-8"),
                model=SpecDocument,
                path=path,
            )
        except FileNotFoundError:
            continue
        except (ValidationError, ValueError):
            continue
        if not _is_root_spec_document(document):
            continue
        effective_root_spec_id = _effective_root_spec_id(document)
        if effective_root_spec_id is None or effective_root_spec_id == open_root_spec_id:
            continue
        deferred.append((document.created_at, document.spec_id))

    deferred.sort(key=lambda item: (item[0], item[1]))
    return tuple(spec_id for _created_at, spec_id in deferred)


def _effective_root_spec_id(
    document: TaskDocument | SpecDocument | IncidentDocument,
) -> str | None:
    if document.root_spec_id is not None:
        return document.root_spec_id
    if isinstance(document, TaskDocument):
        return document.spec_id
    if isinstance(document, IncidentDocument):
        return document.source_spec_id
    if document.source_type in {"idea", "manual"}:
        return document.spec_id
    return None


def _is_root_spec_document(document: SpecDocument) -> bool:
    if document.root_spec_id is not None:
        return document.spec_id == document.root_spec_id
    if document.parent_spec_id is not None and document.parent_spec_id.strip().lower() != "none":
        return False
    return document.source_type in {"idea", "manual"}


def _lineage_scan_specs(
    paths: WorkspacePaths,
) -> tuple[
    tuple[
        Path,
        type[TaskDocument] | type[SpecDocument] | type[IncidentDocument],
        str,
    ],
    ...,
]:
    return (
        (paths.tasks_queue_dir, TaskDocument, "task_id"),
        (paths.tasks_active_dir, TaskDocument, "task_id"),
        (paths.tasks_blocked_dir, TaskDocument, "task_id"),
        (paths.specs_queue_dir, SpecDocument, "spec_id"),
        (paths.specs_active_dir, SpecDocument, "spec_id"),
        (paths.specs_blocked_dir, SpecDocument, "spec_id"),
        (paths.incidents_incoming_dir, IncidentDocument, "incident_id"),
        (paths.incidents_active_dir, IncidentDocument, "incident_id"),
        (paths.incidents_blocked_dir, IncidentDocument, "incident_id"),
    )


def _quarantine_invalid_artifact(directory: Path, source_path: Path, error: str) -> None:
    destination = source_path.with_suffix(f"{source_path.suffix}.invalid")
    suffix_index = 1
    while destination.exists():
        destination = source_path.with_suffix(f"{source_path.suffix}.invalid.{suffix_index}")
        suffix_index += 1

    try:
        source_path.replace(destination)
    except FileNotFoundError:
        return
    log_path = directory / "invalid-artifacts.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "source_name": source_path.name,
                    "quarantine_name": destination.name,
                    "error": error,
                },
                sort_keys=True,
            )
            + "\n"
        )


__all__ = [
    "QueueClaim",
    "claim_next_execution_task",
    "claim_next_learning_request",
    "claim_next_planning_item",
    "list_deferred_root_spec_ids",
    "list_open_lineage_work_ids",
]
