"""Legacy Python runtime effect handler registrations."""

from __future__ import annotations

from .. import planner_effects
from .registry import RuntimeEffectHandlerRegistration, RuntimeEffectHandlerRegistry

LEGACY_PYTHON_EFFECT_RUNNER_ID = "legacy_python_handler"


def legacy_runtime_effect_handler_registrations() -> tuple[RuntimeEffectHandlerRegistration, ...]:
    return (
        RuntimeEffectHandlerRegistration(
            handler_id=planner_effects.PLANNER_DISPOSITION_HANDLER_ID,
            runner_id=LEGACY_PYTHON_EFFECT_RUNNER_ID,
            handler=planner_effects.planner_disposition,
        ),
    )


def default_legacy_runtime_effect_handler_registry() -> RuntimeEffectHandlerRegistry:
    return RuntimeEffectHandlerRegistry.from_registrations(
        legacy_runtime_effect_handler_registrations()
    )


__all__ = [
    "LEGACY_PYTHON_EFFECT_RUNNER_ID",
    "default_legacy_runtime_effect_handler_registry",
    "legacy_runtime_effect_handler_registrations",
]
