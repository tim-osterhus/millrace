"""Runtime and recovery contracts re-exported by contracts.py."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .contract_compounding import ProcedureInjectionBundle
from .contract_context_facts import ContextFactInjectionBundle
from .contract_core import (
    ContractModel,
    ExecutionStatus,
    HeadlessPermissionProfile,
    ReasoningEffort,
    RunnerKind,
    StageType,
    _normalize_datetime,
    _normalize_path,
)


class StageContext(ContractModel):
    """Normalized runner input for one stage invocation."""

    stage: StageType
    runner: RunnerKind
    model: str
    prompt: str = ""
    working_dir: Path
    run_id: str | None = None
    permission_profile: HeadlessPermissionProfile = HeadlessPermissionProfile.NORMAL
    timeout_seconds: int = Field(default=3600, ge=1)
    command: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    prompt_path: Path | None = None
    status_fallback_path: Path | None = None
    allow_search: bool = False
    allow_network: bool = True
    effort: ReasoningEffort | None = None
    prompt_to_stdin: bool = False
    procedure_injection: ProcedureInjectionBundle | None = None
    context_fact_injection: ContextFactInjectionBundle | None = None
    compounding_profile: str | None = None

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

    @field_validator("procedure_injection", mode="before")
    @classmethod
    def normalize_procedure_injection(
        cls, value: ProcedureInjectionBundle | dict[str, Any] | None
    ) -> ProcedureInjectionBundle | None:
        if value is None:
            return None
        if isinstance(value, ProcedureInjectionBundle):
            return value
        return ProcedureInjectionBundle.model_validate(value)

    @field_validator("context_fact_injection", mode="before")
    @classmethod
    def normalize_context_fact_injection(
        cls, value: ContextFactInjectionBundle | dict[str, Any] | None
    ) -> ContextFactInjectionBundle | None:
        if value is None:
            return None
        if isinstance(value, ContextFactInjectionBundle):
            return value
        return ContextFactInjectionBundle.model_validate(value)

    @field_validator("compounding_profile")
    @classmethod
    def normalize_compounding_profile(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
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


__all__ = [
    "CodexUsageSummary",
    "CrossPlaneParentRun",
    "ExecutionResearchHandoff",
    "ResearchRecoveryDecision",
    "ResearchRecoveryLatch",
    "RunnerResult",
    "StageContext",
]
