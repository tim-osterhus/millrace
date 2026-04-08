"""Typed contracts for off-path meta-harness lab requests, proposals, and comparisons."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator

from .contract_core import ContractModel, _normalize_datetime, _normalize_sequence
from .contract_harness import (
    HarnessCandidateCompoundingPolicy,
    HarnessCandidatePromptAssetOverride,
    HarnessChangedSurface,
    _normalize_identifier,
    _normalize_text,
)

LAB_HARNESS_SCHEMA_VERSION = "1.0"


class LabHarnessRequestSourceKind(str, Enum):
    """Supported runtime-truth source kinds for the off-path lab pipeline."""

    RECOMMENDATION = "recommendation"


class LabHarnessProposalState(str, Enum):
    """Lifecycle state for one lab-owned proposal artifact."""

    PROPOSAL = "proposal"


class LabHarnessRequestArtifact(ContractModel):
    """Persisted manual/tightly bounded request for one lab pipeline run."""

    schema_version: Literal["1.0"] = LAB_HARNESS_SCHEMA_VERSION
    request_id: str
    source_kind: LabHarnessRequestSourceKind = LabHarnessRequestSourceKind.RECOMMENDATION
    source_recommendation_id: str
    source_search_id: str
    source_candidate_ids: tuple[str, ...] = ()
    source_benchmark_result_ids: tuple[str, ...] = ()
    created_at: datetime
    created_by: str

    @field_validator("request_id", "source_recommendation_id", "source_search_id")
    @classmethod
    def validate_identifiers(cls, value: str, info: Any) -> str:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("created_by")
    @classmethod
    def validate_created_by(cls, value: str) -> str:
        return _normalize_text(value, field_label="created_by") or ""

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("source_candidate_ids", "source_benchmark_result_ids", mode="before")
    @classmethod
    def normalize_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class LabHarnessProposalArtifact(ContractModel):
    """Lab-owned proposal derived from persisted runtime harness outputs."""

    schema_version: Literal["1.0"] = LAB_HARNESS_SCHEMA_VERSION
    proposal_id: str
    request_id: str
    source_candidate_id: str
    source_benchmark_result_id: str | None = None
    state: LabHarnessProposalState = LabHarnessProposalState.PROPOSAL
    name: str
    summary: str
    changed_surfaces: tuple[HarnessChangedSurface, ...]
    compounding_policy_override: HarnessCandidateCompoundingPolicy | None = None
    prompt_asset_overrides: tuple[HarnessCandidatePromptAssetOverride, ...] = ()
    created_at: datetime
    created_by: str

    @field_validator("proposal_id", "request_id", "source_candidate_id", "source_benchmark_result_id")
    @classmethod
    def validate_identifiers(cls, value: str | None, info: Any) -> str | None:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("name", "summary", "created_by")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("changed_surfaces", mode="before")
    @classmethod
    def normalize_changed_surfaces(
        cls,
        value: tuple[HarnessChangedSurface, ...] | list[HarnessChangedSurface] | tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> tuple[HarnessChangedSurface, ...]:
        if not value:
            raise ValueError("changed_surfaces may not be empty")
        return tuple(
            item if isinstance(item, HarnessChangedSurface) else HarnessChangedSurface.model_validate(item)
            for item in value
        )

    @field_validator("prompt_asset_overrides", mode="before")
    @classmethod
    def normalize_prompt_asset_overrides(
        cls,
        value: tuple[HarnessCandidatePromptAssetOverride, ...]
        | list[HarnessCandidatePromptAssetOverride]
        | tuple[dict[str, Any], ...]
        | list[dict[str, Any]]
        | None,
    ) -> tuple[HarnessCandidatePromptAssetOverride, ...]:
        if not value:
            return ()
        return tuple(
            item
            if isinstance(item, HarnessCandidatePromptAssetOverride)
            else HarnessCandidatePromptAssetOverride.model_validate(item)
            for item in value
        )


class LabHarnessComparisonRow(ContractModel):
    """One deterministic source-result to lab-proposal comparison row."""

    source_candidate_id: str
    source_benchmark_result_id: str | None = None
    proposal_id: str
    benchmark_status: str
    benchmark_outcome: str
    selection_changed: bool = False
    changed_config_fields: tuple[str, ...] = ()
    changed_stage_bindings: tuple[str, ...] = ()
    budget_delta_characters: int = 0
    summary: str

    @field_validator("source_candidate_id", "source_benchmark_result_id", "proposal_id")
    @classmethod
    def validate_identifiers(cls, value: str | None, info: Any) -> str | None:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("benchmark_status", "benchmark_outcome", "summary")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("changed_config_fields", "changed_stage_bindings", mode="before")
    @classmethod
    def normalize_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class LabHarnessComparisonArtifact(ContractModel):
    """Persisted comparison report for one off-path lab pipeline run."""

    schema_version: Literal["1.0"] = LAB_HARNESS_SCHEMA_VERSION
    comparison_id: str
    request_id: str
    source_recommendation_id: str
    proposal_ids: tuple[str, ...] = ()
    rows: tuple[LabHarnessComparisonRow, ...] = ()
    summary: str
    created_at: datetime
    created_by: str

    @field_validator("comparison_id", "request_id", "source_recommendation_id", mode="before")
    @classmethod
    def validate_identifiers(cls, value: str, info: Any) -> str:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("summary", "created_by")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("proposal_ids", mode="before")
    @classmethod
    def normalize_proposal_ids(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])

    @field_validator("rows", mode="before")
    @classmethod
    def normalize_rows(
        cls,
        value: tuple[LabHarnessComparisonRow, ...] | list[LabHarnessComparisonRow] | tuple[dict[str, Any], ...] | list[dict[str, Any]] | None,
    ) -> tuple[LabHarnessComparisonRow, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, LabHarnessComparisonRow) else LabHarnessComparisonRow.model_validate(item)
            for item in value
        )
