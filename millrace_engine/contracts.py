"""Shared contract types."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
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


from .contract_documents import TaskCard


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


from .contract_documents import (
    AuditContract,
    AuditExecutionFinding,
    AuditExecutionReport,
    AuditGateDecision,
    AuditGateDecisionCounts,
    BlockerEntry,
    CompletionDecision,
    CompletionManifest,
    CompletionManifestCommand,
    ObjectiveCompletionPolicy,
    ObjectiveContract,
    StageResult,
    load_objective_contract,
)

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
