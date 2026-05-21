"""Recovery-counter contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import model_validator

from .base import ContractModel
from .enums import WorkItemKind
from .work_refs import coerce_family_and_kind


class RecoveryCounterEntry(ContractModel):
    failure_class: str
    work_item_id: str
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
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
        if self.troubleshoot_attempt_count < 0:
            raise ValueError("troubleshoot_attempt_count must be >= 0")
        if self.mechanic_attempt_count < 0:
            raise ValueError("mechanic_attempt_count must be >= 0")
        if self.fix_cycle_count < 0:
            raise ValueError("fix_cycle_count must be >= 0")
        if self.consultant_invocations < 0:
            raise ValueError("consultant_invocations must be >= 0")
        return self


class RecoveryCounters(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["recovery_counters"] = "recovery_counters"
    entries: tuple[RecoveryCounterEntry, ...] = ()


__all__ = ["RecoveryCounterEntry", "RecoveryCounters"]
