"""Named closure boundary: closure target lifecycle, lineage gating,
backpressure policy, and result-normalization responsibilities.

This is the single public boundary module that kernel code should import
for all closure lifecycle behaviour.  The underlying ``completion_behavior``
module is an internal implementation detail.

The boundary covers four separable responsibility areas:

1. **Closure target lifecycle** — opening, recovering, and refreshing
   closure-target state for active root specs.
2. **Lineage gating** — detecting and block-on lineage drift, blocking
   closure activation when same-lineage work has moved to a different
   root spec.
3. **Backpressure policy** — preflighting spec claims against an already-
   open closure target and deferring new root-spec activation through
   scheduler-policy backpressure outcomes.
4. **Result normalization** — post-Arbiter closure target mutation is
   handled through the ``ClosureTransitionHandler`` extension interface;
   this boundary exposes the pre-result readiness and pre-claim checks.

Kernel callers use this module instead of importing ``completion_behavior``
directly.  This ensures closure domain code stays behind a documented
boundary without leaking implementation details into generic runtime paths.

ADR: ADR-0012 (core-kernel-boundary), ADR-0016 (extension-boundary-
compatibility-facades).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from millrace_ai.contracts import ClosureTargetState
    from millrace_ai.queue_store import QueueClaim
    from millrace_ai.runtime.engine import RuntimeEngine

# ---------------------------------------------------------------------------
# Public boundary API — each function delegates to the underlying
# completion_behavior implementation module.
# ---------------------------------------------------------------------------


def active_closure_target(engine: RuntimeEngine) -> ClosureTargetState | None:
    """Return the single actionable open closure target, or None.

    This is a closure-target-lifecycle responsibility.
    """
    from .completion_behavior import active_closure_target as _impl

    return _impl(engine)


def maybe_activate_completion_stage(engine: RuntimeEngine) -> ClosureTargetState | None:
    """Activate the Arbiter stage if the active closure target is eligible.

    This is a closure-target-lifecycle responsibility.
    """
    from .completion_behavior import maybe_activate_completion_stage as _impl

    return _impl(engine)


def maybe_open_closure_target_for_claim(
    engine: RuntimeEngine,
    claim: QueueClaim,
) -> ClosureTargetState | None:
    """Open a closure target for a queued spec claim when a root spec is claimed.

    This is a backpressure-policy responsibility.
    """
    from .completion_behavior import maybe_open_closure_target_for_claim as _impl

    return _impl(engine, claim)


def prepare_closure_target_for_claim(
    engine: RuntimeEngine,
    claim: QueueClaim,
) -> object:
    """Preflight a claim against the active closure target for backpressure.

    Returns a ``ClosureTargetPreparation`` that indicates whether the claim
    is allowed and provides the open/deferred root-spec-ids for backpressure
    events.

    This is a backpressure-policy responsibility.
    """
    from .completion_behavior import prepare_closure_target_for_claim as _impl

    return _impl(engine, claim)


def refresh_closure_target_readiness(
    engine: RuntimeEngine,
    target: ClosureTargetState,
) -> ClosureTargetState:
    """Refresh the closure target's lineage-blocking status from work inventory.

    This is a lineage-gating responsibility.
    """
    from .completion_behavior import refresh_closure_target_readiness as _impl

    return _impl(engine, target)


def block_on_closure_lineage_drift_if_present(
    engine: RuntimeEngine,
    target: ClosureTargetState,
) -> bool:
    """Block closure activation when lineage work has drifted to a different root.

    This is a lineage-gating responsibility.
    """
    from .completion_behavior import (
        block_on_closure_lineage_drift_if_present as _impl,
    )

    return _impl(engine, target)


__all__ = [
    "active_closure_target",
    "block_on_closure_lineage_drift_if_present",
    "maybe_activate_completion_stage",
    "maybe_open_closure_target_for_claim",
    "prepare_closure_target_for_claim",
    "refresh_closure_target_readiness",
]
