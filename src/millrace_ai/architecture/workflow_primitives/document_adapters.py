"""Document adapter contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ..stage_kinds import ArchitectureContractModel
from ._validation import (
    _canonical,
    _ensure_sequence,
    _normalize_file_extension,
    _normalize_unique_id_tuple,
    _reject_duplicates,
)
from .identifiers import DocumentAdapterId, WorkItemFamilyId


class WorkItemDocumentAdapterDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["work_item_document_adapter"] = "work_item_document_adapter"
    adapter_id: DocumentAdapterId
    schema_id: str
    supported_file_extensions: tuple[str, ...] = Field(min_length=1)
    family_ids: tuple[WorkItemFamilyId, ...] = Field(min_length=1)
    can_parse: bool
    can_render: bool
    can_summarize: bool
    supports_dependencies: bool
    supports_lineage: bool

    @field_validator("adapter_id", "schema_id")
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("family_ids", mode="before")
    @classmethod
    def normalize_family_ids(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="family_ids", allow_empty=False)

    @field_validator("supported_file_extensions", mode="before")
    @classmethod
    def normalize_extensions(cls, value: object) -> tuple[str, ...]:
        raw = _ensure_sequence(value, field_label="supported_file_extensions")
        normalized = [
            _normalize_file_extension(str(item), field_label="supported_file_extensions")
            for item in raw
        ]
        return _reject_duplicates(normalized, field_label="supported_file_extensions")

    @model_validator(mode="after")
    def validate_capabilities(self) -> "WorkItemDocumentAdapterDefinition":
        if not (self.can_parse or self.can_render or self.can_summarize):
            raise ValueError("document adapter must support at least one operation")
        return self
