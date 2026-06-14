"""Recovery-counter contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .enums import WorkItemKind
from .work_refs import coerce_family_and_kind


class RecoveryCounterEntry(ContractModel):
    """Generic scoped recovery counter record.

    Canonical counter data is stored in the generic ``counters`` dict keyed by
    ``counter_id``.

    The composite ``(work_item_family_id, work_item_id, failure_class)`` acts
    as the runtime scope_key.
    """

    failure_class: str
    work_item_id: str
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
    counters: dict[str, int] = Field(default_factory=dict)
    last_updated_at: datetime

    @model_validator(mode="after")
    def validate_non_negative_counts(self) -> "RecoveryCounterEntry":
        family_id, work_item_kind = coerce_family_and_kind(
            family_id=self.work_item_family_id,
            work_item_kind=self.work_item_kind,
        )
        if family_id is None:
            raise ValueError("recovery counter entry requires work_item_family_id or work_item_kind")
        self.work_item_family_id = family_id
        self.work_item_kind = work_item_kind

        for counter_id, count in self.counters.items():
            if count < 0:
                raise ValueError(f"counter {counter_id} must be >= 0")
        return self

    @property
    def scope_key(self) -> str:
        """Stable scope key for this counter record."""
        return f"{self.work_item_family_id}:{self.work_item_id}:{self.failure_class}"


class RecoveryCounters(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["recovery_counters"] = "recovery_counters"
    entries: tuple[RecoveryCounterEntry, ...] = ()


__all__ = ["RecoveryCounterEntry", "RecoveryCounters"]
