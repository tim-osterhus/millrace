from __future__ import annotations

from millrace_ai.contracts import Plane, PlaneConcurrencyPolicyDefinition
from millrace_ai.runtime.plane_concurrency import can_dispatch_plane


def _learning_policy() -> PlaneConcurrencyPolicyDefinition:
    return PlaneConcurrencyPolicyDefinition(
        mutually_exclusive_planes=((Plane.EXECUTION, Plane.PLANNING),),
        may_run_concurrently=(
            (Plane.LEARNING, Plane.EXECUTION),
            (Plane.LEARNING, Plane.PLANNING),
        ),
    )


def test_missing_policy_keeps_runtime_serial() -> None:
    assert can_dispatch_plane(
        policy=None,
        active_planes={Plane.EXECUTION},
        candidate=Plane.LEARNING,
    ) is False


def test_missing_policy_allows_dispatch_when_no_plane_is_active() -> None:
    assert can_dispatch_plane(
        policy=None,
        active_planes=set(),
        candidate=Plane.EXECUTION,
    ) is True


def test_learning_policy_blocks_execution_and_planning_overlap() -> None:
    assert can_dispatch_plane(
        policy=_learning_policy(),
        active_planes={Plane.EXECUTION},
        candidate=Plane.PLANNING,
    ) is False


def test_learning_policy_allows_learning_with_execution() -> None:
    assert can_dispatch_plane(
        policy=_learning_policy(),
        active_planes={Plane.EXECUTION},
        candidate=Plane.LEARNING,
    ) is True


def test_learning_policy_allows_learning_with_planning() -> None:
    assert can_dispatch_plane(
        policy=_learning_policy(),
        active_planes={Plane.PLANNING},
        candidate=Plane.LEARNING,
    ) is True


def test_mutual_exclusion_wins_over_concurrency_allow_list() -> None:
    policy = PlaneConcurrencyPolicyDefinition(
        mutually_exclusive_planes=((Plane.LEARNING, Plane.EXECUTION),),
        may_run_concurrently=((Plane.LEARNING, Plane.EXECUTION),),
    )

    assert can_dispatch_plane(
        policy=policy,
        active_planes={Plane.EXECUTION},
        candidate=Plane.LEARNING,
    ) is False


def test_candidate_cannot_dispatch_when_same_plane_is_active() -> None:
    assert can_dispatch_plane(
        policy=_learning_policy(),
        active_planes={Plane.LEARNING},
        candidate=Plane.LEARNING,
    ) is False
