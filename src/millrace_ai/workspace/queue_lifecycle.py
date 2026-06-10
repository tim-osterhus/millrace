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

from .paths import WorkspacePaths

if TYPE_CHECKING:
    from millrace_ai.runtime.effects import SourceLifecycleIntent

    from .family_adapters import WorkFamilyQueueAdapter


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
        family_id = intent.work_item_family_id or (
            intent.work_item_kind.value if intent.work_item_kind is not None else None
        )
        if family_id is None:
            raise ValueError("Source lifecycle intent requires work item family id")
        family = self._families_by_id.get(family_id)
        adapter = _queue_adapter_for_family(family_id=family_id, family=family)
        if adapter is not None:
            return adapter.apply_lifecycle(
                self._paths,
                intent=intent,
                work_item_families=tuple(self._families_by_id.values()),
            )
        if family is not None:
            return self._apply_generic_family(intent, family_id=family_id)
        raise ValueError(f"Unsupported work item family: {family_id}")

    def _apply_generic_family(self, intent: "SourceLifecycleIntent", *, family_id: str) -> Path:
        family = self._families_by_id[family_id]
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
    family = _families_by_id(work_item_families).get(family_id)
    adapter = _queue_adapter_for_family(family_id=family_id, family=family)
    if adapter is not None:
        return adapter.requeue_active(
            paths,
            work_item_id=work_item_id,
            reason=reason,
            work_item_families=work_item_families,
        )
    if family is None:
        detail = f" (kind={kind.value})" if kind is not None else ""
        raise QueueStateError(f"unsupported active work item family: {family_id}{detail}")
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


def _queue_adapter_for_family(
    *,
    family_id: str,
    family: WorkItemFamilyDefinition | None,
) -> "WorkFamilyQueueAdapter | None":
    from .family_adapters import (
        queue_adapter_for_id,
        resolve_queue_lifecycle_adapter_id,
    )

    if family is not None:
        adapter_id = resolve_queue_lifecycle_adapter_id(family)
        if adapter_id is not None:
            adapter = queue_adapter_for_id(adapter_id)
            if adapter is not None:
                return adapter
    return None


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
