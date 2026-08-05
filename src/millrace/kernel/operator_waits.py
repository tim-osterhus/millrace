"""Operator-wait decision construction.

This module owns direct local-operator resolution of declared operator waits.
It builds transition decisions but leaves idempotency, generic receipt/audit
construction, and mutation application to the surrounding kernel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    OperatorWaitDeclaration,
    SelectedCompiledPlan,
)
from millrace.contracts.state import (
    Activation,
    ClosedWorkItemRecord,
    GovernanceEventRecord,
    OperatorWaitRecord,
    RunRecord,
    RuntimeState,
    TraceRecord,
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
from millrace.kernel.fanout_policy import PolicyAssessment
from millrace.kernel.lookups import artifact_schema_for, operator_wait_for_action
from millrace.kernel.observation_policy import (
    AuthenticatedArtifactProvenance,
    authenticate_artifact_provenance,
)
from millrace.kernel.schema import validate_schema

DecisionFactory = Callable[..., TransitionDecision]

EMPTY_OPERATOR_PAYLOAD_DIGEST = operator_payload_digest({})


@dataclass(frozen=True, slots=True)
class SelectedWaitEvidenceProjection:
    wait_id: str
    operator_wait_id: str
    lineage_id: str
    source_artifact_id: str
    source_artifact_schema_id: str
    source_artifact_digest: str
    source_artifact_payload: Mapping[str, AuthorityValue]
    source_action_id: str
    source_run_id: str
    source_work_item_id: str


def project_selected_wait_evidence_for_target(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> SelectedWaitEvidenceProjection | PolicyAssessment | None:
    target_waits = tuple(
        wait
        for wait in state.operator_waits.values()
        if wait.target_work_item_id == work_item.ref.work_item_id
        or wait.target_activation_id == activation.activation_id
    )
    if not target_waits:
        if any(
            wait.resolved_input_id
            in {
                work_item.created_by_input_id,
                activation.created_by_input_id,
            }
            for wait in state.operator_waits.values()
            if wait.resolved_input_id is not None
        ):
            return _wait_projection_refusal("missing_target_owner")
        return None
    if len(target_waits) != 1:
        return _wait_projection_refusal("target_owner")
    wait = target_waits[0]
    selected_waits = tuple(
        declaration
        for declaration in selected_plan.operator_waits
        if declaration.id == wait.operator_wait_id
        and wait.source_action_id in declaration.source_action_ids
    )
    if len(selected_waits) != 1:
        return _wait_projection_refusal("selected_declaration")
    selected_wait = selected_waits[0]
    if wait.resolution_kind == "resume_recorded_source":
        if (
            wait.status != "resolved"
            or wait.target_work_item_id is not None
            or wait.target_activation_id != activation.activation_id
            or activation.work_item_id != wait.source_work_item_id
            or work_item.ref.work_item_id != wait.source_work_item_id
            or run.work_item_id != wait.source_work_item_id
            or wait.selected_plan_ref != run.run_ref.plan_ref
            or work_item.ref.plan_ref != run.run_ref.plan_ref
            or activation.plan_ref != run.run_ref.plan_ref
            or wait.lineage_id != work_item.lineage_id
            or wait.lineage_id != activation.lineage_id
            or activation.created_by_input_id != wait.resolved_input_id
            or activation.queue_family_id != wait.source_queue_family_id
            or activation.stage_kind_id != wait.source_stage_kind_id
            or activation.graph_node_id != wait.source_graph_node_id
            or activation.runner_binding_id != wait.source_runner_binding_id
        ):
            return _wait_projection_refusal("resume_target_authority")
        return None
    if (
        wait.target_work_item_id != work_item.ref.work_item_id
        or wait.target_activation_id != activation.activation_id
    ):
        return _wait_projection_refusal("target_owner")
    if selected_wait.project_source_artifact is False:
        return None
    if selected_wait.project_source_artifact is not True:
        return _wait_projection_refusal("projection_authority")
    if (
        wait.status != "resolved"
        or wait.resolution_kind != "revise_recorded_source"
        or "revise_recorded_source" not in selected_wait.allowed_resolution_kinds
        or wait.selected_plan_ref != run.run_ref.plan_ref
        or wait.selected_plan_fingerprint != run.run_ref.plan_ref.authority_fingerprint
        or work_item.ref.plan_ref != run.run_ref.plan_ref
        or activation.plan_ref != run.run_ref.plan_ref
        or wait.lineage_id != work_item.lineage_id
        or wait.lineage_id != activation.lineage_id
        or wait.resolved_input_id is None
        or not _resolved_revise_wait_authority_valid(state, wait, work_item)
        or work_item.created_by_input_id != wait.resolved_input_id
        or activation.created_by_input_id != wait.resolved_input_id
        or work_item.ref.work_item_id != run.work_item_id
        or activation.activation_id != run.activation_id
        or activation.work_item_id != work_item.ref.work_item_id
        or work_item.queue_family_id != selected_wait.target_queue_family_id
        or activation.queue_family_id != selected_wait.target_queue_family_id
        or activation.stage_kind_id != selected_wait.target_stage_kind_id
        or activation.graph_node_id != selected_wait.target_graph_node_id
        or activation.runner_binding_id != selected_wait.target_runner_binding_id
    ):
        return _wait_projection_refusal("target_authority")
    artifact = state.artifacts.get(wait.source_artifact_id or "")
    if (
        artifact is None
        or artifact.artifact_id != wait.source_artifact_id
        or sum(
            candidate.artifact_id == wait.source_artifact_id
            for candidate in state.artifacts.values()
        )
        != 1
    ):
        return _wait_projection_refusal("source_artifact")
    authenticated = authenticate_artifact_provenance(state, artifact)
    if not isinstance(authenticated, AuthenticatedArtifactProvenance):
        return _wait_projection_refusal("source_artifact_provenance")
    source = authenticated.observation
    source_action = next(
        (
            action
            for action in selected_plan.terminal_actions
            if action.id == wait.source_action_id
        ),
        None,
    )
    target_stage = next(
        (
            stage
            for stage in selected_plan.stage_kinds
            if stage.id == selected_wait.target_stage_kind_id
        ),
        None,
    )
    if (
        source.run.run_ref.plan_ref != run.run_ref.plan_ref
        or source_action is None
        or source_action.action_kind != "operator_wait"
        or source_action.artifact_schema_id is None
        or artifact.schema_id != source_action.artifact_schema_id
        or target_stage is None
        or artifact.schema_id not in target_stage.artifact_schema_ids
        or artifact.work_item_id != wait.source_work_item_id
        or artifact.source_run_id != wait.source_run_id
        or artifact.source_action_id != wait.source_action_id
        or artifact.source_stage_kind_id != wait.source_stage_kind_id
        or artifact.source_graph_node_id != wait.source_graph_node_id
        or source.work_item.ref.work_item_id != wait.source_work_item_id
        or source.run.run_ref.run_id != wait.source_run_id
        or source.activation.activation_id != wait.source_activation_id
        or source.work_item.lineage_id != wait.lineage_id
        or source.work_item.queue_family_id != wait.source_queue_family_id
        or source.activation.queue_family_id != wait.source_queue_family_id
        or source.run.runner_binding_id != wait.source_runner_binding_id
        or source.activation.runner_binding_id != wait.source_runner_binding_id
    ):
        return _wait_projection_refusal("source_authority")
    return SelectedWaitEvidenceProjection(
        wait_id=wait.wait_id,
        operator_wait_id=str(wait.operator_wait_id),
        lineage_id=wait.lineage_id,
        source_artifact_id=artifact.artifact_id,
        source_artifact_schema_id=str(artifact.schema_id),
        source_artifact_digest=artifact.payload_digest,
        source_artifact_payload=artifact.payload,
        source_action_id=str(artifact.source_action_id),
        source_run_id=artifact.source_run_id,
        source_work_item_id=artifact.work_item_id,
    )


def _wait_projection_refusal(detail: str) -> PolicyAssessment:
    return PolicyAssessment(
        "partial_or_corrupt",
        reason_code="operator_wait_evidence_refused",
        detail=detail,
    )


def _resolved_revise_wait_authority_valid(
    state: RuntimeState,
    wait: OperatorWaitRecord,
    work_item: WorkItem,
) -> bool:
    input_id = wait.resolved_input_id
    if input_id is None:
        return False
    receipt = state.receipts.get(input_id)
    transitions = tuple(
        transition
        for transition in state.transitions
        if transition.input_id == input_id
    )
    events = tuple(
        event for event in state.governance_events if event.input_id == input_id
    )
    traces = tuple(trace for trace in state.traces if trace.input_id == input_id)
    if (
        receipt is None
        or receipt.receipt_ref.input_id != input_id
        or receipt.receipt_ref.input_payload_digest
        != wait.resolved_input_payload_digest
        or not receipt.accepted
        or receipt.refusal_reason is not None
        or len(transitions) != 1
        or len(events) != 1
        or len(traces) != 1
        or receipt.transition_id != transitions[0].record_id
        or not transitions[0].accepted
        or transitions[0].input_kind != OperatorReviseWait.input_kind
        or transitions[0].input_family != "workflow_operator_command"
        or wait.payload_digest != operator_payload_digest(work_item.payload)
        or wait.payload_reference != f"work_item:{work_item.ref.work_item_id}:payload"
    ):
        return False
    expected_audit = (
        input_id,
        OperatorReviseWait.input_kind,
        "workflow_operator_command",
        "accepted",
        wait.selected_plan_fingerprint,
        work_item.ref.work_item_id,
        wait.source_run_id,
        wait.source_action_id,
        "operator_wait",
        None,
    )
    return _operator_wait_resolution_audit_matches(
        events[0],
        expected_record_id=f"{transitions[0].record_id}:governance",
        expected_audit=expected_audit,
    ) and _operator_wait_resolution_audit_matches(
        traces[0],
        expected_record_id=f"{transitions[0].record_id}:trace",
        expected_audit=expected_audit,
    )


def _operator_wait_resolution_audit_matches(
    record: GovernanceEventRecord | TraceRecord,
    *,
    expected_record_id: str,
    expected_audit: tuple[object, ...],
) -> bool:
    return (
        record.record_id == expected_record_id
        and (
            record.input_id,
            record.input_kind,
            record.input_family,
            record.disposition,
            record.plan_fingerprint,
            record.work_item_id,
            record.run_id,
            record.action_id,
            record.authority_source,
            record.refusal_reason,
        )
        == expected_audit
    )


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
    "SelectedWaitEvidenceProjection",
    "decide_operator_close_wait",
    "decide_operator_resume_wait",
    "decide_operator_revise_wait",
    "project_selected_wait_evidence_for_target",
)
