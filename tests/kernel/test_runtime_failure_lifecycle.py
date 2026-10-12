from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_eligible,
    runtime_failure_input,
)
from millrace.contracts.state import (
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RuntimeState,
)
from millrace.contracts.transition import (
    ApplyRunnerRuntimeFailure,
    ClaimWork,
    RunnerResultObserved,
    TimerDue,
)
from millrace.kernel import apply, decide
from millrace.kernel.lookups import run_is_settled
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.testing import (
    decide_with_fake_runner_completion,
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_admission, generic_fanout
from tests.kernel.test_runtime_failure_recovery import failed_state


def _failed_state_with_max_one_active_run(monkeypatch):
    compile_plan = generic_admission.compile_plan

    def compile_with_limit(workflow_source=None):
        source = deepcopy(workflow_source or generic_admission.source())
        source["concurrency_policies"] = [
            {
                "id": "admission.single_active_run",
                "partition_id": "primary",
                "max_active_runs": 1,
                "coexist_partition_ids": (),
            }
        ]
        return compile_plan(source)

    monkeypatch.setattr(generic_admission, "compile_plan", compile_with_limit)
    return failed_state(monkeypatch, mapped=True)


def _apply_timeout(
    state: RuntimeState,
    completion,
    *,
    transition_id: str,
    activation_id: str,
) -> tuple[RuntimeState, ApplyRunnerRuntimeFailure]:
    run = state.runs[completion.run_id]
    command = runtime_failure_input(state, completion)
    decision = decide(
        state,
        command,
        deterministic_context(
            transition_id=transition_id,
            work_item_id=run.work_item_id,
            activation_id=activation_id,
            run_id=run.run_ref.run_id,
            claim_id=run.run_ref.claim_id,
            fencing_token=run.run_ref.fencing_token,
        ),
    )
    assert decision.accepted, decision.refusal
    applied = apply(state, decision)
    assert runtime_failure_applied(applied, run.run_ref.run_id)
    return applied, command


def _claim_activation(
    state: RuntimeState,
    activation_id: str,
    *,
    transition_id: str,
    run_id: str,
    claim_id: str,
    fencing_token: str,
) -> RuntimeState:
    activation = state.activations[activation_id]
    decision = decide(
        state,
        ClaimWork(claim_id, activation_id=activation_id),
        deterministic_context(
            transition_id=transition_id,
            work_item_id=activation.work_item_id,
            activation_id=activation_id,
            run_id=run_id,
            claim_id=claim_id,
            fencing_token=fencing_token,
        ),
    )
    assert decision.accepted, decision.refusal
    return apply(state, decision)


def _observation_for_action(
    state: RuntimeState,
    run_id: str,
    action_id: str,
    *,
    input_id: str,
    observed_at: int = 10,
) -> RunnerResultObserved:
    run = state.runs[run_id]
    activation = state.activations[run.activation_id]
    plan = state.admitted_plans[
        run.run_ref.plan_ref.authority_fingerprint
    ].selected_plan
    action = next(
        action for action in plan.terminal_actions if str(action.id) == action_id
    )
    marker = next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )
    return RunnerResultObserved(
        input_id,
        run_id=run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker=marker,
            artifact_payload=generic_fanout.packet_payload(),
        ),
        observed_at=observed_at,
    )


def _apply_agent_result(
    state: RuntimeState,
    run_id: str,
    action_id: str,
    *,
    input_id: str,
    transition_id: str,
    activation_id: str,
) -> RuntimeState:
    run = state.runs[run_id]
    observation = _observation_for_action(
        state,
        run_id,
        action_id,
        input_id=input_id,
    )
    decision = decide_with_fake_runner_completion(
        state,
        observation,
        deterministic_context(
            transition_id=transition_id,
            work_item_id=run.work_item_id,
            activation_id=activation_id,
            run_id=run_id,
            claim_id=run.run_ref.claim_id,
            fencing_token=run.run_ref.fencing_token,
        ),
    )
    assert decision.accepted, decision.refusal
    return apply(state, decision)


def _make_timeout_completion(
    state: RuntimeState,
    run_id: str,
) -> tuple[RuntimeState, RunnerSessionCompletionRecord]:
    run = state.runs[run_id]
    activation = state.activations[run.activation_id]
    observation = RunnerResultObserved(
        f"timeout-fixture:{run_id}",
        run_id=run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker="DONE",
            artifact_payload=generic_fanout.packet_payload(),
        ),
        observed_at=3,
    )
    state, _ = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )
    run = state.runs[run_id]
    assert run.current_session_id is not None
    session = state.runner_sessions[run.current_session_id]
    completion = state.runner_session_completions[session.session_id]
    failed_session = replace(session, state="failed")
    failed_completion = replace(
        completion,
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind="error",
        adapter_error_kind="timeout",
        runner_result_evidence_digest=None,
    )
    return (
        replace(
            state,
            runner_sessions={
                **state.runner_sessions,
                session.session_id: failed_session,
            },
            runner_session_completions={
                **state.runner_session_completions,
                session.session_id: failed_completion,
            },
        ),
        failed_completion,
    )


def _route_target_for_input(state: RuntimeState, input_id: str) -> str:
    route = next(
        route
        for route in state.activation_routes
        if route.created_by_input_id == input_id
    )
    return route.target_activation_id


def _claim_and_return(
    state: RuntimeState,
    recovery_activation_id: str,
    *,
    suffix: str,
) -> RuntimeState:
    state = _claim_activation(
        state,
        recovery_activation_id,
        transition_id=f"claim-recovery-{suffix}",
        run_id=f"run-recovery-{suffix}",
        claim_id=f"claim-recovery-{suffix}",
        fencing_token=f"fence-recovery-{suffix}",
    )
    state = _apply_agent_result(
        state,
        f"run-recovery-{suffix}",
        generic_admission.RECOVERY_RETURN_ACTION_ID,
        input_id=f"recovery-return-{suffix}",
        transition_id=f"transition-recovery-return-{suffix}",
        activation_id=f"returned-source-{suffix}",
    )
    return state


def test_timeout_runs_selected_recovery_then_returns_to_normal_close(
    monkeypatch,
) -> None:
    state, completion = _failed_state_with_max_one_active_run(monkeypatch)
    original_run_id = completion.run_id
    original_session_id = completion.session_id
    state, command = _apply_timeout(
        state,
        completion,
        transition_id="transition-runtime-timeout-one",
        activation_id="recovery-activation-one",
    )

    assert state.runner_session_completions[original_session_id] == completion
    assert state.runner_sessions[original_session_id].state == "failed"
    assert completion.runner_result_evidence_digest is None
    assert not any(
        o.run_id == original_run_id for o in state.runner_observations.values()
    )
    assert not state.artifacts
    assert run_is_settled(state, original_run_id)
    attempt = next(iter(state.recovery_attempts.values()))
    assert (attempt.attempt_count, attempt.phase) == (1, "active_recovery")

    recovery_activation_id = _route_target_for_input(state, command.input_id)
    assert [
        c.activation_id for c in list_ready_dispatch_candidates(state).candidates
    ] == [recovery_activation_id]
    state = _claim_and_return(state, recovery_activation_id, suffix="one")
    assert run_is_settled(state, "run-recovery-one")
    assert (
        str(state.activations["returned-source-one"].stage_kind_id)
        == generic_admission.PARENT_STAGE_ID
    )

    state = _claim_activation(
        state,
        "returned-source-one",
        transition_id="claim-returned-source-one",
        run_id="run-returned-source-one",
        claim_id="claim-returned-source-one",
        fencing_token="fence-returned-source-one",
    )
    state = _apply_agent_result(
        state,
        "run-returned-source-one",
        "admission.complete",
        input_id="source-normal-success-after-recovery",
        transition_id="transition-source-normal-success-after-recovery",
        activation_id="unused-after-close",
    )

    assert "work-generic-parent" in state.closed_work_items
    assert state.recovery_attempts[attempt.record_id].phase == "resolved"
    assert state.runner_session_completions[original_session_id] == completion
    assert not any(
        o.run_id == original_run_id for o in state.runner_observations.values()
    )
    assert runtime_failure_applied(state, original_run_id)


def test_timeout_policy_exhaustion_waits_routes_and_quarantines_at_threshold(
    monkeypatch,
) -> None:
    state, first_completion = _failed_state_with_max_one_active_run(monkeypatch)
    state, first_command = _apply_timeout(
        state,
        first_completion,
        transition_id="transition-timeout-attempt-one",
        activation_id="recovery-activation-attempt-one",
    )
    first_recovery = _route_target_for_input(state, first_command.input_id)
    state = _claim_and_return(state, first_recovery, suffix="attempt-one")
    state = _claim_activation(
        state,
        "returned-source-attempt-one",
        transition_id="claim-source-attempt-two",
        run_id="run-source-attempt-two",
        claim_id="claim-source-attempt-two",
        fencing_token="fence-source-attempt-two",
    )
    state, second_completion = _make_timeout_completion(state, "run-source-attempt-two")
    state, second_command = _apply_timeout(
        state,
        second_completion,
        transition_id="transition-timeout-attempt-two",
        activation_id="no-recovery-before-cooldown",
    )
    attempt = next(iter(state.recovery_attempts.values()))
    assert (attempt.attempt_count, attempt.phase) == (2, "pending_cooldown")
    wait = next(iter(state.cooldown_waits.values()))
    assert wait.consumed_input_id is None

    timer = TimerDue(
        "timer-runtime-failure-cooldown", wait_id=wait.wait_id, observed_at=wait.due_at
    )
    timer_decision = decide(
        state,
        timer,
        deterministic_context(
            transition_id="transition-timer-runtime-failure-cooldown",
            activation_id="recovery-activation-after-cooldown",
        ),
    )
    assert timer_decision.accepted, timer_decision.refusal
    state = apply(state, timer_decision)
    assert state.cooldown_waits[wait.wait_id].consumed_input_id == timer.input_id
    assert state.cooldown_waits[wait.wait_id].resulting_recovery_activation_id == (
        "recovery-activation-after-cooldown"
    )
    state = _claim_and_return(
        state,
        "recovery-activation-after-cooldown",
        suffix="after-cooldown",
    )
    state = _claim_activation(
        state,
        "returned-source-after-cooldown",
        transition_id="claim-source-attempt-three",
        run_id="run-source-attempt-three",
        claim_id="claim-source-attempt-three",
        fencing_token="fence-source-attempt-three",
    )
    state, third_completion = _make_timeout_completion(
        state, "run-source-attempt-three"
    )
    state, third_command = _apply_timeout(
        state,
        third_completion,
        transition_id="transition-timeout-attempt-three",
        activation_id="forbidden-fourth-recovery",
    )

    attempt = state.recovery_attempts[attempt.record_id]
    assert (attempt.attempt_count, attempt.phase) == (3, "quarantine_eligible")
    assert len(state.lineage_quarantines) == 1
    assert not any(
        route.created_by_input_id == third_command.input_id
        for route in state.activation_routes
    )
    assert run_is_settled(state, "run-source-attempt-three")
    assert runtime_failure_applied(state, "run-source-attempt-three")
    assert run_is_settled(state, "run-source-attempt-two")


def test_pending_cooldown_defers_sibling_timeout_then_reapplies_once(
    monkeypatch,
) -> None:
    state, first_completion = _failed_state_with_max_one_active_run(monkeypatch)
    state, first_command = _apply_timeout(
        state,
        first_completion,
        transition_id="transition-sibling-case-attempt-one",
        activation_id="sibling-case-recovery-one",
    )
    state = _claim_and_return(
        state,
        _route_target_for_input(state, first_command.input_id),
        suffix="sibling-case-one",
    )
    state = _claim_activation(
        state,
        "returned-source-sibling-case-one",
        transition_id="claim-sibling-case-source-two",
        run_id="run-sibling-case-source-two",
        claim_id="claim-sibling-case-source-two",
        fencing_token="fence-sibling-case-source-two",
    )
    state, second_completion = _make_timeout_completion(
        state, "run-sibling-case-source-two"
    )
    state, _ = _apply_timeout(
        state,
        second_completion,
        transition_id="transition-sibling-case-attempt-two",
        activation_id="no-sibling-recovery-before-cooldown",
    )
    wait = next(iter(state.cooldown_waits.values()))

    source_activation = state.activations["returned-source-sibling-case-one"]
    sibling_activation = replace(
        source_activation,
        activation_id="activation-runtime-failure-sibling",
        created_by_input_id="synthetic-runtime-failure-sibling",
        generation=0,
        claimed_by_run_id=None,
    )
    state = replace(
        state,
        activations={
            **state.activations,
            sibling_activation.activation_id: sibling_activation,
        },
    )
    state = _claim_activation(
        state,
        sibling_activation.activation_id,
        transition_id="claim-runtime-failure-sibling",
        run_id="run-runtime-failure-sibling",
        claim_id="claim-runtime-failure-sibling",
        fencing_token="fence-runtime-failure-sibling",
    )
    state, sibling_completion = _make_timeout_completion(
        state,
        "run-runtime-failure-sibling",
    )
    sibling_command = runtime_failure_input(state, sibling_completion)
    deferred = decide(
        state,
        sibling_command,
        deterministic_context(transition_id="defer-runtime-failure-sibling"),
    )
    assert not deferred.accepted
    assert deferred.refusal is not None
    assert deferred.refusal.reason == "cooldown_wait_pending"
    assert deferred.receipt_ref is None
    assert sibling_completion.session_id in state.runner_session_completions
    assert sibling_completion.runner_result_evidence_digest is None
    assert state.cooldown_waits[wait.wait_id].consumed_input_id is None

    timer = TimerDue(
        "timer-before-reapply-sibling-failure",
        wait_id=wait.wait_id,
        observed_at=wait.due_at,
    )
    timer_decision = decide(
        state,
        timer,
        deterministic_context(
            transition_id="transition-timer-before-reapply-sibling-failure",
            activation_id="sibling-case-due-recovery",
        ),
    )
    assert timer_decision.accepted, timer_decision.refusal
    state = apply(state, timer_decision)

    reapply = decide(
        state,
        sibling_command,
        deterministic_context(transition_id="reapply-runtime-failure-sibling"),
    )
    assert reapply.accepted, reapply.refusal
    state = apply(state, reapply)
    attempt = state.recovery_attempts[wait.recovery_attempt_record_id]
    assert (attempt.attempt_count, attempt.phase) == (3, "quarantine_eligible")
    assert runtime_failure_applied(state, "run-runtime-failure-sibling")

    replay = decide(
        state,
        sibling_command,
        deterministic_context(transition_id="reapply-runtime-failure-sibling"),
    )
    assert replay.accepted and replay.disposition == "replayed"
    assert apply(state, replay) == state
    assert state.recovery_attempts[attempt.record_id].attempt_count == 3


@pytest.mark.parametrize(
    ("case", "updates"),
    [
        ("non_timeout_adapter_error", {"adapter_error_kind": "transport"}),
        (
            "operator_cancellation",
            {"primary_cancellation_request_id": "operator-cancel"},
        ),
        ("daemon_shutdown", {"primary_cancellation_request_id": "daemon-stop"}),
        (
            "runtime_deadline_cancellation",
            {"primary_cancellation_request_id": "runtime-timeout"},
        ),
    ],
)
def test_nonrecoverable_and_cancelled_completions_never_qualify(
    monkeypatch,
    case: str,
    updates: dict[str, object],
) -> None:
    state, completion = failed_state(monkeypatch, mapped=True)
    session = state.runner_sessions[completion.session_id]
    if case in {
        "operator_cancellation",
        "daemon_shutdown",
        "runtime_deadline_cancellation",
    }:
        reason, source = {
            "operator_cancellation": ("operator_cancel_work", "operator"),
            "daemon_shutdown": ("daemon_shutdown", "daemon"),
            "runtime_deadline_cancellation": ("runner_timeout", "runtime"),
        }[case]
        request = RunnerSessionCancellationRecord(
            request_id=str(updates["primary_cancellation_request_id"]),
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            reason=reason,
            source_kind=source,
            actor_id=source,
            requested_at=2,
            request_order=1,
            primary=True,
        )
        state = replace(
            state,
            runner_session_cancellation_requests={request.request_id: request},
        )
        changed = replace(completion, cancel_requested_at=2, **updates)
        if case in {"operator_cancellation", "daemon_shutdown"}:
            changed = replace(
                changed,
                terminal_state="interrupted",
                exit_kind="cancelled",
                adapter_outcome_kind="cancelled",
                adapter_error_kind=None,
            )
            state = replace(
                state,
                runner_sessions={
                    **state.runner_sessions,
                    session.session_id: replace(
                        state.runner_sessions[session.session_id],
                        state="interrupted",
                    ),
                },
            )
    else:
        changed = replace(completion, **updates)
    state = replace(
        state,
        runner_session_completions={session.session_id: changed},
    )
    assert not runtime_failure_eligible(state, changed), case
    with pytest.raises(ValueError, match="ineligible runtime failure completion"):
        runtime_failure_input(state, changed)
    assert changed.application_input_id not in state.receipts
