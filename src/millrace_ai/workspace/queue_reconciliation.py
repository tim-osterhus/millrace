"""Queue stale-state reconciliation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from millrace_ai.contracts import WorkItemKind
from millrace_ai.errors import QueueStateError

from .paths import WorkspacePaths


@dataclass(frozen=True, slots=True)
class StaleActiveState:
    """Result payload from stale active-state checks."""

    is_stale: bool
    reasons: tuple[str, ...]


def detect_execution_stale_state(
    paths: WorkspacePaths,
    *,
    snapshot_active_task_id: str | None,
) -> StaleActiveState:
    active_ids = _ids_in_directory(paths.tasks_active_dir)
    queued_ids = _ids_in_directory(paths.tasks_queue_dir)
    reasons: list[str] = []

    if len(active_ids) > 1:
        reasons.append("multiple_active_items")

    if active_ids and snapshot_active_task_id is None:
        reasons.append("active_without_snapshot")

    if snapshot_active_task_id is not None:
        if snapshot_active_task_id in queued_ids:
            reasons.append("snapshot_points_to_queued_item")
        if not active_ids:
            reasons.append("snapshot_without_active_artifact")
        elif len(active_ids) == 1 and active_ids[0] != snapshot_active_task_id:
            reasons.append("snapshot_active_id_mismatch")

    return _stale_state(reasons)


def detect_planning_stale_state(
    paths: WorkspacePaths,
    *,
    snapshot_active_kind: WorkItemKind | None,
    snapshot_active_item_id: str | None,
) -> StaleActiveState:
    has_kind = snapshot_active_kind is not None
    has_item = snapshot_active_item_id is not None
    if has_kind != has_item:
        raise QueueStateError("snapshot_active_kind and snapshot_active_item_id must be set together")

    if snapshot_active_kind not in {None, WorkItemKind.SPEC, WorkItemKind.INCIDENT}:
        raise QueueStateError("Planning stale-state checks only support spec and incident kinds")

    active_specs = [(WorkItemKind.SPEC, item_id) for item_id in _ids_in_directory(paths.specs_active_dir)]
    active_incidents = [
        (WorkItemKind.INCIDENT, item_id)
        for item_id in _ids_in_directory(paths.incidents_active_dir)
    ]
    active_items = active_specs + active_incidents
    reasons: list[str] = []

    if len(active_items) > 1:
        reasons.append("multiple_active_items")

    if active_items and snapshot_active_item_id is None:
        reasons.append("active_without_snapshot")

    if snapshot_active_kind is not None and snapshot_active_item_id is not None:
        queued_ids = (
            _ids_in_directory(paths.specs_queue_dir)
            if snapshot_active_kind is WorkItemKind.SPEC
            else _ids_in_directory(paths.incidents_incoming_dir)
        )
        if snapshot_active_item_id in queued_ids:
            reasons.append("snapshot_points_to_queued_item")

        if not active_items:
            reasons.append("snapshot_without_active_artifact")
        elif len(active_items) == 1:
            active_kind, active_id = active_items[0]
            if active_kind is not snapshot_active_kind or active_id != snapshot_active_item_id:
                reasons.append("snapshot_active_id_mismatch")

    return _stale_state(reasons)


def _ids_in_directory(directory: Path) -> list[str]:
    return sorted(path.stem for path in _list_markdown_files(directory))


def _list_markdown_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.md") if path.is_file())


def _stale_state(reasons: list[str]) -> StaleActiveState:
    deduped = tuple(sorted(set(reasons)))
    return StaleActiveState(is_stale=bool(deduped), reasons=deduped)


__all__ = ["StaleActiveState", "detect_execution_stale_state", "detect_planning_stale_state"]
