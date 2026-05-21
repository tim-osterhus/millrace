"""Shared queue claim value objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from millrace_ai.contracts import Plane, WorkItemKind
from millrace_ai.contracts.work_refs import (
    coerce_family_and_kind,
    plane_for_work_item_family_id,
)


@dataclass(frozen=True, slots=True)
class QueueClaim:
    """Represents ownership of a newly-claimed work item."""

    work_item_id: str
    path: Path
    work_item_kind: WorkItemKind | None = None
    family_id: str | None = None
    plane: Plane | None = None
    source_state: str | None = None
    source_path: Path | None = None
    claim_policy_id: str | None = None
    claim_order: int | None = None

    def __post_init__(self) -> None:
        family_id, work_item_kind = coerce_family_and_kind(
            family_id=self.family_id,
            work_item_kind=self.work_item_kind,
        )
        if family_id is None:
            raise ValueError("QueueClaim requires family_id or work_item_kind")
        plane = self.plane or plane_for_work_item_family_id(family_id)
        object.__setattr__(self, "family_id", family_id)
        object.__setattr__(self, "work_item_kind", work_item_kind)
        object.__setattr__(self, "plane", plane)
        if self.claim_order is not None and self.claim_order < 0:
            raise ValueError("QueueClaim claim_order must be >= 0")


__all__ = ["QueueClaim"]
