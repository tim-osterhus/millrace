"""Durable lane-state helpers for runtime snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import cast

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.contracts import ActiveRunState, LaneRuntimeState, Plane, RuntimeSnapshot


def compiled_plan_fingerprint_for_runtime(compiled_plan: CompiledRunPlan) -> str:
    """Return the compact launch fingerprint persisted on active runtime records."""

    payload = compiled_plan.compile_input_fingerprint.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"compile-input-{hashlib.sha256(encoded).hexdigest()[:16]}"


def lane_id_for_plane(compiled_plan: CompiledRunPlan | None, plane: Plane) -> str:
    """Return the compiled lane used for a plane during the plane-keyed migration."""

    scheduler_policy = getattr(compiled_plan, "scheduler_policy", None)
    if scheduler_policy is not None:
        for lane in scheduler_policy.lanes:
            if lane.plane is plane:
                return cast(str, lane.lane_id)
    return default_lane_id_for_plane(plane)


def lane_dispatch_order(compiled_plan: CompiledRunPlan | None) -> tuple[str, ...]:
    """Return deterministic supervisor lane order for the active compiled plan."""

    scheduler_policy = getattr(compiled_plan, "scheduler_policy", None)
    if scheduler_policy is None:
        return (
            default_lane_id_for_plane(Plane.PLANNING),
            default_lane_id_for_plane(Plane.EXECUTION),
            default_lane_id_for_plane(Plane.LEARNING),
        )
    lane_ids = {lane.lane_id for lane in scheduler_policy.lanes}
    preferred = tuple(
        lane_id
        for lane_id in (
            default_lane_id_for_plane(Plane.PLANNING),
            default_lane_id_for_plane(Plane.EXECUTION),
            default_lane_id_for_plane(Plane.LEARNING),
        )
        if lane_id in lane_ids
    )
    remaining = tuple(sorted(lane_ids - set(preferred)))
    return (*preferred, *remaining)


def default_lane_id_for_plane(plane: Plane) -> str:
    return f"{plane.value}.main"


def ensure_snapshot_lanes(
    snapshot: RuntimeSnapshot,
    compiled_plan: CompiledRunPlan,
) -> RuntimeSnapshot:
    """Ensure a snapshot has lane state for every lane declared by the compiled plan."""

    declared_lanes = _declared_lane_specs(compiled_plan)
    fingerprint = compiled_plan_fingerprint_for_runtime(compiled_plan)
    lanes_by_id = dict(snapshot.lanes_by_id)
    active_runs_by_lane = _active_runs_by_lane(snapshot.active_runs_by_plane.values())

    for lane_id, plane in declared_lanes:
        existing = lanes_by_id.get(lane_id)
        active_runs = active_runs_by_lane.get(lane_id, ())
        if active_runs:
            lanes_by_id[lane_id] = _lane_state_for_active_runs(
                lane_id=lane_id,
                plane=plane,
                active_runs=active_runs,
            )
            continue
        if existing is not None and existing.active_run_ids:
            lanes_by_id[lane_id] = existing
            continue
        lanes_by_id[lane_id] = LaneRuntimeState(
            lane_id=lane_id,
            plane=plane,
            status=(existing.status if existing is not None and existing.status != "active" else "idle"),
            compiled_plan_id=compiled_plan.compiled_plan_id,
            compiled_plan_fingerprint=fingerprint,
            pause_requested=(existing.pause_requested if existing is not None else False),
            stop_requested=(existing.stop_requested if existing is not None else False),
            drain_requested=(existing.drain_requested if existing is not None else False),
            mutation_lock_refs=(existing.mutation_lock_refs if existing is not None else ()),
            completion_target_refs=(existing.completion_target_refs if existing is not None else ()),
            failure_counter_refs=(existing.failure_counter_refs if existing is not None else ()),
            last_claim_attempt_at=(existing.last_claim_attempt_at if existing is not None else None),
            last_terminal_outcome=(existing.last_terminal_outcome if existing is not None else None),
        )

    return snapshot.model_copy(
        update={
            "compiled_plan_fingerprint": fingerprint,
            "lanes_by_id": lanes_by_id,
        }
    )


def snapshot_with_lane_active_run(
    snapshot: RuntimeSnapshot,
    active_run: ActiveRunState,
) -> RuntimeSnapshot:
    lanes_by_id = dict(snapshot.lanes_by_id)
    lanes_by_id[active_run.lane_id] = _lane_state_for_active_runs(
        lane_id=active_run.lane_id,
        plane=active_run.plane,
        active_runs=(active_run,),
    )
    return snapshot.model_copy(update={"lanes_by_id": lanes_by_id})


def snapshot_without_lane_active_run(
    snapshot: RuntimeSnapshot,
    *,
    active_run: ActiveRunState,
) -> RuntimeSnapshot:
    lanes_by_id = dict(snapshot.lanes_by_id)
    existing = lanes_by_id.get(active_run.lane_id)
    if existing is None:
        return snapshot
    remaining_ids = tuple(run_id for run_id in existing.active_run_ids if run_id != active_run.run_id)
    if remaining_ids:
        lanes_by_id[active_run.lane_id] = existing.model_copy(update={"active_run_ids": remaining_ids})
        return snapshot.model_copy(update={"lanes_by_id": lanes_by_id})
    lanes_by_id[active_run.lane_id] = existing.model_copy(
        update={
            "status": "idle",
            "active_run_ids": (),
            "active_work_refs": (),
        }
    )
    return snapshot.model_copy(update={"lanes_by_id": lanes_by_id})


def snapshot_without_lane_active_runs(snapshot: RuntimeSnapshot) -> RuntimeSnapshot:
    lanes_by_id = {
        lane_id: lane_state.model_copy(
            update={
                "status": "idle",
                "active_run_ids": (),
                "active_work_refs": (),
            }
        )
        for lane_id, lane_state in snapshot.lanes_by_id.items()
    }
    return snapshot.model_copy(update={"lanes_by_id": lanes_by_id})


def _declared_lane_specs(compiled_plan: CompiledRunPlan) -> tuple[tuple[str, Plane], ...]:
    scheduler_policy = getattr(compiled_plan, "scheduler_policy", None)
    if scheduler_policy is not None:
        return tuple((lane.lane_id, lane.plane) for lane in scheduler_policy.lanes)
    return tuple(
        (default_lane_id_for_plane(plane), plane)
        for plane in (Plane.EXECUTION, Plane.PLANNING, Plane.LEARNING)
        if plane in compiled_plan.loop_ids_by_plane
    )


def _active_runs_by_lane(
    active_runs: Iterable[ActiveRunState],
) -> dict[str, tuple[ActiveRunState, ...]]:
    grouped: dict[str, list[ActiveRunState]] = {}
    for active_run in active_runs:
        grouped.setdefault(active_run.lane_id, []).append(active_run)
    return {lane_id: tuple(lane_runs) for lane_id, lane_runs in grouped.items()}


def _lane_state_for_active_runs(
    *,
    lane_id: str,
    plane: Plane,
    active_runs: tuple[ActiveRunState, ...],
) -> LaneRuntimeState:
    primary = active_runs[0]
    return LaneRuntimeState(
        lane_id=lane_id,
        plane=plane,
        status="active",
        compiled_plan_id=primary.compiled_plan_id,
        compiled_plan_fingerprint=primary.compiled_plan_fingerprint,
        active_run_ids=tuple(active_run.run_id for active_run in active_runs),
        active_work_refs=tuple(_active_work_ref(active_run) for active_run in active_runs),
    )


def _active_work_ref(active_run: ActiveRunState) -> str:
    if active_run.work_item_family_id is not None and active_run.work_item_id is not None:
        return f"{active_run.work_item_family_id}:{active_run.work_item_id}"
    if active_run.closure_target_root_spec_id is not None:
        return f"closure_target:{active_run.closure_target_root_spec_id}"
    return f"run:{active_run.run_id}"


__all__ = [
    "compiled_plan_fingerprint_for_runtime",
    "default_lane_id_for_plane",
    "ensure_snapshot_lanes",
    "lane_dispatch_order",
    "lane_id_for_plane",
    "snapshot_with_lane_active_run",
    "snapshot_without_lane_active_run",
    "snapshot_without_lane_active_runs",
]
