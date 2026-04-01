"""Shared contract types."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Self
import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CARD_HEADING_RE = re.compile(r"^##\s*(\d{4}-\d{2}-\d{2})\s*[—-]\s*(.+?)\s*$")
FIELD_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*(.+?):\*\*\s*(.*)$")
TOKEN_RE = re.compile(r"[A-Za-z0-9._-]+")
REQUIREMENT_ID_RE = re.compile(r"\bREQ-[A-Za-z0-9._-]+\b")
ACCEPTANCE_ID_RE = re.compile(r"\bAC-[A-Za-z0-9._-]+\b")


def _normalize_sequence(values: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.strip().split())
        if normalized.startswith(("- ", "* ")):
            normalized = normalized[2:].strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return tuple(deduped)


def _normalize_tokens(values: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip().upper()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return tuple(deduped)


def _extract_field_value(body: str, field_name: str) -> str | None:
    target = field_name.casefold()
    for line in body.splitlines():
        match = FIELD_LINE_RE.match(line.strip())
        if not match:
            continue
        if match.group(1).strip().casefold() != target:
            continue
        value = match.group(2).strip()
        return value or None
    return None


def _extract_field_block_lines(body: str, field_name: str) -> tuple[str, ...]:
    target = field_name.casefold()
    lines = body.splitlines()
    collected: list[str] = []
    capture = False

    for line in lines:
        stripped = line.strip()
        field_match = FIELD_LINE_RE.match(stripped)
        if field_match:
            name = field_match.group(1).strip().casefold()
            if capture and name != target:
                break
            if name == target:
                capture = True
                remainder = field_match.group(2).strip()
                if remainder:
                    collected.append(remainder)
                continue

        if not capture:
            continue
        if not stripped:
            continue
        if FIELD_LINE_RE.match(stripped):
            break
        collected.append(stripped)

    return _normalize_sequence(collected)


def _extract_field_tokens(body: str, field_name: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for line in _extract_field_block_lines(body, field_name):
        tokens.extend(match.group(0) for match in TOKEN_RE.finditer(line))
    return _normalize_tokens(tokens)


def _normalize_integration_preference(
    value: object,
) -> Literal["force", "skip", "inherit"] | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized in {"force", "forced", "always", "on", "run", "required", "require"}:
        return "force"
    if normalized in {"skip", "suppress", "never", "off", "disable", "disabled"}:
        return "skip"
    if normalized in {"inherit", "default", "auto"}:
        return "inherit"
    raise ValueError(
        "integration preference must be one of force/run/always, skip/never/off, or inherit/default/auto"
    )


def _slugify_task_id(date_value: str | None, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-") or "task"
    if date_value:
        return f"{date_value}__{slug}"
    return slug


def _normalize_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    text = value.strip()
    if not text:
        return None
    return Path(text)


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        text = value.strip()
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class ContractModel(BaseModel):
    """Shared immutable contract model base."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ContractSurface(str, Enum):
    """Public contract scope for a vocabulary item."""

    PUBLIC_V1 = "public_v1"
    FORWARD_COMPATIBLE = "forward_compatible"


class StageType(str, Enum):
    BUILDER = "builder"
    INTEGRATION = "integration"
    QA = "qa"
    HOTFIX = "hotfix"
    DOUBLECHECK = "doublecheck"
    TROUBLESHOOT = "troubleshoot"
    CONSULT = "consult"
    UPDATE = "update"
    LARGE_PLAN = "large_plan"
    LARGE_EXECUTE = "large_execute"
    REASSESS = "reassess"
    REFACTOR = "refactor"
    GOAL_INTAKE = "goal_intake"
    OBJECTIVE_PROFILE_SYNC = "objective_profile_sync"
    SPEC_SYNTHESIS = "spec_synthesis"
    SPEC_REVIEW = "spec_review"
    TASKMASTER = "taskmaster"
    TASKAUDIT = "taskaudit"
    CLARIFY = "clarify"
    CRITIC = "critic"
    DESIGNER = "designer"
    PHASESPLIT = "phasesplit"
    INCIDENT_INTAKE = "incident_intake"
    INCIDENT_RESOLVE = "incident_resolve"
    INCIDENT_ARCHIVE = "incident_archive"
    AUDIT_INTAKE = "audit_intake"
    AUDIT_VALIDATE = "audit_validate"
    AUDIT_GATEKEEPER = "audit_gatekeeper"
    MECHANIC = "mechanic"

    @property
    def surface(self) -> ContractSurface:
        return stage_surface(self)

    @property
    def is_public_v1(self) -> bool:
        return self.surface is ContractSurface.PUBLIC_V1


class ExecutionStatus(str, Enum):
    IDLE = "IDLE"
    BUILDER_RUNNING = "BUILDER_RUNNING"
    BUILDER_COMPLETE = "BUILDER_COMPLETE"
    HOTFIX_COMPLETE = "HOTFIX_COMPLETE"
    INTEGRATION_RUNNING = "INTEGRATION_RUNNING"
    INTEGRATION_COMPLETE = "INTEGRATION_COMPLETE"
    QA_RUNNING = "QA_RUNNING"
    QA_COMPLETE = "QA_COMPLETE"
    QUICKFIX_NEEDED = "QUICKFIX_NEEDED"
    HOTFIX_RUNNING = "HOTFIX_RUNNING"
    DOUBLECHECK_RUNNING = "DOUBLECHECK_RUNNING"
    TROUBLESHOOT_RUNNING = "TROUBLESHOOT_RUNNING"
    TROUBLESHOOT_COMPLETE = "TROUBLESHOOT_COMPLETE"
    CONSULT_RUNNING = "CONSULT_RUNNING"
    CONSULT_COMPLETE = "CONSULT_COMPLETE"
    NEEDS_RESEARCH = "NEEDS_RESEARCH"
    BLOCKED = "BLOCKED"
    UPDATE_RUNNING = "UPDATE_RUNNING"
    UPDATE_COMPLETE = "UPDATE_COMPLETE"
    NET_WAIT = "NET_WAIT"
    LARGE_PLAN_COMPLETE = "LARGE_PLAN_COMPLETE"
    LARGE_EXECUTE_COMPLETE = "LARGE_EXECUTE_COMPLETE"
    LARGE_REASSESS_COMPLETE = "LARGE_REASSESS_COMPLETE"
    LARGE_REFACTOR_COMPLETE = "LARGE_REFACTOR_COMPLETE"

    @property
    def marker(self) -> str:
        return f"### {self.value}"

    @property
    def surface(self) -> ContractSurface:
        return execution_status_surface(self)

    @property
    def is_public_v1(self) -> bool:
        return self.surface is ContractSurface.PUBLIC_V1


class ResearchStatus(str, Enum):
    IDLE = "IDLE"
    BLOCKED = "BLOCKED"
    GOALSPEC_RUNNING = "GOALSPEC_RUNNING"
    INCIDENT_RUNNING = "INCIDENT_RUNNING"
    GOAL_INTAKE_RUNNING = "GOAL_INTAKE_RUNNING"
    COMPLETION_MANIFEST_RUNNING = "COMPLETION_MANIFEST_RUNNING"
    OBJECTIVE_PROFILE_SYNC_RUNNING = "OBJECTIVE_PROFILE_SYNC_RUNNING"
    SPEC_SYNTHESIS_RUNNING = "SPEC_SYNTHESIS_RUNNING"
    SPEC_REVIEW_RUNNING = "SPEC_REVIEW_RUNNING"
    CLARIFY_RUNNING = "CLARIFY_RUNNING"
    TASKMASTER_RUNNING = "TASKMASTER_RUNNING"
    TASKAUDIT_RUNNING = "TASKAUDIT_RUNNING"
    CRITIC_RUNNING = "CRITIC_RUNNING"
    DESIGNER_RUNNING = "DESIGNER_RUNNING"
    INCIDENT_INTAKE_RUNNING = "INCIDENT_INTAKE_RUNNING"
    INCIDENT_RESOLVE_RUNNING = "INCIDENT_RESOLVE_RUNNING"
    INCIDENT_ARCHIVE_RUNNING = "INCIDENT_ARCHIVE_RUNNING"
    AUDIT_INTAKE_RUNNING = "AUDIT_INTAKE_RUNNING"
    AUDIT_VALIDATE_RUNNING = "AUDIT_VALIDATE_RUNNING"
    AUDIT_RUNNING = "AUDIT_RUNNING"
    AUDIT_PASS = "AUDIT_PASS"
    AUDIT_FAIL = "AUDIT_FAIL"
    NET_WAIT = "NET_WAIT"

    @property
    def marker(self) -> str:
        return f"### {self.value}"

    @property
    def surface(self) -> ContractSurface:
        return research_status_surface(self)

    @property
    def is_public_v1(self) -> bool:
        return self.surface is ContractSurface.PUBLIC_V1


class RunnerKind(str, Enum):
    CODEX = "codex"
    CLAUDE = "claude"
    SUBPROCESS = "subprocess"


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class ResearchMode(str, Enum):
    STUB = "stub"
    AUTO = "auto"
    GOALSPEC = "goalspec"
    INCIDENT = "incident"
    AUDIT = "audit"


PUBLIC_V1_STAGE_TYPES = frozenset(
    {
        StageType.BUILDER,
        StageType.INTEGRATION,
        StageType.QA,
        StageType.HOTFIX,
        StageType.DOUBLECHECK,
        StageType.TROUBLESHOOT,
        StageType.CONSULT,
        StageType.UPDATE,
    }
)

PUBLIC_V1_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.IDLE,
        ExecutionStatus.BUILDER_RUNNING,
        ExecutionStatus.BUILDER_COMPLETE,
        ExecutionStatus.INTEGRATION_RUNNING,
        ExecutionStatus.INTEGRATION_COMPLETE,
        ExecutionStatus.QA_RUNNING,
        ExecutionStatus.QA_COMPLETE,
        ExecutionStatus.QUICKFIX_NEEDED,
        ExecutionStatus.HOTFIX_RUNNING,
        ExecutionStatus.DOUBLECHECK_RUNNING,
        ExecutionStatus.TROUBLESHOOT_RUNNING,
        ExecutionStatus.TROUBLESHOOT_COMPLETE,
        ExecutionStatus.CONSULT_RUNNING,
        ExecutionStatus.CONSULT_COMPLETE,
        ExecutionStatus.NEEDS_RESEARCH,
        ExecutionStatus.BLOCKED,
        ExecutionStatus.UPDATE_RUNNING,
        ExecutionStatus.UPDATE_COMPLETE,
    }
)

PUBLIC_V1_RESEARCH_STATUSES = frozenset(
    {
        ResearchStatus.IDLE,
        ResearchStatus.BLOCKED,
        ResearchStatus.GOALSPEC_RUNNING,
        ResearchStatus.INCIDENT_RUNNING,
        ResearchStatus.AUDIT_RUNNING,
        ResearchStatus.AUDIT_PASS,
        ResearchStatus.AUDIT_FAIL,
    }
)


def stage_surface(stage: StageType) -> ContractSurface:
    if stage in PUBLIC_V1_STAGE_TYPES:
        return ContractSurface.PUBLIC_V1
    return ContractSurface.FORWARD_COMPATIBLE


def execution_status_surface(status: ExecutionStatus) -> ContractSurface:
    if status in PUBLIC_V1_EXECUTION_STATUSES:
        return ContractSurface.PUBLIC_V1
    return ContractSurface.FORWARD_COMPATIBLE


def research_status_surface(status: ResearchStatus) -> ContractSurface:
    if status in PUBLIC_V1_RESEARCH_STATUSES:
        return ContractSurface.PUBLIC_V1
    return ContractSurface.FORWARD_COMPATIBLE


class StageContext(ContractModel):
    """Normalized runner input for one stage invocation."""

    stage: StageType
    runner: RunnerKind
    model: str
    prompt: str = ""
    working_dir: Path
    run_id: str | None = None
    timeout_seconds: int = Field(default=3600, ge=1)
    command: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    prompt_path: Path | None = None
    status_fallback_path: Path | None = None
    allow_search: bool = False
    allow_network: bool = True
    effort: ReasoningEffort | None = None
    prompt_to_stdin: bool = False

    @field_validator("working_dir", "prompt_path", "status_fallback_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("command", mode="before")
    @classmethod
    def normalize_command(cls, value: Any) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, tuple):
            return tuple(str(item) for item in value)
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        raise TypeError("command must be a list or tuple of strings")

    @field_validator("env", mode="before")
    @classmethod
    def normalize_env(cls, value: Any) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise TypeError("env must be a mapping of string keys and values")
        return {str(key): str(item) for key, item in value.items()}

    @field_validator("run_id")
    @classmethod
    def normalize_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class CodexUsageSummary(ContractModel):
    """Normalized token-usage extraction result."""

    ok: bool
    reason: str | None = None
    detail: str | None = None
    source: Path
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    loop: str | None = None
    stage: str | None = None
    model: str | None = None
    runner: str | None = None
    helper_exit: int = Field(default=0, ge=0)

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: str | Path) -> Path:
        path = _normalize_path(value)
        if path is None:
            raise ValueError("usage source may not be empty")
        return path


class RunnerResult(ContractModel):
    """Normalized outcome of one runner invocation."""

    stage: StageType
    runner: RunnerKind
    model: str
    command: tuple[str, ...]
    exit_code: int
    duration_seconds: float = Field(ge=0)
    stdout: str
    stderr: str
    detected_marker: str | None = None
    raw_marker_line: str | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    last_response_path: Path | None = None
    runner_notes_path: Path | None = None
    run_dir: Path | None = None
    started_at: datetime
    completed_at: datetime
    usage_summary: CodexUsageSummary | None = None

    @field_validator(
        "stdout_path",
        "stderr_path",
        "last_response_path",
        "runner_notes_path",
        "run_dir",
        mode="before",
    )
    @classmethod
    def normalize_result_paths(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class TaskCard(ContractModel):
    """Normalized markdown task card."""

    task_id: str
    title: str
    body: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    heading: str
    raw_markdown: str
    date: str | None = None
    source_file: Path | None = None
    spec_id: str | None = None
    complexity: str | None = None
    gates: tuple[str, ...] = ()
    integration_preference: Literal["force", "skip", "inherit"] | None = None
    depends_on: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()
    acceptance_ids: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def populate_derived_fields(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("TaskCard input must be a mapping")

        payload = dict(value)
        heading = str(payload.get("heading", "")).strip()
        body = str(payload.get("body", "")).rstrip("\n")

        if heading:
            match = CARD_HEADING_RE.match(heading)
            if match:
                payload.setdefault("date", match.group(1))
                payload.setdefault("title", match.group(2).strip())

        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("task card title may not be empty")
        payload["title"] = title

        date_value = payload.get("date")
        if not heading:
            if isinstance(date_value, str) and date_value.strip():
                payload["heading"] = f"## {date_value.strip()} - {title}"
            else:
                raise ValueError("task card heading or date is required")

        spec_id = payload.get("spec_id") or _extract_field_value(body, "Spec-ID")
        complexity = (
            payload.get("complexity")
            or _extract_field_value(body, "Complexity")
            or _extract_field_value(body, "Effort")
        )
        task_id = payload.get("task_id") or _extract_field_value(body, "Task-ID")
        if task_id is None:
            task_id = _slugify_task_id(payload.get("date"), title)

        payload["task_id"] = str(task_id).strip()
        payload["body"] = body
        payload["spec_id"] = spec_id.strip() if isinstance(spec_id, str) and spec_id.strip() else spec_id
        payload["complexity"] = (
            complexity.strip() if isinstance(complexity, str) and complexity.strip() else complexity
        )
        metadata = dict(payload.get("metadata") or {})
        payload["metadata"] = metadata
        payload["gates"] = _normalize_tokens(
            list(payload.get("gates") or metadata.get("gates") or _extract_field_tokens(body, "Gates"))
        )
        payload["integration_preference"] = _normalize_integration_preference(
            payload.get("integration_preference")
            or metadata.get("integration")
            or _extract_field_value(body, "Integration")
        )
        payload["depends_on"] = _normalize_sequence(
            list(payload.get("depends_on") or _extract_field_block_lines(body, "Dependencies"))
        )
        payload["blocks"] = _normalize_sequence(
            list(
                payload.get("blocks")
                or _extract_field_block_lines(body, "Blocks")
                or _extract_field_block_lines(body, "Enables")
            )
        )
        payload["provides"] = _normalize_sequence(
            list(payload.get("provides") or _extract_field_block_lines(body, "Provides"))
        )
        payload["requirement_ids"] = _normalize_sequence(
            list(payload.get("requirement_ids") or REQUIREMENT_ID_RE.findall(body))
        )
        payload["acceptance_ids"] = _normalize_sequence(
            list(payload.get("acceptance_ids") or ACCEPTANCE_ID_RE.findall(body))
        )

        raw_markdown = payload.get("raw_markdown")
        if raw_markdown is None:
            payload["raw_markdown"] = cls.render_from_parts(payload["heading"], body)

        return payload

    @field_validator("source_file", mode="before")
    @classmethod
    def normalize_source_file(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("gates", mode="before")
    @classmethod
    def normalize_gates(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_tokens(TOKEN_RE.findall(value))
        return _normalize_tokens([str(item) for item in value])

    @field_validator("integration_preference", mode="before")
    @classmethod
    def normalize_integration_preference_field(
        cls,
        value: object,
    ) -> Literal["force", "skip", "inherit"] | None:
        return _normalize_integration_preference(value)

    @classmethod
    def render_from_parts(cls, heading: str, body: str) -> str:
        cleaned_heading = heading.rstrip()
        cleaned_body = body.rstrip("\n")
        if cleaned_body:
            return f"{cleaned_heading}\n{cleaned_body}"
        return cleaned_heading

    @classmethod
    def from_markdown(cls, raw_markdown: str, *, source_file: Path | None = None) -> Self:
        lines = raw_markdown.rstrip("\n").splitlines()
        if not lines:
            raise ValueError("task card markdown may not be empty")
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).rstrip("\n")
        return cls.model_validate(
            {
                "heading": heading,
                "body": body,
                "raw_markdown": raw_markdown.rstrip("\n"),
                "source_file": source_file,
            }
        )

    def render_markdown(self) -> str:
        return self.raw_markdown.rstrip("\n")


class CrossPlaneParentRun(ContractModel):
    """Parent-run provenance carried across one cross-plane handoff."""

    plane: Literal["execution", "research"]
    run_id: str
    snapshot_id: str | None = None
    frozen_plan_id: str | None = None
    frozen_plan_hash: str | None = None
    transition_history_path: Path | None = None

    @field_validator(
        "run_id",
        "snapshot_id",
        "frozen_plan_id",
        "frozen_plan_hash",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "value")
        if value is None:
            if field_name == "run_id":
                raise ValueError("run_id may not be empty")
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("transition_history_path", mode="before")
    @classmethod
    def normalize_transition_history_path(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)


class ExecutionResearchHandoff(ContractModel):
    """Explicit execution-to-research handoff contract."""

    handoff_id: str
    source_plane: Literal["execution", "research"] = "execution"
    target_plane: Literal["execution", "research"] = "research"
    trigger_event: Literal["handoff.needs_research"] = "handoff.needs_research"
    queue_family: Literal["blocker"] = "blocker"
    parent_run: CrossPlaneParentRun | None = None
    task_id: str
    task_title: str
    status: ExecutionStatus = ExecutionStatus.NEEDS_RESEARCH
    stage: str
    reason: str
    incident_path: Path | None = None
    diagnostics_dir: Path | None = None
    run_dir: Path | None = None
    recovery_batch_id: str | None = None
    failure_signature: str | None = None
    frozen_backlog_cards: int = Field(default=0, ge=0)
    retained_backlog_cards: int = Field(default=0, ge=0)

    @field_validator(
        "handoff_id",
        "task_id",
        "task_title",
        "stage",
        "reason",
        "recovery_batch_id",
        "failure_signature",
    )
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "value")
        if value is None:
            if field_name in {"recovery_batch_id", "failure_signature"}:
                return None
            raise ValueError(f"{field_name} may not be empty")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("incident_path", "diagnostics_dir", "run_dir", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @model_validator(mode="after")
    def validate_handoff(self) -> "ExecutionResearchHandoff":
        if self.source_plane == self.target_plane:
            raise ValueError("cross-plane handoff requires distinct source and target planes")
        if self.status is not ExecutionStatus.NEEDS_RESEARCH:
            raise ValueError("execution handoff status must be NEEDS_RESEARCH")
        if self.parent_run is not None and self.parent_run.plane != self.source_plane:
            raise ValueError("parent_run plane must match source_plane")
        return self


class ResearchRecoveryDecision(ContractModel):
    """Durable research-side decision that authorizes one frozen-batch thaw."""

    decision_type: Literal["regenerated_backlog_work", "durable_remediation_decision"]
    decided_at: datetime
    remediation_spec_id: str
    remediation_record_path: Path
    taskaudit_record_path: Path | None = None
    task_provenance_path: Path | None = None
    lineage_path: Path | None = None
    pending_card_count: int = Field(default=0, ge=0)
    backlog_card_count: int = Field(default=0, ge=0)

    @field_validator("decided_at", mode="before")
    @classmethod
    def normalize_decided_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("remediation_spec_id")
    @classmethod
    def validate_remediation_spec_id(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("remediation_spec_id may not be empty")
        return normalized

    @field_validator(
        "remediation_record_path",
        "taskaudit_record_path",
        "task_provenance_path",
        "lineage_path",
        mode="before",
    )
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)


class ResearchRecoveryLatch(ContractModel):
    """Frozen execution-batch recovery latch."""

    state: str = "frozen"
    batch_id: str
    frozen_at: datetime
    run_dir: Path | None = None
    diag_dir: Path | None = None
    fingerprint: str | None = None
    failure_signature: str
    incident_path: Path | None = None
    stage: str
    reason: str
    frozen_backlog_cards: int = Field(ge=0)
    retained_backlog_cards: int = Field(ge=0)
    quarantine_mode_requested: str = "full"
    quarantine_mode_applied: str = "full"
    quarantine_reason: str
    missing_metadata_quarantined: int = Field(default=0, ge=0)
    handoff: ExecutionResearchHandoff | None = None
    remediation_decision: ResearchRecoveryDecision | None = None

    @field_validator("frozen_at", mode="before")
    @classmethod
    def normalize_frozen_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("run_dir", "diag_dir", "incident_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def normalize_fingerprint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class AuditContract(ContractModel):
    """Strict audit command-contract policy loaded from packaged/workspace JSON."""

    schema_version: Literal["1.0"] = "1.0"
    contract_id: str
    enabled: bool = True
    description: str | None = None
    required_command_substrings: tuple[str, ...] = ()
    forbidden_command_markers: tuple[str, ...] = ()
    required_summaries: tuple[str, ...] = ()

    @field_validator("contract_id")
    @classmethod
    def validate_contract_id(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("contract_id may not be empty")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @field_validator(
        "required_command_substrings",
        "forbidden_command_markers",
        "required_summaries",
        mode="before",
    )
    @classmethod
    def normalize_text_sequences(
        cls,
        value: tuple[str, ...] | list[str] | str | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])


class AuditExecutionFinding(ContractModel):
    """One deterministic command-contract finding captured during audit validate."""

    kind: Literal[
        "missing_required_command_substring",
        "forbidden_command_marker",
        "missing_required_summary",
    ]
    expected: str
    message: str
    observed: tuple[str, ...] = ()

    @field_validator("expected", "message")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("observed", mode="before")
    @classmethod
    def normalize_observed(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])


class AuditExecutionReport(ContractModel):
    """Durable command-contract evidence persisted by audit validate."""

    schema_version: Literal["1.0"] = "1.0"
    artifact_type: Literal["audit_execution_report"] = "audit_execution_report"
    run_id: str
    emitted_at: datetime
    audit_id: str
    working_path: str
    contract_id: str
    strict_contract_path: str
    strict_contract_enabled: bool = True
    observed_commands: tuple[str, ...] = ()
    observed_summaries: tuple[str, ...] = ()
    command_count: int = Field(default=0, ge=0)
    summary_count: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    findings: tuple[AuditExecutionFinding, ...] = ()
    passed: bool = True

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("run_id", "audit_id", "working_path", "contract_id", "strict_contract_path")
    @classmethod
    def validate_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("observed_commands", "observed_summaries", mode="before")
    @classmethod
    def normalize_sequences(
        cls,
        value: tuple[str, ...] | list[str] | str | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])

    @field_validator("findings", mode="before")
    @classmethod
    def normalize_findings(
        cls,
        value: tuple[AuditExecutionFinding, ...] | list[AuditExecutionFinding | dict[str, Any]] | None,
    ) -> tuple[AuditExecutionFinding, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, AuditExecutionFinding) else AuditExecutionFinding.model_validate(item)
            for item in value
        )

    @model_validator(mode="after")
    def validate_counts(self) -> "AuditExecutionReport":
        if self.command_count != len(self.observed_commands):
            raise ValueError("command_count must match observed_commands")
        if self.summary_count != len(self.observed_summaries):
            raise ValueError("summary_count must match observed_summaries")
        if self.finding_count != len(self.findings):
            raise ValueError("finding_count must match findings")
        if self.passed != (self.finding_count == 0):
            raise ValueError("passed must align with finding_count")
        return self


class CompletionManifestCommand(ContractModel):
    """One required completion command declared by the completion manifest."""

    id: str
    required: bool = True
    category: str
    timeout_secs: int = Field(gt=0)
    command: str

    @field_validator("id", "category", "command")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized


class CompletionManifest(ContractModel):
    """Typed completion-manifest contract loaded from packaged/workspace JSON."""

    schema_version: Literal["1.0"] = "1.0"
    profile_id: str
    configured: bool = False
    notes: tuple[str, ...] = ()
    required_completion_commands: tuple[CompletionManifestCommand, ...] = ()

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("profile_id may not be empty")
        return normalized

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])

    @field_validator("required_completion_commands", mode="before")
    @classmethod
    def normalize_required_completion_commands(
        cls,
        value: tuple[CompletionManifestCommand, ...]
        | list[CompletionManifestCommand | dict[str, Any]]
        | None,
    ) -> tuple[CompletionManifestCommand, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, CompletionManifestCommand) else CompletionManifestCommand.model_validate(item)
            for item in value
        )

    @model_validator(mode="after")
    def validate_required_commands(self) -> "CompletionManifest":
        required_commands = tuple(command for command in self.required_completion_commands if command.required)
        if self.configured and not required_commands:
            raise ValueError("configured completion manifest requires at least one required completion command")
        command_ids = [command.id for command in self.required_completion_commands]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("completion manifest command ids must be unique")
        return self

    def required_commands(self) -> tuple[CompletionManifestCommand, ...]:
        return tuple(command for command in self.required_completion_commands if command.required)


class ObjectiveCompletionPolicy(ContractModel):
    """Completion-specific policy block from the objective contract."""

    authoritative_decision_file: Path
    fallback_decision_file: Path
    require_task_store_cards_zero: bool = True
    require_open_gaps_zero: bool = True

    @field_validator("authoritative_decision_file", "fallback_decision_file", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path) -> Path:
        path = _normalize_path(value)
        if path is None:
            raise ValueError("decision path may not be empty")
        return path


class ObjectiveContract(ContractModel):
    """Typed objective-contract surface for completion gate enforcement."""

    schema_version: Literal["1.0"] = "1.0"
    objective_id: str
    objective_root: str
    completion: ObjectiveCompletionPolicy
    seed_state: dict[str, Any] = Field(default_factory=dict)
    gate_integrity: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    objective_profile: dict[str, Any] = Field(default_factory=dict)

    @field_validator("objective_id", "objective_root")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized


_DEFAULT_OBJECTIVE_COMPLETION_POLICY = {
    "authoritative_decision_file": "agents/reports/completion_decision.json",
    "fallback_decision_file": "agents/reports/audit_gate_decision.json",
    "require_task_store_cards_zero": True,
    "require_open_gaps_zero": True,
}


def _parse_legacy_objective_contract_text(raw_text: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if ":" not in raw_line:
            raise ValueError(f"legacy objective contract line {line_number} is missing ':'")
        key, value = raw_line.split(":", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError(f"legacy objective contract line {line_number} has an empty key")
        normalized_value = value.strip()
        if normalized_value[:1] in {"'", '"'} and normalized_value[-1:] == normalized_value[:1]:
            normalized_value = normalized_value[1:-1]
        payload[normalized_key] = normalized_value
    return payload


def _legacy_objective_contract_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    objective_id = str(payload.get("objective_id") or payload.get("goal_id") or payload.get("profile_id") or "").strip()
    if not objective_id:
        return None

    completion_payload = payload.get("completion")
    if isinstance(completion_payload, dict):
        normalized_completion = dict(completion_payload)
    else:
        normalized_completion = {}
    for key, value in _DEFAULT_OBJECTIVE_COMPLETION_POLICY.items():
        normalized_completion.setdefault(key, value)

    objective_profile = payload.get("objective_profile")
    if isinstance(objective_profile, dict):
        normalized_profile = dict(objective_profile)
    else:
        normalized_profile = {}
    for key in (
        "profile_id",
        "goal_id",
        "title",
        "source_path",
        "updated_at",
        "profile_path",
        "profile_markdown_path",
        "research_brief_path",
        "report_path",
        "goal_intake_record_path",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            normalized_profile.setdefault(key, value)

    seed_state = payload.get("seed_state")
    if isinstance(seed_state, dict):
        normalized_seed_state = dict(seed_state)
    else:
        normalized_seed_state = {}
    source_path = payload.get("source_path")
    if source_path not in (None, ""):
        normalized_seed_state.setdefault("source_path", source_path)

    gate_integrity = payload.get("gate_integrity")
    normalized_gate_integrity = dict(gate_integrity) if isinstance(gate_integrity, dict) else {}

    artifacts = payload.get("artifacts")
    normalized_artifacts = dict(artifacts) if isinstance(artifacts, dict) else {}

    return {
        "schema_version": str(payload.get("schema_version") or "1.0").strip() or "1.0",
        "objective_id": objective_id,
        "objective_root": str(payload.get("objective_root") or ".").strip() or ".",
        "completion": normalized_completion,
        "seed_state": normalized_seed_state,
        "gate_integrity": normalized_gate_integrity,
        "artifacts": normalized_artifacts,
        "objective_profile": normalized_profile,
    }


def load_objective_contract(raw_text: str) -> ObjectiveContract:
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("objective contract may not be empty")

    parsed_as_legacy_text = False
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = _parse_legacy_objective_contract_text(stripped)
        parsed_as_legacy_text = True

    if not isinstance(payload, dict):
        raise ValueError("objective contract must contain an object")

    if not parsed_as_legacy_text:
        return ObjectiveContract.model_validate(payload)

    legacy_payload = _legacy_objective_contract_payload(payload)
    if legacy_payload is None:
        return ObjectiveContract.model_validate(payload)
    return ObjectiveContract.model_validate(legacy_payload)


class AuditGateDecisionCounts(ContractModel):
    """Deterministic counts persisted with gate and completion decisions."""

    required_total: int = Field(default=0, ge=0)
    required_pass: int = Field(default=0, ge=0)
    required_fail: int = Field(default=0, ge=0)
    required_blocked: int = Field(default=0, ge=0)
    completion_required: int = Field(default=0, ge=0)
    completion_pass: int = Field(default=0, ge=0)
    open_gaps: int = Field(default=0, ge=0)
    task_store_cards: int = Field(default=0, ge=0)
    active_task_cards: int = Field(default=0, ge=0)
    backlog_cards: int = Field(default=0, ge=0)
    pending_task_cards: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "AuditGateDecisionCounts":
        if self.required_pass + self.required_fail + self.required_blocked != self.required_total:
            raise ValueError("required counts must sum to required_total")
        if self.completion_pass > self.completion_required:
            raise ValueError("completion_pass may not exceed completion_required")
        if self.active_task_cards + self.backlog_cards + self.pending_task_cards != self.task_store_cards:
            raise ValueError("task-store breakdown must sum to task_store_cards")
        return self


class AuditGateDecision(ContractModel):
    """Operator-facing fallback gate decision persisted by the audit gatekeeper."""

    schema_version: Literal["1.0"] = "1.0"
    artifact_type: Literal["audit_gate_decision_report"] = "audit_gate_decision_report"
    run_id: str
    audit_id: str
    generated_at: datetime
    decision: Literal["PASS", "FAIL"]
    reasons: tuple[str, ...] = ()
    counts: AuditGateDecisionCounts
    gate_decision_path: str
    objective_contract_path: str
    completion_manifest_path: str
    execution_report_path: str
    validate_record_path: str

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "audit_id",
        "gate_decision_path",
        "objective_contract_path",
        "completion_manifest_path",
        "execution_report_path",
        "validate_record_path",
    )
    @classmethod
    def validate_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("reasons", mode="before")
    @classmethod
    def normalize_reasons(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])

    @model_validator(mode="after")
    def validate_decision(self) -> "AuditGateDecision":
        if self.decision == "PASS" and self.reasons:
            raise ValueError("PASS gate decisions may not include reasons")
        if self.decision == "FAIL" and not self.reasons:
            raise ValueError("FAIL gate decisions require at least one reason")
        return self


class CompletionDecision(ContractModel):
    """Authoritative completion decision persisted by the audit gatekeeper."""

    schema_version: Literal["1.0"] = "1.0"
    artifact_type: Literal["completion_decision"] = "completion_decision"
    run_id: str
    audit_id: str
    generated_at: datetime
    decision: Literal["PASS", "FAIL"]
    reasons: tuple[str, ...] = ()
    counts: AuditGateDecisionCounts
    completion_decision_path: str
    gate_decision_path: str
    objective_contract_path: str

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "audit_id",
        "completion_decision_path",
        "gate_decision_path",
        "objective_contract_path",
    )
    @classmethod
    def validate_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("reasons", mode="before")
    @classmethod
    def normalize_reasons(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])

    @model_validator(mode="after")
    def validate_decision(self) -> "CompletionDecision":
        if self.decision == "PASS" and self.reasons:
            raise ValueError("PASS completion decisions may not include reasons")
        if self.decision == "FAIL" and not self.reasons:
            raise ValueError("FAIL completion decisions require at least one reason")
        return self


class BlockerEntry(ContractModel):
    """Human-readable blocker ledger entry."""

    occurred_at: datetime
    task_title: str
    status: ExecutionStatus
    stage_blocked: str
    source_task: str
    prompt_artifact: Path | None = None
    run_dir: Path | None = None
    diagnostics_dir: Path | None = None
    root_cause_summary: str
    next_action: str
    incident_path: Path | None = None
    notes: str | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def normalize_occurred_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("prompt_artifact", "run_dir", "diagnostics_dir", "incident_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: ExecutionStatus) -> ExecutionStatus:
        allowed = {
            ExecutionStatus.BLOCKED,
            ExecutionStatus.CONSULT_COMPLETE,
            ExecutionStatus.NEEDS_RESEARCH,
        }
        if value not in allowed:
            raise ValueError(f"blocker entries do not support {value.value}")
        return value

    def render_markdown(self) -> str:
        timestamp = self.occurred_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            f"## {timestamp} — {self.task_title}",
            "",
            f"- **Status:** `{self.status.marker}`",
            f"- **Stage blocked:** {self.stage_blocked}",
            f"- **Source task card:** {self.source_task}",
            f"- **Prompt artifact:** {self.prompt_artifact or 'n/a'}",
            "- **Evidence:**",
            f"  - Runs: `{self.run_dir or 'n/a'}`",
            f"  - Diagnostics: `{self.diagnostics_dir or 'n/a'}`",
            "  - Quickfix/expectations: n/a",
            f"- **Root-cause summary:** {self.root_cause_summary}",
            f"- **Deterministic next action:** {self.next_action}",
            f"- **Incident intake:** `{self.incident_path}`" if self.incident_path else "- **Incident intake:** n/a",
            f"- **Notes:** {self.notes or 'n/a'}",
        ]
        return "\n".join(lines).rstrip("\n")


class StageResult(ContractModel):
    """Canonical public stage-result contract with an optional richer runner artifact."""

    stage: StageType
    status: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = Field(default=0, ge=0)
    runner_used: str | None = None
    model_used: str | None = None
    artifacts: tuple[Path, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    runner_result: RunnerResult | None = None

    @model_validator(mode="before")
    @classmethod
    def populate_public_fields_from_runner_result(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("StageResult input must be a mapping")

        payload = dict(value)
        runner_result = payload.get("runner_result")
        if runner_result is None:
            payload.setdefault("metadata", dict(payload.get("metadata") or {}))
            return payload

        if not isinstance(runner_result, RunnerResult):
            runner_result = RunnerResult.model_validate(runner_result)
            payload["runner_result"] = runner_result

        payload.setdefault("exit_code", runner_result.exit_code)
        payload.setdefault("stdout", runner_result.stdout)
        payload.setdefault("stderr", runner_result.stderr)
        payload.setdefault("duration_seconds", runner_result.duration_seconds)
        payload.setdefault("runner_used", runner_result.runner.value)
        payload.setdefault("model_used", runner_result.model)

        artifact_candidates = payload.get("artifacts")
        if not artifact_candidates:
            artifact_candidates = [
                runner_result.stdout_path,
                runner_result.stderr_path,
                runner_result.last_response_path,
                runner_result.runner_notes_path,
            ]
        deduped_artifacts: list[Path] = []
        seen_artifacts: set[Path] = set()
        for candidate in artifact_candidates:
            path = _normalize_path(candidate)
            if path is None or path in seen_artifacts:
                continue
            seen_artifacts.add(path)
            deduped_artifacts.append(path)
        payload["artifacts"] = tuple(deduped_artifacts)

        default_metadata: dict[str, Any] = {
            "command": list(runner_result.command),
            "detected_marker": runner_result.detected_marker,
            "raw_marker_line": runner_result.raw_marker_line,
            "run_dir": runner_result.run_dir,
            "started_at": runner_result.started_at,
            "completed_at": runner_result.completed_at,
        }
        if runner_result.usage_summary is not None:
            default_metadata["usage_summary"] = runner_result.usage_summary.model_dump(mode="json")
        metadata = dict(default_metadata)
        metadata.update(dict(payload.get("metadata") or {}))
        payload["metadata"] = metadata
        return payload

    @field_validator("artifacts", mode="before")
    @classmethod
    def normalize_artifacts(
        cls,
        value: tuple[Path, ...] | list[Path | str] | None,
    ) -> tuple[Path, ...]:
        if not value:
            return ()
        artifacts: list[Path] = []
        seen: set[Path] = set()
        for item in value:
            path = _normalize_path(item)
            if path is None or path in seen:
                continue
            seen.add(path)
            artifacts.append(path)
        return tuple(artifacts)

# Phase 01B loop-architecture contracts live in a dedicated module but remain
# re-exported here to preserve the existing shared-contract import surface.
from .loop_architecture import (
    AcceptanceProfile,
    ArtifactMultiplicity,
    ArtifactPersistence,
    CardCountRange,
    ControlPlane,
    EdgeAlwaysCondition,
    EdgeArtifactPresentCondition,
    EdgeFactEqualsCondition,
    GateStrictness,
    LoopArchitectureCatalog,
    LoopConfigDefinition,
    LoopConfigPayload,
    LoopEdge,
    LoopEdgeCondition,
    LoopEdgeKind,
    LoopStageNode,
    LoopStageNodeOverrides,
    LoopTerminalClass,
    LoopTerminalState,
    ModeCompositionRules,
    ModeDefinition,
    ModePayload,
    ModePolicyToggles,
    ModelBinding,
    ModelProfileDefinition,
    ModelProfilePayload,
    OutlineMode,
    OutlinePolicy,
    PersistedArchitectureObject,
    PersistedObjectKind,
    PersistedObjectStatus,
    RegistryObjectRef,
    RegistryObjectSource,
    RegistrySourceKind,
    RegistryTier,
    RegisteredStageKindDefinition,
    RegisteredStageKindPayload,
    ResearchAssumption,
    ResearchParticipationMode,
    ScopedModelBinding,
    StageArtifactBinding,
    StageArtifactInput,
    StageArtifactOutput,
    StageIdempotencePolicy,
    StageKindModelBinding,
    StageOverrideField,
    StageResultArtifact,
    StageRetryPolicy,
    StructuredStageResult,
    StructuredStageResultMetadata,
    TaskAuthoringProfileDefinition,
    TaskAuthoringProfilePayload,
    TaskBreadth,
    TaskDecompositionStyle,
)
