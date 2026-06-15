"""Contracts for runtime-owned workspace history entries."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_serializer

from .base import ContractModel


class ProposedHistoryEntry(ContractModel):
    """Stage-authored history proposal before runtime identity normalization."""

    schema_version: Literal[1] = 1
    summary: str
    changed_paths: tuple[str, ...] = ()
    evidence_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    history_entry_id: str | None = None
    date: str | None = None
    occurred_at: datetime | None = None
    run_id: str | None = None
    request_id: str | None = None
    plane: str | None = None
    stage: str | None = None
    node_id: str | None = None
    work_item_kind: str | None = None
    work_item_id: str | None = None
    terminal_result: str | None = None

    @field_serializer("occurred_at")
    def serialize_occurred_at(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None


class CanonicalHistoryEntry(ContractModel):
    """Runtime-authored canonical history entry stored in JSONL."""

    schema_version: Literal[1] = 1
    history_entry_id: str
    date: str
    occurred_at: datetime
    run_id: str
    request_id: str
    plane: str
    stage: str
    node_id: str
    work_item_kind: str
    work_item_id: str
    terminal_result: str
    summary: str
    changed_paths: tuple[str, ...] = Field(default_factory=tuple)
    evidence_paths: tuple[str, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)

    @field_serializer("occurred_at")
    def serialize_occurred_at(self, value: datetime) -> str:
        return value.isoformat()


__all__ = ["CanonicalHistoryEntry", "ProposedHistoryEntry"]
