"""Work-item claim and activation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.contracts import ExecutionStageName, Plane, PlanningStageName, StageName, WorkItemKind
from millrace_ai.queue_store import QueueClaim, QueueStore
from millrace_ai.state_store import save_snapshot

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

from . import completion_behavior


def claim_next_work_item(engine: RuntimeEngine) -> None:
    queue = QueueStore(engine.paths)
    claim = queue.claim_next_planning_item()
    if claim is not None:
        activate_claim(engine, claim)
        return

    claim = queue.claim_next_execution_task()
    if claim is not None:
        activate_claim(engine, claim)


def activate_claim(engine: RuntimeEngine, claim: QueueClaim) -> None:
    assert engine.snapshot is not None
    stage = entry_stage_for_kind(claim.work_item_kind)
    active_plane = (
        Plane.PLANNING
        if claim.work_item_kind in {WorkItemKind.SPEC, WorkItemKind.INCIDENT}
        else Plane.EXECUTION
    )
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_plane": active_plane,
            "active_stage": stage,
            "active_run_id": engine._new_run_id(),
            "active_work_item_kind": claim.work_item_kind,
            "active_work_item_id": claim.work_item_id,
            "active_since": engine._now(),
            "current_failure_class": None,
            "updated_at": engine._now(),
        }
    )
    save_snapshot(engine.paths, engine.snapshot)
    completion_behavior.maybe_open_closure_target_for_claim(engine, claim)


def entry_stage_for_kind(work_item_kind: WorkItemKind) -> StageName:
    if work_item_kind is WorkItemKind.TASK:
        return ExecutionStageName.BUILDER
    if work_item_kind is WorkItemKind.SPEC:
        return PlanningStageName.PLANNER
    return PlanningStageName.AUDITOR


__all__ = ["activate_claim", "claim_next_work_item", "entry_stage_for_kind"]
