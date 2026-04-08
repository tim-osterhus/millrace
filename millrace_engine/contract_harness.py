"""Typed contracts for governed harness candidates and benchmark results."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from .contract_core import ContractModel, StageType, _normalize_datetime, _normalize_sequence

HARNESS_SCHEMA_VERSION = "1.0"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _normalize_identifier(value: str | None, *, field_label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(
            f"{field_label} must contain only letters, digits, dots, underscores, colons, or hyphens"
        )
    return normalized


def _normalize_text(value: str | None, *, field_label: str) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    return normalized


class HarnessChangedSurfaceKind(str, Enum):
    """High-level runtime surface changed by one harness candidate."""

    CONFIG = "config"
    PROMPT_ASSET = "prompt_asset"
    RETRIEVAL = "retrieval"


class HarnessCandidateState(str, Enum):
    """Explicit governance state for one harness candidate."""

    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class HarnessRecommendationDisposition(str, Enum):
    """Conservative bounded-search recommendation outcome."""

    RECOMMEND = "recommend"
    NO_CHANGE = "no_change"


class HarnessBenchmarkStatus(str, Enum):
    """Execution status for one bounded benchmark run."""

    COMPLETE = "complete"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class HarnessBenchmarkOutcome(str, Enum):
    """Meaningful comparison outcome for one bounded benchmark run."""

    CHANGED = "changed"
    UNCHANGED = "unchanged"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class HarnessCandidateCompoundingPolicy(ContractModel):
    """Candidate-owned compounding policy override payload."""

    profile: Literal["baseline", "compounding", "governed_plus", "lab"] = "compounding"
    governed_plus_budget_characters: int = Field(default=3200, ge=1)


class HarnessCandidatePromptAssetOverride(ContractModel):
    """Candidate-owned prompt asset override for one stage."""

    stage: StageType
    source_ref: str
    candidate_prompt_file: Path

    @field_validator("source_ref")
    @classmethod
    def validate_source_ref(cls, value: str) -> str:
        return _normalize_text(value, field_label="source_ref") or ""

    @field_validator("candidate_prompt_file", mode="before")
    @classmethod
    def normalize_candidate_prompt_file(cls, value: str | Path) -> Path:
        return Path(value)


class HarnessChangedSurface(ContractModel):
    """One explicit candidate-owned surface mutation descriptor."""

    kind: HarnessChangedSurfaceKind
    target: str
    summary: str

    @field_validator("target", "summary")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""


class HarnessCandidateArtifact(ContractModel):
    """Persisted governed harness candidate awaiting review or comparison."""

    schema_version: Literal["1.0"] = HARNESS_SCHEMA_VERSION
    candidate_id: str
    name: str
    baseline_ref: str
    benchmark_suite_ref: str
    state: HarnessCandidateState = HarnessCandidateState.CANDIDATE
    changed_surfaces: tuple[HarnessChangedSurface, ...]
    compounding_policy_override: HarnessCandidateCompoundingPolicy | None = None
    prompt_asset_overrides: tuple[HarnessCandidatePromptAssetOverride, ...] = ()
    reviewer_note: str | None = None
    created_at: datetime
    created_by: str

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        return _normalize_identifier(value, field_label="candidate_id") or ""

    @field_validator("name", "baseline_ref", "benchmark_suite_ref", "created_by", "reviewer_note")
    @classmethod
    def validate_text_fields(cls, value: str | None, info: Any) -> str | None:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value"))

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


class HarnessSearchAssetTarget(ContractModel):
    """One declared asset-level search target."""

    stage: StageType
    source_ref: str

    @field_validator("source_ref")
    @classmethod
    def validate_source_ref(cls, value: str) -> str:
        return _normalize_text(value, field_label="source_ref") or ""


class HarnessSearchRequestArtifact(ContractModel):
    """Persisted bounded config/assets-only search request."""

    schema_version: Literal["1.0"] = HARNESS_SCHEMA_VERSION
    search_id: str
    baseline_ref: str
    benchmark_suite_ref: str
    config_variants: tuple[HarnessCandidateCompoundingPolicy, ...] = ()
    asset_targets: tuple[HarnessSearchAssetTarget, ...] = ()
    created_at: datetime
    created_by: str

    @field_validator("search_id")
    @classmethod
    def validate_search_id(cls, value: str) -> str:
        return _normalize_identifier(value, field_label="search_id") or ""

    @field_validator("baseline_ref", "benchmark_suite_ref", "created_by")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("config_variants", mode="before")
    @classmethod
    def normalize_config_variants(
        cls,
        value: tuple[HarnessCandidateCompoundingPolicy, ...]
        | list[HarnessCandidateCompoundingPolicy]
        | tuple[dict[str, Any], ...]
        | list[dict[str, Any]]
        | None,
    ) -> tuple[HarnessCandidateCompoundingPolicy, ...]:
        if not value:
            return ()
        return tuple(
            item
            if isinstance(item, HarnessCandidateCompoundingPolicy)
            else HarnessCandidateCompoundingPolicy.model_validate(item)
            for item in value
        )

    @field_validator("asset_targets", mode="before")
    @classmethod
    def normalize_asset_targets(
        cls,
        value: tuple[HarnessSearchAssetTarget, ...]
        | list[HarnessSearchAssetTarget]
        | tuple[dict[str, Any], ...]
        | list[dict[str, Any]]
        | None,
    ) -> tuple[HarnessSearchAssetTarget, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, HarnessSearchAssetTarget) else HarnessSearchAssetTarget.model_validate(item)
            for item in value
        )

    @field_validator("asset_targets")
    @classmethod
    def validate_nonempty_scope(
        cls,
        value: tuple[HarnessSearchAssetTarget, ...],
        info: Any,
    ) -> tuple[HarnessSearchAssetTarget, ...]:
        config_variants = getattr(info, "data", {}).get("config_variants", ())
        if not config_variants and not value:
            raise ValueError("search request must declare at least one config or asset target")
        return value


class HarnessBenchmarkOutcomeSummary(ContractModel):
    """Typed summary of the observed baseline-versus-candidate comparison."""

    selection_changed: bool
    changed_config_fields: tuple[str, ...] = ()
    changed_stage_bindings: tuple[str, ...] = ()
    baseline_mode_ref: str
    candidate_mode_ref: str
    message: str

    @field_validator("changed_config_fields", "changed_stage_bindings", mode="before")
    @classmethod
    def normalize_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])

    @field_validator("baseline_mode_ref", "candidate_mode_ref", "message")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""


class HarnessBenchmarkCostSummary(ContractModel):
    """Typed summary of the compounding-budget cost delta under comparison."""

    baseline_governed_plus_budget_characters: int = Field(ge=0)
    candidate_governed_plus_budget_characters: int = Field(ge=0)
    budget_delta_characters: int


class HarnessBenchmarkResult(ContractModel):
    """Persisted bounded benchmark result for one harness candidate."""

    schema_version: Literal["1.0"] = HARNESS_SCHEMA_VERSION
    result_id: str
    candidate_id: str
    baseline_ref: str
    benchmark_suite_ref: str
    status: HarnessBenchmarkStatus
    outcome: HarnessBenchmarkOutcome
    started_at: datetime
    completed_at: datetime
    outcome_summary: HarnessBenchmarkOutcomeSummary
    cost_summary: HarnessBenchmarkCostSummary
    artifact_refs: tuple[str, ...] = ()

    @field_validator("result_id", "candidate_id")
    @classmethod
    def validate_identifiers(cls, value: str, info: Any) -> str:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("baseline_ref", "benchmark_suite_ref", mode="before")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def normalize_artifact_refs(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class HarnessRecommendationArtifact(ContractModel):
    """Persisted bounded recommendation backed by benchmark evidence."""

    schema_version: Literal["1.0"] = HARNESS_SCHEMA_VERSION
    recommendation_id: str
    search_id: str
    disposition: HarnessRecommendationDisposition
    recommended_candidate_id: str | None = None
    recommended_result_id: str | None = None
    candidate_ids: tuple[str, ...] = ()
    benchmark_result_ids: tuple[str, ...] = ()
    summary: str
    created_at: datetime
    created_by: str

    @field_validator("recommendation_id", "search_id", "recommended_candidate_id", "recommended_result_id")
    @classmethod
    def validate_identifiers(cls, value: str | None, info: Any) -> str | None:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("summary", "created_by")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("candidate_ids", "benchmark_result_ids", mode="before")
    @classmethod
    def normalize_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])
