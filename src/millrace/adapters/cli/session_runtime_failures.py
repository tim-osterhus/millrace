"""CLI replay and application of authenticated runner runtime failures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from millrace.adapters.cli.context import (
    OpenRuntimeContext,
    refusal_is_pre_persist,
    transition_context,
)
from millrace.contracts.runtime_failures import (
    runtime_failure_action_for,
    runtime_failure_applied,
    runtime_failure_cleanup_digests,
    runtime_failure_diagnostic_matches,
    runtime_failure_eligible,
    runtime_failure_input,
    runtime_failure_kind,
)
from millrace.contracts.state import (
    RunnerObservationRecord,
    RunnerSessionCompletionRecord,
    RuntimeState,
)
from millrace.contracts.transition import ApplyRunnerRuntimeFailure, TransitionDecision
from millrace.kernel import apply, decide
from millrace.substrate.errors import SubstrateError

_COMMAND = "run.session"


@dataclass(frozen=True, slots=True)
class SessionExecutionResult:
    code: str
    accepted: bool = False
    adapter_error_kind: str | None = None
    observation_refusal_reason: str | None = None
    transition_disposition: str | None = None


def apply_rejected_result_failure(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    completion: RunnerSessionCompletionRecord,
    *,
    daemon_stop_requested: Callable[[], bool] | None,
) -> SessionExecutionResult | None:
    """Keep unmapped rejected candidates on their original refusal path."""
    if not runtime_failure_eligible(state, completion):
        return None
    result = apply_persisted_runtime_failure(
        runtime,
        state,
        completion,
        daemon_stop_requested=daemon_stop_requested,
    )
    return None if result.code == "adapter_failure" else result


def replay_completed_result_receipt(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    completion: RunnerSessionCompletionRecord,
    *,
    daemon_stop_requested: Callable[[], bool] | None,
) -> SessionExecutionResult | None:
    receipt = state.receipts.get(completion.application_input_id)
    if receipt is None:
        return None
    if not receipt.accepted:
        recovered = apply_rejected_result_failure(
            runtime,
            state,
            completion,
            daemon_stop_requested=daemon_stop_requested,
        )
        if recovered is not None:
            return recovered
    return SessionExecutionResult(
        "observation_accepted" if receipt.accepted else "observation_refused",
        accepted=receipt.accepted,
        observation_refusal_reason=receipt.refusal_reason,
        transition_disposition="replayed",
    )


def apply_rejected_result_completion(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    completion: RunnerSessionCompletionRecord,
    decision: TransitionDecision,
    *,
    daemon_stop_requested: Callable[[], bool] | None,
) -> SessionExecutionResult:
    recovered = apply_rejected_result_failure(
        runtime,
        state,
        completion,
        daemon_stop_requested=daemon_stop_requested,
    )
    if recovered is not None:
        return recovered
    reason = (
        "transition_refused" if decision.refusal is None else decision.refusal.reason
    )
    return SessionExecutionResult(
        "runner_session_waiting"
        if reason == "cooldown_wait_pending"
        else "observation_refused",
        observation_refusal_reason=reason,
        transition_disposition=decision.disposition,
    )


def apply_persisted_runtime_failure(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    completion: RunnerSessionCompletionRecord,
    *,
    daemon_stop_requested: Callable[[], bool] | None,
) -> SessionExecutionResult:
    """Apply only a mapped settled failure with authenticated diagnostic bytes."""
    if not runtime_failure_eligible(state, completion):
        return SessionExecutionResult(
            "adapter_failure",
            adapter_error_kind=completion.adapter_error_kind,
        )
    command, refusal = _selected_failure_command(runtime, state, completion)
    if refusal is not None:
        return refusal
    if command is None:
        return SessionExecutionResult(
            "adapter_failure",
            adapter_error_kind=completion.adapter_error_kind,
        )
    return _apply_selected_failure(
        runtime,
        state,
        completion,
        command,
        daemon_stop_requested=daemon_stop_requested,
    )


def _selected_failure_command(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    completion: RunnerSessionCompletionRecord,
) -> tuple[ApplyRunnerRuntimeFailure | None, SessionExecutionResult | None]:
    run = state.runs.get(completion.run_id)
    if run is None:
        return None, SessionExecutionResult("completion_refused")
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None:
        return None, SessionExecutionResult("completion_refused")
    action = runtime_failure_action_for(
        admitted.selected_plan,
        run.stage_kind_id,
        run.runner_binding_id,
        runtime_failure_kind(state, completion) or "",
    )
    if action is None:
        return None, None
    try:
        command = runtime_failure_input(state, completion)
        diagnostic_payload = runtime.cas_store.get_bytes(completion.diagnostic_digest)
        cleanup_payloads = {
            digest: runtime.cas_store.get_bytes(digest)
            for digest in runtime_failure_cleanup_digests(state, completion)
        }
    except (OSError, SubstrateError, TypeError, ValueError):
        return None, SessionExecutionResult("completion_refused")
    evidence_payload = None
    if completion.runner_result_evidence_digest is not None:
        try:
            evidence_payload = runtime.cas_store.get_bytes(
                completion.runner_result_evidence_digest
            )
        except (OSError, SubstrateError, TypeError, ValueError):
            return None, SessionExecutionResult("completion_refused")
    if not runtime_failure_diagnostic_matches(
        state,
        completion,
        diagnostic_payload,
        evidence_payload=evidence_payload,
        cleanup_payloads=cleanup_payloads,
    ):
        return None, SessionExecutionResult("completion_refused")
    return command, None


def _apply_selected_failure(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    completion: RunnerSessionCompletionRecord,
    command: ApplyRunnerRuntimeFailure,
    *,
    daemon_stop_requested: Callable[[], bool] | None,
) -> SessionExecutionResult:
    run = state.runs[completion.run_id]
    receipt = state.receipts.get(command.input_id)
    already_applied = runtime_failure_applied(state, run.run_ref.run_id)
    if receipt is not None and not already_applied:
        return SessionExecutionResult("completion_refused")
    if not already_applied and daemon_stop_requested is not None:
        if daemon_stop_requested():
            return SessionExecutionResult(
                "runner_session_waiting",
                adapter_error_kind=completion.adapter_error_kind,
            )
    decision = decide(
        state,
        command,
        transition_context(command=_COMMAND, input_id_value=command.input_id),
    )
    if not decision.accepted:
        refusal_reason = (
            "transition_refused"
            if decision.refusal is None
            else decision.refusal.reason
        )
        if not refusal_is_pre_persist(decision):
            next_state = apply(state, decision)
            runtime.store.persist_runtime_state(next_state, runtime.cas_store)
        return SessionExecutionResult(
            "runner_session_waiting"
            if refusal_reason == "cooldown_wait_pending"
            else "observation_refused",
            adapter_error_kind=completion.adapter_error_kind,
            observation_refusal_reason=refusal_reason,
            transition_disposition=decision.disposition,
        )
    next_state = apply(state, decision)
    runtime.store.persist_runtime_state(next_state, runtime.cas_store)
    return SessionExecutionResult(
        "runtime_failure_recovery_applied",
        accepted=True,
        adapter_error_kind=completion.adapter_error_kind,
        transition_disposition=decision.disposition,
    )


def resume_persisted_completion(
    runtime: OpenRuntimeContext,
    completion: RunnerSessionCompletionRecord,
    *,
    explicit_retry_intent: bool,
    daemon_stop_requested: Callable[[], bool] | None,
    apply_completion: Callable[..., SessionExecutionResult],
) -> SessionExecutionResult | None:
    """Handle an existing terminal completion before session restart logic."""
    if completion.terminal_state == "completed":
        return apply_completion(
            runtime,
            completion,
            daemon_stop_requested=daemon_stop_requested,
        )
    if completion.terminal_state == "lost":
        return SessionExecutionResult("runner_session_orphan_risk")
    result = apply_completion(
        runtime,
        completion,
        daemon_stop_requested=daemon_stop_requested,
    )
    if result.code != "adapter_failure" or not explicit_retry_intent:
        return result
    return None


def runtime_failure_replay_candidate(
    state: RuntimeState,
    run_id: str,
    completion: RunnerSessionCompletionRecord,
) -> bool:
    """Select only unsettled mapped failure completions for daemon replay."""
    if not runtime_failure_eligible(state, completion):
        return False
    run = state.runs.get(run_id)
    if run is None or run.current_session_id != completion.session_id:
        return False
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None:
        return False
    if (
        runtime_failure_action_for(
            admitted.selected_plan,
            run.stage_kind_id,
            run.runner_binding_id,
            runtime_failure_kind(state, completion) or "",
        )
        is None
    ):
        return False
    # Accepted failures leave the replay lane so the recovery run can proceed.
    return not runtime_failure_applied(state, run_id) and not any(
        isinstance(observation, RunnerObservationRecord)
        and observation.run_id == run_id
        for observation in state.runner_observations.values()
    )
