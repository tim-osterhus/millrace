"""Pure lookups over selected compiled-plan authority."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.contracts.compiled_plan import (
    RunnerBindingDeclaration,
    SelectedCompiledPlan,
    StageKindDeclaration,
    TerminalActionDeclaration,
    TerminalOutcomeDeclaration,
)


def stage_kind_for(
    selected_plan: SelectedCompiledPlan,
    stage_kind_id: str,
) -> StageKindDeclaration | None:
    for stage_kind in selected_plan.stage_kinds:
        if str(stage_kind.id) == stage_kind_id:
            return stage_kind
    return None


def runner_binding_for(
    selected_plan: SelectedCompiledPlan,
    runner_binding_id: str,
) -> RunnerBindingDeclaration | None:
    for runner_binding in selected_plan.runner_bindings:
        if str(runner_binding.id) == runner_binding_id:
            return runner_binding
    return None


def runner_selectable_outcome_ids(
    selected_plan: SelectedCompiledPlan,
    *,
    stage_kind_id: str,
) -> frozenset[str] | None:
    stage = stage_kind_for(selected_plan, stage_kind_id)
    if stage is None:
        return None
    binding = runner_binding_for(selected_plan, str(stage.runner_binding_id))
    if binding is None or binding.component_pin is None:
        return None
    return frozenset(
        str(mapping.outcome_id)
        for mapping in binding.terminal_result_mappings
        if str(mapping.stage_kind_id) == stage_kind_id
    )


def runtime_owned_threshold_for_outcomes(
    *,
    threshold_action_kind: str,
    threshold_outcome_id: str,
    runner_selectable_outcome_ids: frozenset[str] | None,
) -> bool:
    return (
        runner_selectable_outcome_ids is not None
        and threshold_action_kind != "recovery_route"
        and threshold_outcome_id not in runner_selectable_outcome_ids
    )


def counter_artifact_contract_mismatch(
    *,
    increment_artifact_schema_id: object,
    threshold_artifact_schema_id: object,
    increment_artifact_field_conditions: object,
    threshold_artifact_field_conditions: object,
) -> str | None:
    if _normalized_schema_id(increment_artifact_schema_id) != _normalized_schema_id(
        threshold_artifact_schema_id
    ):
        return "schema"
    if not artifact_field_conditions_guaranteed_by_increment(
        increment_artifact_field_conditions,
        threshold_artifact_field_conditions,
    ):
        return "conditions"
    return None


def artifact_field_conditions_guaranteed_by_increment(
    increment_conditions: object,
    threshold_conditions: object,
) -> bool:
    if not isinstance(increment_conditions, Mapping) or not isinstance(
        threshold_conditions, Mapping
    ):
        return False
    try:
        return all(
            field_name in increment_conditions
            and _canonical_values_equal(
                increment_conditions[field_name],
                expected_value,
            )
            for field_name, expected_value in threshold_conditions.items()
        )
    except (KeyError, TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return False


def _canonical_values_equal(left: object, right: object) -> bool:
    from millrace.contracts.compiled_plan import canonical_authority_bytes

    try:
        return canonical_authority_bytes(left) == canonical_authority_bytes(right)
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return False


def _normalized_schema_id(value: object) -> str | None:
    return None if value is None else str(value)


def terminal_outcome_for(
    selected_plan: SelectedCompiledPlan,
    stage_kind_id: str,
    marker: str | None,
) -> TerminalOutcomeDeclaration | None:
    if marker is None:
        return None
    stage = stage_kind_for(selected_plan, stage_kind_id)
    if stage is None:
        return None
    for outcome in selected_plan.terminal_outcomes:
        if (
            str(outcome.stage_kind_id) == stage_kind_id
            and outcome.id in stage.declared_outcome_ids
            and outcome.marker == marker
        ):
            return outcome
    return None


def terminal_action_for(
    selected_plan: SelectedCompiledPlan,
    stage_kind_id: str,
    outcome_id: str,
) -> TerminalActionDeclaration | None:
    for action in selected_plan.terminal_actions:
        if (
            str(action.stage_kind_id) == stage_kind_id
            and str(action.outcome_id) == outcome_id
        ):
            return action
    return None


__all__ = (
    "artifact_field_conditions_guaranteed_by_increment",
    "counter_artifact_contract_mismatch",
    "runner_binding_for",
    "runner_selectable_outcome_ids",
    "runtime_owned_threshold_for_outcomes",
    "stage_kind_for",
    "terminal_action_for",
    "terminal_outcome_for",
)
