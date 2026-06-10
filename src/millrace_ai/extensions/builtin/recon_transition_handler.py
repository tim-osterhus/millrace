"""Built-in Recon transition handler adapter.

Thin adapter that delegates to the existing runtime/recon_transitions.py
module.  This preserves the existing Recon behaviour behind the
extension-owned ReconTransitionHandler interface.

Maintenance guardrail: the underlying recon_transitions.py module contains
direct kernel-level imports (RouterAction, QueueStore, etc.).  When Recon
transitions are fully migrated to a runtime-operation-step model
(ADR-0014), this adapter should be replaced with an operation-id-indexed
registration rather than a module-level delegation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.extensions.interfaces import BUILTIN_INTERFACE_IDS
from millrace_ai.router import RouterDecision

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan
    from millrace_ai.runtime.engine import RuntimeEngine


class BuiltInReconTransitionHandler:
    """Built-in Recon transition handler that delegates to
    runtime/recon_transitions.py."""

    interface_id: str = BUILTIN_INTERFACE_IDS["recon_transition_handler"]
    domain: str = "recon"

    def is_recon_stage_result(self, stage_result: StageResultEnvelope) -> bool:
        from millrace_ai.runtime.recon_transitions import is_recon_stage_result as _is_recon

        return _is_recon(stage_result)

    def apply_router_decision(
        self,
        engine: "RuntimeEngine",
        decision: RouterDecision,
        stage_result: StageResultEnvelope,
        *,
        stage_result_path: Path | None = None,
        compiled_plan: "CompiledRunPlan | None" = None,
    ) -> tuple[Path, ...]:
        from millrace_ai.runtime.recon_transitions import apply_recon_router_decision

        return apply_recon_router_decision(
            engine,
            decision,
            stage_result,
            stage_result_path=stage_result_path,
            compiled_plan=compiled_plan,
        )


__all__ = ["BuiltInReconTransitionHandler"]
