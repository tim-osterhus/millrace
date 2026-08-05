"""Pure lookups over selected compiled-plan authority."""

from __future__ import annotations

from millrace.contracts.compiled_plan import (
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
    "stage_kind_for",
    "terminal_action_for",
    "terminal_outcome_for",
)
