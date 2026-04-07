"""Control-plane report and result models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from .compiler import CompileTimeResolvedSnapshot
from .config import ConfigApplyBoundary, ConfigSourceInfo, EngineConfig
from .contract_compounding import (
    ConsideredProcedure,
    InjectedProcedure,
    ProcedureInjectionBundle,
    ProcedureLifecycleState,
    ProcedureScope,
)
from .contract_context_facts import (
    ConsideredContextFact,
    ContextFactArtifact,
    ContextFactInjectionBundle,
    ContextFactLifecycleState,
    ContextFactScope,
    InjectedContextFact,
)
from .contract_harness import (
    HarnessBenchmarkCostSummary,
    HarnessBenchmarkOutcome,
    HarnessBenchmarkOutcomeSummary,
    HarnessBenchmarkStatus,
    HarnessCandidateState,
    HarnessChangedSurface,
    HarnessRecommendationDisposition,
)
from .contracts import AuditGateDecision, CompletionDecision, ContractModel, ExecutionStatus, ResearchMode, ResearchStatus
from .diagnostics import DiagnosticsPolicyEvidenceSnapshot
from .events import EventRecord
from .health import HealthCheckStatus, WorkspaceHealthSummary
from .policies import ExecutionIntegrationContext, SizeClassificationView
from .provenance import RuntimeTransitionRecord, routing_modes_from_records
from .research.audit import AuditRemediationRecord, AuditSummary
from .research.governance import ResearchGovernanceReport
from .research.interview import InterviewDecisionRecord, InterviewQuestionRecord
from .research.queues import ResearchQueueItem
from .research.state import ResearchQueueFamily, ResearchQueueOwnership, ResearchRuntimeState
from .standard_runtime import RuntimeSelectionView
from .status import ControlPlane
from .control_common import normalize_datetime


def _selection_view_ref(view: object | None) -> object | None:
    if view is None:
        return None
    return getattr(view, "ref", None)


def _normalized_count_map(values: dict[str, int]) -> dict[str, int]:
    return {key: values[key] for key in sorted(values)}


class RuntimeState(ContractModel):
    """Persisted runtime snapshot for daemon visibility."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    process_running: bool
    paused: bool
    pause_reason: str | None = None
    pause_run_id: str | None = None
    execution_status: ExecutionStatus
    research_status: ResearchStatus
    active_task_id: str | None = None
    backlog_depth: int = Field(ge=0)
    deferred_queue_size: int = Field(ge=0)
    uptime_seconds: float | None = Field(default=None, ge=0)
    config_hash: str
    asset_bundle_version: str | None = None
    pending_config_hash: str | None = None
    previous_config_hash: str | None = None
    pending_config_boundary: ConfigApplyBoundary | None = None
    pending_config_fields: tuple[str, ...] = ()
    rollback_armed: bool = False
    started_at: datetime | None = None
    updated_at: datetime
    mode: Literal["once", "daemon"] = "once"

    @field_validator("started_at", "updated_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | str | None) -> datetime | None:
        return normalize_datetime(value)

    @field_validator("pause_reason", "pause_run_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class AssetResolutionView(ContractModel):
    """Operator-facing resolved asset payload."""

    requested_path: Path
    workspace_path: Path
    relative_path: str | None = None
    source_kind: Literal["workspace", "package"]
    resolved_ref: str
    family: str | None = None
    category: str | None = None
    bundle_version: str | None = None

    @field_validator("requested_path", "workspace_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path) -> Path:
        return Path(value)


class AssetFamilyEntryView(ContractModel):
    """One visible asset from an overlay-capable family."""

    family: str
    relative_path: str
    source_kind: Literal["workspace", "package"]
    workspace_path: Path
    resolved_ref: str
    category: str | None = None
    bundle_version: str | None = None

    @field_validator("workspace_path", mode="before")
    @classmethod
    def normalize_workspace_path(cls, value: str | Path) -> Path:
        return Path(value)


class AssetInventoryView(ContractModel):
    """Resolved asset inventory for status/config reporting."""

    bundle_version: str
    stage_prompts: dict[str, AssetResolutionView] = Field(default_factory=dict)
    roles: tuple[AssetFamilyEntryView, ...] = ()
    skills: tuple[AssetFamilyEntryView, ...] = ()


class QueueItemView(ContractModel):
    """CLI-safe task-card summary."""

    task_id: str
    title: str
    spec_id: str | None = None


class QueueSnapshot(ContractModel):
    """Visible queue summary."""

    active_task: QueueItemView | None = None
    backlog_depth: int = Field(ge=0)
    next_task: QueueItemView | None = None
    backlog: tuple[QueueItemView, ...] = ()


class ResearchQueueFamilyView(ContractModel):
    """Operator-facing summary for one research queue family."""

    family: ResearchQueueFamily
    ready: bool
    item_count: int = Field(ge=0)
    queue_owner: ControlPlane | None = None
    queue_paths: tuple[Path, ...] = ()
    contract_paths: tuple[Path, ...] = ()
    first_item: ResearchQueueItem | None = None
    ownerships: tuple[ResearchQueueOwnership, ...] = ()

    @field_validator("queue_paths", "contract_paths", mode="before")
    @classmethod
    def normalize_path_tuple(
        cls,
        value: tuple[Path, ...] | list[Path] | tuple[str, ...] | list[str] | None,
    ) -> tuple[Path, ...]:
        if not value:
            return ()
        return tuple(Path(item) for item in value)


class CompletionStateView(ContractModel):
    """Operator-facing completion-marker gate state."""

    marker_path: Path
    marker_present: bool
    completion_allowed: bool
    marker_honored: bool
    latest_decision: Literal["PASS", "FAIL"] | None = None
    reason: Literal["allowed", "marker_missing", "audit_pass_missing", "audit_not_passed"]

    @field_validator("marker_path", mode="before")
    @classmethod
    def normalize_marker_path(cls, value: str | Path) -> Path:
        return Path(value)


class ResearchReport(ContractModel):
    """Deterministic operator-facing research runtime report."""

    config_path: Path
    source_kind: Literal["snapshot", "live"]
    configured_mode: ResearchMode
    configured_idle_mode: Literal["watch", "poll"]
    status: ResearchStatus
    runtime: ResearchRuntimeState
    queue_families: tuple[ResearchQueueFamilyView, ...] = ()
    deferred_breadcrumb_count: int = Field(ge=0)
    audit_history_path: Path
    audit_summary_path: Path
    audit_summary: AuditSummary | None = None
    latest_gate_decision: AuditGateDecision | None = None
    latest_completion_decision: CompletionDecision | None = None
    latest_audit_remediation: AuditRemediationRecord | None = None
    governance: ResearchGovernanceReport | None = None
    completion_state: CompletionStateView

    @field_validator("config_path", "audit_history_path", "audit_summary_path", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path) -> Path:
        return Path(value)


class InterviewQuestionSummary(ContractModel):
    """Compact operator-facing summary for one persisted interview question."""

    question_id: str
    status: str
    spec_id: str
    idea_id: str = ""
    title: str
    question: str
    why_this_matters: str
    recommended_answer: str
    answer_source: str
    blocking: bool
    source_path: str
    updated_at: datetime

    @field_validator(
        "question_id",
        "status",
        "spec_id",
        "idea_id",
        "title",
        "question",
        "why_this_matters",
        "recommended_answer",
        "answer_source",
        "source_path",
    )
    @classmethod
    def normalize_text(cls, value: str, info: object) -> str:
        normalized = " ".join(value.strip().split())
        if normalized or getattr(info, "field_name", "") == "idea_id":
            return normalized
        raise ValueError("interview summary text may not be empty")

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return normalize_datetime(value)


class InterviewListReport(ContractModel):
    """Deterministic payload for `millrace interview list`."""

    config_path: Path
    questions: tuple[InterviewQuestionSummary, ...] = ()

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class InterviewQuestionReport(ContractModel):
    """Detailed payload for `millrace interview show`."""

    config_path: Path
    question_path: Path
    question: InterviewQuestionRecord
    decision_path: Path | None = None
    decision: InterviewDecisionRecord | None = None

    @field_validator("config_path", "question_path", "decision_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value)


class InterviewMutationReport(ContractModel):
    """Detailed payload for interview create/answer/accept/skip operations."""

    config_path: Path
    action: str
    question_path: Path
    question: InterviewQuestionRecord
    decision_path: Path | None = None
    decision: InterviewDecisionRecord | None = None

    @field_validator("config_path", "question_path", "decision_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value)


class SelectionExplanationView(ContractModel):
    """Operator-facing summary of why one execution route/profile was selected."""

    selected_size: Literal["SMALL", "LARGE"]
    route_decision: str
    route_reason: str
    large_profile_decision: str
    large_profile_reason: str | None = None

    @field_validator("route_decision", "route_reason", "large_profile_decision", "large_profile_reason")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("selection explanation text may not be empty")
        return normalized


class StatusReport(ContractModel):
    """Status command payload."""

    runtime: RuntimeState
    source_kind: Literal["snapshot", "live"]
    config_path: Path
    config_source_kind: str
    config_source: ConfigSourceInfo
    selection: RuntimeSelectionView
    selection_explanation: SelectionExplanationView
    size: SizeClassificationView
    integration_policy: ExecutionIntegrationContext | None = None
    assets: AssetInventoryView | None = None
    research: ResearchReport | None = None
    active_task: QueueItemView | None = None
    next_task: QueueItemView | None = None

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class SupervisorAttentionReason(str, Enum):
    """Stable machine-readable one-workspace attention reasons for external supervisors."""

    NONE = "none"
    HEALTH_FAILED = "health_failed"
    NOT_BOOTSTRAPPED = "not_bootstrapped"
    RUNNER_NOT_READY = "runner_not_ready"
    BLOCKED_EXECUTION = "blocked_execution"
    BLOCKED_RESEARCH = "blocked_research"
    AWAITING_OPERATOR_INPUT = "awaiting_operator_input"
    AUDIT_FAILED = "audit_failed"
    STALLED = "stalled"
    IDLE_WITH_NO_WORK = "idle_with_no_work"
    IDLE_WITH_PENDING_WORK = "idle_with_pending_work"
    DEGRADED_STATE = "degraded_state"


class SupervisorAction(str, Enum):
    """Named supported action hints exposed by the supervisor report."""

    PAUSE = "pause"
    RESUME = "resume"
    ADD_TASK = "add_task"
    QUEUE_REORDER = "queue_reorder"
    QUEUE_CLEANUP_REMOVE = "queue_cleanup_remove"
    QUEUE_CLEANUP_QUARANTINE = "queue_cleanup_quarantine"
    STOP = "stop"


class SupervisorReport(ContractModel):
    """Aggregated one-workspace external-supervisor report."""

    schema_version: Literal["1.0"] = "1.0"
    workspace_root: Path
    config_path: Path
    generated_at: datetime
    health_status: HealthCheckStatus
    health_summary: WorkspaceHealthSummary
    bootstrap_ready: bool
    execution_ready: bool
    process_running: bool
    paused: bool
    execution_status: ExecutionStatus
    research_status: ResearchStatus
    status_source_kind: Literal["snapshot", "live"]
    research_source_kind: Literal["snapshot", "live"]
    active_task: QueueItemView | None = None
    next_task: QueueItemView | None = None
    backlog_depth: int = Field(ge=0)
    deferred_queue_size: int = Field(ge=0)
    current_run_id: str | None = None
    current_stage: str | None = None
    time_in_current_status_seconds: float | None = Field(default=None, ge=0)
    attention_reason: SupervisorAttentionReason
    attention_summary: str
    allowed_actions: tuple[SupervisorAction, ...] = ()
    recent_events: tuple[EventRecord, ...] = ()

    @field_validator("workspace_root", "config_path", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path) -> Path:
        return Path(value)

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at(cls, value: datetime | str) -> datetime:
        return normalize_datetime(value)

    @field_validator("attention_summary")
    @classmethod
    def normalize_attention_summary(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("attention_summary may not be empty")
        return normalized


class ConfigShowReport(ContractModel):
    """Deterministic config-show payload."""

    source: ConfigSourceInfo
    config: EngineConfig
    config_hash: str
    selection: RuntimeSelectionView
    selection_explanation: SelectionExplanationView
    assets: AssetInventoryView

    @field_validator("config_hash")
    @classmethod
    def validate_config_hash(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("config_hash may not be empty")
        return normalized


class PolicyHookSummary(ContractModel):
    """Compact operator-facing summary of persisted policy-hook records."""

    record_count: int = Field(ge=0)
    hook_counts: dict[str, int] = Field(default_factory=dict)
    evaluator_counts: dict[str, int] = Field(default_factory=dict)
    decision_counts: dict[str, int] = Field(default_factory=dict)
    latest_hook: str | None = None
    latest_evaluator: str | None = None
    latest_decision: str | None = None
    latest_notes: tuple[str, ...] = ()
    latest_evidence_summaries: tuple[str, ...] = ()

    @field_validator("hook_counts", "evaluator_counts", "decision_counts", mode="before")
    @classmethod
    def normalize_count_maps(cls, value: dict[str, int] | None) -> dict[str, int]:
        if not value:
            return {}
        normalized: dict[str, int] = {}
        for raw_key, raw_count in value.items():
            key = str(raw_key).strip()
            if not key:
                raise ValueError("summary count-map keys may not be empty")
            count = int(raw_count)
            if count < 0:
                raise ValueError("summary count-map values may not be negative")
            normalized[key] = count
        return _normalized_count_map(normalized)

    @field_validator("latest_hook", "latest_evaluator", "latest_decision")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("latest_notes", "latest_evidence_summaries", mode="before")
    @classmethod
    def normalize_text_tuple(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(str(item).strip().split())
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)


class RunProvenanceReport(ContractModel):
    """Coherent control-plane view of compile-time and runtime provenance for one run."""

    run_id: str
    selection: RuntimeSelectionView | None = None
    selection_explanation: SelectionExplanationView | None = None
    current_preview: RuntimeSelectionView | None = None
    current_preview_explanation: SelectionExplanationView | None = None
    current_preview_error: str | None = None
    routing_modes: tuple[str, ...] = ()
    policy_hooks: PolicyHookSummary | None = None
    latest_policy_evidence: DiagnosticsPolicyEvidenceSnapshot | None = None
    integration_policy: ExecutionIntegrationContext | None = None
    compounding: "RunCompoundingReport | None" = None
    compile_snapshot: CompileTimeResolvedSnapshot | None = None
    runtime_history: tuple[RuntimeTransitionRecord, ...] = ()
    snapshot_path: Path | None = None
    transition_history_path: Path | None = None

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("run_id may not be empty")
        return normalized

    @field_validator("current_preview_error")
    @classmethod
    def validate_current_preview_error(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("current_preview_error may not be empty")
        return normalized

    @field_validator("snapshot_path", "transition_history_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value)

    @field_validator("routing_modes", mode="before")
    @classmethod
    def normalize_routing_modes(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            token = str(item).strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return tuple(normalized)

    def expected_routing_modes(self) -> tuple[str, ...]:
        return routing_modes_from_records(self.runtime_history)

    def with_selection_details(
        self,
        *,
        selection: RuntimeSelectionView | None,
        selection_explanation: SelectionExplanationView | None = None,
        current_preview: RuntimeSelectionView | None = None,
        current_preview_explanation: SelectionExplanationView | None = None,
        current_preview_error: str | None = None,
        routing_modes: tuple[str, ...] | list[str] | None = None,
    ) -> "RunProvenanceReport":
        payload = self.model_dump(mode="python")
        payload["selection"] = selection
        payload["selection_explanation"] = selection_explanation
        payload["current_preview"] = current_preview
        payload["current_preview_explanation"] = current_preview_explanation
        payload["current_preview_error"] = current_preview_error
        if routing_modes is not None:
            payload["routing_modes"] = routing_modes
        return self.__class__.model_validate(payload)

    @model_validator(mode="after")
    def validate_alignment(self) -> "RunProvenanceReport":
        if self.compile_snapshot is not None and self.compile_snapshot.run_id != self.run_id:
            raise ValueError("compile snapshot run_id does not match report run_id")
        if self.selection is None and self.selection_explanation is not None:
            raise ValueError("selection_explanation requires selection")
        if self.selection is not None:
            if self.compile_snapshot is None:
                raise ValueError("selection view requires a compile snapshot")
            if self.selection.scope != "frozen_run":
                raise ValueError("run provenance selections must use frozen_run scope")
            if self.selection.run_id != self.run_id:
                raise ValueError("selection view run_id does not match report run_id")
            if self.selection.selection.ref != self.compile_snapshot.selection_ref:
                raise ValueError("selection view selection.ref does not match compile snapshot")
            if self.selection.frozen_plan_id != self.compile_snapshot.frozen_plan.plan_id:
                raise ValueError("selection view frozen_plan_id does not match compile snapshot")
            if self.selection.frozen_plan_hash != self.compile_snapshot.frozen_plan.content_hash:
                raise ValueError("selection view frozen_plan_hash does not match compile snapshot")
            content = self.compile_snapshot.content
            selection_ref_fields = (
                ("mode", _selection_view_ref(self.selection.mode), content.selected_mode_ref),
                ("execution_loop", _selection_view_ref(self.selection.execution_loop), content.selected_execution_loop_ref),
                (
                    "task_authoring_profile",
                    _selection_view_ref(self.selection.task_authoring_profile),
                    content.task_authoring_profile_ref,
                ),
                ("model_profile", _selection_view_ref(self.selection.model_profile), content.model_profile_ref),
            )
            for field_name, actual_ref, expected_ref in selection_ref_fields:
                if actual_ref != expected_ref:
                    raise ValueError(f"selection view {field_name} does not match compile snapshot")
            if self.selection.research_participation != content.research_participation.value:
                raise ValueError("selection view research_participation does not match compile snapshot")
            if self.selection.outline_policy != content.outline_policy:
                raise ValueError("selection view outline_policy does not match compile snapshot")
            if self.selection.policy_toggles != content.policy_toggles:
                raise ValueError("selection view policy_toggles do not match compile snapshot")
            expected_stages = content.execution_plan.stages if content.execution_plan is not None else ()
            if len(self.selection.stage_bindings) != len(expected_stages):
                raise ValueError("selection view stage_bindings do not match compile snapshot")
            for binding, stage in zip(self.selection.stage_bindings, expected_stages):
                if binding.node_id != stage.node_id or binding.kind_id != stage.kind_id:
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if binding.stage_kind.ref != stage.stage_kind_ref:
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if _selection_view_ref(binding.model_profile) != stage.model_profile_ref:
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if binding.runner != stage.runner:
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if binding.model != stage.model:
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if binding.effort != stage.effort:
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if binding.allow_search != stage.allow_search:
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if binding.timeout_seconds != stage.timeout_seconds:
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if binding.prompt_asset_ref != stage.prompt_asset_ref:
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if binding.prompt_resolved_ref != (
                    stage.prompt_asset.resolved_ref if stage.prompt_asset is not None else None
                ):
                    raise ValueError("selection view stage_bindings do not match compile snapshot")
                if binding.prompt_source_kind != (
                    stage.prompt_asset.source_kind.value if stage.prompt_asset is not None else None
                ):
                    raise ValueError("selection view stage_bindings do not match compile snapshot")

        if self.current_preview is not None:
            if self.current_preview.scope != "preview":
                raise ValueError("current_preview must use preview scope")
            if self.current_preview.run_id is not None:
                raise ValueError("current_preview may not carry run_id")
        if self.current_preview is None and self.current_preview_explanation is not None:
            raise ValueError("current_preview_explanation requires current_preview")
        if self.current_preview is not None and self.current_preview_error is not None:
            raise ValueError("current_preview_error requires current_preview to be absent")

        expected_snapshot_id = self.compile_snapshot.snapshot_id if self.compile_snapshot is not None else None
        expected_plan_id = self.compile_snapshot.frozen_plan.plan_id if self.compile_snapshot is not None else None

        for record in self.runtime_history:
            if record.run_id != self.run_id:
                raise ValueError("runtime history record run_id does not match report run_id")
            if expected_snapshot_id is not None and record.snapshot_id != expected_snapshot_id:
                raise ValueError("runtime history snapshot_id does not match compile snapshot")
            if expected_plan_id is not None:
                if record.frozen_plan is None:
                    raise ValueError("runtime history record is missing frozen plan identity")
                if record.frozen_plan.plan_id != expected_plan_id:
                    raise ValueError("runtime history frozen plan does not match compile snapshot")
        if self.routing_modes and self.routing_modes != self.expected_routing_modes():
            raise ValueError("routing_modes do not match the observed runtime history routing modes")
        return self


class RunCreatedProcedureView(ContractModel):
    """Operator-facing summary for one created run-scoped procedure artifact."""

    procedure_id: str
    scope: ProcedureScope
    source_stage: str
    title: str
    summary: str
    created_at: datetime
    artifact_path: Path
    evidence_refs: tuple[str, ...] = ()

    @field_validator("artifact_path", mode="before")
    @classmethod
    def normalize_artifact_path(cls, value: str | Path) -> Path:
        return Path(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime | str) -> datetime:
        return normalize_datetime(value)


class RunProcedureSelectionView(ContractModel):
    """Stage-level compounding consideration and injection summary."""

    event_id: str
    node_id: str
    stage: str
    considered_count: int = Field(default=0, ge=0)
    injected_count: int = Field(default=0, ge=0)
    budget_characters: int = Field(default=0, ge=0)
    used_characters: int = Field(default=0, ge=0)
    truncated_count: int = Field(default=0, ge=0)
    rule_stage: str | None = None
    allowed_scopes: tuple[str, ...] = ()
    allowed_source_stages: tuple[str, ...] = ()
    considered_procedures: tuple[ConsideredProcedure, ...] = ()
    injected_procedures: tuple[InjectedProcedure, ...] = ()

    @classmethod
    def from_bundle(
        cls,
        *,
        event_id: str,
        node_id: str,
        stage: str,
        bundle: ProcedureInjectionBundle,
    ) -> "RunProcedureSelectionView":
        return cls(
            event_id=event_id,
            node_id=node_id,
            stage=stage,
            considered_count=bundle.candidate_count,
            injected_count=bundle.selected_count,
            budget_characters=bundle.budget_characters,
            used_characters=bundle.used_characters,
            truncated_count=bundle.truncated_count,
            rule_stage=bundle.rule.stage.value,
            allowed_scopes=tuple(scope.value for scope in bundle.rule.allowed_scopes),
            allowed_source_stages=tuple(stage.value for stage in bundle.rule.allowed_source_stages),
            considered_procedures=bundle.considered_procedures,
            injected_procedures=bundle.procedures,
        )


class RunContextFactSelectionView(ContractModel):
    """Stage-level durable context-fact consideration and injection summary."""

    event_id: str
    node_id: str
    stage: str
    considered_count: int = Field(default=0, ge=0)
    injected_count: int = Field(default=0, ge=0)
    budget_characters: int = Field(default=0, ge=0)
    used_characters: int = Field(default=0, ge=0)
    truncated_count: int = Field(default=0, ge=0)
    rule_stage: str | None = None
    allowed_scopes: tuple[str, ...] = ()
    allowed_source_stages: tuple[str, ...] = ()
    considered_facts: tuple[ConsideredContextFact, ...] = ()
    injected_facts: tuple[InjectedContextFact, ...] = ()

    @classmethod
    def from_bundle(
        cls,
        *,
        event_id: str,
        node_id: str,
        stage: str,
        bundle: ContextFactInjectionBundle,
    ) -> "RunContextFactSelectionView":
        return cls(
            event_id=event_id,
            node_id=node_id,
            stage=stage,
            considered_count=bundle.candidate_count,
            injected_count=bundle.selected_count,
            budget_characters=bundle.budget_characters,
            used_characters=bundle.used_characters,
            truncated_count=bundle.truncated_count,
            rule_stage=bundle.rule.stage.value,
            allowed_scopes=tuple(scope.value for scope in bundle.rule.allowed_scopes),
            allowed_source_stages=tuple(stage.value for stage in bundle.rule.allowed_source_stages),
            considered_facts=bundle.considered_facts,
            injected_facts=bundle.facts,
        )


class RunCompoundingReport(ContractModel):
    """Structured compounding provenance surfaced through run provenance."""

    created_procedures: tuple[RunCreatedProcedureView, ...] = ()
    procedure_selections: tuple[RunProcedureSelectionView, ...] = ()
    context_fact_selections: tuple[RunContextFactSelectionView, ...] = ()

    @property
    def created_count(self) -> int:
        return len(self.created_procedures)

    @property
    def selection_count(self) -> int:
        return len(self.procedure_selections)

    @property
    def fact_selection_count(self) -> int:
        return len(self.context_fact_selections)

    @property
    def injected_procedure_count(self) -> int:
        return sum(selection.injected_count for selection in self.procedure_selections)

    @property
    def injected_fact_count(self) -> int:
        return sum(selection.injected_count for selection in self.context_fact_selections)


class CompoundingLifecycleRecordView(ContractModel):
    """Operator-facing persisted lifecycle decision for one procedure."""

    record_id: str
    procedure_id: str
    state: ProcedureLifecycleState
    scope: ProcedureScope
    changed_at: datetime
    changed_by: str
    reason: str
    replacement_procedure_id: str | None = None
    record_path: Path

    @field_validator("changed_at", mode="before")
    @classmethod
    def normalize_changed_at(cls, value: datetime | str) -> datetime:
        return normalize_datetime(value)

    @field_validator("record_path", mode="before")
    @classmethod
    def normalize_record_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingProcedureView(ContractModel):
    """Inspectable compounding procedure plus effective lifecycle status."""

    procedure_id: str
    scope: ProcedureScope
    source_run_id: str
    source_stage: str
    title: str
    summary: str
    artifact_path: Path
    retrieval_status: Literal["eligible", "stale", "deprecated", "run_candidate"]
    eligible_for_retrieval: bool
    evidence_refs: tuple[str, ...] = ()
    latest_lifecycle_record: CompoundingLifecycleRecordView | None = None
    lifecycle_record_count: int = Field(default=0, ge=0)

    @field_validator("artifact_path", mode="before")
    @classmethod
    def normalize_artifact_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingProcedureListReport(ContractModel):
    """Deterministic operator-facing list of governed procedures."""

    config_path: Path
    procedures: tuple[CompoundingProcedureView, ...] = ()

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingProcedureReport(ContractModel):
    """Detailed operator-facing report for one governed procedure."""

    config_path: Path
    procedure: CompoundingProcedureView
    lifecycle_records: tuple[CompoundingLifecycleRecordView, ...] = ()

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingContextFactView(ContractModel):
    """Inspectable durable context fact plus effective retrieval status."""

    fact_id: str
    scope: ContextFactScope
    lifecycle_state: ContextFactLifecycleState
    source_run_id: str
    source_stage: str
    title: str
    statement: str
    summary: str
    retrieval_status: Literal["eligible", "stale", "deprecated", "run_candidate"]
    eligible_for_retrieval: bool
    tags: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    observed_at: datetime | None = None
    stale_reason: str | None = None
    supersedes_fact_id: str | None = None
    artifact_path: Path

    @field_validator("artifact_path", mode="before")
    @classmethod
    def normalize_artifact_path(cls, value: str | Path) -> Path:
        return Path(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def normalize_observed_at(cls, value: datetime | str | None) -> datetime | None:
        return normalize_datetime(value)


class CompoundingContextFactListReport(ContractModel):
    """Deterministic operator-facing list of durable context facts."""

    config_path: Path
    facts: tuple[CompoundingContextFactView, ...] = ()

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingContextFactReport(ContractModel):
    """Detailed operator-facing report for one durable context fact."""

    config_path: Path
    fact: CompoundingContextFactView

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingHarnessCandidateView(ContractModel):
    """Inspectable governed harness candidate artifact."""

    candidate_id: str
    name: str
    baseline_ref: str
    benchmark_suite_ref: str
    state: HarnessCandidateState
    changed_surfaces: tuple[HarnessChangedSurface, ...] = ()
    has_compounding_policy_override: bool
    reviewer_note: str | None = None
    created_at: datetime
    created_by: str
    artifact_path: Path

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime | str) -> datetime:
        return normalize_datetime(value)

    @field_validator("artifact_path", mode="before")
    @classmethod
    def normalize_artifact_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingHarnessCandidateListReport(ContractModel):
    """Deterministic operator-facing list of harness candidates."""

    config_path: Path
    candidates: tuple[CompoundingHarnessCandidateView, ...] = ()

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingHarnessBenchmarkView(ContractModel):
    """Inspectable persisted harness benchmark result."""

    result_id: str
    candidate_id: str
    benchmark_suite_ref: str
    status: HarnessBenchmarkStatus
    outcome: HarnessBenchmarkOutcome
    completed_at: datetime
    result_path: Path
    outcome_summary: HarnessBenchmarkOutcomeSummary
    cost_summary: HarnessBenchmarkCostSummary
    artifact_refs: tuple[str, ...] = ()

    @field_validator("completed_at", mode="before")
    @classmethod
    def normalize_completed_at(cls, value: datetime | str) -> datetime:
        return normalize_datetime(value)

    @field_validator("result_path", mode="before")
    @classmethod
    def normalize_result_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingHarnessCandidateReport(ContractModel):
    """Detailed operator-facing report for one harness candidate."""

    config_path: Path
    candidate: CompoundingHarnessCandidateView
    recent_benchmarks: tuple[CompoundingHarnessBenchmarkView, ...] = ()

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingHarnessBenchmarkListReport(ContractModel):
    """Deterministic operator-facing list of benchmark results."""

    config_path: Path
    benchmarks: tuple[CompoundingHarnessBenchmarkView, ...] = ()

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingHarnessBenchmarkReport(ContractModel):
    """Detailed operator-facing report for one benchmark result."""

    config_path: Path
    benchmark: CompoundingHarnessBenchmarkView

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingHarnessRecommendationView(ContractModel):
    """Inspectable persisted harness recommendation artifact."""

    recommendation_id: str
    search_id: str
    disposition: HarnessRecommendationDisposition
    recommended_candidate_id: str | None = None
    recommended_result_id: str | None = None
    candidate_ids: tuple[str, ...] = ()
    benchmark_result_ids: tuple[str, ...] = ()
    summary: str
    created_at: datetime
    created_by: str
    artifact_path: Path

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime | str) -> datetime:
        return normalize_datetime(value)

    @field_validator("artifact_path", mode="before")
    @classmethod
    def normalize_artifact_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingHarnessRecommendationListReport(ContractModel):
    """Deterministic operator-facing list of harness recommendations."""

    config_path: Path
    recommendations: tuple[CompoundingHarnessRecommendationView, ...] = ()

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingHarnessRecommendationReport(ContractModel):
    """Detailed operator-facing report for one harness recommendation."""

    config_path: Path
    recommendation: CompoundingHarnessRecommendationView

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class CompoundingGovernanceSummaryView(ContractModel):
    """Compact cross-family governance summary for operator inspection."""

    config_path: Path
    procedure_total: int = Field(default=0, ge=0)
    procedure_eligible: int = Field(default=0, ge=0)
    procedure_pending_review: int = Field(default=0, ge=0)
    procedure_deprecated: int = Field(default=0, ge=0)
    context_fact_total: int = Field(default=0, ge=0)
    context_fact_eligible: int = Field(default=0, ge=0)
    context_fact_pending_review: int = Field(default=0, ge=0)
    context_fact_deprecated: int = Field(default=0, ge=0)
    harness_candidate_total: int = Field(default=0, ge=0)
    harness_candidate_pending_review: int = Field(default=0, ge=0)
    harness_candidate_accepted: int = Field(default=0, ge=0)
    harness_candidate_rejected: int = Field(default=0, ge=0)
    benchmark_total: int = Field(default=0, ge=0)
    recommendation_total: int = Field(default=0, ge=0)
    recommendation_pending: int = Field(default=0, ge=0)
    recommendation_no_change: int = Field(default=0, ge=0)
    pending_governance_items: int = Field(default=0, ge=0)
    latest_recommendation_id: str | None = None
    latest_recommendation_summary: str | None = None
    recent_usage_run_id: str | None = None
    recent_usage_procedure_count: int = Field(default=0, ge=0)
    recent_usage_context_fact_count: int = Field(default=0, ge=0)

    @field_validator("config_path", mode="before")
    @classmethod
    def normalize_config_path(cls, value: str | Path) -> Path:
        return Path(value)


class OperationResult(ContractModel):
    """Deterministic outcome of one control operation."""

    command_id: str | None = None
    mode: Literal["direct", "mailbox"]
    applied: bool
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
