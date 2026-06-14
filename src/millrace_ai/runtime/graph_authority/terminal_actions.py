"""Terminal-state and terminal-action router decisions."""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledRunPlan,
    FrozenGraphPlanePlan,
    GraphLoopTerminalStateDefinition,
    LifecycleMutationPlanDefinition,
    TerminalActionDefinition,
)
from millrace_ai.contracts import Plane
from millrace_ai.contracts.router import RouterAction, RouterDecision

from .stage_mapping import stage_for_node


def decision_from_terminal_state_action(
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    *,
    graph: FrozenGraphPlanePlan,
    terminal_state: GraphLoopTerminalStateDefinition,
    terminal_actions_by_id: dict[str, TerminalActionDefinition],
    lifecycle_mutation_plans_by_id: dict[str, LifecycleMutationPlanDefinition],
    reason: str,
    failure_class: str | None = None,
    recovery_counter_name: str | None = None,
    counter_key: str | None = None,
    router_consequence_override: str | None = None,
    handoff_plane_override: Plane | None = None,
    handoff_entry_key_override: str | None = None,
    create_incident_override: bool | None = None,
) -> RouterDecision:
    """Resolve a compiled terminal state through its declared terminal action."""

    terminal_action_id = terminal_state.terminal_action_id
    if terminal_action_id is None:
        raise ValueError(f"terminal state {terminal_state.terminal_state_id} lacks terminal_action_id")
    terminal_action = terminal_actions_by_id.get(terminal_action_id)
    if terminal_action is None:
        raise ValueError(f"compiled plan is missing terminal action `{terminal_action_id}`")
    if terminal_action.terminal_class != terminal_state.terminal_class.value:
        raise ValueError(
            "terminal state/action class mismatch for "
            f"{terminal_state.terminal_state_id}:{terminal_action_id}"
        )

    metadata = _terminal_metadata(
        terminal_state,
        terminal_action,
        lifecycle_mutation_plans_by_id,
    )
    router_consequence = router_consequence_override or terminal_action.router_consequence
    effective_failure_class = failure_class or terminal_action.failure_class
    if router_consequence == "idle":
        return RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason=reason,
            failure_class=effective_failure_class,
            counter_mutation_name=recovery_counter_name,
            recovery_counter_name=recovery_counter_name,
            counter_key=counter_key,
            **metadata,
        )
    if router_consequence == "blocked":
        return RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason=reason,
            failure_class=effective_failure_class,
            counter_mutation_name=recovery_counter_name,
            recovery_counter_name=recovery_counter_name,
            counter_key=counter_key,
            **metadata,
        )
    if router_consequence == "handoff":
        handoff_plane = handoff_plane_override or terminal_action.handoff_plane
        handoff_entry_key = handoff_entry_key_override or terminal_action.handoff_entry_key
        if handoff_plane is None or handoff_entry_key is None:
            raise ValueError(f"handoff terminal action {terminal_action_id} lacks handoff target")
        handoff_graph = graphs_by_plane.get(handoff_plane)
        if handoff_graph is None:
            raise ValueError(f"compiled plan is missing handoff plane {handoff_plane.value}")
        target_node_id = _entry_node_id(handoff_graph, handoff_entry_key)
        return RouterDecision(
            action=RouterAction.HANDOFF,
            next_plane=handoff_plane,
            next_stage=stage_for_node(handoff_graph, target_node_id),
            next_node_id=target_node_id,
            next_stage_kind_id=_stage_kind_id(handoff_graph, target_node_id),
            reason=reason,
            failure_class=effective_failure_class,
            counter_mutation_name=recovery_counter_name,
            recovery_counter_name=recovery_counter_name,
            counter_key=counter_key,
            create_incident=(
                terminal_action.create_incident
                if create_incident_override is None
                else create_incident_override
            ),
            **metadata,
        )
    raise ValueError(
        f"unsupported terminal action router consequence: {router_consequence}"
    )


def decision_from_runtime_failure_recovery_exhaustion(
    compiled_plan: CompiledRunPlan | None,
    plane: Plane,
    *,
    reason: str,
    failure_class: str | None = None,
) -> RouterDecision | None:
    """Resolve runtime-failure exhaustion through a graph terminal state when declared."""

    if compiled_plan is None:
        return None
    graph = compiled_plan.graphs_by_plane.get(plane)
    if graph is None or graph.runtime_failure_recovery is None:
        return None
    terminal_state_id = graph.runtime_failure_recovery.exhausted_terminal_state_id
    if terminal_state_id is None:
        return None
    return decision_from_terminal_state_action(
        compiled_plan.graphs_by_plane,
        graph=graph,
        terminal_state=_terminal_state_by_id(graph, terminal_state_id),
        terminal_actions_by_id=compiled_plan.terminal_actions_by_id,
        lifecycle_mutation_plans_by_id=compiled_plan.lifecycle_mutation_plans_by_id,
        reason=reason,
        failure_class=failure_class,
    )


def _terminal_metadata(
    terminal_state: GraphLoopTerminalStateDefinition,
    terminal_action: TerminalActionDefinition,
    lifecycle_mutation_plans_by_id: dict[str, LifecycleMutationPlanDefinition],
) -> dict[str, str | bool | None]:
    lifecycle_plan_id = terminal_action.lifecycle_mutation_plan_id
    lifecycle_plan = (
        lifecycle_mutation_plans_by_id.get(lifecycle_plan_id)
        if lifecycle_plan_id is not None
        else None
    )
    return {
        "terminal_state_id": terminal_state.terminal_state_id,
        "terminal_action_id": terminal_action.terminal_action_id,
        "terminal_action_router_consequence": terminal_action.router_consequence,
        "terminal_action_non_mutating": terminal_action.non_mutating,
        "runtime_operation_id": terminal_action.runtime_operation_id,
        "lifecycle_mutation_plan_id": lifecycle_plan_id,
        "lifecycle_action_id": lifecycle_plan.lifecycle_action_id if lifecycle_plan is not None else None,
        "terminal_writes_status": terminal_state.writes_status,
    }


def _entry_node_id(graph: FrozenGraphPlanePlan, entry_key: str) -> str:
    for entry in graph.compiled_entries:
        if entry.entry_key == entry_key:
            return entry.node_id
    raise ValueError(f"compiled graph is missing entry key `{entry_key}`")


def _terminal_state_by_id(
    graph: FrozenGraphPlanePlan,
    terminal_state_id: str,
) -> GraphLoopTerminalStateDefinition:
    for terminal_state in graph.terminal_states:
        if terminal_state.terminal_state_id == terminal_state_id:
            return terminal_state
    raise ValueError(f"compiled graph is missing terminal state `{terminal_state_id}`")


def _stage_kind_id(graph: FrozenGraphPlanePlan, node_id: str) -> str:
    for node in graph.nodes:
        if node.node_id == node_id:
            return node.stage_kind_id
    raise ValueError(f"compiled graph is missing node `{node_id}`")


__all__ = [
    "decision_from_runtime_failure_recovery_exhaustion",
    "decision_from_terminal_state_action",
]
