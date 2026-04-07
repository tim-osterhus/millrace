"""Typed contracts for durable context-fact artifacts and injections."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
import re

from pydantic import Field, field_validator, model_validator

from .contract_core import ContractModel, StageType, _normalize_datetime, _normalize_sequence


CONTEXT_FACT_SCHEMA_VERSION = "1.0"
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


class ContextFactScope(str, Enum):
    """Governed scope for one durable context fact."""

    RUN = "run"
    WORKSPACE = "workspace"


class ContextFactLifecycleState(str, Enum):
    """Lifecycle state for one durable context fact."""

    CANDIDATE = "candidate"
    PROMOTED = "promoted"
    STALE = "stale"
    DEPRECATED = "deprecated"


class ContextFactSelectionReason(str, Enum):
    """Why one durable context fact was selected for injection."""

    RUN_SCOPE = "run_scope"
    PATTERN_MATCH = "pattern_match"
    BROADER_SCOPE = "broader_scope"


class ContextFactArtifact(ContractModel):
    """Persisted durable fact kept separate from reusable procedures."""

    schema_version: Literal["1.0"] = CONTEXT_FACT_SCHEMA_VERSION
    fact_id: str
    scope: ContextFactScope = ContextFactScope.RUN
    lifecycle_state: ContextFactLifecycleState = ContextFactLifecycleState.CANDIDATE
    source_run_id: str
    source_stage: StageType
    title: str
    statement: str
    summary: str
    tags: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    created_at: datetime
    observed_at: datetime | None = None
    stale_reason: str | None = None
    supersedes_fact_id: str | None = None

    @field_validator("fact_id", "source_run_id", "supersedes_fact_id")
    @classmethod
    def validate_identifiers(cls, value: str | None, info: Any) -> str | None:
        return _normalize_identifier(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("title", "statement", "summary", "stale_reason")
    @classmethod
    def validate_text_fields(cls, value: str | None, info: Any) -> str | None:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("created_at", "observed_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @field_validator("tags", "evidence_refs", mode="before")
    @classmethod
    def normalize_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])

    @model_validator(mode="after")
    def validate_state_specific_fields(self) -> "ContextFactArtifact":
        if self.lifecycle_state is ContextFactLifecycleState.STALE and self.stale_reason is None:
            raise ValueError("stale_reason is required when lifecycle_state is stale")
        if self.lifecycle_state is not ContextFactLifecycleState.STALE and self.stale_reason is not None:
            raise ValueError("stale_reason is only allowed when lifecycle_state is stale")
        return self


class ContextFactRetrievalRule(ContractModel):
    """Stage-aware retrieval constraints for durable context facts."""

    stage: StageType
    allowed_scopes: tuple[ContextFactScope, ...]
    allowed_source_stages: tuple[StageType, ...]
    max_facts: int = Field(default=2, ge=1)
    max_prompt_characters: int = Field(default=1200, ge=1)

    @field_validator("allowed_scopes", mode="before")
    @classmethod
    def normalize_allowed_scopes(
        cls,
        value: tuple[ContextFactScope, ...] | tuple[str, ...] | list[str] | list[ContextFactScope],
    ) -> tuple[ContextFactScope, ...]:
        if not value:
            raise ValueError("allowed_scopes may not be empty")
        normalized: list[ContextFactScope] = []
        seen: set[ContextFactScope] = set()
        for item in value:
            scope = item if isinstance(item, ContextFactScope) else ContextFactScope(str(item).strip().lower())
            if scope in seen:
                continue
            seen.add(scope)
            normalized.append(scope)
        return tuple(normalized)

    @field_validator("allowed_source_stages", mode="before")
    @classmethod
    def normalize_allowed_source_stages(
        cls,
        value: tuple[StageType, ...] | tuple[str, ...] | list[str] | list[StageType],
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


class ConsideredContextFact(ContractModel):
    """One durable context fact considered during stage-aware retrieval."""

    fact_id: str
    scope: ContextFactScope
    source_stage: StageType
    title: str
    summary: str
    tags: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    selection_reason: ContextFactSelectionReason

    @field_validator("fact_id")
    @classmethod
    def validate_fact_id(cls, value: str) -> str:
        return _normalize_identifier(value, field_label="fact_id") or ""

    @field_validator("title", "summary")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("tags", "evidence_refs", mode="before")
    @classmethod
    def normalize_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class InjectedContextFact(ContractModel):
    """One durable context fact selected for stage-context injection."""

    fact_id: str
    scope: ContextFactScope
    source_stage: StageType
    title: str
    summary: str
    statement_excerpt: str
    tags: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    selection_reason: ContextFactSelectionReason
    original_characters: int = Field(default=0, ge=0)
    injected_characters: int = Field(default=0, ge=0)
    truncated: bool = False

    @field_validator("fact_id")
    @classmethod
    def validate_fact_id(cls, value: str) -> str:
        return _normalize_identifier(value, field_label="fact_id") or ""

    @field_validator("title", "summary", "statement_excerpt")
    @classmethod
    def validate_text_fields(cls, value: str, info: Any) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("tags", "evidence_refs", mode="before")
    @classmethod
    def normalize_sequences(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class ContextFactInjectionBundle(ContractModel):
    """Deterministic selection record for context facts injected into one stage context."""

    stage: StageType
    rule: ContextFactRetrievalRule
    considered_facts: tuple[ConsideredContextFact, ...] = ()
    facts: tuple[InjectedContextFact, ...] = ()
    candidate_count: int = Field(default=0, ge=0)
    selected_count: int = Field(default=0, ge=0)
    budget_characters: int = Field(default=0, ge=0)
    used_characters: int = Field(default=0, ge=0)
    truncated_count: int = Field(default=0, ge=0)

    @field_validator("considered_facts", mode="before")
    @classmethod
    def normalize_considered_facts(
        cls,
        value: tuple[ConsideredContextFact, ...]
        | list[ConsideredContextFact]
        | tuple[dict[str, Any], ...]
        | list[dict[str, Any]]
        | None,
    ) -> tuple[ConsideredContextFact, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, ConsideredContextFact) else ConsideredContextFact.model_validate(item)
            for item in value
        )

    @field_validator("facts", mode="before")
    @classmethod
    def normalize_facts(
        cls,
        value: tuple[InjectedContextFact, ...]
        | list[InjectedContextFact]
        | tuple[dict[str, Any], ...]
        | list[dict[str, Any]]
        | None,
    ) -> tuple[InjectedContextFact, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, InjectedContextFact) else InjectedContextFact.model_validate(item)
            for item in value
        )
