"""Owned session failure supervision and cooperative cancellation phases."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from millrace.adapters.cli import session_cancellation as cancel
from millrace.adapters.cli import session_completion as complete
from millrace.adapters.cli import session_reconciliation as reconcile
from millrace.adapters.cli.context import OpenRuntimeContext
from millrace.adapters.runner_contract import (
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    RunnerSessionHandle,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    adapter_error_diagnostic_bytes,
)
from millrace.contracts.state import (
    RunnerSessionCancellationRecord,
    RunnerSessionRecord,
    RunRef,
)
from millrace.contracts.transition import AdvanceRunnerSession

SessionExecutionResult = complete.SessionExecutionResult


def _start_refusal(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    reason: str,
    signal_kind: str,
    signal: object,
) -> SessionExecutionResult:
    complete._audit_session_refusal(
        runtime,
        run_ref=run_ref,
        session=session,
        reason=reason,
        signal_kind=signal_kind,
        signal_digest=complete._signal_digest(signal),
    )
    return SessionExecutionResult("session_reconciliation_required")


def _persist_indeterminate_start(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: StartIndeterminate,
) -> SessionExecutionResult:
    try:
        outcome.dispatch_echo.validate_against(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
    except (TypeError, ValueError):
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_authority_mismatch",
            "runner_dispatch_echo",
            outcome.dispatch_echo,
        )
    locator = outcome.durable_locator_metadata
    if locator is None:
        return SessionExecutionResult("session_reconciliation_required")
    locator_digest = reconcile._safe_coordinator_locator_digest(
        runtime,
        request,
        handle_id=None,
        adapter_locator=locator,
    )
    if locator_digest is None:
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_reconciliation_contradiction",
            "runner_session_locator",
            locator,
        )
    complete._persist_transition(
        runtime,
        AdvanceRunnerSession(
            f"cli:run.session-starting-locator:{session.session_id}",
            run_ref=run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state="starting",
            next_state="starting",
            occurred_at=cast(int, session.start_intent_at),
            durable_locator_digest=locator_digest,
        ),
    )
    return SessionExecutionResult("session_reconciliation_required")


def _persist_refused_start(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: StartRefusedBeforeExternalWork,
) -> SessionExecutionResult:
    error_echo = outcome.adapter_error.dispatch_echo
    if error_echo is None:
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_reconciliation_contradiction",
            "runner_start_outcome",
            outcome,
        )
    try:
        outcome.dispatch_echo.validate_against(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        error_echo.validate_against(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
    except (AttributeError, TypeError, ValueError):
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_authority_mismatch",
            "runner_dispatch_echo",
            (outcome.dispatch_echo, error_echo),
        )
    try:
        diagnostic_bytes = adapter_error_diagnostic_bytes(
            outcome.adapter_error,
            request=request,
        )
    except (TypeError, ValueError):
        diagnostic_bytes = None
    if diagnostic_bytes is None:
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_reconciliation_contradiction",
            "runner_start_diagnostic",
            outcome,
        )
    declared_digest = f"sha256:{sha256(diagnostic_bytes).hexdigest()}"
    if declared_digest != outcome.diagnostic_digest:
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_reconciliation_contradiction",
            "runner_start_diagnostic",
            outcome,
        )
    stored_digest = runtime.cas_store.put_bytes(diagnostic_bytes)
    if stored_digest != declared_digest:
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_reconciliation_contradiction",
            "runner_start_diagnostic",
            (stored_digest, declared_digest),
        )
    return complete._persist_adapter_error(
        runtime,
        run_ref=run_ref,
        session=session,
        outcome=outcome.adapter_error,
        diagnostic_digest=stored_digest,
        cleanup_disposition="not_required",
        redaction_policy=request.redaction_policy,
    )


def _recover_after_running_persistence_failure(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    handle: RunnerSessionHandle,
) -> SessionExecutionResult:
    request_id = f"runtime:runner-session-failure:{session.session_id}"
    try:
        cancel._request_cancellation(
            runtime,
            run_id=run_ref.run_id,
            request_id=request_id,
            reason="runtime_failure",
            source_kind="runtime",
            actor_id="runtime",
        )
        state = complete._load(runtime)
        current = state.runner_sessions[session.session_id]
        primary = cancel._primary_cancellation(state, current)
        if primary is not None:
            return cancel._cancel_running_session(
                runtime,
                run_ref=run_ref,
                session=current,
                request=request,
                handle=handle,
                primary=primary,
            )
    except Exception:
        pass
    return cancel._emergency_cleanup_live_handle(
        runtime,
        run_ref=run_ref,
        session=session,
        handle=handle,
    )


@dataclass(slots=True)
class _CancellationCursor:
    """One owned cancellation; grace clocks survive cooperative service turns."""

    primary: RunnerSessionCancellationRecord
    phase: int = 0
    sequence: int = 0
    deadline: float = 0.0
    operation: RunnerCancellationOperationResult | None = None
    outcome: AdapterInvocationOutcome | None = None
    malformed: bool = False
    cleanup: RunnerCleanupResult | None = None
    cleanup_recorded: bool = False


def _step_cancellation(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    handle: RunnerSessionHandle,
    cursor: _CancellationCursor,
) -> complete.SessionExecutionResult | None:
    """Perform one cancellation phase/poll without waiting for its grace period."""
    session = complete._load(runtime).runner_sessions[session.session_id]
    if cursor.phase < 3 and cursor.outcome is None:
        if cursor.operation is None:
            session = _begin_cancellation_phase(
                runtime, run_ref, session, handle, cursor
            )
        assert cursor.operation is not None
        if cursor.sequence == cursor.phase:
            cursor.sequence = cancel._persist_cancellation_operation(
                runtime,
                run_ref=run_ref,
                session=session,
                primary=cursor.primary,
                sequence=cursor.sequence,
                operation=cursor.operation,
                redaction_policy=request.redaction_policy,
            )
        if not cursor.malformed:
            cursor.outcome, cursor.malformed = cancel._poll_cancellation_handle(
                runtime,
                run_ref=run_ref,
                session=session,
                handle=handle,
            )
        if cursor.outcome is None:
            if not cursor.malformed and cancel._monotonic() < cursor.deadline:
                return None
            cursor.phase += 1
            cursor.operation = None
            if cursor.phase < 3:
                return None
    if cursor.cleanup is None:
        cursor.cleanup = cancel._call_cleanup(handle.cleanup)
    if not cursor.cleanup_recorded:
        cancel._persist_cleanup_operation(
            runtime,
            run_ref=run_ref,
            session=session,
            primary=cursor.primary,
            sequence=cursor.sequence,
            cleanup=cursor.cleanup,
            redaction_policy=request.redaction_policy,
        )
        cursor.cleanup_recorded = True
    session = complete._load(runtime).runner_sessions[session.session_id]
    return cancel._persist_cancellation_result(
        runtime,
        run_ref=run_ref,
        session=session,
        request=request,
        outcome=cursor.outcome,
        cleanup=cursor.cleanup,
        primary=cursor.primary,
    )


def _begin_cancellation_phase(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    handle: RunnerSessionHandle,
    cursor: _CancellationCursor,
) -> RunnerSessionRecord:
    if cursor.phase == 1 and session.state == "cancellation_requested":
        persisted = complete._persist_transition(
            runtime,
            AdvanceRunnerSession(
                f"cli:run.session-terminating:{session.session_id}",
                run_ref=run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                expected_state=session.state,
                next_state="terminating",
                occurred_at=max(cancel._now(), cursor.primary.requested_at),
            ),
        )
        if persisted is None:
            raise RuntimeError("runner termination persistence refused")
        session = persisted.runner_sessions[session.session_id]
    name, call, grace = (
        (
            "cooperative_cancel",
            handle.request_cancel,
            cancel.cooperative_cancel_grace_seconds,
        ),
        ("terminate", handle.terminate, cancel.terminate_grace_seconds),
        ("kill", handle.kill, 0.0),
    )[cursor.phase]
    cursor.operation = cancel._call_cancellation_operation(name, call)
    cursor.deadline = cancel._monotonic() + grace
    if cursor.phase == 0 and cursor.operation.result == "unsupported":
        cursor.deadline = cancel._monotonic()
    return session
