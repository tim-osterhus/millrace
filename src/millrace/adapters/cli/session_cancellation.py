"""Runner-session cancellation, escalation, and owned cleanup."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from millrace.adapters.cli import session_completion as complete
from millrace.adapters.cli.context import (
    OpenRuntimeContext,
)
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterSuccessResult,
    RedactionPolicy,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    RunnerSessionHandle,
    runner_cancellation_diagnostic_digest,
)
from millrace.contracts.state import (
    RunnerSessionCancellationRecord,
    RunnerSessionRecord,
    RunRef,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    RecordRunnerSessionCancellationAttempt,
    RequestRunnerSessionCancellation,
)
from millrace.substrate.errors import StorageIntegrityError

_POLL_INTERVAL_SECONDS = 0.01
cooperative_cancel_grace_seconds = 5.0
terminate_grace_seconds = 5.0


@dataclass(frozen=True, slots=True)
class SessionCancellationRequestResult:
    code: str
    accepted: bool
    session_id: str | None = None


def request_operator_cancellation(
    runtime: OpenRuntimeContext,
    *,
    run_id: str,
    request_id: str,
    actor_id: str,
) -> SessionCancellationRequestResult:
    """Persist the fixed operator cancellation request for the current session."""

    return _request_cancellation(
        runtime,
        run_id=run_id,
        request_id=request_id,
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id=actor_id,
    )


def _emergency_cleanup_live_handle(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    handle: RunnerSessionHandle,
) -> complete.SessionExecutionResult:
    try:
        _request_cancellation(
            runtime,
            run_id=run_ref.run_id,
            request_id=f"runtime:runner-session-failure:{session.session_id}",
            reason="runtime_failure",
            source_kind="runtime",
            actor_id="runtime",
        )
    except Exception:
        pass
    _call_cancellation_operation("cooperative_cancel", handle.request_cancel)
    _call_cancellation_operation("terminate", handle.terminate)
    _call_cancellation_operation("kill", handle.kill)
    cleanup = _call_cleanup(handle.cleanup)
    if cleanup.disposition == "orphan_risk":
        return complete.SessionExecutionResult(
            "runner_session_orphan_risk",
            adapter_error_kind="cancelled",
        )
    return complete.SessionExecutionResult("session_reconciliation_required")


def _request_cancellation(
    runtime: OpenRuntimeContext,
    *,
    run_id: str,
    request_id: str,
    reason: str,
    source_kind: str,
    actor_id: str,
) -> SessionCancellationRequestResult:
    for attempt in range(3):
        try:
            return _request_cancellation_once(
                runtime,
                run_id=run_id,
                request_id=request_id,
                reason=reason,
                source_kind=source_kind,
                actor_id=actor_id,
            )
        except StorageIntegrityError as exc:
            if not str(exc).startswith("stale runtime state ") or attempt == 2:
                raise
    raise AssertionError("bounded cancellation retry exhausted")


def _request_cancellation_once(
    runtime: OpenRuntimeContext,
    *,
    run_id: str,
    request_id: str,
    reason: str,
    source_kind: str,
    actor_id: str,
) -> SessionCancellationRequestResult:
    state = complete._load(runtime)
    existing = state.runner_session_cancellation_requests.get(request_id)
    if existing is not None:
        run = state.runs.get(run_id)
        replayed = (
            run is not None
            and run.current_session_id == existing.session_id
            and existing.reason == reason
            and existing.source_kind == source_kind
            and existing.actor_id == actor_id
        )
        return SessionCancellationRequestResult(
            "runner_session_cancel_requested"
            if replayed
            else "runner_session_cancel_refused",
            replayed,
            existing.session_id if replayed else None,
        )
    run = state.runs.get(run_id)
    if run is None or run.current_session_id is None:
        return SessionCancellationRequestResult("runner_session_cancel_refused", False)
    session = state.runner_sessions.get(run.current_session_id)
    if session is None or session.run_id != run_id:
        return SessionCancellationRequestResult("runner_session_cancel_refused", False)
    if (
        session.state in {"completed", "interrupted", "failed", "lost"}
        or session.session_id in state.runner_session_completions
    ):
        complete._audit_session_refusal(
            runtime,
            run_ref=run.run_ref,
            session=session,
            reason="runner_session_reconciliation_contradiction",
            signal_kind="runner_request",
            signal_digest=complete._signal_digest(
                {
                    "request_id": request_id,
                    "reason": reason,
                    "source_kind": source_kind,
                }
            ),
            input_id=(
                f"cli:run.session-cancel-refusal:{session.session_id}:{request_id}"
            ),
        )
        return SessionCancellationRequestResult(
            "runner_session_cancel_refused", False, session.session_id
        )
    session_requests = tuple(
        item
        for item in state.runner_session_cancellation_requests.values()
        if item.session_id == session.session_id
    )
    requested_at = max(
        _now(),
        session.started_at or session.start_intent_at or session.created_at,
        *(item.requested_at for item in session_requests),
    )
    transition = RequestRunnerSessionCancellation(
        request_id,
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state=session.state,
        request_id=request_id,
        reason=reason,
        source_kind=source_kind,
        actor_id=actor_id,
        requested_at=requested_at,
        request_order=len(session_requests) + 1,
        primary=not session_requests,
    )
    persisted = complete._persist_transition(runtime, transition)
    if persisted is not None:
        complete._record_session_event(
            runtime,
            session=persisted.runner_sessions[session.session_id],
            kind="cancellation_progress",
            observed_at=requested_at,
            payload={
                "state": "cancellation_requested",
                "reason": reason,
                "source_kind": source_kind,
            },
            replay_key=f"cancellation-request:{request_id}",
            redaction_policy=RedactionPolicy(policy_id="runtime-session-events"),
        )
    return SessionCancellationRequestResult(
        "runner_session_cancel_requested"
        if persisted is not None
        else "runner_session_cancel_refused",
        persisted is not None,
        session.session_id if persisted is not None else None,
    )


def _primary_cancellation(
    state: RuntimeState,
    session: RunnerSessionRecord,
) -> RunnerSessionCancellationRecord | None:
    matches = tuple(
        item
        for item in state.runner_session_cancellation_requests.values()
        if item.session_id == session.session_id
        and item.dispatch_generation == session.dispatch_generation
        and item.primary
    )
    return matches[0] if len(matches) == 1 else None


def _cancel_running_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    handle: RunnerSessionHandle,
    primary: RunnerSessionCancellationRecord,
) -> complete.SessionExecutionResult:
    session = complete._load(runtime).runner_sessions[session.session_id]
    sequence = 0
    outcome: AdapterInvocationOutcome | None = None
    malformed_completion = False

    operation = _call_cancellation_operation(
        "cooperative_cancel",
        handle.request_cancel,
    )
    sequence = _persist_cancellation_operation(
        runtime,
        run_ref=run_ref,
        session=session,
        primary=primary,
        sequence=sequence,
        operation=operation,
        redaction_policy=request.redaction_policy,
    )
    if operation.result != "unsupported":
        outcome, malformed_completion = _wait_for_cancellation_completion(
            runtime,
            run_ref=run_ref,
            session=session,
            handle=handle,
            seconds=cooperative_cancel_grace_seconds,
        )

    if outcome is None:
        state = complete._load(runtime)
        current = state.runner_sessions[session.session_id]
        if current.state == "cancellation_requested":
            terminating = AdvanceRunnerSession(
                f"cli:run.session-terminating:{session.session_id}",
                run_ref=run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                expected_state="cancellation_requested",
                next_state="terminating",
                occurred_at=max(_now(), primary.requested_at),
            )
            persisted = complete._persist_transition(runtime, terminating)
            if persisted is not None:
                session = persisted.runner_sessions[session.session_id]
        operation = _call_cancellation_operation(
            "terminate",
            handle.terminate,
        )
        sequence = _persist_cancellation_operation(
            runtime,
            run_ref=run_ref,
            session=session,
            primary=primary,
            sequence=sequence,
            operation=operation,
            redaction_policy=request.redaction_policy,
        )
        phase_outcome, malformed = _wait_for_cancellation_completion(
            runtime,
            run_ref=run_ref,
            session=session,
            handle=handle,
            seconds=terminate_grace_seconds,
        )
        malformed_completion = malformed_completion or malformed
        outcome = None if malformed_completion else phase_outcome

    if outcome is None:
        operation = _call_cancellation_operation("kill", handle.kill)
        sequence = _persist_cancellation_operation(
            runtime,
            run_ref=run_ref,
            session=session,
            primary=primary,
            sequence=sequence,
            operation=operation,
            redaction_policy=request.redaction_policy,
        )
        phase_outcome, malformed = _poll_cancellation_handle(
            runtime,
            run_ref=run_ref,
            session=session,
            handle=handle,
        )
        malformed_completion = malformed_completion or malformed
        outcome = None if malformed_completion else phase_outcome

    cleanup = _call_cleanup(handle.cleanup)
    _persist_cleanup_operation(
        runtime,
        run_ref=run_ref,
        session=session,
        primary=primary,
        sequence=sequence,
        cleanup=cleanup,
        redaction_policy=request.redaction_policy,
    )
    session = complete._load(runtime).runner_sessions[session.session_id]
    return _persist_cancellation_result(
        runtime,
        run_ref=run_ref,
        session=session,
        request=request,
        outcome=outcome,
        cleanup=cleanup,
        primary=primary,
    )


def _persist_cancellation_result(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: AdapterInvocationOutcome | None,
    cleanup: RunnerCleanupResult,
    primary: RunnerSessionCancellationRecord,
) -> complete.SessionExecutionResult:
    if outcome is not None and not complete._adapter_outcome_matches_request(
        outcome,
        request,
    ):
        complete._audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_authority_mismatch",
            signal_kind="runner_completion_outcome",
            signal_digest=complete._signal_digest(outcome),
        )
        return complete.SessionExecutionResult("session_reconciliation_required")
    if cleanup.disposition == "orphan_risk":
        return complete._persist_orphan_risk(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            primary=primary,
            diagnostic={
                "cleanup_disposition": "orphan_risk",
                "adapter_outcome_present": outcome is not None,
            },
            outcome=outcome,
            reported_adapter_error_kind=(
                outcome.error_kind
                if isinstance(outcome, AdapterErrorResult)
                else "cancelled"
            ),
            adapter_outcome_kind=(
                "success" if isinstance(outcome, AdapterSuccessResult) else "error"
            ),
            persistence_failure_code="completion_refused",
        )
    if outcome is not None:
        return complete._persist_completion(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            outcome=outcome,
            cleanup=cleanup,
            primary=primary,
            adapter_error_terminal_state=(
                "interrupted"
                if isinstance(outcome, AdapterErrorResult)
                and outcome.error_kind == "cancelled"
                else "failed"
            ),
        )
    return _persist_cancelled_without_outcome(
        runtime,
        run_ref=run_ref,
        session=session,
        primary=primary,
        cleanup_disposition=cleanup.disposition,
        redaction_policy_id=request.redaction_policy.policy_id,
        diagnostic={
            "cleanup_disposition": cleanup.disposition,
            "outcome": "no_terminal_adapter_outcome",
        },
        event_redaction_policy=request.redaction_policy,
    )


def _cancel_before_external_start(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    primary: RunnerSessionCancellationRecord,
) -> complete.SessionExecutionResult:
    return _persist_cancelled_without_outcome(
        runtime,
        run_ref=run_ref,
        session=session,
        primary=primary,
        cleanup_disposition="not_required",
        redaction_policy_id="runner-session-default",
        diagnostic={"external_start": False},
    )


def _persist_cancelled_without_outcome(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    primary: RunnerSessionCancellationRecord,
    cleanup_disposition: str,
    redaction_policy_id: str,
    diagnostic: dict[str, object],
    event_redaction_policy: RedactionPolicy | None = None,
) -> complete.SessionExecutionResult:
    terminal_state = (
        "interrupted" if cleanup_disposition in {"not_required", "complete"} else "lost"
    )
    record = complete._completion_record(
        session=session,
        terminal_state=terminal_state,
        exit_kind="cancelled" if terminal_state == "interrupted" else "lost",
        adapter_outcome_kind="error",
        adapter_error_kind="cancelled",
        evidence_digest=None,
        diagnostic_digest=runtime.cas_store.put_bytes(
            complete._canonical_json_bytes(diagnostic)
        ),
        cleanup_disposition=cleanup_disposition,
        redaction_policy_id=redaction_policy_id,
        primary=primary,
    )
    if (
        complete._persist_completion_record(
            runtime,
            run_ref,
            session,
            record,
            event_redaction_policy=event_redaction_policy,
        )
        is None
    ):
        return complete.SessionExecutionResult("completion_refused")
    return complete.SessionExecutionResult(
        "adapter_failure",
        adapter_error_kind="cancelled",
    )


def _wait_for_cancellation_completion(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    handle: RunnerSessionHandle,
    seconds: float,
) -> tuple[AdapterInvocationOutcome | None, bool]:
    deadline = _monotonic() + seconds
    while True:
        outcome, malformed = _poll_cancellation_handle(
            runtime,
            run_ref=run_ref,
            session=session,
            handle=handle,
        )
        if malformed or outcome is not None or _monotonic() >= deadline:
            return outcome, malformed
        _sleep(min(_POLL_INTERVAL_SECONDS, deadline - _monotonic()))


def _poll_cancellation_handle(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    handle: RunnerSessionHandle,
) -> tuple[AdapterInvocationOutcome | None, bool]:
    try:
        return _poll_handle(handle), False
    except TypeError:
        complete._audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_reconciliation_contradiction",
            signal_kind="runner_completion_poll",
            signal_digest=complete._signal_digest("malformed_runner_completion"),
        )
        return None, True


def _poll_handle(handle: RunnerSessionHandle) -> AdapterInvocationOutcome | None:
    try:
        outcome = handle.poll_completion()
    except Exception:
        return None
    if outcome is None or isinstance(
        outcome, (AdapterSuccessResult, AdapterErrorResult)
    ):
        return outcome
    raise TypeError("runner session handle returned malformed completion")


def _call_cancellation_operation(
    operation: str,
    call: Callable[[], RunnerCancellationOperationResult],
) -> RunnerCancellationOperationResult:
    try:
        result = call()
        if not isinstance(result, RunnerCancellationOperationResult):
            raise TypeError("invalid cancellation operation result")
        if result.operation != operation:
            raise ValueError("cancellation operation label mismatch")
        return result
    except Exception as exc:
        now = _now()
        diagnostic = {
            "error": type(exc).__qualname__,
            "expected_operation": operation,
        }
        return RunnerCancellationOperationResult(
            operation,
            "failed",
            now,
            now,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


def _call_cleanup(
    call: Callable[[], RunnerCleanupResult],
) -> RunnerCleanupResult:
    try:
        result = call()
        if not isinstance(result, RunnerCleanupResult):
            raise TypeError("invalid cleanup result")
        return result
    except Exception as exc:
        now = _now()
        diagnostic = {"error": type(exc).__qualname__}
        return RunnerCleanupResult(
            "orphan_risk",
            now,
            now,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


def _persist_cancellation_operation(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    primary: RunnerSessionCancellationRecord,
    sequence: int,
    operation: RunnerCancellationOperationResult,
    redaction_policy: RedactionPolicy,
) -> int:
    if not isinstance(operation, RunnerCancellationOperationResult):
        raise TypeError("cancellation operation returned an invalid result")
    started_at = max(primary.requested_at, operation.started_at)
    completed_at = max(started_at, operation.completed_at)
    diagnostic = complete._bounded_session_diagnostic_bytes(
        operation.diagnostic,
        redaction_policy=redaction_policy,
    )
    digest = runtime.cas_store.put_bytes(diagnostic)
    next_sequence = sequence + 1
    persisted = complete._persist_transition(
        runtime,
        RecordRunnerSessionCancellationAttempt(
            f"cli:run.session-cancel-attempt:{session.session_id}:{next_sequence}",
            run_ref=run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state=session.state,
            attempt_id=f"{session.session_id}:cancel-attempt:{next_sequence}",
            request_id=primary.request_id,
            sequence=next_sequence,
            operation=operation.operation,
            result=operation.result,
            started_at=started_at,
            completed_at=completed_at,
            bounded_diagnostic_digest=digest,
        ),
    )
    if persisted is None:
        raise RuntimeError("runner cancellation attempt persistence refused")
    return next_sequence


def _persist_cleanup_operation(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    primary: RunnerSessionCancellationRecord,
    sequence: int,
    cleanup: RunnerCleanupResult,
    redaction_policy: RedactionPolicy,
) -> None:
    if not isinstance(cleanup, RunnerCleanupResult):
        raise TypeError("cleanup returned an invalid result")
    operation = RunnerCancellationOperationResult(
        "transport_cleanup",
        (
            "succeeded"
            if cleanup.disposition in {"not_required", "complete"}
            else "failed"
        ),
        cleanup.started_at,
        cleanup.completed_at,
        cleanup.diagnostic,
        cleanup.diagnostic_digest,
    )
    _persist_cancellation_operation(
        runtime,
        run_ref=run_ref,
        session=session,
        primary=primary,
        sequence=sequence,
        operation=operation,
        redaction_policy=redaction_policy,
    )


def _terminal_cleanup_result(
    cleanup_call: Callable[[], RunnerCleanupResult] | None,
    cleanup_disposition: str | None,
) -> RunnerCleanupResult:
    if cleanup_call is not None:
        return _call_cleanup(cleanup_call)
    if cleanup_disposition not in {"not_required", "complete"}:
        raise ValueError("clean terminal completion requires cleanup proof")
    diagnostic = {"disposition": cleanup_disposition}
    return RunnerCleanupResult(
        cleanup_disposition,
        0,
        0,
        diagnostic,
        runner_cancellation_diagnostic_digest(diagnostic),
    )


def _now() -> int:
    return time.time_ns()


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)
