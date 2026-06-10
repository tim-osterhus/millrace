"""
Generic compiled-graph router shared by all planes.

This module owns ``route_generic_stage_result_from_graph`` — the active
dispatch authority for all planes — and the plane-specific terminal-reason
and failure-class formatter callbacks used to parameterize the generic
three-step pattern (threshold, resume, transition).

Plane-specific modules (``execution.py``, ``planning.py``, ``learning.py``)
are thin wrappers that forward to this module's generic function with their
respective formatter callbacks.
"""

from __future__ import annotations

from collections.abc import Callable

from millrace_ai.architecture import (
    CompiledGraphThresholdPolicyPlan,
    CompiledGraphTransitionPlan,
    CompiledRunPlan,
    FrozenGraphPlanePlan,
)
from millrace_ai.architecture.loop_graphs import GraphLoopTerminalClass
from millrace_ai.contracts import (
    RecoveryCounters,
    RuntimeSnapshot,
    StageName,
    StageResultEnvelope,
)
from millrace_ai.contracts.terminal_outcomes import terminal_outcome_value
from millrace_ai.router import RouterAction, RouterDecision

from .counters import (
    counter_attempts_for_counter_id,
    counter_key_from_snapshot,
    normalize_failure_class,
    resolve_failure_class,
)
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

# ---------------------------------------------------------------------------
# Generic three-step compiled-graph router
# ---------------------------------------------------------------------------


def route_generic_stage_result_from_graph(
    graph_plan: CompiledRunPlan,
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    *,
    source_stage: StageName,
    terminal_failure_class_fn: Callable[
        [StageResultEnvelope, StageName, str, GraphLoopTerminalClass, str | None],
        str | None,
    ],
    terminal_reason_fn: Callable[
        [StageResultEnvelope, StageName, str, str, str | None],
        str,
    ],
    default_threshold_failure_class_fn: Callable[
        [StageResultEnvelope, StageName, CompiledGraphThresholdPolicyPlan, bool], str
    ],
    threshold_reason_fn: Callable[
        [StageResultEnvelope, StageName, CompiledGraphThresholdPolicyPlan, bool], str
    ],
    **kwargs: object,
) -> RouterDecision:
    """
    Generic three-step compiled-graph router shared by all planes.

    Checks, in order:
    1. **Threshold policy** — if the recovery counter for this failure class
       has reached the declared threshold, resolve via the policy's exhausted
       target node or terminal state.
    2. **Resume policy** — if the transition has a resume policy, resolve
       via the policy's default or metadata-driven target node.
    3. **Standard transition** — either a node-to-node edge (``RUN_STAGE``)
       or a terminal state with a terminal action (``IDLE``, ``BLOCKED``,
       or ``HANDOFF``).

    Plane-specific terminal-reason and failure-class formatting is supplied
    via callbacks so the core logic stays plane-agnostic.
    """
    outcome = terminal_outcome_value(stage_result.terminal_result)
    source_node_id = stage_result.node_id

    # 1. Threshold policy check
    threshold_policy = threshold_policy_for_transition(
        graph,
        source_node_id=source_node_id,
        outcome=outcome,
    )
    if threshold_policy is not None:
        failure_class = resolve_failure_class(
            snapshot,
            stage_result,
            default=default_threshold_failure_class_fn(
                stage_result, source_stage, threshold_policy, exhausted=False
            ),
        )
        attempts = counter_attempts_for_counter_id(
            snapshot,
            counters,
            failure_class,
            counter_id=threshold_policy.counter_name.value,
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
                    default=default_threshold_failure_class_fn(
                        stage_result, source_stage, threshold_policy, exhausted=True
                    ),
                ),
                reason=threshold_reason_fn(stage_result, source_stage, threshold_policy, exhausted=True),
            )

    # 2. Resume policy check
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

    # 3. Standard transition
    transition = transition_for_source(graph, source_node_id=source_node_id, outcome=outcome)
    return _decision_from_transition(
        graph_plan,
        graph,
        snapshot,
        source_stage=source_stage,
        stage_result=stage_result,
        transition=transition,
        threshold_policy=threshold_policy,
        terminal_failure_class_fn=terminal_failure_class_fn,
        terminal_reason_fn=terminal_reason_fn,
        default_threshold_failure_class_fn=default_threshold_failure_class_fn,
        threshold_reason_fn=threshold_reason_fn,
    )


def _decision_from_transition(
    graph_plan: CompiledRunPlan,
    graph: FrozenGraphPlanePlan,
    snapshot: RuntimeSnapshot,
    *,
    source_stage: StageName,
    stage_result: StageResultEnvelope,
    transition: CompiledGraphTransitionPlan,
    threshold_policy: CompiledGraphThresholdPolicyPlan | None = None,
    terminal_failure_class_fn: Callable[
        [StageResultEnvelope, StageName, str, GraphLoopTerminalClass, str | None],
        str | None,
    ],
    terminal_reason_fn: Callable[
        [StageResultEnvelope, StageName, str, str, str | None],
        str,
    ],
    default_threshold_failure_class_fn: Callable[
        [StageResultEnvelope, StageName, CompiledGraphThresholdPolicyPlan, bool], str
    ] | None = None,
    threshold_reason_fn: Callable[
        [StageResultEnvelope, StageName, CompiledGraphThresholdPolicyPlan, bool], str
    ] | None = None,
) -> RouterDecision:
    terminal_result = terminal_outcome_value(stage_result.terminal_result)

    if transition.target_node_id is not None:
        if threshold_policy is not None:
            failure_class = resolve_failure_class(
                snapshot,
                stage_result,
                default=(
                    default_threshold_failure_class_fn(
                        stage_result, source_stage, threshold_policy, exhausted=False
                    )
                    if default_threshold_failure_class_fn is not None
                    else _threshold_failure_class_default(
                        stage_result, source_stage, threshold_policy, exhausted=False
                    )
                ),
            )
            counter_mutation = _threshold_counter_mutation_name(threshold_policy)
            return RouterDecision(
                action=RouterAction.RUN_STAGE,
                next_plane=graph.plane,
                next_stage=stage_for_node(graph, transition.target_node_id),
                next_node_id=transition.target_node_id,
                next_stage_kind_id=node_plan_by_id(graph, transition.target_node_id).stage_kind_id,
                reason=(
                    threshold_reason_fn(
                        stage_result, source_stage, threshold_policy, exhausted=False
                    )
                    if threshold_reason_fn is not None
                    else _threshold_reason(
                        stage_result, source_stage, threshold_policy, exhausted=False
                    )
                ),
                failure_class=failure_class,
                counter_key=counter_key_from_snapshot(snapshot, failure_class),
                counter_mutation_name=counter_mutation,
                recovery_counter_name=counter_mutation,
            )
        return RouterDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=stage_for_node(graph, transition.target_node_id),
            next_node_id=transition.target_node_id,
            next_stage_kind_id=node_plan_by_id(graph, transition.target_node_id).stage_kind_id,
            reason=f"{stage_result.node_id}:{terminal_result}",
        )

    terminal_state_id = transition.terminal_state_id
    assert terminal_state_id is not None, "transition must have target_node_id or terminal_state_id"
    terminal_state = terminal_state_by_id(graph, terminal_state_id)

    failure_class = terminal_failure_class_fn(
        stage_result,
        source_stage,
        terminal_result,
        terminal_state.terminal_class,
        terminal_state.failure_class_template,
    )
    return decision_from_terminal_state_action(
        graph_plan.graphs_by_plane,
        graph=graph,
        terminal_state=terminal_state,
        terminal_actions_by_id=graph_plan.terminal_actions_by_id,
        lifecycle_mutation_plans_by_id=graph_plan.lifecycle_mutation_plans_by_id,
        reason=terminal_reason_fn(
            stage_result,
            source_stage,
            terminal_result,
            terminal_state.writes_status,
            terminal_state.router_reason,
        ),
        failure_class=failure_class,
    )


# ---------------------------------------------------------------------------
# Plane-specific terminal formatters
# ---------------------------------------------------------------------------


def execution_terminal_reason(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    terminal_result: str,
    writes_status: str,
    router_reason: str | None,
) -> str:
    """Execution-plane terminal reason formatter.

    Falls back to ``stage_result.node_id`` (compiled node identity) rather than
    ``source_stage.value`` (runtime stage-name string) when no compiled
    ``router_reason`` overrides.
    """
    if router_reason is not None and terminal_result == writes_status:
        return router_reason
    return f"{stage_result.node_id}:{terminal_result}"


def planning_terminal_reason(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    terminal_result: str,
    writes_status: str,
    router_reason: str | None,
) -> str:
    """Planning-plane terminal reason formatter."""
    if router_reason is not None:
        return router_reason
    if terminal_result == writes_status:
        return stage_result.node_id
    return f"{stage_result.node_id}:{terminal_result}"


def learning_terminal_reason(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    terminal_result: str,
    writes_status: str,
    router_reason: str | None,
) -> str:
    """Learning-plane terminal reason formatter.

    Falls back to ``stage_result.node_id`` (compiled node identity) rather than
    ``source_stage.value`` (runtime stage-name string) for non-success and
    success-based fallback formatting.
    """
    if router_reason is not None and terminal_result == writes_status:
        return router_reason
    if not stage_result.success:
        return f"{stage_result.node_id}_blocked"
    return f"{stage_result.node_id}:{terminal_result}"


def execution_terminal_failure_class(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    terminal_result: str,
    terminal_class: GraphLoopTerminalClass,
    failure_class_template: str | None,
) -> str | None:
    """Execution-plane terminal failure class formatter."""
    return failure_class_template


def planning_terminal_failure_class(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    terminal_result: str,
    terminal_class: GraphLoopTerminalClass,
    failure_class_template: str | None,
) -> str | None:
    """Planning-plane terminal failure class formatter.

    Falls back to ``failure_class_template`` from compiled terminal state,
    then to ``stage_result.node_id`` (compiled node identity) rather than
    ``source_stage.value`` (runtime stage-name string).
    """
    if terminal_class is not GraphLoopTerminalClass.BLOCKED:
        return None
    metadata_failure_class = stage_result.metadata.get("failure_class")
    if isinstance(metadata_failure_class, str) and metadata_failure_class.strip():
        return normalize_failure_class(metadata_failure_class)
    return normalize_failure_class(failure_class_template or f"{stage_result.node_id}_blocked")


def learning_terminal_failure_class(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    terminal_result: str,
    terminal_class: GraphLoopTerminalClass,
    failure_class_template: str | None,
) -> str | None:
    """Learning-plane terminal failure class formatter.

    Falls back to ``failure_class_template`` from compiled terminal state,
    then to ``stage_result.node_id`` (compiled node identity) rather than
    ``source_stage.value`` (runtime stage-name string).
    """
    if terminal_class is not GraphLoopTerminalClass.BLOCKED:
        return None
    metadata_failure_class = stage_result.metadata.get("failure_class")
    if isinstance(metadata_failure_class, str) and metadata_failure_class.strip():
        return normalize_failure_class(metadata_failure_class)
    return normalize_failure_class(failure_class_template or f"{stage_result.node_id}_blocked")


# ---------------------------------------------------------------------------
# Threshold helpers
# ---------------------------------------------------------------------------


def _threshold_failure_class_default(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
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
    return f"{stage_result.node_id}_{threshold_policy.on_outcome.lower()}"


def _threshold_reason(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
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
    return f"{stage_result.node_id}_{threshold_policy.on_outcome.lower()}"


def planning_threshold_reason(
    stage_result: StageResultEnvelope,
    source_stage: StageName,
    threshold_policy: CompiledGraphThresholdPolicyPlan,
    *,
    exhausted: bool,
) -> str:
    """
    Planning-plane threshold reason formatter.

    Derives fallback reason from ``stage_result.node_id`` (compiled node identity).
    No hard-coded counter-name suffixes — policy-owned ``exhausted_route_reason``
    is the exclusive exhausted-reason authority.
    """
    policy_reason = (
        threshold_policy.exhausted_route_reason
        if exhausted
        else threshold_policy.route_reason
    )
    if policy_reason is not None:
        return policy_reason
    return f"{stage_result.node_id}_{threshold_policy.on_outcome.lower()}"


def _threshold_counter_mutation_name(policy: CompiledGraphThresholdPolicyPlan) -> str | None:
    return (policy.recovery_counter_mutation_name or policy.counter_name).value


__all__ = [
    "execution_terminal_failure_class",
    "execution_terminal_reason",
    "learning_terminal_failure_class",
    "learning_terminal_reason",
    "planning_terminal_failure_class",
    "planning_terminal_reason",
    "planning_threshold_reason",
    "route_generic_stage_result_from_graph",
]
