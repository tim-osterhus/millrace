"""Stable DTOs for the read-only Millrace Web API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WebModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


class WorkspaceRef(WebModel):
    id: str
    name: str
    path: str


class WorkspacesResponse(WebModel):
    workspaces: tuple[WorkspaceRef, ...]


class HealthResponse(WebModel):
    status: Literal["ok"] = "ok"


class DaemonSummary(WebModel):
    state: Literal["running", "paused", "stopped", "unknown"]
    process_running: bool
    pause_sources: tuple[str, ...] = ()


class ActiveRunSummary(WebModel):
    plane: str
    stage: str
    node_id: str
    stage_kind_id: str
    run_id: str
    request_kind: str
    work_item_kind: str | None = None
    work_item_id: str | None = None
    active_since: datetime | None = None


class RuntimeSummary(WebModel):
    mode_id: str | None = None
    active_plane: str | None = None
    active_stage: str | None = None
    active_node_id: str | None = None
    active_stage_kind_id: str | None = None
    active_run_id: str | None = None
    active_work_item_kind: str | None = None
    active_work_item_id: str | None = None
    started_at: datetime | None = None
    active_since: datetime | None = None
    elapsed_seconds: float | None = None
    active_runs_by_plane: tuple[ActiveRunSummary, ...] = ()


class CompiledPlanSummary(WebModel):
    id: str | None = None
    currentness: Literal["current", "stale", "missing", "unknown"] = "unknown"
    mode_id: str | None = None


class BaselineSummary(WebModel):
    state: Literal["initialized", "missing", "unknown"]
    manifest_id: str | None = None


class QueueBucket(WebModel):
    incoming: int = 0
    active: int = 0
    done: int = 0
    blocked: int = 0


class QueueSummary(WebModel):
    tasks: QueueBucket = Field(default_factory=QueueBucket)
    specs: QueueBucket = Field(default_factory=QueueBucket)
    incidents: QueueBucket = Field(default_factory=QueueBucket)
    learning: QueueBucket = Field(default_factory=QueueBucket)
    blueprint_drafts: QueueBucket = Field(default_factory=QueueBucket)
    graph_owned_families: dict[str, QueueBucket] = Field(default_factory=dict)


class UsageGovernanceSummary(WebModel):
    enabled: bool = False
    paused: bool = False
    blocker_count: int = 0
    auto_resume_possible: bool = True
    budget_status: str = "disabled"


class ArbiterSummary(WebModel):
    closure_target_open: bool = False
    latest_result: str | None = None
    next_stage: str | None = None
    status: Literal["watching", "blocked", "idle", "invalid", "unknown"] = "unknown"


class StageNodeSummary(WebModel):
    node_id: str
    stage_kind_id: str
    plane: str
    label: str


class StageEdgeSummary(WebModel):
    source_node_id: str
    target_node_id: str | None = None
    terminal_state_id: str | None = None
    outcome: str
    kind: str
    terminal_action_id: str | None = None
    terminal_action_router_consequence: str | None = None
    lifecycle_mutation_plan_id: str | None = None
    lifecycle_action_id: str | None = None
    terminal_writes_status: str | None = None
    terminal_create_incident: bool = False


class StageGraphSummary(WebModel):
    plane: str
    loop_id: str
    nodes: tuple[StageNodeSummary, ...] = ()
    edges: tuple[StageEdgeSummary, ...] = ()
    is_fallback: bool = False


class RunArtifactSummary(WebModel):
    path: str
    name: str
    size_bytes: int | None = None
    kind: str = "artifact"


class RunSummary(WebModel):
    run_id: str
    status: str
    stage: str | None = None
    result: str | None = None
    work_item_kind: str | None = None
    work_item_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    total_tokens: int | None = None
    artifacts: tuple[RunArtifactSummary, ...] = ()


class RunsResponse(WebModel):
    runs: tuple[RunSummary, ...]


class TraceNodeSummary(WebModel):
    trace_node_id: str
    plane: str
    stage: str
    node_id: str
    terminal_result: str
    result_class: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None


class TraceEdgeSummary(WebModel):
    source_trace_node_id: str
    outcome: str
    target_node_id: str | None = None
    target_trace_node_id: str | None = None
    terminal_state_id: str | None = None
    terminal_action_id: str | None = None
    terminal_action_router_consequence: str | None = None
    lifecycle_mutation_plan_id: str | None = None
    lifecycle_action_id: str | None = None
    terminal_writes_status: str | None = None
    terminal_metadata_source: str = "unknown"
    failure_class: str | None = None
    create_incident: bool = False
    runtime_operation_id: str | None = None
    edge_kind: str


class RunTraceSummary(WebModel):
    run_id: str
    status: str
    nodes: tuple[TraceNodeSummary, ...] = ()
    edges: tuple[TraceEdgeSummary, ...] = ()
    notes: tuple[str, ...] = ()


class EventSummary(WebModel):
    workspace_id: str
    event_type: str
    occurred_at: datetime
    plane: str | None = None
    stage: str | None = None
    work_item_id: str | None = None
    run_id: str | None = None
    details: str = ""
    artifact_path: str | None = None


class EventsResponse(WebModel):
    events: tuple[EventSummary, ...]


class DashboardSummary(WebModel):
    workspace: WorkspaceRef
    daemon: DaemonSummary
    runtime: RuntimeSummary
    compiled_plan: CompiledPlanSummary
    baseline: BaselineSummary
    queues: QueueSummary
    usage_governance: UsageGovernanceSummary
    arbiter: ArbiterSummary
    graphs: tuple[StageGraphSummary, ...] = ()
    recent_runs: tuple[RunSummary, ...] = ()
    recent_traces: tuple[RunTraceSummary, ...] = ()
    events: tuple[EventSummary, ...] = ()
