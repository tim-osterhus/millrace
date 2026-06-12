"""Blueprint runtime-effect handler registration facade."""

from __future__ import annotations

from millrace_ai.runtime.effects.registry import RuntimeEffectHandlerRegistration

from .operation_runners import artifact_runtime_effect_handler_registrations


def runtime_effect_handler_registrations(
    runner_id: str,
) -> tuple[RuntimeEffectHandlerRegistration, ...]:
    return artifact_runtime_effect_handler_registrations(runner_id)


__all__ = ["runtime_effect_handler_registrations"]
