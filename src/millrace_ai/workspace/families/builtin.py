"""Built-in work-family queue adapter implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.architecture.workflow_primitives import (
    builtin_queue_lifecycle_adapter_id_for_family,
)
from millrace_ai.contracts import Plane, WorkItemKind
from millrace_ai.errors import QueueStateError

from ..paths import WorkspacePaths
from ..queue_claims import QueueClaim
from ..queue_selection import (
    list_deferred_root_spec_ids,
    list_open_lineage_work_ids,
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
        from ..queue_family_interpreter import QueueFamilyInterpreter
        from ..queue_selection import _make_pydantic_document_validator

        interpreter = QueueFamilyInterpreter(paths, families=work_item_families)
        return interpreter.claim_next(
            self.family_id,
            root_spec_id=root_spec_id,
            document_validator=_make_pydantic_document_validator(self.family_id),
        )

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
        from ..queue_family_interpreter import QueueFamilyInterpreter

        if intent.work_item_family_id != self.family_id:
            raise QueueStateError(
                f"lifecycle intent family {intent.work_item_family_id} does not match adapter "
                f"family {self.family_id}"
            )
        interpreter = QueueFamilyInterpreter(paths, families=work_item_families)
        is_complete = intent.action.value == "complete"
        if is_complete:
            return interpreter.move_active_to_done(self.family_id, intent.work_item_id)
        return interpreter.move_active_to_blocked(self.family_id, intent.work_item_id)

    def requeue_active(
        self,
        paths: WorkspacePaths,
        *,
        work_item_id: str,
        reason: str,
        work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
    ) -> Path:
        from ..queue_family_interpreter import QueueFamilyInterpreter

        interpreter = QueueFamilyInterpreter(paths, families=work_item_families)
        return interpreter.move_active_to_queue(self.family_id, work_item_id, reason=reason)

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
