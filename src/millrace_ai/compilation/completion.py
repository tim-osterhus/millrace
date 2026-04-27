"""Compiler helpers for graph completion-entry materialization."""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledGraphCompletionEntryPlan,
    GraphLoopDefinition,
    MaterializedGraphNodePlan,
)


def compile_graph_completion_entry(
    *,
    graph_loop: GraphLoopDefinition,
    node_plan_by_id: dict[str, MaterializedGraphNodePlan],
) -> CompiledGraphCompletionEntryPlan | None:
    completion_behavior = graph_loop.completion_behavior
    if completion_behavior is None:
        return None

    node_plan = node_plan_by_id[completion_behavior.target_node_id]
    return CompiledGraphCompletionEntryPlan(
        node_id=completion_behavior.target_node_id,
        stage_kind_id=node_plan.stage_kind_id,
        plane=graph_loop.plane,
        trigger=completion_behavior.trigger,
        readiness_rule=completion_behavior.readiness_rule,
        request_kind=completion_behavior.request_kind,
        target_selector=completion_behavior.target_selector,
        rubric_policy=completion_behavior.rubric_policy,
        blocked_work_policy=completion_behavior.blocked_work_policy,
        skip_if_already_closed=completion_behavior.skip_if_already_closed,
        on_pass_terminal_state_id=completion_behavior.on_pass_terminal_state_id,
        on_gap_terminal_state_id=completion_behavior.on_gap_terminal_state_id,
        create_incident_on_gap=completion_behavior.create_incident_on_gap,
    )


__all__ = ["compile_graph_completion_entry"]
