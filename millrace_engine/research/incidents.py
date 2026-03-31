"""Typed incident queue contracts plus executable incident stage helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal
import json
import re

from pydantic import Field, field_validator, model_validator

from ..contracts import ContractModel, ResearchRecoveryDecision, _normalize_datetime, _normalize_path
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from ..queue import load_research_recovery_latch, write_research_recovery_latch
from .specs import (
    GoalSpecFamilyGovernorState,
    GoalSpecFamilySpecState,
    GoalSpecFamilyState,
    GoalSpecLineageRecord,
    build_initial_family_plan_snapshot,
    refresh_stable_spec_registry,
    write_goal_spec_family_state,
)

if TYPE_CHECKING:
    from .dispatcher import CompiledResearchDispatch
    from .state import ResearchCheckpoint, ResearchQueueOwnership


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", flags=re.DOTALL)
_HEADING_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", flags=re.MULTILINE)
_FIELD_RE = re.compile(r"^\s*-\s*(?P<name>[^:]+):\s*(?P<value>.*)$")
_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_INCIDENT_ARTIFACT_SCHEMA_VERSION = "1.0"


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", value.strip())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = _WHITESPACE_RE.sub(" ", value.strip())
    if not normalized:
        return None
    return normalized


def _strip_ticks(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("`") and normalized.endswith("`") and len(normalized) >= 2:
        return normalized[1:-1].strip()
    return normalized


def _relative_path(path: Path, *, relative_to: Path) -> str:
    try:
        return path.relative_to(relative_to).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_path_token(path_token: str | Path, *, relative_to: Path) -> Path:
    candidate = Path(path_token)
    if candidate.is_absolute():
        return candidate
    return relative_to / candidate


def _write_json_model(path: Path, model: ContractModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(model.model_dump_json(exclude_none=False))
    write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _slugify(value: str) -> str:
    slug = _TOKEN_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "incident"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text

    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        fields[key.strip()] = raw_value.strip()
    return fields, text[match.end() :]


def _markdown_section(text: str, heading: str) -> str:
    target = heading.strip().casefold()
    current: list[str] = []
    capture = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if capture:
                break
            capture = stripped[3:].strip().casefold() == target
            continue
        if capture:
            current.append(line.rstrip())
    return "\n".join(current).strip()


def _extract_heading_title(text: str) -> str | None:
    match = _HEADING_RE.search(text)
    if match is None:
        return None
    return _normalize_required_text(match.group("title"), field_name="title")


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
            _normalize_optional_text(line.strip().lstrip("-").strip(), field_name="summary")
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
    return _normalize_optional_text(paragraph.replace("\n", " "), field_name="summary")


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


class IncidentExecutionError(RuntimeError):
    """Raised when one incident stage cannot execute safely."""


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
        return _normalize_optional_text(value, field_name=field_name)

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
        return _normalize_optional_text(value, field_name=field_name)

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
        return _normalize_optional_text(value, field_name=field_name)

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
        return _normalize_optional_text(value, field_name=field_name)

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
        return _normalize_optional_text(value, field_name=field_name)

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
        field_name = getattr(info, "field_name", "text")
        return _normalize_optional_text(value, field_name=field_name)


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
        return _normalize_optional_text(value, field_name=field_name)

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
        return _normalize_optional_text(value, field_name=field_name)


@dataclass(frozen=True, slots=True)
class IncidentIntakeExecutionResult:
    """Resolved outputs from one incident intake execution."""

    record_path: str
    lineage_path: str
    working_path: str
    queue_ownership: ResearchQueueOwnership


@dataclass(frozen=True, slots=True)
class IncidentResolveExecutionResult:
    """Resolved outputs from one incident resolve execution."""

    record_path: str
    lineage_path: str
    resolved_path: str
    remediation_record_path: str
    reviewed_spec_path: str
    queue_ownership: ResearchQueueOwnership


@dataclass(frozen=True, slots=True)
class IncidentArchiveExecutionResult:
    """Resolved outputs from one incident archive execution."""

    record_path: str
    lineage_path: str
    archived_path: str
    queue_ownership: ResearchQueueOwnership


@dataclass(frozen=True, slots=True)
class IncidentTaskGenerationExecutionResult:
    """Resolved outputs from one incident remediation task-generation handoff."""

    remediation_record_path: str
    taskmaster_record_path: str
    taskaudit_record_path: str


def _incident_runtime_dir(paths: RuntimePaths) -> Path:
    return paths.research_runtime_dir / "incidents"


def _incident_lineage_path(paths: RuntimePaths, *, incident_key: str) -> Path:
    return _incident_runtime_dir(paths) / "lineage" / f"{incident_key}.json"


def _incident_record_path(paths: RuntimePaths, *, stage: str, run_id: str) -> Path:
    return _incident_runtime_dir(paths) / stage / f"{run_id}.json"


def _incident_remediation_record_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return _incident_runtime_dir(paths) / "remediation" / f"{run_id}.json"


def default_incident_recurrence_ledger(*, observed_at: datetime | None = None) -> IncidentRecurrenceLedger:
    """Return an empty recurrence ledger snapshot."""

    return IncidentRecurrenceLedger(updated_at=observed_at or datetime.now(timezone.utc))


def incident_dedup_signature(
    fingerprint: str | None,
    failure_signature: str | None,
) -> str | None:
    """Return the canonical dedup signature for one incident recurrence pair."""

    normalized_fingerprint = _normalize_optional_text(fingerprint, field_name="fingerprint")
    normalized_failure_signature = _normalize_optional_text(
        failure_signature,
        field_name="failure_signature",
    )
    if normalized_fingerprint is None or normalized_failure_signature is None:
        return None
    return sha256(f"{normalized_fingerprint}|{normalized_failure_signature}".encode("utf-8")).hexdigest()


def load_incident_recurrence_ledger(path: Path) -> IncidentRecurrenceLedger:
    """Load the incident recurrence ledger if present, else return the empty default."""

    if not path.exists():
        return default_incident_recurrence_ledger()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise IncidentExecutionError(f"{path.as_posix()} must contain a JSON object")
    return IncidentRecurrenceLedger.model_validate(payload)


def write_incident_recurrence_ledger(path: Path, ledger: IncidentRecurrenceLedger) -> None:
    """Persist the incident recurrence ledger."""

    _write_json_model(path, ledger)


def find_equivalent_incident(
    paths: RuntimePaths,
    *,
    fingerprint: str | None,
    failure_signature: str | None,
) -> Path | None:
    """Return the active incident path for one deduplicated fingerprint/signature pair."""

    dedup_signature = incident_dedup_signature(fingerprint, failure_signature)
    if dedup_signature is None:
        return None
    for queue_path in (
        paths.agents_dir / "ideas" / "incidents" / "incoming",
        paths.agents_dir / "ideas" / "incidents" / "working",
        paths.agents_dir / "ideas" / "incidents" / "resolved",
    ):
        if not queue_path.is_dir():
            continue
        for incident_path in sorted(path for path in queue_path.glob("*.md") if path.is_file()):
            document = load_incident_document(incident_path)
            if incident_dedup_signature(document.fingerprint, document.failure_signature) == dedup_signature:
                return incident_path
    return None


def resolve_deduplicated_incident_path(
    paths: RuntimePaths,
    *,
    fingerprint: str | None,
    failure_signature: str | None,
    preferred_path: Path | str | None = None,
) -> Path | None:
    """Return the canonical relative incident path for one recurring equivalent incident."""

    existing_path = find_equivalent_incident(
        paths,
        fingerprint=fingerprint,
        failure_signature=failure_signature,
    )
    if existing_path is not None:
        return Path(_relative_path(existing_path, relative_to=paths.root))

    if preferred_path is not None:
        resolved = _resolve_path_token(preferred_path, relative_to=paths.root)
        return Path(_relative_path(resolved, relative_to=paths.root))

    dedup_signature = incident_dedup_signature(fingerprint, failure_signature)
    if dedup_signature is not None:
        return Path("agents/ideas/incidents/incoming") / f"INC-{dedup_signature[:12].upper()}.md"

    return None


def record_incident_recurrence(
    paths: RuntimePaths,
    *,
    fingerprint: str | None,
    failure_signature: str | None,
    observed_at: datetime | None = None,
    source: Literal["execution_quarantine", "incident_intake", "incident_resolve", "incident_archive"],
    incident_id: str | None = None,
    incident_path: Path | str | None = None,
    lifecycle_status: str | None = None,
    source_task: str | None = None,
) -> IncidentRecurrenceRecord | None:
    """Append one recurrence observation for an equivalent incident pair."""

    dedup_signature = incident_dedup_signature(fingerprint, failure_signature)
    if dedup_signature is None:
        return None

    observed_at = observed_at or datetime.now(timezone.utc)
    normalized_fingerprint = _normalize_required_text(fingerprint or "", field_name="fingerprint")
    normalized_failure_signature = _normalize_required_text(
        failure_signature or "",
        field_name="failure_signature",
    )
    relative_incident_path = None
    if incident_path is not None:
        relative_incident_path = _relative_path(
            _resolve_path_token(incident_path, relative_to=paths.root),
            relative_to=paths.root,
        )

    ledger = load_incident_recurrence_ledger(paths.incident_recurrence_ledger_file)
    observation = IncidentRecurrenceObservation(
        observed_at=observed_at,
        source=source,
        incident_id=incident_id,
        incident_path=relative_incident_path,
        lifecycle_status=lifecycle_status,
        source_task=source_task,
    )

    updated_record: IncidentRecurrenceRecord | None = None
    records: list[IncidentRecurrenceRecord] = []
    for record in ledger.records:
        if record.dedup_signature != dedup_signature:
            records.append(record)
            continue
        updated_record = record.model_copy(
            update={
                "last_seen_at": observed_at,
                "occurrence_count": record.occurrence_count + 1,
                "incident_id": incident_id or record.incident_id,
                "active_incident_path": relative_incident_path or record.active_incident_path,
                "source_task": source_task or record.source_task,
                "observations": record.observations + (observation,),
            }
        )
        records.append(updated_record)

    if updated_record is None:
        updated_record = IncidentRecurrenceRecord(
            dedup_signature=dedup_signature,
            fingerprint=normalized_fingerprint,
            failure_signature=normalized_failure_signature,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            occurrence_count=1,
            incident_id=incident_id,
            active_incident_path=relative_incident_path,
            source_task=source_task,
            observations=(observation,),
        )
        records.append(updated_record)

    write_incident_recurrence_ledger(
        paths.incident_recurrence_ledger_file,
        IncidentRecurrenceLedger(
            updated_at=observed_at,
            records=tuple(sorted(records, key=lambda item: item.dedup_signature)),
        ),
    )
    return updated_record


def _incident_key(document: IncidentDocument, source_path: Path) -> str:
    token = document.incident_id or source_path.stem
    return _slugify(token)


def _spec_id_for_incident(document: IncidentDocument, section_text: str) -> str:
    declared = _strip_ticks(_extract_markdown_field(section_text, "Fix Spec ID"))
    if declared:
        normalized = _normalize_required_text(declared, field_name="fix_spec_id")
        return normalized if normalized.upper().startswith("SPEC-") else f"SPEC-{normalized.upper()}"
    return f"SPEC-{_slugify(document.incident_id or document.title).upper()}"


def _scope_summary_for_incident(document: IncidentDocument, section_text: str) -> str:
    declared = _extract_markdown_field(section_text, "Scope summary")
    normalized = _normalize_optional_text(declared, field_name="scope_summary")
    if normalized is not None:
        return normalized
    if document.summary is not None:
        return document.summary
    return f"Remediate incident {document.incident_id or document.title} with a governed fix-spec package."


def _task_step_lines(document: IncidentDocument, scope_summary: str) -> tuple[str, str, str]:
    incident_token = document.incident_id or _slugify(document.title).upper()
    return (
        f"Stabilize the failure path for `{incident_token}` by implementing the minimal unblock-first change set.",
        f"Add regression coverage and validation for `{incident_token}` so the incident cannot recur silently.",
        f"Refresh the affected runtime and task-generation surfaces described by this fix scope: {scope_summary}",
    )


def _render_incident_fix_spec(
    *,
    emitted_at: datetime,
    document: IncidentDocument,
    resolved_path: str,
    lineage_path: str,
    spec_id: str,
    scope_summary: str,
) -> str:
    timestamp = emitted_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    incident_id = document.incident_id or document.source_path.stem
    title = f"{document.title} remediation"
    return "\n".join(
        [
            "---",
            f"spec_id: {spec_id}",
            f"idea_id: {incident_id}",
            f"title: {title}",
            "status: proposed",
            "golden_version: 1",
            f"base_goal_sha256: {sha256(f'{incident_id}|{scope_summary}'.encode('utf-8')).hexdigest()}",
            "effort: 2",
            "decomposition_profile: simple",
            "depends_on_specs: []",
            "owner: research",
            f"created_at: {timestamp}",
            f"updated_at: {timestamp}",
            "---",
            "",
            "## Summary",
            scope_summary,
            "",
            "## Goals",
            f"- Remediate incident `{incident_id}` through the governed GoalSpec task-generation seam.",
            f"- Preserve traceability from `{resolved_path}` and `{lineage_path}` into the generated remediation work.",
            "- Keep the remediation slice reviewable and compatible with the existing Taskmaster/Taskaudit contract.",
            "",
            "## Non-Goals",
            "- Execution thaw in this run.",
            "- Replacing the existing incident archive and lineage flow.",
            "",
            "## Scope",
            "### In Scope",
            "- Emit a reviewed, stable, and decomposable fix-spec package for this incident.",
            "- Generate deterministic pending and backlog work through Taskmaster and Taskaudit.",
            "",
            "### Out of Scope",
            "- Backlog thaw or execution resume.",
            "- Broad refactors outside the bounded remediation seam.",
            "",
            "## Incident Context",
            f"- Incident path: `{resolved_path}`",
            f"- Lineage path: `{lineage_path}`",
            f"- Severity: `{document.severity.value if document.severity is not None else 'S2'}`",
            "",
            "## Implementation Plan",
            "1. Materialize a bounded fix-spec package from the resolved incident.",
            "2. Run Taskmaster to convert the stable phase plan into strict pending shards.",
            "3. Run Taskaudit to merge governed remediation work into backlog with refreshed provenance.",
            "",
            "## Requirements Traceability (Req-ID Matrix)",
            f"- `Req-ID: REQ-INC-001` | Preserve incident lineage and source-path continuity into remediation artifacts | `{lineage_path}`",
            f"- `Req-ID: REQ-INC-002` | Emit governed fix-spec work from a resolved incident artifact | `{resolved_path}`",
            "- `Req-ID: REQ-INC-003` | Keep remediation generation compatible with existing Taskmaster and Taskaudit behavior | `millrace/millrace_engine/research/taskmaster.py`",
            "",
            "## Assumptions Ledger",
            "- Incident `fix_spec` metadata may be incomplete; bounded defaults are derived when needed.",
            "- This remediation package stays single-spec and single-phase for the current incident slice.",
            "",
            "## Verification",
            "- `python3 -m py_compile millrace/millrace_engine/research/incidents.py millrace/millrace_engine/planes/research.py`",
            "- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q millrace/tests/test_research_dispatcher.py -k incident`",
            "",
            "## Dependencies",
            f"- Incident source: `{resolved_path}`",
            f"- Incident lineage: `{lineage_path}`",
            "- Task generation: `millrace/millrace_engine/research/taskmaster.py`",
            "- Provenance merge: `millrace/millrace_engine/research/taskaudit.py`",
            "",
            "## References",
            "- Research plane: `millrace/millrace_engine/planes/research.py`",
            "- Dispatcher coverage: `millrace/tests/test_research_dispatcher.py`",
            "",
        ]
    )


def _render_incident_phase_spec(
    *,
    emitted_at: datetime,
    document: IncidentDocument,
    spec_id: str,
    resolved_path: str,
    lineage_path: str,
    scope_summary: str,
) -> str:
    timestamp = emitted_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    step_1, step_2, step_3 = _task_step_lines(document, scope_summary)
    return "\n".join(
        [
            "---",
            f"phase_id: PHASE-{spec_id}-01",
            "phase_key: PHASE_01",
            "phase_priority: P1",
            f"parent_spec_id: {spec_id}",
            f"title: {document.title} remediation implementation foundation",
            "status: planned",
            "owner: research",
            f"created_at: {timestamp}",
            f"updated_at: {timestamp}",
            "---",
            "",
            "## Objective",
            f"- Convert incident remediation for `{document.incident_id or document.title}` into strict, decomposable work.",
            "",
            "## Entry Criteria",
            f"- Resolved incident exists at `{resolved_path}`.",
            f"- Incident lineage exists at `{lineage_path}`.",
            "",
            "## Scope",
            "### In Scope",
            "- Implement the bounded fix path described by the incident remediation package.",
            "- Preserve regression evidence and runtime traceability for the incident.",
            "",
            "### Out of Scope",
            "- Execution thaw and resume semantics.",
            "- Additional remediation families beyond this incident.",
            "",
            "## Work Plan",
            f"1. {step_1}",
            f"2. {step_2}",
            f"3. {step_3}",
            "",
            "## Verification",
            "- `python3 -m py_compile millrace/millrace_engine/research/incidents.py millrace/millrace_engine/planes/research.py`",
            "- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q millrace/tests/test_research_dispatcher.py -k incident`",
            "",
        ]
    )


def _render_incident_review_questions(
    *,
    emitted_at: datetime,
    run_id: str,
    incident_id: str,
    spec_id: str,
    title: str,
    queue_spec_path: str,
) -> str:
    return "\n".join(
        [
            "# Spec Review Questions",
            "",
            f"- **Run-ID:** {run_id}",
            f"- **Goal-ID:** {incident_id}",
            f"- **Spec-ID:** {spec_id}",
            f"- **Title:** {title}",
            f"- **Reviewed-At:** {emitted_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')}",
            f"- **Queue-Spec:** `{queue_spec_path}`",
            "",
            "## Critic Findings",
            "- No material delta was required before decomposing this incident remediation package.",
            "",
        ]
    )


def _render_incident_review_decision(
    *,
    emitted_at: datetime,
    run_id: str,
    incident_id: str,
    spec_id: str,
    title: str,
    reviewed_path: str,
    lineage_path: str,
    stable_registry_path: str,
) -> str:
    return "\n".join(
        [
            "# Spec Review Decision",
            "",
            f"- **Run-ID:** {run_id}",
            f"- **Goal-ID:** {incident_id}",
            f"- **Spec-ID:** {spec_id}",
            f"- **Title:** {title}",
            "- **Review-Status:** `no_material_delta`",
            f"- **Reviewed-At:** {emitted_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')}",
            f"- **Reviewed-Spec:** `{reviewed_path}`",
            f"- **Stable-Registry:** `{stable_registry_path}`",
            f"- **Lineage-Record:** `{lineage_path}`",
            "",
            "## Decision",
            "- Approved for downstream remediation task generation without additional edits in this run.",
            "",
        ]
    )


def _target_incident_path(paths: RuntimePaths, lifecycle_status: IncidentLifecycleStatus, source_path: Path) -> Path:
    if lifecycle_status is IncidentLifecycleStatus.INCOMING:
        return paths.agents_dir / "ideas" / "incidents" / "working" / source_path.name
    if lifecycle_status is IncidentLifecycleStatus.WORKING:
        return paths.agents_dir / "ideas" / "incidents" / "resolved" / source_path.name
    if lifecycle_status is IncidentLifecycleStatus.RESOLVED:
        return paths.agents_dir / "ideas" / "incidents" / "archived" / source_path.name
    return paths.agents_dir / "ideas" / "incidents" / "archived" / source_path.name


def _move_incident(path: Path, target_path: Path) -> Path:
    if path == target_path:
        return target_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        if path.exists() and target_path.read_text(encoding="utf-8") != path.read_text(encoding="utf-8"):
            raise IncidentExecutionError(
                f"incident target already exists with different contents: {target_path.as_posix()}"
            )
        if path.exists():
            path.unlink()
        return target_path
    path.rename(target_path)
    return target_path


def _load_existing_lineage(path: Path) -> IncidentLineageRecord | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise IncidentExecutionError(f"{path.as_posix()} must contain a JSON object")
    return IncidentLineageRecord.model_validate(payload)


def _incident_fix_spec_record(paths: RuntimePaths, *, document: IncidentDocument, incident_path: Path) -> IncidentFixSpecRecord:
    incident_text = incident_path.read_text(encoding="utf-8")
    _, remainder = _parse_frontmatter(incident_text)
    section_text = _markdown_section(remainder, "fix_spec")
    spec_id = _spec_id_for_incident(document, section_text)
    queue_field = _strip_ticks(_extract_markdown_field(section_text, "Fix Spec Path"))
    slug = _slugify(f"{document.title} remediation")
    queue_spec_path = (
        _resolve_path_token(queue_field, relative_to=paths.root)
        if queue_field
        else paths.ideas_specs_dir / f"{spec_id}__{slug}.md"
    )
    reviewed_path = paths.ideas_specs_reviewed_dir / queue_spec_path.name
    golden_spec_path = paths.specs_stable_golden_dir / queue_spec_path.name
    phase_spec_path = paths.specs_stable_phase_dir / f"{spec_id}__phase-01.md"
    review_questions_path = paths.specs_questions_dir / f"{queue_spec_path.stem}__spec-review.md"
    review_decision_path = paths.specs_decisions_dir / f"{queue_spec_path.stem}__spec-review.md"
    return IncidentFixSpecRecord(
        spec_id=spec_id,
        title=f"{document.title} remediation",
        scope_summary=_scope_summary_for_incident(document, section_text),
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
        golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
        phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
        review_questions_path=_relative_path(review_questions_path, relative_to=paths.root),
        review_decision_path=_relative_path(review_decision_path, relative_to=paths.root),
        stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
    )


def _write_incident_remediation_bundle(
    paths: RuntimePaths,
    *,
    document: IncidentDocument,
    incident_path: Path,
    lineage_path: Path,
    run_id: str,
    emitted_at: datetime,
) -> IncidentRemediationRecord:
    fix_spec = _incident_fix_spec_record(paths, document=document, incident_path=incident_path)
    queue_spec_path = _resolve_path_token(fix_spec.queue_spec_path, relative_to=paths.root)
    reviewed_path = _resolve_path_token(fix_spec.reviewed_path, relative_to=paths.root)
    golden_spec_path = _resolve_path_token(fix_spec.golden_spec_path, relative_to=paths.root)
    phase_spec_path = _resolve_path_token(fix_spec.phase_spec_path, relative_to=paths.root)
    review_questions_path = _resolve_path_token(fix_spec.review_questions_path, relative_to=paths.root)
    review_decision_path = _resolve_path_token(fix_spec.review_decision_path, relative_to=paths.root)
    remediation_record_path = _incident_remediation_record_path(paths, run_id=run_id)
    goalspec_lineage_path = paths.goalspec_lineage_dir / f"{fix_spec.spec_id}.json"
    resolved_relative_path = _relative_path(incident_path, relative_to=paths.root)
    lineage_relative_path = _relative_path(lineage_path, relative_to=paths.root)

    queue_spec_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed_path.parent.mkdir(parents=True, exist_ok=True)
    golden_spec_path.parent.mkdir(parents=True, exist_ok=True)
    phase_spec_path.parent.mkdir(parents=True, exist_ok=True)
    review_questions_path.parent.mkdir(parents=True, exist_ok=True)
    review_decision_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        queue_spec_path,
        _render_incident_fix_spec(
            emitted_at=emitted_at,
            document=document,
            resolved_path=resolved_relative_path,
            lineage_path=lineage_relative_path,
            spec_id=fix_spec.spec_id,
            scope_summary=fix_spec.scope_summary,
        ),
    )
    write_text_atomic(reviewed_path, queue_spec_path.read_text(encoding="utf-8"))
    write_text_atomic(golden_spec_path, queue_spec_path.read_text(encoding="utf-8"))
    write_text_atomic(
        phase_spec_path,
        _render_incident_phase_spec(
            emitted_at=emitted_at,
            document=document,
            spec_id=fix_spec.spec_id,
            resolved_path=resolved_relative_path,
            lineage_path=lineage_relative_path,
            scope_summary=fix_spec.scope_summary,
        ),
    )
    write_text_atomic(
        review_questions_path,
        _render_incident_review_questions(
            emitted_at=emitted_at,
            run_id=run_id,
            incident_id=document.incident_id or incident_path.stem,
            spec_id=fix_spec.spec_id,
            title=fix_spec.title,
            queue_spec_path=fix_spec.queue_spec_path,
        ),
    )
    write_text_atomic(
        review_decision_path,
        _render_incident_review_decision(
            emitted_at=emitted_at,
            run_id=run_id,
            incident_id=document.incident_id or incident_path.stem,
            spec_id=fix_spec.spec_id,
            title=fix_spec.title,
            reviewed_path=fix_spec.reviewed_path,
            lineage_path=_relative_path(goalspec_lineage_path, relative_to=paths.root),
            stable_registry_path=fix_spec.stable_registry_path,
        ),
    )

    provisional_state = GoalSpecFamilyState(
        goal_id=document.incident_id or incident_path.stem,
        source_idea_path="",
        family_phase="initial_family",
        family_complete=True,
        active_spec_id=fix_spec.spec_id,
        spec_order=(fix_spec.spec_id,),
        specs={
            fix_spec.spec_id: GoalSpecFamilySpecState(
                status="reviewed",
                review_status="no_material_delta",
                title=fix_spec.title,
                decomposition_profile="simple",
                queue_path=fix_spec.queue_spec_path,
                reviewed_path=fix_spec.reviewed_path,
                stable_spec_paths=(fix_spec.golden_spec_path, fix_spec.phase_spec_path),
                review_questions_path=fix_spec.review_questions_path,
                review_decision_path=fix_spec.review_decision_path,
            )
        },
        family_governor=GoalSpecFamilyGovernorState(
            initial_family_max_specs=1,
            applied_family_max_specs=1,
        ),
    )
    initial_plan = build_initial_family_plan_snapshot(
        provisional_state,
        repo_root=paths.root,
        trigger_spec_id=fix_spec.spec_id,
        frozen_at=emitted_at,
    )
    write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        provisional_state.model_copy(update={"initial_family_plan": initial_plan}),
        updated_at=emitted_at,
    )
    refresh_stable_spec_registry(
        paths.specs_stable_dir,
        paths.specs_stable_dir / ".frozen",
        paths.specs_index_file,
        relative_to=paths.root,
        updated_at=emitted_at,
    )
    _write_json_model(
        goalspec_lineage_path,
        GoalSpecLineageRecord(
            spec_id=fix_spec.spec_id,
            goal_id=document.incident_id or incident_path.stem,
            queue_path=fix_spec.queue_spec_path,
            reviewed_path=fix_spec.reviewed_path,
            archived_path="",
            stable_spec_paths=(fix_spec.golden_spec_path, fix_spec.phase_spec_path),
            pending_shard_path="",
        ),
    )
    remediation_record = IncidentRemediationRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        incident_id=document.incident_id or incident_path.stem,
        incident_title=document.title,
        resolved_path=resolved_relative_path,
        lineage_path=lineage_relative_path,
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        goalspec_lineage_path=_relative_path(goalspec_lineage_path, relative_to=paths.root),
        fix_spec=fix_spec,
    )
    _write_json_model(remediation_record_path, remediation_record)
    return remediation_record


def _load_incident_remediation_record(path: Path) -> IncidentRemediationRecord:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise IncidentExecutionError(f"{path.as_posix()} must contain a JSON object")
    return IncidentRemediationRecord.model_validate(payload)


def _incident_archive_evidence_paths(
    paths: RuntimePaths,
    *,
    run_id: str,
    lineage_path: Path,
) -> tuple[str, ...]:
    ordered_paths: list[Path] = []

    def _append_if_present(path: Path) -> None:
        if path.exists() and path not in ordered_paths:
            ordered_paths.append(path)

    _append_if_present(_incident_record_path(paths, stage="intake", run_id=run_id))
    _append_if_present(_incident_record_path(paths, stage="resolve", run_id=run_id))
    remediation_path = _incident_remediation_record_path(paths, run_id=run_id)
    _append_if_present(remediation_path)
    if remediation_path.exists():
        remediation_record = _load_incident_remediation_record(remediation_path)
        for token in (
            remediation_record.taskmaster_record_path,
            remediation_record.taskaudit_record_path,
            remediation_record.task_provenance_path,
        ):
            if token:
                _append_if_present(_resolve_path_token(token, relative_to=paths.root))
    _append_if_present(lineage_path)
    return tuple(_relative_path(path, relative_to=paths.root) for path in ordered_paths)


def _incident_paths_from_checkpoint(paths: RuntimePaths, checkpoint: ResearchCheckpoint) -> list[Path]:
    candidates: list[Path] = []
    for ownership in checkpoint.owned_queues:
        if ownership.item_path is not None:
            candidates.append(_resolve_path_token(ownership.item_path, relative_to=paths.root))

    active_request = checkpoint.active_request
    if active_request is None:
        return candidates

    if active_request.incident_document is not None:
        candidates.append(_resolve_path_token(active_request.incident_document.source_path, relative_to=paths.root))
    if active_request.blocker_record is not None and active_request.blocker_record.incident_path is not None:
        candidates.append(_resolve_path_token(active_request.blocker_record.incident_path, relative_to=paths.root))
    if active_request.handoff is not None and active_request.handoff.incident_path is not None:
        candidates.append(_resolve_path_token(active_request.handoff.incident_path, relative_to=paths.root))
    payload_path = active_request.payload.get("path")
    if payload_path:
        candidates.append(_resolve_path_token(str(payload_path), relative_to=paths.root))
    return candidates


def _materializable_incident_path(paths: RuntimePaths, checkpoint: ResearchCheckpoint) -> Path | None:
    if checkpoint.node_id != "incident_intake":
        return None
    candidates = _incident_paths_from_checkpoint(paths, checkpoint)
    if not candidates:
        return None
    return candidates[0]


def _render_materialized_incident_document(
    checkpoint: ResearchCheckpoint,
    *,
    incident_path: Path,
    emitted_at: datetime,
) -> str:
    active_request = checkpoint.active_request
    blocker_record = None if active_request is None else active_request.blocker_record
    handoff = checkpoint.parent_handoff or (None if active_request is None else active_request.handoff)
    incident_id = incident_path.stem
    title = (
        (None if handoff is None else handoff.task_title)
        or (None if blocker_record is None else blocker_record.task_title)
        or incident_id
    )
    source_task = (
        (None if handoff is None else handoff.task_id)
        or (None if blocker_record is None else blocker_record.source_task)
    )
    failure_signature = None if handoff is None else handoff.failure_signature
    timestamp = emitted_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    summary_lines = [
        f"- Consult returned `NEEDS_RESEARCH` during `{handoff.stage}`." if handoff is not None else None,
        (
            f"- {handoff.reason}"
            if handoff is not None and handoff.reason
            else (
                None
                if blocker_record is None or not blocker_record.root_cause_summary
                else f"- {blocker_record.root_cause_summary}"
            )
        ),
    ]
    detail_lines = [
        f"- **Incident-ID:** `{incident_id}`",
        f"- **Source task:** `{source_task}`" if source_task else None,
        f"- **Parent handoff:** `{handoff.handoff_id}`" if handoff is not None else None,
        (
            f"- **Parent run:** `{handoff.parent_run.run_id}`"
            if handoff is not None and handoff.parent_run is not None
            else None
        ),
        f"- **Failure signature:** `{failure_signature}`" if failure_signature else None,
        (
            f"- **Diagnostics directory:** `{handoff.diagnostics_dir.as_posix()}`"
            if handoff is not None and handoff.diagnostics_dir is not None
            else None
        ),
    ]
    lines = [
        "---",
        f"incident_id: {incident_id}",
        "status: incoming",
        f"opened_at: {timestamp}",
        f"updated_at: {timestamp}",
    ]
    if source_task:
        lines.append(f"source_task: {source_task}")
    if failure_signature:
        lines.append(f"failure_signature: {failure_signature}")
    lines.extend(
        [
            "---",
            "",
            f"# {title}",
            "",
            *[line for line in detail_lines if line],
            "",
            "## Summary",
            *[line for line in summary_lines if line],
            "",
        ]
    )
    return "\n".join(lines)


def materialize_incident_source(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    emitted_at: datetime | None = None,
) -> Path | None:
    """Create the authoritative incoming incident file for a materializable intake checkpoint."""

    incident_path = _materializable_incident_path(paths, checkpoint)
    if incident_path is None or incident_path.exists():
        return incident_path
    observed_at = emitted_at or datetime.now(timezone.utc)
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        incident_path,
        _render_materialized_incident_document(
            checkpoint,
            incident_path=incident_path,
            emitted_at=observed_at,
        ),
    )
    return incident_path


def resolve_incident_source(paths: RuntimePaths, checkpoint: ResearchCheckpoint) -> tuple[Path, IncidentDocument]:
    """Resolve the current incident artifact from checkpoint state."""

    checked: list[Path] = []
    for candidate in _incident_paths_from_checkpoint(paths, checkpoint):
        if candidate in checked:
            continue
        checked.append(candidate)
        if candidate.exists():
            return candidate, load_incident_document(candidate)
    materialized = materialize_incident_source(paths, checkpoint)
    if materialized is not None and materialized.exists():
        return materialized, load_incident_document(materialized)
    raise IncidentExecutionError("incident checkpoint has no existing source artifact to execute")


def incident_source_exists(paths: RuntimePaths, checkpoint: ResearchCheckpoint) -> bool:
    """Return True when a checkpoint can resolve an incident artifact on disk."""

    checked: list[Path] = []
    for candidate in _incident_paths_from_checkpoint(paths, checkpoint):
        if candidate in checked:
            continue
        checked.append(candidate)
        if candidate.exists():
            return True
    return _materializable_incident_path(paths, checkpoint) is not None


def incident_source_on_disk(paths: RuntimePaths, checkpoint: ResearchCheckpoint) -> bool:
    """Return True only when one checkpoint candidate already exists on disk."""

    checked: list[Path] = []
    for candidate in _incident_paths_from_checkpoint(paths, checkpoint):
        if candidate in checked:
            continue
        checked.append(candidate)
        if candidate.exists():
            return True
    return False


def _lineage_context(
    checkpoint: ResearchCheckpoint,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    active_request = checkpoint.active_request
    if active_request is None:
        return None, None, None, None, None
    blocker_record = active_request.blocker_record
    handoff = checkpoint.parent_handoff or active_request.handoff
    source_task = None
    blocker_ledger_path = None
    blocker_item_key = None
    if blocker_record is not None:
        source_task = blocker_record.source_task
        blocker_ledger_path = _relative_path(blocker_record.ledger_path, relative_to=blocker_record.ledger_path.parent.parent)
        blocker_item_key = blocker_record.item_key
    elif handoff is not None:
        source_task = handoff.task_id
    return (
        source_task,
        blocker_ledger_path,
        blocker_item_key,
        None if handoff is None else handoff.handoff_id,
        None if handoff is None or handoff.parent_run is None else handoff.parent_run.run_id,
    )


def _updated_lineage_record(
    *,
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    document: IncidentDocument,
    source_path: Path,
    current_path: Path,
    stage: Literal["incident_intake", "incident_resolve", "incident_archive"],
    updated_at: datetime,
) -> tuple[Path, IncidentLineageRecord]:
    incident_key = _incident_key(document, source_path)
    lineage_path = _incident_lineage_path(paths, incident_key=incident_key)
    existing = _load_existing_lineage(lineage_path)
    source_task, blocker_ledger_path, blocker_item_key, parent_handoff_id, parent_run_id = _lineage_context(checkpoint)
    record = IncidentLineageRecord(
        incident_id=document.incident_id or source_path.stem,
        title=document.title,
        source_path=(
            existing.source_path
            if existing is not None
            else _relative_path(source_path, relative_to=paths.root)
        ),
        current_path=_relative_path(current_path, relative_to=paths.root),
        working_path=(
            _relative_path(current_path, relative_to=paths.root)
            if current_path.parent.name == IncidentLifecycleStatus.WORKING.value
            else (None if existing is None else existing.working_path)
        ),
        resolved_path=(
            _relative_path(current_path, relative_to=paths.root)
            if current_path.parent.name == IncidentLifecycleStatus.RESOLVED.value
            else (None if existing is None else existing.resolved_path)
        ),
        archived_path=(
            _relative_path(current_path, relative_to=paths.root)
            if current_path.parent.name == IncidentLifecycleStatus.ARCHIVED.value
            else (None if existing is None else existing.archived_path)
        ),
        source_task=source_task if source_task is not None else (None if existing is None else existing.source_task),
        blocker_ledger_path=(
            blocker_ledger_path
            if blocker_ledger_path is not None
            else (None if existing is None else existing.blocker_ledger_path)
        ),
        blocker_item_key=(
            blocker_item_key
            if blocker_item_key is not None
            else (None if existing is None else existing.blocker_item_key)
        ),
        parent_handoff_id=(
            parent_handoff_id
            if parent_handoff_id is not None
            else (None if existing is None else existing.parent_handoff_id)
        ),
        parent_run_id=(
            parent_run_id
            if parent_run_id is not None
            else (None if existing is None else existing.parent_run_id)
        ),
        remediation_spec_id=None if existing is None else existing.remediation_spec_id,
        remediation_record_path=None if existing is None else existing.remediation_record_path,
        last_stage=stage,
        updated_at=updated_at,
    )
    _write_json_model(lineage_path, record)
    return lineage_path, record


def _queue_ownership_for_incident_path(
    *,
    incident_path: Path,
    run_id: str,
    emitted_at: datetime,
) -> ResearchQueueOwnership:
    from .state import ResearchQueueFamily, ResearchQueueOwnership

    return ResearchQueueOwnership(
        family=ResearchQueueFamily.INCIDENT,
        queue_path=incident_path.parent,
        item_path=incident_path,
        owner_token=run_id,
        acquired_at=emitted_at,
    )


def execute_incident_intake(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> IncidentIntakeExecutionResult:
    """Move one incident into the working queue and persist intake lineage."""

    emitted_at = emitted_at or datetime.now(timezone.utc)
    source_path, document = resolve_incident_source(paths, checkpoint)
    current_path = source_path
    lifecycle = document.lifecycle_status or IncidentLifecycleStatus(source_path.parent.name)
    if lifecycle is IncidentLifecycleStatus.INCOMING:
        current_path = _move_incident(source_path, _target_incident_path(paths, lifecycle, source_path))
    lineage_path, lineage_record = _updated_lineage_record(
        paths=paths,
        checkpoint=checkpoint,
        document=document,
        source_path=source_path,
        current_path=current_path,
        stage="incident_intake",
        updated_at=emitted_at,
    )
    record_path = _incident_record_path(paths, stage="intake", run_id=run_id)
    source_task, blocker_ledger_path, blocker_item_key, parent_handoff_id, parent_run_id = _lineage_context(checkpoint)
    _write_json_model(
        record_path,
        IncidentIntakeRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            incident_id=lineage_record.incident_id,
            title=document.title,
            source_path=_relative_path(source_path, relative_to=paths.root),
            working_path=_relative_path(current_path, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
            source_task=source_task,
            blocker_ledger_path=blocker_ledger_path,
            blocker_item_key=blocker_item_key,
            parent_handoff_id=parent_handoff_id,
            parent_run_id=parent_run_id,
        ),
    )
    return IncidentIntakeExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        working_path=_relative_path(current_path, relative_to=paths.root),
        queue_ownership=_queue_ownership_for_incident_path(
            incident_path=current_path,
            run_id=run_id,
            emitted_at=emitted_at,
        ),
    )


def execute_incident_resolve(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> IncidentResolveExecutionResult:
    """Move one incident into the resolved queue and persist remediation evidence."""

    emitted_at = emitted_at or datetime.now(timezone.utc)
    source_path, document = resolve_incident_source(paths, checkpoint)
    current_path = source_path
    lifecycle = document.lifecycle_status or IncidentLifecycleStatus(source_path.parent.name)
    if lifecycle in {IncidentLifecycleStatus.INCOMING, IncidentLifecycleStatus.WORKING}:
        current_path = _move_incident(source_path, paths.agents_dir / "ideas" / "incidents" / "resolved" / source_path.name)
    lineage_path, lineage_record = _updated_lineage_record(
        paths=paths,
        checkpoint=checkpoint,
        document=document,
        source_path=source_path,
        current_path=current_path,
        stage="incident_resolve",
        updated_at=emitted_at,
    )
    remediation_record = _write_incident_remediation_bundle(
        paths,
        document=document,
        incident_path=current_path,
        lineage_path=lineage_path,
        run_id=run_id,
        emitted_at=emitted_at,
    )
    _write_json_model(
        lineage_path,
        lineage_record.model_copy(
            update={
                "remediation_spec_id": remediation_record.fix_spec.spec_id,
                "remediation_record_path": _relative_path(
                    _incident_remediation_record_path(paths, run_id=run_id),
                    relative_to=paths.root,
                ),
                "updated_at": emitted_at,
            }
        ),
    )
    record_path = _incident_record_path(paths, stage="resolve", run_id=run_id)
    source_task, blocker_ledger_path, blocker_item_key, parent_handoff_id, parent_run_id = _lineage_context(checkpoint)
    _write_json_model(
        record_path,
        IncidentResolveRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            incident_id=lineage_record.incident_id,
            title=document.title,
            source_path=_relative_path(source_path, relative_to=paths.root),
            resolved_path=_relative_path(current_path, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
            resolution_summary=f"Incident {lineage_record.incident_id} advanced to resolved remediation state.",
            source_task=source_task,
            blocker_ledger_path=blocker_ledger_path,
            blocker_item_key=blocker_item_key,
            parent_handoff_id=parent_handoff_id,
            parent_run_id=parent_run_id,
            remediation_record_path=_relative_path(
                _incident_remediation_record_path(paths, run_id=run_id),
                relative_to=paths.root,
            ),
            remediation_spec_id=remediation_record.fix_spec.spec_id,
        ),
    )
    return IncidentResolveExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        resolved_path=_relative_path(current_path, relative_to=paths.root),
        remediation_record_path=_relative_path(
            _incident_remediation_record_path(paths, run_id=run_id),
            relative_to=paths.root,
        ),
        reviewed_spec_path=remediation_record.fix_spec.reviewed_path,
        queue_ownership=_queue_ownership_for_incident_path(
            incident_path=current_path,
            run_id=run_id,
            emitted_at=emitted_at,
        ),
    )


def execute_incident_archive(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> IncidentArchiveExecutionResult:
    """Move one incident into the archived queue and persist closeout evidence."""

    emitted_at = emitted_at or datetime.now(timezone.utc)
    source_path, document = resolve_incident_source(paths, checkpoint)
    current_path = source_path
    if source_path.parent.name != IncidentLifecycleStatus.ARCHIVED.value:
        current_path = _move_incident(
            source_path,
            paths.agents_dir / "ideas" / "incidents" / "archived" / source_path.name,
        )
    lineage_path, lineage_record = _updated_lineage_record(
        paths=paths,
        checkpoint=checkpoint,
        document=document,
        source_path=source_path,
        current_path=current_path,
        stage="incident_archive",
        updated_at=emitted_at,
    )
    record_path = _incident_record_path(paths, stage="archive", run_id=run_id)
    source_task, blocker_ledger_path, blocker_item_key, parent_handoff_id, parent_run_id = _lineage_context(checkpoint)
    evidence_paths = _incident_archive_evidence_paths(
        paths,
        run_id=run_id,
        lineage_path=lineage_path,
    )
    _write_json_model(
        record_path,
        IncidentArchiveRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            incident_id=lineage_record.incident_id,
            title=document.title,
            source_path=_relative_path(source_path, relative_to=paths.root),
            archived_path=_relative_path(current_path, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
            evidence_paths=evidence_paths,
            source_task=source_task,
            blocker_ledger_path=blocker_ledger_path,
            blocker_item_key=blocker_item_key,
            parent_handoff_id=parent_handoff_id,
            parent_run_id=parent_run_id,
        ),
    )
    return IncidentArchiveExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        archived_path=_relative_path(current_path, relative_to=paths.root),
        queue_ownership=_queue_ownership_for_incident_path(
            incident_path=current_path,
            run_id=run_id,
            emitted_at=emitted_at,
        ),
    )


def _checkpoint_handoff(checkpoint: ResearchCheckpoint):
    if checkpoint.parent_handoff is not None:
        return checkpoint.parent_handoff
    active_request = checkpoint.active_request
    if active_request is None:
        return None
    return active_request.handoff


def _persist_recovery_decision(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    emitted_at: datetime,
    remediation_record: IncidentRemediationRecord,
    remediation_record_path: str,
    taskaudit_record_path: str,
    task_provenance_path: str,
    pending_card_count: int,
    backlog_card_count: int,
) -> None:
    handoff = _checkpoint_handoff(checkpoint)
    if handoff is None or handoff.recovery_batch_id is None:
        return

    latch = load_research_recovery_latch(paths.research_recovery_latch_file)
    if latch is None or latch.batch_id != handoff.recovery_batch_id:
        return
    if latch.handoff is not None and latch.handoff.handoff_id != handoff.handoff_id:
        return

    decision_type = (
        "regenerated_backlog_work"
        if pending_card_count > 0
        else "durable_remediation_decision"
    )
    decision = ResearchRecoveryDecision(
        decision_type=decision_type,
        decided_at=emitted_at,
        remediation_spec_id=remediation_record.fix_spec.spec_id,
        remediation_record_path=Path(remediation_record_path),
        taskaudit_record_path=Path(taskaudit_record_path),
        task_provenance_path=Path(task_provenance_path),
        lineage_path=Path(remediation_record.lineage_path),
        pending_card_count=pending_card_count,
        backlog_card_count=backlog_card_count,
    )
    write_research_recovery_latch(
        paths.research_recovery_latch_file,
        latch.model_copy(
            update={
                "handoff": latch.handoff or handoff,
                "remediation_decision": decision,
            }
        ),
    )


def execute_incident_task_generation(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    dispatch: "CompiledResearchDispatch",
    run_id: str,
    emitted_at: datetime | None = None,
    remediation_record_path: str | Path | None = None,
) -> IncidentTaskGenerationExecutionResult:
    """Run Taskmaster and Taskaudit for an incident-produced remediation package."""

    from .state import ResearchQueueFamily, ResearchQueueOwnership
    from .taskaudit import execute_taskaudit
    from .taskmaster import execute_taskmaster

    emitted_at = emitted_at or datetime.now(timezone.utc)
    record_path = _resolve_path_token(
        remediation_record_path or _incident_remediation_record_path(paths, run_id=run_id),
        relative_to=paths.root,
    )
    remediation_record = _load_incident_remediation_record(record_path)
    reviewed_spec_path = _resolve_path_token(remediation_record.fix_spec.reviewed_path, relative_to=paths.root)
    taskmaster_checkpoint = checkpoint.model_copy(
        update={
            "node_id": "taskmaster",
            "stage_kind_id": "research.taskmaster",
            "updated_at": emitted_at,
            "owned_queues": (
                ResearchQueueOwnership(
                    family=ResearchQueueFamily.GOALSPEC,
                    queue_path=reviewed_spec_path.parent,
                    item_path=reviewed_spec_path,
                    owner_token=run_id,
                    acquired_at=emitted_at,
                ),
            ),
        }
    )
    taskmaster_result = execute_taskmaster(
        paths,
        taskmaster_checkpoint,
        dispatch=dispatch,
        run_id=run_id,
        emitted_at=emitted_at,
    )
    taskaudit_result = execute_taskaudit(
        paths,
        run_id=run_id,
        emitted_at=emitted_at,
    )
    _write_json_model(
        record_path,
        remediation_record.model_copy(
            update={
                "taskmaster_record_path": taskmaster_result.record_path,
                "taskaudit_record_path": taskaudit_result.record_path,
                "task_provenance_path": taskaudit_result.provenance_path,
            }
        ),
    )
    persisted_remediation_record = _load_incident_remediation_record(record_path)
    _persist_recovery_decision(
        paths,
        checkpoint,
        emitted_at=emitted_at,
        remediation_record=persisted_remediation_record,
        remediation_record_path=_relative_path(record_path, relative_to=paths.root),
        taskaudit_record_path=taskaudit_result.record_path,
        task_provenance_path=taskaudit_result.provenance_path,
        pending_card_count=taskaudit_result.pending_card_count,
        backlog_card_count=taskaudit_result.backlog_card_count,
    )
    return IncidentTaskGenerationExecutionResult(
        remediation_record_path=_relative_path(record_path, relative_to=paths.root),
        taskmaster_record_path=taskmaster_result.record_path,
        taskaudit_record_path=taskaudit_result.record_path,
    )


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


__all__ = [
    "IncidentArchiveExecutionResult",
    "IncidentArchiveRecord",
    "IncidentDocument",
    "IncidentExecutionError",
    "IncidentIntakeExecutionResult",
    "IncidentIntakeRecord",
    "IncidentLifecycleStatus",
    "IncidentLineageRecord",
    "IncidentRemediationRecord",
    "IncidentRecurrenceLedger",
    "IncidentRecurrenceObservation",
    "IncidentRecurrenceRecord",
    "IncidentResolveExecutionResult",
    "IncidentResolveRecord",
    "IncidentSeverity",
    "IncidentTaskGenerationExecutionResult",
    "default_incident_recurrence_ledger",
    "execute_incident_archive",
    "execute_incident_intake",
    "execute_incident_resolve",
    "execute_incident_task_generation",
    "find_equivalent_incident",
    "incident_source_exists",
    "incident_dedup_signature",
    "load_incident_document",
    "load_incident_recurrence_ledger",
    "parse_incident_document",
    "record_incident_recurrence",
    "resolve_deduplicated_incident_path",
    "resolve_incident_source",
    "write_incident_recurrence_ledger",
]
