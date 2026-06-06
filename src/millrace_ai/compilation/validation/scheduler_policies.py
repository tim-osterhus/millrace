"""Scheduler policy compile-time validation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.assets import WorkflowPrimitiveBundle
from millrace_ai.contracts import ModeDefinition, Plane

from ..outcomes import CompilerValidationError

if TYPE_CHECKING:
    from millrace_ai.architecture.workflow_primitives.concurrency import (
        WorkflowPlaneSchedulerPolicyDefinition,
    )


def validate_scheduler_policy_compile(
    *,
    mode: ModeDefinition,
    workflow_primitives: WorkflowPrimitiveBundle,
) -> None:
    if mode.scheduler_policy_id is None:
        return

    policy_id = mode.scheduler_policy_id
    scheduler_policies_by_id = {
        policy.policy_id: policy
        for policy in workflow_primitives.scheduler_policies
    }
    selected_policy = scheduler_policies_by_id.get(policy_id)

    if selected_policy is None:
        raise CompilerValidationError(
            f"mode {mode.mode_id} references unknown scheduler policy: {policy_id}"
        )

    mode_planes = set(mode.loop_ids_by_plane)
    policy_planes = set(selected_policy.plane_order)

    unknown_planes_in_policy = policy_planes - mode_planes
    if unknown_planes_in_policy:
        raise CompilerValidationError(
            f"scheduler policy {policy_id} references planes not in mode "
            f"{mode.mode_id}: {', '.join(sorted(p.value for p in unknown_planes_in_policy))}"
        )

    missing_mode_planes = mode_planes - policy_planes
    if missing_mode_planes:
        raise CompilerValidationError(
            f"scheduler policy {policy_id} is missing planes required by mode "
            f"{mode.mode_id}: {', '.join(sorted(p.value for p in missing_mode_planes))}"
        )

    claim_policy_ids_by_plane = {
        policy.plane: policy.policy_id
        for policy in workflow_primitives.queue_claim_policies
    }
    for lane in selected_policy.lanes:
        expected_claim_policy_id = claim_policy_ids_by_plane.get(lane.plane)
        if expected_claim_policy_id is None:
            raise CompilerValidationError(
                f"scheduler policy {policy_id} lane {lane.lane_id} references plane "
                f"{lane.plane.value} with no queue claim policy"
            )
        if lane.claim_policy_id != expected_claim_policy_id:
            raise CompilerValidationError(
                f"scheduler policy {policy_id} lane {lane.lane_id} references claim policy "
                f"{lane.claim_policy_id}, expected {expected_claim_policy_id}"
            )

    known_family_ids = frozenset(
        family.family_id for family in workflow_primitives.work_item_families
    )
    for lane in selected_policy.lanes:
        unknown_families = set(lane.allowed_family_ids) - known_family_ids
        if unknown_families:
            raise CompilerValidationError(
                f"scheduler policy {policy_id} lane {lane.lane_id} references unknown "
                f"family ids: {', '.join(sorted(unknown_families))}"
            )

    if not selected_policy.experimental_multi_lane:
        plane_lane_counts: dict[Plane, int] = {}
        for lane in selected_policy.lanes:
            plane_lane_counts[lane.plane] = plane_lane_counts.get(lane.plane, 0) + 1
        for plane, count in plane_lane_counts.items():
            if count > 1:
                raise CompilerValidationError(
                    f"scheduler policy {policy_id} has {count} lanes for plane "
                    f"{plane.value}; multi-lane per plane requires experimental_multi_lane=True"
                )

    _validate_scheduler_policy_rules(selected_policy, policy_id=policy_id)
    _validate_scheduler_policy_residual_surfaces(
        selected_policy, policy_id=policy_id, mode=mode
    )


def _validate_scheduler_policy_residual_surfaces(
    policy: WorkflowPlaneSchedulerPolicyDefinition,
    *,
    policy_id: str,
    mode: ModeDefinition,
) -> None:
    """Validate residual-surface fields against mode and workflow authority."""

    mode_planes = set(mode.loop_ids_by_plane)

    if policy.learning_target_stage_kind_id is not None:
        if Plane.LEARNING not in mode_planes:
            raise CompilerValidationError(
                f"scheduler policy {policy_id} has learning_target_stage_kind_id "
                f"but mode {mode.mode_id} does not include a learning plane"
            )


def _validate_scheduler_policy_rules(
    policy: WorkflowPlaneSchedulerPolicyDefinition,
    *,
    policy_id: str,
) -> None:
    """Validate rule predicates, planes, and order overrides at compile time."""

    predicate_ids = {pred.predicate_id for pred in policy.predicates}
    policy_planes = set(policy.plane_order)

    for rule in policy.rules:
        # Rule predicate_id must reference a known predicate when present.
        if rule.predicate_id is not None and rule.predicate_id not in predicate_ids:
            raise CompilerValidationError(
                f"scheduler policy {policy_id} rule {rule.rule_id} references "
                f"unknown predicate_id: {rule.predicate_id}"
            )

        # Rule target_plane must reference a valid plane in the policy.
        if rule.target_plane is not None and rule.target_plane not in policy_planes:
            raise CompilerValidationError(
                f"scheduler policy {policy_id} rule {rule.rule_id} references "
                f"unknown target_plane: {rule.target_plane.value}"
            )

        # Rule order_override planes must be valid.
        if rule.order_override is not None:
            unknown_planes = set(rule.order_override) - policy_planes
            if unknown_planes:
                raise CompilerValidationError(
                    f"scheduler policy {policy_id} rule {rule.rule_id} "
                    f"order_override references planes not in policy plane_order: "
                    f"{', '.join(sorted(p.value for p in unknown_planes))}"
                )


__all__ = ["validate_scheduler_policy_compile"]
