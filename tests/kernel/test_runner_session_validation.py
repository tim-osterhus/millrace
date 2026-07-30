from __future__ import annotations

from dataclasses import replace

import pytest

from kernel.test_runner_session_lifecycle import (
    _claimed_state,
    _state_with_active_cancellation_history,
)
from millrace.contracts.state import (
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    CreateRunnerSession,
    RecordRunnerSessionCancellationAttempt,
    RecordRunnerSessionCompletion,
    RequestRunnerSessionCancellation,
)
from millrace.kernel import apply, decide
from millrace.testing import deterministic_context


def test_cancellation_request_apply_atomically_enters_cancellation_state() -> None:
    state = _state_with_active_cancellation_history()
    session = replace(
        state.runner_sessions["session-1"],
        state="running",
    )
    state = replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_cancellation_requests={},
    )
    run_ref = state.runs["run-taskmaster"].run_ref
    transition_input = RequestRunnerSessionCancellation(
        "cancel-session",
        run_ref=run_ref,
        session_id=session.session_id,
        dispatch_generation=1,
        session_fencing_token=session.session_fencing_token,
        expected_state="running",
        request_id="cancel-1",
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id="operator-1",
        requested_at=130,
        request_order=1,
        primary=True,
    )

    decision = decide(
        state,
        transition_input,
        deterministic_context(transition_id="transition-cancel-session"),
    )
    assert decision.accepted

    applied = apply(state, decision)
    assert applied.runner_sessions["session-1"].state == "cancellation_requested"
    assert applied.runner_session_cancellation_requests["cancel-1"].primary


def test_cancellation_request_refuses_time_before_latest_session_phase() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="running",
        created_at=100,
        start_intent_at=110,
        started_at=120,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    state = replace(
        state,
        runs={
            **state.runs,
            run.run_ref.run_id: replace(
                run,
                current_session_id=session.session_id,
                last_dispatch_generation=1,
            ),
        },
        runner_sessions={session.session_id: session},
    )

    decision = decide(
        state,
        RequestRunnerSessionCancellation(
            "cancel-before-start",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=1,
            session_fencing_token=session.session_fencing_token,
            expected_state="running",
            request_id="cancel-1",
            reason="operator_cancel_work",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=119,
            request_order=1,
            primary=True,
        ),
        deterministic_context(transition_id="transition-cancel-before-start"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


def test_runner_session_variable_text_is_bounded_by_utf8_bytes() -> None:
    allowed_actor = "é" * 2048
    record = RunnerSessionCancellationRecord(
        request_id="cancel-1",
        session_id="session-1",
        dispatch_generation=1,
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id=allowed_actor,
        requested_at=130,
        request_order=1,
        primary=True,
    )
    assert record.actor_id == allowed_actor

    with pytest.raises(ValueError, match="4096"):
        replace(record, actor_id=allowed_actor + "a")


@pytest.mark.parametrize(
    "field_name",
    ("bounds_summary", "truncation_metadata", "redaction_policy_id"),
)
def test_runner_session_completion_metadata_is_bounded(
    field_name: str,
) -> None:
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id="session-1",
        run_id="run-1",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        terminal_state="interrupted",
        exit_kind="cancelled",
        adapter_outcome_kind=None,
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

    at_limit = replace(completion, **{field_name: "x" * 4096})
    assert len(getattr(at_limit, field_name)) == 4096
    with pytest.raises(ValueError, match="4096"):
        replace(completion, **{field_name: "x" * 4097})


def test_optional_runner_session_text_cannot_be_empty() -> None:
    with pytest.raises(ValueError, match="non-blank"):
        RunnerSessionCompletionRecord(
            completion_id="completion-1",
            session_id="session-1",
            run_id="run-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            terminal_state="interrupted",
            exit_kind="cancelled",
            adapter_outcome_kind="",
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


def test_runner_session_transition_text_is_bounded() -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref

    assert (
        CreateRunnerSession(
            "create-at-limit",
            run_ref=run_ref,
            session_id="x" * 4096,
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ).session_id
        == "x" * 4096
    )
    with pytest.raises(ValueError, match="4096"):
        CreateRunnerSession(
            "create-over-limit",
            run_ref=run_ref,
            session_id="x" * 4097,
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        )


def test_run_current_session_id_is_nonblank_and_bounded() -> None:
    run = _claimed_state().runs["run-taskmaster"]

    with pytest.raises(ValueError, match="non-blank"):
        replace(run, current_session_id="")
    with pytest.raises(ValueError, match="4096"):
        replace(run, current_session_id="x" * 4097)


@pytest.mark.parametrize(
    ("reason", "source_kind"),
    (
        ("operator_cancel_work", "daemon"),
        ("operator_cancel_work", "runtime"),
        ("daemon_shutdown", "operator"),
        ("daemon_shutdown", "runtime"),
        ("runner_timeout", "operator"),
        ("runner_timeout", "daemon"),
        ("runtime_failure", "operator"),
        ("runtime_failure", "daemon"),
    ),
)
def test_cancellation_reason_requires_exact_source(
    reason: str,
    source_kind: str,
) -> None:
    with pytest.raises(ValueError, match="reason and source"):
        RunnerSessionCancellationRecord(
            request_id="cancel-1",
            session_id="session-1",
            dispatch_generation=1,
            reason=reason,
            source_kind=source_kind,
            actor_id="actor-1",
            requested_at=130,
            request_order=1,
            primary=True,
        )


def test_cancellation_attempt_refuses_secondary_request() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="cancellation_requested",
        created_at=100,
        start_intent_at=110,
        started_at=120,
        ended_at=None,
        durable_locator_digest="sha256:" + "a" * 64,
        cleanup_disposition="pending",
    )
    primary = RunnerSessionCancellationRecord(
        request_id="cancel-1",
        session_id=session.session_id,
        dispatch_generation=1,
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id="operator-1",
        requested_at=130,
        request_order=1,
        primary=True,
    )
    secondary = RunnerSessionCancellationRecord(
        request_id="cancel-2",
        session_id=session.session_id,
        dispatch_generation=1,
        reason="daemon_shutdown",
        source_kind="daemon",
        actor_id="daemon-1",
        requested_at=131,
        request_order=2,
        primary=False,
    )
    state = replace(
        state,
        runs={
            **state.runs,
            run.run_ref.run_id: replace(
                run,
                current_session_id=session.session_id,
                last_dispatch_generation=1,
            ),
        },
        runner_sessions={session.session_id: session},
        runner_session_cancellation_requests={
            primary.request_id: primary,
            secondary.request_id: secondary,
        },
    )

    decision = decide(
        state,
        RecordRunnerSessionCancellationAttempt(
            "attempt-secondary",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=1,
            session_fencing_token=session.session_fencing_token,
            expected_state="cancellation_requested",
            attempt_id="attempt-1",
            request_id=secondary.request_id,
            sequence=1,
            operation="cooperative_cancel",
            result="succeeded",
            started_at=135,
            completed_at=140,
            bounded_diagnostic_digest="sha256:" + "b" * 64,
        ),
        deterministic_context(transition_id="transition-attempt-secondary"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


def test_cancellation_decision_refuses_reason_source_mismatch() -> None:
    state = _claimed_state()
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="running",
        created_at=100,
        start_intent_at=110,
        started_at=120,
        ended_at=None,
        durable_locator_digest="sha256:" + "a" * 64,
        cleanup_disposition="pending",
    )
    state = replace(
        state,
        runs={
            **state.runs,
            run.run_ref.run_id: replace(
                run,
                current_session_id=session.session_id,
                last_dispatch_generation=1,
            ),
        },
        runner_sessions={session.session_id: session},
    )

    decision = decide(
        state,
        RequestRunnerSessionCancellation(
            "cancel-mismatch",
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=1,
            session_fencing_token=session.session_fencing_token,
            expected_state="running",
            request_id="cancel-1",
            reason="daemon_shutdown",
            source_kind="operator",
            actor_id="operator-1",
            requested_at=130,
            request_order=1,
            primary=True,
        ),
        deterministic_context(transition_id="transition-cancel-mismatch"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "runner_session_reconciliation_contradiction"


@pytest.mark.parametrize(
    ("input_kind", "field_name"),
    (
        ("request", "reason"),
        ("request", "source_kind"),
        ("attempt", "operation"),
        ("attempt", "result"),
    ),
)
def test_runner_session_transition_refuses_unknown_enum(
    input_kind: str,
    field_name: str,
) -> None:
    state = _claimed_state()
    run_ref = state.runs["run-taskmaster"].run_ref
    if input_kind == "request":
        kwargs = {
            "reason": "operator_cancel_work",
            "source_kind": "operator",
        }
        kwargs[field_name] = "unknown"
        with pytest.raises(ValueError, match="unsupported"):
            RequestRunnerSessionCancellation(
                "cancel-unknown",
                run_ref=run_ref,
                session_id="session-1",
                dispatch_generation=1,
                session_fencing_token="session-fence-1",
                expected_state="running",
                request_id="cancel-1",
                actor_id="operator-1",
                requested_at=130,
                request_order=1,
                primary=True,
                **kwargs,
            )
    else:
        kwargs = {
            "operation": "cooperative_cancel",
            "result": "succeeded",
        }
        kwargs[field_name] = "unknown"
        with pytest.raises(ValueError, match="unsupported"):
            RecordRunnerSessionCancellationAttempt(
                "attempt-unknown",
                run_ref=run_ref,
                session_id="session-1",
                dispatch_generation=1,
                session_fencing_token="session-fence-1",
                expected_state="cancellation_requested",
                attempt_id="attempt-1",
                request_id="cancel-1",
                sequence=1,
                started_at=135,
                completed_at=140,
                bounded_diagnostic_digest="sha256:" + "a" * 64,
                **kwargs,
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


@pytest.mark.parametrize(
    ("command_kind", "field_name", "invalid"),
    (
        ("create", "created_at", "100"),
        ("create", "created_at", 2**63),
        ("create", "explicit_retry_intent", 1),
        ("create", "explicit_retry_intent", "yes"),
        ("advance", "dispatch_generation", 0),
        ("advance", "dispatch_generation", 2**63),
        ("advance", "occurred_at", "110"),
        ("advance", "occurred_at", 2**63),
        ("request", "dispatch_generation", 0),
        ("request", "dispatch_generation", 2**63),
        ("request", "requested_at", "130"),
        ("request", "requested_at", 2**63),
        ("request", "request_order", 0),
        ("request", "request_order", 2**63),
        ("request", "primary", 1),
        ("request", "primary", "yes"),
        ("attempt", "dispatch_generation", 0),
        ("attempt", "dispatch_generation", 2**63),
        ("attempt", "sequence", 0),
        ("attempt", "sequence", 2**63),
        ("attempt", "started_at", "135"),
        ("attempt", "started_at", 2**63),
        ("attempt", "completed_at", "140"),
        ("attempt", "completed_at", 2**63),
    ),
)
def test_runner_session_commands_reject_invalid_scalar_values(
    command_kind: str,
    field_name: str,
    invalid: object,
) -> None:
    run_ref = _claimed_state().runs["run-taskmaster"].run_ref
    commands = {
        "create": CreateRunnerSession(
            "create-session",
            run_ref=run_ref,
            session_id="session-1",
            session_fencing_token="session-fence-1",
            created_at=100,
            explicit_retry_intent=False,
        ),
        "advance": AdvanceRunnerSession(
            "advance-session",
            run_ref=run_ref,
            session_id="session-1",
            dispatch_generation=1,
            session_fencing_token="session-fence-1",
            expected_state="created",
            next_state="starting",
            occurred_at=110,
        ),
        "request": RequestRunnerSessionCancellation(
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
        "attempt": RecordRunnerSessionCancellationAttempt(
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
            bounded_diagnostic_digest="sha256:" + "a" * 64,
        ),
    }

    with pytest.raises(ValueError):
        replace(commands[command_kind], **{field_name: invalid})


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
