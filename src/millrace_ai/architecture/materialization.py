"""Materialized non-authoritative frozen graph-plan contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from millrace_ai.contracts import Plane

from .loop_graphs import (
    GraphLoopCompletionBehaviorDefinition,
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


class MaterializedGraphNodePlan(ArchitectureContractModel):
    node_id: str
    stage_kind_id: str
    plane: Plane
    entrypoint_path: str
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
    compiled_transitions: tuple[CompiledGraphTransitionPlan, ...] = Field(min_length=1)
    terminal_states: tuple[GraphLoopTerminalStateDefinition, ...] = Field(min_length=1)
    completion_behavior: GraphLoopCompletionBehaviorDefinition | None = None

    @model_validator(mode="after")
    def validate_plane_alignment(self) -> "FrozenGraphPlanePlan":
        if any(node.plane is not self.plane for node in self.nodes):
            raise ValueError("all graph nodes must belong to graph plane")
        if any(entry.plane is not self.plane for entry in self.compiled_entries):
            raise ValueError("all compiled graph entries must belong to graph plane")
        return self


class FrozenGraphRunPlan(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["frozen_graph_run_plan"] = "frozen_graph_run_plan"

    compiled_plan_id: str
    mode_id: str
    authoritative_for_runtime_execution: bool = False
    legacy_equivalence_ready_for_cutover: bool = False
    legacy_equivalence_issues: tuple[str, ...] = ()
    execution_graph: FrozenGraphPlanePlan
    planning_graph: FrozenGraphPlanePlan
    compiled_at: datetime
    source_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_graph_planes(self) -> "FrozenGraphRunPlan":
        if self.execution_graph.plane is not Plane.EXECUTION:
            raise ValueError("execution_graph must declare plane=execution")
        if self.planning_graph.plane is not Plane.PLANNING:
            raise ValueError("planning_graph must declare plane=planning")
        if self.legacy_equivalence_ready_for_cutover and self.legacy_equivalence_issues:
            raise ValueError("cutover-ready graph plans may not retain legacy equivalence issues")
        return self


__all__ = [
    "CompiledGraphEntryPlan",
    "CompiledGraphTransitionPlan",
    "FrozenGraphPlanePlan",
    "FrozenGraphRunPlan",
    "MaterializedGraphNodePlan",
]
