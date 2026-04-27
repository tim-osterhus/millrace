"""Compiler validation helpers for mode stage maps."""

from __future__ import annotations

from millrace_ai.contracts import ModeDefinition, StageName

from .outcomes import CompilerValidationError


def validate_mode_stage_maps(mode: ModeDefinition, selected_stages: set[StageName]) -> None:
    for map_name, mapping in (
        ("stage_entrypoint_overrides", mode.stage_entrypoint_overrides),
        ("stage_skill_additions", mode.stage_skill_additions),
        ("stage_model_bindings", mode.stage_model_bindings),
        ("stage_runner_bindings", mode.stage_runner_bindings),
    ):
        for stage in sorted(mapping, key=lambda stage_name: stage_name.value):
            if stage not in selected_stages:
                raise CompilerValidationError(
                    f"Mode map `{map_name}` references stage outside selected loops: {stage.value}"
                )


__all__ = ["validate_mode_stage_maps"]
