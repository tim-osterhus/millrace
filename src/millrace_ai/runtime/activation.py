"""Work-item claim and activation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.contracts import (
    ActiveRunState,
    LearningRequestDocument,
    Plane,
    StageName,
    WorkItemKind,
)
from millrace_ai.contracts.work_refs import plane_for_work_item_family_id
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueClaim, QueueStore
from millrace_ai.state_store import save_snapshot
from millrace_ai.work_documents import read_work_document_as
from millrace_ai.workspace.queue_selection import list_deferred_root_spec_ids

if TYPE_CHECKING:
    from millrace_ai.architecture import (
        CompiledRunPlan,
        PlaneQueueClaimPolicyDefinition,
        WorkItemFamilyDefinition,
    )
    from millrace_ai.runtime.engine import RuntimeEngine

from .active_runs import active_run_from_claim, snapshot_with_active_run
from .graph_authority import (
    GraphActivationDecision,
    learning_stage_activation_for_graph,
    work_item_activation_for_graph,
)
from .lanes import compiled_plan_fingerprint_for_runtime, lane_id_for_plane
from .scheduler_policy import (
    backpressure_outcome,
    fallback_entry_selection,
    foreground_claim_order,
    learning_target_stage_routing,
)


def claim_next_work_item(engine: RuntimeEngine) -> None:
    from millrace_ai.runtime import closure_boundary as _closure_boundary

    queue = QueueStore(engine.paths)
    open_target = _closure_boundary.active_closure_target(engine)
    if open_target is not None:
        _claim_next_open_closure_lineage_work(engine, queue, root_spec_id=open_target.root_spec_id)
        return

    policy = engine.compiled_plan.scheduler_policy if engine.compiled_plan is not None else None
    for plane in foreground_claim_order(policy):
        entry_behavior = fallback_entry_selection(policy)
        if entry_behavior == "pause":
            return
        claim = claim_next_work_item_for_plane(engine, plane)
        if claim is not None:
            activate_claim(engine, claim)
            return


def claim_next_work_item_for_plane(engine: RuntimeEngine, plane: Plane) -> QueueClaim | None:
    from millrace_ai.runtime import closure_boundary as _closure_boundary

    queue = QueueStore(engine.paths)
    open_target = _closure_boundary.active_closure_target(engine)
    if open_target is not None and plane in {Plane.EXECUTION, Plane.PLANNING}:
        return _claim_next_open_closure_lineage_work(
            engine,
            queue,
            root_spec_id=open_target.root_spec_id,
            activate=False,
            plane=plane,
        )
    return _claim_for_plane_no_closure(engine, queue, plane)


def activate_claim(engine: RuntimeEngine, claim: QueueClaim) -> None:
    try:
        activate_claim_for_plane(engine, claim, _plane_for_claim(engine, claim))
    except RuntimeError:
        return


def activate_claim_for_plane(
    engine: RuntimeEngine,
    claim: QueueClaim,
    plane: Plane,
) -> ActiveRunState:
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None

    activation = _activation_for_claim(engine, claim)
    if activation.plane is not plane:
        raise ValueError("claim activation plane does not match requested plane")
    from millrace_ai.runtime import closure_boundary as _closure_boundary

    closure_preparation = _closure_boundary.prepare_closure_target_for_claim(engine, claim)
    if not closure_preparation.allowed:
        outcome = backpressure_outcome(
            engine.compiled_plan.scheduler_policy,
            has_open_closure_target=closure_preparation.open_root_spec_id is not None,
        )
        if outcome == "allow":
            pass  # skip backpressure gate entirely
        elif outcome == "defer":
            _backpressure_claim(engine, claim, open_root_spec_id=closure_preparation.open_root_spec_id)
            raise RuntimeError("claim deferred by scheduler-policy backpressure")
        else:
            _backpressure_claim(engine, claim, open_root_spec_id=closure_preparation.open_root_spec_id)
            raise RuntimeError("claim blocked by scheduler-policy backpressure")

    active_run = active_run_from_claim(
        activation=activation,
        claim=claim,
        lane_id=lane_id_for_plane(engine.compiled_plan, activation.plane),
        run_id=engine._new_run_id(),
        compiled_plan_id=engine.compiled_plan.compiled_plan_id,
        compiled_plan_fingerprint=compiled_plan_fingerprint_for_runtime(engine.compiled_plan),
        now=engine._now(),
    )
    engine.snapshot = snapshot_with_active_run(
        engine.snapshot,
        active_run,
        now=engine._now(),
        current_failure_class=None,
    )
    save_snapshot(engine.paths, engine.snapshot)
    return active_run


def entry_stage_for_kind(
    work_item_kind: WorkItemKind,
    *,
    compiled_plan: CompiledRunPlan,
) -> StageName:
    """Return the entry stage for a work item kind.

    Derives entry selection from the compiled work-item family and graph
    entry data.  A compiled plan is required; hardwired compatibility
    mappings are no longer shipped.
    """
    activation = work_item_activation_for_graph(compiled_plan, work_item_kind)
    return activation.stage


def entry_stage_for_family_id(
    family_id: str,
    *,
    compiled_plan: CompiledRunPlan,
) -> StageName:
    """Return the entry stage for a work item family id.

    Derives entry selection from the compiled work-item family and graph
    entry data.  A compiled plan is required; hardwired compatibility
    mappings are no longer shipped.
    """
    activation = work_item_activation_for_graph(compiled_plan, family_id)
    return activation.stage


def _claim_for_plane_no_closure(
    engine: RuntimeEngine,
    queue: QueueStore,
    plane: Plane,
) -> QueueClaim | None:
    """Claim work for *plane* when no open closure target is active."""
    from millrace_ai.workspace.queue_selection import claim_next_for_plane

    return claim_next_for_plane(
        engine.paths,
        plane,
        queue_claim_policy=_claim_policy_for_plane(engine, plane),
        work_item_families=_work_item_families_for_engine(engine),
    )


def _activation_for_claim(engine: RuntimeEngine, claim: QueueClaim) -> GraphActivationDecision:
    assert engine.compiled_plan is not None
    family_id = _claim_family_id(claim)
    if not _is_learning_family(engine.compiled_plan, family_id=family_id):
        return work_item_activation_for_graph(engine.compiled_plan, family_id)

    document = read_work_document_as(claim.path, model=LearningRequestDocument)
    if document.target_stage is None:
        # Preserve safety check: skip to compiled graph when target_stage is None.
        return work_item_activation_for_graph(engine.compiled_plan, family_id)

    # Consult scheduler-policy learning routing before delegating to compiled graph.
    # The scheduler policy returns a typed LearningStageName, so no inline import
    # of LearningStageName is needed here.
    policy_target = learning_target_stage_routing(
        engine.compiled_plan.scheduler_policy
    )
    if policy_target is not None:
        return learning_stage_activation_for_graph(
            engine.compiled_plan, policy_target
        )
    return learning_stage_activation_for_graph(engine.compiled_plan, document.target_stage)


def _claim_next_open_closure_lineage_work(
    engine: RuntimeEngine,
    queue: QueueStore,
    *,
    root_spec_id: str,
    activate: bool = True,
    plane: Plane | None = None,
) -> QueueClaim | None:
    # Route closure-lineage claiming through the named closure boundary
    # instead of calling queue methods directly.  Closure-lineage claiming
    # uses compiled scheduler/backpressure policy, queue-family metadata,
    # or named closure-boundary services.
    del queue
    from millrace_ai.runtime import closure_boundary as _closure_boundary

    return _closure_boundary.claim_next_closure_lineage_work(
        engine,
        root_spec_id=root_spec_id,
        activate=activate,
        plane=plane,
    )


def _plane_for_claim(engine: RuntimeEngine, claim: QueueClaim) -> Plane:
    family_id = _claim_family_id(claim)
    if claim.plane is not None:
        return claim.plane
    if engine.compiled_plan is not None:
        family = engine.compiled_plan.work_item_families_by_id.get(family_id)
        if family is not None:
            return family.plane
    plane = plane_for_work_item_family_id(family_id)
    if plane is None:
        raise ValueError(f"cannot infer plane for work item family {family_id}")
    return plane


def _claim_policy_for_plane(
    engine: RuntimeEngine,
    plane: Plane,
) -> PlaneQueueClaimPolicyDefinition | None:
    if engine.compiled_plan is None:
        return None
    claim_policy = engine.compiled_plan.queue_claim_policies_by_plane.get(plane)
    if claim_policy is None:
        raise ValueError(f"compiled plan missing {plane.value} queue claim policy")
    return claim_policy


def _work_item_families_for_engine(engine: RuntimeEngine) -> tuple[WorkItemFamilyDefinition, ...] | None:
    if engine.compiled_plan is None:
        return None
    return tuple(engine.compiled_plan.work_item_families_by_id.values())


def _claim_family_id(claim: QueueClaim) -> str:
    if claim.family_id is None:
        raise ValueError("QueueClaim is missing family_id")
    return claim.family_id


def _is_learning_family(
    compiled_plan: CompiledRunPlan,
    *,
    family_id: str,
) -> bool:
    """Determine whether a family is a Learning-domain family.

    Uses compiled plan work-item family metadata rather than
    hardwired WorkItemKind-to-domain branches.
    """
    family = compiled_plan.work_item_families_by_id.get(family_id)
    if family is not None:
        return family.plane is Plane.LEARNING
    return False


def _backpressure_claim(
    engine: RuntimeEngine,
    claim: QueueClaim,
    *,
    open_root_spec_id: str | None,
) -> None:
    if engine.compiled_plan is not None:
        family = engine.compiled_plan.work_item_families_by_id.get(claim.family_id)
        if family is not None:
            from millrace_ai.workspace.work_item_adapters import (
                adapter_for_family_id,
                move_active_with_adapter,
            )

            adapter = adapter_for_family_id(claim.family_id)
            move_active_with_adapter(
                engine.paths,
                adapter,
                claim.work_item_id,
                target_state="queue",
            )
    if open_root_spec_id is not None:
        _emit_closure_target_backpressure(
            engine,
            open_root_spec_id=open_root_spec_id,
            deferred_root_spec_ids=list_deferred_root_spec_ids(
                engine.paths,
                open_root_spec_id=open_root_spec_id,
            ),
        )


def _emit_closure_target_backpressure(
    engine: RuntimeEngine,
    *,
    open_root_spec_id: str,
    deferred_root_spec_ids: tuple[str, ...],
) -> None:
    write_runtime_event(
        engine.paths,
        event_type="closure_target_backpressure",
        data={
            "open_root_spec_id": open_root_spec_id,
            "deferred_root_spec_ids": list(deferred_root_spec_ids),
            "reason": "open_closure_target",
        },
    )


__all__ = [
    "activate_claim",
    "activate_claim_for_plane",
    "claim_next_work_item",
    "claim_next_work_item_for_plane",
    "entry_stage_for_family_id",
    "entry_stage_for_kind",
]
