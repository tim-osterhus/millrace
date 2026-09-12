"""Durable runner-session orchestration facade."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from millrace.adapters.cli import session_cancellation as cancel
from millrace.adapters.cli import session_completion as complete
from millrace.adapters.cli import session_persistence as persistence
from millrace.adapters.cli import session_reconciliation as reconcile
from millrace.adapters.cli import session_supervision as supervision
from millrace.adapters.cli.context import (
    OpenRuntimeContext,
    _RunnerStartDeferred,
)
from millrace.adapters.cli.run_controls import (
    validate_effective_timeout as _validate_effective_timeout,
)
from millrace.adapters.cli.session_supervision import (
    _persist_indeterminate_start,
    _persist_refused_start,
    _recover_after_running_persistence_failure,
    _start_refusal,
)
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationRequest,
    AdapterSuccessResult,
    RunnerAdapter,
    RunnerCleanupResult,
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
from millrace.kernel.run_controls import run_hold_refusal

SESSION_DIAGNOSTIC_MAX_BYTES = complete.SESSION_DIAGNOSTIC_MAX_BYTES
SessionCancellationRequestResult = cancel.SessionCancellationRequestResult
SessionExecutionResult = complete.SessionExecutionResult
cooperative_cancel_grace_seconds = cancel.cooperative_cancel_grace_seconds
request_operator_cancellation = cancel.request_operator_cancellation
session_cancellation_token = reconcile.session_cancellation_token
session_correlation_id = reconcile.session_correlation_id
terminate_grace_seconds = cancel.terminate_grace_seconds

_POLL_INTERVAL_SECONDS = 0.25
_PrepareCreatedSession = Callable[[RunnerSessionRecord], RunnerSessionRecord]


@dataclass(slots=True)
class _RetainedOwner:
    # No runtime, connection, or callback may survive a bounded unit here.
    run_ref: RunRef
    session: RunnerSessionRecord
    request: AdapterInvocationRequest
    handle: RunnerSessionHandle
    deadline: float
    outcome: AdapterSuccessResult | AdapterErrorResult | None = None
    cleanup: RunnerCleanupResult | None = None
    cancellation: supervision._CancellationCursor | None = None
    held: bool = False


_RETAINED_OWNERS: ContextVar[dict[str, _RetainedOwner] | None] = ContextVar(
    "daemon_retained_runner_owners", default=None
)


def _retained_start_eligible(state: RuntimeState) -> bool:
    """Validate held overlap against the state that will admit the new start."""
    for owner in (_RETAINED_OWNERS.get() or {}).values():
        session = state.runner_sessions.get(owner.session.session_id)
        run = state.runs.get(owner.run_ref.run_id)
        if (
            session is None
            or run is None
            or run.run_ref != owner.run_ref
            or run.current_session_id != owner.session.session_id
            or session.run_id != owner.session.run_id
            or session.dispatch_generation != owner.session.dispatch_generation
            or session.session_fencing_token != owner.session.session_fencing_token
        ):
            return False
        if session.session_id in state.runner_session_completions:
            continue
        control = state.run_execution_controls.get(owner.run_ref.run_id)
        if session.state != "running" or control is None or control.state != "paused":
            return False
        native = control.native
        if native is None or native["attempt"] != {
            "session_id": session.session_id,
            "dispatch_generation": session.dispatch_generation,
            "session_fencing_token": session.session_fencing_token,
            "state": session.state,
        }:
            return False
        snapshot = native["snapshot"]
        if (
            snapshot["state"] != "held"
            or snapshot["active_effects"] != 0
            or snapshot.get("eligible") is not True
            or snapshot.get("parked") is not True
            or snapshot.get("invalidation_pending") is not False
        ):
            return False
    return True


def _step_retained_owner(
    runtime: OpenRuntimeContext,
    owner: _RetainedOwner,
    *,
    stop_requested: bool,
) -> SessionExecutionResult | None:
    """Service the exact accepted attempt once, retaining consumed outcomes."""
    from millrace.adapters.cli.run_controls import drive_native_control

    owner.held = False
    state = complete._load(runtime)
    session = state.runner_sessions[owner.session.session_id]
    run = state.runs[owner.run_ref.run_id]
    if (
        run.run_ref != owner.run_ref
        or run.current_session_id != session.session_id
        or session.dispatch_generation != owner.session.dispatch_generation
        or session.session_fencing_token != owner.session.session_fencing_token
    ):
        raise RuntimeError("retained runner authority changed")
    owner.session = session
    completion = state.runner_session_completions.get(session.session_id)
    if completion is not None:
        return complete._apply_persisted_completion(runtime, completion)
    drive_native_control(runtime, session, owner.handle)
    if stop_requested:
        _request_daemon_cancellation(runtime, owner.run_ref, session)
    state = complete._load(runtime)
    primary = cancel._primary_cancellation(state, session)
    if owner.cancellation is None and primary is not None:
        owner.cancellation = supervision._CancellationCursor(primary)
        # A completion consumed before a conflicting public write stays consumed.
        owner.cancellation.outcome = owner.outcome
        owner.cancellation.cleanup = owner.cleanup
    if owner.cancellation is not None:
        owner.held = False
        return supervision._step_cancellation(
            runtime,
            run_ref=owner.run_ref,
            session=session,
            request=owner.request,
            handle=owner.handle,
            cursor=owner.cancellation,
        )
    _poll_retained_owner(owner)
    if owner.outcome is not None:
        return _finish_retained_owner(runtime, owner, session)
    if _monotonic() >= owner.deadline:
        cancel._request_cancellation(
            runtime,
            run_id=owner.run_ref.run_id,
            request_id=f"runtime:runner-session-timeout:{session.session_id}",
            reason="runner_timeout",
            source_kind="runtime",
            actor_id="runtime",
        )
        owner.held = False
        return None
    state = complete._load(runtime)
    control = state.run_execution_controls.get(owner.run_ref.run_id)
    owner.held = (
        control is not None and control.state == "paused" and control.native is not None
    )
    return None


def _poll_retained_owner(owner: _RetainedOwner) -> None:
    if owner.outcome is None:
        outcome = owner.handle.poll_completion()
        if outcome is not None:
            if not isinstance(outcome, (AdapterSuccessResult, AdapterErrorResult)):
                raise RuntimeError("malformed retained runner completion")
            owner.outcome = outcome


def _finish_retained_owner(
    runtime: OpenRuntimeContext,
    owner: _RetainedOwner,
    session: RunnerSessionRecord,
) -> SessionExecutionResult:
    assert owner.outcome is not None
    refusal = complete._completion_refusal(
        runtime,
        run_ref=owner.run_ref,
        session=session,
        request=owner.request,
        outcome=owner.outcome,
    )
    if refusal is not None:
        raise RuntimeError("retained runner completion authority refused")
    if owner.cleanup is None:
        owner.cleanup = cancel._call_cleanup(owner.handle.cleanup)
    # Reload cancellation/session authority after native cleanup and context I/O.
    state = complete._load(runtime)
    session = state.runner_sessions[session.session_id]
    primary = cancel._primary_cancellation(state, session)
    return complete._persist_completion(
        runtime,
        run_ref=owner.run_ref,
        session=session,
        request=owner.request,
        outcome=owner.outcome,
        cleanup=owner.cleanup,
        primary=primary,
    )


def execute_runner_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    explicit_retry_intent: bool,
    prepare_created_session: _PrepareCreatedSession | None = None,
    on_start_reserved: Callable[[RunnerSessionRecord], None] | None = None,
    on_accepted_start: Callable[[RunnerSessionRecord], None] | None = None,
    daemon_stop_requested: Callable[[], bool] | None = None,
    effective_timeout_seconds: float | None = None,
    driving_budget_id: str | None = None,
    driving_budget_clock: Callable[[], float] | None = None,
) -> SessionExecutionResult:
    """Start or replay one durable session attempt for the current run."""

    complete.runner_evidence_from_adapter_outcome = runner_evidence_from_adapter_outcome
    _validate_effective_timeout(effective_timeout_seconds)
    state = complete._load(runtime)
    run = state.runs.get(run_ref.run_id)
    if run is None or run.run_ref != run_ref:
        return SessionExecutionResult("ready_state_corrupt")
    held = run_hold_refusal(state, run_ref.run_id)
    if held is not None:
        return SessionExecutionResult(held)
    current = state.runner_sessions.get(run.current_session_id or "")
    resumed = _resume_current_session(
        runtime,
        run_ref,
        current,
        adapter,
        request_factory,
        explicit_retry_intent,
        prepare_created_session,
        on_start_reserved,
        on_accepted_start,
        daemon_stop_requested,
        effective_timeout_seconds,
        driving_budget_id,
        driving_budget_clock,
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
        prepare_created_session=prepare_created_session,
        on_start_reserved=on_start_reserved,
        on_accepted_start=on_accepted_start,
        daemon_stop_requested=daemon_stop_requested,
        effective_timeout_seconds=effective_timeout_seconds,
        driving_budget_id=driving_budget_id,
        driving_budget_clock=driving_budget_clock,
    )


def _resume_current_session(
    runtime: OpenRuntimeContext,
    run_ref: RunRef,
    current: RunnerSessionRecord | None,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    explicit_retry_intent: bool,
    prepare_created_session: _PrepareCreatedSession | None,
    on_start_reserved: Callable[[RunnerSessionRecord], None] | None,
    on_accepted_start: Callable[[RunnerSessionRecord], None] | None,
    daemon_stop_requested: Callable[[], bool] | None,
    effective_timeout_seconds: float | None,
    driving_budget_id: str | None,
    driving_budget_clock: Callable[[], float] | None,
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
            prepare_created_session=prepare_created_session,
            on_start_reserved=on_start_reserved,
            on_accepted_start=on_accepted_start,
            daemon_stop_requested=daemon_stop_requested,
            effective_timeout_seconds=effective_timeout_seconds,
            driving_budget_id=driving_budget_id,
            driving_budget_clock=driving_budget_clock,
        )
    return None


def _prepare_created_session(
    runtime: OpenRuntimeContext,
    session: RunnerSessionRecord,
    prepare_created_session: _PrepareCreatedSession | None,
) -> RunnerSessionRecord | SessionExecutionResult:
    if prepare_created_session is None:
        return session
    try:
        prepared_session = prepare_created_session(session)
        durable_prepared_session = complete._load(runtime).runner_sessions.get(
            session.session_id
        )
    except Exception:
        return SessionExecutionResult("session_preparation_refused")
    if (
        not isinstance(prepared_session, RunnerSessionRecord)
        or durable_prepared_session != prepared_session
        or prepared_session.session_id != session.session_id
        or prepared_session.state != "created"
    ):
        return SessionExecutionResult("session_preparation_refused")
    return prepared_session


def _start_created_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    prepare_created_session: _PrepareCreatedSession | None,
    on_start_reserved: Callable[[RunnerSessionRecord], None] | None,
    on_accepted_start: Callable[[RunnerSessionRecord], None] | None,
    daemon_stop_requested: Callable[[], bool] | None,
    effective_timeout_seconds: float | None,
    driving_budget_id: str | None,
    driving_budget_clock: Callable[[], float] | None,
) -> SessionExecutionResult:
    durable_session, cancellation = _pre_start_cancellation(
        runtime,
        run_ref,
        session,
        daemon_stop_requested=daemon_stop_requested,
    )
    if cancellation is not None:
        return cancellation
    prepared_session = _prepare_created_session(
        runtime,
        durable_session,
        prepare_created_session,
    )
    if isinstance(prepared_session, SessionExecutionResult):
        return prepared_session
    durable_session = session = prepared_session
    from millrace.adapters.cli.run_controls import admit_runner_start

    try:
        admitted = admit_runner_start(
            runtime,
            run_ref,
            session,
            occurred_at=max(_now(), session.created_at),
            driving_budget_id=driving_budget_id,
            driving_budget_clock=driving_budget_clock,
            on_start_reserved=on_start_reserved,
            on_accepted_start=on_accepted_start,
        )
    except _RunnerStartDeferred:
        return SessionExecutionResult("runner_session_waiting")
    if admitted is None:
        return SessionExecutionResult("session_start_intent_refused")
    session = admitted
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
    timeout = min(timeout, effective_timeout_seconds or timeout)
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
    owners = _RETAINED_OWNERS.get()
    if owners is not None:
        owners[running_session.session_id] = _RetainedOwner(
            run_ref, running_session, request, outcome.handle, deadline
        )
    persistence._record_session_event(
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
    owners = _RETAINED_OWNERS.get()
    if owners is not None and session.session_id in owners:
        if owners[session.session_id].handle is not handle:
            raise RuntimeError("retained runner handle mismatch")
        return SessionExecutionResult("runner_session_retained")
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
        from millrace.adapters.cli.run_controls import drive_native_control

        drive_native_control(runtime, session, handle)
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
