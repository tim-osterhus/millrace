"""Runtime effect contracts and handler registry surface."""

from .models import (
    RuntimeEffectDecision,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
    SourceLifecycleAction,
    SourceLifecycleIntent,
    apply_runtime_effect_result,
    lifecycle_intent_for_terminal_result,
)
from .registry import (
    RuntimeEffectHandler,
    RuntimeEffectHandlerRegistration,
    RuntimeEffectHandlerRegistry,
)

__all__ = [
    "RuntimeEffectDecision",
    "RuntimeEffectHandler",
    "RuntimeEffectHandlerRegistration",
    "RuntimeEffectHandlerRegistry",
    "RuntimeEffectMutationPhase",
    "RuntimeEffectResult",
    "SourceLifecycleAction",
    "SourceLifecycleIntent",
    "apply_runtime_effect_result",
    "lifecycle_intent_for_terminal_result",
]
