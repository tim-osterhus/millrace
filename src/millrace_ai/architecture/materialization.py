"""Materialized compiled plan contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from millrace_ai.contracts import Plane

from .loop_graphs import (
    GraphLoopCompletionBehaviorDefinition,
    GraphLoopCounterName,
    GraphLoopEdgeDefinition,
    GraphLoopEdgeKind,
    GraphLoopEntryDefinition,
    GraphLoopEntryKey,
    GraphLoopTerminalStateDefinition,
)
from .stage_kinds import ArchitectureContractModel


class CompiledGraphEntryPlan(ArchitectureContractModel):
    entry_key: GraphLoopEntryKey
    node_id: str
    stage_kind_id: str
    plane: Plane


class CompiledGraphCompletionEntryPlan(ArchitectureContractModel):
    entry_key: Literal[GraphLoopEntryKey.CLOSURE_TARGET] = GraphLoopEntryKey.CLOSURE_TARGET
    node_id: str
    stage_kind_id: str
    plane: Plane
    trigger: Literal["backlog_drained"]
    readiness_rule: Literal["no_open_lineage_work"]
    request_kind: Literal["closure_target"]
    target_selector: Literal["active_closure_target"]
    rubric_policy: Literal["reuse_or_create"]
    blocked_work_policy: Literal["suppress"]
    skip_if_already_closed: bool = True
    on_pass_terminal_state_id: str
    on_gap_terminal_state_id: str
    create_incident_on_gap: bool = False


class CompiledGraphTransitionPlan(ArchitectureContractModel):
    edge_id: str
    source_node_id: str
    outcome: str
    target_node_id: str | None = None
    terminal_state_id: str | None = None
    kind: GraphLoopEdgeKind = GraphLoopEdgeKind.NORMAL
    priority: int = 100
    max_attempts: int | None = None

    @model_validator(mode="after")
    def validate_target_shape(self) -> "CompiledGraphTransitionPlan":
        target_count = int(self.target_node_id is not None) + int(self.terminal_state_id is not None)
        if target_count != 1:
            raise ValueError(
                "compiled graph transition must target exactly one node or terminal_state_id"
            )
        return self


class CompiledGraphResumePolicyPlan(ArchitectureContractModel):
    policy_id: str
    source_node_id: str
    on_outcome: str
    default_target_node_id: str
    metadata_stage_keys: tuple[str, ...] = ()
    disallowed_target_node_ids: tuple[str, ...] = ()


class CompiledGraphThresholdPolicyPlan(ArchitectureContractModel):
    policy_id: str
    source_node_ids: tuple[str, ...] = Field(min_length=1)
    on_outcome: str
    counter_name: GraphLoopCounterName
    threshold: int = Field(ge=1)
    exhausted_target_node_id: str | None = None
    exhausted_terminal_state_id: str | None = None

    @model_validator(mode="after")
    def validate_target_shape(self) -> "CompiledGraphThresholdPolicyPlan":
        target_count = int(self.exhausted_target_node_id is not None) + int(
            self.exhausted_terminal_state_id is not None
        )
        if target_count != 1:
            raise ValueError(
                "compiled threshold policy must target exactly one exhausted node or terminal state"
            )
        return self


class MaterializedGraphNodePlan(ArchitectureContractModel):
    node_id: str
    stage_kind_id: str
    plane: Plane
    entrypoint_path: str
    entrypoint_contract_id: str | None = None
    required_skill_paths: tuple[str, ...] = ()
    attached_skill_additions: tuple[str, ...] = ()
    runner_name: str | None = None
    model_name: str | None = None
    timeout_seconds: int = 0

    @model_validator(mode="after")
    def validate_timeout(self) -> "MaterializedGraphNodePlan":
        if self.timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")
        return self


class FrozenGraphPlanePlan(ArchitectureContractModel):
    loop_id: str
    plane: Plane
    nodes: tuple[MaterializedGraphNodePlan, ...] = Field(min_length=1)
    entry_nodes: tuple[GraphLoopEntryDefinition, ...] = Field(min_length=1)
    transitions: tuple[GraphLoopEdgeDefinition, ...] = Field(min_length=1)
    compiled_entries: tuple[CompiledGraphEntryPlan, ...] = Field(min_length=1)
    compiled_completion_entry: CompiledGraphCompletionEntryPlan | None = None
    compiled_transitions: tuple[CompiledGraphTransitionPlan, ...] = Field(min_length=1)
    compiled_resume_policies: tuple[CompiledGraphResumePolicyPlan, ...] = ()
    compiled_threshold_policies: tuple[CompiledGraphThresholdPolicyPlan, ...] = ()
    terminal_states: tuple[GraphLoopTerminalStateDefinition, ...] = Field(min_length=1)
    completion_behavior: GraphLoopCompletionBehaviorDefinition | None = None

    @model_validator(mode="after")
    def validate_plane_alignment(self) -> "FrozenGraphPlanePlan":
        if any(node.plane is not self.plane for node in self.nodes):
            raise ValueError("all graph nodes must belong to graph plane")
        if any(entry.plane is not self.plane for entry in self.compiled_entries):
            raise ValueError("all compiled graph entries must belong to graph plane")
        if self.compiled_completion_entry is not None and self.compiled_completion_entry.plane is not self.plane:
            raise ValueError("compiled completion entry must belong to graph plane")
        if self.completion_behavior is None and self.compiled_completion_entry is not None:
            raise ValueError("compiled completion entry requires completion_behavior")
        if self.completion_behavior is not None:
            if self.compiled_completion_entry is None:
                raise ValueError("graphs with completion_behavior must define compiled completion entry")
            if self.compiled_completion_entry.node_id != self.completion_behavior.target_node_id:
                raise ValueError("compiled completion entry must target completion_behavior.target_node_id")
        return self


class CompiledRunPlan(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["compiled_run_plan"] = "compiled_run_plan"

    compiled_plan_id: str
    mode_id: str
    execution_loop_id: str
    planning_loop_id: str
    execution_graph: FrozenGraphPlanePlan
    planning_graph: FrozenGraphPlanePlan
    compiled_at: datetime
    source_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_graph_planes(self) -> "CompiledRunPlan":
        if self.execution_graph.plane is not Plane.EXECUTION:
            raise ValueError("execution_graph must declare plane=execution")
        if self.planning_graph.plane is not Plane.PLANNING:
            raise ValueError("planning_graph must declare plane=planning")
        if self.execution_graph.loop_id != self.execution_loop_id:
            raise ValueError("execution_loop_id must match execution_graph.loop_id")
        if self.planning_graph.loop_id != self.planning_loop_id:
            raise ValueError("planning_loop_id must match planning_graph.loop_id")
        return self


__all__ = [
    "CompiledGraphCompletionEntryPlan",
    "CompiledGraphEntryPlan",
    "CompiledGraphResumePolicyPlan",
    "CompiledGraphThresholdPolicyPlan",
    "CompiledGraphTransitionPlan",
    "CompiledRunPlan",
    "FrozenGraphPlanePlan",
    "MaterializedGraphNodePlan",
]
