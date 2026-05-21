"""Compiled-graph activation decisions."""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledGraphCompletionEntryPlan,
    CompiledRunPlan,
    FrozenGraphPlanePlan,
)
from millrace_ai.architecture.loop_graphs import graph_loop_entry_key_value
from millrace_ai.contracts import LearningStageName, Plane, WorkItemKind
from millrace_ai.contracts.work_refs import family_id_for_work_item_kind, normalize_work_item_family_id

from .models import GraphActivationDecision
from .stage_mapping import stage_for_stage_kind


def work_item_activation_for_graph(
    graph_plan: CompiledRunPlan,
    work_item: WorkItemKind | str,
) -> GraphActivationDecision:
    family_id = (
        family_id_for_work_item_kind(work_item)
        if isinstance(work_item, WorkItemKind)
        else normalize_work_item_family_id(work_item, field_name="family_id")
    )
    if family_id is None:
        raise ValueError("work item family id is required")
    family = graph_plan.work_item_families_by_id.get(family_id)
    if family is None:
        raise ValueError(f"compiled graph is missing work item family `{family_id}`")
    entry_key = family.entry_key
    graph = _graph_for_plane(graph_plan, family.plane)
    return activation_from_entry(graph, entry_key)


def learning_stage_activation_for_graph(
    graph_plan: CompiledRunPlan,
    target_stage: LearningStageName,
) -> GraphActivationDecision:
    if graph_plan.learning_graph is None:
        raise ValueError("compiled graph is missing learning plane")
    for node in graph_plan.learning_graph.nodes:
        if node.stage_kind_id == target_stage.value:
            return activation_from_node(
                graph_plan.learning_graph,
                node.node_id,
                entry_key="learning_request",
            )
    raise ValueError(f"compiled graph is missing learning stage kind `{target_stage.value}`")


def completion_activation_for_graph(graph_plan: CompiledRunPlan) -> GraphActivationDecision:
    completion_entry = graph_plan.planning_graph.compiled_completion_entry
    if completion_entry is None:
        raise ValueError("compiled graph is missing closure_target completion entry")
    return activation_from_completion_entry(graph_plan.planning_graph, completion_entry)


def activation_from_entry(
    graph: FrozenGraphPlanePlan,
    entry_key: str,
) -> GraphActivationDecision:
    for entry in graph.compiled_entries:
        compiled_entry_key = graph_loop_entry_key_value(entry.entry_key)
        if compiled_entry_key == entry_key:
            return GraphActivationDecision(
                plane=graph.plane,
                stage=stage_for_stage_kind(graph.plane, entry.stage_kind_id),
                node_id=entry.node_id,
                stage_kind_id=entry.stage_kind_id,
                entry_key=compiled_entry_key,
            )
    raise ValueError(f"compiled graph is missing `{entry_key}` activation entry")


def activation_from_node(
    graph: FrozenGraphPlanePlan,
    node_id: str,
    *,
    entry_key: str,
) -> GraphActivationDecision:
    for node in graph.nodes:
        if node.node_id == node_id:
            return GraphActivationDecision(
                plane=graph.plane,
                stage=stage_for_stage_kind(graph.plane, node.stage_kind_id),
                node_id=node.node_id,
                stage_kind_id=node.stage_kind_id,
                entry_key=entry_key,
            )
    raise ValueError(f"compiled graph is missing `{node_id}` node")


def activation_from_completion_entry(
    graph: FrozenGraphPlanePlan,
    completion_entry: CompiledGraphCompletionEntryPlan,
) -> GraphActivationDecision:
    return GraphActivationDecision(
        plane=graph.plane,
        stage=stage_for_stage_kind(graph.plane, completion_entry.stage_kind_id),
        node_id=completion_entry.node_id,
        stage_kind_id=completion_entry.stage_kind_id,
        entry_key=graph_loop_entry_key_value(completion_entry.entry_key),
    )


def _graph_for_plane(graph_plan: CompiledRunPlan, plane: Plane) -> FrozenGraphPlanePlan:
    if plane is Plane.EXECUTION:
        return graph_plan.execution_graph
    if plane is Plane.PLANNING:
        return graph_plan.planning_graph
    if plane is Plane.LEARNING and graph_plan.learning_graph is not None:
        return graph_plan.learning_graph
    graph = graph_plan.graphs_by_plane.get(plane)
    if graph is None:
        raise ValueError(f"compiled graph is missing {plane.value} plane")
    return graph


__all__ = [
    "activation_from_completion_entry",
    "activation_from_entry",
    "activation_from_node",
    "completion_activation_for_graph",
    "learning_stage_activation_for_graph",
    "work_item_activation_for_graph",
]
