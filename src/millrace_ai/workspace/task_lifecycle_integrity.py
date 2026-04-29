"""Task lifecycle duplicate diagnostics and safe reconciliation helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from millrace_ai.contracts import TaskDocument

from .lineage_integrity import effective_root_spec_id
from .paths import WorkspacePaths
from .work_documents import read_work_document_as


@dataclass(frozen=True, slots=True)
class TaskLifecycleDuplicate:
    """One task id that appears in more than one lifecycle state."""

    task_id: str
    state_paths: tuple[tuple[str, Path], ...]

    @property
    def states(self) -> tuple[str, ...]:
        return tuple(state for state, _path in self.state_paths)

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(path for _state, path in self.state_paths)


def find_duplicate_task_lifecycle_ids(paths: WorkspacePaths) -> tuple[TaskLifecycleDuplicate, ...]:
    """Return task ids present in multiple task lifecycle directories."""

    by_task_id: dict[str, list[tuple[str, Path]]] = {}
    for state, directory in _task_lifecycle_directories(paths):
        for path in _list_markdown_files(directory):
            task_id = _task_id_for_path(path)
            by_task_id.setdefault(task_id, []).append((state, path))

    duplicates = [
        TaskLifecycleDuplicate(task_id=task_id, state_paths=tuple(state_paths))
        for task_id, state_paths in by_task_id.items()
        if len(state_paths) > 1
    ]
    duplicates.sort(key=lambda item: item.task_id)
    return tuple(duplicates)


def retire_stale_blocked_task_duplicate_after_done(
    paths: WorkspacePaths,
    *,
    task_id: str,
    retired_at: datetime | None = None,
) -> Path | None:
    """Archive a same-root blocked predecessor once a same-id continuation is done."""

    blocked_path = paths.tasks_blocked_dir / f"{task_id}.md"
    done_path = paths.tasks_done_dir / f"{task_id}.md"
    if not blocked_path.is_file() or not done_path.is_file():
        return None

    try:
        blocked_document = read_work_document_as(blocked_path, model=TaskDocument)
        done_document = read_work_document_as(done_path, model=TaskDocument)
    except (OSError, ValidationError, ValueError):
        return None

    blocked_root = effective_root_spec_id(blocked_document)
    done_root = effective_root_spec_id(done_document)
    if blocked_root is None or blocked_root != done_root:
        return None

    archive_dir = paths.tasks_blocked_dir / "superseded"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_at = _coerce_datetime(retired_at)
    archive_path = _superseded_task_path(archive_dir, task_id=task_id, archived_at=archived_at)
    try:
        blocked_path.replace(archive_path)
    except FileNotFoundError:
        return None

    _append_retirement_record(
        paths,
        archive_dir=archive_dir,
        task_id=task_id,
        source_path=blocked_path,
        archive_path=archive_path,
        archived_at=archived_at,
        root_spec_id=blocked_root,
    )
    return archive_path


def _task_lifecycle_directories(paths: WorkspacePaths) -> tuple[tuple[str, Path], ...]:
    return (
        ("queue", paths.tasks_queue_dir),
        ("active", paths.tasks_active_dir),
        ("done", paths.tasks_done_dir),
        ("blocked", paths.tasks_blocked_dir),
    )


def _task_id_for_path(path: Path) -> str:
    try:
        return read_work_document_as(path, model=TaskDocument).task_id
    except (OSError, ValidationError, ValueError):
        return path.stem


def _list_markdown_files(directory: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in directory.glob("*.md") if path.is_file()))


def _coerce_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _superseded_task_path(archive_dir: Path, *, task_id: str, archived_at: datetime) -> Path:
    timestamp = archived_at.strftime("%Y%m%dT%H%M%SZ")
    return archive_dir / f"{task_id}.{timestamp}.{uuid4().hex[:8]}.blocked.md"


def _append_retirement_record(
    paths: WorkspacePaths,
    *,
    archive_dir: Path,
    task_id: str,
    source_path: Path,
    archive_path: Path,
    archived_at: datetime,
    root_spec_id: str,
) -> None:
    record = {
        "archived_at": archived_at.isoformat(),
        "task_id": task_id,
        "root_spec_id": root_spec_id,
        "reason": "same_id_done_continuation",
        "source_path": _workspace_relative_path(paths, source_path),
        "archive_path": _workspace_relative_path(paths, archive_path),
    }
    with (archive_dir / "retirements.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _workspace_relative_path(paths: WorkspacePaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.root))
    except ValueError:
        return str(path)


__all__ = [
    "TaskLifecycleDuplicate",
    "find_duplicate_task_lifecycle_ids",
    "retire_stale_blocked_task_duplicate_after_done",
]
