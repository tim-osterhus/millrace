"""Public run-trace graph contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import model_validator

from .base import ContractModel
from .enums import Plane, ResultClass
from .token_usage import TokenUsage
from .work_refs import legacy_work_item_kind_for_family_id, normalize_work_item_family_id

RunTraceSpawnedWorkKind = str
RunTraceStatus = Literal["active", "complete", "blocked", "handoff", "incomplete", "malformed"]


class RunTraceArtifactRef(ContractModel):
    path: str
    kind: str
    size_bytes: int | None = None
    sha256: str | None = None


class RunTraceSpawnedWorkRef(ContractModel):
    family_id: str | None = None
    kind: RunTraceSpawnedWorkKind | None = None
    item_id: str
    path: str | None = None
    reason: str | None = None
    source_stage_node_id: str | None = None
    source_terminal_result: str | None = None

    @model_validator(mode="after")
    def validate_family_id(self) -> "RunTraceSpawnedWorkRef":
        if self.family_id is None and self.kind is not None:
            self.family_id = self.kind
        if self.family_id is not None:
            self.family_id = normalize_work_item_family_id(self.family_id, field_name="family_id")
            if self.kind is None:
                legacy_kind = legacy_work_item_kind_for_family_id(self.family_id)
                if legacy_kind is not None:
                    self.kind = legacy_kind.value
        if self.family_id is None:
            raise ValueError("spawned work ref requires family_id or kind")
        return self


class RunTraceNode(ContractModel):
    trace_node_id: str
    run_id: str
    request_id: str
    plane: Plane
    stage: str
    node_id: str
    stage_kind_id: str
    compiled_plan_id: str | None = None
    mode_id: str | None = None
    request_kind: str | None = None
    work_item_family_id: str | None = None
    work_item_kind: str | None = None
    work_item_id: str | None = None
    closure_target_root_spec_id: str | None = None
    closure_target_root_source_kind: str | None = None
    closure_target_root_source_id: str | None = None
    closure_target_root_source_path: str | None = None
    terminal_result: str
    result_class: ResultClass
    failure_class: str | None = None
    runner_name: str | None = None
    model_name: str | None = None
    thinking_level: str | None = None
    model_reasoning_effort: str | None = None
    model_assignment_alias_id: str | None = None
    model_assignment_source: str | None = None
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    token_usage: TokenUsage | None = None
    artifacts: tuple[RunTraceArtifactRef, ...] = ()

    @model_validator(mode="after")
    def normalize_work_family(self) -> "RunTraceNode":
        if self.work_item_family_id is None and self.work_item_kind is not None:
            self.work_item_family_id = self.work_item_kind
        if self.work_item_family_id is not None:
            self.work_item_family_id = normalize_work_item_family_id(
                self.work_item_family_id,
                field_name="work_item_family_id",
            )
        return self


class RunTraceEdge(ContractModel):
    trace_edge_id: str
    source_trace_node_id: str
    outcome: str
    edge_kind: str
    target_node_id: str | None = None
    target_trace_node_id: str | None = None
    terminal_state_id: str | None = None
    terminal_action_id: str | None = None
    terminal_action_router_consequence: str | None = None
    lifecycle_mutation_plan_id: str | None = None
    lifecycle_action_id: str | None = None
    terminal_writes_status: str | None = None
    terminal_metadata_source: Literal["graph_resolved", "inferred", "unknown"] = "unknown"
    failure_class: str | None = None
    create_incident: bool = False
    runtime_operation_id: str | None = None
    spawned_work: tuple[RunTraceSpawnedWorkRef, ...] = ()
    decision_reason: str | None = None
    decided_at: datetime


class RunTraceGraph(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["run_trace_graph"] = "run_trace_graph"
    run_id: str
    run_dir: str
    compiled_plan_id: str | None = None
    mode_id: str | None = None
    request_kind: str | None = None
    work_item_family_id: str | None = None
    work_item_kind: str | None = None
    work_item_id: str | None = None
    closure_target_root_spec_id: str | None = None
    closure_target_root_source_kind: str | None = None
    closure_target_root_source_id: str | None = None
    closure_target_root_source_path: str | None = None
    status: RunTraceStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    nodes: tuple[RunTraceNode, ...] = ()
    edges: tuple[RunTraceEdge, ...] = ()
    notes: tuple[str, ...] = ()
    generated_at: datetime

    @model_validator(mode="after")
    def validate_edge_refs(self) -> "RunTraceGraph":
        if self.work_item_family_id is None and self.work_item_kind is not None:
            self.work_item_family_id = self.work_item_kind
        if self.work_item_family_id is not None:
            self.work_item_family_id = normalize_work_item_family_id(
                self.work_item_family_id,
                field_name="work_item_family_id",
            )
        node_ids = {node.trace_node_id for node in self.nodes}
        for edge in self.edges:
            if edge.source_trace_node_id not in node_ids:
                raise ValueError("run trace edge source_trace_node_id must reference a node")
            if edge.target_trace_node_id is not None and edge.target_trace_node_id not in node_ids:
                raise ValueError("run trace edge target_trace_node_id must reference a node")
        return self


__all__ = [
    "RunTraceArtifactRef",
    "RunTraceEdge",
    "RunTraceGraph",
    "RunTraceNode",
    "RunTraceSpawnedWorkKind",
    "RunTraceSpawnedWorkRef",
    "RunTraceStatus",
]
