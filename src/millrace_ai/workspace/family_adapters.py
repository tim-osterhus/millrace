"""Work-family queue adapter contracts and registry."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.architecture.workflow_primitives import (
    builtin_queue_lifecycle_adapter_id_for_family,
)

from .families import builtin_work_family_queue_adapters
from .paths import WorkspacePaths
from .queue_claims import QueueClaim

if TYPE_CHECKING:
    from millrace_ai.runtime.effects import SourceLifecycleIntent


class WorkFamilyQueueAdapter(Protocol):
    @property
    def adapter_id(self) -> str:
        """Stable adapter identifier referenced by work-item family assets."""

    @property
    def family_id(self) -> str:
        """Work-item family handled by this adapter."""

    def claim_next(
        self,
        paths: WorkspacePaths,
        *,
        root_spec_id: str | None = None,
        work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
    ) -> QueueClaim | None:
        """Claim the next queued item for this family."""

    def active_path(
        self,
        paths: WorkspacePaths,
        *,
        work_item_id: str,
    ) -> Path:
        """Return the canonical active-path for one work item id."""

    def apply_lifecycle(
        self,
        paths: WorkspacePaths,
        *,
        intent: "SourceLifecycleIntent",
        work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
    ) -> Path:
        """Apply lifecycle completion/blocking for one active item."""

    def requeue_active(
        self,
        paths: WorkspacePaths,
        *,
        work_item_id: str,
        reason: str,
        work_item_families: tuple[WorkItemFamilyDefinition, ...] | None = None,
    ) -> Path:
        """Move one active work item back to its queue state."""

    def list_open_lineage_work_ids(
        self,
        paths: WorkspacePaths,
        *,
        root_spec_id: str,
    ) -> tuple[str, ...]:
        """Return open lineage work ids for a root spec."""

    def list_deferred_root_spec_ids(
        self,
        paths: WorkspacePaths,
        *,
        open_root_spec_id: str,
    ) -> tuple[str, ...]:
        """Return root spec ids deferred while closure drains one lineage."""


def _index_by_adapter_id(
    adapters: tuple[WorkFamilyQueueAdapter, ...],
) -> dict[str, WorkFamilyQueueAdapter]:
    by_id: dict[str, WorkFamilyQueueAdapter] = {}
    for adapter in adapters:
        existing = by_id.get(adapter.adapter_id)
        if existing is not None:
            raise RuntimeError(f"duplicate work-family queue adapter id: {adapter.adapter_id}")
        by_id[adapter.adapter_id] = adapter
    return by_id


def _index_by_family_id(
    adapters: tuple[WorkFamilyQueueAdapter, ...],
) -> dict[str, WorkFamilyQueueAdapter]:
    by_family_id: dict[str, WorkFamilyQueueAdapter] = {}
    for adapter in adapters:
        existing = by_family_id.get(adapter.family_id)
        if existing is not None:
            raise RuntimeError(
                "duplicate work-family queue adapter for family "
                f"{adapter.family_id}: {existing.adapter_id} and {adapter.adapter_id}"
            )
        by_family_id[adapter.family_id] = adapter
    return by_family_id


_REGISTERED_QUEUE_ADAPTERS: tuple[WorkFamilyQueueAdapter, ...] = builtin_work_family_queue_adapters()
_QUEUE_ADAPTERS_BY_ID = _index_by_adapter_id(_REGISTERED_QUEUE_ADAPTERS)
_QUEUE_ADAPTERS_BY_FAMILY_ID = _index_by_family_id(_REGISTERED_QUEUE_ADAPTERS)


def registered_work_family_queue_adapters() -> tuple[WorkFamilyQueueAdapter, ...]:
    return _REGISTERED_QUEUE_ADAPTERS


def queue_adapter_for_id(adapter_id: str) -> WorkFamilyQueueAdapter | None:
    return _QUEUE_ADAPTERS_BY_ID.get(adapter_id)


def queue_adapter_for_family_id(family_id: str) -> WorkFamilyQueueAdapter | None:
    return _QUEUE_ADAPTERS_BY_FAMILY_ID.get(family_id)


def resolve_queue_lifecycle_adapter_id(
    family: WorkItemFamilyDefinition,
) -> str | None:
    if family.queue_lifecycle_adapter_id is not None:
        return family.queue_lifecycle_adapter_id
    return builtin_queue_lifecycle_adapter_id_for_family(family.family_id)


__all__ = [
    "WorkFamilyQueueAdapter",
    "queue_adapter_for_family_id",
    "queue_adapter_for_id",
    "registered_work_family_queue_adapters",
    "resolve_queue_lifecycle_adapter_id",
]
