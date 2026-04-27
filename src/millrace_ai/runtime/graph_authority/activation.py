"""Compiled-graph activation decisions."""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledGraphCompletionEntryPlan,
    CompiledRunPlan,
    FrozenGraphPlanePlan,
)
from millrace_ai.contracts import LearningStageName, WorkItemKind

from .models import GraphActivationDecision
from .stage_mapping import entry_key_for_work_item_kind, stage_for_stage_kind


def work_item_activation_for_graph(
    graph_plan: CompiledRunPlan,
    work_item_kind: WorkItemKind,
) -> GraphActivationDecision:
    entry_key = entry_key_for_work_item_kind(work_item_kind)
    if work_item_kind is WorkItemKind.TASK:
        graph = graph_plan.execution_graph
    elif work_item_kind is WorkItemKind.LEARNING_REQUEST:
        if graph_plan.learning_graph is None:
            raise ValueError("compiled graph is missing learning plane")
        graph = graph_plan.learning_graph
    else:
        graph = graph_plan.planning_graph
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
        if entry.entry_key.value == entry_key:
            return GraphActivationDecision(
                plane=graph.plane,
                stage=stage_for_stage_kind(graph.plane, entry.stage_kind_id),
                node_id=entry.node_id,
                stage_kind_id=entry.stage_kind_id,
                entry_key=entry.entry_key.value,
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
        entry_key=completion_entry.entry_key.value,
    )


__all__ = [
    "activation_from_completion_entry",
    "activation_from_entry",
    "activation_from_node",
    "completion_activation_for_graph",
    "learning_stage_activation_for_graph",
    "work_item_activation_for_graph",
]
