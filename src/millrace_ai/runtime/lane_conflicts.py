"""Lane-aware runtime dispatch checks."""

from __future__ import annotations

from collections.abc import Iterable

from millrace_ai.architecture import WorkflowPlaneSchedulerPolicyDefinition
from millrace_ai.contracts import PlaneConcurrencyPolicyDefinition

from .plane_concurrency import can_dispatch_plane


def can_dispatch_lane(
    *,
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition | None,
    concurrency_policy: PlaneConcurrencyPolicyDefinition | None,
    active_lane_ids: Iterable[str],
    candidate_lane_id: str,
) -> bool:
    """Return whether a candidate lane may start beside active lanes."""

    if scheduler_policy is None:
        return not tuple(active_lane_ids)

    lanes_by_id = {lane.lane_id: lane for lane in scheduler_policy.lanes}
    candidate_lane = lanes_by_id.get(candidate_lane_id)
    if candidate_lane is None:
        return False

    active_ids = tuple(dict.fromkeys(active_lane_ids))
    if candidate_lane_id in active_ids:
        return False
    active_lanes = []
    for lane_id in active_ids:
        active_lane = lanes_by_id.get(lane_id)
        if active_lane is None:
            return False
        active_lanes.append(active_lane)

    if not can_dispatch_plane(
        policy=concurrency_policy,
        active_planes=(lane.plane for lane in active_lanes),
        candidate=candidate_lane.plane,
    ):
        return False

    for active_lane in active_lanes:
        if not _has_conflict_policy(
            scheduler_policy,
            first_lane_id=active_lane.lane_id,
            second_lane_id=candidate_lane_id,
        ):
            return False
    return True


def _has_conflict_policy(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition,
    *,
    first_lane_id: str,
    second_lane_id: str,
) -> bool:
    pair = tuple(sorted((first_lane_id, second_lane_id)))
    for policy in scheduler_policy.lane_conflict_policies:
        if pair not in policy.lane_pairs:
            continue
        lock_order = set(policy.lock_acquisition_order)
        if policy.conflict_scopes and not set(pair).issubset(lock_order):
            return False
        return True
    return False


__all__ = ["can_dispatch_lane"]
