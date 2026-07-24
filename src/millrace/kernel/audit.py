"""Governance event and trace construction for kernel transitions.

This module owns audit record construction only. It must not construct complete
transition decisions or apply runtime-state mutations.
"""

from __future__ import annotations

from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import ActionId
from millrace.contracts.state import (
    GovernanceEventRecord,
    RuntimeState,
    TraceRecord,
    TransitionRecord,
)
from millrace.contracts.transition import (
    RunnerResultObserved,
    TransitionContext,
    TransitionInput,
    input_family,
    input_kind,
)


def transition_record(
    *,
    transition_input: TransitionInput,
    context: TransitionContext,
    accepted: bool,
) -> TransitionRecord:
    return TransitionRecord(
        record_id=context.transition_id,
        input_id=transition_input.input_id,
        input_kind=input_kind(transition_input),
        input_family=input_family(transition_input),
        accepted=accepted,
    )


def event_and_trace_records(
    *,
    transition_input: TransitionInput,
    context: TransitionContext,
    disposition: str,
    plan_fingerprint: AuthorityFingerprint | None,
    work_item_id: str | None,
    run_id: str | None,
    action_id: ActionId | None,
    authority_source: str | None,
    refusal_reason: str | None,
) -> tuple[GovernanceEventRecord, TraceRecord]:
    event = GovernanceEventRecord(
        record_id=f"{context.transition_id}:governance",
        input_id=transition_input.input_id,
        input_kind=input_kind(transition_input),
        input_family=input_family(transition_input),
        disposition=disposition,
        plan_fingerprint=plan_fingerprint,
        work_item_id=work_item_id,
        run_id=run_id,
        action_id=action_id,
        authority_source=authority_source,
        refusal_reason=refusal_reason,
    )
    trace = TraceRecord(
        record_id=f"{context.transition_id}:trace",
        input_id=transition_input.input_id,
        input_kind=input_kind(transition_input),
        input_family=input_family(transition_input),
        disposition=disposition,
        plan_fingerprint=plan_fingerprint,
        work_item_id=work_item_id,
        run_id=run_id,
        action_id=action_id,
        authority_source=authority_source,
        refusal_reason=refusal_reason,
    )
    return event, trace


def idempotency_conflict_event_context(
    state: RuntimeState,
    transition_input: TransitionInput,
) -> tuple[AuthorityFingerprint | None, str | None, str | None]:
    empty = (None, None, None)
    if not isinstance(transition_input, RunnerResultObserved):
        return empty
    run = state.runs.get(transition_input.run_id)
    if run is None:
        return empty
    work_item = state.work_items.get(run.work_item_id)
    return (
        run.run_ref.plan_ref.authority_fingerprint,
        (
            work_item.ref.work_item_id if work_item is not None else run.work_item_id
        ),
        run.run_ref.run_id,
    )


__all__ = (
    "event_and_trace_records",
    "idempotency_conflict_event_context",
    "transition_record",
)
