"""Projection helpers for public compiled-stage-graph exports."""

from __future__ import annotations

from datetime import datetime, timezone

from millrace_ai.architecture import CompiledRunPlan, FrozenGraphPlanePlan
from millrace_ai.contracts import Plane
from millrace_ai.contracts.graph_exports import (
    CompiledStageGraphExport,
    GraphExportEdge,
    GraphExportEntry,
    GraphExportNode,
    GraphExportTerminalState,
)


def export_compiled_stage_graphs(
    plan: CompiledRunPlan,
) -> tuple[CompiledStageGraphExport, ...]:
    """Project every selected plane graph from a compiled plan."""

    return tuple(
        export_compiled_stage_graph(plan, plane)
        for plane in sorted(plan.graphs_by_plane, key=lambda item: item.value)
    )


def export_compiled_stage_graph(
    plan: CompiledRunPlan,
    plane: Plane,
) -> CompiledStageGraphExport:
    """Project one selected plane graph from a compiled plan."""

    try:
        graph = plan.graphs_by_plane[plane]
    except KeyError as exc:
        raise ValueError(f"compiled plan does not include plane: {plane.value}") from exc
    return _export_graph(plan, graph)


def _export_graph(
    plan: CompiledRunPlan,
    graph: FrozenGraphPlanePlan,
) -> CompiledStageGraphExport:
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    return CompiledStageGraphExport(
        compiled_plan_id=plan.compiled_plan_id,
        mode_id=plan.mode_id,
        loop_id=graph.loop_id,
        plane=graph.plane,
        nodes=tuple(
            GraphExportNode(
                node_id=node.node_id,
                plane=node.plane,
                stage_kind_id=node.stage_kind_id,
                entrypoint_path=node.entrypoint_path,
                entrypoint_contract_id=node.entrypoint_contract_id,
                running_status_marker=node.running_status_marker,
                required_skill_paths=node.required_skill_paths,
                attached_skill_additions=node.attached_skill_additions,
                runner_name=node.runner_name,
                model_name=node.model_name,
                thinking_level=node.thinking_level,
                model_reasoning_effort=node.model_reasoning_effort,
                timeout_seconds=node.timeout_seconds,
                allowed_result_classes_by_outcome=node.allowed_result_classes_by_outcome,
                declared_output_artifacts=node.declared_output_artifacts,
            )
            for node in graph.nodes
        ),
        edges=tuple(
            GraphExportEdge(
                edge_id=edge.edge_id,
                source_node_id=edge.source_node_id,
                outcome=edge.outcome,
                target_node_id=edge.target_node_id,
                terminal_state_id=edge.terminal_state_id,
                kind=edge.kind.value,
                priority=edge.priority,
                max_attempts=edge.max_attempts,
            )
            for edge in graph.compiled_transitions
        ),
        entries=tuple(
            GraphExportEntry(
                entry_key=entry.entry_key.value,
                node_id=entry.node_id,
                stage_kind_id=entry.stage_kind_id,
                plane=entry.plane,
            )
            for entry in graph.compiled_entries
            if entry.node_id in nodes_by_id
        ),
        terminal_states=tuple(
            GraphExportTerminalState(
                terminal_state_id=state.terminal_state_id,
                terminal_class=state.terminal_class.value,
                writes_status=state.writes_status,
                emits_artifacts=state.emits_artifacts,
                ends_plane_run=state.ends_plane_run,
            )
            for state in graph.terminal_states
        ),
        source_refs=plan.source_refs,
        exported_at=datetime.now(timezone.utc),
    )


__all__ = ["export_compiled_stage_graph", "export_compiled_stage_graphs"]
