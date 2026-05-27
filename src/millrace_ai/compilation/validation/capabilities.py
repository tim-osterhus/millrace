"""Mode-bound capability validation helpers."""

from __future__ import annotations

from collections.abc import Mapping

from millrace_ai.contracts import ModeDefinition, StageMapKey

from ..outcomes import CompilerValidationError
from .diagnostics import stage_key_value


def validate_stage_runner_bindings(
    mode: ModeDefinition,
    selected_stages: set[StageMapKey],
) -> None:
    _validate_mode_stage_binding_map(
        map_name="stage_runner_bindings",
        mapping=mode.stage_runner_bindings,
        selected_stages=selected_stages,
    )


def _validate_mode_stage_binding_map(
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


__all__ = ["validate_stage_runner_bindings"]
