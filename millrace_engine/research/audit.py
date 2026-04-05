"""Typed audit queue contracts, stage records, and queue helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal
import json
import re

from pydantic import Field, field_validator, model_validator

from ..baseline_assets import packaged_baseline_asset
from ..contracts import (
    AuditContract,
    AuditExecutionFinding,
    AuditExecutionReport,
    ContractModel,
    ResearchStatus,
    _normalize_datetime,
    _normalize_path,
)
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .audit_gate_helpers import _evaluate_completion_gate
from .audit_remediation_helpers import _persist_audit_remediation
from .audit_storage_helpers import (
    _audit_record_path,
    _audit_remediation_record_path,
    _audited_source_path,
    _execution_report_path,
    _load_json_model,
    _relative_path,
    _resolve_path_token,
    _validate_record_path,
    _write_audit_history,
    _write_audit_summary,
    _write_json_model,
    load_audit_remediation_record,
    load_audit_summary,
)
if TYPE_CHECKING:
    from .state import ResearchCheckpoint


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", flags=re.DOTALL)
_HEADING_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", flags=re.MULTILINE)
_SECTION_HEADING_RE = re.compile(r"^##+\s+(?P<title>.+?)\s*$")
_LIST_MARKER_RE = re.compile(r"^(?:[-*]|\d+\.)\s+")
_WHITESPACE_RE = re.compile(r"\s+")
_COMMAND_SECTION_NAMES = frozenset({"command", "commands", "command evidence", "required commands"})
_SUMMARY_SECTION_NAMES = frozenset({"summary", "summaries", "findings", "decision", "results"})
_SCOPE_QUEUE_EMPTY = "orchestration-loop-backlog-empty-handoff"
_AUDIT_ARTIFACT_SCHEMA_VERSION = "1.0"
_PACKAGED_STRICT_CONTRACT_REF = "packaged:agents/audit/strict_contract.json"
_PACKAGED_COMPLETION_MANIFEST_REF = "packaged:agents/audit/completion_manifest.json"
_PACKAGED_OBJECTIVE_CONTRACT_REF = "packaged:agents/objective/contract.yaml"
_AUDIT_HISTORY_RETENTION_KEEP = 100


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


def _extract_heading_title(text: str) -> str | None:
    match = _HEADING_RE.search(text)
    if match is None:
        return None
    return _normalize_required_text(match.group("title"), field_name="title")


def _normalize_section_name(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip()).casefold()


def _normalize_section_line(value: str) -> str | None:
    stripped = _LIST_MARKER_RE.sub("", value.strip())
    stripped = stripped.strip("`").strip()
    return _normalize_optional_text(stripped, field_name="section line")


def _extract_section_lines(text: str, *, section_names: frozenset[str]) -> tuple[str, ...]:
    _, body = _parse_frontmatter(text)
    collected: list[str] = []
    active_section: str | None = None
    in_code_block = False

    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        heading_match = _SECTION_HEADING_RE.match(stripped)
        if heading_match and not in_code_block:
            candidate = _normalize_section_name(heading_match.group("title"))
            active_section = candidate if candidate in section_names else None
            continue

        if active_section is None or not stripped:
            continue

        normalized = _normalize_section_line(raw_line)
        if normalized is not None:
            collected.append(normalized)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in collected:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return tuple(deduped)


class AuditTrigger(str, Enum):
    """Supported audit trigger vocabulary."""

    QUEUE_EMPTY = "queue_empty"
    MANUAL = "manual"
    INCIDENT_FOLLOWUP = "incident_followup"
    OTHER = "other"


class AuditLifecycleStatus(str, Enum):
    """Supported audit queue lifecycle locations."""

    INCOMING = "incoming"
    WORKING = "working"
    PASSED = "passed"
    FAILED = "failed"


class AuditQueueRecord(ContractModel):
    """Validated audit queue document loaded from one markdown file."""

    source_path: Path
    audit_id: str
    title: str
    scope: str
    trigger: AuditTrigger
    lifecycle_status: AuditLifecycleStatus
    owner: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("source_path", mode="before")
    @classmethod
    def normalize_source_path(cls, value: str | Path) -> Path:
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError("source_path may not be empty")
        return normalized

    @field_validator("audit_id", "title", "scope")
    @classmethod
    def normalize_required_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("owner")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        return _normalize_optional_text(value, field_name=field_name)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)

    @field_validator("trigger", mode="before")
    @classmethod
    def normalize_trigger(cls, value: AuditTrigger | str) -> AuditTrigger:
        return AuditTrigger(str(value).strip().lower())

    @field_validator("lifecycle_status", mode="before")
    @classmethod
    def normalize_lifecycle_status(
        cls,
        value: AuditLifecycleStatus | str,
    ) -> AuditLifecycleStatus:
        return AuditLifecycleStatus(str(value).strip().lower())

    @model_validator(mode="after")
    def validate_timestamps(self) -> "AuditQueueRecord":
        if self.created_at is not None and self.updated_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at may not be earlier than created_at")
        return self


class AuditIntakeRecord(ContractModel):
    """Durable intake record for one audit queue item."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["audit_intake_record"] = "audit_intake_record"
    run_id: str
    emitted_at: datetime
    audit_id: str
    title: str
    trigger: AuditTrigger
    scope: str
    source_path: str
    working_path: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("run_id", "audit_id", "title", "scope", "source_path", "working_path")
    @classmethod
    def normalize_required_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)


class AuditValidateRecord(ContractModel):
    """Durable validation report for one audit run."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["audit_validate_report"] = "audit_validate_report"
    run_id: str
    emitted_at: datetime
    audit_id: str
    title: str
    trigger: AuditTrigger
    scope: str
    working_path: str
    execution_report_path: str
    finding_count: int = Field(default=0, ge=0)
    findings: tuple[str, ...] = ()
    summary: str
    recommended_decision: Literal["pass", "fail"] = "pass"

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("run_id", "audit_id", "title", "scope", "working_path", "execution_report_path", "summary")
    @classmethod
    def normalize_required_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("findings", mode="before")
    @classmethod
    def normalize_findings(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if value is None:
            return ()
        normalized: list[str] = []
        for item in value:
            text = _normalize_optional_text(item, field_name="finding")
            if text is not None:
                normalized.append(text)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_finding_count(self) -> "AuditValidateRecord":
        if self.finding_count != len(self.findings):
            raise ValueError("finding_count must match findings")
        return self


class AuditGatekeeperRecord(ContractModel):
    """Durable terminal decision record for one audit run."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["audit_gate_decision"] = "audit_gate_decision"
    run_id: str
    emitted_at: datetime
    audit_id: str
    title: str
    source_path: str
    terminal_path: str
    validate_record_path: str
    decision: Literal["audit_pass", "audit_fail"]
    final_status: ResearchStatus
    rationale: str
    gate_decision_path: str
    completion_decision_path: str
    remediation_record_path: str | None = None
    remediation_spec_id: str | None = None
    remediation_task_id: str | None = None

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "audit_id",
        "title",
        "source_path",
        "terminal_path",
        "validate_record_path",
        "rationale",
        "gate_decision_path",
        "completion_decision_path",
        "remediation_record_path",
        "remediation_spec_id",
        "remediation_task_id",
    )
    @classmethod
    def normalize_required_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        if field_name in {"remediation_record_path", "remediation_spec_id", "remediation_task_id"}:
            return _normalize_optional_text(value, field_name=field_name)
        return _normalize_required_text(value, field_name=field_name)


class AuditSummaryLastOutcome(ContractModel):
    """Compact latest-outcome snapshot mirrored into the workspace summary file."""

    status: Literal["AUDIT_PASS", "AUDIT_FAIL", "none"] = "none"
    details: str = "none"
    at: datetime | None = None
    audit_id: str | None = None
    title: str | None = None
    scope: str | None = None
    trigger: AuditTrigger | None = None
    decision: Literal["PASS", "FAIL"] | None = None
    reason_count: int = Field(default=0, ge=0)
    source_path: str | None = None
    terminal_path: str | None = None
    gate_decision_path: str | None = None
    completion_decision_path: str | None = None
    remediation_spec_id: str | None = None
    remediation_task_id: str | None = None
    remediation_record_path: str | None = None

    @field_validator("at", mode="before")
    @classmethod
    def normalize_at(cls, value: datetime | str | None) -> datetime | None:
        if value in (None, ""):
            return None
        return _normalize_datetime(value)

    @field_validator(
        "details",
        "audit_id",
        "title",
        "scope",
        "source_path",
        "terminal_path",
        "gate_decision_path",
        "completion_decision_path",
        "remediation_spec_id",
        "remediation_task_id",
        "remediation_record_path",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        if field_name == "details":
            return _normalize_required_text(value or "none", field_name=field_name)
        return _normalize_optional_text(value, field_name=field_name)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str | None) -> Literal["AUDIT_PASS", "AUDIT_FAIL", "none"]:
        if value is None:
            return "none"
        normalized = value.strip().upper()
        if normalized in {"AUDIT_PASS", "AUDIT_FAIL"}:
            return normalized
        return "none"

    @field_validator("decision", mode="before")
    @classmethod
    def normalize_decision(cls, value: str | None) -> Literal["PASS", "FAIL"] | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"PASS", "FAIL"}:
            raise ValueError("decision must be PASS or FAIL")
        return normalized


class AuditSummary(ContractModel):
    """Durable operator-facing audit summary."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    updated_at: datetime | None = None
    last_outcome: AuditSummaryLastOutcome | None = None
    counts: dict[str, int] = Field(default_factory=lambda: {"total": 0, "pass": 0, "fail": 0})

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        if value in (None, ""):
            return None
        return _normalize_datetime(value)

    @field_validator("counts", mode="before")
    @classmethod
    def normalize_counts(cls, value: dict[str, int] | None) -> dict[str, int]:
        payload = {"total": 0, "pass": 0, "fail": 0}
        if value:
            for key in payload:
                try:
                    parsed = int(value.get(key, 0))
                except (TypeError, ValueError):
                    parsed = 0
                payload[key] = parsed if parsed >= 0 else 0
        return payload


class AuditRemediationRecord(ContractModel):
    """Durable audit-failure remediation selection and enqueue record."""

    schema_version: Literal["1.0"] = _AUDIT_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["audit_remediation"] = "audit_remediation"
    run_id: str
    emitted_at: datetime
    audit_id: str
    title: str
    scope: str
    trigger: AuditTrigger
    source_path: str
    terminal_path: str
    gate_decision_path: str
    completion_decision_path: str
    validate_record_path: str
    execution_report_path: str
    selected_action: Literal["enqueue_backlog_task", "reuse_existing_task"]
    remediation_spec_id: str
    remediation_task_id: str
    remediation_task_title: str
    backlog_depth_after_enqueue: int = Field(ge=0)
    reasons: tuple[str, ...] = ()
    recovery_latch_updated: bool = False

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "audit_id",
        "title",
        "scope",
        "source_path",
        "terminal_path",
        "gate_decision_path",
        "completion_decision_path",
        "validate_record_path",
        "execution_report_path",
        "remediation_spec_id",
        "remediation_task_id",
        "remediation_task_title",
        mode="before",
    )
    @classmethod
    def normalize_required_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("reasons", mode="before")
    @classmethod
    def normalize_reasons(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if value is None:
            return ()
        normalized: list[str] = []
        for item in value:
            text = _normalize_optional_text(item, field_name="reason")
            if text is not None:
                normalized.append(text)
        return tuple(normalized)


class AuditIntakeExecutionResult(ContractModel):
    """Minimal intake result returned to the research plane."""

    record_path: str
    working_path: str
    audit_record: AuditQueueRecord

    @field_validator("record_path", "working_path")
    @classmethod
    def normalize_paths(cls, value: str | Path, info: object) -> str:
        field_name = getattr(info, "field_name", "path")
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError(f"{field_name} may not be empty")
        return normalized.as_posix()


class AuditValidateExecutionResult(ContractModel):
    """Minimal validate result returned to the research plane."""

    record_path: str
    working_path: str
    audit_record: AuditQueueRecord
    validate_record: AuditValidateRecord

    @field_validator("record_path", "working_path")
    @classmethod
    def normalize_paths(cls, value: str | Path, info: object) -> str:
        field_name = getattr(info, "field_name", "path")
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError(f"{field_name} may not be empty")
        return normalized.as_posix()


class AuditGatekeeperExecutionResult(ContractModel):
    """Minimal gatekeeper result returned to the research plane."""

    record_path: str
    terminal_path: str
    gate_decision_path: str
    completion_decision_path: str
    audit_record: AuditQueueRecord
    final_status: ResearchStatus
    remediation_record_path: str | None = None

    @field_validator(
        "record_path",
        "terminal_path",
        "gate_decision_path",
        "completion_decision_path",
        "remediation_record_path",
    )
    @classmethod
    def normalize_paths(cls, value: str | Path, info: object) -> str:
        field_name = getattr(info, "field_name", "path")
        if value is None and field_name == "remediation_record_path":
            return None
        normalized = _normalize_path(value)
        if normalized is None:
            raise ValueError(f"{field_name} may not be empty")
        return normalized.as_posix()


class AuditExecutionError(RuntimeError):
    """Raised when a supported audit stage cannot complete safely."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _frontmatter_value(frontmatter: dict[str, str], key: str, *, default: str) -> str:
    raw_value = frontmatter.get(key)
    if raw_value is None:
        return default
    normalized = raw_value.strip()
    return normalized or default


def _render_frontmatter(frontmatter: dict[str, str]) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _resolve_checkpoint_audit_record(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
) -> AuditQueueRecord:
    active_request = checkpoint.active_request
    candidates: list[tuple[Path, AuditLifecycleStatus | None]] = []
    if active_request is not None and active_request.audit_record is not None:
        source_path = _resolve_path_token(active_request.audit_record.source_path, relative_to=paths.root)
        expected_status = None
        parent_name = source_path.parent.name.strip().lower()
        if parent_name in {status.value for status in AuditLifecycleStatus}:
            expected_status = AuditLifecycleStatus(parent_name)
        candidates.append((source_path, expected_status))
    for ownership in checkpoint.owned_queues:
        if ownership.item_path is None:
            continue
        item_path = _resolve_path_token(ownership.item_path, relative_to=paths.root)
        expected_status = None
        parent_name = item_path.parent.name.strip().lower()
        if parent_name in {status.value for status in AuditLifecycleStatus}:
            expected_status = AuditLifecycleStatus(parent_name)
        candidates.append((item_path, expected_status))
    seen: set[Path] = set()
    for candidate_path, expected_status in candidates:
        if candidate_path in seen or not candidate_path.exists():
            continue
        seen.add(candidate_path)
        return load_audit_queue_record(candidate_path, expected_status=expected_status)
    raise AuditExecutionError("audit checkpoint does not reference an available audit queue item")


def _load_audit_contract(paths: RuntimePaths) -> tuple[AuditContract, str]:
    contract_path = paths.audit_strict_contract_file
    if contract_path.exists():
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
        contract_ref = _relative_path(contract_path, relative_to=paths.root)
    else:
        payload = json.loads(packaged_baseline_asset("agents/audit/strict_contract.json").read_text(encoding="utf-8"))
        contract_ref = _PACKAGED_STRICT_CONTRACT_REF
    return AuditContract.model_validate(payload), contract_ref


def _build_execution_report(
    *,
    paths: RuntimePaths,
    run_id: str,
    emitted_at: datetime,
    record: AuditQueueRecord,
    contract: AuditContract,
    strict_contract_ref: str,
) -> AuditExecutionReport:
    source_text = record.source_path.read_text(encoding="utf-8")
    observed_commands = _extract_section_lines(source_text, section_names=_COMMAND_SECTION_NAMES)
    observed_summaries = _extract_section_lines(source_text, section_names=_SUMMARY_SECTION_NAMES)
    findings: list[AuditExecutionFinding] = []

    if contract.enabled:
        for required_substring in contract.required_command_substrings:
            matching_commands = tuple(
                command for command in observed_commands if required_substring.casefold() in command.casefold()
            )
            if matching_commands:
                continue
            findings.append(
                AuditExecutionFinding(
                    kind="missing_required_command_substring",
                    expected=required_substring,
                    observed=observed_commands,
                    message=f"Missing required command substring `{required_substring}`.",
                )
            )

        for forbidden_marker in contract.forbidden_command_markers:
            offending_commands = tuple(
                command for command in observed_commands if forbidden_marker.casefold() in command.casefold()
            )
            if not offending_commands:
                continue
            findings.append(
                AuditExecutionFinding(
                    kind="forbidden_command_marker",
                    expected=forbidden_marker,
                    observed=offending_commands,
                    message=f"Forbidden command marker `{forbidden_marker}` found in observed commands.",
                )
            )

        for required_summary in contract.required_summaries:
            matching_summaries = tuple(
                summary for summary in observed_summaries if required_summary.casefold() in summary.casefold()
            )
            if matching_summaries:
                continue
            findings.append(
                AuditExecutionFinding(
                    kind="missing_required_summary",
                    expected=required_summary,
                    observed=observed_summaries,
                    message=f"Missing required summary `{required_summary}`.",
                )
            )

    return AuditExecutionReport(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=record.audit_id,
        working_path=_relative_path(record.source_path, relative_to=paths.root),
        contract_id=contract.contract_id,
        strict_contract_path=strict_contract_ref,
        strict_contract_enabled=contract.enabled,
        observed_commands=observed_commands,
        observed_summaries=observed_summaries,
        command_count=len(observed_commands),
        summary_count=len(observed_summaries),
        findings=findings,
        finding_count=len(findings),
        passed=not findings,
    )


def _move_audit_record(
    record: AuditQueueRecord,
    *,
    target_status: AuditLifecycleStatus,
    owner: str,
    updated_at: datetime,
) -> AuditQueueRecord:
    source_path = record.source_path
    target_dir = source_path.parent.parent / target_status.value
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / source_path.name
    updated_iso = updated_at.isoformat().replace("+00:00", "Z")

    if source_path.exists():
        frontmatter, body = _parse_frontmatter(source_path.read_text(encoding="utf-8"))
        frontmatter["audit_id"] = _frontmatter_value(frontmatter, "audit_id", default=record.audit_id)
        frontmatter["scope"] = _frontmatter_value(frontmatter, "scope", default=record.scope)
        frontmatter["trigger"] = _frontmatter_value(frontmatter, "trigger", default=record.trigger.value)
        frontmatter["status"] = target_status.value
        frontmatter["owner"] = owner
        frontmatter["created_at"] = _frontmatter_value(frontmatter, "created_at", default=updated_iso)
        frontmatter["updated_at"] = updated_iso
        write_text_atomic(target_path, _render_frontmatter(frontmatter) + "\n\n" + body.lstrip("\n"))
        if source_path != target_path:
            source_path.unlink()
    elif not target_path.exists():
        raise AuditExecutionError(f"audit queue item is missing: {source_path.as_posix()}")

    return load_audit_queue_record(target_path, expected_status=target_status)


def execute_audit_intake(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> AuditIntakeExecutionResult:
    """Move one audit item into the working queue and persist intake evidence."""

    emitted_at = emitted_at or _utcnow()
    record = _resolve_checkpoint_audit_record(paths, checkpoint)
    working_record = record
    if record.lifecycle_status is AuditLifecycleStatus.INCOMING:
        working_record = _move_audit_record(
            record,
            target_status=AuditLifecycleStatus.WORKING,
            owner=run_id,
            updated_at=emitted_at,
        )
    elif record.lifecycle_status is not AuditLifecycleStatus.WORKING:
        raise AuditExecutionError(
            f"audit intake requires incoming or working status, got {record.lifecycle_status.value}"
        )

    intake_record = AuditIntakeRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=working_record.audit_id,
        title=working_record.title,
        trigger=working_record.trigger,
        scope=working_record.scope,
        source_path=_relative_path(record.source_path, relative_to=paths.root),
        working_path=_relative_path(working_record.source_path, relative_to=paths.root),
    )
    record_path = _audit_record_path(paths, stage="intake", run_id=run_id)
    _write_json_model(record_path, intake_record)
    return AuditIntakeExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        working_path=_relative_path(working_record.source_path, relative_to=paths.root),
        audit_record=working_record,
    )


def execute_audit_validate(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> AuditValidateExecutionResult:
    """Evaluate the strict command contract and persist deterministic audit evidence."""

    emitted_at = emitted_at or _utcnow()
    record = _resolve_checkpoint_audit_record(paths, checkpoint)
    if record.lifecycle_status is not AuditLifecycleStatus.WORKING:
        raise AuditExecutionError(
            f"audit validate requires a working audit item, got {record.lifecycle_status.value}"
        )

    contract, strict_contract_ref = _load_audit_contract(paths)
    execution_report = _build_execution_report(
        paths=paths,
        run_id=run_id,
        emitted_at=emitted_at,
        record=record,
        contract=contract,
        strict_contract_ref=strict_contract_ref,
    )
    execution_report_path = _execution_report_path(paths, run_id=run_id)
    _write_json_model(execution_report_path, execution_report)
    findings = tuple(finding.message for finding in execution_report.findings)
    if execution_report.passed:
        summary = (
            "Command-contract guard passed; observed audit evidence satisfied the strict contract."
            if contract.enabled
            else "Strict command-contract guard is disabled; no blocking findings were produced."
        )
        recommended_decision = "pass"
    else:
        summary = (
            "Command-contract guard failed; blocking findings were recorded in the durable execution report."
        )
        recommended_decision = "fail"
    validate_record = AuditValidateRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=record.audit_id,
        title=record.title,
        trigger=record.trigger,
        scope=record.scope,
        working_path=_relative_path(record.source_path, relative_to=paths.root),
        execution_report_path=_relative_path(execution_report_path, relative_to=paths.root),
        finding_count=len(findings),
        findings=findings,
        summary=summary,
        recommended_decision=recommended_decision,
    )
    record_path = _validate_record_path(paths, run_id=run_id)
    _write_json_model(record_path, validate_record)
    return AuditValidateExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        working_path=_relative_path(record.source_path, relative_to=paths.root),
        audit_record=record,
        validate_record=validate_record,
    )


def execute_audit_gatekeeper(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> AuditGatekeeperExecutionResult:
    """Persist the terminal audit decision and move the queue item to its final bucket."""

    emitted_at = emitted_at or _utcnow()
    record = _resolve_checkpoint_audit_record(paths, checkpoint)
    if record.lifecycle_status is not AuditLifecycleStatus.WORKING:
        raise AuditExecutionError(
            f"audit gatekeeper requires a working audit item, got {record.lifecycle_status.value}"
        )

    validate_record_path = _validate_record_path(paths, run_id=run_id)
    if not validate_record_path.exists():
        raise AuditExecutionError(
            f"audit gatekeeper requires a validate report at {validate_record_path.as_posix()}"
        )
    validate_record = AuditValidateRecord.model_validate(
        _load_json_model(validate_record_path, AuditValidateRecord)
    )

    gate_decision, completion_decision, final_status = _evaluate_completion_gate(
        paths=paths,
        run_id=run_id,
        emitted_at=emitted_at,
        record=record,
        validate_record=validate_record,
        validate_record_path=validate_record_path,
        packaged_objective_contract_ref=_PACKAGED_OBJECTIVE_CONTRACT_REF,
        packaged_completion_manifest_ref=_PACKAGED_COMPLETION_MANIFEST_REF,
    )
    execution_report_path = _resolve_path_token(validate_record.execution_report_path, relative_to=paths.root)
    audited_source_path = _audited_source_path(paths, run_id=run_id, record=record)
    if final_status is ResearchStatus.AUDIT_FAIL:
        target_status = AuditLifecycleStatus.FAILED
        decision = "audit_fail"
    else:
        target_status = AuditLifecycleStatus.PASSED
        decision = "audit_pass"

    terminal_record = _move_audit_record(
        record,
        target_status=target_status,
        owner=run_id,
        updated_at=emitted_at,
    )
    remediation_record = None
    if final_status is ResearchStatus.AUDIT_FAIL:
        remediation_record = _persist_audit_remediation(
            paths,
            checkpoint,
            run_id=run_id,
            emitted_at=emitted_at,
            audited_source_path=audited_source_path,
            record=record,
            terminal_record=terminal_record,
            gate_decision=gate_decision,
            completion_decision=completion_decision,
            validate_record_path=validate_record_path,
            execution_report_path=execution_report_path,
        )
    _write_audit_summary(
        paths,
        emitted_at=emitted_at,
        audited_source_path=audited_source_path,
        record=record,
        terminal_record=terminal_record,
        gate_decision=gate_decision,
        completion_decision=completion_decision,
        final_status=final_status,
        remediation_record=remediation_record,
    )
    _write_audit_history(
        paths,
        emitted_at=emitted_at,
        audited_source_path=audited_source_path,
        record=record,
        terminal_record=terminal_record,
        gate_decision=gate_decision,
        completion_decision=completion_decision,
        final_status=final_status,
        remediation_record=remediation_record,
        retention_keep=_AUDIT_HISTORY_RETENTION_KEEP,
    )
    gate_record = AuditGatekeeperRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=terminal_record.audit_id,
        title=terminal_record.title,
        source_path=audited_source_path,
        terminal_path=_relative_path(terminal_record.source_path, relative_to=paths.root),
        validate_record_path=_relative_path(validate_record_path, relative_to=paths.root),
        decision=decision,
        final_status=final_status,
        rationale=(
            f"Gate decision {gate_decision.decision} moved the queue item to `{target_status.value}`."
        ),
        gate_decision_path=gate_decision.gate_decision_path,
        completion_decision_path=completion_decision.completion_decision_path,
        remediation_record_path=(
            None if remediation_record is None else _relative_path(_audit_remediation_record_path(paths, run_id=run_id), relative_to=paths.root)
        ),
        remediation_spec_id=(None if remediation_record is None else remediation_record.remediation_spec_id),
        remediation_task_id=(None if remediation_record is None else remediation_record.remediation_task_id),
    )
    record_path = _audit_record_path(paths, stage="gatekeeper", run_id=run_id)
    _write_json_model(record_path, gate_record)
    return AuditGatekeeperExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        terminal_path=_relative_path(terminal_record.source_path, relative_to=paths.root),
        gate_decision_path=gate_decision.gate_decision_path,
        completion_decision_path=completion_decision.completion_decision_path,
        audit_record=terminal_record,
        final_status=final_status,
        remediation_record_path=(
            None if remediation_record is None else _relative_path(_audit_remediation_record_path(paths, run_id=run_id), relative_to=paths.root)
        ),
    )


def parse_audit_queue_record(
    text: str,
    *,
    source_path: Path,
    expected_status: AuditLifecycleStatus | None = None,
) -> AuditQueueRecord:
    """Validate one audit markdown document."""

    frontmatter, body = _parse_frontmatter(text)
    status = frontmatter.get("status") or source_path.parent.name
    audit_id = frontmatter.get("audit_id") or source_path.stem
    title = _extract_heading_title(body) or f"Audit {audit_id}"
    record = AuditQueueRecord.model_validate(
        {
            "source_path": source_path,
            "audit_id": audit_id,
            "title": title,
            "scope": frontmatter.get("scope") or "unscoped-audit",
            "trigger": frontmatter.get("trigger") or AuditTrigger.OTHER.value,
            "lifecycle_status": status,
            "owner": frontmatter.get("owner"),
            "created_at": frontmatter.get("created_at"),
            "updated_at": frontmatter.get("updated_at"),
        }
    )
    if expected_status is not None and record.lifecycle_status is not expected_status:
        raise ValueError(
            f"audit record status {record.lifecycle_status.value} does not match queue root {expected_status.value}"
        )
    return record


def load_audit_queue_record(
    path: Path,
    *,
    expected_status: AuditLifecycleStatus | None = None,
) -> AuditQueueRecord:
    """Read and validate one audit queue document from disk."""

    return parse_audit_queue_record(
        path.read_text(encoding="utf-8"),
        source_path=path,
        expected_status=expected_status,
    )


def ensure_backlog_empty_audit_ticket(
    paths: RuntimePaths,
    *,
    observed_at: datetime,
    backlog_depth: int = 0,
) -> AuditQueueRecord:
    """Materialize one actionable audit ticket for backlog-empty handoff."""

    incoming_dir = paths.agents_dir / "ideas" / "audit" / "incoming"
    working_dir = paths.agents_dir / "ideas" / "audit" / "working"
    for queue_dir, status in (
        (incoming_dir, AuditLifecycleStatus.INCOMING),
        (working_dir, AuditLifecycleStatus.WORKING),
    ):
        if not queue_dir.is_dir():
            continue
        for path in sorted(queue_dir.glob("*.md")):
            try:
                record = load_audit_queue_record(path, expected_status=status)
            except ValueError:
                continue
            if record.trigger is AuditTrigger.QUEUE_EMPTY and record.scope == _SCOPE_QUEUE_EMPTY:
                return record

    incoming_dir.mkdir(parents=True, exist_ok=True)
    timestamp = observed_at.strftime("%Y%m%dT%H%M%SZ")
    audit_id = f"AUD-BACKLOG-EMPTY-{timestamp}"
    path = incoming_dir / f"{audit_id}.md"
    suffix = 2
    while path.exists():
        path = incoming_dir / f"{audit_id}__{suffix}.md"
        suffix += 1

    observed_iso = observed_at.isoformat().replace("+00:00", "Z")
    write_text_atomic(
        path,
        "\n".join(
            [
                "---",
                f"audit_id: {path.stem}",
                f"scope: {_SCOPE_QUEUE_EMPTY}",
                f"trigger: {AuditTrigger.QUEUE_EMPTY.value}",
                f"status: {AuditLifecycleStatus.INCOMING.value}",
                "owner: research-plane",
                f"created_at: {observed_iso}",
                f"updated_at: {observed_iso}",
                "---",
                "",
                f"# Audit {path.stem}",
                "",
                "## Objective",
                "- Validate backlog-empty completion conditions through the audit queue.",
                "",
                "## Inputs",
                "- `agents/tasks.md`",
                "- `agents/tasksbacklog.md`",
                "- `agents/taskspending.md`",
                "",
                "## Checks",
                "- Confirm the backlog transition is being treated as audit work, not success.",
                "- Preserve a durable queue item for later audit validation/gate stages.",
                "",
                "## Findings",
                f"- Backlog-empty event observed with backlog_depth={backlog_depth}.",
                "",
                "## Evidence",
                "- Research queue discovery should report this file as actionable audit work.",
                "",
                "## Decision",
                "- Pending",
                "",
                "## Follow-ups",
                "- Run the later audit validation and gatekeeper stages.",
                "",
            ]
        ),
    )
    return load_audit_queue_record(path, expected_status=AuditLifecycleStatus.INCOMING)


__all__ = [
    "AuditExecutionError",
    "AuditGatekeeperExecutionResult",
    "AuditGatekeeperRecord",
    "AuditIntakeExecutionResult",
    "AuditIntakeRecord",
    "AuditLifecycleStatus",
    "AuditQueueRecord",
    "AuditRemediationRecord",
    "AuditSummary",
    "AuditSummaryLastOutcome",
    "AuditTrigger",
    "AuditValidateExecutionResult",
    "AuditValidateRecord",
    "ensure_backlog_empty_audit_ticket",
    "execute_audit_gatekeeper",
    "execute_audit_intake",
    "execute_audit_validate",
    "load_audit_remediation_record",
    "load_audit_summary",
    "load_audit_queue_record",
    "parse_audit_queue_record",
]
