"""Recovery-counter contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .enums import WorkItemKind
from .work_refs import coerce_family_and_kind

_LEGACY_COUNTER_IDS = frozenset({
    "troubleshoot_attempt_count",
    "mechanic_attempt_count",
    "fix_cycle_count",
    "consultant_invocations",
})


class RecoveryCounterEntry(ContractModel):
    """Generic scoped recovery counter record.

    Canonical counter data is stored in the generic ``counters`` dict keyed by
    ``counter_id``.  Legacy fixed fields (``troubleshoot_attempt_count``,
    ``mechanic_attempt_count``, ``fix_cycle_count``, ``consultant_invocations``)
    are compatibility projections derived from the generic store.

    The composite ``(work_item_family_id, work_item_id, failure_class)`` acts
    as the runtime scope_key.
    """

    failure_class: str
    work_item_id: str
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
    counters: dict[str, int] = Field(default_factory=dict)
    troubleshoot_attempt_count: int = 0
    mechanic_attempt_count: int = 0
    fix_cycle_count: int = 0
    consultant_invocations: int = 0
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

        # Validate legacy fixed fields for negative values before migration
        # and projection.  Otherwise a negative legacy value would be silently
        # replaced by the projection step and never caught.
        for legacy_id in _LEGACY_COUNTER_IDS:
            legacy_value = getattr(self, legacy_id)
            if legacy_value < 0:
                raise ValueError(f"counter {legacy_id} must be >= 0")

        # Migrate legacy fixed fields into generic counters dict when the
        # dict is empty but legacy fields are non-zero (loading old state).
        migrated = dict(self.counters)
        for legacy_id in _LEGACY_COUNTER_IDS:
            legacy_value = getattr(self, legacy_id)
            if legacy_value > 0 and legacy_id not in migrated:
                migrated[legacy_id] = legacy_value
        if migrated != self.counters:
            self.counters = migrated

        # Project generic counters back into legacy compatibility fields.
        self.troubleshoot_attempt_count = self.counters.get("troubleshoot_attempt_count", 0)
        self.mechanic_attempt_count = self.counters.get("mechanic_attempt_count", 0)
        self.fix_cycle_count = self.counters.get("fix_cycle_count", 0)
        self.consultant_invocations = self.counters.get("consultant_invocations", 0)

        # Validate all counter values are non-negative.
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
