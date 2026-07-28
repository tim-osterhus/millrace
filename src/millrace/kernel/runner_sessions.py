"""Generic runner-session transition validation."""

from __future__ import annotations

from dataclasses import replace

from millrace.contracts.state import (
    RunnerSessionCancellationAttemptRecord,
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
    RunRef,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    CreateRunnerSession,
    RecordRunnerSessionCancellationAttempt,
    RecordRunnerSessionCompletion,
    RequestRunnerSessionCancellation,
)

RUNNER_SESSION_TRANSITIONS = {
    "created": frozenset({"starting", "cancellation_requested", "failed"}),
    "starting": frozenset(
        {"running", "completed", "failed", "cancellation_requested", "lost"}
    ),
    "running": frozenset(
        {"completed", "failed", "cancellation_requested", "lost"}
    ),
    "cancellation_requested": frozenset(
        {"terminating", "completed", "interrupted", "failed", "lost"}
    ),
    "terminating": frozenset({"completed", "interrupted", "failed", "lost"}),
    "completed": frozenset(),
    "interrupted": frozenset(),
    "failed": frozenset(),
    "lost": frozenset(),
}


def is_legal_runner_session_transition(prior: str, next_state: str) -> bool:
    return next_state in RUNNER_SESSION_TRANSITIONS.get(prior, ())


def create_runner_session_refusal(
    state: RuntimeState,
    transition_input: CreateRunnerSession,
) -> str | None:
    run = state.runs.get(transition_input.run_ref.run_id)
    if run is None or run.run_ref != transition_input.run_ref:
        return "runner_session_authority_mismatch"
    if transition_input.session_id in state.runner_sessions:
        return "stale_runner_session"
    if any(
        session.run_id == run.run_ref.run_id
        and session.session_fencing_token
        == transition_input.session_fencing_token
        for session in state.runner_sessions.values()
    ):
        return "runner_session_reconciliation_contradiction"
    if run.current_session_id is None:
        if run.last_dispatch_generation != 0:
            return "runner_session_reconciliation_contradiction"
        return None
    prior = state.runner_sessions.get(run.current_session_id)
    if prior is None or prior.run_id != run.run_ref.run_id:
        return "runner_session_reconciliation_contradiction"
    if not transition_input.explicit_retry_intent:
        return "runner_session_retry_forbidden"
    if any(
        observation.run_id == run.run_ref.run_id
        for observation in state.runner_observations.values()
    ):
        return "runner_session_retry_forbidden"
    if prior.state not in {"failed", "interrupted"}:
        return "runner_session_retry_forbidden"
    if prior.cleanup_disposition not in {"not_required", "complete"}:
        return "runner_session_cleanup_incomplete"
    return None


def runner_session_for_creation(
    state: RuntimeState,
    transition_input: CreateRunnerSession,
) -> RunnerSessionRecord:
    run = state.runs[transition_input.run_ref.run_id]
    return RunnerSessionRecord(
        session_id=transition_input.session_id,
        run_id=run.run_ref.run_id,
        dispatch_generation=run.last_dispatch_generation + 1,
        session_fencing_token=transition_input.session_fencing_token,
        state="created",
        created_at=transition_input.created_at,
        start_intent_at=None,
        started_at=None,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )


def session_authority_refusal(
    state: RuntimeState,
    *,
    run_ref: RunRef,
    session_id: str,
    dispatch_generation: int,
    session_fencing_token: str,
    expected_state: str,
) -> str | None:
    run_id = run_ref.run_id
    run = state.runs.get(run_id)
    if run is None or run.run_ref != run_ref:
        return "runner_session_authority_mismatch"
    if run.current_session_id != session_id:
        return "stale_runner_session"
    session = state.runner_sessions.get(session_id)
    if (
        session is None
        or session.run_id != run_id
        or session.dispatch_generation != dispatch_generation
        or session.session_fencing_token != session_fencing_token
        or session.state != expected_state
    ):
        return "stale_runner_session"
    return None


def advance_runner_session_refusal(
    state: RuntimeState,
    transition_input: AdvanceRunnerSession,
) -> str | None:
    refusal = session_authority_refusal(
        state,
        run_ref=transition_input.run_ref,
        session_id=transition_input.session_id,
        dispatch_generation=transition_input.dispatch_generation,
        session_fencing_token=transition_input.session_fencing_token,
        expected_state=transition_input.expected_state,
    )
    if refusal is not None:
        return refusal
    session = state.runner_sessions[transition_input.session_id]
    starting_locator_enrichment = (
        transition_input.expected_state == "starting"
        and transition_input.next_state == "starting"
        and transition_input.durable_locator_digest is not None
        and session.durable_locator_digest is None
        and transition_input.occurred_at == session.start_intent_at
    )
    if (
        not starting_locator_enrichment
        and (
            not is_legal_runner_session_transition(
                transition_input.expected_state,
                transition_input.next_state,
            )
            or transition_input.next_state
            in {"completed", "interrupted", "failed", "lost"}
        )
    ):
        return "invalid_runner_session_transition"
    if transition_input.durable_locator_digest is not None and (
        not starting_locator_enrichment
        and (
            transition_input.expected_state != "starting"
            or transition_input.next_state != "running"
        )
    ):
        return "invalid_runner_session_transition"
    latest = max(
        value
        for value in (
            session.created_at,
            session.start_intent_at,
            session.started_at,
        )
        if value is not None
    )
    if transition_input.occurred_at < latest:
        return "runner_session_reconciliation_contradiction"
    return None


def runner_session_for_advance(
    state: RuntimeState,
    transition_input: AdvanceRunnerSession,
) -> RunnerSessionRecord:
    session = state.runner_sessions[transition_input.session_id]
    if (
        transition_input.expected_state == "starting"
        and transition_input.next_state == "starting"
    ):
        return replace(
            session,
            durable_locator_digest=transition_input.durable_locator_digest,
        )
    if transition_input.next_state == "starting":
        return replace(
            session,
            state=transition_input.next_state,
            start_intent_at=transition_input.occurred_at,
        )
    if transition_input.next_state == "running":
        return replace(
            session,
            state=transition_input.next_state,
            started_at=transition_input.occurred_at,
            durable_locator_digest=transition_input.durable_locator_digest,
        )
    return replace(session, state=transition_input.next_state)


def cancellation_record(
    transition_input: RequestRunnerSessionCancellation,
) -> RunnerSessionCancellationRecord:
    return RunnerSessionCancellationRecord(
        request_id=transition_input.request_id,
        session_id=transition_input.session_id,
        dispatch_generation=transition_input.dispatch_generation,
        reason=transition_input.reason,
        source_kind=transition_input.source_kind,
        actor_id=transition_input.actor_id,
        requested_at=transition_input.requested_at,
        request_order=transition_input.request_order,
        primary=transition_input.primary,
    )


def cancellation_request_refusal(
    state: RuntimeState,
    transition_input: RequestRunnerSessionCancellation,
) -> str | None:
    refusal = session_authority_refusal(
        state,
        run_ref=transition_input.run_ref,
        session_id=transition_input.session_id,
        dispatch_generation=transition_input.dispatch_generation,
        session_fencing_token=transition_input.session_fencing_token,
        expected_state=transition_input.expected_state,
    )
    if refusal is not None:
        return refusal
    if transition_input.request_id in state.runner_session_cancellation_requests:
        return "runner_session_reconciliation_contradiction"
    if transition_input.expected_state != "cancellation_requested" and (
        not is_legal_runner_session_transition(
            transition_input.expected_state,
            "cancellation_requested",
        )
    ):
        return "invalid_runner_session_transition"
    requests = tuple(
        request
        for request in state.runner_session_cancellation_requests.values()
        if request.session_id == transition_input.session_id
    )
    expected_order = len(requests) + 1
    if transition_input.request_order != expected_order:
        return "runner_session_reconciliation_contradiction"
    if transition_input.primary != (expected_order == 1):
        return "runner_session_reconciliation_contradiction"
    if requests and transition_input.requested_at < requests[-1].requested_at:
        return "runner_session_reconciliation_contradiction"
    session = state.runner_sessions[transition_input.session_id]
    latest_phase_at = max(
        timestamp
        for timestamp in (
            session.created_at,
            session.start_intent_at,
            session.started_at,
        )
        if timestamp is not None
    )
    if transition_input.requested_at < latest_phase_at:
        return "runner_session_reconciliation_contradiction"
    try:
        cancellation_record(transition_input)
    except ValueError:
        return "runner_session_reconciliation_contradiction"
    return None


def session_for_cancellation_request(
    state: RuntimeState,
    transition_input: RequestRunnerSessionCancellation,
) -> RunnerSessionRecord:
    session = state.runner_sessions[transition_input.session_id]
    if session.state == "cancellation_requested":
        return session
    return replace(session, state="cancellation_requested")


def cancellation_attempt_record(
    transition_input: RecordRunnerSessionCancellationAttempt,
) -> RunnerSessionCancellationAttemptRecord:
    return RunnerSessionCancellationAttemptRecord(
        attempt_id=transition_input.attempt_id,
        session_id=transition_input.session_id,
        request_id=transition_input.request_id,
        sequence=transition_input.sequence,
        operation=transition_input.operation,
        result=transition_input.result,
        started_at=transition_input.started_at,
        completed_at=transition_input.completed_at,
        bounded_diagnostic_digest=transition_input.bounded_diagnostic_digest,
    )


def cancellation_attempt_refusal(
    state: RuntimeState,
    transition_input: RecordRunnerSessionCancellationAttempt,
) -> str | None:
    refusal = session_authority_refusal(
        state,
        run_ref=transition_input.run_ref,
        session_id=transition_input.session_id,
        dispatch_generation=transition_input.dispatch_generation,
        session_fencing_token=transition_input.session_fencing_token,
        expected_state=transition_input.expected_state,
    )
    if refusal is not None:
        return refusal
    if transition_input.expected_state not in {
        "cancellation_requested",
        "terminating",
    }:
        return "invalid_runner_session_transition"
    request = state.runner_session_cancellation_requests.get(
        transition_input.request_id
    )
    if (
        request is None
        or request.session_id != transition_input.session_id
        or not request.primary
    ):
        return "runner_session_reconciliation_contradiction"
    attempts = tuple(
        attempt
        for attempt in state.runner_session_cancellation_attempts.values()
        if attempt.session_id == transition_input.session_id
    )
    if (
        transition_input.attempt_id in state.runner_session_cancellation_attempts
        or transition_input.sequence != len(attempts) + 1
        or transition_input.started_at < request.requested_at
    ):
        return "runner_session_reconciliation_contradiction"
    try:
        cancellation_attempt_record(transition_input)
    except ValueError:
        return "runner_session_reconciliation_contradiction"
    return None


def completion_refusal(
    state: RuntimeState,
    transition_input: RecordRunnerSessionCompletion,
) -> str | None:
    completion = transition_input.completion
    run = state.runs.get(transition_input.run_ref.run_id)
    if (
        run is not None
        and run.run_ref == transition_input.run_ref
        and run.current_session_id == completion.session_id
        and completion.session_id in state.runner_session_completions
    ):
        return "duplicate_runner_session_completion"
    refusal = session_authority_refusal(
        state,
        run_ref=transition_input.run_ref,
        session_id=completion.session_id,
        dispatch_generation=completion.dispatch_generation,
        session_fencing_token=completion.session_fencing_token,
        expected_state=transition_input.expected_state,
    )
    if refusal is not None:
        return refusal
    if completion.run_id != transition_input.run_ref.run_id:
        return "runner_session_authority_mismatch"
    if any(
        record.application_input_id == completion.application_input_id
        for record in state.runner_session_completions.values()
    ):
        return "duplicate_runner_session_completion"
    if not is_legal_runner_session_transition(
        transition_input.expected_state,
        completion.terminal_state,
    ):
        return "invalid_runner_session_transition"
    session = state.runner_sessions[completion.session_id]
    if session.started_at is None:
        if completion.started_at is None:
            if completion.terminal_state == "completed":
                return "runner_session_reconciliation_contradiction"
        elif (
            session.start_intent_at is None
            or completion.started_at < session.start_intent_at
        ):
            return "runner_session_reconciliation_contradiction"
    elif session.started_at != completion.started_at:
        return "runner_session_reconciliation_contradiction"
    latest_session_fact_at = max(
        (
            session.created_at,
            *(
                timestamp
                for timestamp in (session.start_intent_at, session.started_at)
                if timestamp is not None
            ),
            *(
                request.requested_at
                for request in state.runner_session_cancellation_requests.values()
                if request.session_id == completion.session_id
            ),
            *(
                timestamp
                for attempt in state.runner_session_cancellation_attempts.values()
                if attempt.session_id == completion.session_id
                for timestamp in (attempt.started_at, attempt.completed_at)
                if timestamp is not None
            ),
        )
    )
    if completion.completed_at < latest_session_fact_at:
        return "runner_session_reconciliation_contradiction"
    primary_request_id = completion.primary_cancellation_request_id
    cancellation_requests = tuple(
        request
        for request in state.runner_session_cancellation_requests.values()
        if request.session_id == completion.session_id
    )
    has_cancellation_history = bool(cancellation_requests) or any(
        attempt.session_id == completion.session_id
        for attempt in state.runner_session_cancellation_attempts.values()
    )
    if completion.terminal_state == "interrupted" and has_cancellation_history:
        if not cancellation_requests:
            return "runner_session_reconciliation_contradiction"
        primary_request = min(
            cancellation_requests,
            key=lambda request: request.request_order,
        )
        if (
            not primary_request.primary
            or primary_request_id != primary_request.request_id
            or completion.cancel_requested_at != primary_request.requested_at
        ):
            return "runner_session_reconciliation_contradiction"
    if primary_request_id is None:
        if completion.cancel_requested_at is not None:
            return "runner_session_reconciliation_contradiction"
    else:
        request = state.runner_session_cancellation_requests.get(primary_request_id)
        if (
            request is None
            or request.session_id != completion.session_id
            or not request.primary
            or completion.cancel_requested_at != request.requested_at
        ):
            return "runner_session_reconciliation_contradiction"
    return None


def session_for_completion(
    state: RuntimeState,
    completion: RunnerSessionCompletionRecord,
) -> RunnerSessionRecord:
    return replace(
        state.runner_sessions[completion.session_id],
        state=completion.terminal_state,
        started_at=completion.started_at,
        ended_at=completion.completed_at,
        cleanup_disposition=completion.cleanup_disposition,
    )


__all__ = (
    "advance_runner_session_refusal",
    "cancellation_attempt_record",
    "cancellation_attempt_refusal",
    "cancellation_record",
    "cancellation_request_refusal",
    "completion_refusal",
    "create_runner_session_refusal",
    "is_legal_runner_session_transition",
    "runner_session_for_advance",
    "runner_session_for_creation",
    "session_for_cancellation_request",
    "session_for_completion",
)
