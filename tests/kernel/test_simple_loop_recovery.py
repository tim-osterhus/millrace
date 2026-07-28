from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

import millrace.contracts.transition as transition_contracts
import millrace.operator as operator_api
from kernel.simple_loop_scenarios import (
    bootstrap_to_manager_claim,
    bootstrap_to_manager_cooldown_wait,
    bootstrap_to_manager_retry_claim_after_first_recovery,
    bootstrap_to_reviewer_claim,
    bootstrap_to_worker_claim,
)
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import (
    ActionId,
    ArtifactSchemaId,
    ClaimWork,
    EnqueueWork,
    QueueFamilyId,
    RecoveryPolicyId,
)
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.state import (
    Activation,
    ClosedWorkItemRecord,
    CooldownWaitRecord,
    LineageQuarantineRecord,
    RecoveryAttemptRecord,
    RunRecord,
    RunRef,
    RuntimeState,
)
from millrace.contracts.transition import (
    OperatorReviseLineage,
    RunnerResultObserved,
    TransitionDecision,
    TransitionInput,
)
from millrace.kernel import StateConcurrencyError, apply, decide
from millrace.operator import operator_status
from millrace.operator.dispatch import (
    build_dispatch_envelope_for_run as _build_dispatch_envelope_for_run,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
    fake_runner_session_state,
)
from support.simple_loop import (
    action_by_id,
    compile_simple_loop,
    mutation_kinds,
    runner_observation,
    simple_loop_context,
    troubleshooting_report_payload,
    work_packet_payload,
    work_prompt_payload,
)

MANAGER_PACKET_READY_ACTION_ID = ActionId("simple_loop.manager.packet_ready")
MANAGER_BLOCKED_ACTION_ID = ActionId("simple_loop.manager.blocked")
WORKER_BLOCKED_ACTION_ID = ActionId("simple_loop.worker.blocked")
WORKER_FAILED_ACTION_ID = ActionId("simple_loop.worker.failed")
REVIEWER_BLOCKED_ACTION_ID = ActionId("simple_loop.reviewer.blocked")


def build_dispatch_envelope_for_run(*, state, run_id):
    return _build_dispatch_envelope_for_run(
        state=fake_runner_session_state(state=state, run_id=run_id),
        run_id=run_id,
    )
TROUBLESHOOTER_RESOLVED_ACTION_ID = ActionId("simple_loop.troubleshooter.resolved")
TROUBLESHOOTER_OPERATOR_NEEDED_ACTION_ID = ActionId(
    "simple_loop.troubleshooter.operator_needed"
)
RECOVERY_POLICY_ID = "simple_loop.blocked_recovery"

PROGRESS_MUTATIONS = {
    "mutation.record_runner_observation",
    "mutation.record_artifact",
    "mutation.create_work_item",
    "mutation.create_activation",
    "mutation.route_activation",
    "mutation.record_recovery_attempt",
    "mutation.record_lineage_quarantine",
    "mutation.close_work_item",
    "mutation.set_pause",
    "mutation.set_quarantine",
}


def _recovery_observation(
    *,
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    action_id: ActionId,
    input_id: str,
    marker: str | None = None,
    observed_at: int | None = None,
) -> RunnerResultObserved:
    return runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=str(action_id),
        input_id=input_id,
        artifact_payload={},
        marker=marker,
        observed_at=observed_at,
    )


def _assert_no_forbidden_recovery_mutations(decision: TransitionDecision) -> None:
    forbidden = {
        "mutation.record_artifact",
        "mutation.create_work_item",
        "mutation.close_work_item",
        "mutation.set_pause",
        "mutation.set_quarantine",
    }
    assert forbidden.isdisjoint(mutation_kinds(decision))


def _assert_no_workflow_progress(decision: TransitionDecision) -> None:
    assert PROGRESS_MUTATIONS.isdisjoint(mutation_kinds(decision))


def _assert_audit_context(
    decision: TransitionDecision,
    *,
    fingerprint: str,
    work_item_id: str,
    run_id: str,
    action_id: ActionId,
    refusal_reason: str | None = None,
) -> None:
    assert len(decision.governance_events) == 1
    assert len(decision.trace_records) == 1
    for record in (*decision.governance_events, *decision.trace_records):
        assert record.plan_fingerprint == fingerprint
        assert record.work_item_id == work_item_id
        assert record.run_id == run_id
        assert record.action_id == action_id
        assert record.authority_source == "terminal_action"
        assert record.refusal_reason == refusal_reason


def _assert_recovery_activation(
    *,
    activation: Activation,
    source_work_item_id: str,
    lineage_id: str,
    fingerprint: str,
    queue_family_id: QueueFamilyId,
) -> None:
    assert activation.work_item_id == source_work_item_id
    assert activation.lineage_id == lineage_id
    assert activation.plan_ref.authority_fingerprint == fingerprint
    assert activation.queue_family_id == queue_family_id
    assert str(activation.stage_kind_id) == "simple_loop.troubleshooter"
    assert activation.graph_node_id == "simple_loop.troubleshooter.start"
    assert str(activation.runner_binding_id) == "simple_loop.default_agent_runner"
    assert activation.claimed_by_run_id is None


def _assert_recovery_route(
    *,
    after: RuntimeState,
    action_id: ActionId,
    source_run_id: str,
    source_work_item_id: str,
    target_activation_id: str,
) -> None:
    route = after.activation_routes[-1]
    assert route.action_id == action_id
    assert route.source_run_id == source_run_id
    assert route.source_work_item_id == source_work_item_id
    assert route.target_work_item_id == source_work_item_id
    assert route.target_activation_id == target_activation_id


def _only_recovery_attempt(state: RuntimeState) -> RecoveryAttemptRecord:
    attempts = tuple(state.recovery_attempts.values())
    assert len(attempts) == 1
    return attempts[0]


def _active_lineage_quarantine(
    state: RuntimeState,
    lineage_id: str,
) -> LineageQuarantineRecord:
    records = tuple(
        quarantine
        for quarantine in state.lineage_quarantines.values()
        if quarantine.lineage_id == lineage_id and quarantine.status == "active"
    )
    assert len(records) == 1
    return records[0]


def _only_cooldown_wait(state: RuntimeState) -> CooldownWaitRecord:
    waits = tuple(state.cooldown_waits.values())
    assert len(waits) == 1
    return waits[0]


def _timer_due(input_id: str, *, wait_id: str, observed_at: int) -> TransitionInput:
    timer_type = getattr(transition_contracts, "TimerDue")
    return cast(
        TransitionInput,
        timer_type(input_id, wait_id=wait_id, observed_at=observed_at),
    )


def _resume_only_cooldown_wait(
    state: RuntimeState,
    *,
    input_id: str = "timer-cooldown-due",
    observed_at: int = 1900,
    activation_id: str = "activation-troubleshooter-manager-resumed",
) -> RuntimeState:
    wait = _only_cooldown_wait(state)
    return apply(
        state,
        decide(
            state,
            _timer_due(input_id, wait_id=wait.wait_id, observed_at=observed_at),
            deterministic_context(
                transition_id=f"transition-{input_id}",
                activation_id=activation_id,
            ),
        ),
    )


def _claim_recovery_activation(
    state: RuntimeState,
    *,
    input_id: str,
    activation_id: str,
    run_id: str,
    claim_id: str,
    fencing_token: str,
) -> RuntimeState:
    return apply(
        state,
        decide(
            state,
            ClaimWork(input_id, activation_id=activation_id),
            deterministic_context(
                transition_id=f"transition-{input_id}",
                run_id=run_id,
                claim_id=claim_id,
                fencing_token=fencing_token,
            ),
        ),
    )


def _third_source_retry_claimed(
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    waiting = bootstrap_to_manager_cooldown_wait(plan, fingerprint, observed_at=1000)
    resumed = _resume_only_cooldown_wait(waiting)
    resumed_claimed = _claim_recovery_activation(
        resumed,
        input_id="claim-troubleshooter-manager-resumed",
        activation_id="activation-troubleshooter-manager-resumed",
        run_id="run-troubleshooter-manager-resumed",
        claim_id="claim-troubleshooter-manager-resumed",
        fencing_token="fence-troubleshooter-manager-resumed",
    )
    _return_decision, returned = _return_from_troubleshooter(
        resumed_claimed,
        plan=plan,
        fingerprint=fingerprint,
        input_id="observe-troubleshooter-resolved-after-cooldown",
        run_id="run-troubleshooter-manager-resumed",
        activation_id="activation-returned-manager-2",
    )
    assert _return_decision.accepted is True
    third_source = apply(
        returned,
        decide(
            returned,
            ClaimWork(
                "claim-returned-manager-2",
                activation_id="activation-returned-manager-2",
            ),
            deterministic_context(
                transition_id="transition-claim-returned-manager-2",
                run_id="run-source-retry-3",
                claim_id="claim-source-retry-3",
                fencing_token="fence-source-retry-3",
            ),
        ),
    )
    return third_source


def _return_from_troubleshooter(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    input_id: str,
    run_id: str,
    activation_id: str,
    action_id: ActionId = TROUBLESHOOTER_RESOLVED_ACTION_ID,
) -> tuple[TransitionDecision, RuntimeState]:
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=run_id,
            action_id=str(action_id),
            input_id=input_id,
            artifact_payload=troubleshooting_report_payload(),
        ),
        deterministic_context(
            transition_id=f"transition-{input_id}",
            activation_id=activation_id,
        ),
    )
    return decision, apply(state, decision) if decision.accepted else state


def _queue_counts(
    state: RuntimeState,
    queue_family_id: str,
) -> tuple[int, int, int, int]:
    family = next(
        family
        for family in operator_status(state).queue_families
        if family.queue_family_id == queue_family_id
    )
    return (
        family.ready_count,
        family.active_count,
        family.closed_count,
        family.quarantined_count,
    )


def _lineage_quarantined_state(
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    waiting = bootstrap_to_manager_cooldown_wait(plan, fingerprint, observed_at=1000)
    resumed = _resume_only_cooldown_wait(waiting)
    resumed_claimed = _claim_recovery_activation(
        resumed,
        input_id="claim-troubleshooter-manager-resumed",
        activation_id="activation-troubleshooter-manager-resumed",
        run_id="run-troubleshooter-manager-resumed",
        claim_id="claim-troubleshooter-manager-resumed",
        fencing_token="fence-troubleshooter-manager-resumed",
    )
    _return_decision, returned = _return_from_troubleshooter(
        resumed_claimed,
        plan=plan,
        fingerprint=fingerprint,
        input_id="observe-troubleshooter-resolved-after-cooldown",
        run_id="run-troubleshooter-manager-resumed",
        activation_id="activation-returned-manager-2",
    )
    assert _return_decision.accepted is True
    third_source = apply(
        returned,
        decide(
            returned,
            ClaimWork(
                "claim-returned-manager-2",
                activation_id="activation-returned-manager-2",
            ),
            deterministic_context(
                transition_id="transition-claim-returned-manager-2",
                run_id="run-source-retry-3",
                claim_id="claim-source-retry-3",
                fencing_token="fence-source-retry-3",
            ),
        ),
    )
    return apply(
        third_source,
        decide(
            third_source,
            _recovery_observation(
                state=third_source,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-source-retry-3",
                action_id=MANAGER_BLOCKED_ACTION_ID,
                input_id="observe-manager-blocked-3",
            ),
            deterministic_context(
                transition_id="transition-observe-manager-blocked-3",
                activation_id="activation-should-not-exist-at-threshold",
            ),
        ),
    )


def test_manager_blocked_schedules_recovery_through_compiled_action() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    source_work_item = state.work_items["work-prompt"]

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id=str(MANAGER_BLOCKED_ACTION_ID),
            input_id="observe-manager-blocked",
            artifact_payload={},
        ),
        simple_loop_context("observe-manager-blocked"),
    )

    assert decision.accepted is True
    assert {
        "mutation.record_runner_observation",
        "mutation.create_activation",
        "mutation.route_activation",
    } <= set(mutation_kinds(decision))
    _assert_no_forbidden_recovery_mutations(decision)
    after = apply(state, decision)

    recovery_activation = after.activations["activation-troubleshooter-manager"]
    _assert_recovery_activation(
        activation=recovery_activation,
        source_work_item_id="work-prompt",
        lineage_id=source_work_item.lineage_id,
        fingerprint=fingerprint,
        queue_family_id=QueueFamilyId("work_prompt"),
    )
    assert after.work_items["work-prompt"] == source_work_item
    _assert_recovery_route(
        after=after,
        action_id=MANAGER_BLOCKED_ACTION_ID,
        source_run_id="run-manager",
        source_work_item_id="work-prompt",
        target_activation_id="activation-troubleshooter-manager",
    )
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-prompt",
        run_id="run-manager",
        action_id=MANAGER_BLOCKED_ACTION_ID,
    )
    assert after.artifacts == state.artifacts
    assert after.closed_work_items == state.closed_work_items
    assert after.pause == state.pause
    assert after.quarantines == state.quarantines


def test_first_recovery_attempt_records_selected_policy_and_source_context() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)

    decision = decide(
        state,
        _recovery_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id=MANAGER_BLOCKED_ACTION_ID,
            input_id="observe-manager-blocked",
        ),
        simple_loop_context("observe-manager-blocked"),
    )

    assert decision.accepted is True
    assert "mutation.record_recovery_attempt" in mutation_kinds(decision)
    after = apply(state, decision)
    attempt = _only_recovery_attempt(after)

    assert str(attempt.policy_id) == RECOVERY_POLICY_ID
    assert attempt.plan_ref.authority_fingerprint == fingerprint
    assert attempt.lineage_id == "work-prompt"
    assert attempt.attempt_count == 1
    assert attempt.phase == "active_recovery"
    assert attempt.source_run_id == "run-manager"
    assert attempt.source_work_item_id == "work-prompt"
    assert attempt.source_activation_id == "activation-manager"
    assert attempt.source_graph_node_id == "simple_loop.manager.start"
    assert str(attempt.source_stage_kind_id) == "simple_loop.manager"
    assert str(attempt.source_runner_binding_id) == (
        "simple_loop.default_agent_runner"
    )
    assert str(attempt.source_queue_family_id) == "work_prompt"
    assert attempt.recovery_action_id == MANAGER_BLOCKED_ACTION_ID
    assert attempt.latest_recovery_activation_id == "activation-troubleshooter-manager"
    assert attempt.latest_recovery_run_id is None
    assert attempt.latest_return_action_id is None
    assert attempt.created_by_input_id == "observe-manager-blocked"
    assert attempt.updated_by_input_id == "observe-manager-blocked"

    status = operator_status(after)
    status_attempt = status.recovery_attempts[0]
    assert status_attempt.policy_id == RECOVERY_POLICY_ID
    assert status_attempt.plan_fingerprint == fingerprint
    assert status_attempt.attempt_count == 1
    assert status_attempt.phase == "active_recovery"
    assert status_attempt.source_run_id == "run-manager"
    assert status_attempt.source_stage_kind_id == "simple_loop.manager"
    assert status_attempt.source_queue_family_id == "work_prompt"


def test_troubleshooter_return_uses_recorded_source_context() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    recovered = apply(
        state,
        decide(
            state,
            _recovery_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-manager",
                action_id=MANAGER_BLOCKED_ACTION_ID,
                input_id="observe-manager-blocked",
            ),
            simple_loop_context("observe-manager-blocked"),
        ),
    )
    claimed = _claim_recovery_activation(
        recovered,
        input_id="claim-troubleshooter-manager",
        activation_id="activation-troubleshooter-manager",
        run_id="run-troubleshooter-manager",
        claim_id="claim-troubleshooter-manager",
        fencing_token="fence-troubleshooter-manager",
    )
    attempt_after_claim = _only_recovery_attempt(claimed)
    assert attempt_after_claim.latest_recovery_run_id == "run-troubleshooter-manager"

    decision, returned = _return_from_troubleshooter(
        claimed,
        plan=plan,
        fingerprint=fingerprint,
        input_id="observe-troubleshooter-resolved",
        run_id="run-troubleshooter-manager",
        activation_id="activation-returned-manager",
    )

    assert decision.accepted is True
    assert "mutation.record_artifact" in mutation_kinds(decision)
    artifact = returned.artifacts["transition-observe-troubleshooter-resolved:artifact"]
    assert artifact.schema_id == ArtifactSchemaId("simple_loop.troubleshooting_report")
    assert artifact.work_item_id == "work-prompt"
    assert artifact.payload == troubleshooting_report_payload()
    returned_activation = returned.activations["activation-returned-manager"]
    assert returned_activation.work_item_id == "work-prompt"
    assert returned_activation.lineage_id == "work-prompt"
    assert returned_activation.plan_ref.authority_fingerprint == fingerprint
    assert returned_activation.queue_family_id == QueueFamilyId("work_prompt")
    assert returned_activation.graph_node_id == "simple_loop.manager.start"
    assert str(returned_activation.stage_kind_id) == "simple_loop.manager"
    assert str(returned_activation.runner_binding_id) == (
        "simple_loop.default_agent_runner"
    )
    _assert_recovery_route(
        after=returned,
        action_id=TROUBLESHOOTER_RESOLVED_ACTION_ID,
        source_run_id="run-troubleshooter-manager",
        source_work_item_id="work-prompt",
        target_activation_id="activation-returned-manager",
    )
    attempt = _only_recovery_attempt(returned)
    assert attempt.latest_return_action_id == TROUBLESHOOTER_RESOLVED_ACTION_ID
    assert attempt.phase == "active_recovery"


def test_troubleshooter_return_refuses_invalid_report_without_progress() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    recovered = apply(
        state,
        decide(
            state,
            _recovery_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-manager",
                action_id=MANAGER_BLOCKED_ACTION_ID,
                input_id="observe-manager-blocked",
            ),
            simple_loop_context("observe-manager-blocked"),
        ),
    )
    claimed = _claim_recovery_activation(
        recovered,
        input_id="claim-troubleshooter-manager",
        activation_id="activation-troubleshooter-manager",
        run_id="run-troubleshooter-manager",
        claim_id="claim-troubleshooter-manager",
        fencing_token="fence-troubleshooter-manager",
    )

    decision = decide(
        claimed,
        runner_observation(
            state=claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-troubleshooter-manager",
            action_id=str(TROUBLESHOOTER_RESOLVED_ACTION_ID),
            input_id="observe-troubleshooter-invalid-report",
            artifact_payload={"artifact_kind": "WRONG"},
        ),
        deterministic_context(
            transition_id="transition-observe-troubleshooter-invalid-report",
            activation_id="activation-returned-invalid",
        ),
    )
    after = apply(claimed, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    _assert_no_workflow_progress(decision)
    assert after.runner_observations == claimed.runner_observations
    assert after.artifacts == claimed.artifacts
    assert after.work_items == claimed.work_items
    assert after.activations == claimed.activations
    assert after.activation_routes == claimed.activation_routes
    assert after.closed_work_items == claimed.closed_work_items
    assert after.pause == claimed.pause
    assert after.quarantines == claimed.quarantines
    assert after.recovery_attempts == claimed.recovery_attempts
    assert "activation-returned-invalid" not in after.activations
    assert "transition-observe-troubleshooter-invalid-report:artifact" not in (
        after.artifacts
    )


def test_troubleshooter_return_refuses_stale_recovery_run() -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(plan, fingerprint, observed_at=1000)
    resumed = _resume_only_cooldown_wait(waiting)
    latest_claimed = _claim_recovery_activation(
        resumed,
        input_id="claim-troubleshooter-manager-resumed",
        activation_id="activation-troubleshooter-manager-resumed",
        run_id="run-troubleshooter-manager-resumed",
        claim_id="claim-troubleshooter-manager-resumed",
        fencing_token="fence-troubleshooter-manager-resumed",
    )
    latest_activation = latest_claimed.activations[
        "activation-troubleshooter-manager-resumed"
    ]
    stale_activation = replace(
        latest_activation,
        activation_id="activation-stale-recovery",
        claimed_by_run_id="run-stale-recovery",
        created_by_input_id="seed-stale-recovery",
    )
    stale_run = RunRecord(
        run_ref=RunRef(
            run_id="run-stale-recovery",
            work_item_id=stale_activation.work_item_id,
            claim_id="claim-stale-recovery",
            plan_ref=stale_activation.plan_ref,
            generation=0,
            fencing_token="fence-stale-recovery",
        ),
        work_item_id=stale_activation.work_item_id,
        activation_id=stale_activation.activation_id,
        stage_kind_id=stale_activation.stage_kind_id,
        runner_binding_id=stale_activation.runner_binding_id,
        created_by_input_id="seed-stale-recovery",
    )
    stale_state = replace(
        latest_claimed,
        activations={
            **latest_claimed.activations,
            stale_activation.activation_id: stale_activation,
        },
        runs={**latest_claimed.runs, stale_run.run_ref.run_id: stale_run},
    )

    stale_decision, after = _return_from_troubleshooter(
        stale_state,
        plan=plan,
        fingerprint=fingerprint,
        input_id="observe-stale-troubleshooter-resolved",
        run_id="run-stale-recovery",
        activation_id="activation-returned-stale",
    )

    assert stale_decision.accepted is False
    assert stale_decision.refusal is not None
    assert stale_decision.refusal.reason == "unsupported_runtime_terminal_action"
    assert after == stale_state
    assert "activation-returned-stale" not in after.activations


def test_troubleshooter_return_refuses_unrelated_lineage_attempt() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    recovered = apply(
        state,
        decide(
            state,
            _recovery_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-manager",
                action_id=MANAGER_BLOCKED_ACTION_ID,
                input_id="observe-manager-blocked",
            ),
            simple_loop_context("observe-manager-blocked"),
        ),
    )
    claimed = _claim_recovery_activation(
        recovered,
        input_id="claim-troubleshooter-manager",
        activation_id="activation-troubleshooter-manager",
        run_id="run-troubleshooter-manager",
        claim_id="claim-troubleshooter-manager",
        fencing_token="fence-troubleshooter-manager",
    )
    unrelated_attempt = replace(
        _only_recovery_attempt(claimed),
        record_id=(
            f"recovery-attempt:{fingerprint}:{RECOVERY_POLICY_ID}:other-lineage"
        ),
        lineage_id="other-lineage",
    )
    unrelated_state = replace(
        claimed,
        recovery_attempts={unrelated_attempt.record_id: unrelated_attempt},
    )

    decision, after = _return_from_troubleshooter(
        unrelated_state,
        plan=plan,
        fingerprint=fingerprint,
        input_id="observe-unrelated-troubleshooter-resolved",
        run_id="run-troubleshooter-manager",
        activation_id="activation-returned-unrelated",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_runtime_terminal_action"
    assert after == unrelated_state
    assert "activation-returned-unrelated" not in after.activations


def test_troubleshooter_return_refuses_different_policy_attempt() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    recovered = apply(
        state,
        decide(
            state,
            _recovery_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-manager",
                action_id=MANAGER_BLOCKED_ACTION_ID,
                input_id="observe-manager-blocked",
            ),
            simple_loop_context("observe-manager-blocked"),
        ),
    )
    claimed = _claim_recovery_activation(
        recovered,
        input_id="claim-troubleshooter-manager",
        activation_id="activation-troubleshooter-manager",
        run_id="run-troubleshooter-manager",
        claim_id="claim-troubleshooter-manager",
        fencing_token="fence-troubleshooter-manager",
    )
    policy_id = RecoveryPolicyId("other.policy")
    other_policy_attempt = replace(
        _only_recovery_attempt(claimed),
        record_id=f"recovery-attempt:{fingerprint}:{policy_id}:work-prompt",
        policy_id=policy_id,
    )
    other_policy_state = replace(
        claimed,
        recovery_attempts={other_policy_attempt.record_id: other_policy_attempt},
    )

    decision, after = _return_from_troubleshooter(
        other_policy_state,
        plan=plan,
        fingerprint=fingerprint,
        input_id="observe-other-policy-troubleshooter-resolved",
        run_id="run-troubleshooter-manager",
        activation_id="activation-returned-other-policy",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_runtime_terminal_action"
    assert after == other_policy_state
    assert "activation-returned-other-policy" not in after.activations


def test_selected_reset_trigger_marks_recovery_attempt_resolved() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    recovered = apply(
        state,
        decide(
            state,
            _recovery_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-manager",
                action_id=MANAGER_BLOCKED_ACTION_ID,
                input_id="observe-manager-blocked",
            ),
            simple_loop_context("observe-manager-blocked"),
        ),
    )
    claimed = _claim_recovery_activation(
        recovered,
        input_id="claim-troubleshooter-manager",
        activation_id="activation-troubleshooter-manager",
        run_id="run-troubleshooter-manager",
        claim_id="claim-troubleshooter-manager",
        fencing_token="fence-troubleshooter-manager",
    )
    _return_decision, returned = _return_from_troubleshooter(
        claimed,
        plan=plan,
        fingerprint=fingerprint,
        input_id="observe-troubleshooter-resolved",
        run_id="run-troubleshooter-manager",
        activation_id="activation-returned-manager",
    )
    retry_claimed = apply(
        returned,
        decide(
            returned,
            ClaimWork(
                "claim-returned-manager",
                activation_id="activation-returned-manager",
            ),
            deterministic_context(
                transition_id="transition-claim-returned-manager",
                run_id="run-manager-retry",
                claim_id="claim-returned-manager",
                fencing_token="fence-manager-retry",
            ),
        ),
    )

    decision = decide(
        retry_claimed,
        runner_observation(
            state=retry_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager-retry",
            action_id=str(MANAGER_PACKET_READY_ACTION_ID),
            input_id="observe-manager-packet-ready-after-recovery",
            artifact_payload=work_packet_payload(),
        ),
        simple_loop_context("observe-manager-packet-ready-after-recovery"),
    )
    resolved = apply(retry_claimed, decision)

    assert decision.accepted is True
    assert "mutation.record_recovery_attempt" in mutation_kinds(decision)
    assert _only_recovery_attempt(resolved).phase == "resolved"


def test_second_repeated_recovery_records_pending_cooldown_without_scheduling() -> None:
    plan, fingerprint = compile_simple_loop()
    retry_claimed = bootstrap_to_manager_retry_claim_after_first_recovery(
        plan,
        fingerprint,
    )

    decision = decide(
        retry_claimed,
        _recovery_observation(
            state=retry_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager-retry",
            action_id=MANAGER_BLOCKED_ACTION_ID,
            input_id="observe-manager-blocked-2",
            observed_at=1000,
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-2",
            activation_id="activation-should-not-exist",
        ),
    )
    after = apply(retry_claimed, decision)
    attempt = _only_recovery_attempt(after)

    assert decision.accepted is True
    assert attempt.attempt_count == 2
    assert attempt.phase == "pending_cooldown"
    assert "mutation.record_recovery_attempt" in mutation_kinds(decision)
    assert "mutation.record_cooldown_wait" in mutation_kinds(decision)
    assert "mutation.create_activation" not in mutation_kinds(decision)
    assert "activation-should-not-exist" not in after.activations
    assert after.pause is None
    assert after.quarantines == {}
    wait = _only_cooldown_wait(after)
    policy = plan.recovery_policies[0]
    assert wait.wait_id.startswith(f"cooldown-wait:{fingerprint}:")
    assert wait.policy_id == policy.id
    assert wait.lineage_id == "work-prompt"
    assert wait.recovery_attempt_record_id == attempt.record_id
    assert wait.attempt_count == 2
    assert wait.source_run_id == "run-manager-retry"
    assert wait.source_work_item_id == "work-prompt"
    assert wait.source_activation_id == "activation-returned-manager"
    assert wait.recovery_action_id == MANAGER_BLOCKED_ACTION_ID
    assert str(wait.target_stage_kind_id) == "simple_loop.troubleshooter"
    assert wait.target_graph_node_id == "simple_loop.troubleshooter.start"
    assert str(wait.target_runner_binding_id) == (
        "simple_loop.default_agent_runner"
    )
    assert wait.plan_ref.authority_fingerprint == fingerprint
    assert wait.created_input_id == "observe-manager-blocked-2"
    assert wait.created_at == 1000
    assert wait.due_at == 1000 + policy.default_cooldown_seconds
    assert wait.consumed_input_id is None
    assert wait.consumed_at is None
    assert wait.resulting_recovery_activation_id is None
    status = operator_status(after)
    assert len(status.cooldown_waits) == 1
    status_wait = status.cooldown_waits[0]
    assert status_wait.wait_id == wait.wait_id
    assert status_wait.policy_id == RECOVERY_POLICY_ID
    assert status_wait.lineage_id == "work-prompt"
    assert status_wait.recovery_action_id == str(MANAGER_BLOCKED_ACTION_ID)
    assert status_wait.source_run_id == "run-manager-retry"
    assert status_wait.source_work_item_id == "work-prompt"
    assert status_wait.plan_fingerprint == fingerprint
    assert status_wait.created_at == 1000
    assert status_wait.due_at == 1000 + policy.default_cooldown_seconds
    assert _queue_counts(after, "work_prompt") == (0, 0, 0, 0)


def test_cooldown_producing_observation_requires_observed_at() -> None:
    plan, fingerprint = compile_simple_loop()
    retry_claimed = bootstrap_to_manager_retry_claim_after_first_recovery(
        plan,
        fingerprint,
    )

    decision = decide(
        retry_claimed,
        _recovery_observation(
            state=retry_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager-retry",
            action_id=MANAGER_BLOCKED_ACTION_ID,
            input_id="observe-manager-blocked-2",
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-2",
            activation_id="activation-should-not-exist",
        ),
    )
    after = apply(retry_claimed, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "missing_observed_at"
    _assert_no_workflow_progress(decision)
    assert after.cooldown_waits == {}
    assert "activation-should-not-exist" not in after.activations
    assert after.pause is None
    assert after.quarantines == {}
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-prompt",
        run_id="run-manager-retry",
        action_id=MANAGER_BLOCKED_ACTION_ID,
        refusal_reason="missing_observed_at",
    )


def test_cooldown_producing_observation_refuses_due_time_overflow() -> None:
    plan, fingerprint = compile_simple_loop()
    policy = plan.recovery_policies[0]
    retry_claimed = bootstrap_to_manager_retry_claim_after_first_recovery(
        plan,
        fingerprint,
    )

    decision = decide(
        retry_claimed,
        _recovery_observation(
            state=retry_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager-retry",
            action_id=MANAGER_BLOCKED_ACTION_ID,
            input_id="observe-manager-blocked-overflow",
            observed_at=(
                transition_contracts.DURABLE_INT64_MAX
                - policy.default_cooldown_seconds
                + 1
            ),
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-overflow",
            activation_id="activation-should-not-exist",
        ),
    )
    after = apply(retry_claimed, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "observed_at_out_of_range"
    _assert_no_workflow_progress(decision)
    assert after.cooldown_waits == {}
    assert "activation-should-not-exist" not in after.activations


def test_cooldown_due_time_uses_selected_policy_value() -> None:
    plan, _fingerprint = compile_simple_loop()
    policy = replace(plan.recovery_policies[0], default_cooldown_seconds=37)
    wait = replace(plan.wait_states[0], duration_seconds=37)
    plan = replace(plan, recovery_policies=(policy,), wait_states=(wait,))
    fingerprint = authority_fingerprint(plan)

    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=500,
    )

    wait = _only_cooldown_wait(waiting)
    assert wait.created_at == 500
    assert wait.due_at == 537


def test_not_yet_due_timer_refuses_without_resuming_cooldown_wait() -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=1000,
    )
    wait = _only_cooldown_wait(waiting)

    decision = decide(
        waiting,
        _timer_due("timer-cooldown-early", wait_id=wait.wait_id, observed_at=1899),
        deterministic_context(
            transition_id="transition-timer-cooldown-early",
            activation_id="activation-not-due",
        ),
    )
    after = apply(waiting, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "wait_not_due"
    _assert_no_workflow_progress(decision)
    assert after.cooldown_waits[wait.wait_id] == wait
    assert "activation-not-due" not in after.activations
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-prompt",
        run_id="run-manager-retry",
        action_id=MANAGER_BLOCKED_ACTION_ID,
        refusal_reason="wait_not_due",
    )


def test_due_timer_resumes_declared_recovery_target_and_is_exactly_idempotent(
) -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=1000,
    )
    wait = _only_cooldown_wait(waiting)

    timer = _timer_due("timer-cooldown-due", wait_id=wait.wait_id, observed_at=1900)
    decision = decide(
        waiting,
        timer,
        deterministic_context(
            transition_id="transition-timer-cooldown-due",
            activation_id="activation-troubleshooter-manager-resumed",
        ),
    )
    after = apply(waiting, decision)
    consumed_wait = after.cooldown_waits[wait.wait_id]
    attempt = _only_recovery_attempt(after)

    assert decision.accepted is True
    assert "mutation.record_cooldown_wait" in mutation_kinds(decision)
    assert "mutation.create_activation" in mutation_kinds(decision)
    assert consumed_wait.consumed_input_id == "timer-cooldown-due"
    assert consumed_wait.consumed_at == 1900
    assert consumed_wait.resulting_recovery_activation_id == (
        "activation-troubleshooter-manager-resumed"
    )
    assert attempt.phase == "active_recovery"
    assert attempt.attempt_count == 2
    assert attempt.latest_recovery_activation_id == (
        "activation-troubleshooter-manager-resumed"
    )
    assert attempt.latest_recovery_run_id is None
    resumed_activation = after.activations["activation-troubleshooter-manager-resumed"]
    _assert_recovery_activation(
        activation=resumed_activation,
        source_work_item_id="work-prompt",
        lineage_id="work-prompt",
        fingerprint=fingerprint,
        queue_family_id=QueueFamilyId("work_prompt"),
    )
    assert resumed_activation.stage_kind_id == wait.target_stage_kind_id
    assert resumed_activation.graph_node_id == wait.target_graph_node_id
    assert resumed_activation.runner_binding_id == wait.target_runner_binding_id
    _assert_recovery_route(
        after=after,
        action_id=MANAGER_BLOCKED_ACTION_ID,
        source_run_id="run-manager-retry",
        source_work_item_id="work-prompt",
        target_activation_id="activation-troubleshooter-manager-resumed",
    )

    replay = decide(
        after,
        timer,
        deterministic_context(
            transition_id="transition-timer-cooldown-due-replay",
            activation_id="activation-duplicate-replay",
        ),
    )
    replayed = apply(after, replay)
    assert replay.accepted is True
    assert replay.disposition == "replayed"
    assert replay.mutations == ()
    assert replayed == after

    conflict = decide(
        after,
        _timer_due("timer-cooldown-due", wait_id=wait.wait_id, observed_at=1901),
        deterministic_context(transition_id="transition-timer-cooldown-conflict"),
    )
    assert conflict.accepted is False
    assert conflict.refusal is not None
    assert conflict.refusal.reason == "idempotency_conflict"

    consumed_again = decide(
        after,
        _timer_due("timer-cooldown-again", wait_id=wait.wait_id, observed_at=1901),
        deterministic_context(transition_id="transition-timer-cooldown-again"),
    )
    assert consumed_again.accepted is False
    assert consumed_again.refusal is not None
    assert consumed_again.refusal.reason == "wait_already_consumed"

    claimed = _claim_recovery_activation(
        after,
        input_id="claim-troubleshooter-manager-resumed",
        activation_id="activation-troubleshooter-manager-resumed",
        run_id="run-troubleshooter-manager-resumed",
        claim_id="claim-troubleshooter-manager-resumed",
        fencing_token="fence-troubleshooter-manager-resumed",
    )
    assert _only_recovery_attempt(claimed).latest_recovery_run_id == (
        "run-troubleshooter-manager-resumed"
    )
    quarantine_decision = decide(
        claimed,
        runner_observation(
            state=claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-troubleshooter-manager-resumed",
            action_id=str(TROUBLESHOOTER_OPERATOR_NEEDED_ACTION_ID),
            input_id="observe-troubleshooter-operator-needed-after-cooldown",
            artifact_payload=troubleshooting_report_payload(
                result="operator needed",
                next_route="operator_intervention",
            ),
        ),
        deterministic_context(
            transition_id=(
                "transition-observe-troubleshooter-operator-needed-after-cooldown"
            ),
        ),
    )
    quarantined = apply(claimed, quarantine_decision)
    attempt = _only_recovery_attempt(quarantined)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")

    assert quarantine_decision.accepted is True
    assert quarantine_decision.refusal is None
    assert "mutation.record_lineage_quarantine" in mutation_kinds(quarantine_decision)
    assert attempt.phase == "quarantine_eligible"
    assert quarantine.policy_id == attempt.policy_id
    assert quarantine.recovery_attempt_record_id == attempt.record_id
    assert quarantine.original_source_run_id == "run-manager-retry"
    assert quarantine.original_source_work_item_id == "work-prompt"
    assert quarantine.original_source_activation_id == "activation-returned-manager"
    assert quarantine.emitting_recovery_activation_id == (
        "activation-troubleshooter-manager-resumed"
    )
    assert quarantine.emitting_recovery_run_id == "run-troubleshooter-manager-resumed"
    assert quarantine.action_id == TROUBLESHOOTER_OPERATOR_NEEDED_ACTION_ID
    assert quarantine.attempt_count == 2
    assert quarantine.created_input_id == (
        "observe-troubleshooter-operator-needed-after-cooldown"
    )
    assert quarantine.actor_kind == "runtime"
    assert quarantine.status == "active"


def test_third_recovery_attempt_records_runtime_lineage_quarantine() -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(plan, fingerprint, observed_at=1000)
    resumed = _resume_only_cooldown_wait(waiting)
    resumed_claimed = _claim_recovery_activation(
        resumed,
        input_id="claim-troubleshooter-manager-resumed",
        activation_id="activation-troubleshooter-manager-resumed",
        run_id="run-troubleshooter-manager-resumed",
        claim_id="claim-troubleshooter-manager-resumed",
        fencing_token="fence-troubleshooter-manager-resumed",
    )
    _return_decision, returned = _return_from_troubleshooter(
        resumed_claimed,
        plan=plan,
        fingerprint=fingerprint,
        input_id="observe-troubleshooter-resolved-after-cooldown",
        run_id="run-troubleshooter-manager-resumed",
        activation_id="activation-returned-manager-2",
    )
    assert _return_decision.accepted is True
    third_source = apply(
        returned,
        decide(
            returned,
            ClaimWork(
                "claim-returned-manager-2",
                activation_id="activation-returned-manager-2",
            ),
            deterministic_context(
                transition_id="transition-claim-returned-manager-2",
                run_id="run-source-retry-3",
                claim_id="claim-source-retry-3",
                fencing_token="fence-source-retry-3",
            ),
        ),
    )

    third_decision = decide(
        third_source,
        _recovery_observation(
            state=third_source,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-source-retry-3",
            action_id=MANAGER_BLOCKED_ACTION_ID,
            input_id="observe-manager-blocked-3",
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-3",
            activation_id="activation-troubleshooter-manager-3",
        ),
    )
    third = apply(third_source, third_decision)
    attempt3 = _only_recovery_attempt(third)

    assert third_decision.accepted is True
    assert attempt3.attempt_count == 3
    assert attempt3.phase == "quarantine_eligible"
    assert attempt3.latest_recovery_activation_id is None
    assert attempt3.latest_recovery_run_id is None
    assert "mutation.create_activation" not in mutation_kinds(third_decision)
    assert "mutation.route_activation" not in mutation_kinds(third_decision)
    assert "mutation.record_lineage_quarantine" in mutation_kinds(third_decision)
    assert third.quarantines == {}
    assert "activation-troubleshooter-manager-3" not in third.activations
    assert "activation-should-not-exist-at-threshold" not in third.activations
    assert third.pause is None
    lineage_quarantines = getattr(third, "lineage_quarantines")
    assert len(lineage_quarantines) == 1
    quarantine = _active_lineage_quarantine(third, "work-prompt")
    assert quarantine.quarantine_id.startswith(f"lineage-quarantine:{fingerprint}:")
    assert quarantine.policy_id == attempt3.policy_id
    assert quarantine.lineage_id == "work-prompt"
    assert quarantine.selected_plan_ref == attempt3.plan_ref
    assert quarantine.selected_plan_fingerprint == fingerprint
    assert quarantine.recovery_attempt_record_id == attempt3.record_id
    assert quarantine.original_source_run_id == "run-source-retry-3"
    assert quarantine.original_source_work_item_id == "work-prompt"
    assert quarantine.original_source_activation_id == "activation-returned-manager-2"
    assert quarantine.emitting_recovery_activation_id == (
        "activation-returned-manager-2"
    )
    assert quarantine.emitting_recovery_run_id == "run-source-retry-3"
    assert quarantine.action_id == TROUBLESHOOTER_OPERATOR_NEEDED_ACTION_ID
    assert quarantine.attempt_count == 3
    assert quarantine.created_input_id == "observe-manager-blocked-3"
    assert quarantine.actor_kind == "runtime"
    assert quarantine.status == "active"
    assert quarantine.superseded_input_id is None

    status = operator_status(third)
    assert status.active_runs == ()
    assert _queue_counts(third, "work_prompt") == (0, 0, 0, 1)

    unrelated_ready = apply(
        third,
        decide(
            third,
            EnqueueWork(
                "enqueue-unrelated-after-lineage-quarantine",
                queue_family_id=QueueFamilyId("work_prompt"),
                payload=work_prompt_payload(),
            ),
            deterministic_context(
                transition_id="transition-enqueue-unrelated-after-lineage-quarantine",
                work_item_id="work-unrelated",
                activation_id="activation-unrelated-manager",
            ),
        ),
    )
    unrelated_claim = decide(
        unrelated_ready,
        ClaimWork(
            "claim-unrelated-after-lineage-quarantine",
            activation_id="activation-unrelated-manager",
        ),
        deterministic_context(
            transition_id="transition-claim-unrelated-after-lineage-quarantine",
            run_id="run-unrelated-manager",
            claim_id="claim-unrelated-manager",
            fencing_token="fence-unrelated-manager",
        ),
    )
    assert unrelated_claim.accepted is True


def test_operator_resume_lineage_uses_declared_option_and_recorded_source() -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan=plan, fingerprint=fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    attempt = _only_recovery_attempt(quarantined)
    ResumeInput = getattr(operator_api, "OperatorResumeLineageInput")

    transition_input = operator_api.build_resume_lineage(
        quarantined,
        ResumeInput(
            input_id="operator-resume-lineage",
            option_id="simple_loop.resume_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator resumed simple loop lineage",
            payload=None,
        ),
    )

    assert transition_input.input_kind == "workflow.operator_resume_lineage"
    assert transition_input.option_id == "simple_loop.resume_lineage"
    assert transition_input.actor_kind == "local_operator"
    decision = decide(
        quarantined,
        transition_input,
        deterministic_context(
            transition_id="transition-operator-resume-lineage",
            activation_id="activation-operator-resumed-manager",
        ),
    )

    assert decision.accepted is True
    assert "mutation.create_work_item" not in mutation_kinds(decision)
    assert "mutation.create_run" not in mutation_kinds(decision)
    assert mutation_kinds(decision).count("mutation.create_activation") == 1
    assert {
        "mutation.supersede_lineage_quarantine",
        "mutation.record_recovery_attempt",
        "mutation.record_operator_intervention",
    } <= set(mutation_kinds(decision))
    after = apply(quarantined, decision)

    resumed_quarantine = after.lineage_quarantines[quarantine.quarantine_id]
    assert resumed_quarantine.status == "superseded"
    assert resumed_quarantine.superseded_input_id == "operator-resume-lineage"
    resumed_attempt = _only_recovery_attempt(after)
    assert resumed_attempt.phase == "resolved"
    assert resumed_attempt.updated_by_input_id == "operator-resume-lineage"
    activation = after.activations["activation-operator-resumed-manager"]
    assert activation.work_item_id == attempt.source_work_item_id
    assert activation.lineage_id == attempt.lineage_id
    assert activation.graph_node_id == attempt.source_graph_node_id
    assert activation.stage_kind_id == attempt.source_stage_kind_id
    assert activation.runner_binding_id == attempt.source_runner_binding_id
    assert activation.queue_family_id == attempt.source_queue_family_id
    assert activation.claimed_by_run_id is None
    assert after.work_items == quarantined.work_items

    interventions = getattr(after, "operator_interventions")
    assert len(interventions) == 1
    record = next(iter(interventions.values()))
    assert record.kind == "resume_lineage"
    assert record.result == "resumed"
    assert record.option_id == "simple_loop.resume_lineage"
    assert record.policy_id == attempt.policy_id
    assert record.lineage_id == "work-prompt"
    assert record.quarantine_id == quarantine.quarantine_id
    assert record.recovery_attempt_record_id == attempt.record_id
    assert record.recovery_attempt_count == 3
    assert record.attempt_effect == "resolve_attempt"
    assert record.selected_plan_ref == quarantine.selected_plan_ref
    assert record.selected_plan_fingerprint == fingerprint
    assert record.actor_kind == "local_operator"
    assert record.actor_id == "local-operator-tim"
    assert record.target_activation_id == "activation-operator-resumed-manager"
    assert record.closed_work_item_ids == ()
    assert record.payload_digest is not None
    assert record.payload_reference is None
    assert _queue_counts(after, "work_prompt") == (1, 0, 0, 0)


def test_operator_close_lineage_uses_declared_option_and_closes_live_work() -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan=plan, fingerprint=fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    attempt = _only_recovery_attempt(quarantined)
    CloseInput = getattr(operator_api, "OperatorCloseLineageInput")

    transition_input = operator_api.build_close_lineage(
        quarantined,
        CloseInput(
            input_id="operator-close-lineage",
            option_id="simple_loop.close_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator closed simple loop lineage",
            payload={},
        ),
    )
    decision = decide(
        quarantined,
        transition_input,
        deterministic_context(transition_id="transition-operator-close-lineage"),
    )

    assert decision.accepted is True
    assert {
        "mutation.supersede_lineage_quarantine",
        "mutation.record_recovery_attempt",
        "mutation.record_operator_intervention",
        "mutation.close_work_item",
    } <= set(mutation_kinds(decision))
    after = apply(quarantined, decision)

    closed_quarantine = after.lineage_quarantines[quarantine.quarantine_id]
    assert closed_quarantine.status == "superseded"
    assert closed_quarantine.superseded_input_id == "operator-close-lineage"
    closed_attempt = _only_recovery_attempt(after)
    assert closed_attempt.phase == "resolved"
    assert closed_attempt.updated_by_input_id == "operator-close-lineage"

    interventions = getattr(after, "operator_interventions")
    assert len(interventions) == 1
    record = next(iter(interventions.values()))
    assert record.kind == "close_lineage"
    assert record.result == "closed"
    assert record.option_id == "simple_loop.close_lineage"
    assert record.policy_id == attempt.policy_id
    assert record.lineage_id == "work-prompt"
    assert record.quarantine_id == quarantine.quarantine_id
    assert record.recovery_attempt_record_id == attempt.record_id
    assert record.attempt_effect == "resolve_attempt"
    assert record.closed_work_item_ids == ("work-prompt",)
    assert record.closed_activation_ids
    assert record.closed_run_ids
    assert record.target_activation_id is None
    closed = after.closed_work_items["work-prompt"]
    assert closed.source_run_id is None
    assert closed.operator_intervention_record_id == record.record_id
    assert closed.close_kind == "operator_intervention"
    assert _queue_counts(after, "work_prompt") == (0, 0, 1, 0)

    refused_claim = decide(
        after,
        ClaimWork(
            "claim-closed-lineage-work",
            activation_id=quarantine.original_source_activation_id,
        ),
        deterministic_context(transition_id="transition-claim-closed-lineage-work"),
    )
    assert refused_claim.accepted is False
    assert refused_claim.refusal is not None
    assert refused_claim.refusal.reason == "work_item_closed"


def test_operator_revise_lineage_validates_payload_and_routes_declared_target(
) -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan=plan, fingerprint=fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    attempt = _only_recovery_attempt(quarantined)
    ReviseInput = getattr(operator_api, "OperatorReviseLineageInput")
    bad_payload = dict(work_packet_payload())
    bad_payload.pop("completion_definition")

    with pytest.raises(operator_api.OperatorInputError) as exc_info:
        operator_api.build_revise_lineage(
            quarantined,
            ReviseInput(
                input_id="operator-revise-lineage-invalid",
                option_id="simple_loop.revise_lineage",
                selected_plan_ref=quarantine.selected_plan_ref,
                quarantine_id=quarantine.quarantine_id,
                lineage_id=None,
                actor_id="local-operator-tim",
                actor_kind="local_operator",
                reason="operator revised simple loop packet",
                payload=bad_payload,
            ),
        )

    assert exc_info.value.reason == "invalid_payload_schema"
    assert getattr(quarantined, "operator_interventions") == {}
    assert "work-operator-revised-packet" not in quarantined.work_items
    direct_invalid = OperatorReviseLineage(
        "operator-revise-lineage-invalid-direct",
        option_id="simple_loop.revise_lineage",
        selected_plan_ref=quarantine.selected_plan_ref,
        quarantine_id=quarantine.quarantine_id,
        lineage_id="work-prompt",
        actor_id="local-operator-tim",
        actor_kind="local_operator",
        reason="operator revised simple loop packet",
        payload={"artifact_kind": "simple_loop.work_packet"},
    )
    direct_invalid_decision = decide(
        quarantined,
        direct_invalid,
        deterministic_context(
            transition_id="transition-operator-revise-lineage-invalid-direct",
            work_item_id="work-invalid-direct-revision",
            activation_id="activation-invalid-direct-revision",
        ),
    )
    direct_invalid_after = apply(quarantined, direct_invalid_decision)
    assert direct_invalid_decision.accepted is False
    assert direct_invalid_decision.refusal is not None
    assert (
        direct_invalid_decision.refusal.reason
        == "invalid_operator_intervention_payload_schema"
    )
    assert "mutation.create_work_item" not in mutation_kinds(direct_invalid_decision)
    assert "mutation.create_activation" not in mutation_kinds(direct_invalid_decision)
    assert "mutation.supersede_lineage_quarantine" not in mutation_kinds(
        direct_invalid_decision
    )
    assert "mutation.record_recovery_attempt" not in mutation_kinds(
        direct_invalid_decision
    )
    assert "mutation.record_operator_intervention" not in mutation_kinds(
        direct_invalid_decision
    )
    assert direct_invalid_after.work_items == quarantined.work_items
    assert direct_invalid_after.activations == quarantined.activations
    assert direct_invalid_after.recovery_attempts == quarantined.recovery_attempts
    assert direct_invalid_after.lineage_quarantines == quarantined.lineage_quarantines
    assert direct_invalid_after.operator_interventions == (
        quarantined.operator_interventions
    )

    transition_input = operator_api.build_revise_lineage(
        quarantined,
        ReviseInput(
            input_id="operator-revise-lineage",
            option_id="simple_loop.revise_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator revised simple loop packet",
            payload=work_packet_payload(),
        ),
    )

    assert transition_input.input_kind == "workflow.operator_revise_lineage"
    assert transition_input.option_id == "simple_loop.revise_lineage"
    decision = decide(
        quarantined,
        transition_input,
        deterministic_context(
            transition_id="transition-operator-revise-lineage",
            work_item_id="work-operator-revised-packet",
            activation_id="activation-operator-revised-worker",
        ),
    )

    assert decision.accepted is True
    assert mutation_kinds(decision).count("mutation.create_work_item") == 1
    assert mutation_kinds(decision).count("mutation.create_activation") == 1
    assert "mutation.create_run" not in mutation_kinds(decision)
    assert {
        "mutation.supersede_lineage_quarantine",
        "mutation.record_recovery_attempt",
        "mutation.record_operator_intervention",
    } <= set(mutation_kinds(decision))
    for record in decision.governance_events:
        assert record.plan_fingerprint == fingerprint
        assert record.work_item_id == "work-operator-revised-packet"
        assert record.run_id == quarantine.emitting_recovery_run_id
        assert record.action_id == TROUBLESHOOTER_OPERATOR_NEEDED_ACTION_ID
        assert record.authority_source == "operator_intervention"
        assert record.refusal_reason is None
    for trace_record in decision.trace_records:
        assert trace_record.plan_fingerprint == fingerprint
        assert trace_record.work_item_id == "work-operator-revised-packet"
        assert trace_record.run_id == quarantine.emitting_recovery_run_id
        assert trace_record.action_id == TROUBLESHOOTER_OPERATOR_NEEDED_ACTION_ID
        assert trace_record.authority_source == "operator_intervention"
        assert trace_record.refusal_reason is None

    after = apply(quarantined, decision)

    revised_quarantine = after.lineage_quarantines[quarantine.quarantine_id]
    assert revised_quarantine.status == "superseded"
    assert revised_quarantine.superseded_input_id == "operator-revise-lineage"
    revised_attempt = _only_recovery_attempt(after)
    assert revised_attempt.phase == "resolved"
    assert revised_attempt.updated_by_input_id == "operator-revise-lineage"

    work_item = after.work_items["work-operator-revised-packet"]
    assert work_item.payload == work_packet_payload()
    assert work_item.lineage_id == "work-prompt"
    assert work_item.queue_family_id == QueueFamilyId("work_packet")
    assert work_item.ref.plan_ref == quarantine.selected_plan_ref
    assert work_item.created_by_input_id == "operator-revise-lineage"
    activation = after.activations["activation-operator-revised-worker"]
    assert activation.work_item_id == "work-operator-revised-packet"
    assert activation.lineage_id == "work-prompt"
    assert activation.queue_family_id == QueueFamilyId("work_packet")
    assert str(activation.stage_kind_id) == "simple_loop.worker"
    assert activation.graph_node_id == "simple_loop.worker.start"
    assert str(activation.runner_binding_id) == "simple_loop.default_agent_runner"
    assert activation.claimed_by_run_id is None

    interventions = getattr(after, "operator_interventions")
    assert len(interventions) == 1
    intervention = next(iter(interventions.values()))
    assert intervention.kind == "revise_lineage"
    assert intervention.result == "revised"
    assert intervention.option_id == "simple_loop.revise_lineage"
    assert intervention.policy_id == attempt.policy_id
    assert intervention.lineage_id == "work-prompt"
    assert intervention.quarantine_id == quarantine.quarantine_id
    assert intervention.recovery_attempt_record_id == attempt.record_id
    assert intervention.recovery_attempt_count == 3
    assert intervention.attempt_effect == "resolve_attempt"
    assert intervention.selected_plan_ref == quarantine.selected_plan_ref
    assert intervention.selected_plan_fingerprint == fingerprint
    assert intervention.actor_kind == "local_operator"
    assert intervention.actor_id == "local-operator-tim"
    assert intervention.target_work_item_id == "work-operator-revised-packet"
    assert intervention.target_activation_id == "activation-operator-revised-worker"
    assert intervention.closed_work_item_ids == ()
    assert intervention.closed_activation_ids == ()
    assert intervention.closed_run_ids == ()
    assert intervention.payload_digest is not None
    assert intervention.payload_digest.startswith("sha256:")
    assert intervention.payload_reference == (
        "work_item:work-operator-revised-packet:payload"
    )
    assert _queue_counts(after, "work_packet") == (1, 0, 0, 0)
    assert _queue_counts(after, "work_prompt") == (0, 0, 0, 0)

    claim_decision = decide(
        after,
        ClaimWork(
            "claim-operator-revised-worker",
            activation_id="activation-operator-revised-worker",
        ),
        deterministic_context(
            transition_id="transition-claim-operator-revised-worker",
            run_id="run-operator-revised-worker",
            claim_id="claim-operator-revised-worker",
            fencing_token="fence-operator-revised-worker",
        ),
    )
    assert claim_decision.accepted is True

    replay = decide(
        after,
        transition_input,
        deterministic_context(
            transition_id="transition-operator-revise-lineage-replay",
            work_item_id="work-operator-duplicate-revision",
            activation_id="activation-operator-duplicate-revision",
        ),
    )
    replayed = apply(after, replay)
    assert replay.accepted is True
    assert replay.disposition == "replayed"
    assert replay.mutations == ()
    assert replayed == after

    duplicate = operator_api.build_revise_lineage(
        after,
        ReviseInput(
            input_id="operator-revise-lineage-again",
            option_id="simple_loop.revise_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator revised simple loop packet again",
            payload=work_packet_payload(),
        ),
    )
    duplicate_decision = decide(
        after,
        duplicate,
        deterministic_context(
            transition_id="transition-operator-revise-lineage-again",
            work_item_id="work-operator-revised-packet-again",
            activation_id="activation-operator-revised-worker-again",
        ),
    )
    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "lineage_quarantine_not_active"


def test_operator_resume_superseded_quarantine_allows_new_recovery_episode() -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan=plan, fingerprint=fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    old_attempt = _only_recovery_attempt(quarantined)
    ResumeInput = getattr(operator_api, "OperatorResumeLineageInput")
    resumed = apply(
        quarantined,
        decide(
            quarantined,
            operator_api.build_resume_lineage(
                quarantined,
                ResumeInput(
                    input_id="operator-resume-lineage",
                    option_id="simple_loop.resume_lineage",
                    selected_plan_ref=quarantine.selected_plan_ref,
                    quarantine_id=quarantine.quarantine_id,
                    lineage_id=None,
                    actor_id="local-operator-tim",
                    actor_kind="local_operator",
                    reason="operator resumed simple loop lineage",
                    payload=None,
                ),
            ),
            deterministic_context(
                transition_id="transition-operator-resume-lineage",
                activation_id="activation-operator-resumed-manager",
            ),
        ),
    )
    claimed = apply(
        resumed,
        decide(
            resumed,
            ClaimWork(
                "claim-operator-resumed-manager",
                activation_id="activation-operator-resumed-manager",
            ),
            deterministic_context(
                transition_id="transition-claim-operator-resumed-manager",
                run_id="run-source-after-operator-resume",
                claim_id="claim-source-after-operator-resume",
                fencing_token="fence-source-after-operator-resume",
            ),
        ),
    )

    decision = decide(
        claimed,
        _recovery_observation(
            state=claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-source-after-operator-resume",
            action_id=MANAGER_BLOCKED_ACTION_ID,
            input_id="observe-manager-blocked-after-operator-resume",
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-after-operator-resume",
            activation_id="activation-troubleshooter-after-operator-resume",
        ),
    )

    assert decision.accepted is True
    after = apply(claimed, decision)
    assert after.lineage_quarantines[quarantine.quarantine_id].status == "superseded"
    attempts = sorted(
        after.recovery_attempts.values(),
        key=lambda attempt: attempt.created_by_input_id,
    )
    assert len(attempts) == 2
    assert {attempt.phase for attempt in attempts} == {"active_recovery", "resolved"}
    new_attempt = next(attempt for attempt in attempts if attempt.phase != "resolved")
    assert new_attempt.record_id != old_attempt.record_id
    assert new_attempt.attempt_count == 1
    assert new_attempt.created_by_input_id == (
        "observe-manager-blocked-after-operator-resume"
    )


def test_operator_close_fences_stale_accepted_claim_decision() -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan=plan, fingerprint=fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    work_item = quarantined.work_items["work-prompt"]
    source_activation = quarantined.activations[
        quarantine.original_source_activation_id
    ]
    ready_activation = Activation(
        activation_id="activation-ready-before-operator-close",
        work_item_id=work_item.ref.work_item_id,
        lineage_id=work_item.lineage_id,
        plan_ref=work_item.ref.plan_ref,
        queue_family_id=work_item.queue_family_id,
        graph_node_id=source_activation.graph_node_id,
        stage_kind_id=source_activation.stage_kind_id,
        runner_binding_id=source_activation.runner_binding_id,
        generation=work_item.ref.generation,
        created_by_input_id="test-ready-before-operator-close",
    )
    state_with_ready = replace(
        quarantined,
        activations={
            **quarantined.activations,
            ready_activation.activation_id: ready_activation,
        },
    )
    pre_quarantine_claimable = replace(state_with_ready, lineage_quarantines={})
    stale_claim = decide(
        pre_quarantine_claimable,
        ClaimWork(
            "claim-ready-before-operator-close",
            activation_id=ready_activation.activation_id,
        ),
        deterministic_context(
            transition_id="transition-claim-ready-before-operator-close",
            run_id="run-ready-before-operator-close",
            claim_id="claim-ready-before-operator-close",
            fencing_token="fence-ready-before-operator-close",
        ),
    )
    assert stale_claim.accepted is True
    CloseInput = getattr(operator_api, "OperatorCloseLineageInput")
    closed = apply(
        state_with_ready,
        decide(
            state_with_ready,
            operator_api.build_close_lineage(
                state_with_ready,
                CloseInput(
                    input_id="operator-close-lineage",
                    option_id="simple_loop.close_lineage",
                    selected_plan_ref=quarantine.selected_plan_ref,
                    quarantine_id=quarantine.quarantine_id,
                    lineage_id=None,
                    actor_id="local-operator-tim",
                    actor_kind="local_operator",
                    reason="operator closed simple loop lineage",
                    payload={},
                ),
            ),
            deterministic_context(transition_id="transition-operator-close-lineage"),
        ),
    )

    with pytest.raises(StateConcurrencyError, match="closed work item state changed"):
        apply(closed, stale_claim)


def test_closed_work_fences_stale_accepted_runner_result_decision() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    decision = decide(
        state,
        _recovery_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id=MANAGER_BLOCKED_ACTION_ID,
            input_id="observe-manager-blocked-before-close-race",
        ),
        simple_loop_context("observe-manager-blocked-before-close-race"),
    )
    assert decision.accepted is True
    closed = replace(
        state,
        closed_work_items={
            "work-prompt": ClosedWorkItemRecord(
                record_id="close-work-prompt-before-stale-result",
                work_item_id="work-prompt",
                source_run_id=None,
                action_id=None,
                created_by_input_id="operator-close-before-stale-result",
                operator_intervention_record_id=(
                    "operator-intervention:operator-close-before-stale-result"
                ),
                close_kind="operator_intervention",
            )
        },
    )

    with pytest.raises(StateConcurrencyError, match="closed work item state changed"):
        apply(closed, decision)


@pytest.mark.parametrize(
    ("field_name", "bad_value", "expected_reason"),
    (
        ("option_id", "simple_loop.missing_option", "unknown_intervention_option"),
        ("actor_kind", "runtime", "invalid_actor_kind"),
        ("payload", {"revise": True}, "payload_forbidden"),
    ),
)
def test_operator_lineage_intervention_preflight_rejects_invalid_requests(
    field_name: str,
    bad_value: object,
    expected_reason: str,
) -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan=plan, fingerprint=fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    ResumeInput = getattr(operator_api, "OperatorResumeLineageInput")
    kwargs = {
        "input_id": "operator-resume-lineage",
        "option_id": "simple_loop.resume_lineage",
        "selected_plan_ref": quarantine.selected_plan_ref,
        "quarantine_id": quarantine.quarantine_id,
        "lineage_id": None,
        "actor_id": "local-operator-tim",
        "actor_kind": "local_operator",
        "reason": "operator resumed simple loop lineage",
        "payload": None,
    }
    kwargs[field_name] = bad_value

    with pytest.raises(operator_api.OperatorInputError) as exc_info:
        operator_api.build_resume_lineage(quarantined, ResumeInput(**kwargs))

    assert exc_info.value.reason == expected_reason


def test_quarantine_lineage_action_from_active_recovery_records_quarantine() -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(plan, fingerprint, observed_at=1000)
    resumed = _resume_only_cooldown_wait(waiting)
    resumed_claimed = _claim_recovery_activation(
        resumed,
        input_id="claim-troubleshooter-manager-resumed",
        activation_id="activation-troubleshooter-manager-resumed",
        run_id="run-troubleshooter-manager-resumed",
        claim_id="claim-troubleshooter-manager-resumed",
        fencing_token="fence-troubleshooter-manager-resumed",
    )

    decision = decide(
        resumed_claimed,
        runner_observation(
            state=resumed_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-troubleshooter-manager-resumed",
            action_id=str(TROUBLESHOOTER_OPERATOR_NEEDED_ACTION_ID),
            input_id="observe-troubleshooter-operator-needed-active",
            artifact_payload=troubleshooting_report_payload(
                result="operator needed",
                next_route="operator_intervention",
            ),
        ),
        deterministic_context(
            transition_id="transition-observe-troubleshooter-operator-needed-active",
        ),
    )

    after = apply(resumed_claimed, decision)
    quarantine = _active_lineage_quarantine(after, "work-prompt")

    assert decision.accepted is True
    assert decision.refusal is None
    assert "mutation.record_artifact" in mutation_kinds(decision)
    assert quarantine.action_id == TROUBLESHOOTER_OPERATOR_NEEDED_ACTION_ID
    assert quarantine.attempt_count == 2
    assert quarantine.created_input_id == (
        "observe-troubleshooter-operator-needed-active"
    )
    assert "mutation.record_lineage_quarantine" in mutation_kinds(decision)
    artifact = after.artifacts[
        "transition-observe-troubleshooter-operator-needed-active:artifact"
    ]
    assert artifact.schema_id == ArtifactSchemaId("simple_loop.troubleshooting_report")
    assert artifact.work_item_id == "work-prompt"
    assert artifact.payload == troubleshooting_report_payload(
        result="operator needed",
        next_route="operator_intervention",
    )


def test_quarantined_lineage_fences_claim_and_stale_runner_decisions() -> None:
    plan, fingerprint = compile_simple_loop()
    third_source = _third_source_retry_claimed(
        plan=plan,
        fingerprint=fingerprint,
    )
    stale_packet_ready = decide(
        third_source,
        runner_observation(
            state=third_source,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-source-retry-3",
            action_id=str(MANAGER_PACKET_READY_ACTION_ID),
            input_id="observe-manager-packet-ready-stale-at-threshold",
            artifact_payload=work_packet_payload(),
        ),
        deterministic_context(
            transition_id=(
                "transition-observe-manager-packet-ready-stale-at-threshold"
            ),
            work_item_id="work-stale-worker-at-threshold",
            activation_id="activation-stale-worker-at-threshold",
        ),
    )
    assert stale_packet_ready.accepted is True

    quarantine_decision = decide(
        third_source,
        _recovery_observation(
            state=third_source,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-source-retry-3",
            action_id=MANAGER_BLOCKED_ACTION_ID,
            input_id="observe-manager-blocked-3-racing",
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-3-racing",
            activation_id="activation-should-not-exist-at-threshold-racing",
        ),
    )
    assert quarantine_decision.accepted is True
    quarantined = apply(third_source, quarantine_decision)

    refused_claim = decide(
        quarantined,
        ClaimWork(
            "claim-source-retry-after-lineage-quarantine",
            activation_id="activation-returned-manager-2",
        ),
        deterministic_context(
            transition_id="transition-claim-source-retry-after-quarantine",
        ),
    )
    assert refused_claim.accepted is False
    assert refused_claim.refusal is not None
    assert refused_claim.refusal.reason == "lineage_quarantined"
    with pytest.raises(StateConcurrencyError, match="lineage quarantine"):
        apply(quarantined, stale_packet_ready)


def test_recovery_attempts_are_keyed_by_plan_fingerprint() -> None:
    plan1, fingerprint1 = compile_simple_loop()
    policy2 = replace(plan1.recovery_policies[0], default_cooldown_seconds=901)
    wait2 = replace(plan1.wait_states[0], duration_seconds=901)
    plan2 = replace(plan1, recovery_policies=(policy2,), wait_states=(wait2,))
    fingerprint2 = authority_fingerprint(plan2)

    state1 = bootstrap_to_manager_claim(plan1, fingerprint1)
    attempt1 = apply(
        state1,
        decide(
            state1,
            _recovery_observation(
                state=state1,
                plan=plan1,
                fingerprint=fingerprint1,
                run_id="run-manager",
                action_id=MANAGER_BLOCKED_ACTION_ID,
                input_id="observe-manager-blocked",
            ),
            simple_loop_context("observe-manager-blocked"),
        ),
    )

    state2_source = replace(
        bootstrap_to_manager_claim(plan2, fingerprint2),
        recovery_attempts=attempt1.recovery_attempts,
    )
    state2 = apply(
        state2_source,
        decide(
            state2_source,
            _recovery_observation(
                state=state2_source,
                plan=plan2,
                fingerprint=fingerprint2,
                run_id="run-manager",
                action_id=MANAGER_BLOCKED_ACTION_ID,
                input_id="observe-manager-blocked-plan2",
            ),
            simple_loop_context("observe-manager-blocked-plan2"),
        ),
    )

    attempts = tuple(state2.recovery_attempts.values())
    assert len(attempts) == 2
    assert {
        attempt.plan_ref.authority_fingerprint for attempt in attempts
    } == {fingerprint1, fingerprint2}
    assert {
        attempt.plan_ref.authority_fingerprint: attempt.attempt_count
        for attempt in attempts
    } == {fingerprint1: 1, fingerprint2: 1}

@pytest.mark.parametrize(
    ("action_id", "input_id", "expected_activation_id"),
    (
        (
            WORKER_BLOCKED_ACTION_ID,
            "observe-worker-blocked",
            "activation-troubleshooter-worker-blocked",
        ),
        (
            WORKER_FAILED_ACTION_ID,
            "observe-worker-failed",
            "activation-troubleshooter-worker-failed",
        ),
    ),
)
def test_worker_blocked_and_failed_schedule_same_compiled_recovery_target(
    action_id: ActionId,
    input_id: str,
    expected_activation_id: str,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    source_work_item = state.work_items["work-worker"]
    source_run = state.runs["run-worker"]
    action = action_by_id(plan, str(action_id))

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id=str(action_id),
            input_id=input_id,
            artifact_payload={},
        ),
        simple_loop_context(input_id),
    )

    assert decision.accepted is True
    _assert_no_forbidden_recovery_mutations(decision)
    after = apply(state, decision)
    recovery_activation = after.activations[expected_activation_id]

    assert recovery_activation.stage_kind_id == action.target_stage_kind_id
    assert recovery_activation.graph_node_id == action.target_graph_node_id
    assert recovery_activation.runner_binding_id == action.runner_binding_id
    _assert_recovery_activation(
        activation=recovery_activation,
        source_work_item_id="work-worker",
        lineage_id=source_work_item.lineage_id,
        fingerprint=fingerprint,
        queue_family_id=QueueFamilyId("work_packet"),
    )
    assert source_work_item.payload == after.work_items["work-worker"].payload
    assert set(source_work_item.payload) == {"prompt_id", "body", "work_packet"}
    assert {
        "prompt_id": source_work_item.payload["prompt_id"],
        "body": source_work_item.payload["body"],
    } == work_prompt_payload()
    assert source_run.run_ref.plan_ref == after.runs["run-worker"].run_ref.plan_ref
    assert recovery_activation.plan_ref == source_run.run_ref.plan_ref
    _assert_recovery_route(
        after=after,
        action_id=action_id,
        source_run_id="run-worker",
        source_work_item_id="work-worker",
        target_activation_id=expected_activation_id,
    )
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-worker",
        run_id="run-worker",
        action_id=action_id,
    )


def test_reviewer_blocked_schedules_partitionless_troubleshooter_recovery() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_reviewer_claim(plan, fingerprint)
    troubleshooter = next(
        stage
        for stage in plan.stage_kinds
        if str(stage.id) == "simple_loop.troubleshooter"
    )
    assert troubleshooter.partition_id is None

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer",
            action_id=str(REVIEWER_BLOCKED_ACTION_ID),
            input_id="observe-reviewer-blocked",
            artifact_payload={},
        ),
        simple_loop_context("observe-reviewer-blocked"),
    )

    assert decision.accepted is True
    after = apply(state, decision)
    recovery_activation = after.activations["activation-troubleshooter-reviewer"]

    assert recovery_activation.work_item_id == "work-reviewer"
    assert recovery_activation.stage_kind_id == troubleshooter.id
    assert recovery_activation.graph_node_id == "simple_loop.troubleshooter.start"
    assert recovery_activation.runner_binding_id == troubleshooter.runner_binding_id
    assert recovery_activation.plan_ref.authority_fingerprint == fingerprint
    assert _queue_counts(after, "work_packet") == (1, 0, 0, 0)

    status = operator_status(after)
    stage_catalog = {
        stage.stage_kind_id: stage.partition_id for stage in status.stage_kinds
    }
    assert stage_catalog["simple_loop.troubleshooter"] is None
    assert status.active_runs == ()


def test_stale_and_duplicate_recovery_observations_do_not_progress() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    first = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-manager",
        action_id=str(MANAGER_BLOCKED_ACTION_ID),
        input_id="observe-manager-blocked",
        artifact_payload={},
    )
    first_decision = decide(
        state,
        first,
        simple_loop_context("observe-manager-blocked"),
    )
    second_decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id=str(MANAGER_BLOCKED_ACTION_ID),
            input_id="observe-manager-blocked-race",
            artifact_payload={},
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-race",
            activation_id="activation-troubleshooter-manager-race",
        ),
    )
    assert first_decision.accepted is True
    assert second_decision.accepted is True

    after_first = apply(state, first_decision)
    with pytest.raises(StateConcurrencyError, match="run observation state changed"):
        apply(after_first, second_decision)

    duplicate = runner_observation(
        state=after_first,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-manager",
        action_id=str(MANAGER_BLOCKED_ACTION_ID),
        input_id="observe-manager-blocked-again",
        artifact_payload={},
        observation_payload_overrides={"second": "observation"},
    )
    duplicate_decision = decide(
        after_first,
        duplicate,
        deterministic_context(transition_id="transition-duplicate-manager-blocked"),
    )
    after_duplicate = apply(after_first, duplicate_decision)

    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "duplicate_runner_observation"
    _assert_no_workflow_progress(duplicate_decision)
    assert after_duplicate.activations == after_first.activations
    assert after_duplicate.activation_routes == after_first.activation_routes
    _assert_audit_context(
        duplicate_decision,
        fingerprint=fingerprint,
        work_item_id="work-prompt",
        run_id="run-manager",
        action_id=MANAGER_BLOCKED_ACTION_ID,
        refusal_reason="duplicate_runner_observation",
    )


def test_recovery_dispatch_uses_source_work_item_payload_after_claim() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    decision = decide(
        state,
        _recovery_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id=WORKER_BLOCKED_ACTION_ID,
            input_id="observe-worker-blocked",
        ),
        simple_loop_context("observe-worker-blocked"),
    )
    recovered = apply(
        state,
        decision,
    )

    claimed = apply(
        recovered,
        decide(
            recovered,
            ClaimWork(
                "claim-troubleshooter-worker",
                activation_id="activation-troubleshooter-worker-blocked",
            ),
            deterministic_context(
                transition_id="transition-claim-troubleshooter-worker",
                run_id="run-troubleshooter-worker",
                claim_id="claim-troubleshooter-worker",
                fencing_token="fence-troubleshooter-worker",
            ),
        ),
    )
    production_dispatch = build_dispatch_envelope_for_run(
        state=claimed,
        run_id="run-troubleshooter-worker",
    )
    fake_dispatch = fake_runner_dispatch_envelope_for_run(
        state=claimed,
        run_id="run-troubleshooter-worker",
    )

    assert fake_dispatch.payload() == production_dispatch.payload()
    assert production_dispatch.work_item_id == "work-worker"
    assert production_dispatch.stage_kind_id == "simple_loop.troubleshooter"
    assert production_dispatch.graph_node_id == "simple_loop.troubleshooter.start"
    assert set(production_dispatch.work_item_payload) == {
        "prompt_id",
        "body",
        "work_packet",
    }
    assert {
        "prompt_id": production_dispatch.work_item_payload["prompt_id"],
        "body": production_dispatch.work_item_payload["body"],
    } == work_prompt_payload()
