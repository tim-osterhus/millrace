"""Immutable view models for the Millrace TUI shell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Generic, TypeVar

# Shell structure lives in a sibling module but remains re-exported here to
# preserve the top-level TUI model import surface.
from .shell_models import (
    DEFAULT_PANEL,
    DisplayMode,
    EXPANDED_STREAM_WIDGET_ID,
    PANEL_BY_ID,
    PANELS,
    PanelDefinition,
    PanelId,
    ShellBodyMode,
    nav_button_id,
    panel_widget_id,
    shell_content_target,
    toggle_display_mode,
    toggle_shell_body_mode,
)


class NoticeLevel(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class FailureCategory(StrEnum):
    CONTROL = "control"
    INPUT = "input"
    IO = "io"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class KeyValueView:
    key: str
    value: str


class ConfigFieldInputKind(StrEnum):
    INTEGER = "integer"
    CHOICE = "choice"


@dataclass(frozen=True, slots=True)
class ConfigFieldView:
    key: str
    label: str
    value: str
    boundary: str
    description: str
    editable: bool = False
    input_kind: ConfigFieldInputKind | None = None
    options: tuple[str, ...] = ()
    minimum: int | None = None


@dataclass(frozen=True, slots=True)
class ConfigOverviewView:
    config_path: str
    source_kind: str
    source_ref: str
    config_hash: str
    bundle_version: str | None
    editing_enabled: bool
    editing_disabled_reason: str | None
    fields: tuple[ConfigFieldView, ...] = ()
    startup_only_fields: tuple[ConfigFieldView, ...] = ()


@dataclass(frozen=True, slots=True)
class QueueTaskView:
    task_id: str
    title: str
    spec_id: str | None = None


@dataclass(frozen=True, slots=True)
class SelectionSummaryView:
    scope: str
    selection_ref: str
    mode_ref: str | None
    execution_loop_ref: str | None
    frozen_plan_id: str
    frozen_plan_hash: str
    run_id: str | None
    research_participation: str
    stage_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectionDecisionView:
    selected_size: str
    route_decision: str
    route_reason: str
    large_profile_decision: str
    large_profile_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeOverviewView:
    workspace_path: str
    config_path: str
    config_source_kind: str
    source_kind: str
    process_running: bool
    paused: bool
    pause_reason: str | None
    pause_run_id: str | None
    mode: str
    execution_status: str
    research_status: str
    active_task_id: str | None
    backlog_depth: int
    deferred_queue_size: int
    uptime_seconds: float | None
    asset_bundle_version: str | None
    pending_config_hash: str | None
    previous_config_hash: str | None
    pending_config_boundary: str | None
    pending_config_fields: tuple[str, ...]
    rollback_armed: bool
    started_at: datetime | None
    updated_at: datetime
    selection: SelectionSummaryView
    selection_decision: SelectionDecisionView


class LifecycleState(StrEnum):
    IDLE = "idle"
    LAUNCHING_ONCE = "launching_once"
    LAUNCHING_DAEMON = "launching_daemon"
    DAEMON_RUNNING = "daemon_running"
    PAUSED = "paused"
    STOP_IN_PROGRESS = "stop_in_progress"
    LIFECYCLE_FAILURE = "lifecycle_failure"


@dataclass(frozen=True, slots=True)
class LifecycleSignalView:
    state: LifecycleState
    label: str
    detail: str


def lifecycle_signal_from_context(
    *,
    runtime: RuntimeOverviewView | None,
    pending_action: str | None = None,
    pending_message: str | None = None,
    lifecycle_failure: GatewayFailure | None = None,
) -> LifecycleSignalView:
    if pending_action == "start_once":
        return LifecycleSignalView(
            state=LifecycleState.LAUNCHING_ONCE,
            label="launching once",
            detail=pending_message or "foreground once launch in progress",
        )
    if pending_action == "start_daemon":
        return LifecycleSignalView(
            state=LifecycleState.LAUNCHING_DAEMON,
            label="launching daemon",
            detail=pending_message or "daemon launch in progress",
        )
    if pending_action == "stop":
        return LifecycleSignalView(
            state=LifecycleState.STOP_IN_PROGRESS,
            label="stop in progress",
            detail=pending_message or "waiting for daemon stop acknowledgement",
        )
    if lifecycle_failure is not None:
        return LifecycleSignalView(
            state=LifecycleState.LIFECYCLE_FAILURE,
            label="lifecycle failure",
            detail=lifecycle_failure.message,
        )
    if runtime is None:
        return LifecycleSignalView(
            state=LifecycleState.IDLE,
            label="idle",
            detail="awaiting first workspace snapshot",
        )
    if runtime.paused:
        reason = runtime.pause_reason or "daemon is paused"
        return LifecycleSignalView(
            state=LifecycleState.PAUSED,
            label="paused",
            detail=reason,
        )
    if runtime.process_running:
        return LifecycleSignalView(
            state=LifecycleState.DAEMON_RUNNING,
            label="daemon running",
            detail=f"mode {runtime.mode} | exec {runtime.execution_status}",
        )
    return LifecycleSignalView(
        state=LifecycleState.IDLE,
        label="idle",
        detail=f"mode {runtime.mode} | exec {runtime.execution_status}",
    )


@dataclass(frozen=True, slots=True)
class QueueOverviewView:
    active_task: QueueTaskView | None
    next_task: QueueTaskView | None
    backlog_depth: int
    backlog: tuple[QueueTaskView, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchQueueItemView:
    family: str
    item_key: str
    title: str
    item_kind: str
    queue_path: str
    item_path: str | None = None
    occurred_at: datetime | None = None
    source_status: str | None = None
    stage_blocked: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchQueueFamilyView:
    family: str
    ready: bool
    item_count: int
    queue_owner: str | None
    queue_paths: tuple[str, ...]
    contract_paths: tuple[str, ...]
    first_item: ResearchQueueItemView | None = None


@dataclass(frozen=True, slots=True)
class InterviewQuestionSummaryView:
    question_id: str
    status: str
    spec_id: str
    idea_id: str = ""
    title: str = ""
    question: str = ""
    why_this_matters: str = ""
    recommended_answer: str = ""
    answer_source: str = ""
    blocking: bool = False
    source_path: str = ""
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResearchOverviewView:
    status: str
    source_kind: str
    configured_mode: str
    configured_idle_mode: str
    current_mode: str
    last_mode: str
    mode_reason: str
    cycle_count: int
    transition_count: int
    selected_family: str | None
    deferred_breadcrumb_count: int
    deferred_request_count: int
    queue_families: tuple[ResearchQueueFamilyView, ...]
    audit_summary_path: str
    audit_history_path: str
    audit_summary_present: bool
    latest_gate_decision: str | None
    latest_completion_decision: str | None
    completion_allowed: bool
    completion_reason: str
    updated_at: datetime
    next_poll_at: datetime | None
    interview_questions: tuple[InterviewQuestionSummaryView, ...] = ()
    audit_summary: "ResearchAuditSummaryView | None" = None
    governance: "ResearchGovernanceOverviewView | None" = None
    recent_activity: tuple["RuntimeEventView", ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchAuditSummaryView:
    updated_at: datetime | None = None
    total_count: int = 0
    pass_count: int = 0
    fail_count: int = 0
    last_status: str = "none"
    last_details: str = "none"
    last_at: datetime | None = None
    last_title: str | None = None
    last_decision: str | None = None
    last_reason_count: int = 0
    remediation_action: str | None = None
    remediation_spec_id: str | None = None
    remediation_task_id: str | None = None
    remediation_task_title: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchGovernanceOverviewView:
    queue_governor_status: str
    queue_governor_reason: str
    drift_status: str
    drift_reason: str
    drift_fields: tuple[str, ...] = ()
    canary_status: str = "not_configured"
    canary_reason: str = ""
    canary_changed_fields: tuple[str, ...] = ()
    recovery_status: str = "not_active"
    recovery_reason: str = ""
    recovery_batch_id: str | None = None
    recovery_visible_task_count: int = 0
    recovery_escalation_action: str = "none"
    recovery_regeneration_status: str | None = None
    regenerated_task_id: str | None = None
    regenerated_task_title: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeEventView:
    event_type: str
    source: str
    timestamp: datetime
    is_research_event: bool
    payload: tuple[KeyValueView, ...] = ()
    category: str = ""
    summary: str = ""
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class EventLogView:
    events: tuple[RuntimeEventView, ...] = ()
    last_loaded_at: datetime | None = None


RuntimeEventIdentity = tuple[str, str, str, str, str, str, tuple[tuple[str, str], ...]]


def runtime_event_identity(event: RuntimeEventView) -> RuntimeEventIdentity:
    """Return a strict shaped-event identity for live log dedupe and selection."""

    return (
        event.timestamp.isoformat(),
        event.source,
        event.event_type,
        event.category,
        event.summary,
        event.run_id or "",
        tuple((detail.key, detail.value) for detail in event.payload),
    )


@dataclass(frozen=True, slots=True)
class PublishOverviewView:
    staging_repo_dir: str
    manifest_source_kind: str
    manifest_source_ref: str
    manifest_version: int
    selected_paths: tuple[str, ...]
    branch: str | None
    commit_message: str
    push_requested: bool
    git_worktree_present: bool
    git_worktree_valid: bool
    origin_configured: bool
    has_changes: bool
    changed_paths: tuple[str, ...]
    commit_allowed: bool
    publish_allowed: bool
    status: str
    skip_reason: str | None


@dataclass(frozen=True, slots=True)
class RunSummaryView:
    run_id: str
    compiled_at: datetime | None = None
    selection_ref: str | None = None
    frozen_plan_id: str | None = None
    frozen_plan_hash: str | None = None
    stage_count: int | None = None
    transition_count: int = 0
    latest_transition_at: datetime | None = None
    latest_transition_label: str | None = None
    latest_status: str | None = None
    routing_modes: tuple[str, ...] = ()
    latest_policy_decision: str | None = None
    integration_target: str | None = None
    integration_enabled: bool | None = None
    snapshot_present: bool = False
    history_present: bool = False
    note: str | None = None
    issue: str | None = None


@dataclass(frozen=True, slots=True)
class RunsOverviewView:
    runs_dir: str
    scanned_at: datetime
    runs: tuple[RunSummaryView, ...] = ()


@dataclass(frozen=True, slots=True)
class RunTransitionView:
    event_id: str
    timestamp: datetime
    observed_timestamp: datetime
    event_name: str
    source: str
    plane: str
    node_id: str
    kind_id: str | None
    outcome: str | None
    status_before: str | None
    status_after: str | None
    active_task_before: str | None
    active_task_after: str | None
    routing_mode: str | None
    queue_mutations_applied: tuple[str, ...]
    artifacts_emitted: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunPolicyEvidenceView:
    hook: str
    evaluator: str
    decision: str
    timestamp: datetime
    event_name: str
    node_id: str
    routing_mode: str | None = None
    notes: tuple[str, ...] = ()
    evidence_summaries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunIntegrationSummaryView:
    effective_mode: str
    builder_success_target: str
    should_run_integration: bool
    task_gate_required: bool = False
    task_integration_preference: str | None = None
    requested_sequence: tuple[str, ...] = ()
    effective_sequence: tuple[str, ...] = ()
    available_execution_nodes: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RunDetailView:
    run_id: str
    compiled_at: datetime | None
    frozen_plan_id: str | None
    frozen_plan_hash: str | None
    stage_count: int | None
    selection: SelectionSummaryView | None
    selection_decision: SelectionDecisionView | None
    current_preview: SelectionSummaryView | None
    current_preview_decision: SelectionDecisionView | None
    current_preview_error: str | None
    routing_modes: tuple[str, ...]
    snapshot_path: str | None
    transition_history_path: str | None
    policy_hook_count: int
    latest_policy_decision: str | None
    latest_policy_evidence: RunPolicyEvidenceView | None
    integration_policy: RunIntegrationSummaryView | None
    transitions: tuple[RunTransitionView, ...]


@dataclass(frozen=True, slots=True)
class NoticeView:
    level: NoticeLevel
    title: str
    message: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GatewayFailure:
    operation: str
    category: FailureCategory
    message: str
    exception_type: str
    retryable: bool = True


@dataclass(frozen=True, slots=True)
class ActionResultView:
    action: str
    message: str
    applied: bool
    mode: str | None = None
    command_id: str | None = None
    details: tuple[KeyValueView, ...] = ()


@dataclass(frozen=True, slots=True)
class RefreshPayload:
    refreshed_at: datetime
    runtime: RuntimeOverviewView | None = None
    config: ConfigOverviewView | None = None
    queue: QueueOverviewView | None = None
    research: ResearchOverviewView | None = None
    events: EventLogView | None = None
    publish: PublishOverviewView | None = None
    runs: RunsOverviewView | None = None
    run_detail: RunDetailView | None = None

    def __post_init__(self) -> None:
        if (
            self.runtime is None
            and self.config is None
            and self.queue is None
            and self.research is None
            and self.events is None
            and self.publish is None
            and self.runs is None
            and self.run_detail is None
        ):
            raise ValueError("refresh payload must include at least one view")


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class GatewayResult(Generic[T]):
    value: T | None = None
    failure: GatewayFailure | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.failure is None):
            raise ValueError("gateway result must contain exactly one of value or failure")

    @property
    def ok(self) -> bool:
        return self.failure is None


def notice_from_action(
    result: ActionResultView,
    *,
    created_at: datetime | None = None,
    level: NoticeLevel | None = None,
) -> NoticeView:
    message = result.message
    if result.mode == "mailbox" and result.command_id:
        message = f"{message} (command {result.command_id})"
    return NoticeView(
        level=level or (NoticeLevel.SUCCESS if result.applied else NoticeLevel.WARNING),
        title=result.action.replace(".", " ").title(),
        message=message,
        created_at=created_at or datetime.now(timezone.utc),
    )


def notice_from_failure(
    failure: GatewayFailure,
    *,
    created_at: datetime | None = None,
) -> NoticeView:
    return NoticeView(
        level=NoticeLevel.ERROR,
        title=failure.operation.replace(".", " ").title(),
        message=failure.message,
        created_at=created_at or datetime.now(timezone.utc),
    )


__all__ = [
    "ActionResultView",
    "ConfigFieldInputKind",
    "ConfigFieldView",
    "ConfigOverviewView",
    "DEFAULT_PANEL",
    "DisplayMode",
    "EventLogView",
    "EXPANDED_STREAM_WIDGET_ID",
    "FailureCategory",
    "GatewayFailure",
    "GatewayResult",
    "InterviewQuestionSummaryView",
    "KeyValueView",
    "LifecycleSignalView",
    "LifecycleState",
    "NoticeLevel",
    "NoticeView",
    "PANEL_BY_ID",
    "PANELS",
    "PanelDefinition",
    "PanelId",
    "PublishOverviewView",
    "QueueOverviewView",
    "QueueTaskView",
    "RefreshPayload",
    "ResearchAuditSummaryView",
    "ResearchGovernanceOverviewView",
    "ResearchOverviewView",
    "ResearchQueueFamilyView",
    "ResearchQueueItemView",
    "RunDetailView",
    "RunIntegrationSummaryView",
    "RunPolicyEvidenceView",
    "RunSummaryView",
    "RunTransitionView",
    "RunsOverviewView",
    "RuntimeEventView",
    "RuntimeEventIdentity",
    "RuntimeOverviewView",
    "SelectionDecisionView",
    "SelectionSummaryView",
    "lifecycle_signal_from_context",
    "nav_button_id",
    "notice_from_action",
    "notice_from_failure",
    "panel_widget_id",
    "runtime_event_identity",
    "ShellBodyMode",
    "shell_content_target",
    "toggle_display_mode",
    "toggle_shell_body_mode",
]
