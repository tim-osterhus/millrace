"""Typed contracts for runtime-owned compounding artifacts."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator

from .contract_core import ContractModel, StageType, _normalize_datetime, _normalize_sequence

COMPOUNDING_SCHEMA_VERSION = "1.0"
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
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    return normalized


class ProcedureScope(str, Enum):
    """Governed reuse scope for one runtime-learned procedure."""

    RUN = "run"
    WORKSPACE = "workspace"


class ProcedureLifecycleState(str, Enum):
    """Explicit lifecycle state for one reusable procedure."""

    CANDIDATE = "candidate"
    PROMOTED = "promoted"
    DEPRECATED = "deprecated"


class ProcedureUsageDisposition(str, Enum):
    """How one procedure participated in one run/stage."""

    CONSIDERED = "considered"
    INJECTED = "injected"
    SKIPPED = "skipped"


class CompoundingFlushMilestone(str, Enum):
    """Runtime milestone that finalized pending governed compounding artifacts."""

    STAGE_SUCCESS = "stage_success"
    RECOVERY_SUCCESS = "recovery_success"
    RUN_CLOSEOUT = "run_closeout"


class CompoundingKnowledgeFamily(str, Enum):
    """Governed compounding family represented in derived orientation artifacts."""

    PROCEDURE = "procedure"
    CONTEXT_FACT = "context_fact"
    HARNESS_CANDIDATE = "harness_candidate"
    HARNESS_BENCHMARK = "harness_benchmark"
    HARNESS_RECOMMENDATION = "harness_recommendation"


class CompoundingRelationshipKind(str, Enum):
    """Deterministic relationship cluster kind for orientation summaries."""

    SOURCE_RUN = "source_run"
    EVIDENCE_REF = "evidence_ref"
    TAG = "tag"
    BENCHMARK_CANDIDATE = "benchmark_candidate"
    RECOMMENDATION_BUNDLE = "recommendation_bundle"


class ProcedureRetrievalRule(ContractModel):
    """Stage-aware retrieval constraints for reusable procedures."""

    stage: StageType
    allowed_scopes: tuple[ProcedureScope, ...]
    allowed_source_stages: tuple[StageType, ...]
    max_procedures: int = Field(default=2, ge=1)
    max_prompt_characters: int = Field(default=2400, ge=1)

    @field_validator("allowed_scopes", mode="before")
    @classmethod
    def normalize_allowed_scopes(
        cls, value: tuple[ProcedureScope, ...] | tuple[str, ...] | list[str] | list[ProcedureScope]
    ) -> tuple[ProcedureScope, ...]:
        if not value:
            raise ValueError("allowed_scopes may not be empty")
        normalized: list[ProcedureScope] = []
        seen: set[ProcedureScope] = set()
        for item in value:
            scope = item if isinstance(item, ProcedureScope) else ProcedureScope(str(item).strip().lower())
            if scope in seen:
                continue
            seen.add(scope)
            normalized.append(scope)
        return tuple(normalized)

    @field_validator("allowed_source_stages", mode="before")
    @classmethod
    def normalize_allowed_source_stages(
        cls, value: tuple[StageType, ...] | tuple[str, ...] | list[str] | list[StageType]
    ) -> tuple[StageType, ...]:
        if not value:
            raise ValueError("allowed_source_stages may not be empty")
        normalized: list[StageType] = []
        seen: set[StageType] = set()
        for item in value:
            stage = item if isinstance(item, StageType) else StageType(str(item).strip().lower())
            if stage in seen:
                continue
            seen.add(stage)
            normalized.append(stage)
        return tuple(normalized)


class InjectedProcedure(ContractModel):
    """One reusable procedure selected for stage-context injection."""

    procedure_id: str
    scope: ProcedureScope
    source_stage: StageType
    title: str
    summary: str
    prompt_excerpt: str
    evidence_refs: tuple[str, ...] = ()
    original_characters: int = Field(default=0, ge=0)
    injected_characters: int = Field(default=0, ge=0)
    truncated: bool = False

    @field_validator("procedure_id")
    @classmethod
    def validate_procedure_id(cls, value: str) -> str:
        return _normalize_identifier(value, field_label="procedure_id") or ""

    @field_validator("title", "summary", "prompt_excerpt")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def normalize_evidence_refs(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class ConsideredProcedure(ContractModel):
    """One reusable procedure considered during stage-aware retrieval."""

    procedure_id: str
    scope: ProcedureScope
    source_stage: StageType
    title: str
    summary: str
    evidence_refs: tuple[str, ...] = ()

    @field_validator("procedure_id")
    @classmethod
    def validate_procedure_id(cls, value: str) -> str:
        return _normalize_identifier(value, field_label="procedure_id") or ""

    @field_validator("title", "summary")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def normalize_evidence_refs(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class ProcedureInjectionBundle(ContractModel):
    """Deterministic selection record for procedures injected into one stage context."""

    stage: StageType
    rule: ProcedureRetrievalRule
    considered_procedures: tuple[ConsideredProcedure, ...] = ()
    procedures: tuple[InjectedProcedure, ...] = ()
    candidate_count: int = Field(default=0, ge=0)
    selected_count: int = Field(default=0, ge=0)
    budget_characters: int = Field(default=0, ge=0)
    used_characters: int = Field(default=0, ge=0)
    truncated_count: int = Field(default=0, ge=0)

    @field_validator("considered_procedures", mode="before")
    @classmethod
    def normalize_considered_procedures(
        cls,
        value: tuple[ConsideredProcedure, ...]
        | list[ConsideredProcedure]
        | tuple[dict[str, Any], ...]
        | list[dict[str, Any]]
        | None,
    ) -> tuple[ConsideredProcedure, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, ConsideredProcedure) else ConsideredProcedure.model_validate(item)
            for item in value
        )

    @field_validator("procedures", mode="before")
    @classmethod
    def normalize_procedures(
        cls, value: tuple[InjectedProcedure, ...] | list[InjectedProcedure] | tuple[dict[str, Any], ...] | list[dict[str, Any]] | None
    ) -> tuple[InjectedProcedure, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, InjectedProcedure) else InjectedProcedure.model_validate(item)
            for item in value
        )


class ReusableProcedureArtifact(ContractModel):
    """Persisted reusable-procedure artifact extracted from runtime evidence."""

    schema_version: Literal["1.0"] = COMPOUNDING_SCHEMA_VERSION
    procedure_id: str
    scope: ProcedureScope = ProcedureScope.RUN
    source_run_id: str
    source_stage: StageType
    title: str
    summary: str
    procedure_markdown: str
    tags: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    created_at: datetime
    supersedes_procedure_id: str | None = None

    @field_validator("procedure_id", "source_run_id", "supersedes_procedure_id")
    @classmethod
    def validate_identifiers(cls, value: str | None, info: Any) -> str | None:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("title", "summary", "procedure_markdown")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("tags", "evidence_refs", mode="before")
    @classmethod
    def normalize_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class ProcedureUsageRecord(ContractModel):
    """Typed usage record for one procedure consideration/injection decision."""

    schema_version: Literal["1.0"] = COMPOUNDING_SCHEMA_VERSION
    usage_id: str
    procedure_id: str
    run_id: str
    stage: StageType
    disposition: ProcedureUsageDisposition
    recorded_at: datetime
    reason: str | None = None
    execution_ref: str | None = None

    @field_validator("usage_id", "procedure_id", "run_id", "execution_ref")
    @classmethod
    def validate_identifiers(cls, value: str | None, info: Any) -> str | None:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        return _normalize_text(value, field_label="reason")

    @field_validator("recorded_at", mode="before")
    @classmethod
    def normalize_recorded_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class ProcedureLifecycleRecord(ContractModel):
    """Reviewable lifecycle transition record for one reusable procedure."""

    schema_version: Literal["1.0"] = COMPOUNDING_SCHEMA_VERSION
    record_id: str
    procedure_id: str
    state: ProcedureLifecycleState
    scope: ProcedureScope
    changed_at: datetime
    changed_by: str
    reason: str
    replacement_procedure_id: str | None = None

    @field_validator("record_id", "procedure_id", "changed_by", "replacement_procedure_id")
    @classmethod
    def validate_identifiers(cls, value: str | None, info: Any) -> str | None:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_text(value, field_label="reason") or ""

    @field_validator("changed_at", mode="before")
    @classmethod
    def normalize_changed_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class CompoundingFlushCheckpoint(ContractModel):
    """Inspectable runtime checkpoint for one governed-compounding flush."""

    schema_version: Literal["1.0"] = COMPOUNDING_SCHEMA_VERSION
    run_id: str
    trigger_stage: StageType
    milestone: CompoundingFlushMilestone
    finalized_procedure_ids: tuple[str, ...] = ()
    finalized_context_fact_ids: tuple[str, ...] = ()

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _normalize_identifier(value, field_label="run_id") or ""

    @field_validator("finalized_procedure_ids", "finalized_context_fact_ids", mode="before")
    @classmethod
    def normalize_identifier_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            identifier = _normalize_identifier(str(item), field_label="identifier sequence value")
            if identifier is None or identifier in seen:
                continue
            seen.add(identifier)
            normalized.append(identifier)
        return tuple(normalized)


class CompoundingKnowledgeIndexEntry(ContractModel):
    """One derived orientation entry over a governed compounding artifact."""

    schema_version: Literal["1.0"] = COMPOUNDING_SCHEMA_VERSION
    entry_id: str
    family: CompoundingKnowledgeFamily
    status: str
    label: str
    summary: str
    artifact_ref: str
    source_run_id: str | None = None
    source_stage: StageType | None = None
    tags: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    related_ids: tuple[str, ...] = ()

    @field_validator("entry_id", "source_run_id")
    @classmethod
    def validate_identifiers(cls, value: str | None, info: Any) -> str | None:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("status", "label", "summary", "artifact_ref")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("tags", "evidence_refs", mode="before")
    @classmethod
    def normalize_text_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])

    @field_validator("related_ids", mode="before")
    @classmethod
    def normalize_related_ids(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            identifier = _normalize_identifier(str(item), field_label="related_ids value")
            if identifier is None or identifier in seen:
                continue
            seen.add(identifier)
            normalized.append(identifier)
        return tuple(normalized)


class CompoundingKnowledgeIndexArtifact(ContractModel):
    """Derived secondary index over governed compounding stores."""

    schema_version: Literal["1.0"] = COMPOUNDING_SCHEMA_VERSION
    generated_at: datetime
    secondary_surface_note: str
    source_families: tuple[str, ...] = ()
    family_counts: dict[str, int] = Field(default_factory=dict)
    entries: tuple[CompoundingKnowledgeIndexEntry, ...] = ()

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("secondary_surface_note")
    @classmethod
    def validate_secondary_surface_note(cls, value: str) -> str:
        return _normalize_text(value, field_label="secondary_surface_note") or ""

    @field_validator("source_families", mode="before")
    @classmethod
    def normalize_source_families(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])

    @field_validator("family_counts", mode="before")
    @classmethod
    def normalize_family_counts(cls, value: dict[str, int] | None) -> dict[str, int]:
        if not value:
            return {}
        normalized: dict[str, int] = {}
        for key in sorted(value):
            count = int(value[key])
            if count < 0:
                raise ValueError("family_counts values must be non-negative")
            normalized[str(key)] = count
        return normalized

    @field_validator("entries", mode="before")
    @classmethod
    def normalize_entries(
        cls,
        value: tuple[CompoundingKnowledgeIndexEntry, ...]
        | list[CompoundingKnowledgeIndexEntry]
        | tuple[dict[str, Any], ...]
        | list[dict[str, Any]]
        | None,
    ) -> tuple[CompoundingKnowledgeIndexEntry, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, CompoundingKnowledgeIndexEntry) else CompoundingKnowledgeIndexEntry.model_validate(item)
            for item in value
        )


class CompoundingRelationshipCluster(ContractModel):
    """One derived relationship cluster over governed compounding entries."""

    schema_version: Literal["1.0"] = COMPOUNDING_SCHEMA_VERSION
    cluster_id: str
    kind: CompoundingRelationshipKind
    label: str
    summary: str
    member_ids: tuple[str, ...] = ()
    shared_terms: tuple[str, ...] = ()

    @field_validator("cluster_id")
    @classmethod
    def validate_cluster_id(cls, value: str) -> str:
        return _normalize_identifier(value, field_label="cluster_id") or ""

    @field_validator("label", "summary")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("member_ids", mode="before")
    @classmethod
    def normalize_member_ids(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            identifier = _normalize_identifier(str(item), field_label="member_ids value")
            if identifier is None or identifier in seen:
                continue
            seen.add(identifier)
            normalized.append(identifier)
        return tuple(normalized)

    @field_validator("shared_terms", mode="before")
    @classmethod
    def normalize_shared_terms(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class CompoundingRelationshipSummaryArtifact(ContractModel):
    """Derived relationship summaries over the governed compounding index."""

    schema_version: Literal["1.0"] = COMPOUNDING_SCHEMA_VERSION
    generated_at: datetime
    secondary_surface_note: str
    index_artifact_ref: str
    cluster_counts: dict[str, int] = Field(default_factory=dict)
    clusters: tuple[CompoundingRelationshipCluster, ...] = ()

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("secondary_surface_note", "index_artifact_ref")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("cluster_counts", mode="before")
    @classmethod
    def normalize_cluster_counts(cls, value: dict[str, int] | None) -> dict[str, int]:
        if not value:
            return {}
        normalized: dict[str, int] = {}
        for key in sorted(value):
            count = int(value[key])
            if count < 0:
                raise ValueError("cluster_counts values must be non-negative")
            normalized[str(key)] = count
        return normalized

    @field_validator("clusters", mode="before")
    @classmethod
    def normalize_clusters(
        cls,
        value: tuple[CompoundingRelationshipCluster, ...]
        | list[CompoundingRelationshipCluster]
        | tuple[dict[str, Any], ...]
        | list[dict[str, Any]]
        | None,
    ) -> tuple[CompoundingRelationshipCluster, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, CompoundingRelationshipCluster) else CompoundingRelationshipCluster.model_validate(item)
            for item in value
        )
