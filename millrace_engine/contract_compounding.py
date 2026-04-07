"""Typed contracts for runtime-owned compounding artifacts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
import re

from pydantic import field_validator

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
