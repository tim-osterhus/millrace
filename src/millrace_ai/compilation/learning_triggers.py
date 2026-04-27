"""Compiler validation for learning-trigger rules."""

from __future__ import annotations

from millrace_ai.contracts import ModeDefinition, StageName

from .outcomes import CompilerValidationError


def validate_learning_trigger_rules(
    mode: ModeDefinition,
    selected_stages: set[StageName],
) -> None:
    for rule in mode.learning_trigger_rules:
        if rule.source_stage not in selected_stages:
            raise CompilerValidationError(
                "Learning trigger rule references source stage outside selected loops: "
                f"{rule.rule_id}:{rule.source_stage.value}"
            )
        if rule.target_stage not in selected_stages:
            raise CompilerValidationError(
                "Learning trigger rule references target learning stage outside selected loops: "
                f"{rule.rule_id}:{rule.target_stage.value}"
            )


__all__ = ["validate_learning_trigger_rules"]
