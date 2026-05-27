"""Lifecycle and terminal-action validation helpers."""

from __future__ import annotations

from millrace_ai.architecture import (
    FrozenGraphPlanePlan,
    LifecycleMutationPlanDefinition,
    RegisteredStageKindDefinition,
    RuntimeEffectRuleDefinition,
    TerminalActionDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.contracts import Plane

from ..outcomes import CompilerValidationError


def validate_terminal_actions(
    *,
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    terminal_actions_by_id: dict[str, TerminalActionDefinition],
    lifecycle_plans_by_id: dict[str, LifecycleMutationPlanDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
) -> None:
    terminal_classes = {
        getattr(action, "terminal_class")
        for action in terminal_actions_by_id.values()
    }
    for graph in graphs_by_plane.values():
        for state in graph.terminal_states:
            terminal_class = state.terminal_class.value
            if terminal_class not in terminal_classes:
                raise CompilerValidationError(
                    f"terminal state {state.terminal_state_id} uses terminal class "
                    f"{terminal_class} without a terminal action"
                )

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


def validate_lifecycle_plans(
    *,
    families_by_id: dict[str, WorkItemFamilyDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    lifecycle_plan_ids: dict[str, LifecycleMutationPlanDefinition],
) -> None:
    for plan in lifecycle_plan_ids.values():
        source_family_id = getattr(plan, "source_family_id")
        if source_family_id not in families_by_id:
            raise CompilerValidationError(
                f"lifecycle mutation plan {getattr(plan, 'plan_id')} references unknown "
                f"source family {source_family_id}"
            )
        source_node_id = getattr(plan, "source_node_id")
        if source_node_id != "any" and source_node_id not in stage_kinds:
            raise CompilerValidationError(
                f"lifecycle mutation plan {getattr(plan, 'plan_id')} references unknown "
                f"source node {source_node_id}"
            )


__all__ = ["validate_lifecycle_plans", "validate_terminal_actions"]
