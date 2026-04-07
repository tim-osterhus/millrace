"""Typed contracts for durable context-fact artifacts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
import re

from pydantic import field_validator, model_validator

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
