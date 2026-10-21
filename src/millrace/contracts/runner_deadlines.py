"""Authenticated invocation-deadline cancellation and cleanup provenance."""

from __future__ import annotations

from millrace.contracts.state import RunnerSessionCompletionRecord, RuntimeState
from millrace.contracts.transition import (
    RecordRunnerSessionCancellationAttempt,
    RequestRunnerSessionCancellation,
    input_family,
    input_payload_digest,
)


def runtime_deadline_failure(
    state: RuntimeState, completion: RunnerSessionCompletionRecord
) -> bool:
    """A sole authenticated invocation deadline differs from shutdown/cancel."""
    session = state.runner_sessions.get(completion.session_id)
    run = state.runs.get(completion.run_id)
    requests = [
        request
        for request in state.runner_session_cancellation_requests.values()
        if request.session_id == completion.session_id
    ]
    if (
        session is None
        or run is None
        or len(requests) != 1
        or session.started_at is None
        or completion.terminal_state != "interrupted"
        or completion.exit_kind != "cancelled"
        or completion.adapter_outcome_kind != "error"
        or completion.adapter_error_kind != "cancelled"
        or completion.runner_result_evidence_digest is not None
    ):
        return False
    request = requests[0]
    if (
        request.request_id != completion.primary_cancellation_request_id
        or request.dispatch_generation != completion.dispatch_generation
        or not request.primary
        or request.request_order != 1
        or (request.reason, request.source_kind, request.actor_id)
        != ("runner_timeout", "runtime", "runtime")
        or completion.cancel_requested_at != request.requested_at
        or request.requested_at < session.started_at
    ):
        return False
    attempts = sorted(
        (
            a
            for a in state.runner_session_cancellation_attempts.values()
            if a.session_id == session.session_id
        ),
        key=lambda a: a.sequence,
    )
    if (
        not attempts
        or [a.sequence for a in attempts] != list(range(1, len(attempts) + 1))
        or attempts[0].operation != "cooperative_cancel"
        or attempts[-1].operation != "transport_cleanup"
        or attempts[-1].result != "succeeded"
        or sum(a.operation == "transport_cleanup" for a in attempts) != 1
    ):
        return False
    previous_completed_at = request.requested_at
    for attempt in attempts:
        if (
            state.runner_session_cancellation_attempts.get(attempt.attempt_id)
            != attempt
            or attempt.request_id != request.request_id
            or attempt.attempt_id
            != f"{session.session_id}:cancel-attempt:{attempt.sequence}"
            or attempt.started_at < previous_completed_at
            or attempt.completed_at > completion.completed_at
        ):
            return False
        previous_completed_at = attempt.completed_at
        operation_id = (
            f"cli:run.session-cancel-attempt:{session.session_id}:{attempt.sequence}"
        )
        operation_receipt = state.receipts.get(operation_id)
        operation_transitions = [
            t for t in state.transitions if t.input_id == operation_id
        ]
        commands = [
            RecordRunnerSessionCancellationAttempt(
                operation_id,
                run_ref=run.run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                expected_state=expected_state,
                attempt_id=attempt.attempt_id,
                request_id=request.request_id,
                sequence=attempt.sequence,
                operation=attempt.operation,
                result=attempt.result,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                bounded_diagnostic_digest=attempt.bounded_diagnostic_digest,
            )
            for expected_state in ("cancellation_requested", "terminating")
        ]
        if (
            operation_receipt is None
            or not operation_receipt.accepted
            or operation_receipt.receipt_ref.input_payload_digest
            not in {input_payload_digest(c) for c in commands}
            or len(operation_transitions) != 1
            or not operation_transitions[0].accepted
            or operation_transitions[0].record_id != operation_receipt.transition_id
            or operation_transitions[0].input_kind != commands[0].input_kind
            or operation_transitions[0].input_family != input_family(commands[0])
        ):
            return False
    command = RequestRunnerSessionCancellation(
        request.request_id,
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="running",
        request_id=request.request_id,
        reason=request.reason,
        source_kind=request.source_kind,
        actor_id=request.actor_id,
        requested_at=request.requested_at,
        request_order=request.request_order,
        primary=request.primary,
    )
    receipt = state.receipts.get(command.input_id)
    transitions = [t for t in state.transitions if t.input_id == command.input_id]
    return (
        receipt is not None
        and receipt.accepted
        and receipt.receipt_ref.input_payload_digest == input_payload_digest(command)
        and len(transitions) == 1
        and transitions[0].accepted
        and transitions[0].record_id == receipt.transition_id
        and transitions[0].input_kind == command.input_kind
        and transitions[0].input_family == input_family(command)
    )


def runtime_failure_cleanup_digests(
    state: RuntimeState,
    completion: RunnerSessionCompletionRecord,
) -> tuple[str, ...]:
    """Deadline cleanup evidence is required in addition to terminal strings."""
    if not runtime_deadline_failure(state, completion):
        return ()
    return tuple(
        attempt.bounded_diagnostic_digest
        for attempt in sorted(
            (
                a
                for a in state.runner_session_cancellation_attempts.values()
                if a.session_id == completion.session_id
            ),
            key=lambda a: a.sequence,
        )
    )
