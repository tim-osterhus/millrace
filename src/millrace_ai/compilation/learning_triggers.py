"""Compiler validation for learning-trigger rules."""

from __future__ import annotations

from millrace_ai.contracts import LearningStageName, ModeDefinition, StageMapKey

from .outcomes import CompilerValidationError


def validate_learning_trigger_rules(
    mode: ModeDefinition,
    selected_stages: set[StageMapKey],
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
        if (
            rule.target_stage is LearningStageName.CURATOR
            and rule.target_skill_id is None
            and not rule.preferred_output_paths
        ):
            raise CompilerValidationError(
                "Learning trigger rule targets curator without a safe destination: "
                f"{rule.rule_id}. Direct curator triggers require target_skill_id "
                "or preferred_output_paths; route vague learning through analyst."
            )


__all__ = ["validate_learning_trigger_rules"]
