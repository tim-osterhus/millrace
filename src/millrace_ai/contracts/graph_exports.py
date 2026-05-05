"""Public compiled-stage-graph export contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .base import ContractModel
from .enums import Plane, ResultClass


class GraphExportNode(ContractModel):
    node_id: str
    plane: Plane
    stage_kind_id: str
    entrypoint_path: str
    entrypoint_contract_id: str | None = None
    running_status_marker: str
    required_skill_paths: tuple[str, ...] = ()
    attached_skill_additions: tuple[str, ...] = ()
    runner_name: str | None = None
    model_name: str | None = None
    thinking_level: str | None = None
    model_reasoning_effort: str | None = None
    timeout_seconds: int = 0
    allowed_result_classes_by_outcome: dict[str, tuple[ResultClass, ...]]
    declared_output_artifacts: tuple[str, ...] = ()


class GraphExportEdge(ContractModel):
    edge_id: str
    source_node_id: str
    outcome: str
    target_node_id: str | None = None
    terminal_state_id: str | None = None
    kind: str
    priority: int
    max_attempts: int | None = None


class GraphExportEntry(ContractModel):
    entry_key: str
    node_id: str
    stage_kind_id: str
    plane: Plane


class GraphExportTerminalState(ContractModel):
    terminal_state_id: str
    terminal_class: str
    writes_status: str
    emits_artifacts: tuple[str, ...] = ()
    ends_plane_run: bool = True


class CompiledStageGraphExport(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["compiled_stage_graph"] = "compiled_stage_graph"
    compiled_plan_id: str
    mode_id: str
    loop_id: str
    plane: Plane
    nodes: tuple[GraphExportNode, ...]
    edges: tuple[GraphExportEdge, ...]
    entries: tuple[GraphExportEntry, ...]
    terminal_states: tuple[GraphExportTerminalState, ...]
    source_refs: tuple[str, ...] = ()
    exported_at: datetime


__all__ = [
    "CompiledStageGraphExport",
    "GraphExportEdge",
    "GraphExportEntry",
    "GraphExportNode",
    "GraphExportTerminalState",
]
