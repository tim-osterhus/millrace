"""Built-in work-item document adapters for generic queue operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

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
from .task_lifecycle_integrity import retire_stale_blocked_task_duplicate_after_done
from .work_documents import parse_work_document_as, render_work_document

_DocT = TypeVar(
    "_DocT",
    TaskDocument,
    ProbeDocument,
    SpecDocument,
    IncidentDocument,
    LearningRequestDocument,
)


@dataclass(frozen=True, slots=True)
class WorkItemDocumentAdapter(Generic[_DocT]):
    family_id: str
    work_item_kind: WorkItemKind
    document_model: type[_DocT]
    id_attr: str
    timestamp_attr: str
    queue_dir_attr: str
    active_dir_attr: str
    done_dir_attr: str
    blocked_dir_attr: str
    supports_root_filter: bool = False

    def queue_dir(self, paths: WorkspacePaths) -> Path:
        return getattr(paths, self.queue_dir_attr)

    def active_dir(self, paths: WorkspacePaths) -> Path:
        return getattr(paths, self.active_dir_attr)

    def done_dir(self, paths: WorkspacePaths) -> Path:
        return getattr(paths, self.done_dir_attr)

    def blocked_dir(self, paths: WorkspacePaths) -> Path:
        return getattr(paths, self.blocked_dir_attr)

    def item_id(self, document: _DocT) -> str:
        return str(getattr(document, self.id_attr))

    def timestamp(self, document: _DocT) -> datetime:
        return getattr(document, self.timestamp_attr)

    def parse(self, text: str, *, path: Path) -> _DocT:
        return parse_work_document_as(text, model=self.document_model, path=path)

    def validate_filename(self, path: Path, *, item_id: str | None = None) -> None:
        expected_id = item_id
        if expected_id is None:
            document = self.parse(path.read_text(encoding="utf-8"), path=path)
            expected_id = self.item_id(document)
        if path.stem != expected_id:
            raise QueueStateError(
                f"filename stem does not match {self.id_attr}: expected {expected_id}, found {path.stem}"
            )


_TASK_ADAPTER = WorkItemDocumentAdapter(
    family_id="task",
    work_item_kind=WorkItemKind.TASK,
    document_model=TaskDocument,
    id_attr="task_id",
    timestamp_attr="created_at",
    queue_dir_attr="tasks_queue_dir",
    active_dir_attr="tasks_active_dir",
    done_dir_attr="tasks_done_dir",
    blocked_dir_attr="tasks_blocked_dir",
    supports_root_filter=True,
)
_PROBE_ADAPTER = WorkItemDocumentAdapter(
    family_id="probe",
    work_item_kind=WorkItemKind.PROBE,
    document_model=ProbeDocument,
    id_attr="probe_id",
    timestamp_attr="created_at",
    queue_dir_attr="probes_queue_dir",
    active_dir_attr="probes_active_dir",
    done_dir_attr="probes_done_dir",
    blocked_dir_attr="probes_blocked_dir",
)
_SPEC_ADAPTER = WorkItemDocumentAdapter(
    family_id="spec",
    work_item_kind=WorkItemKind.SPEC,
    document_model=SpecDocument,
    id_attr="spec_id",
    timestamp_attr="created_at",
    queue_dir_attr="specs_queue_dir",
    active_dir_attr="specs_active_dir",
    done_dir_attr="specs_done_dir",
    blocked_dir_attr="specs_blocked_dir",
    supports_root_filter=True,
)
_INCIDENT_ADAPTER = WorkItemDocumentAdapter(
    family_id="incident",
    work_item_kind=WorkItemKind.INCIDENT,
    document_model=IncidentDocument,
    id_attr="incident_id",
    timestamp_attr="opened_at",
    queue_dir_attr="incidents_incoming_dir",
    active_dir_attr="incidents_active_dir",
    done_dir_attr="incidents_resolved_dir",
    blocked_dir_attr="incidents_blocked_dir",
    supports_root_filter=True,
)
_LEARNING_REQUEST_ADAPTER = WorkItemDocumentAdapter(
    family_id="learning_request",
    work_item_kind=WorkItemKind.LEARNING_REQUEST,
    document_model=LearningRequestDocument,
    id_attr="learning_request_id",
    timestamp_attr="created_at",
    queue_dir_attr="learning_requests_queue_dir",
    active_dir_attr="learning_requests_active_dir",
    done_dir_attr="learning_requests_done_dir",
    blocked_dir_attr="learning_requests_blocked_dir",
)

_BUILTIN_ADAPTERS = (
    _TASK_ADAPTER,
    _PROBE_ADAPTER,
    _SPEC_ADAPTER,
    _INCIDENT_ADAPTER,
    _LEARNING_REQUEST_ADAPTER,
)


def builtin_work_item_adapters() -> tuple[WorkItemDocumentAdapter[Any], ...]:
    return _BUILTIN_ADAPTERS


def adapter_for_kind(kind: WorkItemKind) -> WorkItemDocumentAdapter[Any]:
    for adapter in _BUILTIN_ADAPTERS:
        if adapter.work_item_kind is kind:
            return adapter
    raise QueueStateError(f"Unknown work item kind: {kind.value}")


def adapter_for_family_id(family_id: str) -> WorkItemDocumentAdapter[Any]:
    for adapter in _BUILTIN_ADAPTERS:
        if adapter.family_id == family_id:
            return adapter
    raise QueueStateError(f"Unknown work item family: {family_id}")


def parse_with_adapter(
    adapter: WorkItemDocumentAdapter[_DocT],
    text: str,
    *,
    path: Path,
) -> _DocT:
    return adapter.parse(text, path=path)


def enqueue_with_adapter(
    paths: WorkspacePaths,
    adapter: WorkItemDocumentAdapter[_DocT],
    document: _DocT,
) -> Path:
    item_id = adapter.item_id(document)
    ensure_unique_with_adapter(paths, adapter, item_id)
    destination = adapter.queue_dir(paths) / f"{item_id}.md"
    destination.write_text(render_work_document(document), encoding="utf-8")
    return destination


def ensure_unique_with_adapter(
    paths: WorkspacePaths,
    adapter: WorkItemDocumentAdapter[Any],
    item_id: str,
) -> None:
    filename = f"{item_id}.md"
    for directory in (
        adapter.queue_dir(paths),
        adapter.active_dir(paths),
        adapter.done_dir(paths),
        adapter.blocked_dir(paths),
    ):
        if (directory / filename).exists():
            raise QueueStateError(f"{adapter.family_id} {item_id} already exists")


def move_active_with_adapter(
    paths: WorkspacePaths,
    adapter: WorkItemDocumentAdapter[Any],
    item_id: str,
    *,
    target_state: str,
) -> Path:
    if target_state == "done":
        destination_dir = adapter.done_dir(paths)
    elif target_state == "blocked":
        destination_dir = adapter.blocked_dir(paths)
    elif target_state == "queue":
        destination_dir = adapter.queue_dir(paths)
    else:
        raise QueueStateError(f"Unsupported lifecycle target state: {target_state}")

    destination = move_with_adapter(
        adapter,
        source_dir=adapter.active_dir(paths),
        destination_dir=destination_dir,
        item_id=item_id,
        source_state="active",
    )
    if adapter.work_item_kind is WorkItemKind.TASK and target_state == "done":
        retire_stale_blocked_task_duplicate_after_done(paths, task_id=item_id)
    return destination


def move_with_adapter(
    adapter: WorkItemDocumentAdapter[Any],
    *,
    source_dir: Path,
    destination_dir: Path,
    item_id: str,
    source_state: str,
) -> Path:
    source = source_dir / f"{item_id}.md"
    if not source.exists():
        raise QueueStateError(f"{adapter.family_id} {item_id} is not {source_state}")
    destination = destination_dir / source.name
    if destination.exists():
        raise QueueStateError(f"{adapter.family_id} {item_id} already exists at destination")
    source.replace(destination)
    return destination


__all__ = [
    "WorkItemDocumentAdapter",
    "adapter_for_family_id",
    "adapter_for_kind",
    "builtin_work_item_adapters",
    "enqueue_with_adapter",
    "ensure_unique_with_adapter",
    "move_active_with_adapter",
    "move_with_adapter",
    "parse_with_adapter",
]
