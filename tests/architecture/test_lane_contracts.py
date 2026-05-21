from __future__ import annotations

import pytest
from pydantic import ValidationError

from millrace_ai.architecture import (
    LaneConflictPolicyDefinition,
    PlaneQueueClaimPolicyDefinition,
    WorkflowLaneDefinition,
    WorkflowPlaneSchedulerPolicyDefinition,
)
from millrace_ai.contracts import Plane


def _claim_policy(plane: Plane, *families: str) -> PlaneQueueClaimPolicyDefinition:
    return PlaneQueueClaimPolicyDefinition(
        policy_id=f"{plane.value}.default",
        plane=plane,
        family_order=families,
    )


def test_lane_accepts_spec_named_family_field() -> None:
    lane = WorkflowLaneDefinition(
        lane_id="execution.main",
        plane=Plane.EXECUTION,
        accepted_family_ids=("task",),
        claim_policy_id="execution.default",
    )

    assert lane.allowed_family_ids == ("task",)
    assert lane.accepted_family_ids == ("task",)


def test_production_scheduler_rejects_multi_active_lane_without_experimental_flag() -> None:
    claim_policy = _claim_policy(Plane.EXECUTION, "task")

    with pytest.raises(ValidationError, match="experimental_multi_lane"):
        WorkflowPlaneSchedulerPolicyDefinition(
            policy_id="default",
            plane_order=(Plane.EXECUTION,),
            lanes=(
                WorkflowLaneDefinition(
                    lane_id="execution.main",
                    plane=Plane.EXECUTION,
                    accepted_family_ids=("task",),
                    claim_policy_id=claim_policy.policy_id,
                    max_active_runs=2,
                ),
            ),
            claim_policies_by_plane={Plane.EXECUTION: claim_policy},
        )


def test_production_scheduler_rejects_two_lanes_on_same_plane_without_experimental_flag() -> None:
    claim_policy = _claim_policy(Plane.EXECUTION, "task")

    with pytest.raises(ValidationError, match="one lane per plane"):
        WorkflowPlaneSchedulerPolicyDefinition(
            policy_id="default",
            plane_order=(Plane.EXECUTION,),
            lanes=(
                WorkflowLaneDefinition(
                    lane_id="execution.main",
                    plane=Plane.EXECUTION,
                    accepted_family_ids=("task",),
                    claim_policy_id=claim_policy.policy_id,
                ),
                WorkflowLaneDefinition(
                    lane_id="execution.secondary",
                    plane=Plane.EXECUTION,
                    accepted_family_ids=("task",),
                    claim_policy_id=claim_policy.policy_id,
                ),
            ),
            claim_policies_by_plane={Plane.EXECUTION: claim_policy},
        )


def test_experimental_scheduler_allows_multiple_lanes_when_conflicts_are_declared() -> None:
    claim_policy = _claim_policy(Plane.EXECUTION, "task")
    scheduler = WorkflowPlaneSchedulerPolicyDefinition(
        policy_id="experimental",
        plane_order=(Plane.EXECUTION,),
        experimental_multi_lane=True,
        lanes=(
            WorkflowLaneDefinition(
                lane_id="execution.main",
                plane=Plane.EXECUTION,
                accepted_family_ids=("task",),
                claim_policy_id=claim_policy.policy_id,
            ),
            WorkflowLaneDefinition(
                lane_id="execution.secondary",
                plane=Plane.EXECUTION,
                accepted_family_ids=("task",),
                claim_policy_id=claim_policy.policy_id,
            ),
        ),
        claim_policies_by_plane={Plane.EXECUTION: claim_policy},
        lane_conflict_policies=(
            LaneConflictPolicyDefinition(
                policy_id="execution-lane-conflict",
                first_lane_id="execution.main",
                second_lane_id="execution.secondary",
                conflict_scopes=("workspace",),
                lock_acquisition_order=("execution.main", "execution.secondary"),
                release_policy="on_result_applied",
                missing_lock_policy="block_dispatch",
            ),
        ),
    )

    assert len(scheduler.lanes) == 2
    assert scheduler.lane_conflict_policies[0].lane_pair == (
        "execution.main",
        "execution.secondary",
    )
