"""Operator-wait decision construction.

This module owns direct local-operator resolution of declared operator waits.
It builds transition decisions but leaves idempotency, generic receipt/audit
construction, and mutation application to the surrounding kernel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from millrace.contracts.compiled_plan import OperatorWaitDeclaration
from millrace.contracts.state import (
    Activation,
    ClosedWorkItemRecord,
    OperatorWaitRecord,
    RuntimeState,
    WorkItem,
    WorkItemRef,
)
from millrace.contracts.transition import (
    CloseWorkItem,
    CreateActivation,
    CreateWorkItem,
    OperatorCloseWait,
    OperatorResumeWait,
    OperatorReviseWait,
    RecordOperatorWait,
    TransitionContext,
    TransitionDecision,
    operator_payload_digest,
)
from millrace.kernel.lookups import artifact_schema_for, operator_wait_for_action
from millrace.kernel.schema import validate_schema

DecisionFactory = Callable[..., TransitionDecision]

EMPTY_OPERATOR_PAYLOAD_DIGEST = operator_payload_digest({})


def decide_operator_resume_wait(
    state: RuntimeState,
    transition_input: OperatorResumeWait,
    context: TransitionContext,
    digest: str,
    *,
    accept_decision: DecisionFactory,
    refuse_decision: DecisionFactory,
) -> TransitionDecision:
    """Resolve an active operator wait by reactivating the recorded source item."""
    resolved = _operator_wait_context(
        state,
        transition_input=transition_input,
        resolution_kind="resume_recorded_source",
    )
    if isinstance(resolved, str):
        return refuse_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=resolved,
        )
    _operator_wait, wait, work_item = resolved
    if work_item.ref.work_item_id in state.closed_work_items:
        return _operator_wait_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="work_item_closed",
            wait=wait,
            refuse_decision=refuse_decision,
        )
    target_activation = Activation(
        activation_id=context.activation_id,
        work_item_id=wait.source_work_item_id,
        lineage_id=wait.lineage_id,
        plan_ref=wait.selected_plan_ref,
        queue_family_id=wait.source_queue_family_id,
        graph_node_id=wait.source_graph_node_id,
        stage_kind_id=wait.source_stage_kind_id,
        runner_binding_id=wait.source_runner_binding_id,
        generation=work_item.ref.generation,
        created_by_input_id=transition_input.input_id,
    )
    resolved_wait = _resolved_operator_wait_record(
        wait,
        transition_input=transition_input,
        digest=digest,
        resolution_kind="resume_recorded_source",
        target_activation_id=target_activation.activation_id,
        payload_digest=EMPTY_OPERATOR_PAYLOAD_DIGEST,
    )
    return accept_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            RecordOperatorWait(record_id=resolved_wait.wait_id, record=resolved_wait),
            CreateActivation(target_activation),
        ),
        expected_plan_fingerprint=wait.selected_plan_fingerprint,
        expected_work_item_generations={
            work_item.ref.work_item_id: work_item.ref.generation
        },
        expected_work_item_plan_refs={
            work_item.ref.work_item_id: work_item.ref.plan_ref
        },
        expected_work_item_open=(work_item.ref.work_item_id,),
        event_plan_fingerprint=wait.selected_plan_fingerprint,
        event_work_item_id=wait.source_work_item_id,
        event_run_id=wait.source_run_id,
        event_action_id=wait.source_action_id,
        event_authority_source="operator_wait",
    )


def decide_operator_close_wait(
    state: RuntimeState,
    transition_input: OperatorCloseWait,
    context: TransitionContext,
    digest: str,
    *,
    accept_decision: DecisionFactory,
    refuse_decision: DecisionFactory,
) -> TransitionDecision:
    """Resolve an active operator wait by closing the recorded source item."""
    resolved = _operator_wait_context(
        state,
        transition_input=transition_input,
        resolution_kind="close_recorded_source",
    )
    if isinstance(resolved, str):
        return refuse_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=resolved,
        )
    _operator_wait, wait, work_item = resolved
    close_mutations = _operator_wait_close_source_mutations(
        state,
        context=context,
        wait=wait,
        input_id=transition_input.input_id,
    )
    resolved_wait = _resolved_operator_wait_record(
        wait,
        transition_input=transition_input,
        digest=digest,
        resolution_kind="close_recorded_source",
        closed_work_item_ids=(wait.source_work_item_id,),
        payload_digest=EMPTY_OPERATOR_PAYLOAD_DIGEST,
    )
    expected_open = (
        (work_item.ref.work_item_id,)
        if work_item.ref.work_item_id not in state.closed_work_items
        else ()
    )
    return accept_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            RecordOperatorWait(record_id=resolved_wait.wait_id, record=resolved_wait),
            *close_mutations,
        ),
        expected_plan_fingerprint=wait.selected_plan_fingerprint,
        expected_work_item_generations={
            work_item.ref.work_item_id: work_item.ref.generation
        },
        expected_work_item_plan_refs={
            work_item.ref.work_item_id: work_item.ref.plan_ref
        },
        expected_work_item_open=expected_open,
        event_plan_fingerprint=wait.selected_plan_fingerprint,
        event_work_item_id=wait.source_work_item_id,
        event_run_id=wait.source_run_id,
        event_action_id=wait.source_action_id,
        event_authority_source="operator_wait",
    )


def decide_operator_revise_wait(
    state: RuntimeState,
    transition_input: OperatorReviseWait,
    context: TransitionContext,
    digest: str,
    *,
    accept_decision: DecisionFactory,
    refuse_decision: DecisionFactory,
) -> TransitionDecision:
    """Resolve an active operator wait by creating declared replacement work."""
    resolved = _operator_wait_context(
        state,
        transition_input=transition_input,
        resolution_kind="revise_recorded_source",
    )
    if isinstance(resolved, str):
        return refuse_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=resolved,
        )
    operator_wait, wait, source_work_item = resolved
    if (
        operator_wait.payload_schema_id is None
        or operator_wait.target_queue_family_id is None
        or operator_wait.target_stage_kind_id is None
        or operator_wait.target_graph_node_id is None
        or operator_wait.target_runner_binding_id is None
    ):
        return _operator_wait_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_operator_wait",
            wait=wait,
            refuse_decision=refuse_decision,
        )
    admitted = state.admitted_plans[wait.selected_plan_ref.authority_fingerprint]
    artifact_schema = artifact_schema_for(
        admitted.selected_plan,
        str(operator_wait.payload_schema_id),
    )
    if artifact_schema is None:
        return _operator_wait_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_operator_wait",
            wait=wait,
            refuse_decision=refuse_decision,
        )
    validation = validate_schema(artifact_schema.schema, transition_input.payload)
    if not validation.accepted:
        return _operator_wait_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_operator_wait_payload_schema",
            wait=wait,
            refuse_decision=refuse_decision,
        )
    target_work_item_ref = WorkItemRef(
        work_item_id=context.work_item_id,
        plan_ref=wait.selected_plan_ref,
        generation=0,
    )
    target_work_item = WorkItem(
        ref=target_work_item_ref,
        queue_family_id=operator_wait.target_queue_family_id,
        payload=transition_input.payload,
        lineage_id=wait.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    target_activation = Activation(
        activation_id=context.activation_id,
        work_item_id=target_work_item.ref.work_item_id,
        lineage_id=wait.lineage_id,
        plan_ref=wait.selected_plan_ref,
        queue_family_id=operator_wait.target_queue_family_id,
        graph_node_id=operator_wait.target_graph_node_id,
        stage_kind_id=operator_wait.target_stage_kind_id,
        runner_binding_id=operator_wait.target_runner_binding_id,
        generation=0,
        created_by_input_id=transition_input.input_id,
    )
    close_mutations = _operator_wait_close_source_mutations(
        state,
        context=context,
        wait=wait,
        input_id=transition_input.input_id,
    )
    resolved_wait = _resolved_operator_wait_record(
        wait,
        transition_input=transition_input,
        digest=digest,
        resolution_kind="revise_recorded_source",
        target_work_item_id=target_work_item.ref.work_item_id,
        target_activation_id=target_activation.activation_id,
        closed_work_item_ids=(wait.source_work_item_id,),
        payload_digest=operator_payload_digest(transition_input.payload),
        payload_reference=f"work_item:{target_work_item.ref.work_item_id}:payload",
    )
    expected_open = (
        (source_work_item.ref.work_item_id,)
        if source_work_item.ref.work_item_id not in state.closed_work_items
        else ()
    )
    return accept_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            RecordOperatorWait(record_id=resolved_wait.wait_id, record=resolved_wait),
            *close_mutations,
            CreateWorkItem(target_work_item),
            CreateActivation(target_activation),
        ),
        expected_plan_fingerprint=wait.selected_plan_fingerprint,
        expected_work_item_generations={
            source_work_item.ref.work_item_id: source_work_item.ref.generation
        },
        expected_work_item_plan_refs={
            source_work_item.ref.work_item_id: source_work_item.ref.plan_ref
        },
        expected_work_item_open=expected_open,
        event_plan_fingerprint=wait.selected_plan_fingerprint,
        event_work_item_id=target_work_item.ref.work_item_id,
        event_run_id=wait.source_run_id,
        event_action_id=wait.source_action_id,
        event_authority_source="operator_wait",
    )


def _operator_wait_context(
    state: RuntimeState,
    *,
    transition_input: OperatorResumeWait | OperatorCloseWait | OperatorReviseWait,
    resolution_kind: str,
) -> tuple[OperatorWaitDeclaration, OperatorWaitRecord, WorkItem] | str:
    if transition_input.actor_kind != "local_operator":
        return "invalid_actor_kind"
    if resolution_kind != "revise_recorded_source" and transition_input.payload:
        return "payload_forbidden"
    admitted = state.admitted_plans.get(
        transition_input.selected_plan_ref.authority_fingerprint
    )
    if admitted is None or admitted.plan_ref != transition_input.selected_plan_ref:
        return "unknown_plan_ref"
    wait = state.operator_waits.get(transition_input.wait_id)
    if wait is None:
        return "unknown_operator_wait"
    if wait.selected_plan_ref != transition_input.selected_plan_ref:
        return "selected_plan_ref_mismatch"
    if wait.lineage_id != transition_input.lineage_id:
        return "operator_wait_lineage_mismatch"
    if wait.status != "active":
        return "operator_wait_not_active"
    operator_wait = operator_wait_for_action(
        admitted.selected_plan,
        str(wait.source_action_id),
    )
    if operator_wait is None or operator_wait.id != wait.operator_wait_id:
        return "invalid_operator_wait"
    if operator_wait.actor_kind != "local_operator":
        return "invalid_actor_kind"
    if resolution_kind not in set(operator_wait.allowed_resolution_kinds):
        return "invalid_operator_wait_resolution"
    work_item = state.work_items.get(wait.source_work_item_id)
    if work_item is None or work_item.lineage_id != wait.lineage_id:
        return "missing_work_item"
    if work_item.ref.plan_ref != wait.selected_plan_ref:
        return "selected_plan_ref_mismatch"
    return operator_wait, wait, work_item


def _operator_wait_refused_decision(
    *,
    transition_input: OperatorResumeWait | OperatorCloseWait | OperatorReviseWait,
    context: TransitionContext,
    digest: str,
    reason: str,
    wait: OperatorWaitRecord,
    refuse_decision: DecisionFactory,
) -> TransitionDecision:
    return refuse_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=reason,
        event_plan_fingerprint=wait.selected_plan_fingerprint,
        event_work_item_id=wait.source_work_item_id,
        event_run_id=wait.source_run_id,
        event_action_id=wait.source_action_id,
        event_authority_source="operator_wait",
    )


def _resolved_operator_wait_record(
    wait: OperatorWaitRecord,
    *,
    transition_input: OperatorResumeWait | OperatorCloseWait | OperatorReviseWait,
    digest: str,
    resolution_kind: str,
    target_work_item_id: str | None = None,
    target_activation_id: str | None = None,
    closed_work_item_ids: tuple[str, ...] = (),
    payload_digest: str | None = None,
    payload_reference: str | None = None,
) -> OperatorWaitRecord:
    return replace(
        wait,
        status="resolved",
        resolved_input_id=transition_input.input_id,
        resolved_input_payload_digest=digest,
        actor_id=transition_input.actor_id,
        actor_kind=transition_input.actor_kind,
        resolution_kind=resolution_kind,
        target_work_item_id=target_work_item_id,
        target_activation_id=target_activation_id,
        closed_work_item_ids=closed_work_item_ids,
        payload_digest=payload_digest,
        payload_reference=payload_reference,
    )


def _operator_wait_close_source_mutations(
    state: RuntimeState,
    *,
    context: TransitionContext,
    wait: OperatorWaitRecord,
    input_id: str,
) -> tuple[CloseWorkItem, ...]:
    if wait.source_work_item_id in state.closed_work_items:
        return ()
    close_record = ClosedWorkItemRecord(
        record_id=f"{context.transition_id}:close:{wait.source_work_item_id}",
        work_item_id=wait.source_work_item_id,
        source_run_id=wait.source_run_id,
        action_id=wait.source_action_id,
        created_by_input_id=input_id,
        close_kind="terminal_action",
    )
    return (CloseWorkItem(record_id=close_record.record_id, record=close_record),)


__all__ = (
    "decide_operator_close_wait",
    "decide_operator_resume_wait",
    "decide_operator_revise_wait",
)
