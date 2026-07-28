"""Durable runner-session start and completion coordination."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from hashlib import sha256
from typing import cast
from uuid import uuid4

from millrace.adapters.cli.context import (
    OpenRuntimeContext,
    refusal_is_pre_persist,
    transition_context,
)
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterSuccessResult,
    RedactionPolicy,
    RunnerAdapter,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    RunnerSessionHandle,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    runner_cancellation_diagnostic_digest,
    runner_evidence_from_adapter_outcome,
    start_refusal_diagnostic_bytes,
    start_refusal_diagnostic_digest,
)
from millrace.contracts.runner import (
    RunnerDispatchEnvelope,
    RunnerResultEvidence,
    runner_result_evidence_bytes,
    runner_result_evidence_digest,
    runner_result_evidence_from_payload,
    runner_session_locator_bytes,
)
from millrace.contracts.state import (
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
    RefuseRunnerSessionSignal,
    RequestRunnerSessionCancellation,
    RunnerResultObserved,
    TransitionInput,
)
from millrace.kernel import apply, decide
from millrace.operator.dispatch import (
    DispatchProjectionError,
    build_dispatch_envelope_for_run,
)

_COMMAND = "run.session"
_POLL_INTERVAL_SECONDS = 0.01
SESSION_DIAGNOSTIC_MAX_BYTES = 16 * 1024
cooperative_cancel_grace_seconds = 5.0
terminate_grace_seconds = 5.0


@dataclass(frozen=True, slots=True)
class SessionExecutionResult:
    code: str
    accepted: bool = False
    adapter_error_kind: str | None = None
    observation_refusal_reason: str | None = None
    transition_disposition: str | None = None


@dataclass(frozen=True, slots=True)
class SessionCancellationRequestResult:
    code: str
    accepted: bool
    session_id: str | None = None


def execute_runner_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    explicit_retry_intent: bool,
    daemon_stop_requested: Callable[[], bool] | None = None,
    effective_timeout_seconds: float | None = None,
) -> SessionExecutionResult:
    """Start or replay one durable session attempt for the current run."""

    state = _load(runtime)
    run = state.runs.get(run_ref.run_id)
    if run is None or run.run_ref != run_ref:
        return SessionExecutionResult("ready_state_corrupt")

    current = _current_session(state, run.current_session_id)
    if current is not None:
        completion = state.runner_session_completions.get(current.session_id)
        if completion is not None:
            if completion.terminal_state == "completed":
                return _apply_persisted_completion(runtime, completion)
            if not explicit_retry_intent:
                return SessionExecutionResult(
                    "adapter_failure",
                    adapter_error_kind=completion.adapter_error_kind,
                )
        elif current.state == "starting":
            return SessionExecutionResult("session_reconciliation_required")
        elif current.state == "running":
            return SessionExecutionResult("session_running")
        elif current.state in {"cancellation_requested", "terminating"}:
            return SessionExecutionResult("session_running")
        elif current.state == "created":
            return _start_created_session(
                runtime,
                run_ref=run_ref,
                session=current,
                adapter=adapter,
                request_factory=request_factory,
                daemon_stop_requested=daemon_stop_requested,
                effective_timeout_seconds=effective_timeout_seconds,
            )

    session_id = f"session-{uuid4().hex}"
    session_fence = f"session-fence-{uuid4().hex}"
    created_at = _now()
    created = CreateRunnerSession(
        f"cli:run.session-create:{session_id}",
        run_ref=run_ref,
        session_id=session_id,
        session_fencing_token=session_fence,
        created_at=created_at,
        explicit_retry_intent=explicit_retry_intent,
    )
    persisted = _persist_transition(runtime, created)
    if persisted is None:
        return SessionExecutionResult("session_creation_refused")
    session = persisted.runner_sessions[session_id]
    return _start_created_session(
        runtime,
        run_ref=run_ref,
        session=session,
        adapter=adapter,
        request_factory=request_factory,
        daemon_stop_requested=daemon_stop_requested,
        effective_timeout_seconds=effective_timeout_seconds,
    )


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


def session_correlation_id(session: RunnerSessionRecord) -> str:
    return (
        f"cli:run.session:{session.session_id}:"
        f"{session.dispatch_generation}"
    )


def session_cancellation_token(session: RunnerSessionRecord) -> str:
    return (
        f"cli:run.session-cancel:{session.session_id}:"
        f"{session.dispatch_generation}"
    )


def _start_created_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    daemon_stop_requested: Callable[[], bool] | None,
    effective_timeout_seconds: float | None,
) -> SessionExecutionResult:
    if daemon_stop_requested is not None and daemon_stop_requested():
        _request_cancellation(
            runtime,
            run_id=run_ref.run_id,
            request_id=f"daemon:runner-session-cancel:{session.session_id}",
            reason="daemon_shutdown",
            source_kind="daemon",
            actor_id="daemon",
        )
    durable_session = _load(runtime).runner_sessions[session.session_id]
    primary = _primary_cancellation(_load(runtime), durable_session)
    if primary is not None:
        return _cancel_before_external_start(
            runtime,
            run_ref=run_ref,
            session=durable_session,
            primary=primary,
        )
    starting = AdvanceRunnerSession(
        f"cli:run.session-start-intent:{session.session_id}",
        run_ref=run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        next_state="starting",
        occurred_at=max(_now(), session.created_at),
    )
    persisted = _persist_transition(runtime, starting)
    if persisted is None:
        return SessionExecutionResult("session_start_intent_refused")
    session = persisted.runner_sessions[session.session_id]
    request = request_factory(session)
    durable_state = _load(runtime)
    durable_session = durable_state.runner_sessions[session.session_id]
    primary = _primary_cancellation(durable_state, durable_session)
    if primary is not None:
        return _cancel_before_external_start(
            runtime,
            run_ref=run_ref,
            session=durable_session,
            primary=primary,
        )
    if not _request_matches_current_authority(
        runtime,
        session=session,
        adapter=adapter,
        request=request,
    ):
        _audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_authority_mismatch",
            signal_kind="runner_request",
            signal_digest=_signal_digest(request),
        )
        return SessionExecutionResult("session_reconciliation_required")
    if daemon_stop_requested is not None and daemon_stop_requested():
        _request_cancellation(
            runtime,
            run_id=run_ref.run_id,
            request_id=f"daemon:runner-session-cancel:{session.session_id}",
            reason="daemon_shutdown",
            source_kind="daemon",
            actor_id="daemon",
        )
    durable_state = _load(runtime)
    durable_session = durable_state.runner_sessions[session.session_id]
    primary = _primary_cancellation(durable_state, durable_session)
    if primary is not None:
        return _cancel_before_external_start(
            runtime,
            run_ref=run_ref,
            session=durable_session,
            primary=primary,
        )
    deadline = _monotonic() + (
        request.timeout_seconds
        if effective_timeout_seconds is None
        else min(request.timeout_seconds, effective_timeout_seconds)
    )
    try:
        start_outcome = adapter.start_session(request)
    except Exception:
        return SessionExecutionResult("session_reconciliation_required")
    if isinstance(start_outcome, StartIndeterminate):
        try:
            start_outcome.dispatch_echo.validate_against(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
            )
        except (TypeError, ValueError):
            _audit_session_refusal(
                runtime,
                run_ref=run_ref,
                session=session,
                reason="runner_session_authority_mismatch",
                signal_kind="runner_dispatch_echo",
                signal_digest=_signal_digest(start_outcome.dispatch_echo),
            )
            return SessionExecutionResult("session_reconciliation_required")
        locator = start_outcome.durable_locator_metadata
        if locator is not None:
            locator_digest = _safe_locator_digest(runtime, request, locator)
            if locator_digest is None:
                _audit_session_refusal(
                    runtime,
                    run_ref=run_ref,
                    session=session,
                    reason="runner_session_reconciliation_contradiction",
                    signal_kind="runner_session_locator",
                    signal_digest=_signal_digest(locator),
                )
                return SessionExecutionResult("session_reconciliation_required")
            enrichment = AdvanceRunnerSession(
                f"cli:run.session-starting-locator:{session.session_id}",
                run_ref=run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                expected_state="starting",
                next_state="starting",
                occurred_at=cast(int, session.start_intent_at),
                durable_locator_digest=locator_digest,
            )
            _persist_transition(runtime, enrichment)
        return SessionExecutionResult("session_reconciliation_required")
    if isinstance(start_outcome, StartRefusedBeforeExternalWork):
        error_echo = start_outcome.adapter_error.dispatch_echo
        if error_echo is None:
            _audit_session_refusal(
                runtime,
                run_ref=run_ref,
                session=session,
                reason="runner_session_reconciliation_contradiction",
                signal_kind="runner_start_outcome",
                signal_digest=_signal_digest(start_outcome),
            )
            return SessionExecutionResult("session_reconciliation_required")
        try:
            start_outcome.dispatch_echo.validate_against(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
            )
            error_echo.validate_against(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            _audit_session_refusal(
                runtime,
                run_ref=run_ref,
                session=session,
                reason="runner_session_authority_mismatch",
                signal_kind="runner_dispatch_echo",
                signal_digest=_signal_digest((start_outcome.dispatch_echo, error_echo)),
            )
            return SessionExecutionResult("session_reconciliation_required")
        try:
            diagnostic_bytes = start_refusal_diagnostic_bytes(
                start_outcome.adapter_error
            )
            declared_digest = start_refusal_diagnostic_digest(
                start_outcome.adapter_error
            )
        except (TypeError, ValueError):
            _audit_session_refusal(
                runtime,
                run_ref=run_ref,
                session=session,
                reason="runner_session_reconciliation_contradiction",
                signal_kind="runner_start_diagnostic",
                signal_digest=_signal_digest(start_outcome),
            )
            return SessionExecutionResult("session_reconciliation_required")
        if declared_digest != start_outcome.diagnostic_digest:
            _audit_session_refusal(
                runtime,
                run_ref=run_ref,
                session=session,
                reason="runner_session_reconciliation_contradiction",
                signal_kind="runner_start_diagnostic",
                signal_digest=_signal_digest(start_outcome),
            )
            return SessionExecutionResult("session_reconciliation_required")
        stored_digest = runtime.cas_store.put_bytes(diagnostic_bytes)
        if stored_digest != declared_digest:
            _audit_session_refusal(
                runtime,
                run_ref=run_ref,
                session=session,
                reason="runner_session_reconciliation_contradiction",
                signal_kind="runner_start_diagnostic",
                signal_digest=_signal_digest((stored_digest, declared_digest)),
            )
            return SessionExecutionResult("session_reconciliation_required")
        return _persist_adapter_error(
            runtime,
            run_ref=run_ref,
            session=session,
            outcome=start_outcome.adapter_error,
            diagnostic_digest=stored_digest,
            cleanup_disposition="not_required",
        )
    if not isinstance(start_outcome, StartedSession):
        _audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_reconciliation_contradiction",
            signal_kind="runner_start_outcome",
            signal_digest=_signal_digest(start_outcome),
        )
        return SessionExecutionResult("session_reconciliation_required")
    try:
        start_outcome.dispatch_echo.validate_against(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
        )
    except (TypeError, ValueError):
        _audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_authority_mismatch",
            signal_kind="runner_dispatch_echo",
            signal_digest=_signal_digest(start_outcome.dispatch_echo),
        )
        return SessionExecutionResult("session_reconciliation_required")

    locator_digest = _safe_locator_digest(
        runtime,
        request,
        start_outcome.durable_locator_metadata,
    )
    if locator_digest is None:
        _audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_reconciliation_contradiction",
            signal_kind="runner_session_locator",
            signal_digest=_signal_digest(start_outcome.durable_locator_metadata),
        )
        return SessionExecutionResult("session_reconciliation_required")
    running_at = max(_now(), cast(int, session.start_intent_at))
    running = AdvanceRunnerSession(
        f"cli:run.session-running:{session.session_id}",
        run_ref=run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="starting",
        next_state="running",
        occurred_at=running_at,
        durable_locator_digest=locator_digest,
    )
    running_state = _persist_transition(runtime, running)
    if running_state is None:
        return SessionExecutionResult("session_reconciliation_required")
    running_session = running_state.runner_sessions[session.session_id]
    cancellation_started = False
    while True:
        if daemon_stop_requested is not None and daemon_stop_requested():
            _request_cancellation(
                runtime,
                run_id=run_ref.run_id,
                request_id=(
                    "daemon:runner-session-cancel:"
                    f"{running_session.session_id}"
                ),
                reason="daemon_shutdown",
                source_kind="daemon",
                actor_id="daemon",
            )
        primary = _primary_cancellation(_load(runtime), running_session)
        if primary is not None and not cancellation_started:
            cancellation_started = True
            return _cancel_running_session(
                runtime,
                run_ref=run_ref,
                session=running_session,
                request=request,
                handle=start_outcome.handle,
                primary=primary,
            )
        try:
            outcome = start_outcome.handle.poll_completion()
        except Exception as exc:
            _audit_session_refusal(
                runtime,
                run_ref=run_ref,
                session=running_session,
                reason="runner_session_reconciliation_contradiction",
                signal_kind="runner_completion_poll",
                signal_digest=_signal_digest(type(exc).__qualname__),
            )
            _request_cancellation(
                runtime,
                run_id=run_ref.run_id,
                request_id=(
                    "runtime:runner-session-failure:"
                    f"{running_session.session_id}"
                ),
                reason="runtime_failure",
                source_kind="runtime",
                actor_id="runtime",
            )
            primary = _primary_cancellation(_load(runtime), running_session)
            if primary is None:
                return SessionExecutionResult("session_reconciliation_required")
            return _cancel_running_session(
                runtime,
                run_ref=run_ref,
                session=running_session,
                request=request,
                handle=start_outcome.handle,
                primary=primary,
            )
        if outcome is not None:
            if not isinstance(outcome, (AdapterSuccessResult, AdapterErrorResult)):
                _audit_session_refusal(
                    runtime,
                    run_ref=run_ref,
                    session=running_session,
                    reason="runner_session_reconciliation_contradiction",
                    signal_kind="runner_completion_outcome",
                    signal_digest=_signal_digest(outcome),
                )
                return SessionExecutionResult("session_reconciliation_required")
            return _persist_completion(
                runtime,
                run_ref=run_ref,
                session=running_session,
                request=request,
                outcome=outcome,
                cleanup_disposition="not_required",
            )
        remaining = deadline - _monotonic()
        if remaining <= 0:
            _request_cancellation(
                runtime,
                run_id=run_ref.run_id,
                request_id=(
                    "runtime:runner-session-timeout:"
                    f"{running_session.session_id}"
                ),
                reason="runner_timeout",
                source_kind="runtime",
                actor_id="runtime",
            )
            primary = _primary_cancellation(_load(runtime), running_session)
            if primary is None:
                return SessionExecutionResult("session_reconciliation_required")
            return _cancel_running_session(
                runtime,
                run_ref=run_ref,
                session=running_session,
                request=request,
                handle=start_outcome.handle,
                primary=primary,
            )
        _sleep(min(_POLL_INTERVAL_SECONDS, remaining))


def _request_cancellation(
    runtime: OpenRuntimeContext,
    *,
    run_id: str,
    request_id: str,
    reason: str,
    source_kind: str,
    actor_id: str,
) -> SessionCancellationRequestResult:
    state = _load(runtime)
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
        return SessionCancellationRequestResult(
            "runner_session_cancel_refused", False
        )
    session = state.runner_sessions.get(run.current_session_id)
    if (
        session is None
        or session.run_id != run_id
    ):
        return SessionCancellationRequestResult(
            "runner_session_cancel_refused", False
        )
    if (
        session.state in {"completed", "interrupted", "failed", "lost"}
        or session.session_id in state.runner_session_completions
    ):
        _audit_session_refusal(
            runtime,
            run_ref=run.run_ref,
            session=session,
            reason="runner_session_reconciliation_contradiction",
            signal_kind="runner_request",
            signal_digest=_signal_digest(
                {
                    "request_id": request_id,
                    "reason": reason,
                    "source_kind": source_kind,
                }
            ),
            input_id=(
                "cli:run.session-cancel-refusal:"
                f"{session.session_id}:{request_id}"
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
    persisted = _persist_transition(runtime, transition)
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
) -> SessionExecutionResult:
    session = _load(runtime).runner_sessions[session.session_id]
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
        state = _load(runtime)
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
            persisted = _persist_transition(runtime, terminating)
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
    state = _load(runtime)
    session = state.runner_sessions[session.session_id]
    if cleanup.disposition == "orphan_risk":
        diagnostic_digest = runtime.cas_store.put_bytes(
            _canonical_json_bytes(
                {
                    "cleanup_disposition": "orphan_risk",
                    "adapter_outcome_present": outcome is not None,
                }
            )
        )
        completion = _completion_record(
            session=session,
            terminal_state="lost",
            exit_kind="lost",
            adapter_outcome_kind=(
                "error"
                if not isinstance(outcome, AdapterSuccessResult)
                else "success"
            ),
            adapter_error_kind=(
                outcome.error_kind
                if isinstance(outcome, AdapterErrorResult)
                else None
            ),
            evidence_digest=None,
            diagnostic_digest=diagnostic_digest,
            cleanup_disposition="orphan_risk",
            redaction_policy_id=request.redaction_policy.policy_id,
            primary=primary,
        )
        if _persist_completion_record(runtime, run_ref, session, completion) is None:
            return SessionExecutionResult("completion_refused")
        return SessionExecutionResult(
            "runner_session_orphan_risk",
            adapter_error_kind=(
                outcome.error_kind
                if isinstance(outcome, AdapterErrorResult)
                else "cancelled"
            ),
        )
    if outcome is not None:
        if isinstance(outcome, AdapterSuccessResult):
            return _persist_completion(
                runtime,
                run_ref=run_ref,
                session=session,
                request=request,
                outcome=outcome,
                cleanup_disposition=cleanup.disposition,
                primary=primary,
            )
        if isinstance(outcome, AdapterErrorResult):
            diagnostic_digest = runtime.cas_store.put_bytes(
                start_refusal_diagnostic_bytes(outcome)
            )
            return _persist_adapter_error(
                runtime,
                run_ref=run_ref,
                session=session,
                outcome=outcome,
                diagnostic_digest=diagnostic_digest,
                cleanup_disposition=cleanup.disposition,
                terminal_state=(
                    "interrupted"
                    if outcome.error_kind == "cancelled"
                    else "failed"
                ),
                primary=primary,
            )
    terminal_state = (
        "interrupted"
        if cleanup.disposition in {"not_required", "complete"}
        else "lost"
    )
    diagnostic_digest = runtime.cas_store.put_bytes(
        _canonical_json_bytes(
            {
                "cleanup_disposition": cleanup.disposition,
                "outcome": "no_terminal_adapter_outcome",
            }
        )
    )
    completion = _completion_record(
        session=session,
        terminal_state=terminal_state,
        exit_kind="cancelled" if terminal_state == "interrupted" else "lost",
        adapter_outcome_kind="error",
        adapter_error_kind="cancelled",
        evidence_digest=None,
        diagnostic_digest=diagnostic_digest,
        cleanup_disposition=cleanup.disposition,
        redaction_policy_id=request.redaction_policy.policy_id,
        primary=primary,
    )
    if _persist_completion_record(runtime, run_ref, session, completion) is None:
        return SessionExecutionResult("completion_refused")
    return SessionExecutionResult(
        "adapter_failure",
        adapter_error_kind="cancelled",
    )


def _cancel_before_external_start(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    primary: RunnerSessionCancellationRecord,
) -> SessionExecutionResult:
    diagnostic_digest = runtime.cas_store.put_bytes(
        _canonical_json_bytes({"external_start": False})
    )
    completion = _completion_record(
        session=session,
        terminal_state="interrupted",
        exit_kind="cancelled",
        adapter_outcome_kind="error",
        adapter_error_kind="cancelled",
        evidence_digest=None,
        diagnostic_digest=diagnostic_digest,
        cleanup_disposition="not_required",
        redaction_policy_id="runner-session-default",
        primary=primary,
    )
    if _persist_completion_record(runtime, run_ref, session, completion) is None:
        return SessionExecutionResult("completion_refused")
    return SessionExecutionResult(
        "adapter_failure",
        adapter_error_kind="cancelled",
    )


def _wait_for_completion(
    handle: RunnerSessionHandle,
    *,
    seconds: float,
) -> AdapterInvocationOutcome | None:
    deadline = _monotonic() + seconds
    while True:
        outcome = _poll_handle(handle)
        if outcome is not None or _monotonic() >= deadline:
            return outcome
        _sleep(min(_POLL_INTERVAL_SECONDS, deadline - _monotonic()))


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
        _audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_reconciliation_contradiction",
            signal_kind="runner_completion_poll",
            signal_digest=_signal_digest("malformed_runner_completion"),
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
    diagnostic = _bounded_session_diagnostic_bytes(
        operation.diagnostic,
        redaction_policy=redaction_policy,
    )
    digest = runtime.cas_store.put_bytes(diagnostic)
    next_sequence = sequence + 1
    persisted = _persist_transition(
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


def _persist_completion(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: AdapterInvocationOutcome,
    cleanup_disposition: str,
    primary: RunnerSessionCancellationRecord | None = None,
) -> SessionExecutionResult:
    dispatch_echo = outcome.dispatch_echo
    if dispatch_echo is None:
        _audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_reconciliation_contradiction",
            signal_kind="runner_completion_outcome",
            signal_digest=_signal_digest(outcome),
        )
        return SessionExecutionResult("session_reconciliation_required")
    try:
        dispatch_echo.validate_against(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
        )
    except (TypeError, ValueError):
        _audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_authority_mismatch",
            signal_kind="runner_dispatch_echo",
            signal_digest=_signal_digest(dispatch_echo),
        )
        return SessionExecutionResult("session_reconciliation_required")
    if isinstance(outcome, AdapterErrorResult):
        diagnostic_digest = runtime.cas_store.put_bytes(
            start_refusal_diagnostic_bytes(outcome)
        )
        return _persist_adapter_error(
            runtime,
            run_ref=run_ref,
            session=session,
            outcome=outcome,
            diagnostic_digest=diagnostic_digest,
            cleanup_disposition=cleanup_disposition,
        )
    try:
        evidence = runner_evidence_from_adapter_outcome(outcome, request)
    except (TypeError, ValueError):
        _audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_reconciliation_contradiction",
            signal_kind="runner_result_evidence",
            signal_digest=_signal_digest(outcome),
        )
        return SessionExecutionResult("adapter_conversion_refused")
    state = _load(runtime)
    if not _evidence_matches_current_authority(
        state,
        session=session,
        evidence=evidence,
    ):
        _audit_session_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            reason="runner_session_authority_mismatch",
            signal_kind="runner_result_evidence",
            signal_digest=_signal_digest(evidence.payload()),
        )
        return SessionExecutionResult("completion_refused")
    evidence_digest = runtime.cas_store.put_bytes(
        runner_result_evidence_bytes(evidence)
    )
    diagnostic_digest = runtime.cas_store.put_bytes(
        _bounded_session_diagnostic_bytes(
            outcome.evidence_construction_diagnostics,
            redaction_policy=request.redaction_policy,
        )
    )
    completion = _completion_record(
        session=session,
        terminal_state="completed",
        exit_kind="success",
        adapter_outcome_kind="success",
        adapter_error_kind=None,
        evidence_digest=evidence_digest,
        diagnostic_digest=diagnostic_digest,
        cleanup_disposition=cleanup_disposition,
        redaction_policy_id=outcome.redaction_policy_id,
        primary=primary,
    )
    persisted = _persist_completion_record(runtime, run_ref, session, completion)
    if persisted is None:
        return SessionExecutionResult("completion_refused")
    return _apply_persisted_completion(runtime, completion)


def _persist_adapter_error(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    outcome: AdapterErrorResult,
    diagnostic_digest: str,
    cleanup_disposition: str,
    terminal_state: str = "failed",
    primary: RunnerSessionCancellationRecord | None = None,
) -> SessionExecutionResult:
    completion = _completion_record(
        session=session,
        terminal_state=terminal_state,
        exit_kind="cancelled" if terminal_state == "interrupted" else "error",
        adapter_outcome_kind="error",
        adapter_error_kind=outcome.error_kind,
        evidence_digest=None,
        diagnostic_digest=diagnostic_digest,
        cleanup_disposition=cleanup_disposition,
        redaction_policy_id=outcome.redaction_policy_id,
        primary=primary,
    )
    if _persist_completion_record(runtime, run_ref, session, completion) is None:
        return SessionExecutionResult("completion_refused")
    return SessionExecutionResult(
        "adapter_failure",
        adapter_error_kind=outcome.error_kind,
    )


def _completion_record(
    *,
    session: RunnerSessionRecord,
    terminal_state: str,
    exit_kind: str,
    adapter_outcome_kind: str,
    adapter_error_kind: str | None,
    evidence_digest: str | None,
    diagnostic_digest: str,
    cleanup_disposition: str,
    redaction_policy_id: str,
    primary: RunnerSessionCancellationRecord | None = None,
) -> RunnerSessionCompletionRecord:
    completion_id = f"completion-{uuid4().hex}"
    return RunnerSessionCompletionRecord(
        completion_id=completion_id,
        session_id=session.session_id,
        run_id=session.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        terminal_state=terminal_state,
        exit_kind=exit_kind,
        adapter_outcome_kind=adapter_outcome_kind,
        adapter_error_kind=adapter_error_kind,
        runner_result_evidence_digest=evidence_digest,
        primary_cancellation_request_id=(
            None if primary is None else primary.request_id
        ),
        cleanup_disposition=cleanup_disposition,
        started_at=session.started_at,
        cancel_requested_at=(
            None if primary is None else primary.requested_at
        ),
        completed_at=max(_now(), session.started_at or session.created_at),
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id=redaction_policy_id,
        diagnostic_digest=diagnostic_digest,
        application_input_id=f"cli:run.session-completion:{completion_id}",
    )


def _persist_completion_record(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    completion: RunnerSessionCompletionRecord,
) -> RuntimeState | None:
    return _persist_transition(
        runtime,
        RecordRunnerSessionCompletion(
            f"cli:run.session-record-completion:{completion.completion_id}",
            run_ref=run_ref,
            expected_state=session.state,
            completion=completion,
        ),
    )


def _apply_persisted_completion(
    runtime: OpenRuntimeContext,
    completion: RunnerSessionCompletionRecord,
) -> SessionExecutionResult:
    if completion.terminal_state != "completed":
        return SessionExecutionResult(
            "adapter_failure",
            adapter_error_kind=completion.adapter_error_kind,
        )
    state = _load(runtime)
    run = state.runs.get(completion.run_id)
    session = state.runner_sessions.get(completion.session_id)
    if (
        run is None
        or run.current_session_id != completion.session_id
        or session is None
        or session.run_id != completion.run_id
        or session.dispatch_generation != completion.dispatch_generation
        or session.session_fencing_token != completion.session_fencing_token
        or session.state != "completed"
    ):
        return SessionExecutionResult("completion_refused")
    receipt = state.receipts.get(completion.application_input_id)
    if receipt is not None:
        return SessionExecutionResult(
            "observation_accepted",
            accepted=receipt.accepted,
            transition_disposition="replayed",
        )
    digest = completion.runner_result_evidence_digest
    if digest is None:
        return SessionExecutionResult("ready_state_corrupt")
    try:
        evidence = _load_evidence(runtime, digest)
    except (TypeError, ValueError, json.JSONDecodeError):
        return SessionExecutionResult("completion_refused")
    if (
        runner_result_evidence_digest(evidence) != digest
        or not _evidence_matches_current_authority(
            state,
            session=session,
            evidence=evidence,
            completion=completion,
        )
    ):
        return SessionExecutionResult("completion_refused")
    observation = RunnerResultObserved(
        completion.application_input_id,
        run_id=completion.run_id,
        payload=evidence.payload(),
        observed_at=None,
    )
    decision = decide(
        state,
        observation,
        transition_context(
            command=_COMMAND,
            input_id_value=observation.input_id,
        ),
    )
    if not decision.accepted:
        if not refusal_is_pre_persist(decision):
            next_state = apply(state, decision)
            runtime.store.persist_runtime_state(next_state, runtime.cas_store)
        return SessionExecutionResult(
            "observation_refused",
            observation_refusal_reason=(
                "transition_refused"
                if decision.refusal is None
                else decision.refusal.reason
            ),
            transition_disposition=decision.disposition,
        )
    next_state = apply(state, decision)
    runtime.store.persist_runtime_state(next_state, runtime.cas_store)
    return SessionExecutionResult(
        "observation_accepted",
        accepted=True,
        transition_disposition=decision.disposition,
    )


def _load_evidence(
    runtime: OpenRuntimeContext,
    digest: str,
) -> RunnerResultEvidence:
    parsed = json.loads(runtime.cas_store.get_bytes(digest))
    if not isinstance(parsed, dict):
        raise ValueError("runner result evidence CAS object must be a mapping")
    return runner_result_evidence_from_payload(parsed)


def _persist_transition(
    runtime: OpenRuntimeContext,
    transition_input: TransitionInput,
) -> RuntimeState | None:
    state = _load(runtime)
    decision = decide(
        state,
        transition_input,
        transition_context(
            command=_COMMAND,
            input_id_value=transition_input.input_id,
        ),
    )
    if not decision.accepted and refusal_is_pre_persist(decision):
        return None
    next_state = apply(state, decision)
    runtime.store.persist_runtime_state(next_state, runtime.cas_store)
    if not decision.accepted:
        return None
    return next_state


def _request_matches_current_authority(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    adapter: RunnerAdapter,
    request: object,
) -> bool:
    if not isinstance(request, AdapterInvocationRequest):
        return False
    try:
        expected_dispatch = build_dispatch_envelope_for_run(
            state=_load(runtime),
            run_id=session.run_id,
        )
    except (DispatchProjectionError, TypeError, ValueError):
        return False
    return (
        request.dispatch_envelope == expected_dispatch
        and request.session_id == session.session_id
        and request.dispatch_generation == session.dispatch_generation
        and request.session_fencing_token == session.session_fencing_token
        and request.selected_adapter_kind == adapter.adapter_kind
        and request.correlation_id == session_correlation_id(session)
        and request.cancellation_token == session_cancellation_token(session)
    )


def _audit_session_refusal(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    reason: str,
    signal_kind: str,
    signal_digest: str,
    input_id: str | None = None,
) -> None:
    _persist_transition(
        runtime,
        RefuseRunnerSessionSignal(
            input_id
            or (
                "cli:run.session-signal-refusal:"
                f"{session.session_id}:{signal_kind}:"
                f"{signal_digest.removeprefix('sha256:')}"
            ),
            run_ref=run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state=session.state,
            signal_kind=signal_kind,
            reason=reason,
            signal_digest=signal_digest,
        ),
    )


def _safe_locator_digest(
    runtime: OpenRuntimeContext,
    request: AdapterInvocationRequest,
    locator: object,
) -> str | None:
    try:
        redacted = request.redaction_policy.redact_authority_value(locator)
        if not isinstance(redacted, Mapping):
            return None
        payload = runner_session_locator_bytes(redacted)
    except (TypeError, ValueError):
        return None
    return runtime.cas_store.put_bytes(payload)


def _evidence_matches_current_authority(
    state: RuntimeState,
    *,
    session: RunnerSessionRecord,
    evidence: RunnerResultEvidence,
    completion: RunnerSessionCompletionRecord | None = None,
) -> bool:
    run = state.runs.get(session.run_id)
    if (
        run is None
        or run.current_session_id != session.session_id
        or state.runner_sessions.get(session.session_id) != session
    ):
        return False
    try:
        dispatch = build_dispatch_envelope_for_run(
            state=state,
            run_id=session.run_id,
        )
    except (DispatchProjectionError, TypeError, ValueError):
        return False
    if not _evidence_matches_dispatch(evidence, dispatch):
        return False
    if completion is None:
        return True
    return (
        completion.session_id == evidence.session_id
        and completion.run_id == evidence.run_id
        and completion.dispatch_generation == evidence.dispatch_generation
        and completion.session_fencing_token == evidence.session_fencing_token
        and completion.runner_result_evidence_digest
        == runner_result_evidence_digest(evidence)
    )


def _evidence_matches_dispatch(
    evidence: RunnerResultEvidence,
    dispatch: RunnerDispatchEnvelope,
) -> bool:
    return (
        evidence.run_id == dispatch.run_id
        and evidence.session_id == dispatch.session_id
        and evidence.dispatch_generation == dispatch.dispatch_generation
        and evidence.session_fencing_token == dispatch.session_fencing_token
        and evidence.plan_fingerprint == dispatch.plan_fingerprint
        and evidence.claim_id == dispatch.claim_id
        and evidence.generation == dispatch.generation
        and evidence.fencing_token == dispatch.fencing_token
        and evidence.stage_kind_id == dispatch.stage_kind_id
        and evidence.graph_node_id == dispatch.graph_node_id
        and evidence.runner_binding_id == dispatch.runner_binding_id
    )


def _current_session(
    state: RuntimeState,
    session_id: str | None,
) -> RunnerSessionRecord | None:
    if session_id is None:
        return None
    return state.runner_sessions.get(session_id)


def _load(runtime: OpenRuntimeContext) -> RuntimeState:
    return runtime.store.load_runtime_state(runtime.cas_store)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _plain_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bounded_session_diagnostic_bytes(
    value: object,
    *,
    redaction_policy: RedactionPolicy,
) -> bytes:
    try:
        redacted = redaction_policy.redact_authority_value(value)
        payload = _canonical_json_bytes(redacted)
    except Exception:
        return _canonical_json_bytes({"redaction_failed": True})
    if len(payload) <= SESSION_DIAGNOSTIC_MAX_BYTES:
        return payload
    return _canonical_json_bytes(
        {
            "full_diagnostic_digest": f"sha256:{sha256(payload).hexdigest()}",
            "observed_bytes": len(payload),
            "truncated": True,
        }
    )


def _signal_digest(value: object) -> str:
    payload = _canonical_json_bytes(_stable_signal_value(value))
    return f"sha256:{sha256(payload).hexdigest()}"


def _stable_signal_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        return {"float_hex": value.hex()}
    if isinstance(value, Mapping):
        return {
            str(key): _stable_signal_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_stable_signal_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "record_type": f"{type(value).__module__}.{type(value).__qualname__}",
            **{
                item.name: _stable_signal_value(getattr(value, item.name))
                for item in fields(value)
            },
        }
    return {
        "value_type": f"{type(value).__module__}.{type(value).__qualname__}",
    }


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_json_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, tuple):
        return [_plain_json_value(item) for item in value]
    return value


def _now() -> int:
    return time.time_ns()


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


__all__ = (
    "SESSION_DIAGNOSTIC_MAX_BYTES",
    "SessionExecutionResult",
    "execute_runner_session",
    "session_cancellation_token",
    "session_correlation_id",
)
