from __future__ import annotations

from millrace_ai.architecture import (
    LaneConflictPolicyDefinition,
    PlaneQueueClaimPolicyDefinition,
    WorkflowLaneDefinition,
    WorkflowPlaneSchedulerPolicyDefinition,
)
from millrace_ai.contracts import Plane, PlaneConcurrencyPolicyDefinition
from millrace_ai.runtime.lane_conflicts import can_dispatch_lane


def _claim_policy(plane: Plane, *families: str) -> PlaneQueueClaimPolicyDefinition:
    return PlaneQueueClaimPolicyDefinition(
        policy_id=f"{plane.value}.default",
        plane=plane,
        family_order=families,
    )


def _scheduler(
    *,
    conflict_policies: tuple[LaneConflictPolicyDefinition, ...] = (),
) -> WorkflowPlaneSchedulerPolicyDefinition:
    execution_claim = _claim_policy(Plane.EXECUTION, "task")
    learning_claim = _claim_policy(Plane.LEARNING, "learning_request")
    return WorkflowPlaneSchedulerPolicyDefinition(
        policy_id="test.scheduler",
        plane_order=(Plane.EXECUTION, Plane.LEARNING),
        concurrency_policy_id="test.concurrency",
        lanes=(
            WorkflowLaneDefinition(
                lane_id="execution.main",
                plane=Plane.EXECUTION,
                accepted_family_ids=("task",),
                claim_policy_id=execution_claim.policy_id,
            ),
            WorkflowLaneDefinition(
                lane_id="learning.main",
                plane=Plane.LEARNING,
                accepted_family_ids=("learning_request",),
                claim_policy_id=learning_claim.policy_id,
            ),
        ),
        claim_policies_by_plane={
            Plane.EXECUTION: execution_claim,
            Plane.LEARNING: learning_claim,
        },
        lane_conflict_policies=conflict_policies,
    )


def _learning_concurrency_policy() -> PlaneConcurrencyPolicyDefinition:
    return PlaneConcurrencyPolicyDefinition(
        may_run_concurrently=((Plane.EXECUTION, Plane.LEARNING),),
    )


def test_lane_dispatch_allows_overlap_when_plane_and_lane_policies_allow_it() -> None:
    scheduler = _scheduler(
        conflict_policies=(
            LaneConflictPolicyDefinition(
                policy_id="execution-learning",
                first_lane_id="execution.main",
                second_lane_id="learning.main",
                conflict_scopes=("workspace",),
                lock_acquisition_order=("execution.main", "learning.main"),
                release_policy="on_result_applied",
                missing_lock_policy="block_dispatch",
            ),
        )
    )

    assert (
        can_dispatch_lane(
            scheduler_policy=scheduler,
            concurrency_policy=_learning_concurrency_policy(),
            active_lane_ids=("execution.main",),
            candidate_lane_id="learning.main",
        )
        is True
    )


def test_lane_dispatch_blocks_overlap_without_lane_conflict_policy() -> None:
    assert (
        can_dispatch_lane(
            scheduler_policy=_scheduler(),
            concurrency_policy=_learning_concurrency_policy(),
            active_lane_ids=("execution.main",),
            candidate_lane_id="learning.main",
        )
        is False
    )


def test_lane_dispatch_blocks_unknown_candidate_lane() -> None:
    assert (
        can_dispatch_lane(
            scheduler_policy=_scheduler(),
            concurrency_policy=_learning_concurrency_policy(),
            active_lane_ids=("execution.main",),
            candidate_lane_id="learning.secondary",
        )
        is False
    )
