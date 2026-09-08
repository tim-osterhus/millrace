from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    ClaimWork,
    RecordRecoveryAttempt,
    RunnerResultObserved,
    TimerDue,
)
from millrace.kernel import apply, decide
from millrace.kernel.terminal_actions import (
    TerminalActionResolution,
    _resolve_close_action,
    _with_reset_recovery_attempts,
)
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import (
    decide_with_fake_runner_completion as decide_with_completion,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_observation_payload,
)
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import generic_fanout
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_cooldown_runtime_state,
)


def _persisted_state(
    tmp_path: Path,
    state: RuntimeState,
    name: str,
) -> RuntimeState:
    path = tmp_path / name
    path.mkdir()
    return persist_and_load_runtime_state(path, state)


def _claim_sibling_parent(state: RuntimeState) -> RuntimeState:
    source = state.activations["activation-generic-returned-parent"]
    sibling = replace(
        source,
        activation_id="activation-generic-sibling",
        generation=0,
        created_by_input_id="synthetic-sibling",
        claimed_by_run_id=None,
    )
    state = replace(
        state,
        activations={**state.activations, sibling.activation_id: sibling},
    )
    context = deterministic_context(
        transition_id="transition-claim-generic-sibling",
        activation_id=sibling.activation_id,
        run_id="run-generic-sibling",
        claim_id="claim-generic-sibling",
        fencing_token="fence-generic-sibling",
    )
    decision = decide(
        state,
        ClaimWork("claim-generic-sibling", activation_id=sibling.activation_id),
        context,
    )
    assert decision.accepted is True
    return apply(state, decision)


def _sibling_pass(state: RuntimeState) -> RunnerResultObserved:
    run = state.runs["run-generic-sibling"]
    activation = state.activations[run.activation_id]
    plan = next(iter(state.admitted_plans.values())).selected_plan
    action = next(
        action
        for action in plan.terminal_actions
        if str(action.id) == "admission.complete"
    )
    marker = next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )
    return RunnerResultObserved(
        "observe-generic-sibling-pass",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker=marker,
            artifact_payload=generic_fanout.packet_payload(),
        ),
        observed_at=None,
    )


def _sibling_pass_context():
    return deterministic_context(
        transition_id="transition-observe-generic-sibling-pass",
        run_id="run-generic-sibling",
        activation_id="activation-generic-sibling",
    )


def _durable_counts(state: RuntimeState) -> tuple[int, ...]:
    return (
        len(state.runs),
        len(state.runner_sessions),
        len(state.runner_session_completions),
        len(state.runner_observations),
        len(state.transitions),
        len(state.receipts),
    )


def test_persisted_sibling_pass_keeps_pending_cooldown_wait(
    tmp_path: Path,
) -> None:
    state = _persisted_state(
        tmp_path,
        _claim_sibling_parent(_generic_cooldown_runtime_state()),
        "before",
    )
    wait = next(iter(state.cooldown_waits.values()))
    attempt = state.recovery_attempts[wait.recovery_attempt_record_id]

    observation = _sibling_pass(state)
    decision = decide_with_completion(
        state,
        observation,
        _sibling_pass_context(),
    )

    assert decision.accepted is True
    assert not any(
        isinstance(mutation, RecordRecoveryAttempt)
        for mutation in decision.mutations
    )
    applied = apply(state, decision)
    assert applied.cooldown_waits[wait.wait_id] == wait
    assert applied.recovery_attempts[attempt.record_id] == attempt

    reloaded = _persisted_state(tmp_path, applied, "after")
    assert reloaded.cooldown_waits[wait.wait_id] == wait
    assert reloaded.recovery_attempts[attempt.record_id] == attempt

    counts = _durable_counts(reloaded)
    replay = decide_with_completion(reloaded, observation, _sibling_pass_context())
    assert replay.accepted is True
    assert replay.disposition == "replayed"
    replayed = apply(reloaded, replay)
    assert replayed == reloaded
    assert _durable_counts(replayed) == counts


def test_due_wait_dispatches_then_later_reset_preserves_consumed_history(
    tmp_path: Path,
) -> None:
    state = _persisted_state(
        tmp_path,
        _generic_cooldown_runtime_state(),
        "waiting",
    )
    wait = next(iter(state.cooldown_waits.values()))
    attempt_id = wait.recovery_attempt_record_id

    early = decide(
        state,
        TimerDue("timer-early", wait_id=wait.wait_id, observed_at=wait.due_at - 1),
        deterministic_context(transition_id="transition-timer-early"),
    )
    assert early.accepted is False
    assert early.refusal is not None
    assert early.refusal.reason == "wait_not_due"

    due = decide(
        state,
        TimerDue("timer-due", wait_id=wait.wait_id, observed_at=wait.due_at),
        deterministic_context(
            transition_id="transition-timer-due",
            activation_id="activation-after-cooldown",
        ),
    )
    assert due.accepted is True
    state = apply(state, due)
    consumed_wait = state.cooldown_waits[wait.wait_id]
    assert consumed_wait.consumed_input_id == "timer-due"
    assert consumed_wait.resulting_recovery_activation_id == "activation-after-cooldown"
    assert state.recovery_attempts[attempt_id].phase == "active_recovery"
    state = _persisted_state(tmp_path, state, "consumed")

    duplicate = decide(
        state,
        TimerDue("timer-duplicate", wait_id=wait.wait_id, observed_at=wait.due_at + 1),
        deterministic_context(transition_id="transition-timer-duplicate"),
    )
    assert duplicate.accepted is False
    assert duplicate.refusal is not None
    assert duplicate.refusal.reason == "wait_already_consumed"

    state = _claim_sibling_parent(state)
    pass_decision = decide_with_completion(
        state,
        _sibling_pass(state),
        _sibling_pass_context(),
    )
    assert pass_decision.accepted is True
    assert any(
        isinstance(mutation, RecordRecoveryAttempt)
        for mutation in pass_decision.mutations
    )
    state = apply(state, pass_decision)
    assert state.recovery_attempts[attempt_id].phase == "resolved"
    assert state.cooldown_waits[wait.wait_id].consumed_input_id == "timer-due"

    reloaded = _persisted_state(tmp_path, state, "resolved")
    assert reloaded.recovery_attempts[attempt_id].phase == "resolved"
    assert reloaded.cooldown_waits[wait.wait_id].consumed_input_id == "timer-due"


@pytest.mark.parametrize("mismatch", ("lineage", "plan"))
def test_reset_guard_does_not_defer_unrelated_active_attempt(
    mismatch: str,
) -> None:
    state = _generic_cooldown_runtime_state()
    wait = next(iter(state.cooldown_waits.values()))
    original_attempt = state.recovery_attempts[wait.recovery_attempt_record_id]
    plan = next(iter(state.admitted_plans.values())).selected_plan
    run = state.runs["run-generic-returned-parent"]
    activation = state.activations[run.activation_id]
    work_item = state.work_items[run.work_item_id]
    action = next(
        action
        for action in plan.terminal_actions
        if str(action.id) == "admission.complete"
    )
    result = _resolve_close_action(
        transition_input=RunnerResultObserved(
            "synthetic-reset-result",
            run_id=run.run_ref.run_id,
            payload=fake_runner_observation_payload(
                run=run,
                activation=activation,
                plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
                marker="FANOUT_READY",
                artifact_payload=generic_fanout.packet_payload(),
            ),
            observed_at=None,
        ),
        context=deterministic_context(transition_id="transition-synthetic-reset"),
        selected_plan=plan,
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        observation_payload=None,
    )
    assert isinstance(result, TerminalActionResolution)

    if mismatch == "lineage":
        other_attempt = replace(
            original_attempt,
            record_id="synthetic-other-lineage-attempt",
            lineage_id="synthetic-other-lineage",
            phase="active_recovery",
            updated_by_input_id="synthetic-other-lineage",
        )
        other_work_item = replace(
            work_item,
            lineage_id="synthetic-other-lineage",
        )
        other_run = run
    else:
        other_plan_ref = replace(
            run.run_ref.plan_ref,
            authority_fingerprint=f"sha256:{'2' * 64}",
        )
        other_attempt = replace(
            original_attempt,
            record_id="synthetic-other-plan-attempt",
            plan_ref=other_plan_ref,
            phase="active_recovery",
            updated_by_input_id="synthetic-other-plan",
        )
        other_work_item = replace(
            work_item,
            ref=replace(work_item.ref, plan_ref=other_plan_ref),
        )
        other_run = replace(
            run,
            run_ref=replace(run.run_ref, plan_ref=other_plan_ref),
        )

    state = replace(
        state,
        recovery_attempts={
            **state.recovery_attempts,
            other_attempt.record_id: other_attempt,
        },
    )
    isolated = _with_reset_recovery_attempts(
        result=result,
        selected_plan=plan,
        state=state,
        run=other_run,
        work_item=other_work_item,
        action=action,
        input_id="synthetic-isolated-reset",
    )
    assert isinstance(isolated, TerminalActionResolution)
    reset_mutations = [
        mutation
        for mutation in isolated.mutations
        if isinstance(mutation, RecordRecoveryAttempt)
    ]
    assert len(reset_mutations) == 1
    assert reset_mutations[0].attempt is not None
    assert reset_mutations[0].attempt.record_id == other_attempt.record_id
    assert reset_mutations[0].attempt.phase == "resolved"
    assert (
        state.recovery_attempts[original_attempt.record_id].phase
        == "pending_cooldown"
    )
    assert state.cooldown_waits[wait.wait_id].consumed_input_id is None


@pytest.mark.parametrize("mismatch", ("lineage", "record_id", "attempt_count"))
def test_reset_guard_requires_exact_wait_not_pending_phase_alone(
    mismatch: str,
) -> None:
    state = _generic_cooldown_runtime_state()
    wait = next(iter(state.cooldown_waits.values()))
    original_attempt = state.recovery_attempts[wait.recovery_attempt_record_id]
    plan = next(iter(state.admitted_plans.values())).selected_plan
    run = state.runs["run-generic-returned-parent"]
    activation = state.activations[run.activation_id]
    work_item = state.work_items[run.work_item_id]
    action = next(
        action
        for action in plan.terminal_actions
        if str(action.id) == "admission.complete"
    )
    result = _resolve_close_action(
        transition_input=RunnerResultObserved(
            "synthetic-missing-wait-result",
            run_id=run.run_ref.run_id,
            payload=fake_runner_observation_payload(
                run=run,
                activation=activation,
                plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
                marker="FANOUT_READY",
                artifact_payload=generic_fanout.packet_payload(),
            ),
            observed_at=None,
        ),
        context=deterministic_context(transition_id="transition-synthetic-missing-wait"),
        selected_plan=plan,
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        observation_payload=None,
    )
    assert isinstance(result, TerminalActionResolution)

    if mismatch == "lineage":
        other_attempt = replace(
            original_attempt,
            record_id="synthetic-missing-wait-attempt",
            lineage_id="synthetic-missing-wait-lineage",
            phase="pending_cooldown",
            updated_by_input_id="synthetic-missing-wait",
        )
        other_work_item = replace(
            work_item,
            lineage_id=other_attempt.lineage_id,
        )
        recovery_attempts = {
            **state.recovery_attempts,
            other_attempt.record_id: other_attempt,
        }
    elif mismatch == "record_id":
        other_attempt = replace(
            original_attempt,
            record_id="synthetic-mismatched-record-id",
            phase="pending_cooldown",
            updated_by_input_id="synthetic-mismatched-record-id",
        )
        other_work_item = work_item
        recovery_attempts = {other_attempt.record_id: other_attempt}
    else:
        other_attempt = replace(
            original_attempt,
            record_id="synthetic-mismatched-attempt-count",
            attempt_count=original_attempt.attempt_count + 1,
            phase="pending_cooldown",
            updated_by_input_id="synthetic-mismatched-attempt-count",
        )
        other_work_item = work_item
        recovery_attempts = {other_attempt.record_id: other_attempt}

    state = replace(state, recovery_attempts=recovery_attempts)
    isolated = _with_reset_recovery_attempts(
        result=result,
        selected_plan=plan,
        state=state,
        run=run,
        work_item=other_work_item,
        action=action,
        input_id="synthetic-missing-wait-reset",
    )
    assert isinstance(isolated, TerminalActionResolution)
    reset = [
        mutation
        for mutation in isolated.mutations
        if isinstance(mutation, RecordRecoveryAttempt)
    ]
    assert len(reset) == 1
    assert reset[0].attempt is not None
    assert reset[0].attempt.record_id == other_attempt.record_id
    assert reset[0].attempt.phase == "resolved"
    assert state.cooldown_waits[wait.wait_id].consumed_input_id is None
    if mismatch == "lineage":
        assert (
            state.recovery_attempts[original_attempt.record_id].phase
            == "pending_cooldown"
        )


@pytest.mark.parametrize("phase", ("active_recovery", "resolved"))
def test_pending_wait_phase_integrity_refusal_remains(
    tmp_path: Path,
    phase: str,
) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, _generic_cooldown_runtime_state())
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE recovery_attempts SET phase = ?",
            (phase,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="cooldown_waits pending wait must match pending_cooldown attempt",
    ):
        load_runtime_state(db_path, cas_root)
