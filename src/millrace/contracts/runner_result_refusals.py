"""Authenticate the immutable evidence and audit chain for rejected results."""

from __future__ import annotations

from millrace.contracts.runner import RunnerResultEvidence
from millrace.contracts.selected_plan_lookups import (
    terminal_action_for,
    terminal_outcome_for,
)
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import RunnerResultObserved, input_payload_digest


def runner_result_refusal_chain(
    state: RuntimeState,
    *,
    run_id: str,
    session_id: str,
    application_input_id: str,
    evidence: RunnerResultEvidence | None,
) -> tuple[bool, str | None]:
    session = state.runner_sessions.get(session_id)
    completion = state.runner_session_completions.get(session_id)
    run = None if session is None else state.runs.get(run_id)
    if (
        session is None
        or completion is None
        or run is None
        or run.current_session_id != session_id
        or completion.application_input_id != application_input_id
        or completion.run_id != run_id
    ):
        return False, None
    receipt = state.receipts.get(application_input_id)
    if (
        receipt is None
        or receipt.receipt_ref.input_id != application_input_id
        or receipt.accepted
        or receipt.refusal_reason is None
    ):
        return False, None
    transitions = tuple(
        transition
        for transition in state.transitions
        if transition.input_id == application_input_id
    )
    if len(transitions) != 1:
        return False, None
    transition = transitions[0]
    if (
        receipt.transition_id != transition.record_id
        or transition.input_kind != RunnerResultObserved.input_kind
        or transition.input_family != "workflow_observation"
        or transition.accepted
    ):
        return False, None

    if evidence is not None:
        if (
            evidence.run_id != run_id
            or evidence.session_id != session_id
            or evidence.dispatch_generation != session.dispatch_generation
            or evidence.session_fencing_token != session.session_fencing_token
        ):
            return False, None
        try:
            expected_digest = input_payload_digest(
                RunnerResultObserved(
                    application_input_id,
                    run_id=run_id,
                    payload=evidence.payload(),
                    observed_at=completion.completed_at // 1_000_000_000,
                )
            )
        except (RecursionError, TypeError, ValueError):
            return False, None
        if receipt.receipt_ref.input_payload_digest != expected_digest:
            return False, None

    refusals = tuple(
        refusal
        for refusal in state.refusals
        if refusal.input_id == application_input_id
    )
    traces = tuple(
        trace for trace in state.traces if trace.input_id == application_input_id
    )
    events = tuple(
        event
        for event in state.governance_events
        if event.input_id == application_input_id
    )
    if len(refusals) != 1 or len(traces) != 1 or len(events) != 1:
        return False, None
    refusal = refusals[0]
    trace = traces[0]
    event = events[0]
    if (
        refusal.record_id != f"{transition.record_id}:refusal"
        or refusal.input_kind != transition.input_kind
        or refusal.input_family != transition.input_family
        or refusal.reason != receipt.refusal_reason
        or trace.record_id != f"{transition.record_id}:trace"
        or event.record_id != f"{transition.record_id}:governance"
    ):
        return False, None

    expected_plan_fingerprint = run.run_ref.plan_ref.authority_fingerprint
    expected_action_id = None
    expected_authority_source = None
    if evidence is not None:
        admitted = state.admitted_plans.get(expected_plan_fingerprint)
        if admitted is None:
            return False, None
        outcome = terminal_outcome_for(
            admitted.selected_plan,
            str(run.stage_kind_id),
            evidence.marker,
        )
        action = (
            None
            if outcome is None
            else terminal_action_for(
                admitted.selected_plan,
                str(run.stage_kind_id),
                str(outcome.id),
            )
        )
        if action is not None:
            expected_action_id = action.id
            expected_authority_source = "terminal_action"

    common_audit_fields = (
        application_input_id,
        transition.input_kind,
        transition.input_family,
        "refused",
        expected_plan_fingerprint,
        run.work_item_id,
        run_id,
        receipt.refusal_reason,
    )
    trace_common = (
        trace.input_id,
        trace.input_kind,
        trace.input_family,
        trace.disposition,
        trace.plan_fingerprint,
        trace.work_item_id,
        trace.run_id,
        trace.refusal_reason,
    )
    event_common = (
        event.input_id,
        event.input_kind,
        event.input_family,
        event.disposition,
        event.plan_fingerprint,
        event.work_item_id,
        event.run_id,
        event.refusal_reason,
    )
    if trace_common != common_audit_fields or event_common != common_audit_fields:
        return False, None
    if (
        trace.action_id != event.action_id
        or trace.authority_source != event.authority_source
    ):
        return False, None
    if evidence is not None and (
        trace.action_id != expected_action_id
        or trace.authority_source != expected_authority_source
    ):
        return False, None
    return True, receipt.refusal_reason


__all__ = ("runner_result_refusal_chain",)
