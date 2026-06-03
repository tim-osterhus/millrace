"""Planning-plane compiled graph routing."""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledGraphThresholdPolicyPlan,
    CompiledGraphTransitionPlan,
    CompiledRunPlan,
    FrozenGraphPlanePlan,
)
from millrace_ai.architecture.loop_graphs import GraphLoopTerminalClass
from millrace_ai.contracts import (
    Plane,
    PlanningStageName,
    RecoveryCounters,
    RuntimeSnapshot,
    StageResultEnvelope,
)
from millrace_ai.contracts.terminal_outcomes import terminal_outcome_value
from millrace_ai.router import RouterAction, RouterDecision

from .counters import counter_attempts_for_name, counter_key_from_snapshot, resolve_failure_class
from .policies import (
    decision_from_resume_policy,
    decision_from_threshold_resolution,
    resume_policy_for_source,
    terminal_state_by_id,
    threshold_policy_for_transition,
    transition_for_source,
)
from .stage_mapping import node_plan_by_id, stage_for_node
from .terminal_actions import decision_from_terminal_state_action
from .validation import validate_stage_result_matches_snapshot


def route_planning_stage_result_from_graph(
    graph_plan: CompiledRunPlan,
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    *,
    max_mechanic_attempts: int,
) -> RouterDecision:
    validate_stage_result_matches_snapshot(snapshot, stage_result, expected_plane=Plane.PLANNING)
    source_stage = PlanningStageName(stage_result.stage)
    outcome = terminal_outcome_value(stage_result.terminal_result)
    source_node_id = stage_result.node_id

    threshold_policy = threshold_policy_for_transition(
        graph,
        source_node_id=source_node_id,
        outcome=outcome,
    )
    if threshold_policy is not None:
        failure_class = resolve_failure_class(
            snapshot,
            stage_result,
            default=_threshold_failure_class_default(
                source_stage,
                threshold_policy,
                exhausted=False,
            ),
        )
        attempts = counter_attempts_for_name(
            snapshot,
            counters,
            failure_class,
            counter_name=threshold_policy.counter_name,
        )
        if attempts >= threshold_policy.threshold:
            return decision_from_threshold_resolution(
                graph_plan.graphs_by_plane,
                graph,
                snapshot,
                source_stage=source_stage,
                policy=threshold_policy,
                terminal_actions_by_id=graph_plan.terminal_actions_by_id,
                lifecycle_mutation_plans_by_id=graph_plan.lifecycle_mutation_plans_by_id,
                failure_class=resolve_failure_class(
                    snapshot,
                    stage_result,
                    default=_threshold_failure_class_default(
                        source_stage,
                        threshold_policy,
                        exhausted=True,
                    ),
                ),
                reason=_threshold_reason(source_stage, threshold_policy, exhausted=True),
            )

    resume_policy = resume_policy_for_source(
        graph,
        source_node_id=source_node_id,
        outcome=outcome,
    )
    if resume_policy is not None:
        return decision_from_resume_policy(
            graph,
            source_stage=source_stage,
            stage_result=stage_result,
            policy=resume_policy,
        )

    transition = transition_for_source(graph, source_node_id=source_node_id, outcome=outcome)
    return decision_from_planning_transition(
        graph_plan,
        graph,
        snapshot,
        source_stage=source_stage,
        stage_result=stage_result,
        transition=transition,
        threshold_policy=threshold_policy,
    )


def decision_from_planning_transition(
    graph_plan: CompiledRunPlan,
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    *,
    source_stage: PlanningStageName,
    stage_result: StageResultEnvelope,
    transition: CompiledGraphTransitionPlan,
    threshold_policy: CompiledGraphThresholdPolicyPlan | None = None,
) -> RouterDecision:
    terminal_result = terminal_outcome_value(stage_result.terminal_result)

    if transition.target_node_id is not None:
        if threshold_policy is not None:
            failure_class = resolve_failure_class(
                snapshot,
                stage_result,
                default=_threshold_failure_class_default(
                    source_stage,
                    threshold_policy,
                    exhausted=False,
                ),
            )
            counter_mutation_name = _threshold_counter_mutation_name(threshold_policy)
            return RouterDecision(
                action=RouterAction.RUN_STAGE,
                next_plane=graph.plane,
                next_stage=stage_for_node(graph, transition.target_node_id),
                next_node_id=transition.target_node_id,
                next_stage_kind_id=node_plan_by_id(graph, transition.target_node_id).stage_kind_id,
                reason=_threshold_reason(source_stage, threshold_policy, exhausted=False),
                failure_class=failure_class,
                counter_key=counter_key_from_snapshot(snapshot, failure_class),
                counter_mutation_name=counter_mutation_name,
                recovery_counter_name=counter_mutation_name,
            )
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=stage_for_node(graph, transition.target_node_id),
            next_node_id=transition.target_node_id,
            next_stage_kind_id=node_plan_by_id(graph, transition.target_node_id).stage_kind_id,
            reason=f"{source_stage.value}:{terminal_result}",
        )

    terminal_state_id = transition.terminal_state_id
    assert terminal_state_id is not None
    terminal_state = terminal_state_by_id(graph, terminal_state_id)

    failure_class = _planning_terminal_failure_class(
        snapshot,
        stage_result,
        source_stage=source_stage,
        terminal_result=terminal_result,
        terminal_class=terminal_state.terminal_class,
        failure_class_template=terminal_state.failure_class_template,
    )
    return decision_from_terminal_state_action(
        graph_plan.graphs_by_plane,
        graph=graph,
        terminal_state=terminal_state,
        terminal_actions_by_id=graph_plan.terminal_actions_by_id,
        lifecycle_mutation_plans_by_id=graph_plan.lifecycle_mutation_plans_by_id,
        reason=_planning_terminal_reason(
            stage_result,
            terminal_result,
            terminal_state.terminal_state_id,
            terminal_state.writes_status,
            terminal_state.router_reason,
        ),
        failure_class=failure_class,
    )


def _planning_terminal_reason(
    stage_result: StageResultEnvelope,
    terminal_result: str,
    terminal_state_id: str,
    writes_status: str,
    router_reason: str | None,
) -> str:
    if router_reason is not None:
        return router_reason
    if terminal_result == writes_status:
        return terminal_state_id
    return f"{stage_result.node_id}:{terminal_result}"


def _planning_terminal_failure_class(
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    *,
    source_stage: PlanningStageName,
    terminal_result: str,
    terminal_class: GraphLoopTerminalClass,
    failure_class_template: str | None,
) -> str | None:
    if terminal_class is GraphLoopTerminalClass.BLOCKED:
        return resolve_failure_class(
            snapshot,
            stage_result,
            default=failure_class_template or f"{stage_result.stage_kind_id}_blocked",
        )
    return None


def _threshold_failure_class_default(
    source_stage: PlanningStageName,
    threshold_policy: CompiledGraphThresholdPolicyPlan,
    *,
    exhausted: bool,
) -> str:
    policy_default = (
        threshold_policy.exhausted_failure_class_template
        if exhausted
        else threshold_policy.default_failure_class_template
    )
    if policy_default is not None:
        return policy_default
    return f"{source_stage.value}_{threshold_policy.on_outcome.lower()}"


def _threshold_reason(
    source_stage: PlanningStageName,
    threshold_policy: CompiledGraphThresholdPolicyPlan,
    *,
    exhausted: bool,
) -> str:
    policy_reason = (
        threshold_policy.exhausted_route_reason
        if exhausted
        else threshold_policy.route_reason
    )
    if policy_reason is not None:
        return policy_reason
    reason = f"{source_stage.value}_{threshold_policy.on_outcome.lower()}"
    if exhausted and threshold_policy.counter_name.value == "mechanic_attempt_count":
        return f"{reason}:mechanic_attempts_exhausted"
    return reason


def _threshold_counter_mutation_name(policy: CompiledGraphThresholdPolicyPlan) -> str:
    return (policy.recovery_counter_mutation_name or policy.counter_name).value


__all__ = [
    "decision_from_planning_transition",
    "route_planning_stage_result_from_graph",
]
