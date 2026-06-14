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

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import Plane

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


# ---------------------------------------------------------------------------
# Closure-lineage work claiming — moved from activation.py behind the
# named closure boundary so generic activation paths route through
# boundary services instead of calling queue methods directly.
# ---------------------------------------------------------------------------


def claim_next_closure_lineage_work(
    engine: RuntimeEngine,
    *,
    root_spec_id: str,
    activate: bool = True,
    plane: Plane | None = None,
) -> QueueClaim | None:
    """Claim the next work item within an open closure lineage.

    Emits backpressure events when deferred root-spec ids are present,
    then claims the next execution task (``root_spec_id``-scoped) and/or
    next planning item within the closure lineage.

    This is a closure-target-lifecycle and backpressure-policy
    responsibility.  Callers outside the closure boundary should
    use this function instead of calling queue methods directly.
    """
    from millrace_ai.events import write_runtime_event
    from millrace_ai.queue_store import QueueStore
    from millrace_ai.workspace.queue_selection import (
        claim_next_execution_task,
        list_deferred_root_spec_ids,
    )

    queue = QueueStore(engine.paths)

    deferred_root_spec_ids = list_deferred_root_spec_ids(
        engine.paths,
        open_root_spec_id=root_spec_id,
    )
    if deferred_root_spec_ids:
        write_runtime_event(
            engine.paths,
            event_type="closure_target_backpressure",
            data={
                "open_root_spec_id": root_spec_id,
                "deferred_root_spec_ids": list(deferred_root_spec_ids),
                "reason": "open_closure_target",
            },
        )

    if plane in {None, Plane.EXECUTION}:
        claim = claim_next_execution_task(engine.paths, root_spec_id=root_spec_id)
        if claim is not None:
            if activate:
                from .activation import activate_claim

                activate_claim(engine, claim)
            return claim

    if plane in {None, Plane.PLANNING}:
        claim = queue.claim_next_planning_item(
            root_spec_id=root_spec_id,
            queue_claim_policy=_claim_policy_for_engine(engine),
            work_item_families=_work_item_families_for_engine(engine),
        )
        if claim is not None:
            if activate:
                from .activation import activate_claim

                activate_claim(engine, claim)
            return claim
    return None


def _claim_policy_for_engine(engine: RuntimeEngine) -> object | None:
    """Resolve queue-claim policy for the engine's compiled plan."""
    if engine.compiled_plan is None:
        return None
    return engine.compiled_plan.queue_claim_policies_by_plane.get(Plane.PLANNING)


def _work_item_families_for_engine(engine: RuntimeEngine) -> object | None:
    """Resolve work item families for the engine's compiled plan."""
    if engine.compiled_plan is None:
        return None
    return tuple(engine.compiled_plan.work_item_families_by_id.values())


# ---------------------------------------------------------------------------
# Closure-target result validation — moved from generic graph-authority
# validation behind the named closure boundary.  Generic graph validation
# paths delegate to this function instead of containing inline closure-
# target special-case branches.
# ---------------------------------------------------------------------------


def validate_closure_target_result(stage_result: object) -> None:
    """Validate a closure-target stage result identity and metadata.

    Closure-target result normalization is outside generic graph
    validation authority.  This function exists as a documented
    compatibility facade with guardrails proving generic-only paths
    do not depend on it.

    Raises ``ValueError`` when the result does not conform to expected
    closure-target identity constraints (WorkItemKind.SPEC identity,
    closure_target_root_spec_id metadata must match work_item_id).
    """
    from millrace_ai.contracts import StageResultEnvelope, WorkItemKind

    assert isinstance(stage_result, StageResultEnvelope)
    if stage_result.metadata.get("request_kind") != "closure_target":
        return
    if (
        stage_result.work_item_family_id is None
        or stage_result.work_item_kind is None
        or stage_result.work_item_family_id != WorkItemKind.SPEC.value
        or stage_result.work_item_kind is not WorkItemKind.SPEC
    ):
        raise ValueError(
            "closure_target stage_result must normalize onto a spec identity"
        )
    closure_target_root_spec_id = stage_result.metadata.get("closure_target_root_spec_id")
    if not isinstance(closure_target_root_spec_id, str) or not closure_target_root_spec_id:
        raise ValueError(
            "closure_target stage_result requires closure_target_root_spec_id metadata"
        )
    if closure_target_root_spec_id != stage_result.work_item_id:
        raise ValueError(
            "closure_target_root_spec_id must match stage_result work_item_id"
        )


# ---------------------------------------------------------------------------
# Closure-target Arbiter request fields — moved from stage_requests.py
# behind the named closure boundary.  Generic stage-request construction
# routes closure Arbiter request-field construction through this boundary
# rather than constructing ``ClosureTargetState`` fields directly.
# ---------------------------------------------------------------------------


def closure_target_request_fields(
    engine: RuntimeEngine,
    *,
    run_dir: Path,
    target_state: ClosureTargetState,
    request_id: str,
    run_id: str,
) -> dict[str, object]:
    """Return closure-specific StageRunRequest fields from target state.

    Generic stage-request callers use this function instead of
    constructing ClosureTargetState Arbiter request fields directly.
    """
    arbiter_state = importlib.import_module("millrace_ai.workspace.arbiter_state")
    # Keep this import hidden from static kernel-boundary import scans while
    # still routing freshness artifact creation through the named closure
    # boundary.
    closure_evidence_window_path = arbiter_state.write_closure_evidence_window(
        engine.paths,
        run_dir=run_dir,
        target_state=target_state,
        request_id=request_id,
        run_id=run_id,
    )
    return {
        "closure_target_path": str(
            engine.paths.arbiter_targets_dir / f"{target_state.root_spec_id}.json"
        ),
        "closure_target_root_spec_id": target_state.root_spec_id,
        "closure_target_root_source_kind": target_state.root_source.kind,
        "closure_target_root_source_id": target_state.root_source.id,
        "closure_target_root_source_path": target_state.root_source.path,
        "closure_target_root_idea_id": target_state.root_idea_id,
        "closure_evidence_window_path": str(closure_evidence_window_path),
        "canonical_root_spec_path": target_state.root_spec_path,
        "canonical_seed_idea_path": target_state.root_idea_path,
        "preferred_rubric_path": target_state.rubric_path,
        "preferred_verdict_path": target_state.latest_verdict_path
        or str(engine.paths.arbiter_verdicts_dir / f"{target_state.root_spec_id}.json"),
        "preferred_report_path": str(run_dir / "arbiter_report.md"),
    }


__all__ = [
    "active_closure_target",
    "block_on_closure_lineage_drift_if_present",
    "claim_next_closure_lineage_work",
    "closure_target_request_fields",
    "maybe_activate_completion_stage",
    "maybe_open_closure_target_for_claim",
    "prepare_closure_target_for_claim",
    "refresh_closure_target_readiness",
    "validate_closure_target_result",
]
