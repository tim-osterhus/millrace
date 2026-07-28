"""Durable runner-session start and completion coordination."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from millrace.adapters.cli.context import OpenRuntimeContext, transition_context
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    RunnerAdapter,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    runner_evidence_from_adapter_outcome,
)
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner import (
    RunnerResultEvidence,
    runner_result_evidence_from_payload,
)
from millrace.contracts.state import (
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
    RunRef,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    CreateRunnerSession,
    RecordRunnerSessionCompletion,
    RunnerResultObserved,
    TransitionInput,
)
from millrace.kernel import apply, decide

_COMMAND = "run.session"


@dataclass(frozen=True, slots=True)
class SessionExecutionResult:
    code: str
    accepted: bool = False
    adapter_error_kind: str | None = None
    observation_refusal_reason: str | None = None
    transition_disposition: str | None = None


def execute_runner_session(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    adapter: RunnerAdapter,
    request_factory: Callable[[RunnerSessionRecord], AdapterInvocationRequest],
    explicit_retry_intent: bool,
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
        elif current.state == "created":
            return _start_created_session(
                runtime,
                run_ref=run_ref,
                session=current,
                adapter=adapter,
                request_factory=request_factory,
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
) -> SessionExecutionResult:
    request = request_factory(session)
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
            pass
        return SessionExecutionResult("session_reconciliation_required")
    if isinstance(start_outcome, StartRefusedBeforeExternalWork):
        error_echo = start_outcome.adapter_error.dispatch_echo
        if error_echo is None:
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
            return SessionExecutionResult("session_reconciliation_required")
        return _persist_adapter_error(
            runtime,
            run_ref=run_ref,
            session=session,
            outcome=start_outcome.adapter_error,
            diagnostic_digest=runtime.cas_store.put_bytes(
                start_outcome.diagnostic_digest.encode("utf-8")
            ),
            cleanup_disposition="not_required",
        )
    if not isinstance(start_outcome, StartedSession):
        return SessionExecutionResult("session_reconciliation_required")
    try:
        start_outcome.dispatch_echo.validate_against(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
        )
    except (TypeError, ValueError):
        return SessionExecutionResult("session_reconciliation_required")

    locator_digest = runtime.cas_store.put_bytes(
        _canonical_json_bytes(start_outcome.durable_locator_metadata)
    )
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
    try:
        outcome = start_outcome.handle.poll_completion()
    except Exception:
        return SessionExecutionResult("session_reconciliation_required")
    if outcome is None:
        return SessionExecutionResult("session_running")
    return _persist_completion(
        runtime,
        run_ref=run_ref,
        session=running_session,
        request=request,
        outcome=outcome,
        cleanup_disposition="not_required",
    )


def _persist_completion(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: AdapterInvocationOutcome,
    cleanup_disposition: str,
) -> SessionExecutionResult:
    dispatch_echo = outcome.dispatch_echo
    if dispatch_echo is None:
        return SessionExecutionResult("session_reconciliation_required")
    try:
        dispatch_echo.validate_against(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
        )
    except (TypeError, ValueError):
        return SessionExecutionResult("session_reconciliation_required")
    if isinstance(outcome, AdapterErrorResult):
        diagnostic_digest = runtime.cas_store.put_bytes(
            _canonical_json_bytes(outcome.diagnostics)
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
        return SessionExecutionResult("adapter_conversion_refused")
    evidence_digest = runtime.cas_store.put_bytes(
        _canonical_json_bytes(evidence.payload())
    )
    diagnostic_digest = runtime.cas_store.put_bytes(
        _canonical_json_bytes(outcome.evidence_construction_diagnostics)
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
) -> SessionExecutionResult:
    completion = _completion_record(
        session=session,
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind="error",
        adapter_error_kind=outcome.error_kind,
        evidence_digest=None,
        diagnostic_digest=diagnostic_digest,
        cleanup_disposition=cleanup_disposition,
        redaction_policy_id=outcome.redaction_policy_id,
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
        primary_cancellation_request_id=None,
        cleanup_disposition=cleanup_disposition,
        started_at=session.started_at,
        cancel_requested_at=None,
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
    evidence = _load_evidence(runtime, digest)
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
    if not decision.accepted:
        return None
    next_state = apply(state, decision)
    runtime.store.persist_runtime_state(next_state, runtime.cas_store)
    return next_state


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


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_json_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, tuple):
        return [_plain_json_value(item) for item in value]
    return cast(AuthorityValue, value)


def _now() -> int:
    return time.time_ns()


__all__ = (
    "SessionExecutionResult",
    "execute_runner_session",
    "session_cancellation_token",
    "session_correlation_id",
)
