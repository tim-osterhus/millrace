"""Built-in work-family queue adapter implementations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.architecture.workflow_primitives import (
    builtin_queue_lifecycle_adapter_id_for_family,
)
from millrace_ai.contracts import Plane, WorkItemKind
from millrace_ai.errors import QueueStateError

from ..blueprint_state import (
    approve_active_blueprint_draft,
    block_active_blueprint_draft,
    claim_next_blueprint_draft,
    requeue_active_blueprint_draft,
)
from ..paths import WorkspacePaths
from ..queue_claims import QueueClaim
from ..queue_selection import (
    _select_oldest_incident,
    _select_oldest_probe,
    _select_oldest_spec,
    claim_next_execution_task,
    claim_next_learning_request,
    list_deferred_root_spec_ids,
    list_open_lineage_work_ids,
)
from ..queue_transitions import (
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


@dataclass(frozen=True, slots=True)
class BuiltinWorkFamilyQueueAdapter:
    adapter_id: str
    family_id: str
    plane: Plane
    active_relative_dir: str
    file_extension: str
    planning_claim_policy_id: str | None = None

    def claim_next(
        self,
        paths: WorkspacePaths,
        *,
        root_spec_id: str | None = None,
        work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
    ) -> QueueClaim | None:
        if self.family_id == WorkItemKind.TASK.value:
            return claim_next_execution_task(paths, root_spec_id=root_spec_id)
        if self.family_id == WorkItemKind.LEARNING_REQUEST.value:
            return claim_next_learning_request(paths)
        if self.family_id == WorkItemKind.INCIDENT.value:
            return _claim_with_retry(
                selector=lambda: _select_oldest_incident(
                    paths.incidents_incoming_dir,
                    root_spec_id=root_spec_id,
                ),
                destination_dir=paths.incidents_active_dir,
                work_item_kind=WorkItemKind.INCIDENT,
                source_state="incoming",
            )
        if self.family_id == WorkItemKind.PROBE.value:
            if root_spec_id is not None:
                return None
            return _claim_with_retry(
                selector=lambda: _select_oldest_probe(paths.probes_queue_dir),
                destination_dir=paths.probes_active_dir,
                work_item_kind=WorkItemKind.PROBE,
                source_state="queue",
            )
        if self.family_id == WorkItemKind.SPEC.value:
            return _claim_with_retry(
                selector=lambda: _select_oldest_spec(
                    paths.specs_queue_dir,
                    root_spec_id=root_spec_id,
                ),
                destination_dir=paths.specs_active_dir,
                work_item_kind=WorkItemKind.SPEC,
                source_state="queue",
            )
        if self.family_id == WorkItemKind.BLUEPRINT_DRAFT.value:
            return claim_next_blueprint_draft(paths, root_spec_id=root_spec_id)
        raise QueueStateError(f"unsupported built-in claim adapter family: {self.family_id}")

    def active_path(
        self,
        paths: WorkspacePaths,
        *,
        work_item_id: str,
    ) -> Path:
        return paths.runtime_root / self.active_relative_dir / f"{work_item_id}{self.file_extension}"

    def apply_lifecycle(
        self,
        paths: WorkspacePaths,
        *,
        intent: "SourceLifecycleIntent",
        work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
    ) -> Path:
        if intent.work_item_family_id != self.family_id:
            raise QueueStateError(
                f"lifecycle intent family {intent.work_item_family_id} does not match adapter "
                f"family {self.family_id}"
            )
        is_complete = intent.action.value == "complete"
        if self.family_id == WorkItemKind.TASK.value:
            return (
                mark_task_done(paths, intent.work_item_id)
                if is_complete
                else mark_task_blocked(paths, intent.work_item_id)
            )
        if self.family_id == WorkItemKind.SPEC.value:
            return (
                mark_spec_done(paths, intent.work_item_id)
                if is_complete
                else mark_spec_blocked(paths, intent.work_item_id)
            )
        if self.family_id == WorkItemKind.PROBE.value:
            return (
                mark_probe_done(paths, intent.work_item_id)
                if is_complete
                else mark_probe_blocked(paths, intent.work_item_id)
            )
        if self.family_id == WorkItemKind.INCIDENT.value:
            return (
                mark_incident_resolved(paths, intent.work_item_id)
                if is_complete
                else mark_incident_blocked(paths, intent.work_item_id)
            )
        if self.family_id == WorkItemKind.LEARNING_REQUEST.value:
            return (
                mark_learning_request_done(paths, intent.work_item_id)
                if is_complete
                else mark_learning_request_blocked(paths, intent.work_item_id)
            )
        if self.family_id == WorkItemKind.BLUEPRINT_DRAFT.value:
            return (
                approve_active_blueprint_draft(paths, intent.work_item_id)
                if is_complete
                else block_active_blueprint_draft(paths, intent.work_item_id)
            )
        raise QueueStateError(f"unsupported built-in lifecycle adapter family: {self.family_id}")

    def requeue_active(
        self,
        paths: WorkspacePaths,
        *,
        work_item_id: str,
        reason: str,
        work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
    ) -> Path:
        if self.family_id == WorkItemKind.TASK.value:
            return requeue_task(paths, work_item_id, reason=reason)
        if self.family_id == WorkItemKind.SPEC.value:
            return requeue_spec(paths, work_item_id, reason=reason)
        if self.family_id == WorkItemKind.PROBE.value:
            return requeue_probe(paths, work_item_id, reason=reason)
        if self.family_id == WorkItemKind.INCIDENT.value:
            return requeue_incident(paths, work_item_id, reason=reason)
        if self.family_id == WorkItemKind.LEARNING_REQUEST.value:
            return requeue_learning_request(paths, work_item_id, reason=reason)
        if self.family_id == WorkItemKind.BLUEPRINT_DRAFT.value:
            destination = requeue_active_blueprint_draft(paths, work_item_id)
            _append_family_requeue_reason(
                destination.parent,
                work_item_id,
                family_id=self.family_id,
                reason=reason,
            )
            return destination
        raise QueueStateError(f"unsupported built-in requeue adapter family: {self.family_id}")

    def list_open_lineage_work_ids(
        self,
        paths: WorkspacePaths,
        *,
        root_spec_id: str,
    ) -> tuple[str, ...]:
        return list_open_lineage_work_ids(paths, root_spec_id=root_spec_id)

    def list_deferred_root_spec_ids(
        self,
        paths: WorkspacePaths,
        *,
        open_root_spec_id: str,
    ) -> tuple[str, ...]:
        return list_deferred_root_spec_ids(paths, open_root_spec_id=open_root_spec_id)


def _claim_with_retry(
    *,
    selector: Callable[[], tuple[str, Path] | None],
    destination_dir: Path,
    work_item_kind: WorkItemKind,
    source_state: str,
) -> QueueClaim | None:
    while True:
        candidate = selector()
        if candidate is None:
            return None
        work_item_id, source = candidate
        destination = destination_dir / source.name
        try:
            source.replace(destination)
        except FileNotFoundError:
            continue
        return QueueClaim(
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            path=destination,
            source_state=source_state,
            source_path=source,
        )


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


def _builtin_adapter_id_for_family(family_id: str) -> str:
    adapter_id = builtin_queue_lifecycle_adapter_id_for_family(family_id)
    if adapter_id is None:
        raise RuntimeError(f"missing built-in queue lifecycle adapter id for family {family_id}")
    return adapter_id


def builtin_queue_family_adapters() -> tuple[BuiltinWorkFamilyQueueAdapter, ...]:
    return (
        BuiltinWorkFamilyQueueAdapter(
            adapter_id=_builtin_adapter_id_for_family(WorkItemKind.TASK.value),
            family_id=WorkItemKind.TASK.value,
            plane=Plane.EXECUTION,
            active_relative_dir="tasks/active",
            file_extension=".md",
        ),
        BuiltinWorkFamilyQueueAdapter(
            adapter_id=_builtin_adapter_id_for_family(WorkItemKind.SPEC.value),
            family_id=WorkItemKind.SPEC.value,
            plane=Plane.PLANNING,
            active_relative_dir="specs/active",
            file_extension=".md",
        ),
        BuiltinWorkFamilyQueueAdapter(
            adapter_id=_builtin_adapter_id_for_family(WorkItemKind.PROBE.value),
            family_id=WorkItemKind.PROBE.value,
            plane=Plane.PLANNING,
            active_relative_dir="probes/active",
            file_extension=".md",
        ),
        BuiltinWorkFamilyQueueAdapter(
            adapter_id=_builtin_adapter_id_for_family(WorkItemKind.INCIDENT.value),
            family_id=WorkItemKind.INCIDENT.value,
            plane=Plane.PLANNING,
            active_relative_dir="incidents/active",
            file_extension=".md",
        ),
        BuiltinWorkFamilyQueueAdapter(
            adapter_id=_builtin_adapter_id_for_family(WorkItemKind.LEARNING_REQUEST.value),
            family_id=WorkItemKind.LEARNING_REQUEST.value,
            plane=Plane.LEARNING,
            active_relative_dir="learning/requests/active",
            file_extension=".md",
        ),
    )


__all__ = ["BuiltinWorkFamilyQueueAdapter", "builtin_queue_family_adapters"]
