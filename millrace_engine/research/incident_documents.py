"""Incident document contracts and markdown parsing helpers."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..contracts import ContractModel, _normalize_datetime, _normalize_path
from .normalization_helpers import _normalize_optional_text_or_none, _normalize_required_text
from .parser_helpers import (
    _extract_heading_title as _shared_extract_heading_title,
)
from .parser_helpers import (
    _markdown_section as _shared_markdown_section,
)
from .parser_helpers import (
    _parse_frontmatter_block as _shared_parse_frontmatter_block,
)

_HEADING_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", flags=re.MULTILINE)
_FIELD_RE = re.compile(r"^\s*-\s*(?P<name>[^:]+):\s*(?P<value>.*)$")
_INCIDENT_ARTIFACT_SCHEMA_VERSION = "1.0"


def _strip_ticks(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("`") and normalized.endswith("`") and len(normalized) >= 2:
        return normalized[1:-1].strip()
    return normalized


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    return _shared_parse_frontmatter_block(text)


def _markdown_section(text: str, heading: str) -> str:
    return _shared_markdown_section(text, heading)


def _extract_heading_title(text: str) -> str | None:
    return _shared_extract_heading_title(
        text,
        normalize=lambda value: _normalize_required_text(value, field_name="title"),
    )


def _extract_markdown_field(text: str, field_name: str) -> str | None:
    target = field_name.casefold()
    for line in text.splitlines():
        match = _FIELD_RE.match(line.strip())
        if match is None:
            continue
        if match.group("name").strip().casefold() != target:
            continue
        value = match.group("value").strip()
        return value or None
    return None


def _extract_summary(text: str) -> str | None:
    match = re.search(
        r"(?ms)^##\s+Summary\s*$\n(?P<body>.*?)(?:\n##\s+|\Z)",
        text,
    )
    if match is not None:
        lines = [
            _normalize_optional_text_or_none(line.strip().lstrip("-").strip())
            for line in match.group("body").splitlines()
            if line.strip()
        ]
        collapsed = " ".join(line for line in lines if line)
        return collapsed or None

    heading = _HEADING_RE.search(text)
    if heading is None:
        return None
    trailing = text[heading.end() :].strip()
    if not trailing:
        return None
    paragraph = trailing.split("\n\n", 1)[0].strip()
    if paragraph.startswith("## "):
        return None
    return _normalize_optional_text_or_none(paragraph.replace("\n", " "))


class IncidentLifecycleStatus(str, Enum):
    """Supported incident queue lifecycle locations."""

    INCOMING = "incoming"
    WORKING = "working"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class IncidentSeverity(str, Enum):
    """Governance severity class carried by incident documents."""

    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"


class IncidentDocument(ContractModel):
    """Validated incident queue document loaded from one markdown file."""

    source_path: Path
    incident_id: str | None = None
    title: str
    lifecycle_status: IncidentLifecycleStatus | None = None
    severity: IncidentSeverity | None = None
    fingerprint: str | None = None
    failure_signature: str | None = None
    source_task: str | None = None
    opened_at: datetime | None = None
    updated_at: datetime | None = None
    summary: str | None = None

    @field_validator("source_path", mode="before")
    @classmethod
    def normalize_source_path(cls, value: str | Path) -> Path:
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError("source_path may not be empty")
        return normalized

    @field_validator(
        "incident_id",
        "title",
        "fingerprint",
        "failure_signature",
        "source_task",
        "summary",
    )
    @classmethod
    def normalize_text(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        if field_name == "title":
            if value is None:
                raise ValueError("title may not be empty")
            return _normalize_required_text(value, field_name="title")
        return _normalize_optional_text_or_none(value)

    @field_validator("opened_at", "updated_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value: IncidentSeverity | str | None) -> IncidentSeverity | None:
        if value is None:
            return None
        return IncidentSeverity(str(value).strip().upper())

    @field_validator("lifecycle_status", mode="before")
    @classmethod
    def normalize_lifecycle_status(
        cls,
        value: IncidentLifecycleStatus | str | None,
    ) -> IncidentLifecycleStatus | None:
        if value is None:
            return None
        return IncidentLifecycleStatus(str(value).strip().lower())

    @model_validator(mode="after")
    def validate_timestamps(self) -> "IncidentDocument":
        if self.opened_at is not None and self.updated_at is not None and self.updated_at < self.opened_at:
            raise ValueError("updated_at may not be earlier than opened_at")
        return self


class IncidentLineageRecord(ContractModel):
    """Durable lineage snapshot for one incident across queue movement."""

    schema_version: Literal["1.0"] = _INCIDENT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["incident_lineage"] = "incident_lineage"
    incident_id: str
    title: str
    source_path: str
    current_path: str
    working_path: str | None = None
    resolved_path: str | None = None
    archived_path: str | None = None
    source_task: str | None = None
    blocker_ledger_path: str | None = None
    blocker_item_key: str | None = None
    parent_handoff_id: str | None = None
    parent_run_id: str | None = None
    remediation_spec_id: str | None = None
    remediation_record_path: str | None = None
    last_stage: Literal["incident_intake", "incident_resolve", "incident_archive"]
    updated_at: datetime

    @field_validator(
        "incident_id",
        "title",
        "source_path",
        "current_path",
        "working_path",
        "resolved_path",
        "archived_path",
        "source_task",
        "blocker_ledger_path",
        "blocker_item_key",
        "parent_handoff_id",
        "parent_run_id",
        "remediation_spec_id",
        "remediation_record_path",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        if field_name in {"incident_id", "title", "source_path", "current_path"}:
            if value is None:
                raise ValueError(f"{field_name} may not be empty")
            return _normalize_required_text(value, field_name=field_name)
        return _normalize_optional_text_or_none(value)

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class IncidentIntakeRecord(ContractModel):
    """Durable runtime record for one incident intake execution."""

    schema_version: Literal["1.0"] = _INCIDENT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["incident_intake"] = "incident_intake"
    run_id: str
    emitted_at: datetime
    incident_id: str
    title: str
    source_path: str
    working_path: str
    lineage_path: str
    source_task: str | None = None
    blocker_ledger_path: str | None = None
    blocker_item_key: str | None = None
    parent_handoff_id: str | None = None
    parent_run_id: str | None = None
    remediation_record_path: str | None = None
    remediation_spec_id: str | None = None

    @field_validator(
        "run_id",
        "incident_id",
        "title",
        "source_path",
        "working_path",
        "lineage_path",
        "source_task",
        "blocker_ledger_path",
        "blocker_item_key",
        "parent_handoff_id",
        "parent_run_id",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        if field_name in {"run_id", "incident_id", "title", "source_path", "working_path", "lineage_path"}:
            if value is None:
                raise ValueError(f"{field_name} may not be empty")
            return _normalize_required_text(value, field_name=field_name)
        return _normalize_optional_text_or_none(value)

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class IncidentResolveRecord(ContractModel):
    """Durable runtime record for one incident resolve execution."""

    schema_version: Literal["1.0"] = _INCIDENT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["incident_resolve"] = "incident_resolve"
    run_id: str
    emitted_at: datetime
    incident_id: str
    title: str
    source_path: str
    resolved_path: str
    lineage_path: str
    resolution_summary: str
    source_task: str | None = None
    blocker_ledger_path: str | None = None
    blocker_item_key: str | None = None
    parent_handoff_id: str | None = None
    parent_run_id: str | None = None
    remediation_record_path: str | None = None
    remediation_spec_id: str | None = None

    @field_validator(
        "run_id",
        "incident_id",
        "title",
        "source_path",
        "resolved_path",
        "lineage_path",
        "resolution_summary",
        "source_task",
        "blocker_ledger_path",
        "blocker_item_key",
        "parent_handoff_id",
        "parent_run_id",
        "remediation_record_path",
        "remediation_spec_id",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        if field_name in {
            "run_id",
            "incident_id",
            "title",
            "source_path",
            "resolved_path",
            "lineage_path",
            "resolution_summary",
        }:
            if value is None:
                raise ValueError(f"{field_name} may not be empty")
            return _normalize_required_text(value, field_name=field_name)
        return _normalize_optional_text_or_none(value)

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class IncidentArchiveRecord(ContractModel):
    """Durable runtime record for one incident archive execution."""

    schema_version: Literal["1.0"] = _INCIDENT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["incident_archive"] = "incident_archive"
    run_id: str
    emitted_at: datetime
    incident_id: str
    title: str
    source_path: str
    archived_path: str
    lineage_path: str
    evidence_paths: tuple[str, ...] = ()
    source_task: str | None = None
    blocker_ledger_path: str | None = None
    blocker_item_key: str | None = None
    parent_handoff_id: str | None = None
    parent_run_id: str | None = None

    @field_validator(
        "run_id",
        "incident_id",
        "title",
        "source_path",
        "archived_path",
        "lineage_path",
        "source_task",
        "blocker_ledger_path",
        "blocker_item_key",
        "parent_handoff_id",
        "parent_run_id",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        if field_name in {"run_id", "incident_id", "title", "source_path", "archived_path", "lineage_path"}:
            if value is None:
                raise ValueError(f"{field_name} may not be empty")
            return _normalize_required_text(value, field_name=field_name)
        return _normalize_optional_text_or_none(value)

    @field_validator("evidence_paths", mode="before")
    @classmethod
    def normalize_evidence_paths(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if value is None:
            return ()
        return tuple(_normalize_required_text(item, field_name="evidence_paths") for item in value)

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class IncidentRecurrenceObservation(ContractModel):
    """One durable observation of an equivalent incident recurring."""

    observed_at: datetime
    source: Literal["execution_quarantine", "incident_intake", "incident_resolve", "incident_archive"]
    incident_id: str | None = None
    incident_path: str | None = None
    lifecycle_status: str | None = None
    source_task: str | None = None

    @field_validator("observed_at", mode="before")
    @classmethod
    def normalize_observed_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("incident_id", "incident_path", "lifecycle_status", "source_task", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | None, info: object) -> str | None:
        return _normalize_optional_text_or_none(value)


class IncidentRecurrenceRecord(ContractModel):
    """Ledger entry for one deduplicated incident signature."""

    dedup_signature: str
    fingerprint: str
    failure_signature: str
    first_seen_at: datetime
    last_seen_at: datetime
    occurrence_count: int = Field(default=1, ge=1)
    incident_id: str | None = None
    active_incident_path: str | None = None
    source_task: str | None = None
    observations: tuple[IncidentRecurrenceObservation, ...] = ()

    @field_validator(
        "dedup_signature",
        "fingerprint",
        "failure_signature",
        "incident_id",
        "active_incident_path",
        "source_task",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        if field_name in {"dedup_signature", "fingerprint", "failure_signature"}:
            if value is None:
                raise ValueError(f"{field_name} may not be empty")
            return _normalize_required_text(value, field_name=field_name)
        return _normalize_optional_text_or_none(value)

    @field_validator("first_seen_at", "last_seen_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @model_validator(mode="after")
    def validate_observation_window(self) -> "IncidentRecurrenceRecord":
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at may not be earlier than first_seen_at")
        if self.observations and len(self.observations) != self.occurrence_count:
            raise ValueError("occurrence_count must match the number of observations")
        return self


class IncidentRecurrenceLedger(ContractModel):
    """Durable ledger of equivalent incident recurrences."""

    schema_version: Literal["1.0"] = _INCIDENT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["incident_recurrence_ledger"] = "incident_recurrence_ledger"
    updated_at: datetime
    records: tuple[IncidentRecurrenceRecord, ...] = ()

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class IncidentFixSpecRecord(ContractModel):
    """Fix-spec package derived from one resolved incident."""

    spec_id: str
    title: str
    scope_summary: str
    queue_spec_path: str
    reviewed_path: str
    golden_spec_path: str
    phase_spec_path: str
    review_questions_path: str
    review_decision_path: str
    stable_registry_path: str

    @field_validator(
        "spec_id",
        "title",
        "scope_summary",
        "queue_spec_path",
        "reviewed_path",
        "golden_spec_path",
        "phase_spec_path",
        "review_questions_path",
        "review_decision_path",
        "stable_registry_path",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        if value is None:
            raise ValueError(f"{field_name} may not be empty")
        return _normalize_required_text(value, field_name=field_name)


class IncidentRemediationRecord(ContractModel):
    """Durable incident-to-remediation handoff record."""

    schema_version: Literal["1.0"] = _INCIDENT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["incident_remediation"] = "incident_remediation"
    run_id: str
    emitted_at: datetime
    incident_id: str
    incident_title: str
    resolved_path: str
    lineage_path: str
    family_state_path: str
    goalspec_lineage_path: str
    fix_spec: IncidentFixSpecRecord
    taskmaster_record_path: str | None = None
    taskaudit_record_path: str | None = None
    task_provenance_path: str | None = None

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "incident_id",
        "incident_title",
        "resolved_path",
        "lineage_path",
        "family_state_path",
        "goalspec_lineage_path",
        "taskmaster_record_path",
        "taskaudit_record_path",
        "task_provenance_path",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        if field_name in {
            "run_id",
            "incident_id",
            "incident_title",
            "resolved_path",
            "lineage_path",
            "family_state_path",
            "goalspec_lineage_path",
        }:
            if value is None:
                raise ValueError(f"{field_name} may not be empty")
            return _normalize_required_text(value, field_name=field_name)
        return _normalize_optional_text_or_none(value)


def parse_incident_document(text: str, *, source_path: Path) -> IncidentDocument:
    """Validate one incident markdown document."""

    frontmatter, remainder = _parse_frontmatter(text)
    heading_title = _extract_heading_title(remainder)
    incident_id = (
        frontmatter.get("incident_id")
        or _strip_ticks(_extract_markdown_field(remainder, "Incident-ID"))
        or None
    )
    lifecycle_status = frontmatter.get("status")
    if lifecycle_status is None:
        parent_name = source_path.parent.name.strip().lower()
        if parent_name in {status.value for status in IncidentLifecycleStatus}:
            lifecycle_status = parent_name

    title = heading_title or incident_id or source_path.stem

    return IncidentDocument.model_validate(
        {
            "source_path": source_path,
            "incident_id": incident_id,
            "title": title,
            "lifecycle_status": lifecycle_status,
            "severity": frontmatter.get("severity") or _strip_ticks(_extract_markdown_field(remainder, "Severity Class")),
            "fingerprint": frontmatter.get("fingerprint") or _strip_ticks(_extract_markdown_field(remainder, "Fingerprint")),
            "failure_signature": (
                frontmatter.get("failure_signature")
                or _strip_ticks(_extract_markdown_field(remainder, "Failure signature"))
                or _strip_ticks(_extract_markdown_field(remainder, "Primary signature"))
            ),
            "source_task": frontmatter.get("source_task") or _strip_ticks(_extract_markdown_field(remainder, "Source task")),
            "opened_at": frontmatter.get("opened_at"),
            "updated_at": frontmatter.get("updated_at"),
            "summary": _extract_summary(remainder),
        }
    )


def load_incident_document(path: Path) -> IncidentDocument:
    """Read and validate one incident queue file."""

    return parse_incident_document(path.read_text(encoding="utf-8"), source_path=path)

