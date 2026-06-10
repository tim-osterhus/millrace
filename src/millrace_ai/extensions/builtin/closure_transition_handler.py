"""Built-in closure transition handler adapter.

Thin adapter that delegates to the existing runtime/closure_transitions.py
module.  This preserves the existing closure behaviour behind the
extension-owned ClosureTransitionHandler interface.

Maintenance guardrail: the underlying closure_transitions.py module
contains direct imports of queue state helpers and legacy snapshot
management.  When closure transitions are fully migrated to a
runtime-operation-step model (ADR-0014), this adapter should be
replaced with an operation-id-indexed registration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.extensions.interfaces import BUILTIN_INTERFACE_IDS
from millrace_ai.router import RouterDecision

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


class BuiltInClosureTransitionHandler:
    """Built-in closure transition handler that delegates to
    runtime/closure_transitions.py."""

    interface_id: str = BUILTIN_INTERFACE_IDS["closure_transition_handler"]
    domain: str = "closure"

    def is_closure_target_result(self, stage_result: StageResultEnvelope) -> bool:
        return stage_result.metadata.get("request_kind") == "closure_target"

    def apply_router_decision(
        self,
        engine: "RuntimeEngine",
        decision: RouterDecision,
        stage_result: StageResultEnvelope,
    ) -> None:
        from millrace_ai.runtime.closure_transitions import apply_closure_target_router_decision

        apply_closure_target_router_decision(engine, decision, stage_result)


__all__ = ["BuiltInClosureTransitionHandler"]
