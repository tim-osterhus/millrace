"""Built-in learning promotion handler adapter.

Thin adapter that delegates to the existing runtime/learning_promotions.py
module.  This preserves the existing Curator promotion behaviour behind
the extension-owned LearningPromotionHandler interface.

Maintenance guardrail: the underlying learning_promotions.py module
uses direct filesystem mutation to manage deferred/applied promotion
records.  When learning promotions are fully migrated to a
runtime-operation-step model (ADR-0014), this adapter should be replaced
with an operation-id-indexed registration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.extensions.interfaces import BUILTIN_INTERFACE_IDS

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


class BuiltInLearningPromotionHandler:
    """Built-in learning promotion handler that delegates to
    runtime/learning_promotions.py."""

    interface_id: str = BUILTIN_INTERFACE_IDS["learning_promotion_handler"]
    domain: str = "learning"

    def handle_curator_promotion(
        self,
        engine: "RuntimeEngine",
        *,
        stage_result: StageResultEnvelope,
    ) -> None:
        from millrace_ai.runtime.learning_promotions import handle_learning_curator_promotion_boundary

        handle_learning_curator_promotion_boundary(
            engine,
            stage_result=stage_result,
        )

    def apply_deferred_promotions(self, engine: "RuntimeEngine") -> int:
        from millrace_ai.runtime.learning_promotions import apply_deferred_learning_promotions_if_safe

        return apply_deferred_learning_promotions_if_safe(engine)


__all__ = ["BuiltInLearningPromotionHandler"]
