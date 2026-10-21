"""Exact runtime ownership for attempt-owned recovery terminal actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from millrace.contracts.compiled_plan import (
    RecoveryPolicyDeclaration,
    SelectedCompiledPlan,
    TerminalActionDeclaration,
)
from millrace.contracts.runtime_failure_history import (
    current_recovery_return_matches,
    recovery_attempt_history_matches,
)
from millrace.contracts.state import (
    Activation,
    RecoveryAttemptRecord,
    RunRecord,
    RuntimeState,
    WorkItem,
)

RecoveryActionContextRefusalReason = Literal[
    "recovery_action_context_missing",
    "recovery_action_context_ambiguous",
    "recovery_action_context_stale",
    "recovery_action_context_phase",
    "recovery_action_context_ownership",
]


@dataclass(frozen=True, slots=True)
class RecoveryActionContext:
    policy: RecoveryPolicyDeclaration
    attempt: RecoveryAttemptRecord


@dataclass(frozen=True, slots=True)
class RecoveryActionContextRefusal:
    reason: RecoveryActionContextRefusalReason


RecoveryActionContextResult = RecoveryActionContext | RecoveryActionContextRefusal


def recovery_action_context_for_terminal_action(
    *,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
) -> RecoveryActionContextResult:
    """Resolve the unique selected attempt that owns this action invocation."""
    if action.action_kind == "return_to_recorded_source":
        action_policies = tuple(
            policy
            for policy in selected_plan.recovery_policies
            if action.id in policy.return_action_ids
        )
    elif action.action_kind == "quarantine_lineage":
        action_policies = tuple(
            policy
            for policy in selected_plan.recovery_policies
            if action.id in policy.quarantine_action_ids
        )
    else:
        return RecoveryActionContextRefusal("recovery_action_context_missing")

    if not action_policies:
        return RecoveryActionContextRefusal("recovery_action_context_ownership")
    if any(
        policy.recovery_stage_kind_id != action.stage_kind_id
        or action.stage_kind_id != run.stage_kind_id
        for policy in action_policies
    ):
        return RecoveryActionContextRefusal("recovery_action_context_ownership")
    if (
        run.activation_id != activation.activation_id
        or activation.claimed_by_run_id != run.run_ref.run_id
        or activation.plan_ref != run.run_ref.plan_ref
        or work_item.ref.plan_ref != run.run_ref.plan_ref
    ):
        return RecoveryActionContextRefusal("recovery_action_context_ownership")
    if work_item.lineage_id is None:
        return RecoveryActionContextRefusal("recovery_action_context_ownership")

    plan_ref = run.run_ref.plan_ref
    admitted = state.admitted_plans.get(plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != plan_ref:
        return RecoveryActionContextRefusal("recovery_action_context_stale")
    if admitted.selected_plan != selected_plan:
        return RecoveryActionContextRefusal("recovery_action_context_ownership")

    owner_ids = {policy.id for policy in action_policies}
    attempts = tuple(state.recovery_attempts.values())
    current_attempts = tuple(
        attempt
        for attempt in attempts
        if attempt.policy_id in owner_ids
        and attempt.latest_recovery_run_id == run.run_ref.run_id
        and attempt.latest_recovery_activation_id == activation.activation_id
    )
    foreign_current_attempts = tuple(
        attempt
        for attempt in attempts
        if attempt.policy_id not in owner_ids
        and attempt.latest_recovery_run_id == run.run_ref.run_id
        and attempt.latest_recovery_activation_id == activation.activation_id
    )
    if foreign_current_attempts:
        return RecoveryActionContextRefusal("recovery_action_context_ownership")
    if len(current_attempts) > 1:
        return RecoveryActionContextRefusal("recovery_action_context_ambiguous")
    if not current_attempts:
        if _ordinary_ingress_has_accepted_creator(state, selected_plan, activation):
            return RecoveryActionContextRefusal("recovery_action_context_missing")
        plan_lineage_attempts = tuple(
            attempt
            for attempt in attempts
            if attempt.policy_id in owner_ids
            and attempt.plan_ref == plan_ref
            and attempt.lineage_id == work_item.lineage_id
        )
        if not plan_lineage_attempts:
            return RecoveryActionContextRefusal("recovery_action_context_stale")
        source_owned_attempts = tuple(
            attempt
            for attempt in plan_lineage_attempts
            if attempt.source_work_item_id == work_item.ref.work_item_id
        )
        if not source_owned_attempts:
            return RecoveryActionContextRefusal("recovery_action_context_ownership")
        return RecoveryActionContextRefusal("recovery_action_context_stale")

    attempt = current_attempts[0]
    if (
        attempt.plan_ref != plan_ref
        or attempt.lineage_id != work_item.lineage_id
        or attempt.source_work_item_id != work_item.ref.work_item_id
    ):
        return RecoveryActionContextRefusal("recovery_action_context_ownership")
    policy = next(
        (
            candidate
            for candidate in action_policies
            if candidate.id == attempt.policy_id
        ),
        None,
    )
    if policy is None:
        return RecoveryActionContextRefusal("recovery_action_context_ownership")
    allowed_phases = (
        set(policy.return_allowed_phases)
        if action.action_kind == "return_to_recorded_source"
        else {"active_recovery", "quarantine_eligible"}
    )
    if attempt.phase not in allowed_phases:
        return RecoveryActionContextRefusal("recovery_action_context_phase")
    if not recovery_attempt_history_matches(
        state, attempt, policy
    ) or not current_recovery_return_matches(state, selected_plan, attempt, policy):
        return RecoveryActionContextRefusal("recovery_action_context_stale")
    return RecoveryActionContext(policy=policy, attempt=attempt)


def _ordinary_ingress_has_accepted_creator(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
) -> bool:
    """Keep an absent formal attempt distinct from an ordinary bad marker."""
    creator_id = activation.created_by_input_id
    receipt = state.receipts.get(creator_id)
    transitions = tuple(t for t in state.transitions if t.input_id == creator_id)
    events = tuple(e for e in state.governance_events if e.input_id == creator_id)
    traces = tuple(t for t in state.traces if t.input_id == creator_id)
    if (
        receipt is None
        or not receipt.accepted
        or len(transitions) != 1
        or not transitions[0].accepted
        or len(events) != 1
        or len(traces) != 1
        or events[0].disposition != "accepted"
        or traces[0].disposition != "accepted"
        or receipt.transition_id != transitions[0].record_id
        or receipt.receipt_ref.input_id != creator_id
        or events[0].record_id != f"{transitions[0].record_id}:governance"
        or traces[0].record_id != f"{transitions[0].record_id}:trace"
        or events[0].plan_fingerprint != activation.plan_ref.authority_fingerprint
        or (
            traces[0].input_id,
            traces[0].input_kind,
            traces[0].input_family,
            traces[0].plan_fingerprint,
            traces[0].work_item_id,
            traces[0].run_id,
            traces[0].action_id,
            traces[0].authority_source,
        )
        != (
            events[0].input_id,
            events[0].input_kind,
            events[0].input_family,
            events[0].plan_fingerprint,
            events[0].work_item_id,
            events[0].run_id,
            events[0].action_id,
            events[0].authority_source,
        )
        or events[0].input_kind != transitions[0].input_kind
        or events[0].input_family != transitions[0].input_family
    ):
        return False
    if transitions[0].input_kind not in {
        "workflow.enqueue_work",
        "workflow.fanout_from_artifact",
        "workflow.join_from_artifact",
        "workflow.evaluate_completion_behavior",
        "workflow.runner_result_observed",
        "workflow.operator_resume_wait",
        "workflow.operator_revise_wait",
    }:
        return False
    action_id = events[0].action_id
    if action_id is not None:
        action = next(
            (a for a in selected_plan.terminal_actions if a.id == action_id), None
        )
        if action is None or action.action_kind in {
            "recovery_route",
            "return_to_recorded_source",
            "quarantine_lineage",
        }:
            return False
    routes = tuple(
        r
        for r in state.activation_routes
        if r.target_activation_id == activation.activation_id
    )
    if len(routes) > 1:
        return False
    if routes and (
        routes[0].created_by_input_id != creator_id
        or routes[0].action_id != action_id
        or routes[0].target_work_item_id != activation.work_item_id
    ):
        return False
    if transitions[0].input_kind == "workflow.runner_result_observed":
        return (
            len(routes) == 1
            and routes[0].created_by_input_id == creator_id
            and routes[0].action_id == action_id
            and routes[0].target_work_item_id == activation.work_item_id
        )
    return True


__all__ = [
    "RecoveryActionContext",
    "RecoveryActionContextRefusal",
    "RecoveryActionContextRefusalReason",
    "RecoveryActionContextResult",
    "recovery_action_context_for_terminal_action",
]
