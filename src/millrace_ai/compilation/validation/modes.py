"""Mode-map validation helpers."""

from __future__ import annotations

from collections.abc import Mapping

from millrace_ai.contracts import ModeDefinition, StageMapKey

from ..outcomes import CompilerValidationError
from .capabilities import validate_stage_runner_bindings
from .diagnostics import stage_key_value
from .model_assignments import (
    validate_stage_model_bindings,
    validate_stage_thinking_bindings,
)


def validate_mode_stage_maps(mode: ModeDefinition, selected_stages: set[StageMapKey]) -> None:
    _validate_mode_stage_map(
        map_name="stage_entrypoint_overrides",
        mapping=mode.stage_entrypoint_overrides,
        selected_stages=selected_stages,
    )
    _validate_mode_stage_map(
        map_name="stage_skill_additions",
        mapping=mode.stage_skill_additions,
        selected_stages=selected_stages,
    )
    validate_stage_model_bindings(mode, selected_stages)
    validate_stage_runner_bindings(mode, selected_stages)
    validate_stage_thinking_bindings(mode, selected_stages)


def _validate_mode_stage_map(
    *,
    map_name: str,
    mapping: Mapping[StageMapKey, object],
    selected_stages: set[StageMapKey],
) -> None:
    for stage in sorted(mapping, key=stage_key_value):
        if stage not in selected_stages:
            raise CompilerValidationError(
                "Mode map "
                f"`{map_name}` references stage outside selected loops: {stage_key_value(stage)}"
            )


__all__ = ["validate_mode_stage_maps"]
