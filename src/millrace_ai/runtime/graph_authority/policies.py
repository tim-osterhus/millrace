"""Compiled graph policy and transition helpers."""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledGraphResumePolicyPlan,
    CompiledGraphThresholdPolicyPlan,
    CompiledGraphTransitionPlan,
    FrozenGraphPlanePlan,
    GraphLoopTerminalStateDefinition,
    LifecycleMutationPlanDefinition,
    TerminalActionDefinition,
)
from millrace_ai.contracts import ExecutionStageName, PlanningStageName, RuntimeSnapshot, StageResultEnvelope
from millrace_ai.contracts.router import RouterAction, RouterDecision

from .counters import counter_key_from_snapshot
from .stage_mapping import node_plan_by_id, stage_for_node
from .terminal_actions import decision_from_terminal_state_action


def decision_from_resume_policy(
    graph: FrozenGraphPlanePlan,
    *,
    source_stage: ExecutionStageName | PlanningStageName,
    stage_result: StageResultEnvelope,
    policy: CompiledGraphResumePolicyPlan,
) -> RouterDecision:
    target_node_id = policy.default_target_node_id
    valid_node_ids = {node.node_id for node in graph.nodes}
    for metadata_key in policy.metadata_stage_keys:
        candidate = stage_result.metadata.get(metadata_key)
        if not isinstance(candidate, str):
            continue
        normalized = candidate.strip().lower()
        if not normalized or normalized in policy.disallowed_target_node_ids:
            continue
        if normalized not in valid_node_ids:
            continue
        target_node_id = normalized
        break

    return RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=graph.plane,
        next_stage=stage_for_node(graph, target_node_id),
        next_node_id=target_node_id,
        next_stage_kind_id=node_plan_by_id(graph, target_node_id).stage_kind_id,
        reason=policy.route_reason or f"{stage_result.node_id}:{policy.on_outcome}",
    )


def decision_from_threshold_resolution(
    graphs_by_plane: dict,
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    *,
    source_stage: ExecutionStageName | PlanningStageName,
    policy: CompiledGraphThresholdPolicyPlan,
    terminal_actions_by_id: dict[str, TerminalActionDefinition],
    lifecycle_mutation_plans_by_id: dict[str, LifecycleMutationPlanDefinition],
    failure_class: str,
    reason: str,
) -> RouterDecision:
    counter_key = counter_key_from_snapshot(snapshot, failure_class)
    counter_mutation_name = _policy_exhausted_counter_mutation_name(policy)
    decision_reason = policy.exhausted_route_reason or reason
    if policy.exhausted_target_node_id is not None:
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=stage_for_node(graph, policy.exhausted_target_node_id),
            next_node_id=policy.exhausted_target_node_id,
            next_stage_kind_id=node_plan_by_id(graph, policy.exhausted_target_node_id).stage_kind_id,
            reason=decision_reason,
            failure_class=failure_class,
            counter_mutation_name=counter_mutation_name,
            recovery_counter_name=counter_mutation_name,
            counter_key=counter_key,
        )

    assert policy.exhausted_terminal_state_id is not None
    terminal_state = terminal_state_by_id(graph, policy.exhausted_terminal_state_id)
    return decision_from_terminal_state_action(
        graphs_by_plane,
        graph=graph,
        terminal_state=terminal_state,
        terminal_actions_by_id=terminal_actions_by_id,
        lifecycle_mutation_plans_by_id=lifecycle_mutation_plans_by_id,
        reason=decision_reason,
        failure_class=failure_class,
        recovery_counter_name=counter_mutation_name,
        counter_key=counter_key,
    )


def _policy_counter_mutation_name(policy: CompiledGraphThresholdPolicyPlan) -> str | None:
    counter_name = policy.recovery_counter_mutation_name or policy.counter_name
    return counter_name.value


def _policy_exhausted_counter_mutation_name(
    policy: CompiledGraphThresholdPolicyPlan,
) -> str | None:
    counter_name = policy.exhausted_counter_mutation_name or policy.recovery_counter_mutation_name
    if counter_name is None:
        return None
    return counter_name.value


def transition_for_source(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    outcome: str,
) -> CompiledGraphTransitionPlan:
    for transition in graph.compiled_transitions:
        if transition.source_node_id == source_node_id and transition.outcome == outcome:
            return transition
    raise ValueError(f"compiled graph is missing transition for {source_node_id}:{outcome}")


def resume_policy_for_source(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    outcome: str,
) -> CompiledGraphResumePolicyPlan | None:
    for policy in graph.compiled_resume_policies:
        if policy.source_node_id == source_node_id and policy.on_outcome == outcome:
            return policy
    return None


def threshold_policy_for_source(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    outcome: str,
    counter_name: str,
    threshold: int,
) -> CompiledGraphThresholdPolicyPlan:
    for policy in graph.compiled_threshold_policies:
        if (
            source_node_id in policy.source_node_ids
            and policy.on_outcome == outcome
            and policy.counter_name.value == counter_name
            and policy.threshold == threshold
        ):
            return policy
    raise ValueError(
        "compiled graph is missing threshold policy for "
        f"{source_node_id}:{outcome}:{counter_name}:{threshold}"
    )


def threshold_policy_for_transition(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    outcome: str,
) -> CompiledGraphThresholdPolicyPlan | None:
    for policy in graph.compiled_threshold_policies:
        if source_node_id in policy.source_node_ids and policy.on_outcome == outcome:
            return policy
    return None


def terminal_state_by_id(
    graph: FrozenGraphPlanePlan,
    terminal_state_id: str,
) -> GraphLoopTerminalStateDefinition:
    for terminal_state in graph.terminal_states:
        if terminal_state.terminal_state_id == terminal_state_id:
            return terminal_state
    raise ValueError(f"compiled graph is missing terminal state `{terminal_state_id}`")


__all__ = [
    "decision_from_resume_policy",
    "decision_from_threshold_resolution",
    "resume_policy_for_source",
    "terminal_state_by_id",
    "threshold_policy_for_source",
    "threshold_policy_for_transition",
    "transition_for_source",
]
