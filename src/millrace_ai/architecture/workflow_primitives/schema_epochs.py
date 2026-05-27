"""Workspace schema epoch contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationInfo, field_validator

from ..common import normalize_nonempty_text
from ..stage_kinds import ArchitectureContractModel
from ._validation import _canonical, _ensure_sequence, _normalize_unique_id_tuple


class WorkspaceSchemaEpochDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["workspace_schema_epoch"] = "workspace_schema_epoch"
    epoch_id: str
    minimum_supported_epoch_id: str
    archive_required_from_epoch_ids: tuple[str, ...] = ()
    reset_command: str
    compatibility_notes: tuple[str, ...] = ()

    @field_validator("epoch_id", "minimum_supported_epoch_id")
    @classmethod
    def validate_epoch_id(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("archive_required_from_epoch_ids", mode="before")
    @classmethod
    def normalize_archive_epochs(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label="archive_required_from_epoch_ids",
            allow_empty=True,
        )

    @field_validator("reset_command")
    @classmethod
    def validate_reset_command(cls, value: str) -> str:
        return normalize_nonempty_text(value, field_label="reset_command")

    @field_validator("compatibility_notes", mode="before")
    @classmethod
    def normalize_compatibility_notes(cls, value: object) -> tuple[str, ...]:
        raw = _ensure_sequence(value, field_label="compatibility_notes", allow_empty=True)
        notes = [normalize_nonempty_text(str(item), field_label="compatibility_notes") for item in raw]
        return tuple(notes)
