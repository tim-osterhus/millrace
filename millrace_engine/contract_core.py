"""Foundational shared contract primitives."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict


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
    SPEC_INTERVIEW = "spec_interview"
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
    SPEC_INTERVIEW_RUNNING = "SPEC_INTERVIEW_RUNNING"
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


class SpecInterviewPolicy(str, Enum):
    OFF = "off"
    WHEN_AMBIGUOUS = "when_ambiguous"
    ALWAYS = "always"
    MANUAL_ONLY = "manual_only"


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
