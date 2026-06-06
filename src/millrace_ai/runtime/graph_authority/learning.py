"""Learning-plane compiled graph routing.

Compatibility surface — the learning plane's graph routing is not yet
migrated through the generic multi-plane router in ``routing.py`` because
the learning plane has no threshold or resume policies. This module remains
a standalone implementation with no active dispatch dependency on legacy
plane-specific branching.

All public exports are preserved for import compatibility.
"""

from __future__ import annotations

from millrace_ai.architecture import CompiledRunPlan, FrozenGraphPlanePlan
from millrace_ai.architecture.loop_graphs import GraphLoopTerminalClass
from millrace_ai.contracts import LearningStageName, Plane, RecoveryCounters, RuntimeSnapshot, StageResultEnvelope
from millrace_ai.contracts.terminal_outcomes import terminal_outcome_value
from millrace_ai.router import RouterAction, RouterDecision

from .counters import normalize_failure_class
from .policies import terminal_state_by_id, transition_for_source
from .stage_mapping import node_plan_by_id, stage_for_node
from .terminal_actions import decision_from_terminal_state_action


def route_learning_stage_result_from_graph(
    graph_plan: CompiledRunPlan,
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters | None = None,
    **kwargs: object,
) -> RouterDecision:
    source_stage = LearningStageName(stage_result.stage)
    outcome = terminal_outcome_value(stage_result.terminal_result)
    transition = transition_for_source(graph, source_node_id=stage_result.node_id, outcome=outcome)

    if transition.target_node_id is not None:
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=Plane.LEARNING,
            next_stage=stage_for_node(graph, transition.target_node_id),
            next_node_id=transition.target_node_id,
            next_stage_kind_id=node_plan_by_id(graph, transition.target_node_id).stage_kind_id,
            reason=f"{source_stage.value}:{outcome}",
        )

    terminal_state_id = transition.terminal_state_id
    assert terminal_state_id is not None
    terminal_state = terminal_state_by_id(graph, terminal_state_id)
    return decision_from_terminal_state_action(
        graph_plan.graphs_by_plane,
        graph=graph,
        terminal_state=terminal_state,
        terminal_actions_by_id=graph_plan.terminal_actions_by_id,
        lifecycle_mutation_plans_by_id=graph_plan.lifecycle_mutation_plans_by_id,
        reason=_learning_terminal_reason(
            source_stage,
            outcome,
            terminal_state.writes_status,
            terminal_state.terminal_class,
            terminal_state.router_reason,
        ),
        failure_class=_learning_terminal_failure_class(
            source_stage,
            stage_result,
            terminal_state.terminal_class,
            terminal_state.failure_class_template,
        ),
    )


def _learning_terminal_reason(
    source_stage: LearningStageName,
    outcome: str,
    writes_status: str,
    terminal_class: GraphLoopTerminalClass,
    router_reason: str | None,
) -> str:
    if router_reason is not None and outcome == writes_status:
        return router_reason
    if terminal_class is GraphLoopTerminalClass.BLOCKED:
        return f"{source_stage.value}_blocked"
    return f"{source_stage.value}:{outcome}"


def _learning_terminal_failure_class(
    source_stage: LearningStageName,
    stage_result: StageResultEnvelope,
    terminal_class: GraphLoopTerminalClass,
    failure_class_template: str | None,
) -> str | None:
    if terminal_class is not GraphLoopTerminalClass.BLOCKED:
        return None
    metadata_failure_class = stage_result.metadata.get("failure_class")
    if isinstance(metadata_failure_class, str) and metadata_failure_class.strip():
        return normalize_failure_class(metadata_failure_class)
    return normalize_failure_class(failure_class_template or f"{source_stage.value}_blocked")


__all__ = ["route_learning_stage_result_from_graph"]
