"""Graph topology validation helpers."""

from __future__ import annotations

from collections.abc import Iterable

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    FrozenGraphPlanePlan,
    MaterializedGraphNodePlan,
    RegisteredStageKindDefinition,
    TerminalActionDefinition,
)
from millrace_ai.contracts import Plane

from ..outcomes import CompilerValidationError


def graph_nodes_by_id(
    graphs: Iterable[FrozenGraphPlanePlan],
) -> dict[str, MaterializedGraphNodePlan]:
    nodes_by_id: dict[str, MaterializedGraphNodePlan] = {}
    for graph in graphs:
        for node in graph.nodes:
            nodes_by_id.setdefault(node.node_id, node)
    return nodes_by_id


def validate_graph_terminal_artifact_references(
    *,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
) -> None:
    for graph in graphs_by_plane.values():
        for terminal_state in graph.terminal_states:
            for artifact_id in terminal_state.emits_artifacts:
                if artifact_id not in artifact_contracts_by_id:
                    raise CompilerValidationError(
                        f"graph {graph.loop_id} terminal {terminal_state.terminal_state_id} "
                        f"emits unknown artifact {artifact_id}"
                    )


def validate_structural_graph_smoke(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    terminal_actions_by_id: dict[str, TerminalActionDefinition],
) -> None:
    terminal_classes = {
        getattr(action, "terminal_class")
        for action in terminal_actions_by_id.values()
    }
    for graph in graphs_by_plane.values():
        nodes_by_id = {node.node_id: node for node in graph.nodes}
        routed_outcomes: dict[str, set[str]] = {node.node_id: set() for node in graph.nodes}
        for transition in graph.compiled_transitions:
            if transition.outcome in routed_outcomes[transition.source_node_id]:
                raise CompilerValidationError(
                    f"graph {graph.loop_id} node {transition.source_node_id} has multiple "
                    f"routes for outcome {transition.outcome}"
                )
            routed_outcomes[transition.source_node_id].add(transition.outcome)
            if transition.terminal_state_id is not None:
                terminal_state = next(
                    state
                    for state in graph.terminal_states
                    if state.terminal_state_id == transition.terminal_state_id
                )
                if terminal_state.terminal_class.value not in terminal_classes:
                    raise CompilerValidationError(
                        f"terminal state {terminal_state.terminal_state_id} uses terminal "
                        f"class {terminal_state.terminal_class.value} without a terminal action"
                    )

        for node in graph.nodes:
            stage_kind = stage_kinds[node.stage_kind_id]
            for outcome in stage_kind.legal_outcomes:
                if outcome not in routed_outcomes[node.node_id]:
                    raise CompilerValidationError(
                        f"graph {graph.loop_id} node {node.node_id} has no route for "
                        f"legal outcome {outcome}"
                    )

        for entry in graph.entry_nodes:
            _walk_graph_from_entry(
                graph=graph,
                entry_node_id=entry.node_id,
                nodes_by_id=nodes_by_id,
            )


def _walk_graph_from_entry(
    *,
    graph: FrozenGraphPlanePlan,
    entry_node_id: str,
    nodes_by_id: dict[str, MaterializedGraphNodePlan],
) -> None:
    stack = [entry_node_id]
    seen: set[str] = set()
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        if node_id not in nodes_by_id:
            raise CompilerValidationError(
                f"graph {graph.loop_id} entry walk reached unknown node {node_id}"
            )
        for transition in graph.compiled_transitions:
            if transition.source_node_id != node_id or transition.target_node_id is None:
                continue
            stack.append(transition.target_node_id)


__all__ = [
    "graph_nodes_by_id",
    "validate_graph_terminal_artifact_references",
    "validate_structural_graph_smoke",
]
