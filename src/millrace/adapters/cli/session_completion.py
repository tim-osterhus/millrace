"""Runner-session completion persistence and replay mechanics."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from uuid import uuid4

from millrace.adapters.cli import session_persistence as persistence
from millrace.adapters.cli.context import (
    OpenRuntimeContext,
    refusal_is_pre_persist,
    transition_context,
)
from millrace.adapters.cli.context_writeback import validate_context_writeback
from millrace.adapters.cli.session_diagnostics import (
    _completion_diagnostic_bytes,
    _completion_diagnostic_bytes_for_dispatch,
    _signal_digest,
)
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterSuccessResult,
    RedactionPolicy,
    RunnerCleanupResult,
    start_refusal_diagnostic_bytes,
)
from millrace.adapters.runner_contract import (
    runner_evidence_from_adapter_outcome as runner_evidence_from_adapter_outcome,
)
from millrace.contracts.runner import (
    RUNNER_SESSION_COMPLETION_DIAGNOSTIC_MAX_BYTES,
    RunnerDispatchEnvelope,
    RunnerResultEvidence,
    runner_result_evidence_bytes,
    runner_result_evidence_digest,
)
from millrace.contracts.state import (
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
    RunRef,
    RuntimeState,
)
from millrace.contracts.transition import (
    RecordRunnerSessionCompletion,
    RefuseRunnerSessionSignal,
    RunnerResultObserved,
    TransitionInput,
)
from millrace.kernel import apply, decide
from millrace.operator.dispatch import (
    DispatchProjectionError,
    build_dispatch_envelope_for_run,
)
from millrace.substrate.errors import SubstrateError

_COMMAND = "run.session"
SESSION_DIAGNOSTIC_MAX_BYTES = RUNNER_SESSION_COMPLETION_DIAGNOSTIC_MAX_BYTES

__all__ = (
    "SESSION_DIAGNOSTIC_MAX_BYTES",
    "_signal_digest",
    "build_dispatch_envelope_for_run",
)
_RUNTIME_SESSION_EVENT_POLICY = RedactionPolicy(policy_id="runtime-session-events")


@dataclass(frozen=True, slots=True)
class SessionExecutionResult:
    code: str
    accepted: bool = False
    adapter_error_kind: str | None = None
    observation_refusal_reason: str | None = None
    transition_disposition: str | None = None


def _persist_orphan_risk(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    primary: RunnerSessionCancellationRecord | None,
    diagnostic: Mapping[str, object] | None = None,
    outcome: AdapterInvocationOutcome | None = None,
    reported_adapter_error_kind: str | None = None,
    adapter_outcome_kind: str | None = None,
    persistence_failure_code: str = "runner_session_reconciliation_contradiction",
) -> SessionExecutionResult:
    diagnostic_digest = runtime.cas_store.put_bytes(
        _completion_diagnostic_bytes(
            request,
            {"reconciliation": "unsupported"} if diagnostic is None else diagnostic,
        )
    )
    completion = _completion_record(
        session=session,
        terminal_state="lost",
        exit_kind="lost",
        adapter_outcome_kind=adapter_outcome_kind
        or (
            "success"
            if isinstance(outcome, AdapterSuccessResult)
            else "error"
            if isinstance(outcome, AdapterErrorResult)
            else "unsupported"
        ),
        adapter_error_kind=(
            outcome.error_kind if isinstance(outcome, AdapterErrorResult) else None
        ),
        evidence_digest=None,
        diagnostic_digest=diagnostic_digest,
        cleanup_disposition="orphan_risk",
        redaction_policy_id=request.redaction_policy.policy_id,
        primary=primary,
    )
    if (
        _persist_completion_record(
            runtime,
            run_ref,
            session,
            completion,
            event_redaction_policy=request.redaction_policy,
        )
        is None
    ):
        return SessionExecutionResult(persistence_failure_code)
    if not persistence._persist_governed_runner_usage(runtime, session, outcome):
        return SessionExecutionResult("runner_usage_evidence_refused")
    return SessionExecutionResult(
        "runner_session_orphan_risk",
        adapter_error_kind=reported_adapter_error_kind,
    )


def _persist_completion(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: AdapterInvocationOutcome,
    cleanup: RunnerCleanupResult,
    primary: RunnerSessionCancellationRecord | None = None,
    adapter_error_terminal_state: str = "failed",
) -> SessionExecutionResult:
    refusal = _completion_refusal(
        runtime,
        run_ref=run_ref,
        session=session,
        request=request,
        outcome=outcome,
    )
    if refusal is not None:
        return refusal
    if isinstance(outcome, AdapterErrorResult):
        result = _persist_error_completion(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            outcome=outcome,
            cleanup=cleanup,
            primary=primary,
            terminal_state=adapter_error_terminal_state,
        )
    else:
        result = _persist_success_completion(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            outcome=outcome,
            cleanup=cleanup,
            primary=primary,
        )
    if result.code in {"completion_refused", "session_reconciliation_required"}:
        return result
    if not persistence._persist_governed_runner_usage(runtime, session, outcome):
        return SessionExecutionResult("runner_usage_evidence_refused")
    return result


def _completion_refusal(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: AdapterInvocationOutcome,
) -> SessionExecutionResult | None:
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
    if not _adapter_outcome_matches_request(outcome, request):
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
        if _adapter_error_diagnostic_bytes(outcome, request=request) is None:
            _audit_session_refusal(
                runtime,
                run_ref=run_ref,
                session=session,
                reason="runner_session_reconciliation_contradiction",
                signal_kind="runner_completion_outcome",
                signal_digest=_signal_digest(outcome),
            )
            return SessionExecutionResult("session_reconciliation_required")
        return _context_writeback_refusal(
            runtime,
            run_ref=run_ref,
            session=session,
            evidence=None,
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
    if not _evidence_matches_current_authority(
        _load(runtime),
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
    return _context_writeback_refusal(
        runtime,
        run_ref=run_ref,
        session=session,
        evidence=evidence,
    )


def _context_writeback_refusal(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    evidence: RunnerResultEvidence | None,
) -> SessionExecutionResult | None:
    refusal = validate_context_writeback(
        runtime,
        session=session,
        evidence=evidence,
    )
    if refusal is None:
        return None
    _audit_session_refusal(
        runtime,
        run_ref=run_ref,
        session=session,
        reason="runner_session_reconciliation_contradiction",
        signal_kind=(
            "runner_completion_outcome"
            if evidence is None
            else "runner_result_evidence"
        ),
        signal_digest=_signal_digest({"context_writeback_refusal": refusal}),
    )
    return SessionExecutionResult("completion_refused")


def _persist_error_completion(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: AdapterErrorResult,
    cleanup: RunnerCleanupResult,
    primary: RunnerSessionCancellationRecord | None,
    terminal_state: str,
) -> SessionExecutionResult:
    raw_diagnostic_bytes = _adapter_error_diagnostic_bytes(
        outcome,
        request=request,
    )
    if raw_diagnostic_bytes is None:
        raise AssertionError("validated adapter error diagnostic disappeared")
    if cleanup.disposition == "orphan_risk":
        return _persist_orphan_risk(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            primary=primary,
            diagnostic={
                "cleanup_disposition": "orphan_risk",
                "adapter_outcome_present": True,
            },
        )
    diagnostic_digest = runtime.cas_store.put_bytes(raw_diagnostic_bytes)
    return _persist_adapter_error(
        runtime,
        run_ref=run_ref,
        session=session,
        outcome=outcome,
        diagnostic_digest=diagnostic_digest,
        cleanup_disposition=cleanup.disposition,
        terminal_state=terminal_state,
        primary=primary,
        redaction_policy=request.redaction_policy,
        request=request,
    )


def _persist_success_completion(
    runtime: OpenRuntimeContext,
    *,
    run_ref: RunRef,
    session: RunnerSessionRecord,
    request: AdapterInvocationRequest,
    outcome: AdapterSuccessResult,
    cleanup: RunnerCleanupResult,
    primary: RunnerSessionCancellationRecord | None,
) -> SessionExecutionResult:
    evidence = runner_evidence_from_adapter_outcome(outcome, request)
    if cleanup.disposition == "orphan_risk":
        return _persist_orphan_risk(
            runtime,
            run_ref=run_ref,
            session=session,
            request=request,
            primary=primary,
            diagnostic={
                "cleanup_disposition": "orphan_risk",
                "adapter_outcome_present": True,
            },
        )
    evidence_digest = runtime.cas_store.put_bytes(
        runner_result_evidence_bytes(evidence)
    )
    diagnostic_digest = runtime.cas_store.put_bytes(
        _completion_diagnostic_bytes(
            request,
            outcome.evidence_construction_diagnostics,
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
        cleanup_disposition=cleanup.disposition,
        redaction_policy_id=outcome.redaction_policy_id,
        primary=primary,
    )
    persisted = _persist_completion_record(
        runtime,
        run_ref,
        session,
        completion,
        event_redaction_policy=request.redaction_policy,
    )
    if persisted is None:
        return SessionExecutionResult("completion_refused")
    return _apply_persisted_completion(runtime, completion)


def _adapter_outcome_matches_request(
    outcome: object,
    request: AdapterInvocationRequest,
) -> bool:
    if not isinstance(outcome, (AdapterSuccessResult, AdapterErrorResult)):
        return False
    if (
        outcome.adapter_id != request.adapter_id
        or outcome.redaction_policy_id != request.redaction_policy.policy_id
        or outcome.dispatch_echo is None
    ):
        return False
    try:
        outcome.dispatch_echo.validate_against(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return True


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
    redaction_policy: RedactionPolicy,
    request: AdapterInvocationRequest | None = None,
) -> SessionExecutionResult:
    refusal = _context_writeback_refusal(
        runtime,
        run_ref=run_ref,
        session=session,
        evidence=None,
    )
    if refusal is not None:
        return refusal
    try:
        raw_diagnostic_bytes = runtime.cas_store.get_bytes(diagnostic_digest)
        dispatch = (
            request.dispatch_envelope
            if request is not None
            else build_dispatch_envelope_for_run(
                state=_load(runtime),
                run_id=run_ref.run_id,
            )
        )
        diagnostic_digest = runtime.cas_store.put_bytes(
            _completion_diagnostic_bytes_for_dispatch(
                dispatch,
                raw_diagnostic_bytes,
                redaction_policy=redaction_policy,
            )
        )
    except (OSError, SubstrateError, TypeError, ValueError):
        return SessionExecutionResult("completion_refused")
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
    if (
        _persist_completion_record(
            runtime,
            run_ref,
            session,
            completion,
            event_redaction_policy=redaction_policy,
        )
        is None
    ):
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
        cancel_requested_at=(None if primary is None else primary.requested_at),
        completed_at=max(time.time_ns(), session.started_at or session.created_at),
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
    *,
    event_redaction_policy: RedactionPolicy | None = None,
) -> RuntimeState | None:
    persisted = _persist_transition(
        runtime,
        RecordRunnerSessionCompletion(
            f"cli:run.session-record-completion:{completion.completion_id}",
            run_ref=run_ref,
            expected_state=session.state,
            completion=completion,
        ),
    )
    if persisted is not None:
        persistence._record_session_event(
            runtime,
            session=persisted.runner_sessions[session.session_id],
            kind="session_terminal",
            observed_at=completion.completed_at,
            payload={
                "terminal_state": completion.terminal_state,
                "exit_kind": completion.exit_kind,
                "cleanup_disposition": completion.cleanup_disposition,
            },
            replay_key=f"session-terminal:{completion.completion_id}",
            redaction_policy=(
                _RUNTIME_SESSION_EVENT_POLICY
                if event_redaction_policy is None
                else event_redaction_policy
            ),
        )
    return persisted


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
        evidence = persistence._load_evidence(runtime, digest)
    except (TypeError, ValueError, json.JSONDecodeError):
        return SessionExecutionResult("completion_refused")
    if runner_result_evidence_digest(
        evidence
    ) != digest or not _evidence_matches_current_authority(
        state,
        session=session,
        evidence=evidence,
        completion=completion,
    ):
        return SessionExecutionResult("completion_refused")
    refusal = _context_writeback_refusal(
        runtime,
        run_ref=run.run_ref,
        session=session,
        evidence=evidence,
    )
    if refusal is not None:
        return refusal
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


def _record_session_event(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    kind: str,
    observed_at: int,
    payload: Mapping[str, object],
    replay_key: str,
    redaction_policy: RedactionPolicy,
) -> None:
    persistence._record_session_event(
        runtime,
        session=session,
        kind=kind,
        observed_at=observed_at,
        payload=payload,
        replay_key=replay_key,
        redaction_policy=redaction_policy,
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
                f"cli:run.session-signal-refusal:{session.session_id}:{signal_kind}:{signal_digest.removeprefix('sha256:')}"
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


def _load(runtime: OpenRuntimeContext) -> RuntimeState:
    return runtime.store.load_runtime_state(runtime.cas_store)


def _adapter_error_diagnostic_bytes(
    outcome: AdapterErrorResult,
    *,
    request: AdapterInvocationRequest,
) -> bytes | None:
    if outcome.redaction_policy_id != request.redaction_policy.policy_id:
        return None
    try:
        redacted = request.redaction_policy.redact_authority_value(outcome.diagnostics)
    except Exception:
        redacted = {"redaction_failed": True}
    if not isinstance(redacted, Mapping):
        return None
    return start_refusal_diagnostic_bytes(replace(outcome, diagnostics=redacted))
