"""Typed audit queue contracts, stage records, and queue helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal
import json
import re

from pydantic import Field, ValidationError, field_validator, model_validator

from ..baseline_assets import packaged_baseline_asset
from ..contracts import (
    AuditContract,
    AuditGateDecision,
    AuditGateDecisionCounts,
    AuditExecutionFinding,
    AuditExecutionReport,
    CompletionDecision,
    CompletionManifest,
    ContractModel,
    ObjectiveContract,
    ResearchRecoveryDecision,
    ResearchStatus,
    TaskCard,
    _normalize_datetime,
    _normalize_path,
    load_objective_contract,
)
from ..markdown import TaskStoreDocument, parse_task_store, render_task_store, write_text_atomic
from ..paths import RuntimePaths
from ..queue import load_research_recovery_latch, write_research_recovery_latch
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


def _relative_path(path: Path, *, relative_to: Path) -> str:
    try:
        return path.relative_to(relative_to).as_posix()
    except ValueError:
        return path.as_posix()


def _write_json_model(path: Path, model: ContractModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(model.model_dump_json(exclude_none=False, by_alias=True))
    write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _load_json_model(path: Path, model_cls: type[ContractModel]) -> ContractModel:
    return model_cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _resolve_path_token(path_token: Path | str, *, relative_to: Path) -> Path:
    candidate = Path(path_token)
    if candidate.is_absolute():
        return candidate
    return relative_to / candidate


def _audit_runtime_dir(paths: RuntimePaths) -> Path:
    return paths.research_runtime_dir / "audit"


def _audit_record_path(paths: RuntimePaths, *, stage: str, run_id: str) -> Path:
    return _audit_runtime_dir(paths) / stage / f"{run_id}.json"


def _audit_history_path(paths: RuntimePaths) -> Path:
    return paths.agents_dir / "audit_history.md"


def _audit_summary_path(paths: RuntimePaths) -> Path:
    return paths.agents_dir / "audit_summary.json"


def _audit_remediation_record_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return _audit_record_path(paths, stage="remediation", run_id=run_id)


def _validate_record_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return _audit_record_path(paths, stage="validate", run_id=run_id)


def _execution_report_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return _audit_record_path(paths, stage="execution", run_id=run_id)


def _audited_source_path(paths: RuntimePaths, *, run_id: str, record: AuditQueueRecord) -> str:
    intake_record_path = _audit_record_path(paths, stage="intake", run_id=run_id)
    if intake_record_path.exists():
        intake_record = AuditIntakeRecord.model_validate(
            _load_json_model(intake_record_path, AuditIntakeRecord)
        )
        return intake_record.source_path
    return _relative_path(record.source_path, relative_to=paths.root)


def _default_gate_decision_path(paths: RuntimePaths) -> Path:
    return paths.reports_dir / "audit_gate_decision.json"


def _default_completion_decision_path(paths: RuntimePaths) -> Path:
    return paths.reports_dir / "completion_decision.json"


def _default_audit_summary() -> AuditSummary:
    return AuditSummary(
        updated_at=None,
        last_outcome=AuditSummaryLastOutcome(status="none", details="none", at=None),
        counts={"total": 0, "pass": 0, "fail": 0},
    )


def _load_audit_summary(paths: RuntimePaths) -> AuditSummary:
    summary_path = _audit_summary_path(paths)
    if not summary_path.exists():
        return _default_audit_summary()
    try:
        return AuditSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError, json.JSONDecodeError):
        return _default_audit_summary()


def _audit_remediation_spec_id(audit_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "-", audit_id.strip().upper()).strip("-")
    if not token:
        token = "AUDIT"
    return f"SPEC-{token}-REMEDIATION"


def _audit_remediation_title(record: AuditQueueRecord) -> str:
    return f"Remediate failed audit {record.audit_id}"


def _render_audit_remediation_body(
    *,
    record: AuditQueueRecord,
    gate_decision: AuditGateDecision,
    remediation_record_path: str,
    execution_report_path: str,
    validate_record_path: str,
) -> str:
    reasons = list(gate_decision.reasons[:3])
    if not reasons:
        reasons = ["Audit gate failed closed; inspect the gate and execution artifacts."]
    reason_text = " ".join(reasons)
    return "\n".join(
        [
            f"- **Goal:** Repair the work audited by `{record.audit_id}` and rerun the completion gate.",
            f"- **Why:** {reason_text}",
            "- **Acceptance:** The failed audit reasons are addressed, the audit can be rerun, and the completion gate can pass cleanly.",
            f"- **Audit-ID:** {record.audit_id}",
            f"- **Audit-Scope:** {record.scope}",
            f"- **Audit-Trigger:** {record.trigger.value}",
            f"- **Audit-Gate-Decision:** `{gate_decision.gate_decision_path}`",
            f"- **Audit-Validate-Record:** `{validate_record_path}`",
            f"- **Audit-Execution-Report:** `{execution_report_path}`",
            f"- **Audit-Remediation-Record:** `{remediation_record_path}`",
        ]
    )


def _existing_remediation_task(paths: RuntimePaths, *, remediation_spec_id: str) -> TaskCard | None:
    for store_path in (paths.tasks_file, paths.backlog_file, paths.taskspending_file):
        if not store_path.exists():
            continue
        document = parse_task_store(store_path.read_text(encoding="utf-8"), source_file=store_path)
        for card in document.cards:
            if card.spec_id == remediation_spec_id:
                return card
    return None


def _append_backlog_task(
    paths: RuntimePaths,
    *,
    title: str,
    body: str,
    spec_id: str,
) -> TaskCard:
    task_date = datetime.now(timezone.utc).date().isoformat()
    card = TaskCard.model_validate(
        {
            "heading": f"## {task_date} - {title}",
            "body": f"- **Spec-ID:** {spec_id}\n\n{body.strip()}",
        }
    )
    existing = parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file)
    updated = TaskStoreDocument(preamble=existing.preamble, cards=[*existing.cards, card])
    write_text_atomic(paths.backlog_file, render_task_store(updated))
    return card


def _write_audit_summary(
    paths: RuntimePaths,
    *,
    emitted_at: datetime,
    audited_source_path: str,
    record: AuditQueueRecord,
    terminal_record: AuditQueueRecord,
    gate_decision: AuditGateDecision,
    completion_decision: CompletionDecision,
    final_status: ResearchStatus,
    remediation_record: AuditRemediationRecord | None,
) -> AuditSummary:
    summary = _load_audit_summary(paths)
    counts = dict(summary.counts)
    if final_status is ResearchStatus.AUDIT_PASS:
        counts["pass"] = counts.get("pass", 0) + 1
        counts["total"] = counts.get("total", 0) + 1
    elif final_status is ResearchStatus.AUDIT_FAIL:
        counts["fail"] = counts.get("fail", 0) + 1
        counts["total"] = counts.get("total", 0) + 1

    details = "; ".join(gate_decision.reasons[:5]) if gate_decision.reasons else "none"
    payload = AuditSummary(
        updated_at=emitted_at,
        last_outcome=AuditSummaryLastOutcome(
            status=final_status.value,
            details=details,
            at=emitted_at,
            audit_id=record.audit_id,
            title=record.title,
            scope=record.scope,
            trigger=record.trigger,
            decision=gate_decision.decision,
            reason_count=len(gate_decision.reasons),
            source_path=audited_source_path,
            terminal_path=_relative_path(terminal_record.source_path, relative_to=paths.root),
            gate_decision_path=gate_decision.gate_decision_path,
            completion_decision_path=completion_decision.completion_decision_path,
            remediation_spec_id=(
                None if remediation_record is None else remediation_record.remediation_spec_id
            ),
            remediation_task_id=(
                None if remediation_record is None else remediation_record.remediation_task_id
            ),
            remediation_record_path=(
                None
                if remediation_record is None
                else _relative_path(_audit_remediation_record_path(paths, run_id=remediation_record.run_id), relative_to=paths.root)
            ),
        ),
        counts=counts,
    )
    _write_json_model(_audit_summary_path(paths), payload)
    return payload


def _write_audit_history(
    paths: RuntimePaths,
    *,
    emitted_at: datetime,
    audited_source_path: str,
    record: AuditQueueRecord,
    terminal_record: AuditQueueRecord,
    gate_decision: AuditGateDecision,
    completion_decision: CompletionDecision,
    final_status: ResearchStatus,
    remediation_record: AuditRemediationRecord | None,
) -> None:
    history_path = _audit_history_path(paths)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    existing_entries: list[str] = []
    if history_path.exists():
        text = history_path.read_text(encoding="utf-8", errors="replace")
        existing_entries = [match.group(0).rstrip() for match in re.finditer(r"(?ms)^## .*?(?=^## |\Z)", text)]

    lines = [
        f"## {emitted_at.isoformat().replace('+00:00', 'Z')} - {final_status.value}",
        "",
        f"- Audit: `{record.audit_id}` :: {record.title}",
        f"- Scope: `{record.scope}`",
        f"- Trigger: `{record.trigger.value}`",
        f"- Decision: `{gate_decision.decision}` ({len(gate_decision.reasons)} reason(s))",
        f"- Source path: `{audited_source_path}`",
        f"- Terminal path: `{_relative_path(terminal_record.source_path, relative_to=paths.root)}`",
        f"- Gate decision: `{gate_decision.gate_decision_path}`",
        f"- Completion decision: `{completion_decision.completion_decision_path}`",
    ]
    if gate_decision.reasons:
        lines.append(f"- Details: {'; '.join(gate_decision.reasons[:5])}")
    else:
        lines.append("- Details: none")
    if remediation_record is not None:
        lines.append(
            f"- Remediation: `{remediation_record.remediation_spec_id}` -> `{remediation_record.remediation_task_id}`"
        )
        lines.append(
            f"- Remediation record: `{_relative_path(_audit_remediation_record_path(paths, run_id=remediation_record.run_id), relative_to=paths.root)}`"
        )
    else:
        lines.append("- Remediation: none")
    entry = "\n".join(lines).rstrip()
    entries = [entry, *existing_entries][: _AUDIT_HISTORY_RETENTION_KEEP]
    header = [
        "# Audit History",
        "",
        "Local audit outcomes recorded by `millrace_engine.research.audit` (newest first).",
        "",
    ]
    rendered = "\n".join(header) + "\n\n".join(entries) + ("\n" if entries else "")
    write_text_atomic(history_path, rendered)


def _persist_audit_recovery_decision(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    emitted_at: datetime,
    remediation_record: AuditRemediationRecord,
) -> bool:
    handoff = checkpoint.parent_handoff or (
        None if checkpoint.active_request is None else checkpoint.active_request.handoff
    )
    if handoff is None or handoff.recovery_batch_id is None:
        return False

    latch = load_research_recovery_latch(paths.research_recovery_latch_file)
    if latch is None or latch.batch_id != handoff.recovery_batch_id:
        return False
    if latch.handoff is not None and latch.handoff.handoff_id != handoff.handoff_id:
        return False

    decision = ResearchRecoveryDecision(
        decision_type="durable_remediation_decision",
        decided_at=emitted_at,
        remediation_spec_id=remediation_record.remediation_spec_id,
        remediation_record_path=Path(
            _relative_path(_audit_remediation_record_path(paths, run_id=remediation_record.run_id), relative_to=paths.root)
        ),
        taskaudit_record_path=None,
        task_provenance_path=None,
        lineage_path=None,
        pending_card_count=0,
        backlog_card_count=remediation_record.backlog_depth_after_enqueue,
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
    return True


def _persist_audit_remediation(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    run_id: str,
    emitted_at: datetime,
    audited_source_path: str,
    record: AuditQueueRecord,
    terminal_record: AuditQueueRecord,
    gate_decision: AuditGateDecision,
    completion_decision: CompletionDecision,
    validate_record_path: Path,
    execution_report_path: Path,
) -> AuditRemediationRecord:
    remediation_spec_id = _audit_remediation_spec_id(record.audit_id)
    remediation_path = _audit_remediation_record_path(paths, run_id=run_id)
    existing_task = _existing_remediation_task(paths, remediation_spec_id=remediation_spec_id)
    if existing_task is None:
        task_card = _append_backlog_task(
            paths,
            title=_audit_remediation_title(record),
            body=_render_audit_remediation_body(
                record=record,
                gate_decision=gate_decision,
                remediation_record_path=_relative_path(remediation_path, relative_to=paths.root),
                execution_report_path=_relative_path(execution_report_path, relative_to=paths.root),
                validate_record_path=_relative_path(validate_record_path, relative_to=paths.root),
            ),
            spec_id=remediation_spec_id,
        )
        selected_action: Literal["enqueue_backlog_task", "reuse_existing_task"] = "enqueue_backlog_task"
    else:
        task_card = existing_task
        selected_action = "reuse_existing_task"

    backlog_depth = len(parse_task_store(paths.backlog_file.read_text(encoding="utf-8")).cards)
    remediation_record = AuditRemediationRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=record.audit_id,
        title=record.title,
        scope=record.scope,
        trigger=record.trigger,
        source_path=audited_source_path,
        terminal_path=_relative_path(terminal_record.source_path, relative_to=paths.root),
        gate_decision_path=gate_decision.gate_decision_path,
        completion_decision_path=completion_decision.completion_decision_path,
        validate_record_path=_relative_path(validate_record_path, relative_to=paths.root),
        execution_report_path=_relative_path(execution_report_path, relative_to=paths.root),
        selected_action=selected_action,
        remediation_spec_id=remediation_spec_id,
        remediation_task_id=task_card.task_id,
        remediation_task_title=task_card.title,
        backlog_depth_after_enqueue=backlog_depth,
        reasons=gate_decision.reasons,
        recovery_latch_updated=False,
    )
    recovery_latch_updated = _persist_audit_recovery_decision(
        paths,
        checkpoint,
        emitted_at=emitted_at,
        remediation_record=remediation_record,
    )
    remediation_record = remediation_record.model_copy(update={"recovery_latch_updated": recovery_latch_updated})
    _write_json_model(remediation_path, remediation_record)
    return remediation_record


def load_audit_summary(paths: RuntimePaths) -> AuditSummary:
    """Load the workspace audit summary with fail-soft defaults."""

    return _load_audit_summary(paths)


def load_audit_remediation_record(
    paths: RuntimePaths,
    *,
    run_id: str,
) -> AuditRemediationRecord | None:
    """Load one audit remediation record if it exists."""

    record_path = _audit_remediation_record_path(paths, run_id=run_id)
    if not record_path.exists():
        return None
    return AuditRemediationRecord.model_validate_json(record_path.read_text(encoding="utf-8"))


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


def _load_objective_contract(
    paths: RuntimePaths,
) -> tuple[ObjectiveContract | None, str, tuple[str, ...], Path, Path]:
    contract_path = paths.objective_contract_file
    gate_decision_path = _default_gate_decision_path(paths)
    completion_decision_path = _default_completion_decision_path(paths)
    contract_ref = _relative_path(contract_path, relative_to=paths.root)
    issues: list[str] = []

    if contract_path.exists():
        raw_text = contract_path.read_text(encoding="utf-8")
    else:
        asset_path = packaged_baseline_asset("agents/objective/contract.yaml")
        raw_text = asset_path.read_text(encoding="utf-8")
        contract_ref = _PACKAGED_OBJECTIVE_CONTRACT_REF

    try:
        contract = load_objective_contract(raw_text)
    except (ValidationError, ValueError) as exc:
        issues.append(f"Objective contract is invalid: {_normalize_required_text(str(exc), field_name='objective contract')}")
        return None, contract_ref, tuple(issues), gate_decision_path, completion_decision_path

    gate_decision_path = _resolve_path_token(contract.completion.fallback_decision_file, relative_to=paths.root)
    completion_decision_path = _resolve_path_token(
        contract.completion.authoritative_decision_file,
        relative_to=paths.root,
    )
    return contract, contract_ref, tuple(issues), gate_decision_path, completion_decision_path


def _load_completion_manifest(
    paths: RuntimePaths,
) -> tuple[CompletionManifest | None, str, tuple[str, ...]]:
    manifest_path = paths.audit_completion_manifest_file
    manifest_ref = _relative_path(manifest_path, relative_to=paths.root)
    issues: list[str] = []

    if manifest_path.exists():
        raw_text = manifest_path.read_text(encoding="utf-8")
    else:
        asset_path = packaged_baseline_asset("agents/audit/completion_manifest.json")
        raw_text = asset_path.read_text(encoding="utf-8")
        manifest_ref = _PACKAGED_COMPLETION_MANIFEST_REF

    try:
        manifest = CompletionManifest.model_validate(json.loads(raw_text))
    except (json.JSONDecodeError, ValidationError) as exc:
        issues.append(
            f"Completion manifest is invalid: {_normalize_required_text(str(exc), field_name='completion manifest')}"
        )
        return None, manifest_ref, tuple(issues)
    return manifest, manifest_ref, tuple(issues)


def _dedupe_messages(messages: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in messages:
        normalized = _normalize_optional_text(item, field_name="message")
        if normalized is None:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return tuple(deduped)


def _task_store_card_count(path: Path) -> tuple[int, str | None]:
    if not path.exists():
        return 0, None
    try:
        document = parse_task_store(path.read_text(encoding="utf-8"), source_file=path)
    except ValueError as exc:
        return 0, f"Task store `{path.name}` is invalid: {_normalize_required_text(str(exc), field_name='task store')}"
    return len(document.cards), None


def _count_open_gaps(gaps_path: Path) -> tuple[int, str | None]:
    if not gaps_path.exists():
        return 0, None

    try:
        text = gaps_path.read_text(encoding="utf-8")
    except OSError as exc:
        return 0, f"Gaps file `{gaps_path.name}` could not be read: {_normalize_required_text(str(exc), field_name='gaps')}"

    actionable_open_gaps = 0
    in_open = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Open Gaps"):
            in_open = True
            continue
        if in_open and stripped.startswith("## "):
            break
        if not in_open or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if len(cells) < 7:
            continue
        gap_id = cells[0]
        status = cells[5].lower()
        if not gap_id or gap_id in {"Gap ID", "---", "GAP-000"}:
            continue
        if "<" in stripped or ">" in stripped:
            continue
        if status == "open":
            actionable_open_gaps += 1
    return actionable_open_gaps, None


def _evaluate_completion_gate(
    *,
    paths: RuntimePaths,
    run_id: str,
    emitted_at: datetime,
    record: AuditQueueRecord,
    validate_record: AuditValidateRecord,
    validate_record_path: Path,
) -> tuple[AuditGateDecision, CompletionDecision, ResearchStatus]:
    execution_report_path = _resolve_path_token(validate_record.execution_report_path, relative_to=paths.root)
    gate_checks: list[bool] = []
    reasons: list[str] = []

    objective_contract, objective_contract_ref, objective_issues, gate_decision_path, completion_decision_path = (
        _load_objective_contract(paths)
    )
    gate_checks.append(not objective_issues)
    reasons.extend(objective_issues)

    execution_report: AuditExecutionReport | None = None
    try:
        execution_report = AuditExecutionReport.model_validate(
            _load_json_model(execution_report_path, AuditExecutionReport)
        )
    except (FileNotFoundError, ValidationError, json.JSONDecodeError, ValueError) as exc:
        reasons.append(
            f"Audit execution report is unavailable: {_normalize_required_text(str(exc), field_name='execution report')}"
        )
        gate_checks.append(False)
    else:
        artifacts_aligned = (
            execution_report.run_id == run_id
            and execution_report.audit_id == record.audit_id
            and validate_record.execution_report_path == _relative_path(execution_report_path, relative_to=paths.root)
        )
        gate_checks.append(artifacts_aligned)
        if not artifacts_aligned:
            reasons.append("Audit execution and validate artifacts are not aligned for this audit run.")

    validate_passed = validate_record.recommended_decision == "pass"
    gate_checks.append(validate_passed)
    if not validate_passed:
        reasons.extend(list(validate_record.findings) or [validate_record.summary])

    completion_manifest, completion_manifest_ref, manifest_issues = _load_completion_manifest(paths)
    gate_checks.append(not manifest_issues)
    reasons.extend(manifest_issues)

    required_commands: tuple[str, ...] = ()
    if completion_manifest is not None:
        gate_checks.append(completion_manifest.configured)
        if not completion_manifest.configured:
            reasons.append("Completion manifest is not configured (`configured=false`).")

        required_commands = tuple(command.command for command in completion_manifest.required_commands())
        has_required_commands = bool(required_commands)
        gate_checks.append(has_required_commands)
        if completion_manifest.configured and not has_required_commands:
            reasons.append("Completion manifest is configured but declares no required completion commands.")
    else:
        gate_checks.extend((False, False))

    observed_commands = execution_report.observed_commands if execution_report is not None else ()
    missing_required_commands = tuple(
        command for command in required_commands if command not in observed_commands
    )
    completion_command_coverage = not missing_required_commands
    gate_checks.append(completion_command_coverage)
    if missing_required_commands:
        reasons.append(
            "Required completion command evidence is missing: "
            + "; ".join(missing_required_commands[:5])
        )

    active_task_cards, active_issue = _task_store_card_count(paths.tasks_file)
    backlog_cards, backlog_issue = _task_store_card_count(paths.backlog_file)
    pending_task_cards, pending_issue = _task_store_card_count(paths.taskspending_file)
    for issue in (active_issue, backlog_issue, pending_issue):
        if issue is not None:
            reasons.append(issue)

    task_store_cards = active_task_cards + backlog_cards + pending_task_cards
    require_task_store_cards_zero = (
        objective_contract.completion.require_task_store_cards_zero
        if objective_contract is not None
        else True
    )
    if require_task_store_cards_zero:
        gate_checks.append(task_store_cards == 0)
        if active_task_cards:
            reasons.append(f"Active task store still has {active_task_cards} task card(s).")
        if backlog_cards:
            reasons.append(f"Backlog still has {backlog_cards} task card(s).")
        if pending_task_cards:
            reasons.append(f"Pending task store still has {pending_task_cards} task card(s).")

    open_gaps, gaps_issue = _count_open_gaps(paths.agents_dir / "gaps.md")
    if gaps_issue is not None:
        reasons.append(gaps_issue)
    require_open_gaps_zero = (
        objective_contract.completion.require_open_gaps_zero
        if objective_contract is not None
        else True
    )
    if require_open_gaps_zero:
        gate_checks.append(open_gaps == 0)
        if open_gaps:
            reasons.append(f"{open_gaps} actionable open gap row(s) remain in `agents/gaps.md`.")

    deduped_reasons = _dedupe_messages(reasons)
    required_total = len(gate_checks)
    required_pass = sum(1 for item in gate_checks if item)
    counts = AuditGateDecisionCounts(
        required_total=required_total,
        required_pass=required_pass,
        required_fail=required_total - required_pass,
        required_blocked=0,
        completion_required=len(required_commands),
        completion_pass=len(required_commands) - len(missing_required_commands),
        open_gaps=open_gaps,
        task_store_cards=task_store_cards,
        active_task_cards=active_task_cards,
        backlog_cards=backlog_cards,
        pending_task_cards=pending_task_cards,
    )
    decision = "PASS" if not deduped_reasons and required_pass == required_total else "FAIL"
    final_status = ResearchStatus.AUDIT_PASS if decision == "PASS" else ResearchStatus.AUDIT_FAIL

    gate_decision = AuditGateDecision(
        run_id=run_id,
        audit_id=record.audit_id,
        generated_at=emitted_at,
        decision=decision,
        reasons=deduped_reasons,
        counts=counts,
        gate_decision_path=_relative_path(gate_decision_path, relative_to=paths.root),
        objective_contract_path=objective_contract_ref,
        completion_manifest_path=completion_manifest_ref,
        execution_report_path=_relative_path(execution_report_path, relative_to=paths.root),
        validate_record_path=_relative_path(validate_record_path, relative_to=paths.root),
    )
    completion_decision = CompletionDecision(
        run_id=run_id,
        audit_id=record.audit_id,
        generated_at=emitted_at,
        decision=decision,
        reasons=deduped_reasons,
        counts=counts,
        completion_decision_path=_relative_path(completion_decision_path, relative_to=paths.root),
        gate_decision_path=_relative_path(gate_decision_path, relative_to=paths.root),
        objective_contract_path=objective_contract_ref,
    )
    _write_json_model(gate_decision_path, gate_decision)
    _write_json_model(completion_decision_path, completion_decision)
    return gate_decision, completion_decision, final_status


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
