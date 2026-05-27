"""Artifact contract definitions for workflow primitives."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ..common import normalize_canonical_id
from ..stage_kinds import ArchitectureContractModel
from ._validation import (
    _ensure_sequence,
    _normalize_artifact_filename,
    _normalize_unique_id_tuple,
    _reject_duplicates,
)
from .identifiers import ArtifactContractId, RuntimeEffectHandlerId, WorkItemFamilyId


class ArtifactFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"
    TEXT = "text"
    DIRECTORY = "directory"


class ArtifactFilenameAdapterDefinition(ArchitectureContractModel):
    filename: str
    format: ArtifactFormat
    parser_id: str
    renderer_id: str | None = None

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return _normalize_artifact_filename(value, field_label="filename")

    @field_validator("parser_id", "renderer_id")
    @classmethod
    def validate_adapter_id(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return normalize_canonical_id(value, field_label=info.field_name or "adapter id")


class ArtifactContractDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["artifact_contract"] = "artifact_contract"
    artifact_id: ArtifactContractId
    canonical_filename: str
    accepted_filenames: tuple[str, ...] = ()
    preferred_format: ArtifactFormat
    schema_id: str
    filename_adapters: tuple[ArtifactFilenameAdapterDefinition, ...] = Field(min_length=1)
    producer_stage_kind_ids: tuple[str, ...] = ()
    consumer_handler_ids: tuple[RuntimeEffectHandlerId, ...] = ()
    consumer_operation_ids: tuple[str, ...] = ()
    destination_family_id: WorkItemFamilyId | None = None

    @field_validator("artifact_id", "schema_id", "destination_family_id")
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return normalize_canonical_id(value, field_label=info.field_name or "artifact contract id")

    @field_validator("canonical_filename")
    @classmethod
    def validate_canonical_filename(cls, value: str) -> str:
        return _normalize_artifact_filename(value, field_label="canonical_filename")

    @field_validator("accepted_filenames", mode="before")
    @classmethod
    def normalize_accepted_filenames(cls, value: object) -> tuple[str, ...]:
        raw = _ensure_sequence(value, field_label="accepted_filenames", allow_empty=True)
        normalized = [
            _normalize_artifact_filename(str(item), field_label="accepted_filenames")
            for item in raw
        ]
        return _reject_duplicates(normalized, field_label="accepted_filenames")

    @field_validator(
        "producer_stage_kind_ids",
        "consumer_handler_ids",
        "consumer_operation_ids",
        mode="before",
    )
    @classmethod
    def normalize_reference_ids(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "artifact contract reference ids",
            allow_empty=True,
        )

    @model_validator(mode="after")
    def validate_filename_adapters(self) -> "ArtifactContractDefinition":
        filenames = self.all_filenames
        if len(set(filenames)) != len(filenames):
            raise ValueError("duplicate artifact filename")
        adapter_names = [adapter.filename for adapter in self.filename_adapters]
        if len(set(adapter_names)) != len(adapter_names):
            raise ValueError("duplicate filename_adapters filename")
        declared = set(filenames)
        adapted = set(adapter_names)
        missing = sorted(declared - adapted)
        if missing:
            raise ValueError(
                "filename_adapters must define parser semantics for every artifact filename: "
                + ", ".join(missing)
            )
        extra = sorted(adapted - declared)
        if extra:
            raise ValueError(
                "filename_adapters may only reference declared artifact filenames: "
                + ", ".join(extra)
            )
        canonical_adapter = self.filename_adapters_by_name[self.canonical_filename]
        if canonical_adapter.format is not self.preferred_format:
            raise ValueError("canonical filename adapter format must match preferred_format")
        return self

    @property
    def all_filenames(self) -> tuple[str, ...]:
        return (self.canonical_filename, *self.accepted_filenames)

    @property
    def filename_adapters_by_name(self) -> dict[str, ArtifactFilenameAdapterDefinition]:
        return {adapter.filename: adapter for adapter in self.filename_adapters}
