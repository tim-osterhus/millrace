"""Helpers for plane-indexed runtime active-run state."""

from __future__ import annotations

from datetime import datetime

from millrace_ai.contracts import (
    ActiveRunState,
    ClosureTargetState,
    Plane,
    RuntimeSnapshot,
    StageName,
    WorkItemKind,
)
from millrace_ai.queue_store import QueueClaim
from millrace_ai.runtime.graph_authority import GraphActivationDecision


def active_run_for_plane(snapshot: RuntimeSnapshot, plane: Plane) -> ActiveRunState | None:
    active_run = snapshot.active_runs_by_plane.get(plane)
    if active_run is not None:
        return active_run
    if snapshot.active_plane is not plane or snapshot.active_stage is None or snapshot.active_run_id is None:
        return None
    if snapshot.active_work_item_kind is None or snapshot.active_work_item_id is None:
        return None
    return ActiveRunState(
        plane=plane,
        stage=snapshot.active_stage,
        node_id=snapshot.active_node_id or snapshot.active_stage.value,
        stage_kind_id=snapshot.active_stage_kind_id or snapshot.active_stage.value,
        run_id=snapshot.active_run_id,
        request_kind=(
            "learning_request"
            if snapshot.active_work_item_kind is WorkItemKind.LEARNING_REQUEST
            else "active_work_item"
        ),
        work_item_kind=snapshot.active_work_item_kind,
        work_item_id=snapshot.active_work_item_id,
        active_since=snapshot.active_since or snapshot.updated_at,
    )


def active_run_from_claim(
    *,
    activation: GraphActivationDecision,
    claim: QueueClaim,
    run_id: str,
    now: datetime,
) -> ActiveRunState:
    return ActiveRunState(
        plane=activation.plane,
        stage=activation.stage,
        node_id=activation.node_id,
        stage_kind_id=activation.stage_kind_id,
        run_id=run_id,
        request_kind=(
            "learning_request"
            if claim.work_item_kind is WorkItemKind.LEARNING_REQUEST
            else "active_work_item"
        ),
        work_item_kind=claim.work_item_kind,
        work_item_id=claim.work_item_id,
        active_since=now,
    )


def active_run_from_closure_target(
    *,
    activation: GraphActivationDecision,
    target: ClosureTargetState,
    run_id: str,
    now: datetime,
) -> ActiveRunState:
    return ActiveRunState(
        plane=activation.plane,
        stage=activation.stage,
        node_id=activation.node_id,
        stage_kind_id=activation.stage_kind_id,
        run_id=run_id,
        request_kind="closure_target",
        closure_target_root_spec_id=target.root_spec_id,
        closure_target_root_idea_id=target.root_idea_id,
        active_since=now,
    )


def snapshot_with_active_run(
    snapshot: RuntimeSnapshot,
    active_run: ActiveRunState,
    *,
    now: datetime,
    current_failure_class: str | None = None,
) -> RuntimeSnapshot:
    active_runs = dict(snapshot.active_runs_by_plane)
    active_runs[active_run.plane] = active_run
    update: dict[str, object] = {
        "active_runs_by_plane": active_runs,
        "current_failure_class": current_failure_class,
        "updated_at": now,
    }
    update.update(_legacy_projection_update(active_runs))
    return snapshot.model_copy(update=update)


def snapshot_with_next_stage_for_plane(
    snapshot: RuntimeSnapshot,
    *,
    plane: Plane,
    stage: StageName,
    node_id: str,
    stage_kind_id: str,
    now: datetime,
    current_failure_class: str | None,
) -> RuntimeSnapshot:
    active_run = active_run_for_plane(snapshot, plane)
    if active_run is None:
        raise ValueError(f"no active run for plane {plane.value}")
    updated_run = active_run.model_copy(
        update={
            "stage": stage,
            "node_id": node_id,
            "stage_kind_id": stage_kind_id,
            "active_since": now,
        }
    )
    return snapshot_with_active_run(
        snapshot,
        updated_run,
        now=now,
        current_failure_class=current_failure_class,
    )


def snapshot_projected_to_plane(snapshot: RuntimeSnapshot, plane: Plane) -> RuntimeSnapshot:
    active_run = active_run_for_plane(snapshot, plane)
    if active_run is None:
        return snapshot
    return snapshot.model_copy(
        update={
            "active_plane": active_run.plane,
            "active_stage": active_run.stage,
            "active_node_id": active_run.node_id,
            "active_stage_kind_id": active_run.stage_kind_id,
            "active_run_id": active_run.run_id,
            "active_work_item_kind": active_run.work_item_kind,
            "active_work_item_id": active_run.work_item_id,
            "active_since": active_run.active_since,
        }
    )


def snapshot_without_active_plane(
    snapshot: RuntimeSnapshot,
    *,
    plane: Plane,
    now: datetime,
    current_failure_class: str | None,
) -> RuntimeSnapshot:
    active_runs = dict(snapshot.active_runs_by_plane)
    active_runs.pop(plane, None)
    update: dict[str, object] = {
        "active_runs_by_plane": active_runs,
        "current_failure_class": current_failure_class,
        "updated_at": now,
    }
    update.update(_legacy_projection_update(active_runs))
    return snapshot.model_copy(update=update)


def snapshot_without_active_runs(
    snapshot: RuntimeSnapshot,
    *,
    now: datetime,
    current_failure_class: str | None = None,
) -> RuntimeSnapshot:
    update: dict[str, object] = {
        "active_runs_by_plane": {},
        "current_failure_class": current_failure_class,
        "updated_at": now,
    }
    empty_active_runs: dict[Plane, ActiveRunState] = {}
    update.update(_legacy_projection_update(empty_active_runs))
    return snapshot.model_copy(update=update)


def _legacy_projection_update(active_runs: dict[Plane, ActiveRunState]) -> dict[str, object]:
    if not active_runs:
        return {
            "active_plane": None,
            "active_stage": None,
            "active_node_id": None,
            "active_stage_kind_id": None,
            "active_run_id": None,
            "active_work_item_kind": None,
            "active_work_item_id": None,
            "active_since": None,
        }
    active_run = _foreground_active_run(active_runs)
    return {
        "active_plane": active_run.plane,
        "active_stage": active_run.stage,
        "active_node_id": active_run.node_id,
        "active_stage_kind_id": active_run.stage_kind_id,
        "active_run_id": active_run.run_id,
        "active_work_item_kind": active_run.work_item_kind,
        "active_work_item_id": active_run.work_item_id,
        "active_since": active_run.active_since,
    }


def _foreground_active_run(active_runs: dict[Plane, ActiveRunState]) -> ActiveRunState:
    for plane in (Plane.PLANNING, Plane.EXECUTION, Plane.LEARNING):
        active_run = active_runs.get(plane)
        if active_run is not None:
            return active_run
    raise ValueError("active_runs cannot be empty")


__all__ = [
    "active_run_for_plane",
    "active_run_from_claim",
    "active_run_from_closure_target",
    "snapshot_with_active_run",
    "snapshot_with_next_stage_for_plane",
    "snapshot_projected_to_plane",
    "snapshot_without_active_plane",
    "snapshot_without_active_runs",
]
