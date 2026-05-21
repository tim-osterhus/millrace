"""Queue lifecycle interpreter for built-in active work item families."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.contracts import WorkItemKind
from millrace_ai.contracts.work_refs import coerce_family_and_kind
from millrace_ai.errors import QueueStateError

from .blueprint_state import approve_active_blueprint_draft, block_active_blueprint_draft, requeue_active_blueprint_draft
from .paths import WorkspacePaths
from .queue_transitions import (
    mark_incident_blocked,
    mark_incident_resolved,
    mark_learning_request_blocked,
    mark_learning_request_done,
    mark_probe_blocked,
    mark_probe_done,
    mark_spec_blocked,
    mark_spec_done,
    mark_task_blocked,
    mark_task_done,
    requeue_incident,
    requeue_learning_request,
    requeue_probe,
    requeue_spec,
    requeue_task,
)

if TYPE_CHECKING:
    from millrace_ai.runtime.effects import SourceLifecycleIntent


class QueueLifecycleInterpreter:
    """Apply source lifecycle intents to the workspace queue state."""

    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
    ) -> None:
        self._paths = paths
        family_definitions = (
            work_item_families
            if work_item_families is not None
            else load_builtin_workflow_primitives().work_item_families
        )
        self._families_by_id = {family.family_id: family for family in family_definitions}

    def apply(self, intent: "SourceLifecycleIntent") -> Path:
        if intent.work_item_kind is WorkItemKind.TASK:
            return self._apply_task(intent)
        if intent.work_item_kind is WorkItemKind.SPEC:
            return self._apply_spec(intent)
        if intent.work_item_kind is WorkItemKind.PROBE:
            return self._apply_probe(intent)
        if intent.work_item_kind is WorkItemKind.INCIDENT:
            return self._apply_incident(intent)
        if intent.work_item_kind is WorkItemKind.LEARNING_REQUEST:
            return self._apply_learning_request(intent)
        if intent.work_item_kind is WorkItemKind.BLUEPRINT_DRAFT:
            return self._apply_blueprint_draft(intent)
        if intent.work_item_family_id in self._families_by_id:
            return self._apply_generic_family(intent)
        raise ValueError(f"Unsupported work item family: {intent.work_item_family_id}")

    def _apply_task(self, intent: "SourceLifecycleIntent") -> Path:
        if intent.action.value == "complete":
            return mark_task_done(self._paths, intent.work_item_id)
        return mark_task_blocked(self._paths, intent.work_item_id)

    def _apply_spec(self, intent: "SourceLifecycleIntent") -> Path:
        if intent.action.value == "complete":
            return mark_spec_done(self._paths, intent.work_item_id)
        return mark_spec_blocked(self._paths, intent.work_item_id)

    def _apply_probe(self, intent: "SourceLifecycleIntent") -> Path:
        if intent.action.value == "complete":
            return mark_probe_done(self._paths, intent.work_item_id)
        return mark_probe_blocked(self._paths, intent.work_item_id)

    def _apply_incident(self, intent: "SourceLifecycleIntent") -> Path:
        if intent.action.value == "complete":
            return mark_incident_resolved(self._paths, intent.work_item_id)
        return mark_incident_blocked(self._paths, intent.work_item_id)

    def _apply_learning_request(self, intent: "SourceLifecycleIntent") -> Path:
        if intent.action.value == "complete":
            return mark_learning_request_done(self._paths, intent.work_item_id)
        return mark_learning_request_blocked(self._paths, intent.work_item_id)

    def _apply_blueprint_draft(self, intent: "SourceLifecycleIntent") -> Path:
        if intent.action.value == "complete":
            return approve_active_blueprint_draft(self._paths, intent.work_item_id)
        return block_active_blueprint_draft(self._paths, intent.work_item_id)

    def _apply_generic_family(self, intent: "SourceLifecycleIntent") -> Path:
        assert intent.work_item_family_id is not None
        family = self._families_by_id[intent.work_item_family_id]
        target_relative = (
            family.queue_dirs.done if intent.action.value == "complete" else family.queue_dirs.blocked
        )
        source = self._paths.runtime_root / family.queue_dirs.active / (
            f"{intent.work_item_id}{family.file_extension}"
        )
        destination = self._paths.runtime_root / target_relative / source.name
        if not source.exists():
            raise QueueStateError(f"{family.family_id} {intent.work_item_id} is not active")
        if destination.exists():
            raise QueueStateError(
                f"{family.family_id} {intent.work_item_id} already exists at destination"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return destination


def requeue_active_work_item(
    paths: WorkspacePaths,
    *,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | None = None,
    work_item_id: str,
    reason: str,
    work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
) -> Path:
    family_id, kind = coerce_family_and_kind(
        family_id=work_item_family_id,
        work_item_kind=work_item_kind,
    )
    if family_id is None:
        raise QueueStateError("requeue requires work_item_family_id or work_item_kind")
    if kind is WorkItemKind.TASK:
        return requeue_task(paths, work_item_id, reason=reason)
    if kind is WorkItemKind.SPEC:
        return requeue_spec(paths, work_item_id, reason=reason)
    if kind is WorkItemKind.PROBE:
        return requeue_probe(paths, work_item_id, reason=reason)
    if kind is WorkItemKind.INCIDENT:
        return requeue_incident(paths, work_item_id, reason=reason)
    if kind is WorkItemKind.LEARNING_REQUEST:
        return requeue_learning_request(paths, work_item_id, reason=reason)
    if kind is WorkItemKind.BLUEPRINT_DRAFT:
        destination = requeue_active_blueprint_draft(paths, work_item_id)
        _append_family_requeue_reason(destination.parent, work_item_id, family_id=family_id, reason=reason)
        return destination

    family = _families_by_id(work_item_families).get(family_id)
    if family is None:
        raise QueueStateError(f"unsupported active work item family: {family_id}")
    return _requeue_active_generic_family(paths, family=family, work_item_id=work_item_id, reason=reason)


def requeue_all_active_work_items(
    paths: WorkspacePaths,
    *,
    reason: str,
    work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
) -> int:
    requeued_count = 0
    for family in _families_by_id(work_item_families).values():
        active_dir = paths.runtime_root / family.queue_dirs.active
        if not active_dir.exists():
            continue
        for path in sorted(active_dir.glob(f"*{family.file_extension}")):
            if not path.is_file():
                continue
            try:
                requeue_active_work_item(
                    paths,
                    work_item_family_id=family.family_id,
                    work_item_id=path.stem,
                    reason=reason,
                    work_item_families=work_item_families,
                )
            except QueueStateError:
                continue
            requeued_count += 1
    return requeued_count


def _families_by_id(
    work_item_families: tuple[WorkItemFamilyDefinition, ...] | None,
) -> dict[str, WorkItemFamilyDefinition]:
    families = (
        work_item_families
        if work_item_families is not None
        else load_builtin_workflow_primitives().work_item_families
    )
    return {family.family_id: family for family in families}


def _requeue_active_generic_family(
    paths: WorkspacePaths,
    *,
    family: WorkItemFamilyDefinition,
    work_item_id: str,
    reason: str,
) -> Path:
    source = paths.runtime_root / family.queue_dirs.active / f"{work_item_id}{family.file_extension}"
    destination = paths.runtime_root / family.queue_dirs.queue / source.name
    if not source.is_file():
        raise QueueStateError(f"{family.family_id} {work_item_id} is not active")
    if destination.exists():
        raise QueueStateError(f"{family.family_id} {work_item_id} is already queued")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)
    _append_family_requeue_reason(destination.parent, work_item_id, family_id=family.family_id, reason=reason)
    return destination


def _append_family_requeue_reason(
    destination_dir: Path,
    work_item_id: str,
    *,
    family_id: str,
    reason: str,
) -> None:
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise QueueStateError("requeue reason is required")
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "family_id": family_id,
        "reason": cleaned_reason,
    }
    with (destination_dir / f"{work_item_id}.requeue.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


__all__ = [
    "QueueLifecycleInterpreter",
    "requeue_active_work_item",
    "requeue_all_active_work_items",
]
