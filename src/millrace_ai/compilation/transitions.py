"""Compiler helpers for graph transition materialization."""

from __future__ import annotations

from millrace_ai.architecture import CompiledGraphTransitionPlan, GraphLoopEdgeDefinition


def compile_graph_transitions(
    edges: tuple[GraphLoopEdgeDefinition, ...],
) -> tuple[CompiledGraphTransitionPlan, ...]:
    compiled: list[CompiledGraphTransitionPlan] = []
    for edge in edges:
        for outcome in edge.on_outcomes:
            compiled.append(
                CompiledGraphTransitionPlan(
                    edge_id=edge.edge_id,
                    source_node_id=edge.from_node_id,
                    outcome=outcome,
                    target_node_id=edge.to_node_id,
                    terminal_state_id=edge.terminal_state_id,
                    kind=edge.kind,
                    priority=edge.priority,
                    max_attempts=edge.max_attempts,
                )
            )
    return tuple(compiled)


__all__ = ["compile_graph_transitions"]
