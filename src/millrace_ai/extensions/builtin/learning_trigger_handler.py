"""Built-in learning trigger handler adapter.

Thin adapter that delegates to the existing runtime/learning_triggers.py
module.  This preserves the existing learning trigger behaviour behind
the extension-owned LearningTriggerHandler interface.

Maintenance guardrail: the underlying learning_triggers.py module
evaluates compiler-frozen trigger rules and enqueues Learning request
documents directly through QueueStore.  When learning triggers are fully
migrated to a runtime-operation-step model (ADR-0014), this adapter
should be replaced with an operation-id-indexed registration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.extensions.interfaces import BUILTIN_INTERFACE_IDS

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


class BuiltInLearningTriggerHandler:
    """Built-in learning trigger handler that delegates to
    runtime/learning_triggers.py."""

    interface_id: str = BUILTIN_INTERFACE_IDS["learning_trigger_handler"]
    domain: str = "learning"

    def enqueue_learning_requests(
        self,
        engine: "RuntimeEngine",
        *,
        stage_result: StageResultEnvelope,
        stage_result_path: Path,
    ) -> tuple[Path, ...]:
        from millrace_ai.runtime.learning_triggers import enqueue_learning_requests_for_stage_result

        return enqueue_learning_requests_for_stage_result(
            engine,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
        )


__all__ = ["BuiltInLearningTriggerHandler"]
