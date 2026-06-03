"""Lifecycle and terminal-action validation helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from millrace_ai.architecture import (
    CompiledGraphThresholdPolicyPlan,
    CompiledGraphTransitionPlan,
    FrozenGraphPlanePlan,
    GraphLoopEntryKey,
    LifecycleMutationPlanDefinition,
    RegisteredStageKindDefinition,
    RuntimeEffectRuleDefinition,
    TerminalActionDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.architecture.loop_graphs import graph_loop_entry_key_value
from millrace_ai.contracts import Plane

from ..outcomes import CompilerValidationError

_ANY_SOURCE_NODE = "any"
_ANY_SOURCE_FAMILY = "any"
_RUNTIME_FAILURE_EXHAUSTED_OUTCOME = "RUNTIME_FAILURE_EXHAUSTED"
_TERMINAL_ACTION_RUNTIME_OPERATION_IDS = frozenset(
    {
        "recon.enqueue_task",
        "recon.enqueue_spec",
        "recon.noop",
        "recon.block_work_item",
    }
)


@dataclass(frozen=True)
class _TerminalSelectionContext:
    graph_id: str
    terminal_state_id: str
    source_node_id: str
    source_stage_kind_id: str
    outcome_id: str
    source_family_ids: frozenset[str]
    applicability_context: str
    source_label: str


def validate_terminal_actions(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    terminal_actions_by_id: dict[str, TerminalActionDefinition],
    lifecycle_plans_by_id: dict[str, LifecycleMutationPlanDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
) -> None:
    for action in terminal_actions_by_id.values():
        plan_id = getattr(action, "lifecycle_mutation_plan_id")
        if plan_id is not None and plan_id not in lifecycle_plans_by_id:
            raise CompilerValidationError(
                f"terminal action {getattr(action, 'terminal_action_id')} references unknown "
                f"lifecycle mutation plan {plan_id}"
            )
        for rule_id in getattr(action, "effect_rule_ids"):
            if rule_id not in runtime_effect_rules_by_id:
                raise CompilerValidationError(
                    f"terminal action {getattr(action, 'terminal_action_id')} references "
                    f"unknown runtime effect rule {rule_id}"
                )
        runtime_operation_id = getattr(action, "runtime_operation_id", None)
        if (
            runtime_operation_id is not None
            and runtime_operation_id not in _TERMINAL_ACTION_RUNTIME_OPERATION_IDS
        ):
            raise CompilerValidationError(
                f"terminal action {getattr(action, 'terminal_action_id')} references "
                f"unknown runtime operation {runtime_operation_id}"
            )

    for graph in graphs_by_plane.values():
        for state in graph.terminal_states:
            action_id = state.terminal_action_id
            action = terminal_actions_by_id.get(action_id)
            if action is None:
                raise CompilerValidationError(
                    f"terminal state {state.terminal_state_id} references unknown "
                    f"terminal action {action_id}"
                )
            action_class = getattr(action, "terminal_class")
            state_class = state.terminal_class.value
            if action_class != state_class:
                raise CompilerValidationError(
                    f"terminal state {state.terminal_state_id} uses terminal action "
                    f"{action_id} with class {action_class} but state class is {state_class}"
                )
            for context in _terminal_selection_contexts(
                graph=graph,
                terminal_state_id=state.terminal_state_id,
                families_by_id=families_by_id,
            ):
                _validate_terminal_action_applicability(
                    action=action,
                    plan=(
                        lifecycle_plans_by_id.get(action.lifecycle_mutation_plan_id)
                        if action.lifecycle_mutation_plan_id is not None
                        else None
                    ),
                    context=context,
                )

def _terminal_selection_contexts(
    *,
    graph: FrozenGraphPlanePlan,
    terminal_state_id: str,
    families_by_id: dict[str, WorkItemFamilyDefinition],
) -> Iterable[_TerminalSelectionContext]:
    reachable_families_by_node = _reachable_source_families_by_node(
        graph=graph,
        families_by_id=families_by_id,
    )
    stage_kind_ids_by_node = _stage_kind_ids_by_node(graph)
    for transition in graph.compiled_transitions:
        if transition.terminal_state_id != terminal_state_id:
            continue
        yield _context_for_transition(
            graph=graph,
            transition=transition,
            stage_kind_ids_by_node=stage_kind_ids_by_node,
            source_family_ids=reachable_families_by_node.get(transition.source_node_id, frozenset()),
            source_label=f"terminal transition {transition.edge_id}",
        )

    for policy in graph.compiled_threshold_policies:
        if policy.exhausted_terminal_state_id != terminal_state_id:
            continue
        yield from _contexts_for_threshold_policy(
            graph=graph,
            policy=policy,
            stage_kind_ids_by_node=stage_kind_ids_by_node,
            reachable_families_by_node=reachable_families_by_node,
        )

    recovery = graph.runtime_failure_recovery
    if recovery is not None and recovery.exhausted_terminal_state_id == terminal_state_id:
        for node in graph.nodes:
            families = reachable_families_by_node.get(node.node_id, frozenset())
            if not families:
                continue
            yield _TerminalSelectionContext(
                graph_id=graph.loop_id,
                terminal_state_id=terminal_state_id,
                source_node_id=node.node_id,
                source_stage_kind_id=node.stage_kind_id,
                outcome_id=_RUNTIME_FAILURE_EXHAUSTED_OUTCOME,
                source_family_ids=families,
                applicability_context="runtime_failure_exhaustion",
                source_label=(
                    "runtime failure recovery exhaustion "
                    f"{graph.loop_id}:{node.node_id}"
                ),
            )

    completion = graph.completion_behavior
    if completion is None:
        return
    if terminal_state_id == completion.on_pass_terminal_state_id:
        yield _TerminalSelectionContext(
            graph_id=graph.loop_id,
            terminal_state_id=terminal_state_id,
            source_node_id=completion.target_node_id,
            source_stage_kind_id=stage_kind_ids_by_node[completion.target_node_id],
            outcome_id="COMPLETION_PASS",
            source_family_ids=frozenset(completion.root_source_policy.accepted_kinds),
            applicability_context="completion_behavior",
            source_label=f"completion behavior pass {graph.loop_id}",
        )
    if terminal_state_id == completion.on_gap_terminal_state_id:
        yield _TerminalSelectionContext(
            graph_id=graph.loop_id,
            terminal_state_id=terminal_state_id,
            source_node_id=completion.target_node_id,
            source_stage_kind_id=stage_kind_ids_by_node[completion.target_node_id],
            outcome_id="COMPLETION_GAP",
            source_family_ids=frozenset(completion.root_source_policy.accepted_kinds),
            applicability_context="completion_behavior",
            source_label=f"completion behavior gap {graph.loop_id}",
        )


def _context_for_transition(
    *,
    graph: FrozenGraphPlanePlan,
    transition: CompiledGraphTransitionPlan,
    stage_kind_ids_by_node: dict[str, str],
    source_family_ids: frozenset[str],
    source_label: str,
) -> _TerminalSelectionContext:
    terminal_state_id = transition.terminal_state_id
    assert terminal_state_id is not None
    return _TerminalSelectionContext(
        graph_id=graph.loop_id,
        terminal_state_id=terminal_state_id,
        source_node_id=transition.source_node_id,
        source_stage_kind_id=stage_kind_ids_by_node[transition.source_node_id],
        outcome_id=transition.outcome,
        source_family_ids=source_family_ids,
        applicability_context="graph_transition",
        source_label=source_label,
    )


def _contexts_for_threshold_policy(
    *,
    graph: FrozenGraphPlanePlan,
    policy: CompiledGraphThresholdPolicyPlan,
    stage_kind_ids_by_node: dict[str, str],
    reachable_families_by_node: dict[str, frozenset[str]],
) -> Iterable[_TerminalSelectionContext]:
    terminal_state_id = policy.exhausted_terminal_state_id
    assert terminal_state_id is not None
    for source_node_id in policy.source_node_ids:
        yield _TerminalSelectionContext(
            graph_id=graph.loop_id,
            terminal_state_id=terminal_state_id,
            source_node_id=source_node_id,
            source_stage_kind_id=stage_kind_ids_by_node[source_node_id],
            outcome_id=policy.on_outcome,
            source_family_ids=reachable_families_by_node.get(source_node_id, frozenset()),
            applicability_context="threshold_exhaustion",
            source_label=f"threshold policy {policy.policy_id}",
        )


def _validate_terminal_action_applicability(
    *,
    action: TerminalActionDefinition,
    plan: LifecycleMutationPlanDefinition | None,
    context: _TerminalSelectionContext,
) -> None:
    if action.non_mutating:
        if context.source_family_ids:
            raise CompilerValidationError(
                f"{context.source_label} in graph {context.graph_id} targets terminal "
                f"state {context.terminal_state_id} with non-mutating terminal action "
                f"{action.terminal_action_id}, but reachable source families are "
                f"{', '.join(sorted(context.source_family_ids))}"
            )
        return

    if plan is None:
        raise CompilerValidationError(
            f"terminal action {action.terminal_action_id} selected by "
            f"{context.source_label} has no lifecycle mutation plan"
        )

    if context.applicability_context not in plan.applicability_contexts:
        raise CompilerValidationError(
            f"terminal action {action.terminal_action_id} lifecycle plan {plan.plan_id} "
            f"does not apply to {context.source_label} context "
            f"{context.applicability_context}"
        )
    if plan.source_scope == "graph_node" and plan.source_graph_node_id != context.source_node_id:
        raise CompilerValidationError(
            f"terminal action {action.terminal_action_id} lifecycle plan {plan.plan_id} "
            f"source_graph_node_id {plan.source_graph_node_id} does not apply to "
            f"{context.source_label} source node {context.source_node_id}"
        )
    if plan.source_scope == "stage_kind" and plan.source_stage_kind_id != context.source_stage_kind_id:
        raise CompilerValidationError(
            f"terminal action {action.terminal_action_id} lifecycle plan {plan.plan_id} "
            f"source_stage_kind_id {plan.source_stage_kind_id} does not apply to "
            f"{context.source_label} source stage kind {context.source_stage_kind_id}"
        )
    if plan.outcome_scope == "outcome" and plan.outcome_id != context.outcome_id:
        raise CompilerValidationError(
            f"terminal action {action.terminal_action_id} lifecycle plan {plan.plan_id} "
            f"outcome_id {plan.outcome_id} does not apply to {context.source_label} "
            f"outcome {context.outcome_id}"
        )
    if plan.source_family_scope == "any":
        return
    if not context.source_family_ids:
        raise CompilerValidationError(
            f"terminal action {action.terminal_action_id} lifecycle plan {plan.plan_id} "
            f"declares source_family_id {plan.source_family_id}, but "
            f"{context.source_label} has no reachable source family context"
        )
    assert plan.source_family_id is not None
    if plan.source_family_id not in context.source_family_ids:
        raise CompilerValidationError(
            f"terminal action {action.terminal_action_id} lifecycle plan {plan.plan_id} "
            f"source_family_id {plan.source_family_id} is incompatible with "
            f"{context.source_label} reachable source families "
            f"{', '.join(sorted(context.source_family_ids))}"
        )
    if len(context.source_family_ids) > 1:
        raise CompilerValidationError(
            f"terminal action {action.terminal_action_id} lifecycle plan {plan.plan_id} "
            f"declares concrete source_family_id {plan.source_family_id}, but "
            f"{context.source_label} is reachable from multiple source families "
            f"{', '.join(sorted(context.source_family_ids))}"
        )


def _reachable_source_families_by_node(
    *,
    graph: FrozenGraphPlanePlan,
    families_by_id: dict[str, WorkItemFamilyDefinition],
) -> dict[str, frozenset[str]]:
    families_by_entry = {
        (family.plane, family.entry_key): family.family_id
        for family in families_by_id.values()
    }
    reachable: dict[str, set[str]] = {node.node_id: set() for node in graph.nodes}
    outgoing: dict[str, list[str]] = {node.node_id: [] for node in graph.nodes}
    for transition in graph.compiled_transitions:
        if transition.target_node_id is not None:
            outgoing.setdefault(transition.source_node_id, []).append(transition.target_node_id)

    for entry in graph.entry_nodes:
        entry_key = graph_loop_entry_key_value(entry.entry_key)
        if entry_key == GraphLoopEntryKey.CLOSURE_TARGET.value:
            continue
        family_id = families_by_entry.get((graph.plane, entry_key))
        if family_id is None:
            continue
        stack = [entry.node_id]
        seen: set[str] = set()
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            reachable.setdefault(node_id, set()).add(family_id)
            stack.extend(outgoing.get(node_id, ()))

    return {node_id: frozenset(family_ids) for node_id, family_ids in reachable.items()}


def validate_lifecycle_plans(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    lifecycle_plan_ids: dict[str, LifecycleMutationPlanDefinition],
) -> None:
    graph_node_ids = {
        node.node_id
        for graph in graphs_by_plane.values()
        for node in graph.nodes
    }
    for plan in lifecycle_plan_ids.values():
        source_family_id = getattr(plan, "source_family_id")
        if plan.source_family_scope == "family" and source_family_id not in families_by_id:
            raise CompilerValidationError(
                f"lifecycle mutation plan {getattr(plan, 'plan_id')} references unknown "
                f"source family {source_family_id}"
            )
        if plan.source_scope == "graph_node" and plan.source_graph_node_id not in graph_node_ids:
            raise CompilerValidationError(
                f"lifecycle mutation plan {getattr(plan, 'plan_id')} references unknown "
                f"source graph node {plan.source_graph_node_id}"
            )
        if plan.source_scope == "stage_kind" and plan.source_stage_kind_id not in stage_kinds:
            raise CompilerValidationError(
                f"lifecycle mutation plan {getattr(plan, 'plan_id')} references unknown "
                f"source stage kind {plan.source_stage_kind_id}"
            )


def _stage_kind_ids_by_node(graph: FrozenGraphPlanePlan) -> dict[str, str]:
    return {node.node_id: node.stage_kind_id for node in graph.nodes}


__all__ = ["validate_lifecycle_plans", "validate_terminal_actions"]
