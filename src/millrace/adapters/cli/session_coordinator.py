"""Durable runner-session orchestration facade."""

from __future__ import annotations

import time
from collections.abc import Callable
from hashlib import sha256
from math import isfinite
from typing import cast
from uuid import uuid4

from millrace.adapters.cli import session_cancellation as cancel
from millrace.adapters.cli import session_completion as complete
from millrace.adapters.cli import session_reconciliation as reconcile
from millrace.adapters.cli.context import (
    OpenRuntimeContext,
)
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationRequest,
    AdapterSuccessResult,
    RunnerAdapter,
    RunnerSessionHandle,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    runner_evidence_from_adapter_outcome,
)
from millrace.contracts.state import (
    RunnerSessionRecord,
    RunRef,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    CreateRunnerSession,
)

SESSION_DIAGNOSTIC_MAX_BYTES = complete.SESSION_DIAGNOSTIC_MAX_BYTES
SessionCancellationRequestResult = cancel.SessionCancellationRequestResult
SessionExecutionResult = complete.SessionExecutionResult
cooperative_cancel_grace_seconds = cancel.cooperative_cancel_grace_seconds
request_operator_cancellation = cancel.request_operator_cancellation
session_cancellation_token = reconcile.session_cancellation_token
session_correlation_id = reconcile.session_correlation_id
terminate_grace_seconds = cancel.terminate_grace_seconds

_POLL_INTERVAL_SECONDS = 0.01


def execute_runner_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    explicit_retry_intent: bool,
    on_start_reserved: Callable[[RunnerSessionRecord], None] | None = None,
    on_accepted_start: Callable[[RunnerSessionRecord], None] | None = None,
    daemon_stop_requested: Callable[[], bool] | None = None,
    effective_timeout_seconds: float | None = None,
) -> SessionExecutionResult:
    """Start or replay one durable session attempt for the current run."""

    complete.runner_evidence_from_adapter_outcome = runner_evidence_from_adapter_outcome
    _validate_effective_timeout(effective_timeout_seconds)
    state = complete._load(runtime)
    run = state.runs.get(run_ref.run_id)
    if run is None or run.run_ref != run_ref:
        return SessionExecutionResult("ready_state_corrupt")
    current = _current_session(state, run.current_session_id)
    resumed = _resume_current_session(
        runtime,
        run_ref,
        current,
        adapter,
        request_factory,
        explicit_retry_intent,
        on_start_reserved,
        on_accepted_start,
        daemon_stop_requested,
        effective_timeout_seconds,
    )
    if resumed is not None:
        return resumed
    session_id = f"session-{uuid4().hex}"
    persisted = complete._persist_transition(
        runtime,
        CreateRunnerSession(
            f"cli:run.session-create:{session_id}",
            run_ref=run_ref,
            session_id=session_id,
            session_fencing_token=f"session-fence-{uuid4().hex}",
            created_at=_now(),
            explicit_retry_intent=explicit_retry_intent,
        ),
    )
    if persisted is None:
        code = "session_creation_refused"
        if current is not None and explicit_retry_intent:
            code = "runner_session_retry_refused"
        return SessionExecutionResult(code)
    return _start_created_session(
        runtime,
        run_ref=run_ref,
        session=persisted.runner_sessions[session_id],
        adapter=adapter,
        request_factory=request_factory,
        on_start_reserved=on_start_reserved,
        on_accepted_start=on_accepted_start,
        daemon_stop_requested=daemon_stop_requested,
        effective_timeout_seconds=effective_timeout_seconds,
    )


def _validate_effective_timeout(effective_timeout_seconds: float | None) -> None:
    if effective_timeout_seconds is None:
        return
    if type(effective_timeout_seconds) not in {int, float}:
        raise TypeError("effective_timeout_seconds must be a number")
    if effective_timeout_seconds <= 0 or not isfinite(float(effective_timeout_seconds)):
        raise ValueError("effective_timeout_seconds must be finite and positive")


def _resume_current_session(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    current: RunnerSessionRecord | None,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    explicit_retry_intent: bool,
    on_start_reserved: Callable[[RunnerSessionRecord], None] | None,
    on_accepted_start: Callable[[RunnerSessionRecord], None] | None,
    daemon_stop_requested: Callable[[], bool] | None,
    effective_timeout_seconds: float | None,
) -> SessionExecutionResult | None:
    if current is None:
        return None
    state = complete._load(runtime)
    stored_completion = state.runner_session_completions.get(current.session_id)
    if stored_completion is not None:
        if stored_completion.terminal_state == "completed":
            return complete._apply_persisted_completion(runtime, stored_completion)
        if stored_completion.terminal_state == "lost":
            return SessionExecutionResult("runner_session_orphan_risk")
        if not explicit_retry_intent:
            return SessionExecutionResult(
                "adapter_failure",
                adapter_error_kind=stored_completion.adapter_error_kind,
            )
        return None
    if current.state in {
        "starting",
        "running",
        "cancellation_requested",
        "terminating",
    }:
        reconciled = reconcile._reconcile_session(
            runtime,
            run_ref=run_ref,
            session=current,
            adapter=adapter,
            request_factory=request_factory,
            effective_timeout_seconds=effective_timeout_seconds,
        )
        if isinstance(reconciled, SessionExecutionResult):
            return reconciled
        return _drive_owned_live_handle(
            runtime,
            run_ref=run_ref,
            session=reconciled.session,
            request=reconciled.request,
            handle=reconciled.handle,
            deadline=reconciled.deadline,
            daemon_stop_requested=daemon_stop_requested,
        )
    if current.state == "lost":
        return SessionExecutionResult("runner_session_orphan_risk")
    if current.state == "created":
        return _start_created_session(
            runtime,
            run_ref=run_ref,
            session=current,
            adapter=adapter,
            request_factory=request_factory,
            on_start_reserved=on_start_reserved,
            on_accepted_start=on_accepted_start,
            daemon_stop_requested=daemon_stop_requested,
            effective_timeout_seconds=effective_timeout_seconds,
        )
    return None


def _start_created_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    on_start_reserved: Callable[[RunnerSessionRecord], None] | None,
    on_accepted_start: Callable[[RunnerSessionRecord], None] | None,
    daemon_stop_requested: Callable[[], bool] | None,
    effective_timeout_seconds: float | None,
) -> SessionExecutionResult:
    durable_session, cancellation = _pre_start_cancellation(
        runtime,
        run_ref,
        session,
        daemon_stop_requested=daemon_stop_requested,
    )
    if cancellation is not None:
        return cancellation
    if on_start_reserved is not None:
        on_start_reserved(durable_session)
    persisted = complete._persist_transition(
        runtime,
        AdvanceRunnerSession(
            f"cli:run.session-start-intent:{session.session_id}",
            run_ref=run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state="created",
            next_state="starting",
            occurred_at=max(_now(), session.created_at),
        ),
    )
    if persisted is None:
        return SessionExecutionResult("session_start_intent_refused")
    session = persisted.runner_sessions[session.session_id]
    if on_accepted_start is not None:
        on_accepted_start(session)
    request = request_factory(session)
    _durable_session, cancellation = _pre_start_cancellation(
        runtime,
        run_ref,
        session,
    )
    if cancellation is not None:
        return cancellation
    if not reconcile._request_matches_current_authority(
        runtime,
        session=session,
        adapter=adapter,
        request=request,
    ):
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_authority_mismatch",
            "runner_request",
            request,
        )
    _durable_session, cancellation = _pre_start_cancellation(
        runtime,
        run_ref,
        session,
        daemon_stop_requested=daemon_stop_requested,
    )
    if cancellation is not None:
        return cancellation
    timeout = request.timeout_seconds
    if effective_timeout_seconds is not None:
        timeout = min(timeout, effective_timeout_seconds)
    deadline = _monotonic() + timeout
    try:
        start_outcome = adapter.start_session(request)
    except Exception:
        return SessionExecutionResult("session_reconciliation_required")
    return _handle_start_outcome(
        runtime,
        run_ref,
        session,
        request,
        start_outcome,
        deadline,
        daemon_stop_requested,
    )


def _pre_start_cancellation(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    *,
    daemon_stop_requested: Callable[[], bool] | None = None,
) -> tuple[RunnerSessionRecord, SessionExecutionResult | None]:
    if daemon_stop_requested is not None and daemon_stop_requested():
        _request_daemon_cancellation(runtime, run_ref, session)
    durable_state = complete._load(runtime)
    durable_session = durable_state.runner_sessions[session.session_id]
    primary = cancel._primary_cancellation(durable_state, durable_session)
    if primary is None:
        return durable_session, None
    return durable_session, cancel._cancel_before_external_start(
        runtime,
        run_ref=run_ref,
        session=durable_session,
        primary=primary,
    )


def _request_daemon_cancellation(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
) -> None:
    cancel._request_cancellation(
        runtime,
        run_id=run_ref.run_id,
        request_id=f"daemon:runner-session-cancel:{session.session_id}",
        reason="daemon_shutdown",
        source_kind="daemon",
        actor_id="daemon",
    )


def _observe_daemon_stop(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    daemon_stop_requested: Callable[[], bool] | None,
) -> None:
    if daemon_stop_requested is not None and daemon_stop_requested():
        _request_daemon_cancellation(runtime, run_ref, session)


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


def _handle_start_outcome(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    start_outcome: object,
    deadline: float,
    daemon_stop_requested: Callable[[], bool] | None,
) -> SessionExecutionResult:
    if isinstance(start_outcome, StartIndeterminate):
        return _persist_indeterminate_start(
            runtime,
            run_ref,
            session,
            request,
            start_outcome,
        )
    if isinstance(start_outcome, StartRefusedBeforeExternalWork):
        return _persist_refused_start(
            runtime,
            run_ref,
            session,
            request,
            start_outcome,
        )
    if not isinstance(start_outcome, StartedSession):
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_reconciliation_contradiction",
            "runner_start_outcome",
            start_outcome,
        )
    return _persist_started_session(
        runtime,
        run_ref,
        session,
        request,
        start_outcome,
        deadline,
        daemon_stop_requested,
    )


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
        diagnostic_bytes = complete._adapter_error_diagnostic_bytes(
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


def _persist_started_session(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: StartedSession,
    deadline: float,
    daemon_stop_requested: Callable[[], bool] | None,
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
    locator_digest = reconcile._safe_coordinator_locator_digest(
        runtime,
        request,
        handle_id=outcome.handle_id,
        adapter_locator=outcome.durable_locator_metadata,
    )
    if locator_digest is None:
        return _start_refusal(
            runtime,
            run_ref,
            session,
            "runner_session_reconciliation_contradiction",
            "runner_session_locator",
            outcome.durable_locator_metadata,
        )
    running_at = max(_now(), cast(int, session.start_intent_at))
    transition = AdvanceRunnerSession(
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
    try:
        running_state = complete._persist_transition(runtime, transition)
    except Exception:
        running_state = None
    if running_state is None:
        return _recover_after_running_persistence_failure(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            handle=outcome.handle,
        )
    running_session = running_state.runner_sessions[session.session_id]
    complete._record_session_event(
        runtime,
        session=running_session,
        kind="session_started",
        observed_at=running_at,
        payload={"state": "running"},
        replay_key="session-started",
        redaction_policy=request.redaction_policy,
    )
    return _drive_owned_live_handle(
        runtime,
        run_ref=run_ref,
        session=running_session,
        request=request,
        handle=outcome.handle,
        deadline=deadline,
        daemon_stop_requested=daemon_stop_requested,
    )


def _drive_owned_live_handle(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    handle: RunnerSessionHandle,
    deadline: float,
    daemon_stop_requested: Callable[[], bool] | None,
) -> SessionExecutionResult:
    try:
        result, terminal_cleanup_disposition = _drive_running_session(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            handle=handle,
            deadline=deadline,
            daemon_stop_requested=daemon_stop_requested,
        )
    except Exception:
        if _session_completion_persisted(runtime, session.session_id):
            raise
        return cancel._emergency_cleanup_live_handle(
            runtime,
            run_ref=run_ref,
            session=session,
            handle=handle,
        )
    if _session_completion_persisted(runtime, session.session_id):
        return result
    if terminal_cleanup_disposition in {"not_required", "complete"}:
        return SessionExecutionResult("session_reconciliation_required")
    return cancel._emergency_cleanup_live_handle(
        runtime,
        run_ref=run_ref,
        session=session,
        handle=handle,
    )


def _session_completion_persisted(
    runtime: OpenRuntimeContext,
    session_id: str,
) -> bool:
    try:
        return session_id in complete._load(runtime).runner_session_completions
    except Exception:
        return False


def _drive_running_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    handle: RunnerSessionHandle,
    deadline: float,
    daemon_stop_requested: Callable[[], bool] | None,
) -> tuple[SessionExecutionResult, str | None]:
    while True:
        _observe_daemon_stop(runtime, run_ref, session, daemon_stop_requested)
        primary = cancel._primary_cancellation(complete._load(runtime), session)
        if primary is not None:
            return (
                cancel._cancel_running_session(
                    runtime,
                    run_ref=run_ref,
                    session=session,
                    request=request,
                    handle=handle,
                    primary=primary,
                ),
                None,
            )
        try:
            outcome = handle.poll_completion()
        except Exception as exc:
            complete._audit_session_refusal(
                runtime,
                run_ref=run_ref,
                session=session,
                reason="runner_session_reconciliation_contradiction",
                signal_kind="runner_completion_poll",
                signal_digest=complete._signal_digest(type(exc).__qualname__),
            )
            cancel._request_cancellation(
                runtime,
                run_id=run_ref.run_id,
                request_id=(f"runtime:runner-session-failure:{session.session_id}"),
                reason="runtime_failure",
                source_kind="runtime",
                actor_id="runtime",
            )
            primary = cancel._primary_cancellation(complete._load(runtime), session)
            if primary is None:
                return SessionExecutionResult("session_reconciliation_required"), None
            return (
                cancel._cancel_running_session(
                    runtime,
                    run_ref=run_ref,
                    session=session,
                    request=request,
                    handle=handle,
                    primary=primary,
                ),
                None,
            )
        if outcome is not None:
            if not isinstance(outcome, (AdapterSuccessResult, AdapterErrorResult)):
                complete._audit_session_refusal(
                    runtime,
                    run_ref=run_ref,
                    session=session,
                    reason="runner_session_reconciliation_contradiction",
                    signal_kind="runner_completion_outcome",
                    signal_digest=complete._signal_digest(outcome),
                )
                return SessionExecutionResult("session_reconciliation_required"), None
            refusal = complete._completion_refusal(
                runtime,
                run_ref=run_ref,
                session=session,
                request=request,
                outcome=outcome,
            )
            if refusal is not None:
                return refusal, None
            cleanup = cancel._call_cleanup(handle.cleanup)
            return (
                complete._persist_completion(
                    runtime,
                    run_ref=run_ref,
                    session=session,
                    request=request,
                    outcome=outcome,
                    cleanup=cleanup,
                ),
                cleanup.disposition,
            )
        remaining = deadline - _monotonic()
        if remaining <= 0:
            cancel._request_cancellation(
                runtime,
                run_id=run_ref.run_id,
                request_id=(f"runtime:runner-session-timeout:{session.session_id}"),
                reason="runner_timeout",
                source_kind="runtime",
                actor_id="runtime",
            )
            primary = cancel._primary_cancellation(complete._load(runtime), session)
            if primary is None:
                return SessionExecutionResult("session_reconciliation_required"), None
            return (
                cancel._cancel_running_session(
                    runtime,
                    run_ref=run_ref,
                    session=session,
                    request=request,
                    handle=handle,
                    primary=primary,
                ),
                None,
            )
        _sleep(min(_POLL_INTERVAL_SECONDS, remaining))


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


def _current_session(
    state: RuntimeState,
    session_id: str | None,
) -> RunnerSessionRecord | None:
    if session_id is None:
        return None
    return state.runner_sessions.get(session_id)


def _now() -> int:
    return time.time_ns()


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


__all__ = (
    "SESSION_DIAGNOSTIC_MAX_BYTES",
    "SessionCancellationRequestResult",
    "SessionExecutionResult",
    "cooperative_cancel_grace_seconds",
    "execute_runner_session",
    "request_operator_cancellation",
    "session_cancellation_token",
    "session_correlation_id",
    "terminate_grace_seconds",
)
