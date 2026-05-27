"""Lane conflict coverage validation helpers."""

from __future__ import annotations

from millrace_ai.architecture import WorkflowPlaneSchedulerPolicyDefinition
from millrace_ai.contracts import ModeDefinition, Plane

from ..outcomes import CompilerValidationError


def validate_lane_conflict_coverage(
    *,
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition,
    mode: ModeDefinition,
) -> None:
    if mode.concurrency_policy is None:
        return

    lanes_by_plane: dict[Plane, tuple[str, ...]] = {}
    for lane in scheduler_policy.lanes:
        lanes_by_plane.setdefault(lane.plane, ())
        lanes_by_plane[lane.plane] = (*lanes_by_plane[lane.plane], lane.lane_id)

    covered_pairs = {
        pair
        for policy in scheduler_policy.lane_conflict_policies
        for pair in policy.lane_pairs
    }
    for plane_pair in mode.concurrency_policy.may_run_concurrently:
        if len(plane_pair) != 2:
            raise CompilerValidationError("may_run_concurrently entries must name exactly two planes")
        first_plane, second_plane = plane_pair
        for first_lane_id in lanes_by_plane.get(first_plane, ()):
            for second_lane_id in lanes_by_plane.get(second_plane, ()):
                lane_pair = tuple(sorted((first_lane_id, second_lane_id)))
                if lane_pair not in covered_pairs:
                    raise CompilerValidationError(
                        "lane conflict policy missing for concurrent lane pair "
                        f"{lane_pair[0]} + {lane_pair[1]}"
                    )


__all__ = ["validate_lane_conflict_coverage"]
