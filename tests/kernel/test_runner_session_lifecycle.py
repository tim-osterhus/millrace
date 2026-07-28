from __future__ import annotations

from dataclasses import replace

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.state import RunnerSessionCompletionRecord, RunnerSessionRecord
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    CreateRunnerSession,
    RecordRunnerSessionCancellationAttempt,
    RecordRunnerSessionCompletion,
    RequestRunnerSessionCancellation,
)
from millrace.kernel import StateConcurrencyError, apply, decide
from millrace.kernel.runner_sessions import is_legal_runner_session_transition
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping


def _claimed_state():
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    fingerprint = authority_fingerprint(result.plan)
    return bootstrap_to_taskmaster_claim(result.plan, fingerprint)


def test_session_creation_refuses_run_authority_drift() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    stale_ref = replace(run.run_ref, generation=run.run_ref.generation + 1)

    decision = decide(
        state,
        CreateRunnerSession(
            "create-session-drift",
            run_ref=stale_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        deterministic_context(transition_id="transition-create-session-drift"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_authority_mismatch"


def test_session_creation_apply_rechecks_complete_run_authority() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    transition_input = CreateRunnerSession(
        "create-session",
        run_ref=run.run_ref,
        session_id="session-1",
        session_fencing_token="session-fence-1",
        created_at=100,
        explicit_retry_intent=False,
    )
    decision = decide(
        state,
        transition_input,
        deterministic_context(transition_id="transition-create-session"),
    )
    drifted_run = replace(
        run,
        run_ref=replace(run.run_ref, claim_id="changed-claim"),
    )
    drifted_state = replace(
        state,
        runs={**state.runs, run.run_ref.run_id: drifted_run},
    )

    with pytest.raises(StateConcurrencyError, match="authority"):
        apply(drifted_state, decision)


def test_session_creation_does_not_persist_a_prestart_locator() -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref
    decision = decide(
        state,
        CreateRunnerSession(
            "create-session",
            run_ref=run_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        deterministic_context(transition_id="transition-create-session"),
    )

    created = apply(state, decision).runner_sessions["session-1"]

    assert created.durable_locator_digest is None


def test_explicit_retry_keeps_runref_and_advances_session_generation() -> None:
    state = _claimed_state()
    original_run_ref = state.runs["run-taskmaster"].run_ref
    first = CreateRunnerSession(
        "create-session-1",
        run_ref=original_run_ref,
        session_id="session-1",
        session_fencing_token="session-fence-1",
        created_at=100,
        explicit_retry_intent=False,
    )
    state = apply(
        state,
        decide(
            state,
            first,
            deterministic_context(transition_id="transition-create-session-1"),
        ),
    )
    first_session = replace(
        state.runner_sessions["session-1"],
        state="failed",
        ended_at=150,
        cleanup_disposition="complete",
    )
    state = replace(
        state,
        runner_sessions={"session-1": first_session},
    )

    retry = CreateRunnerSession(
        "create-session-2",
        run_ref=original_run_ref,
        session_id="session-2",
        session_fencing_token="session-fence-2",
        created_at=200,
        explicit_retry_intent=True,
    )
    state = apply(
        state,
        decide(
            state,
            retry,
            deterministic_context(transition_id="transition-create-session-2"),
        ),
    )

    assert state.runs["run-taskmaster"].run_ref == original_run_ref
    assert state.runs["run-taskmaster"].current_session_id == "session-2"
    assert state.runs["run-taskmaster"].last_dispatch_generation == 2
    assert state.runner_sessions["session-2"].dispatch_generation == 2


@pytest.mark.parametrize(
    ("prior", "next_state"),
    (
        ("created", "starting"),
        ("created", "cancellation_requested"),
        ("created", "failed"),
        ("starting", "running"),
        ("starting", "completed"),
        ("starting", "failed"),
        ("starting", "cancellation_requested"),
        ("starting", "lost"),
        ("running", "completed"),
        ("running", "failed"),
        ("running", "cancellation_requested"),
        ("running", "lost"),
        ("cancellation_requested", "terminating"),
        ("cancellation_requested", "completed"),
        ("cancellation_requested", "interrupted"),
        ("cancellation_requested", "failed"),
        ("cancellation_requested", "lost"),
        ("terminating", "completed"),
        ("terminating", "interrupted"),
        ("terminating", "failed"),
        ("terminating", "lost"),
    ),
)
def test_runner_session_legal_state_transitions(
    prior: str,
    next_state: str,
) -> None:
    assert is_legal_runner_session_transition(prior, next_state)


@pytest.mark.parametrize(
    ("prior", "next_state"),
    (
        ("created", "running"),
        ("running", "starting"),
        ("completed", "running"),
        ("interrupted", "failed"),
        ("failed", "starting"),
        ("lost", "created"),
    ),
)
def test_runner_session_illegal_state_transitions(
    prior: str,
    next_state: str,
) -> None:
    assert not is_legal_runner_session_transition(prior, next_state)


@pytest.mark.parametrize(
    ("state", "cleanup", "valid"),
    (
        ("completed", "complete", True),
        ("failed", "not_required", True),
        ("interrupted", "complete", True),
        ("lost", "orphan_risk", True),
        ("completed", "pending", False),
        ("failed", "orphan_risk", False),
        ("interrupted", "pending", False),
        ("lost", "complete", False),
    ),
)
def test_runner_session_terminal_cleanup_pairing(
    state: str,
    cleanup: str,
    valid: bool,
) -> None:
    kwargs = {
        "session_id": "session-1",
        "run_id": "run-1",
        "dispatch_generation": 1,
        "session_fencing_token": "session-fence-1",
        "state": state,
        "created_at": 100,
        "start_intent_at": 110,
        "started_at": 120,
        "ended_at": 130,
        "durable_locator_digest": "sha256:" + "a" * 64,
        "cleanup_disposition": cleanup,
    }
    if valid:
        assert RunnerSessionRecord(**kwargs).cleanup_disposition == cleanup
    else:
        with pytest.raises(ValueError):
            RunnerSessionRecord(**kwargs)


def test_completed_session_requires_result_evidence() -> None:
    with pytest.raises(ValueError, match="result evidence"):
        RunnerSessionCompletionRecord(
            completion_id="completion-1",
            session_id="session-1",
            run_id="run-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            terminal_state="completed",
            exit_kind="success",
            adapter_outcome_kind="success",
            adapter_error_kind=None,
            runner_result_evidence_digest=None,
            primary_cancellation_request_id=None,
            cleanup_disposition="complete",
            started_at=120,
            cancel_requested_at=None,
            completed_at=130,
            bounds_summary="bounded",
            truncation_metadata="none",
            redaction_policy_id="redaction.default",
            diagnostic_digest="sha256:" + "a" * 64,
            application_input_id="cli:run.session-completion:completion-1",
        )


@pytest.mark.parametrize(
    ("state", "start_intent_at", "started_at"),
    (
        ("created", 110, None),
        ("starting", None, None),
        ("running", 110, None),
        ("cancellation_requested", None, 120),
    ),
)
def test_runner_session_refuses_state_timestamp_contradictions(
    state: str,
    start_intent_at: int | None,
    started_at: int | None,
) -> None:
    with pytest.raises(ValueError, match="runner session"):
        RunnerSessionRecord(
            session_id="session-1",
            run_id="run-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            state=state,
            created_at=100,
            start_intent_at=start_intent_at,
            started_at=started_at,
            ended_at=None,
            durable_locator_digest="sha256:" + "a" * 64,
            cleanup_disposition="pending",
        )


def test_runner_session_cancellation_attempt_and_completion_are_durable() -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref
    inputs = (
        CreateRunnerSession(
            "create-session",
            run_ref=run_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        AdvanceRunnerSession(
            "start-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="created",
            next_state="starting",
            occurred_at=110,
        ),
        AdvanceRunnerSession(
            "session-running",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="starting",
            next_state="running",
            occurred_at=120,
            durable_locator_digest="sha256:" + "a" * 64,
        ),
        RequestRunnerSessionCancellation(
            "cancel-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="running",
            request_id="cancel-1",
            reason="operator_cancel_work",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=130,
            request_order=1,
            primary=True,
        ),
        RecordRunnerSessionCancellationAttempt(
            "attempt-cancel-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="cancellation_requested",
            attempt_id="attempt-1",
            request_id="cancel-1",
            sequence=1,
            operation="cooperative_cancel",
            result="succeeded",
            started_at=135,
            completed_at=140,
            bounded_diagnostic_digest="sha256:" + "b" * 64,
        ),
    )
    for transition_input in inputs:
        decision = decide(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}"
            ),
        )
        assert decision.accepted
        state = apply(state, decision)

    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id="session-1",
        run_id="run-taskmaster",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        terminal_state="interrupted",
        exit_kind="cancelled",
        adapter_outcome_kind=None,
        adapter_error_kind=None,
        runner_result_evidence_digest=None,
        primary_cancellation_request_id="cancel-1",
        cleanup_disposition="complete",
        started_at=120,
        cancel_requested_at=130,
        completed_at=150,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest="sha256:" + "c" * 64,
        application_input_id="cli:run.session-completion:completion-1",
    )
    decision = decide(
        state,
        RecordRunnerSessionCompletion(
            "record-completion",
            run_ref=run_ref,
            expected_state="cancellation_requested",
            completion=completion,
        ),
        deterministic_context(transition_id="transition-record-completion"),
    )
    assert decision.accepted
    state = apply(state, decision)

    assert state.runner_sessions["session-1"].state == "interrupted"
    assert (
        state.runner_sessions["session-1"].durable_locator_digest
        == "sha256:" + "a" * 64
    )
    assert state.runner_session_cancellation_requests["cancel-1"].primary
    assert state.runner_session_cancellation_attempts["attempt-1"].sequence == 1
    assert state.runner_session_completions["session-1"] == completion

    duplicate = decide(
        state,
        RecordRunnerSessionCompletion(
            "record-completion-duplicate",
            run_ref=run_ref,
            expected_state="cancellation_requested",
            completion=completion,
        ),
        deterministic_context(transition_id="transition-completion-duplicate"),
    )
    assert duplicate.accepted is False
    assert duplicate.refusal is not None
    assert duplicate.refusal.reason == "duplicate_runner_session_completion"

    terminal_attempt = decide(
        state,
        RecordRunnerSessionCancellationAttempt(
            "attempt-after-completion",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="interrupted",
            attempt_id="attempt-2",
            request_id="cancel-1",
            sequence=2,
            operation="transport_cleanup",
            result="succeeded",
            started_at=151,
            completed_at=152,
            bounded_diagnostic_digest="sha256:" + "d" * 64,
        ),
        deterministic_context(transition_id="transition-terminal-attempt"),
    )
    assert terminal_attempt.accepted is False
    assert terminal_attempt.refusal is not None
    assert terminal_attempt.refusal.reason == "invalid_runner_session_transition"
