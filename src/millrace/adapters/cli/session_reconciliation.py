"""Runner-session restart reconciliation and locator validation."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from millrace.adapters.cli import session_cancellation as cancel
from millrace.adapters.cli import session_completion as complete
from millrace.adapters.cli.context import (
    OpenRuntimeContext,
)
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationRequest,
    CleanupPending,
    Contradiction,
    DispatchEcho,
    RunnerAdapter,
    RunnerSessionHandle,
    RunnerSessionReconcileRequest,
    Terminal,
    Unsupported,
    VerifiedLive,
)
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner import (
    runner_session_locator_bytes,
    runner_session_locator_from_bytes,
)
from millrace.contracts.state import (
    RunnerSessionRecord,
    RunRef,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
)
from millrace.operator.dispatch import (
    DispatchProjectionError,
    build_dispatch_envelope_for_run,
)

_COORDINATOR_LOCATOR_RECORD_KIND = "runner_session_coordinator_locator"
_COORDINATOR_LOCATOR_SCHEMA_VERSION = 1
_COORDINATOR_LOCATOR_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "adapter_locator",
        "handle_id_digest",
    }
)
_RESERVED_ADAPTER_LOCATOR_KEYS = frozenset({"handle_id"})


@dataclass(frozen=True, slots=True)
class _StoredReconciliationLocator:
    handle_id_digest: str | None
    adapter_locator: Mapping[str, AuthorityValue]
    legacy: bool


@dataclass(frozen=True, slots=True)
class _VerifiedLiveSession:
    session: RunnerSessionRecord
    request: AdapterInvocationRequest
    handle: RunnerSessionHandle
    deadline: float


def _reconcile_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    effective_timeout_seconds: float | None,
) -> complete.SessionExecutionResult | _VerifiedLiveSession:
    request = request_factory(session)
    if not _request_matches_current_authority(
        runtime,
        session=session,
        adapter=adapter,
        request=request,
    ):
        return _reconciliation_contradiction(
            runtime, run_ref=run_ref, session=session, signal=request
        )
    stored_locator = _load_reconciliation_locator(runtime, session)
    if stored_locator is None:
        return _reconciliation_contradiction(
            runtime,
            run_ref=run_ref,
            session=session,
            signal={"durable_locator_digest": session.durable_locator_digest},
        )
    outcome, valid_outcome = _validated_reconciliation_outcome(
        adapter,
        request=request,
        stored_locator=stored_locator,
    )
    if not valid_outcome:
        return _reconciliation_contradiction(
            runtime,
            run_ref=run_ref,
            session=session,
            signal=outcome,
        )
    if isinstance(outcome, Contradiction):
        return _reconciliation_contradiction(
            runtime, run_ref=run_ref, session=session, signal=outcome
        )
    if isinstance(outcome, Unsupported):
        return complete._persist_orphan_risk(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            primary=cancel._primary_cancellation(complete._load(runtime), session),
        )
    if isinstance(outcome, Terminal):
        return _reconcile_terminal(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            outcome=outcome,
        )
    if isinstance(outcome, VerifiedLive):
        return _reconcile_verified_live(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            stored_locator=stored_locator,
            outcome=outcome,
            effective_timeout_seconds=effective_timeout_seconds,
        )
    if isinstance(outcome, CleanupPending):
        if (
            stored_locator.handle_id_digest is None
            or _handle_id_digest(outcome.handle_id) != stored_locator.handle_id_digest
        ):
            return _reconciliation_contradiction(
                runtime,
                run_ref=run_ref,
                session=session,
                signal={"cleanup_pending_handle_id": outcome.handle_id},
            )
        return _resume_reconciled_cleanup(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            handle=outcome.handle,
        )
    return _reconciliation_contradiction(
        runtime, run_ref=run_ref, session=session, signal=outcome
    )


def _validated_reconciliation_outcome(
    adapter: RunnerAdapter,
    *,
    request: AdapterInvocationRequest,
    stored_locator: _StoredReconciliationLocator,
) -> tuple[object, bool]:
    try:
        outcome = adapter.reconcile_session(
            RunnerSessionReconcileRequest(
                request,
                stored_locator.adapter_locator,
            )
        )
    except Exception:
        return {"adapter_exception": True}, False
    echo = getattr(outcome, "dispatch_echo", None)
    if not isinstance(echo, DispatchEcho):
        return outcome, False
    try:
        echo.validate_against(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
    except (AttributeError, TypeError, ValueError):
        return outcome, False
    return outcome, True


def _reconcile_terminal(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: Terminal,
) -> complete.SessionExecutionResult:
    if outcome.adapter_outcome.dispatch_echo != outcome.dispatch_echo:
        return _reconciliation_contradiction(
            runtime, run_ref=run_ref, session=session, signal=outcome
        )
    if session.state == "starting":
        running_session = _advance_reconciled_starting_session(
            runtime,
            run_ref=run_ref,
            session=session,
            locator_digest=session.durable_locator_digest,
        )
        if running_session is None:
            return complete.SessionExecutionResult(
                "runner_session_reconciliation_contradiction"
            )
        session = running_session
    return complete._persist_completion(
        runtime,
        run_ref=run_ref,
        session=session,
        request=request,
        outcome=outcome.adapter_outcome,
        cleanup=cancel._terminal_cleanup_result(None, outcome.cleanup_disposition),
        primary=cancel._primary_cancellation(complete._load(runtime), session),
    )


def _reconcile_verified_live(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    stored_locator: _StoredReconciliationLocator,
    outcome: VerifiedLive,
    effective_timeout_seconds: float | None,
) -> complete.SessionExecutionResult | _VerifiedLiveSession:
    if (
        stored_locator.handle_id_digest is not None
        and _handle_id_digest(outcome.handle_id) != stored_locator.handle_id_digest
    ):
        return _reconciliation_contradiction(
            runtime,
            run_ref=run_ref,
            session=session,
            signal={"verified_live_handle_id": outcome.handle_id},
        )
    locator_digest = _safe_coordinator_locator_digest(
        runtime,
        request,
        handle_id=outcome.handle_id,
        adapter_locator=outcome.durable_locator_metadata,
    )
    if locator_digest is None:
        return _reconciliation_contradiction(
            runtime,
            run_ref=run_ref,
            session=session,
            signal=outcome.durable_locator_metadata,
        )
    updated_session: RunnerSessionRecord | None = session
    if session.state == "starting":
        updated_session = _advance_reconciled_starting_session(
            runtime,
            run_ref=run_ref,
            session=session,
            locator_digest=locator_digest,
        )
    elif stored_locator.legacy:
        updated_session = _upgrade_legacy_reconciled_locator(
            runtime,
            run_ref=run_ref,
            session=session,
            locator_digest=locator_digest,
        )
    if updated_session is None:
        return complete.SessionExecutionResult(
            "runner_session_reconciliation_contradiction"
        )
    timeout = request.timeout_seconds
    if effective_timeout_seconds is not None:
        timeout = min(timeout, effective_timeout_seconds)
    return _VerifiedLiveSession(
        updated_session,
        request,
        outcome.handle,
        _monotonic() + timeout,
    )


def _advance_reconciled_starting_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    locator_digest: str | None,
) -> RunnerSessionRecord | None:
    running = AdvanceRunnerSession(
        f"cli:run.session-reconciled-running:{session.session_id}",
        run_ref=run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="starting",
        next_state="running",
        occurred_at=max(_now(), cast(int, session.start_intent_at)),
        durable_locator_digest=locator_digest,
    )
    persisted = complete._persist_transition(runtime, running)
    if persisted is None:
        return None
    return persisted.runner_sessions[session.session_id]


def _upgrade_legacy_reconciled_locator(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    locator_digest: str,
) -> RunnerSessionRecord | None:
    if session.state not in {
        "running",
        "cancellation_requested",
        "terminating",
    }:
        return None
    occurred_at = max(
        value
        for value in (
            session.created_at,
            session.start_intent_at,
            session.started_at,
        )
        if value is not None
    )
    persisted = complete._persist_transition(
        runtime,
        AdvanceRunnerSession(
            f"cli:run.session-upgrade-locator:{session.session_id}",
            run_ref=run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state=session.state,
            next_state=session.state,
            occurred_at=occurred_at,
            durable_locator_digest=locator_digest,
        ),
    )
    if persisted is None:
        return None
    return persisted.runner_sessions[session.session_id]


def _load_reconciliation_locator(
    runtime: OpenRuntimeContext,
    session: RunnerSessionRecord,
) -> _StoredReconciliationLocator | None:
    if session.durable_locator_digest is None:
        return (
            _StoredReconciliationLocator(None, {}, False)
            if session.state == "starting"
            else None
        )
    try:
        payload = runtime.cas_store.get_bytes(session.durable_locator_digest)
        locator = runner_session_locator_from_bytes(payload)
    except (OSError, TypeError, ValueError):
        return None
    is_coordinator_locator = (
        locator.get("record_kind") == _COORDINATOR_LOCATOR_RECORD_KIND
        or "adapter_locator" in locator
        or "handle_id_digest" in locator
    )
    if not is_coordinator_locator:
        return _StoredReconciliationLocator(None, locator, True)
    if (
        set(locator) != _COORDINATOR_LOCATOR_KEYS
        or locator.get("record_kind") != _COORDINATOR_LOCATOR_RECORD_KIND
        or type(locator.get("schema_version")) is not int
        or locator.get("schema_version") != _COORDINATOR_LOCATOR_SCHEMA_VERSION
    ):
        return None
    handle_id_digest = locator.get("handle_id_digest")
    adapter_locator = locator.get("adapter_locator")
    if (
        handle_id_digest is not None and not isinstance(handle_id_digest, str)
    ) or not isinstance(adapter_locator, Mapping):
        return None
    if isinstance(handle_id_digest, str):
        if (
            not handle_id_digest.startswith("sha256:")
            or len(handle_id_digest) != 71
            or any(
                character not in "0123456789abcdef"
                for character in handle_id_digest[7:]
            )
        ):
            return None
    return _StoredReconciliationLocator(
        handle_id_digest,
        adapter_locator,
        False,
    )


def _reconciliation_contradiction(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    signal: object,
) -> complete.SessionExecutionResult:
    complete._audit_session_refusal(
        runtime,
        run_ref=run_ref,
        session=session,
        reason="runner_session_reconciliation_contradiction",
        signal_kind="runner_reconciliation",
        signal_digest=complete._signal_digest(signal),
    )
    return complete.SessionExecutionResult(
        "runner_session_reconciliation_contradiction"
    )


def _resume_reconciled_cleanup(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    handle: RunnerSessionHandle,
) -> complete.SessionExecutionResult:
    primary = cancel._primary_cancellation(complete._load(runtime), session)
    if primary is None:
        return _reconciliation_contradiction(
            runtime,
            run_ref=run_ref,
            session=session,
            signal={"cleanup_pending_without_primary": True},
        )
    cleanup = cancel._call_cleanup(handle.cleanup)
    attempts = [
        attempt
        for attempt in complete._load(
            runtime
        ).runner_session_cancellation_attempts.values()
        if attempt.session_id == session.session_id
    ]
    cancel._persist_cleanup_operation(
        runtime,
        run_ref=run_ref,
        session=session,
        primary=primary,
        sequence=len(attempts),
        cleanup=cleanup,
        redaction_policy=request.redaction_policy,
    )
    if cleanup.disposition == "orphan_risk":
        return complete._persist_orphan_risk(
            runtime,
            run_ref=run_ref,
            session=complete._load(runtime).runner_sessions[session.session_id],
            request=request,
            primary=primary,
        )
    try:
        outcome = cancel._poll_handle(handle)
    except TypeError:
        outcome = None
    if outcome is None:
        return _reconciliation_contradiction(
            runtime,
            run_ref=run_ref,
            session=complete._load(runtime).runner_sessions[session.session_id],
            signal={"cleanup_completed_without_terminal_outcome": True},
        )
    return complete._persist_completion(
        runtime,
        run_ref=run_ref,
        session=complete._load(runtime).runner_sessions[session.session_id],
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


def session_correlation_id(session: RunnerSessionRecord) -> str:
    return f"cli:run.session:{session.session_id}:{session.dispatch_generation}"


def session_cancellation_token(session: RunnerSessionRecord) -> str:
    return f"cli:run.session-cancel:{session.session_id}:{session.dispatch_generation}"


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
            state=complete._load(runtime),
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


def _safe_coordinator_locator_digest(
    runtime: OpenRuntimeContext,
    request: AdapterInvocationRequest,
    *,
    handle_id: str | None,
    adapter_locator: object,
) -> str | None:
    if handle_id is not None and (
        not isinstance(handle_id, str) or not handle_id.strip()
    ):
        return None
    if _contains_reserved_adapter_locator_key(adapter_locator):
        return None
    try:
        redacted = request.redaction_policy.redact_authority_value(adapter_locator)
    except (TypeError, ValueError):
        return None
    if not isinstance(redacted, Mapping):
        return None
    locator: dict[str, object] = {
        "record_kind": _COORDINATOR_LOCATOR_RECORD_KIND,
        "schema_version": _COORDINATOR_LOCATOR_SCHEMA_VERSION,
        "adapter_locator": redacted,
        "handle_id_digest": (
            _handle_id_digest(handle_id) if handle_id is not None else None
        ),
    }
    try:
        payload = runner_session_locator_bytes(locator)
    except (TypeError, ValueError):
        return None
    return runtime.cas_store.put_bytes(payload)


def _contains_reserved_adapter_locator_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in _RESERVED_ADAPTER_LOCATOR_KEYS
            or _contains_reserved_adapter_locator_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_reserved_adapter_locator_key(item) for item in value)
    return False


def _handle_id_digest(handle_id: str) -> str:
    if not isinstance(handle_id, str) or not handle_id.strip():
        raise ValueError("handle_id must be a nonblank string")
    return f"sha256:{sha256(handle_id.encode('utf-8')).hexdigest()}"


def _now() -> int:
    return time.time_ns()


def _monotonic() -> float:
    return time.monotonic()
