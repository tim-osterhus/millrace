"""Deterministic queue selection and claim helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from millrace_ai.contracts import IncidentDocument, SpecDocument, TaskDocument, WorkItemKind
from millrace_ai.errors import QueueStateError

from .paths import WorkspacePaths
from .work_documents import parse_work_document_as

_DocT = TypeVar("_DocT", TaskDocument, SpecDocument, IncidentDocument)


@dataclass(frozen=True, slots=True)
class QueueClaim:
    """Represents ownership of a newly-claimed work item."""

    work_item_kind: WorkItemKind
    work_item_id: str
    path: Path


def claim_next_execution_task(paths: WorkspacePaths) -> QueueClaim | None:
    active = _list_markdown_files(paths.tasks_active_dir)
    if len(active) > 1:
        raise QueueStateError("Multiple active execution tasks found")
    if active:
        return None

    while True:
        candidate = _select_oldest_task(paths.tasks_queue_dir)
        if candidate is None:
            return None

        task_id, source = candidate
        destination = paths.tasks_active_dir / source.name
        try:
            source.replace(destination)
        except FileNotFoundError:
            continue
        return QueueClaim(work_item_kind=WorkItemKind.TASK, work_item_id=task_id, path=destination)


def claim_next_planning_item(paths: WorkspacePaths) -> QueueClaim | None:
    active_specs = _list_markdown_files(paths.specs_active_dir)
    active_incidents = _list_markdown_files(paths.incidents_active_dir)
    if len(active_specs) + len(active_incidents) > 1:
        raise QueueStateError("Multiple active planning items found")
    if active_specs or active_incidents:
        return None

    while True:
        incident_candidate = _select_oldest_incident(paths.incidents_incoming_dir)
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

        spec_candidate = _select_oldest_spec(paths.specs_queue_dir)
        if spec_candidate is None:
            return None

        spec_id, source = spec_candidate
        destination = paths.specs_active_dir / source.name
        try:
            source.replace(destination)
        except FileNotFoundError:
            continue
        return QueueClaim(work_item_kind=WorkItemKind.SPEC, work_item_id=spec_id, path=destination)


def _select_oldest_task(directory: Path) -> tuple[str, Path] | None:
    return _select_oldest_document(
        directory=directory,
        model=TaskDocument,
        id_attr="task_id",
        timestamp_attr="created_at",
    )


def _select_oldest_spec(directory: Path) -> tuple[str, Path] | None:
    return _select_oldest_document(
        directory=directory,
        model=SpecDocument,
        id_attr="spec_id",
        timestamp_attr="created_at",
    )


def _select_oldest_incident(directory: Path) -> tuple[str, Path] | None:
    return _select_oldest_document(
        directory=directory,
        model=IncidentDocument,
        id_attr="incident_id",
        timestamp_attr="opened_at",
    )


def _select_oldest_document(
    *,
    directory: Path,
    model: type[_DocT],
    id_attr: str,
    timestamp_attr: str,
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
        timestamp = getattr(document, timestamp_attr)
        candidates.append((timestamp, item_id, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    _timestamp, item_id, path = candidates[0]
    return item_id, path


def _list_markdown_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.md") if path.is_file())


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


__all__ = ["QueueClaim", "claim_next_execution_task", "claim_next_planning_item"]
