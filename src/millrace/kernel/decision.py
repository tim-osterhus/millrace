"""Transition input decision construction.

This module owns transition input dispatch, idempotency/refusal handling, and
complete `TransitionDecision` construction from transition inputs. It must not
apply mutations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    AuthorityValue,
    CapabilityDeclaration,
    CompletionBehaviorDeclaration,
    CounterDeclaration,
    ExternalEnqueueRouteDeclaration,
    FanoutDeclaration,
    GeneratedWorkRouteDeclaration,
    InterventionOptionDeclaration,
    OperatorWaitDeclaration,
    RecoveryPolicyDeclaration,
    RemediationPolicyDeclaration,
    SelectedCompiledPlan,
    StageKindDeclaration,
    TerminalActionDeclaration,
    runner_component_authority_refusal,
    verify_authority_fingerprint,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import (
    ActionId,
    ArtifactSchemaId,
    PartitionId,
    QueueFamilyId,
    RemediationPolicyId,
    RunnerBindingId,
    StageKindId,
    WaitStateId,
)
from millrace.contracts.operator_waits import (
    _SUPPORTED_OPERATOR_WAIT_RESOLUTION_KINDS,
    _operator_wait_audit_metadata_requirements,
)
from millrace.contracts.runner import (
    RunnerResultEvidence,
    runner_result_evidence_from_payload,
)
from millrace.contracts.state import (
    Activation,
    ActivationRouteRecord,
    ArtifactRecord,
    ClosedWorkItemRecord,
    ClosureBlockedRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    ClosureTerminalRecord,
    CooldownWaitRecord,
    EffectProposalRecord,
    EffectReconciliationRecord,
    ExternalEnqueueRoute,
    FanoutRecord,
    InputReceipt,
    InputReceiptRef,
    LineageQuarantineRecord,
    OperatorInterventionRecord,
    PlanRef,
    RecoveryAttemptRecord,
    RemediationWorkRecord,
    RunRecord,
    RunRef,
    RuntimeState,
    TransitionRefusal,
    WorkDependencyRecord,
    WorkItem,
    WorkItemRef,
)
from millrace.contracts.transition import (
    AdmitPlan,
    AdmitPlanRef,
    AdvanceRunnerSession,
    AdvanceRunnerSessionRecord,
    ClaimWork,
    CloseClosureTarget,
    CloseWorkItem,
    CreateActivation,
    CreateRun,
    CreateRunnerSession,
    CreateRunnerSessionRecord,
    CreateWorkItem,
    EmitGovernanceEvent,
    EmitTrace,
    EnqueueWork,
    EvaluateCompletionBehavior,
    FanoutFromArtifact,
    InitializeWorkspace,
    JoinFromArtifact,
    OpenClosureTarget,
    OperatorCloseLineage,
    OperatorCloseWait,
    OperatorResumeLineage,
    OperatorResumeWait,
    OperatorReviseLineage,
    OperatorReviseWait,
    ReconcileEffect,
    RecordArtifact,
    RecordClosureBlocked,
    RecordClosureEvaluation,
    RecordClosureTarget,
    RecordClosureTerminal,
    RecordCooldownWait,
    RecordEffectReconciliation,
    RecordFanout,
    RecordInputReceipt,
    RecordOperatorIntervention,
    RecordRecoveryAttempt,
    RecordRefusal,
    RecordRemediationWork,
    RecordRunnerSessionCancellation,
    RecordRunnerSessionCancellationAttempt,
    RecordRunnerSessionCancellationAttemptRecord,
    RecordRunnerSessionCompletion,
    RecordRunnerSessionCompletionRecord,
    RecordTransition,
    RecordWorkDependency,
    RequestRunnerSessionCancellation,
    RouteActivation,
    RunnerResultObserved,
    SelectDefaultPlan,
    SelectDefaultPlanRef,
    SupersedeLineageQuarantine,
    TimerDue,
    TransitionContext,
    TransitionDecision,
    TransitionInput,
    TransitionMutation,
    input_family,
    input_kind,
    input_payload_digest,
    operator_payload_digest,
)
from millrace.kernel.audit import (
    event_and_trace_records,
    idempotency_conflict_event_context,
    transition_record,
)
from millrace.kernel.fanout_policy import (
    FanoutItems,
    PolicyAssessment,
    SourceContext,
    assess_fanout,
    fanout_item_identity,
    fanout_items,
    fanout_target_payload,
    source_context_for_artifact,
)
from millrace.kernel.joins import (
    decide_join_from_artifact,
    join_authority_refusal,
)
from millrace.kernel.lookups import (
    active_lineage_quarantine_for,
    active_operator_wait_for,
    artifact_schema_for,
    fanout_for,
    intervention_option_for,
    lineage_quarantine_scope_key,
    operator_wait_scope_key,
    plan_ref_for,
    route_contract_supported,
    run_has_observation,
    runner_binding_for,
    stage_kind_for,
    terminal_action_for,
    terminal_outcome_for,
    wait_state_for_policy,
)
from millrace.kernel.operator_waits import (
    decide_operator_close_wait,
    decide_operator_resume_wait,
    decide_operator_revise_wait,
)
from millrace.kernel.runner_sessions import (
    advance_runner_session_refusal,
    cancellation_attempt_record,
    cancellation_attempt_refusal,
    cancellation_record,
    cancellation_request_refusal,
    completion_refusal,
    create_runner_session_refusal,
    runner_session_for_advance,
    runner_session_for_creation,
    session_for_cancellation_request,
    session_for_completion,
)
from millrace.kernel.schema import validate_schema
from millrace.kernel.terminal_actions import (
    AUTHORITY_SOURCE_TERMINAL_ACTION,
    SUPPORTED_RUNTIME_TERMINAL_ACTION_KINDS,
    TerminalActionRefusal,
    TerminalActionResolution,
    resolve_terminal_action,
)

EMPTY_OPERATOR_PAYLOAD_DIGEST = operator_payload_digest({})

_COMMON_INTERVENTION_AUDIT_REQUIREMENTS = (
    "input_id",
    "input_digest",
    "selected_plan_fingerprint",
    "actor_id",
    "actor_kind",
    "reason",
    "option_id",
    "policy_id",
    "lineage_id",
    "quarantine_id",
    "recovery_attempt_record_id",
)

_RESUME_INTERVENTION_AUDIT_REQUIREMENTS = (
    *_COMMON_INTERVENTION_AUDIT_REQUIREMENTS,
    "target_activation_id",
    "empty_payload",
)

_CLOSE_INTERVENTION_AUDIT_REQUIREMENTS = (
    *_COMMON_INTERVENTION_AUDIT_REQUIREMENTS,
    "closed_work_item_ids",
    "closed_activation_ids",
    "closed_run_ids",
    "empty_payload",
)

_REVISE_INTERVENTION_AUDIT_REQUIREMENTS = (
    *_COMMON_INTERVENTION_AUDIT_REQUIREMENTS,
    "recovery_attempt_count",
    "target_work_item_id",
    "target_activation_id",
    "payload_digest",
    "payload_reference",
)

_SUPPORTED_RECOVERY_RETURN_PHASES = frozenset(
    {"active_recovery", "quarantine_eligible"}
)
_SUPPORTED_CAPABILITY_KINDS = frozenset({"runner.invoke"})
_SUPPORTED_CAPABILITY_SUPPORT_STATUSES = frozenset({"supported", "unsupported"})
_SUPPORTED_CAPABILITY_GRANT_STATUSES = frozenset(
    {"granted", "denied", "approval_pending"}
)
_SelectedRouteDeclaration = (
    ExternalEnqueueRouteDeclaration | GeneratedWorkRouteDeclaration
)


def decide(
    state: RuntimeState,
    transition_input: TransitionInput,
    context: TransitionContext,
) -> TransitionDecision:
    """Build a deterministic transition decision without mutating state."""
    digest = input_payload_digest(transition_input)
    replay_or_conflict = _idempotency_decision(
        state,
        transition_input,
        context,
        digest,
    )
    if replay_or_conflict is not None:
        return replay_or_conflict

    if isinstance(transition_input, InitializeWorkspace):
        return _accepted_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            mutations=(),
        )
    if isinstance(transition_input, AdmitPlan):
        return _decide_admit_plan(state, transition_input, context, digest)
    if isinstance(transition_input, SelectDefaultPlan):
        return _decide_select_default_plan(state, transition_input, context, digest)
    if isinstance(transition_input, EnqueueWork):
        return _decide_enqueue(state, transition_input, context, digest)
    if isinstance(transition_input, ClaimWork):
        return _decide_claim(state, transition_input, context, digest)
    if isinstance(transition_input, CreateRunnerSession):
        refusal = create_runner_session_refusal(state, transition_input)
        if refusal is not None:
            return _refused_decision(
                transition_input=transition_input,
                context=context,
                digest=digest,
                reason=refusal,
                event_plan_fingerprint=(
                    transition_input.run_ref.plan_ref.authority_fingerprint
                ),
                event_run_id=transition_input.run_ref.run_id,
            )
        run = state.runs[transition_input.run_ref.run_id]
        return _accepted_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            mutations=(
                CreateRunnerSessionRecord(
                    session=runner_session_for_creation(state, transition_input),
                    expected_run_ref=transition_input.run_ref,
                    expected_current_session_id=run.current_session_id,
                ),
            ),
            expected_plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            expected_run_generations={
                run.run_ref.run_id: run.run_ref.generation,
            },
            expected_run_fencing_tokens={
                run.run_ref.run_id: run.run_ref.fencing_token,
            },
            event_plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            event_run_id=run.run_ref.run_id,
            event_authority_source="run",
        )
    if isinstance(transition_input, AdvanceRunnerSession):
        refusal = advance_runner_session_refusal(state, transition_input)
        if refusal is not None:
            return _runner_session_refused_decision(
                transition_input,
                context,
                digest,
                refusal,
            )
        return _runner_session_accepted_decision(
            transition_input,
            context,
            digest,
            transition_input.run_ref,
            (
                AdvanceRunnerSessionRecord(
                    session=runner_session_for_advance(state, transition_input),
                    expected_run_ref=transition_input.run_ref,
                    expected_session_state=transition_input.expected_state,
                ),
            ),
        )
    if isinstance(transition_input, RequestRunnerSessionCancellation):
        refusal = cancellation_request_refusal(state, transition_input)
        if refusal is not None:
            return _runner_session_refused_decision(
                transition_input,
                context,
                digest,
                refusal,
            )
        return _runner_session_accepted_decision(
            transition_input,
            context,
            digest,
            transition_input.run_ref,
            (
                RecordRunnerSessionCancellation(
                    record=cancellation_record(transition_input),
                    expected_run_ref=transition_input.run_ref,
                    expected_session_state=transition_input.expected_state,
                ),
                AdvanceRunnerSessionRecord(
                    session=session_for_cancellation_request(
                        state,
                        transition_input,
                    ),
                    expected_run_ref=transition_input.run_ref,
                    expected_session_state=transition_input.expected_state,
                ),
            ),
        )
    if isinstance(transition_input, RecordRunnerSessionCancellationAttempt):
        refusal = cancellation_attempt_refusal(state, transition_input)
        if refusal is not None:
            return _runner_session_refused_decision(
                transition_input,
                context,
                digest,
                refusal,
            )
        return _runner_session_accepted_decision(
            transition_input,
            context,
            digest,
            transition_input.run_ref,
            (
                RecordRunnerSessionCancellationAttemptRecord(
                    record=cancellation_attempt_record(transition_input),
                    expected_run_ref=transition_input.run_ref,
                    expected_session_state=transition_input.expected_state,
                ),
            ),
        )
    if isinstance(transition_input, RecordRunnerSessionCompletion):
        refusal = completion_refusal(state, transition_input)
        if refusal is not None:
            return _runner_session_refused_decision(
                transition_input,
                context,
                digest,
                refusal,
            )
        return _runner_session_accepted_decision(
            transition_input,
            context,
            digest,
            transition_input.run_ref,
            (
                RecordRunnerSessionCompletionRecord(
                    record=transition_input.completion,
                    expected_run_ref=transition_input.run_ref,
                    expected_session_state=transition_input.expected_state,
                ),
                AdvanceRunnerSessionRecord(
                    session=session_for_completion(
                        state,
                        transition_input.completion,
                    ),
                    expected_run_ref=transition_input.run_ref,
                    expected_session_state=transition_input.expected_state,
                ),
            ),
        )
    if isinstance(transition_input, FanoutFromArtifact):
        return _decide_fanout_from_artifact(state, transition_input, context, digest)
    if isinstance(transition_input, JoinFromArtifact):
        return decide_join_from_artifact(
            state,
            transition_input,
            context,
            digest,
            accept_decision=_accepted_decision,
            refuse_decision=_refused_decision,
            selected_authority_refusal=_selected_authority_refusal,
        )
    if isinstance(transition_input, TimerDue):
        return _decide_timer_due(state, transition_input, context, digest)
    if isinstance(transition_input, ReconcileEffect):
        return _decide_reconcile_effect(state, transition_input, context, digest)
    if isinstance(transition_input, OpenClosureTarget):
        return _decide_open_closure_target(state, transition_input, context, digest)
    if isinstance(transition_input, EvaluateCompletionBehavior):
        return _decide_evaluate_completion_behavior(
            state,
            transition_input,
            context,
            digest,
        )
    if isinstance(transition_input, RunnerResultObserved):
        return _decide_runner_result(state, transition_input, context, digest)
    if isinstance(transition_input, OperatorResumeLineage):
        return _decide_operator_resume_lineage(state, transition_input, context, digest)
    if isinstance(transition_input, OperatorCloseLineage):
        return _decide_operator_close_lineage(state, transition_input, context, digest)
    if isinstance(transition_input, OperatorReviseLineage):
        return _decide_operator_revise_lineage(state, transition_input, context, digest)
    if isinstance(transition_input, OperatorResumeWait):
        return decide_operator_resume_wait(
            state,
            transition_input,
            context,
            digest,
            accept_decision=_accepted_decision,
            refuse_decision=_refused_decision,
        )
    if isinstance(transition_input, OperatorCloseWait):
        return decide_operator_close_wait(
            state,
            transition_input,
            context,
            digest,
            accept_decision=_accepted_decision,
            refuse_decision=_refused_decision,
        )
    if isinstance(transition_input, OperatorReviseWait):
        return decide_operator_revise_wait(
            state,
            transition_input,
            context,
            digest,
            accept_decision=_accepted_decision,
            refuse_decision=_refused_decision,
        )
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason="unsupported_input",
    )


def _idempotency_decision(
    state: RuntimeState,
    transition_input: TransitionInput,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision | None:
    existing = state.receipts.get(transition_input.input_id)
    if existing is None:
        return None
    if existing.receipt_ref.input_payload_digest == digest:
        if not existing.accepted:
            refusal = TransitionRefusal(
                record_id=f"{context.transition_id}:refusal",
                input_id=transition_input.input_id,
                input_kind=input_kind(transition_input),
                input_family=input_family(transition_input),
                reason=existing.refusal_reason or "replayed_refusal",
            )
            return TransitionDecision(
                input_id=transition_input.input_id,
                input_kind=input_kind(transition_input),
                input_family=input_family(transition_input),
                input_payload_digest=digest,
                accepted=False,
                receipt_ref=existing.receipt_ref,
                refusal=refusal,
                expected_plan_fingerprint=None,
                expected_work_item_generations={},
                expected_activation_generations={},
                expected_activation_unclaimed=(),
                expected_run_generations={},
                expected_run_fencing_tokens={},
                expected_run_unobserved=(),
                expected_pause_absent=False,
                expected_lineage_quarantine_absent=(),
                expected_work_item_open=(),
                mutations=(),
                governance_events=(),
                trace_records=(),
            )
        if isinstance(transition_input, EnqueueWork):
            replay_detail = _enqueue_replay_invalid_detail(state, transition_input)
            if replay_detail is not None:
                reason = (
                    "idempotency_conflict"
                    if replay_detail == "default_plan_mismatch"
                    else "enqueue_replay_target_invalid"
                )
                return _refused_decision(
                    transition_input=transition_input,
                    context=context,
                    digest=digest,
                    reason=reason,
                    record_receipt=False,
                    detail=replay_detail,
                )
        return TransitionDecision(
            input_id=transition_input.input_id,
            input_kind=input_kind(transition_input),
            input_family=input_family(transition_input),
            input_payload_digest=digest,
            accepted=True,
            receipt_ref=existing.receipt_ref,
            refusal=None,
            expected_plan_fingerprint=None,
            expected_work_item_generations={},
            expected_activation_generations={},
            expected_activation_unclaimed=(),
            expected_run_generations={},
            expected_run_fencing_tokens={},
            expected_run_unobserved=(),
            expected_pause_absent=False,
            expected_lineage_quarantine_absent=(),
            expected_work_item_open=(),
            mutations=(),
            governance_events=(),
            trace_records=(),
        )
    event_plan_fingerprint, event_work_item_id, event_run_id = (
        idempotency_conflict_event_context(state, transition_input)
    )
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason="idempotency_conflict",
        record_receipt=False,
        event_plan_fingerprint=event_plan_fingerprint,
        event_work_item_id=event_work_item_id,
        event_run_id=event_run_id,
    )


def _enqueue_replay_invalid_detail(
    state: RuntimeState,
    transition_input: EnqueueWork,
) -> str | None:
    target_work_items = tuple(
        work_item
        for work_item in state.work_items.values()
        if work_item.created_by_input_id == transition_input.input_id
    )
    target_activations = tuple(
        activation
        for activation in state.activations.values()
        if activation.created_by_input_id == transition_input.input_id
    )
    if len(target_work_items) != 1:
        return "missing_or_ambiguous_work_item"
    if len(target_activations) != 1:
        return "missing_or_ambiguous_activation"

    work_item = target_work_items[0]
    activation = target_activations[0]
    if activation.work_item_id != work_item.ref.work_item_id:
        return "activation_work_item_mismatch"
    if work_item.created_by_input_id != transition_input.input_id:
        return "work_item_input_mismatch"
    if activation.created_by_input_id != transition_input.input_id:
        return "activation_input_mismatch"
    if work_item.queue_family_id != transition_input.queue_family_id:
        return "work_item_queue_family_mismatch"
    if activation.queue_family_id != transition_input.queue_family_id:
        return "activation_queue_family_mismatch"
    if work_item.ref.plan_ref != activation.plan_ref:
        return "activation_plan_ref_mismatch"

    default_plan_ref = state.default_plan_ref
    if default_plan_ref != work_item.ref.plan_ref:
        return "default_plan_mismatch"

    admitted = state.admitted_plans.get(work_item.ref.plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != work_item.ref.plan_ref:
        return "missing_admitted_plan"
    if not verify_authority_fingerprint(
        admitted.selected_plan,
        work_item.ref.plan_ref.authority_fingerprint,
    ):
        return "selected_plan_authority_mismatch"
    selected_route = _selected_external_enqueue_route(
        admitted.selected_plan,
        transition_input.queue_family_id,
    )
    if selected_route is None:
        return "missing_selected_external_enqueue_route"
    cached_route = admitted.external_enqueue_routes.get(
        transition_input.queue_family_id,
    )
    if cached_route is None:
        return "missing_external_enqueue_route"
    if not _external_enqueue_route_matches_declaration(
        cached_route,
        selected_route,
    ):
        return "selected_route_authority_mismatch"
    if selected_route.queue_family_id != work_item.queue_family_id:
        return "selected_route_queue_family_mismatch"
    if activation.graph_node_id != selected_route.graph_node_id:
        return "selected_route_graph_node_mismatch"
    if activation.stage_kind_id != selected_route.stage_kind_id:
        return "selected_route_stage_kind_mismatch"
    if activation.runner_binding_id != selected_route.runner_binding_id:
        return "selected_route_runner_binding_mismatch"
    return None


def _selected_external_enqueue_route(
    selected_plan: SelectedCompiledPlan,
    queue_family_id: QueueFamilyId,
) -> ExternalEnqueueRouteDeclaration | None:
    for route in selected_plan.external_enqueue_routes:
        if route.queue_family_id == queue_family_id:
            return route
    return None


def _external_enqueue_route_matches_declaration(
    route: ExternalEnqueueRoute,
    declaration: ExternalEnqueueRouteDeclaration,
) -> bool:
    return (
        route.queue_family_id == declaration.queue_family_id
        and route.graph_node_id == declaration.graph_node_id
        and route.stage_kind_id == declaration.stage_kind_id
        and route.runner_binding_id == declaration.runner_binding_id
        and route.payload_schema_id == declaration.payload_schema_id
    )


def _decide_admit_plan(
    state: RuntimeState,
    transition_input: AdmitPlan,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    plan_ref = plan_ref_for(
        transition_input.selected_plan,
        transition_input.authority_fingerprint,
    )
    extra_mutations: tuple[TransitionMutation, ...]
    existing = state.admitted_plans.get(plan_ref.authority_fingerprint)
    if existing is not None:
        if (
            existing.plan_ref != plan_ref
            or existing.selected_plan != transition_input.selected_plan
        ):
            return _refused_decision(
                transition_input=transition_input,
                context=context,
                digest=digest,
                reason="plan_authority_conflict",
            )
        authority_refusal = _selected_authority_refusal(transition_input.selected_plan)
        if authority_refusal is not None:
            return _refused_decision(
                transition_input=transition_input,
                context=context,
                digest=digest,
                reason="unsupported_selected_authority",
                detail=authority_refusal,
            )
        extra_mutations = ()
    else:
        if not verify_authority_fingerprint(
            transition_input.selected_plan,
            transition_input.authority_fingerprint,
        ):
            return _refused_decision(
                transition_input=transition_input,
                context=context,
                digest=digest,
                reason="plan_fingerprint_mismatch",
            )
        authority_refusal = _selected_authority_refusal(transition_input.selected_plan)
        if authority_refusal is not None:
            return _refused_decision(
                transition_input=transition_input,
                context=context,
                digest=digest,
                reason="unsupported_selected_authority",
                detail=authority_refusal,
            )
        extra_mutations = (
            AdmitPlanRef(
                plan_ref=plan_ref,
                selected_plan=transition_input.selected_plan,
            ),
        )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=extra_mutations,
    )


def _decide_select_default_plan(
    state: RuntimeState,
    transition_input: SelectDefaultPlan,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    admitted = state.admitted_plans.get(transition_input.authority_fingerprint)
    if admitted is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unknown_plan_ref",
        )
    authority_refusal = _selected_authority_refusal(admitted.selected_plan)
    if authority_refusal is not None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_selected_authority",
            detail=authority_refusal,
        )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(SelectDefaultPlanRef(admitted.plan_ref),),
        expected_plan_fingerprint=admitted.plan_ref.authority_fingerprint,
    )


def _selected_authority_refusal(selected_plan: SelectedCompiledPlan) -> str | None:
    component_refusal = runner_component_authority_refusal(selected_plan)
    if component_refusal is not None:
        return component_refusal
    for capability in selected_plan.capabilities:
        capability_refusal = _capability_authority_refusal(capability)
        if capability_refusal is not None:
            return capability_refusal
    for action in selected_plan.terminal_actions:
        if action.action_kind not in SUPPORTED_RUNTIME_TERMINAL_ACTION_KINDS:
            return f"terminal_action_kind:{action.action_kind}"
    close_escalation_refusal = (
        _terminal_action_close_with_escalation_route_authority_refusal(selected_plan)
    )
    if close_escalation_refusal is not None:
        return close_escalation_refusal
    graph_refusal = _selected_graph_refusal(selected_plan)
    if graph_refusal is not None:
        return graph_refusal
    route_payload_schema_refusal = _external_enqueue_route_payload_schema_refusal(
        selected_plan
    )
    if route_payload_schema_refusal is not None:
        return route_payload_schema_refusal
    route_authority_refusal = _selected_enqueue_route_authority_refusal(selected_plan)
    if route_authority_refusal is not None:
        return route_authority_refusal
    action_artifact_refusal = _terminal_action_artifact_authority_refusal(selected_plan)
    if action_artifact_refusal is not None:
        return action_artifact_refusal
    effect_declaration_refusal = _effect_declaration_authority_refusal(selected_plan)
    if effect_declaration_refusal is not None:
        return effect_declaration_refusal
    static_route_refusal = _terminal_action_static_route_authority_refusal(
        selected_plan
    )
    if static_route_refusal is not None:
        return static_route_refusal
    dynamic_route_refusal = _terminal_action_dynamic_route_authority_refusal(
        selected_plan
    )
    if dynamic_route_refusal is not None:
        return dynamic_route_refusal
    if selected_plan.lineage_policy not in {"root_from_external_enqueue", "none"}:
        return f"lineage_policy:{selected_plan.lineage_policy}"
    if selected_plan.lineage_policy == "none":
        if selected_plan.recovery_policies:
            return "lineage_policy_conflict:recovery_policies"
        if selected_plan.intervention_options:
            return "lineage_policy_conflict:intervention_options"
        if selected_plan.operator_waits:
            return "lineage_policy_conflict:operator_waits"
        if selected_plan.counters:
            return "lineage_policy_conflict:counters"
    stage_kind_ids = {stage.id for stage in selected_plan.stage_kinds}
    action_by_id = {action.id: action for action in selected_plan.terminal_actions}
    counter_action_refusal = _counter_action_ownership_refusal(selected_plan)
    if counter_action_refusal is not None:
        return counter_action_refusal
    counters_by_increment_action = {
        counter.increment_action_id: counter for counter in selected_plan.counters
    }
    threshold_action_ids = {
        counter.threshold_action_id for counter in selected_plan.counters
    }
    policy_ids = {policy.id for policy in selected_plan.recovery_policies}
    wait_ids = {wait.id for wait in selected_plan.wait_states}
    for wait in selected_plan.wait_states:
        if wait.wait_kind != "timer":
            return f"wait_state_kind:{wait.wait_kind}"
        if wait.starts_at_attempt <= 0 or wait.duration_seconds <= 0:
            return f"wait_state_value:{wait.id}"
        if wait.policy_id not in policy_ids:
            return f"wait_state_policy:{wait.id}"
    referenced_wait_ids: set[WaitStateId] = set()
    for policy in selected_plan.recovery_policies:
        if policy.attempt_scope != "lineage":
            return f"recovery_policy_attempt_scope:{policy.attempt_scope}"
        if policy.recorded_source_selector != "latest_recovery_attempt_for_lineage":
            return "recovery_policy_recorded_source_selector"
        if policy.threshold_behavior != "runtime_quarantine_at_threshold":
            return f"recovery_policy_threshold_behavior:{policy.threshold_behavior}"
        policy_refusal = _recovery_policy_field_refusal(
            policy,
            action_by_id=action_by_id,
            counters_by_increment_action=counters_by_increment_action,
            threshold_action_ids=threshold_action_ids,
            stage_kind_ids=stage_kind_ids,
        )
        if policy_refusal is not None:
            return policy_refusal
        if policy.cooldown_wait_state_id is None:
            return "recovery_policy_missing_wait_state"
        if policy.cooldown_wait_state_id not in wait_ids:
            return f"recovery_policy_wait_state:{policy.cooldown_wait_state_id}"
        matching_waits = tuple(
            item
            for item in selected_plan.wait_states
            if item.id == policy.cooldown_wait_state_id
        )
        if len(matching_waits) != 1:
            return f"recovery_policy_wait_state_count:{policy.id}"
        wait = matching_waits[0]
        if (
            wait.policy_id != policy.id
            or wait.starts_at_attempt != policy.cooldown_starts_at_attempt
            or wait.duration_seconds != policy.default_cooldown_seconds
        ):
            return f"recovery_policy_wait_state_mismatch:{policy.id}"
        referenced_wait_ids.add(policy.cooldown_wait_state_id)
        if (
            policy.immediate_recovery_limit <= 0
            or policy.cooldown_starts_at_attempt <= 0
            or policy.quarantine_threshold_attempt <= 0
            or policy.default_cooldown_seconds <= 0
            or policy.quarantine_threshold_attempt < policy.cooldown_starts_at_attempt
        ):
            return f"recovery_policy_threshold:{policy.id}"
    for wait in selected_plan.wait_states:
        if wait.id not in referenced_wait_ids:
            return f"wait_state_orphan:{wait.id}"
    for counter in selected_plan.counters:
        if counter.counter_kind != "lineage_terminal_action_counter":
            return f"counter_kind:{counter.counter_kind}"
        if counter.scope != "lineage":
            return f"counter_scope:{counter.scope}"
        if counter.threshold_count <= 1:
            return f"counter_threshold:{counter.id}"
        if counter.stage_kind_id not in stage_kind_ids:
            return f"counter_stage_kind:{counter.id}"
        increment_action = action_by_id.get(counter.increment_action_id)
        threshold_action = action_by_id.get(counter.threshold_action_id)
        if increment_action is None or threshold_action is None:
            return f"counter_action:{counter.id}"
        if increment_action.stage_kind_id != counter.stage_kind_id:
            return f"counter_action:{counter.id}"
        if (
            threshold_action.stage_kind_id != counter.stage_kind_id
            or counter.threshold_action_id == counter.increment_action_id
        ):
            return f"counter_threshold_action:{counter.id}"
        if threshold_action.action_kind == "recovery_route" and not any(
            counter.increment_action_id in policy.source_recovery_action_ids
            and threshold_action.target_stage_kind_id == policy.recovery_stage_kind_id
            for policy in selected_plan.recovery_policies
        ):
            return f"counter_recovery_policy_source:{counter.id}"
    for option in selected_plan.intervention_options:
        if option.option_kind not in {
            "resume_lineage",
            "close_lineage",
            "revise_lineage",
        }:
            return f"intervention_option_kind:{option.option_kind}"
        if option.policy_id not in policy_ids:
            return f"intervention_option_policy:{option.id}"
        if option.legal_source_state != "active_lineage_quarantine":
            return f"intervention_option_legal_source_state:{option.id}"
        if (
            option.target_selector
            != "selected_quarantine_or_active_quarantine_by_lineage"
        ):
            return f"intervention_option_target_selector:{option.id}"
        if option.supersede_behavior != "supersede_quarantine":
            return f"intervention_option_supersede_behavior:{option.id}"
        if option.attempt_effect != "resolve_attempt":
            return f"intervention_option_attempt_effect:{option.id}"
        if option.actor_kind != "local_operator":
            return f"intervention_option_actor_kind:{option.id}"
        if (
            option.payload_schema_id is not None
            and artifact_schema_for(
                selected_plan,
                str(option.payload_schema_id),
            )
            is None
        ):
            return f"intervention_option_payload_schema:{option.id}"
        if (
            option.audit_metadata_requirements
            != _expected_intervention_audit_requirements(option.option_kind)
        ):
            return f"intervention_option_audit_metadata_requirements:{option.id}"
        option_refusal = _intervention_option_field_refusal(selected_plan, option)
        if option_refusal is not None:
            return option_refusal
    operator_wait_refusal = _operator_wait_authority_refusal(
        selected_plan,
        action_by_id=action_by_id,
    )
    if operator_wait_refusal is not None:
        return operator_wait_refusal
    fanout_refusal = _fanout_authority_refusal(selected_plan, action_by_id=action_by_id)
    if fanout_refusal is not None:
        return fanout_refusal
    join_refusal = join_authority_refusal(selected_plan)
    if join_refusal is not None:
        return join_refusal
    concurrency_refusal = _concurrency_authority_refusal(selected_plan)
    if concurrency_refusal is not None:
        return concurrency_refusal
    completion_refusal = _completion_authority_refusal(
        selected_plan,
        action_by_id=action_by_id,
        stage_kind_ids=stage_kind_ids,
    )
    if completion_refusal is not None:
        return completion_refusal
    return None


def _external_enqueue_route_payload_schema_refusal(
    selected_plan: SelectedCompiledPlan,
) -> str | None:
    for route in selected_plan.external_enqueue_routes:
        if route.payload_schema_id is None:
            continue
        if artifact_schema_for(selected_plan, str(route.payload_schema_id)) is None:
            return f"external_enqueue_route_payload_schema:{route.id}"
    for generated_route in selected_plan.generated_work_routes:
        if generated_route.payload_schema_id is None:
            continue
        if (
            artifact_schema_for(selected_plan, str(generated_route.payload_schema_id))
            is None
        ):
            return f"generated_work_route_payload_schema:{generated_route.id}"
    return None

def _selected_enqueue_route_authority_refusal(
    selected_plan: SelectedCompiledPlan,
) -> str | None:
    queue_family_ids = {family.id for family in selected_plan.queue_families}
    graph_node_ids = {
        node_id for graph in selected_plan.graphs for node_id in graph.node_ids
    }
    stage_by_id = {stage.id: stage for stage in selected_plan.stage_kinds}
    runner_by_id = {runner.id: runner for runner in selected_plan.runner_bindings}
    seen_route_ids: set[str] = set()
    for route in _selected_enqueue_routes(selected_plan):
        if route.id in seen_route_ids:
            return f"selected_enqueue_route_duplicate:{route.id}"
        seen_route_ids.add(route.id)
        if route.queue_family_id not in queue_family_ids:
            return f"selected_enqueue_route_queue_family:{route.id}"
        if route.graph_node_id not in graph_node_ids:
            return f"selected_enqueue_route_graph_node:{route.id}"
        stage = stage_by_id.get(route.stage_kind_id)
        if stage is None:
            return f"selected_enqueue_route_stage_kind:{route.id}"
        runner = runner_by_id.get(route.runner_binding_id)
        if runner is None:
            return f"selected_enqueue_route_runner_binding:{route.id}"
        if route.queue_family_id not in stage.input_queue_family_ids:
            return f"selected_enqueue_route_stage_input:{route.id}"
        if stage.runner_binding_id != route.runner_binding_id:
            return f"selected_enqueue_route_stage_runner:{route.id}"
        if route.stage_kind_id not in runner.stage_kind_ids:
            return f"selected_enqueue_route_runner_stage:{route.id}"
    return None


def _recovery_policy_field_refusal(
    policy: RecoveryPolicyDeclaration,
    *,
    action_by_id: Mapping[ActionId, TerminalActionDeclaration],
    counters_by_increment_action: Mapping[ActionId, CounterDeclaration],
    threshold_action_ids: set[ActionId],
    stage_kind_ids: set[StageKindId],
) -> str | None:
    if policy.recovery_stage_kind_id not in stage_kind_ids:
        return f"recovery_policy_stage_kind:{policy.id}"
    if not policy.source_recovery_action_ids or _has_duplicates(
        policy.source_recovery_action_ids
    ):
        return f"recovery_policy_source_action:{policy.id}"
    if not policy.return_action_ids or _has_duplicates(policy.return_action_ids):
        return f"recovery_policy_return_action:{policy.id}"
    if not policy.quarantine_action_ids or _has_duplicates(
        policy.quarantine_action_ids
    ):
        return f"recovery_policy_quarantine_action:{policy.id}"
    for action_id in policy.source_recovery_action_ids:
        action = action_by_id.get(action_id)
        if action is None or action_id in threshold_action_ids:
            return f"recovery_policy_source_action:{policy.id}"
        if action.action_kind == "recovery_route":
            if (
                action.target_stage_kind_id == policy.recovery_stage_kind_id
                and action.target_graph_node_id is not None
                and action.runner_binding_id is not None
            ):
                continue
            return f"recovery_policy_source_action:{policy.id}"
        counter = counters_by_increment_action.get(action_id)
        threshold_action = (
            action_by_id.get(counter.threshold_action_id)
            if counter is not None
            else None
        )
        if (
            counter is None
            or threshold_action is None
            or threshold_action.action_kind != "recovery_route"
            or threshold_action.target_stage_kind_id != policy.recovery_stage_kind_id
            or threshold_action.target_graph_node_id is None
            or threshold_action.runner_binding_id is None
        ):
            return f"recovery_policy_source_action:{policy.id}"
    for action_id in policy.return_action_ids:
        action = action_by_id.get(action_id)
        if (
            action is None
            or action.action_kind != "return_to_recorded_source"
            or action.stage_kind_id != policy.recovery_stage_kind_id
        ):
            return f"recovery_policy_return_action:{policy.id}"
    for action_id in policy.quarantine_action_ids:
        action = action_by_id.get(action_id)
        if (
            action is None
            or action.action_kind != "quarantine_lineage"
            or action.stage_kind_id != policy.recovery_stage_kind_id
        ):
            return f"recovery_policy_quarantine_action:{policy.id}"
    if not policy.return_allowed_phases or not set(
        policy.return_allowed_phases
    ).issubset(_SUPPORTED_RECOVERY_RETURN_PHASES):
        return f"recovery_policy_return_allowed_phases:{policy.id}"
    if policy.immediate_recovery_limit != policy.cooldown_starts_at_attempt - 1:
        return f"recovery_policy_immediate_recovery_limit:{policy.id}"
    reset_refusal = _recovery_policy_reset_trigger_refusal(policy, action_by_id)
    if reset_refusal is not None:
        return reset_refusal
    return None


def _recovery_policy_reset_trigger_refusal(
    policy: RecoveryPolicyDeclaration,
    action_by_id: Mapping[ActionId, TerminalActionDeclaration],
) -> str | None:
    recovery_action_ids = {
        *policy.source_recovery_action_ids,
        *policy.return_action_ids,
        *policy.quarantine_action_ids,
    }
    seen: set[ActionId] = set()
    for action_id in policy.reset_trigger_action_ids:
        if action_id in seen:
            return f"recovery_policy_reset_trigger_action:{policy.id}"
        seen.add(action_id)
        if action_id not in action_by_id or action_id in recovery_action_ids:
            return f"recovery_policy_reset_trigger_action:{policy.id}"
    return None


def _has_duplicates(values: tuple[ActionId, ...]) -> bool:
    return len(values) != len(set(values))


def _expected_intervention_audit_requirements(option_kind: str) -> tuple[str, ...]:
    if option_kind == "resume_lineage":
        return _RESUME_INTERVENTION_AUDIT_REQUIREMENTS
    if option_kind == "close_lineage":
        return _CLOSE_INTERVENTION_AUDIT_REQUIREMENTS
    if option_kind == "revise_lineage":
        return _REVISE_INTERVENTION_AUDIT_REQUIREMENTS
    return ()


def _intervention_option_field_refusal(
    selected_plan: SelectedCompiledPlan,
    option: InterventionOptionDeclaration,
) -> str | None:
    if option.option_kind == "resume_lineage":
        if option.resume_target_selector != "recorded_source":
            return f"intervention_option_resume_target_selector:{option.id}"
        if option.close_behavior is not None:
            return f"intervention_option_close_behavior:{option.id}"
        if _has_revise_target_fields(option):
            return f"intervention_option_target:{option.id}"
    if option.option_kind == "close_lineage":
        if option.resume_target_selector is not None:
            return f"intervention_option_resume_target_selector:{option.id}"
        if option.close_behavior != "close_ready_or_active_work_in_lineage":
            return f"intervention_option_close_behavior:{option.id}"
        if _has_revise_target_fields(option):
            return f"intervention_option_target:{option.id}"
    if option.option_kind == "revise_lineage":
        if option.resume_target_selector is not None:
            return f"intervention_option_resume_target_selector:{option.id}"
        if option.close_behavior is not None:
            return f"intervention_option_close_behavior:{option.id}"
        if not _revise_target_supported(selected_plan, option):
            return f"intervention_option_target:{option.id}"
    return None


def _operator_wait_authority_refusal(
    selected_plan: SelectedCompiledPlan,
    *,
    action_by_id: Mapping[ActionId, TerminalActionDeclaration],
) -> str | None:
    owner_by_action_id: dict[ActionId, OperatorWaitDeclaration] = {}
    for operator_wait in selected_plan.operator_waits:
        if operator_wait.wait_scope != "lineage":
            return f"operator_wait_scope:{operator_wait.id}"
        if operator_wait.source_work_item_behavior not in {
            "leave_open",
            "close_on_create",
        }:
            return f"operator_wait_source_work_item_behavior:{operator_wait.id}"
        if not operator_wait.unrelated_lineages_continue:
            return f"operator_wait_unrelated_lineages_continue:{operator_wait.id}"
        if operator_wait.actor_kind != "local_operator":
            return f"operator_wait_actor_kind:{operator_wait.id}"
        if operator_wait.correlation_key != "wait_id":
            return f"operator_wait_correlation_key:{operator_wait.id}"
        if operator_wait.idempotency != "input_receipt_and_active_wait_status":
            return f"operator_wait_idempotency:{operator_wait.id}"
        if operator_wait.timeout_policy != "none":
            return f"operator_wait_timeout_policy:{operator_wait.id}"
        if operator_wait.expiry_policy != "none":
            return f"operator_wait_expiry_policy:{operator_wait.id}"
        if operator_wait.cancellation_policy != "selected_resolution_only":
            return f"operator_wait_cancellation_policy:{operator_wait.id}"
        if operator_wait.status_effect != "operator_wait_active":
            return f"operator_wait_status_effect:{operator_wait.id}"
        if not operator_wait.source_action_ids or _has_duplicates(
            operator_wait.source_action_ids
        ):
            return f"operator_wait_source_action:{operator_wait.id}"
        for action_id in operator_wait.source_action_ids:
            action = action_by_id.get(action_id)
            if action is None or action.action_kind != "operator_wait":
                return f"operator_wait_source_action:{operator_wait.id}"
            if action_id in owner_by_action_id:
                return f"operator_wait_duplicate_owner:{action_id}"
            owner_by_action_id[action_id] = operator_wait

        allowed = operator_wait.allowed_resolution_kinds
        allowed_set = set(allowed)
        if (
            not allowed
            or len(allowed) != len(allowed_set)
            or not allowed_set.issubset(_SUPPORTED_OPERATOR_WAIT_RESOLUTION_KINDS)
        ):
            return f"operator_wait_resolution_kind:{operator_wait.id}"
        if (
            operator_wait.source_work_item_behavior == "close_on_create"
            and allowed_set != {"close_recorded_source"}
        ):
            return f"operator_wait_source_work_item_behavior:{operator_wait.id}"
        if "revise_recorded_source" in allowed_set:
            if not _operator_wait_revise_target_supported(selected_plan, operator_wait):
                return f"operator_wait_target:{operator_wait.id}"
        elif _operator_wait_has_revise_target_fields(operator_wait):
            return f"operator_wait_target:{operator_wait.id}"
        if operator_wait.audit_metadata_requirements != (
            _operator_wait_audit_metadata_requirements(
                operator_wait.allowed_resolution_kinds
            )
        ):
            return f"operator_wait_audit_metadata_requirements:{operator_wait.id}"

    for action in selected_plan.terminal_actions:
        if (
            action.action_kind == "operator_wait"
            and action.id not in owner_by_action_id
        ):
            return f"operator_wait_missing_authority:{action.id}"
    return None


def _fanout_authority_refusal(
    selected_plan: SelectedCompiledPlan,
    *,
    action_by_id: Mapping[ActionId, TerminalActionDeclaration],
) -> str | None:
    routes_by_id = {
        route.id: route for route in _selected_enqueue_routes(selected_plan)
    }
    for fanout in selected_plan.fanout_declarations:
        if fanout.duplicate_policy != "refuse":
            return f"fanout_duplicate_policy:{fanout.id}"
        if fanout.root_lineage_policy != "inherit_source_lineage":
            return f"fanout_root_lineage_policy:{fanout.id}"
        if (
            fanout.source_state_policy,
            fanout.dependency_policy,
        ) not in {
            ("source_closed", "depends_on_source_work_item"),
            ("accepted_terminal_observation", "none"),
        }:
            return f"fanout_dependency_policy:{fanout.id}"
        if fanout.source_state_policy not in {
            "source_closed",
            "accepted_terminal_observation",
        }:
            return f"fanout_source_state_policy:{fanout.id}"
        if not fanout.item_source_path or not fanout.item_id_key:
            return f"fanout_item_selector:{fanout.id}"
        if not fanout.target_payload_mapping:
            return f"fanout_payload_mapping:{fanout.id}"
        action = action_by_id.get(fanout.source_action_id)
        if (
            action is None
            or action.artifact_schema_id != fanout.source_artifact_schema_id
        ):
            return f"fanout_source_action:{fanout.id}"
        supported_action_kinds = (
            {"close", "complete_work_item"}
            if fanout.source_state_policy == "source_closed"
            else {
                "route",
                "create_incident_route",
                "close",
                "complete_work_item",
                "close_with_escalation",
                "block_work_item",
            }
        )
        if action.action_kind not in supported_action_kinds:
            return f"fanout_source_action_kind:{fanout.id}"
        if (
            artifact_schema_for(selected_plan, str(fanout.source_artifact_schema_id))
            is None
        ):
            return f"fanout_source_schema:{fanout.id}"
        if (
            artifact_schema_for(selected_plan, str(fanout.target_payload_schema_id))
            is None
        ):
            return f"fanout_target_schema:{fanout.id}"
        route = routes_by_id.get(fanout.target_route_id)
        if route is None:
            return f"fanout_target_route:{fanout.id}"
        if (
            route.queue_family_id != fanout.target_queue_family_id
            or route.stage_kind_id != fanout.target_stage_kind_id
            or route.graph_node_id != fanout.target_graph_node_id
            or route.runner_binding_id != fanout.target_runner_binding_id
            or route.payload_schema_id != fanout.target_payload_schema_id
        ):
            return f"fanout_target_route:{fanout.id}"
    return None


def _selected_enqueue_routes(
    selected_plan: SelectedCompiledPlan,
) -> tuple[_SelectedRouteDeclaration, ...]:
    return (
        *selected_plan.external_enqueue_routes,
        *selected_plan.generated_work_routes,
    )


def _concurrency_authority_refusal(selected_plan: SelectedCompiledPlan) -> str | None:
    partition_ids = {partition.id for partition in selected_plan.partitions}
    owner_by_partition: dict[PartitionId, str] = {}
    for policy in selected_plan.concurrency_policies:
        if policy.partition_id not in partition_ids:
            return f"concurrency_policy_partition:{policy.id}"
        if policy.partition_id in owner_by_partition:
            return f"concurrency_policy_duplicate_partition:{policy.partition_id}"
        owner_by_partition[policy.partition_id] = policy.id
        if policy.max_active_runs <= 0:
            return f"concurrency_policy_max_active_runs:{policy.id}"
        if len(policy.coexist_partition_ids) != len(set(policy.coexist_partition_ids)):
            return f"concurrency_policy_coexist_partition:{policy.id}"
        if policy.partition_id in policy.coexist_partition_ids:
            return f"concurrency_policy_self_coexist:{policy.id}"
        for partition_id in policy.coexist_partition_ids:
            if partition_id not in partition_ids:
                return f"concurrency_policy_coexist_partition:{policy.id}"

    policies_by_partition = {
        policy.partition_id: policy for policy in selected_plan.concurrency_policies
    }
    for policy in selected_plan.concurrency_policies:
        for peer_partition_id in policy.coexist_partition_ids:
            peer_policy = policies_by_partition.get(peer_partition_id)
            if (
                peer_policy is not None
                and policy.partition_id not in peer_policy.coexist_partition_ids
            ):
                return f"concurrency_policy_asymmetric_coexist:{policy.id}"
    return None


def _completion_authority_refusal(
    selected_plan: SelectedCompiledPlan,
    *,
    action_by_id: Mapping[ActionId, TerminalActionDeclaration],
    stage_kind_ids: set[StageKindId],
) -> str | None:
    remediation_by_id = {
        policy.id: policy for policy in selected_plan.remediation_policies
    }
    owned_remediation_actions: dict[ActionId, RemediationPolicyId] = {}
    for policy in selected_plan.remediation_policies:
        refusal = _remediation_policy_authority_refusal(
            selected_plan,
            policy,
            action_by_id=action_by_id,
            stage_kind_ids=stage_kind_ids,
        )
        if refusal is not None:
            return refusal
        existing_owner = owned_remediation_actions.get(policy.source_action_id)
        if existing_owner is not None and existing_owner != policy.id:
            return f"remediation_policy_duplicate_source:{policy.source_action_id}"
        owned_remediation_actions[policy.source_action_id] = policy.id

    for behavior in selected_plan.completion_behaviors:
        refusal = _completion_behavior_authority_refusal(
            selected_plan,
            behavior,
            action_by_id=action_by_id,
            remediation_by_id=remediation_by_id,
            stage_kind_ids=stage_kind_ids,
        )
        if refusal is not None:
            return refusal
    return None


def _completion_behavior_authority_refusal(
    selected_plan: SelectedCompiledPlan,
    behavior: CompletionBehaviorDeclaration,
    *,
    action_by_id: Mapping[ActionId, TerminalActionDeclaration],
    remediation_by_id: Mapping[RemediationPolicyId, RemediationPolicyDeclaration],
    stage_kind_ids: set[StageKindId],
) -> str | None:
    if behavior.trigger != "backlog_drained":
        return f"completion_behavior_trigger:{behavior.id}"
    if behavior.readiness_rule != "no_open_lineage_work":
        return f"completion_behavior_readiness_rule:{behavior.id}"
    if behavior.request_kind != "closure_target":
        return f"completion_behavior_request_kind:{behavior.id}"
    if behavior.target_selector != "active_closure_target":
        return f"completion_behavior_target_selector:{behavior.id}"
    if behavior.root_source_resolution != "runtime_inventory":
        return f"completion_behavior_root_source_resolution:{behavior.id}"
    if behavior.evidence_window_policy != "lineage":
        return f"completion_behavior_evidence_window_policy:{behavior.id}"
    if behavior.rubric_policy != "reuse_or_create":
        return f"completion_behavior_rubric_policy:{behavior.id}"
    if behavior.blocked_work_policy != "suppress":
        return f"completion_behavior_blocked_work_policy:{behavior.id}"
    if not behavior.skip_if_closed:
        return f"completion_behavior_skip_if_closed:{behavior.id}"
    if (
        not behavior.accepted_root_source_kinds
        or len(behavior.accepted_root_source_kinds)
        != len(set(behavior.accepted_root_source_kinds))
        or any(
            not _non_blank_text(item) for item in behavior.accepted_root_source_kinds
        )
    ):
        return f"completion_behavior_root_source_kind:{behavior.id}"
    if behavior.target_stage_kind_id not in stage_kind_ids:
        return f"completion_behavior_stage_kind:{behavior.id}"
    if (
        artifact_schema_for(selected_plan, str(behavior.verdict_artifact_schema_id))
        is None
    ):
        return f"completion_behavior_verdict_schema:{behavior.id}"
    target_stage = stage_kind_for(selected_plan, str(behavior.target_stage_kind_id))
    target_runner = runner_binding_for(selected_plan, str(behavior.runner_binding_id))
    graph_node_stage_owner = _known_graph_node_stage_owner(selected_plan)
    if (
        target_stage is None
        or target_runner is None
        or target_stage.runner_binding_id != behavior.runner_binding_id
        or behavior.target_stage_kind_id not in target_runner.stage_kind_ids
        or behavior.request_queue_family_id not in target_stage.input_queue_family_ids
        or graph_node_stage_owner.get(behavior.target_graph_node_id)
        not in {None, behavior.target_stage_kind_id}
    ):
        return f"completion_behavior_target:{behavior.id}"
    actions = (
        (behavior.pass_action_id, {"close", "complete_work_item"}),
        (behavior.gap_action_id, {"closure_gap"}),
        (behavior.blocked_action_id, {"close", "block_work_item"}),
    )
    for action_id, expected_kinds in actions:
        action = action_by_id.get(action_id)
        if (
            action is None
            or action.stage_kind_id != behavior.target_stage_kind_id
            or action.action_kind not in expected_kinds
        ):
            return f"completion_behavior_action:{behavior.id}"
    pass_action = action_by_id[behavior.pass_action_id]
    if pass_action.artifact_schema_id != behavior.verdict_artifact_schema_id:
        return f"completion_behavior_verdict_action:{behavior.id}"
    policy = remediation_by_id.get(behavior.remediation_policy_id)
    if policy is None or policy.source_action_id != behavior.gap_action_id:
        return f"completion_behavior_remediation_policy:{behavior.id}"
    return None


def _remediation_policy_authority_refusal(
    selected_plan: SelectedCompiledPlan,
    policy: RemediationPolicyDeclaration,
    *,
    action_by_id: Mapping[ActionId, TerminalActionDeclaration],
    stage_kind_ids: set[StageKindId],
) -> str | None:
    action = action_by_id.get(policy.source_action_id)
    if action is None or action.action_kind != "closure_gap":
        return f"remediation_policy_source_action:{policy.id}"
    if policy.target_stage_kind_id not in stage_kind_ids:
        return f"remediation_policy_stage_kind:{policy.id}"
    if policy.guidance_source != "source_artifact":
        return f"remediation_policy_guidance_source:{policy.id}"
    if policy.dedupe_key != "closure_target_and_source_artifact":
        return f"remediation_policy_dedupe_key:{policy.id}"
    if policy.duplicate_policy != "refuse":
        return f"remediation_policy_duplicate_policy:{policy.id}"
    if policy.suppression_policy != "suppress_repeated_same_evidence":
        return f"remediation_policy_suppression_policy:{policy.id}"
    if not _non_blank_text(policy.root_source_kind):
        return f"remediation_policy_root_source_kind:{policy.id}"
    if artifact_schema_for(selected_plan, str(policy.payload_schema_id)) is None:
        return f"remediation_policy_payload_schema:{policy.id}"
    target_stage = stage_kind_for(selected_plan, str(policy.target_stage_kind_id))
    target_runner = runner_binding_for(
        selected_plan,
        str(policy.target_runner_binding_id),
    )
    if (
        target_stage is None
        or target_runner is None
        or target_stage.runner_binding_id != policy.target_runner_binding_id
        or policy.target_stage_kind_id not in target_runner.stage_kind_ids
        or policy.target_queue_family_id not in target_stage.input_queue_family_ids
    ):
        return f"remediation_policy_target:{policy.id}"
    return None


def _terminal_action_artifact_authority_refusal(
    selected_plan: SelectedCompiledPlan,
) -> str | None:
    stages = {stage.id: stage for stage in selected_plan.stage_kinds}
    for action in selected_plan.terminal_actions:
        if action.action_kind in {"route", "create_incident_route"}:
            continue
        if action.artifact_schema_id is None:
            continue
        stage = stages.get(action.stage_kind_id)
        if stage is None or action.artifact_schema_id not in stage.artifact_schema_ids:
            return f"terminal_action_artifact_schema:{action.id}"
    return None


def _effect_declaration_authority_refusal(
    selected_plan: SelectedCompiledPlan,
) -> str | None:
    actions_by_id = {action.id: action for action in selected_plan.terminal_actions}
    artifact_schema_ids = {schema.id for schema in selected_plan.artifact_schemas}
    seen_effect_ids: set[object] = set()
    seen_terminal_action_ids: set[ActionId] = set()
    for declaration in selected_plan.effect_declarations:
        if declaration.effect_declaration_id in seen_effect_ids:
            return f"effect_declaration_duplicate:{declaration.effect_declaration_id}"
        seen_effect_ids.add(declaration.effect_declaration_id)
        if declaration.terminal_action_id in seen_terminal_action_ids:
            return (
                "effect_declaration_terminal_action_duplicate:"
                f"{declaration.terminal_action_id}"
            )
        seen_terminal_action_ids.add(declaration.terminal_action_id)
        if not declaration.target_ref_kind.strip():
            return (
                "effect_declaration_target_ref_kind:"
                f"{declaration.effect_declaration_id}"
            )
        if not declaration.target_ref_schema.strip():
            return (
                "effect_declaration_target_ref_schema:"
                f"{declaration.effect_declaration_id}"
            )
        action = actions_by_id.get(declaration.terminal_action_id)
        if action is None:
            return (
                "effect_declaration_terminal_action:"
                f"{declaration.effect_declaration_id}"
            )
        if action.action_kind not in {"close", "complete_work_item"}:
            return (
                "effect_declaration_terminal_action:"
                f"{declaration.effect_declaration_id}"
            )
        if action.artifact_schema_id != declaration.artifact_schema_id:
            return (
                "effect_declaration_terminal_action:"
                f"{declaration.effect_declaration_id}"
            )
        if declaration.artifact_schema_id not in artifact_schema_ids:
            return (
                "effect_declaration_artifact_schema:"
                f"{declaration.effect_declaration_id}"
            )
        if declaration.provider_ref != "provider.fake_local.workspace":
            return f"effect_declaration_provider:{declaration.effect_declaration_id}"
        if (
            declaration.capability_policy_ref
            != "policy.fake_local.no_real_side_effects"
        ):
            return (
                "effect_declaration_capability_policy:"
                f"{declaration.effect_declaration_id}"
            )
        if declaration.allowed_reconciliation_statuses != (
            "applied",
            "no_op",
            "refused",
        ):
            return (
                "effect_declaration_reconciliation_statuses:"
                f"{declaration.effect_declaration_id}"
            )
        if declaration.real_side_effects_allowed:
            return (
                "effect_declaration_real_side_effects:"
                f"{declaration.effect_declaration_id}"
            )
    return None


def _terminal_action_static_route_authority_refusal(
    selected_plan: SelectedCompiledPlan,
) -> str | None:
    graph_node_stage_owner = _known_graph_node_stage_owner(selected_plan)
    for action in selected_plan.terminal_actions:
        if action.action_kind not in {"route", "create_incident_route"}:
            continue
        if (
            action.target_stage_kind_id is None
            or action.target_graph_node_id is None
            or action.emitted_queue_family_id is None
            or action.artifact_schema_id is None
            or action.runner_binding_id is None
        ):
            return f"terminal_route_authority:{action.id}"
        known_stage_for_node = graph_node_stage_owner.get(action.target_graph_node_id)
        if (
            known_stage_for_node is not None
            and known_stage_for_node != action.target_stage_kind_id
        ):
            return f"terminal_route_graph_node_stage:{action.id}"
        if not route_contract_supported(
            selected_plan,
            source_stage_kind_id=str(action.stage_kind_id),
            target_stage_kind_id=str(action.target_stage_kind_id),
            emitted_queue_family_id=str(action.emitted_queue_family_id),
            artifact_schema_id=str(action.artifact_schema_id),
            runner_binding_id=str(action.runner_binding_id),
        ):
            return f"terminal_route_artifact_schema:{action.id}"
    return None


def _terminal_action_close_with_escalation_route_authority_refusal(
    selected_plan: SelectedCompiledPlan,
) -> str | None:
    for action in selected_plan.terminal_actions:
        if action.action_kind != "close_with_escalation":
            continue
        for field_name, value in (
            ("target_stage_kind_id", action.target_stage_kind_id),
            ("target_graph_node_id", action.target_graph_node_id),
            ("emitted_queue_family_id", action.emitted_queue_family_id),
            ("runner_binding_id", action.runner_binding_id),
            ("payload_projection", action.payload_projection),
            ("dynamic_target_selector", action.dynamic_target_selector),
        ):
            if value is not None:
                return (
                    "terminal_close_with_escalation_route_authority:"
                    f"{action.id}.{field_name}"
                )
    return None


def _terminal_action_dynamic_route_authority_refusal(
    selected_plan: SelectedCompiledPlan,
) -> str | None:
    for action in selected_plan.terminal_actions:
        dynamic_targets = _dynamic_route_target_tuples(action)
        if isinstance(dynamic_targets, str):
            return dynamic_targets
        if not dynamic_targets:
            continue
        if action.artifact_schema_id is None:
            return f"dynamic_route_artifact_schema:{action.id}"
        for queue_id, stage_id, _graph_node_id, runner_id in dynamic_targets:
            if not route_contract_supported(
                selected_plan,
                source_stage_kind_id=str(action.stage_kind_id),
                target_stage_kind_id=str(stage_id),
                emitted_queue_family_id=str(queue_id),
                artifact_schema_id=str(action.artifact_schema_id),
                runner_binding_id=str(runner_id),
            ):
                return f"dynamic_route_target:{action.id}"
    return None


def _operator_wait_has_revise_target_fields(
    operator_wait: OperatorWaitDeclaration,
) -> bool:
    return (
        operator_wait.payload_schema_id is not None
        or operator_wait.target_queue_family_id is not None
        or operator_wait.target_stage_kind_id is not None
        or operator_wait.target_graph_node_id is not None
        or operator_wait.target_runner_binding_id is not None
    )


def _operator_wait_revise_target_supported(
    selected_plan: SelectedCompiledPlan,
    operator_wait: OperatorWaitDeclaration,
) -> bool:
    if (
        operator_wait.payload_schema_id is None
        or operator_wait.target_queue_family_id is None
        or operator_wait.target_stage_kind_id is None
        or operator_wait.target_graph_node_id is None
        or operator_wait.target_runner_binding_id is None
    ):
        return False
    if artifact_schema_for(selected_plan, str(operator_wait.payload_schema_id)) is None:
        return False
    target_stage = next(
        (
            stage
            for stage in selected_plan.stage_kinds
            if stage.id == operator_wait.target_stage_kind_id
        ),
        None,
    )
    target_runner = next(
        (
            runner
            for runner in selected_plan.runner_bindings
            if runner.id == operator_wait.target_runner_binding_id
        ),
        None,
    )
    if target_stage is None or target_runner is None:
        return False
    if operator_wait.target_queue_family_id not in {
        queue_family.id for queue_family in selected_plan.queue_families
    }:
        return False
    if operator_wait.target_queue_family_id not in target_stage.input_queue_family_ids:
        return False
    if target_stage.runner_binding_id != operator_wait.target_runner_binding_id:
        return False
    if operator_wait.target_stage_kind_id not in target_runner.stage_kind_ids:
        return False
    target_tuple = (
        operator_wait.target_queue_family_id,
        operator_wait.target_stage_kind_id,
        operator_wait.target_graph_node_id,
        operator_wait.target_runner_binding_id,
    )
    if target_tuple not in _selected_route_targets(selected_plan):
        return False
    target_payload_schema_ids = _selected_route_payload_schema_ids(
        selected_plan,
        queue_family_id=operator_wait.target_queue_family_id,
        stage_kind_id=operator_wait.target_stage_kind_id,
        graph_node_id=operator_wait.target_graph_node_id,
        runner_binding_id=operator_wait.target_runner_binding_id,
    )
    return (
        not target_payload_schema_ids
        or operator_wait.payload_schema_id in target_payload_schema_ids
    )


def _has_revise_target_fields(option: InterventionOptionDeclaration) -> bool:
    return (
        option.payload_schema_id is not None
        or option.target_queue_family_id is not None
        or option.target_stage_kind_id is not None
        or option.target_graph_node_id is not None
        or option.target_runner_binding_id is not None
    )


def _revise_target_supported(
    selected_plan: SelectedCompiledPlan,
    option: InterventionOptionDeclaration,
) -> bool:
    if (
        option.payload_schema_id is None
        or option.target_queue_family_id is None
        or option.target_stage_kind_id is None
        or option.target_graph_node_id is None
        or option.target_runner_binding_id is None
    ):
        return False
    if artifact_schema_for(selected_plan, str(option.payload_schema_id)) is None:
        return False

    target_stage = next(
        (
            stage
            for stage in selected_plan.stage_kinds
            if stage.id == option.target_stage_kind_id
        ),
        None,
    )
    target_runner = next(
        (
            runner
            for runner in selected_plan.runner_bindings
            if runner.id == option.target_runner_binding_id
        ),
        None,
    )
    if target_stage is None or target_runner is None:
        return False
    if option.target_queue_family_id not in {
        queue_family.id for queue_family in selected_plan.queue_families
    }:
        return False
    if option.target_queue_family_id not in target_stage.input_queue_family_ids:
        return False
    if target_stage.runner_binding_id != option.target_runner_binding_id:
        return False
    if option.target_stage_kind_id not in target_runner.stage_kind_ids:
        return False

    target_tuple = (
        option.target_queue_family_id,
        option.target_stage_kind_id,
        option.target_graph_node_id,
        option.target_runner_binding_id,
    )
    if target_tuple not in _selected_route_targets(selected_plan):
        return False
    target_payload_schema_ids = _selected_route_payload_schema_ids(
        selected_plan,
        queue_family_id=option.target_queue_family_id,
        stage_kind_id=option.target_stage_kind_id,
        graph_node_id=option.target_graph_node_id,
        runner_binding_id=option.target_runner_binding_id,
    )
    return (
        not target_payload_schema_ids
        or option.payload_schema_id in target_payload_schema_ids
    )


def _counter_action_ownership_refusal(
    selected_plan: SelectedCompiledPlan,
) -> str | None:
    owners: dict[ActionId, tuple[object, str]] = {}
    for counter in selected_plan.counters:
        for field_name, action_id in (
            ("increment_action_id", counter.increment_action_id),
            ("threshold_action_id", counter.threshold_action_id),
        ):
            existing_owner = owners.get(action_id)
            if existing_owner is not None and existing_owner[0] != counter.id:
                return f"counter_duplicate_action:{action_id}"
            owners.setdefault(action_id, (counter.id, field_name))
    return None


def _capability_authority_refusal(
    capability: CapabilityDeclaration,
) -> str | None:
    if capability.capability_kind not in _SUPPORTED_CAPABILITY_KINDS:
        return f"capability_kind:{capability.id}"
    if capability.support_status not in _SUPPORTED_CAPABILITY_SUPPORT_STATUSES:
        return f"capability_support_status:{capability.id}"
    if capability.grant_status not in _SUPPORTED_CAPABILITY_GRANT_STATUSES:
        return f"capability_grant_status:{capability.id}"
    if capability.approval_policy_id is not None:
        return f"capability_approval_policy:{capability.id}"
    return None


def _selected_graph_refusal(selected_plan: SelectedCompiledPlan) -> str | None:
    graph_node_ids = {
        node_id for graph in selected_plan.graphs for node_id in graph.node_ids
    }
    graph_node_stage_owner = _known_graph_node_stage_owner(selected_plan)
    for route in _selected_enqueue_routes(selected_plan):
        if route.graph_node_id not in graph_node_ids:
            return f"graph_node_missing:{route.graph_node_id}"
    for behavior in selected_plan.completion_behaviors:
        if behavior.target_graph_node_id not in graph_node_ids:
            return f"graph_node_missing:{behavior.target_graph_node_id}"
    for policy in selected_plan.remediation_policies:
        if policy.target_graph_node_id not in graph_node_ids:
            return f"graph_node_missing:{policy.target_graph_node_id}"
    for action in selected_plan.terminal_actions:
        if (
            action.target_graph_node_id is not None
            and action.target_graph_node_id not in graph_node_ids
        ):
            return f"graph_node_missing:{action.target_graph_node_id}"
        if (
            action.action_kind == "recovery_route"
            and action.target_graph_node_id is not None
            and action.target_stage_kind_id is not None
        ):
            known_stage = graph_node_stage_owner.get(action.target_graph_node_id)
            if known_stage is not None and known_stage != action.target_stage_kind_id:
                return f"terminal_recovery_route_graph_node_stage:{action.id}"
        dynamic_targets = _dynamic_route_target_tuples(action)
        if isinstance(dynamic_targets, str):
            return dynamic_targets
        for _queue_id, _stage_id, graph_node_id, _runner_id in dynamic_targets:
            if graph_node_id not in graph_node_ids:
                return f"graph_node_missing:{graph_node_id}"
    return None


def _known_graph_node_stage_owner(
    selected_plan: SelectedCompiledPlan,
) -> Mapping[str, StageKindId]:
    owners: dict[str, StageKindId] = {}
    for route in _selected_enqueue_routes(selected_plan):
        owners.setdefault(route.graph_node_id, route.stage_kind_id)
    for behavior in selected_plan.completion_behaviors:
        owners.setdefault(behavior.target_graph_node_id, behavior.target_stage_kind_id)
    for policy in selected_plan.remediation_policies:
        owners.setdefault(policy.target_graph_node_id, policy.target_stage_kind_id)
    for action in selected_plan.terminal_actions:
        if action.action_kind == "recovery_route":
            continue
        if (
            action.target_graph_node_id is not None
            and action.target_stage_kind_id is not None
        ):
            owners.setdefault(action.target_graph_node_id, action.target_stage_kind_id)
        dynamic_targets = _dynamic_route_target_tuples(action)
        if isinstance(dynamic_targets, str):
            continue
        for _queue_id, stage_id, graph_node_id, _runner_id in dynamic_targets:
            owners.setdefault(graph_node_id, stage_id)
    return owners


def _selected_route_targets(
    selected_plan: SelectedCompiledPlan,
) -> frozenset[tuple[QueueFamilyId, StageKindId, str, RunnerBindingId]]:
    route_targets = {
        (
            route.queue_family_id,
            route.stage_kind_id,
            route.graph_node_id,
            route.runner_binding_id,
        )
        for route in _selected_enqueue_routes(selected_plan)
    }
    route_targets.update(
        (
            behavior.request_queue_family_id,
            behavior.target_stage_kind_id,
            behavior.target_graph_node_id,
            behavior.runner_binding_id,
        )
        for behavior in selected_plan.completion_behaviors
    )
    route_targets.update(
        (
            policy.target_queue_family_id,
            policy.target_stage_kind_id,
            policy.target_graph_node_id,
            policy.target_runner_binding_id,
        )
        for policy in selected_plan.remediation_policies
    )
    for action in selected_plan.terminal_actions:
        if (
            action.emitted_queue_family_id is not None
            and action.target_stage_kind_id is not None
            and action.target_graph_node_id is not None
            and action.runner_binding_id is not None
        ):
            route_targets.add(
                (
                    action.emitted_queue_family_id,
                    action.target_stage_kind_id,
                    action.target_graph_node_id,
                    action.runner_binding_id,
                )
            )
        dynamic_targets = _dynamic_route_target_tuples(action)
        if not isinstance(dynamic_targets, str):
            route_targets.update(dynamic_targets)
    return frozenset(route_targets)


def _selected_route_payload_schema_ids(
    selected_plan: SelectedCompiledPlan,
    *,
    queue_family_id: QueueFamilyId,
    stage_kind_id: StageKindId,
    graph_node_id: str,
    runner_binding_id: RunnerBindingId,
) -> frozenset[ArtifactSchemaId]:
    schema_ids: set[ArtifactSchemaId] = set()
    for route in _selected_enqueue_routes(selected_plan):
        if (
            route.queue_family_id == queue_family_id
            and route.stage_kind_id == stage_kind_id
            and route.graph_node_id == graph_node_id
            and route.runner_binding_id == runner_binding_id
            and route.payload_schema_id is not None
        ):
            schema_ids.add(route.payload_schema_id)
    for action in selected_plan.terminal_actions:
        if (
            action.emitted_queue_family_id == queue_family_id
            and action.target_stage_kind_id == stage_kind_id
            and action.target_graph_node_id == graph_node_id
            and action.runner_binding_id == runner_binding_id
            and action.artifact_schema_id is not None
        ):
            schema_ids.add(action.artifact_schema_id)
    return frozenset(schema_ids)


def _dynamic_route_target_tuples(
    action: TerminalActionDeclaration,
) -> tuple[tuple[QueueFamilyId, StageKindId, str, RunnerBindingId], ...] | str:
    selector = action.dynamic_target_selector
    if selector is None:
        return ()
    if action.action_kind not in {"route", "create_incident_route"}:
        return f"dynamic_route_action_kind:{action.id}"
    if not isinstance(selector, Mapping):
        return f"dynamic_route_selector:{action.id}"
    if selector.get("kind") != "observation_payload_route_target":
        return f"dynamic_route_selector:{action.id}"
    field_names = selector.get("field_names")
    if (
        not isinstance(field_names, tuple)
        or not field_names
        or any(not _non_blank_text(field_name) for field_name in field_names)
        or len(set(field_names)) != len(field_names)
    ):
        return f"dynamic_route_selector:{action.id}"
    targets = selector.get("targets")
    if not isinstance(targets, Mapping) or not targets:
        return f"dynamic_route_selector:{action.id}"
    raw_disallowed_targets = selector.get("disallowed_targets", ())
    if not isinstance(raw_disallowed_targets, tuple) or any(
        not _non_blank_text(target_name) for target_name in raw_disallowed_targets
    ):
        return f"dynamic_route_selector:{action.id}"
    disallowed_targets = frozenset(cast(str, item) for item in raw_disallowed_targets)
    selected_targets: list[tuple[QueueFamilyId, StageKindId, str, RunnerBindingId]] = []
    for key, target in targets.items():
        if not _non_blank_text(key) or not isinstance(target, Mapping):
            return f"dynamic_route_selector:{action.id}"
        if key in disallowed_targets:
            return f"dynamic_route_disallowed_target:{action.id}"
        raw_queue_id = target.get("emitted_queue_family_id")
        raw_stage_id = target.get("target_stage_kind_id")
        raw_node_id = target.get("target_graph_node_id")
        raw_runner_id = target.get("runner_binding_id")
        if not (
            _non_blank_text(raw_queue_id)
            and _non_blank_text(raw_stage_id)
            and _non_blank_text(raw_node_id)
            and _non_blank_text(raw_runner_id)
        ):
            return f"dynamic_route_selector:{action.id}"
        selected_targets.append(
            (
                QueueFamilyId(cast(str, raw_queue_id)),
                StageKindId(cast(str, raw_stage_id)),
                cast(str, raw_node_id),
                RunnerBindingId(cast(str, raw_runner_id)),
            )
        )
    return tuple(selected_targets)


def _non_blank_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _activation_authority_refusal(
    *,
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
    work_item: WorkItem,
    recovery_attempt: RecoveryAttemptRecord | None,
) -> str | None:
    if activation.queue_family_id != work_item.queue_family_id:
        return f"activation_queue_family:{activation.activation_id}"
    graph_node_ids = {
        node_id for graph in selected_plan.graphs for node_id in graph.node_ids
    }
    if activation.graph_node_id not in graph_node_ids:
        return f"activation_graph_node_missing:{activation.graph_node_id}"
    activation_route_target = (
        activation.queue_family_id,
        activation.stage_kind_id,
        activation.graph_node_id,
        activation.runner_binding_id,
    )
    if activation_route_target not in _selected_route_targets(selected_plan):
        if _is_selected_recovery_activation(
            selected_plan=selected_plan,
            activation=activation,
            recovery_attempt=recovery_attempt,
        ):
            return None
        return f"activation_route_target:{activation.activation_id}"
    return None


def _is_selected_recovery_activation(
    *,
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
    recovery_attempt: RecoveryAttemptRecord | None,
) -> bool:
    if recovery_attempt is None:
        return False
    policy = next(
        (
            policy
            for policy in selected_plan.recovery_policies
            if policy.id == recovery_attempt.policy_id
        ),
        None,
    )
    action = next(
        (
            action
            for action in selected_plan.terminal_actions
            if action.id == recovery_attempt.recovery_action_id
        ),
        None,
    )
    if policy is None or action is None:
        return False
    return (
        _recovery_action_matches_policy_source(
            selected_plan,
            policy,
            recovery_attempt.recovery_action_id,
        )
        and action.action_kind == "recovery_route"
        and activation.queue_family_id == recovery_attempt.source_queue_family_id
        and activation.stage_kind_id == policy.recovery_stage_kind_id
        and activation.stage_kind_id == action.target_stage_kind_id
        and activation.graph_node_id == action.target_graph_node_id
        and activation.runner_binding_id == action.runner_binding_id
    )


def _recovery_action_matches_policy_source(
    selected_plan: SelectedCompiledPlan,
    policy: RecoveryPolicyDeclaration,
    action_id: ActionId,
) -> bool:
    action = next(
        (
            candidate
            for candidate in selected_plan.terminal_actions
            if candidate.id == action_id
        ),
        None,
    )
    if (
        action is None
        or action.action_kind != "recovery_route"
        or action.target_stage_kind_id != policy.recovery_stage_kind_id
    ):
        return False
    if action_id in policy.source_recovery_action_ids:
        return True
    return any(
        counter.threshold_action_id == action_id
        and counter.increment_action_id in policy.source_recovery_action_ids
        for counter in selected_plan.counters
    )


def _decide_enqueue(
    state: RuntimeState,
    transition_input: EnqueueWork,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    default_plan_ref = state.default_plan_ref
    if default_plan_ref is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="missing_default_plan",
        )

    admitted = state.admitted_plans.get(default_plan_ref.authority_fingerprint)
    if admitted is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unknown_plan_ref",
        )

    route = admitted.external_enqueue_routes.get(transition_input.queue_family_id)
    if route is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="queue_family_not_external",
        )
    if route.payload_schema_id is not None:
        schema = artifact_schema_for(
            admitted.selected_plan,
            str(route.payload_schema_id),
        )
        if schema is None:
            return _refused_decision(
                transition_input=transition_input,
                context=context,
                digest=digest,
                reason="unknown_enqueue_payload_schema",
            )
        validation = validate_schema(schema.schema, transition_input.payload)
        if not validation.accepted:
            return _refused_decision(
                transition_input=transition_input,
                context=context,
                digest=digest,
                reason="invalid_enqueue_payload_schema",
            )

    work_item_ref = WorkItemRef(
        work_item_id=context.work_item_id,
        plan_ref=default_plan_ref,
        generation=0,
    )
    lineage_id = (
        None
        if admitted.selected_plan.lineage_policy == "none"
        else work_item_ref.work_item_id
    )
    work_item = WorkItem(
        ref=work_item_ref,
        queue_family_id=route.queue_family_id,
        payload=transition_input.payload,
        lineage_id=lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    activation = Activation(
        activation_id=context.activation_id,
        work_item_id=work_item_ref.work_item_id,
        lineage_id=lineage_id,
        plan_ref=default_plan_ref,
        queue_family_id=route.queue_family_id,
        graph_node_id=route.graph_node_id,
        stage_kind_id=route.stage_kind_id,
        runner_binding_id=route.runner_binding_id,
        generation=0,
        created_by_input_id=transition_input.input_id,
    )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            CreateWorkItem(work_item),
            CreateActivation(activation),
        ),
        expected_plan_fingerprint=default_plan_ref.authority_fingerprint,
    )


def _decide_fanout_from_artifact(
    state: RuntimeState,
    transition_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    source_context = _fanout_source_context(
        state,
        transition_input,
        context,
        digest,
    )
    if isinstance(source_context, TransitionDecision):
        return source_context
    artifact = source_context.artifact
    source_run = source_context.run
    source_work_item = source_context.work_item
    selected_plan = source_context.selected_plan
    fanout = fanout_for(selected_plan, transition_input.fanout_id)
    if fanout is None:
        return _fanout_context_refusal(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_fanout",
            source_run=source_run,
            source_work_item=source_work_item,
        )

    assessment_refusal = _fanout_assessment_refusal(
        state,
        transition_input,
        context,
        digest,
        source_context=source_context,
        fanout=fanout,
    )
    if assessment_refusal is not None:
        return assessment_refusal

    items = _fanout_items_or_refusal(
        transition_input,
        context,
        digest,
        artifact=artifact,
        source_run=source_run,
        source_work_item=source_work_item,
        fanout=fanout,
    )
    if isinstance(items, TransitionDecision):
        return items

    target_schema = _fanout_target_schema_or_refusal(
        selected_plan,
        transition_input,
        context,
        digest,
        source_run=source_run,
        source_work_item=source_work_item,
        fanout=fanout,
    )
    if isinstance(target_schema, TransitionDecision):
        return target_schema

    mutations: list[TransitionMutation] = []
    for item_key, raw_item in items:
        payload = _fanout_target_payload_or_refusal(
            transition_input,
            context,
            digest,
            source_artifact_payload=artifact.payload,
            source_run=source_run,
            source_work_item=source_work_item,
            fanout=fanout,
            target_schema=target_schema,
            raw_item=raw_item,
        )
        if isinstance(payload, TransitionDecision):
            return payload
        item_mutations = _fanout_mutations_for_item(
            state,
            transition_input,
            context,
            digest,
            artifact=artifact,
            source_run=source_run,
            source_work_item=source_work_item,
            fanout=fanout,
            item_key=item_key,
            payload=payload,
        )
        if isinstance(item_mutations, TransitionDecision):
            return item_mutations
        mutations.extend(item_mutations)

    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=tuple(mutations),
        expected_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        expected_run_generations={
            source_run.run_ref.run_id: source_run.run_ref.generation,
        },
        expected_work_item_plan_refs={
            source_work_item.ref.work_item_id: source_work_item.ref.plan_ref,
        },
        event_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        event_work_item_id=source_work_item.ref.work_item_id,
        event_run_id=source_run.run_ref.run_id,
        event_action_id=fanout.source_action_id,
        event_authority_source="fanout_declaration",
    )


def _fanout_source_context(
    state: RuntimeState,
    transition_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
) -> SourceContext | TransitionDecision:
    artifact = state.artifacts.get(transition_input.source_artifact_id)
    if artifact is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="missing_source_artifact",
        )
    source_context = source_context_for_artifact(state, artifact)
    if isinstance(source_context, PolicyAssessment):
        source_run = state.runs.get(artifact.source_run_id)
        source_work_item = state.work_items.get(artifact.work_item_id)
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=source_context.reason_code or "wrong_source_artifact",
            detail=source_context.detail,
            event_plan_fingerprint=(
                None
                if source_run is None
                else source_run.run_ref.plan_ref.authority_fingerprint
            ),
            event_work_item_id=(
                None
                if source_work_item is None
                else source_work_item.ref.work_item_id
            ),
            event_run_id=None if source_run is None else source_run.run_ref.run_id,
        )
    source_run = source_context.run
    source_work_item = source_context.work_item
    selected_plan = source_context.selected_plan
    authority_refusal = _selected_authority_refusal(selected_plan)
    if authority_refusal is not None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_selected_authority",
            detail=authority_refusal,
            event_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
            event_work_item_id=source_work_item.ref.work_item_id,
            event_run_id=source_run.run_ref.run_id,
        )
    return source_context


def _fanout_assessment_refusal(
    state: RuntimeState,
    transition_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
    *,
    source_context: SourceContext,
    fanout: FanoutDeclaration,
) -> TransitionDecision | None:
    assessment = assess_fanout(state, source_context, fanout)
    if assessment.status == "ready":
        return None
    if assessment.status == "not_ready":
        reason = "source_work_item_not_closed"
    elif assessment.status == "complete":
        reason = "fanout_already_applied"
    else:
        reason = assessment.reason_code or "fanout_partial_state"
    return _fanout_context_refusal(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=reason,
        detail=assessment.detail,
        source_run=source_context.run,
        source_work_item=source_context.work_item,
        action_id=fanout.source_action_id,
    )


def _fanout_items_or_refusal(
    transition_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
    *,
    artifact: ArtifactRecord,
    source_run: RunRecord,
    source_work_item: WorkItem,
    fanout: FanoutDeclaration,
) -> FanoutItems | TransitionDecision:
    items = fanout_items(artifact, fanout)
    if items is not None:
        return items
    return _fanout_payload_refusal(
        transition_input,
        context,
        digest,
        source_run,
        source_work_item,
        fanout.source_action_id,
    )


def _fanout_target_schema_or_refusal(
    selected_plan: SelectedCompiledPlan,
    transition_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
    *,
    source_run: RunRecord,
    source_work_item: WorkItem,
    fanout: FanoutDeclaration,
) -> ArtifactSchemaDeclaration | TransitionDecision:
    target_schema = artifact_schema_for(
        selected_plan,
        str(fanout.target_payload_schema_id),
    )
    if target_schema is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_selected_authority",
            detail=f"fanout_target_schema:{fanout.id}",
            event_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
            event_work_item_id=source_work_item.ref.work_item_id,
            event_run_id=source_run.run_ref.run_id,
            event_action_id=fanout.source_action_id,
        )
    return target_schema


def _fanout_target_payload_or_refusal(
    transition_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
    *,
    source_artifact_payload: Mapping[str, AuthorityValue],
    source_run: RunRecord,
    source_work_item: WorkItem,
    fanout: FanoutDeclaration,
    target_schema: ArtifactSchemaDeclaration,
    raw_item: Mapping[object, object],
) -> Mapping[str, AuthorityValue] | TransitionDecision:
    payload = fanout_target_payload(
        fanout.target_payload_mapping,
        raw_item,
        source_artifact_payload,
    )
    if payload is None or not validate_schema(target_schema.schema, payload).accepted:
        return _fanout_payload_refusal(
            transition_input,
            context,
            digest,
            source_run,
            source_work_item,
            fanout.source_action_id,
        )
    return payload


def _fanout_mutations_for_item(
    state: RuntimeState,
    transition_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
    *,
    artifact: ArtifactRecord,
    source_run: RunRecord,
    source_work_item: WorkItem,
    fanout: FanoutDeclaration,
    item_key: str,
    payload: Mapping[str, AuthorityValue],
) -> tuple[TransitionMutation, ...] | TransitionDecision:
    identity = fanout_item_identity(
        plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        fanout_id=str(fanout.id),
        source_artifact_id=artifact.artifact_id,
        item_key=item_key,
    )
    target_work_item_id = identity.target_work_item_id
    target_activation_id = identity.target_activation_id
    fanout_record_id = identity.fanout_record_id
    dependency_id = identity.dependency_id
    route_record_id = identity.route_record_id
    if (
        target_work_item_id in state.work_items
        or target_activation_id in state.activations
        or fanout_record_id in state.fanout_records
        or dependency_id in state.work_dependencies
        or any(route.record_id == route_record_id for route in state.activation_routes)
    ):
        return _fanout_context_refusal(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="fanout_partial_state",
            source_run=source_run,
            source_work_item=source_work_item,
            action_id=fanout.source_action_id,
        )
    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id=target_work_item_id,
            plan_ref=source_run.run_ref.plan_ref,
            generation=0,
        ),
        queue_family_id=fanout.target_queue_family_id,
        payload=payload,
        lineage_id=source_work_item.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    activation = Activation(
        activation_id=target_activation_id,
        work_item_id=target_work_item_id,
        lineage_id=source_work_item.lineage_id,
        plan_ref=source_run.run_ref.plan_ref,
        queue_family_id=fanout.target_queue_family_id,
        graph_node_id=fanout.target_graph_node_id,
        stage_kind_id=fanout.target_stage_kind_id,
        runner_binding_id=fanout.target_runner_binding_id,
        generation=0,
        created_by_input_id=transition_input.input_id,
    )
    route = ActivationRouteRecord(
        record_id=route_record_id,
        action_id=fanout.source_action_id,
        source_run_id=source_run.run_ref.run_id,
        source_work_item_id=source_work_item.ref.work_item_id,
        target_work_item_id=target_work_item_id,
        target_activation_id=target_activation_id,
        created_by_input_id=transition_input.input_id,
    )
    fanout_record = FanoutRecord(
        record_id=fanout_record_id,
        fanout_id=fanout.id,
        source_artifact_id=artifact.artifact_id,
        source_artifact_digest=artifact.payload_digest,
        source_work_item_id=source_work_item.ref.work_item_id,
        source_run_id=source_run.run_ref.run_id,
        source_action_id=fanout.source_action_id,
        target_work_item_id=target_work_item_id,
        target_activation_id=target_activation_id,
        target_queue_family_id=fanout.target_queue_family_id,
        target_stage_kind_id=fanout.target_stage_kind_id,
        target_graph_node_id=fanout.target_graph_node_id,
        item_key=item_key,
        lineage_id=source_work_item.lineage_id,
        selected_plan_ref=source_run.run_ref.plan_ref,
        created_by_input_id=transition_input.input_id,
    )
    mutations: tuple[TransitionMutation, ...] = (
        CreateWorkItem(work_item),
        CreateActivation(activation),
        RouteActivation(record_id=route.record_id, route=route),
        RecordFanout(record_id=fanout_record.record_id, record=fanout_record),
    )
    if fanout.dependency_policy != "depends_on_source_work_item":
        return mutations
    dependency = WorkDependencyRecord(
        dependency_id=dependency_id,
        dependent_work_item_id=target_work_item_id,
        dependency_work_item_id=source_work_item.ref.work_item_id,
        selected_plan_ref=source_run.run_ref.plan_ref,
        lineage_id=source_work_item.lineage_id,
        fanout_record_id=fanout_record_id,
        created_by_input_id=transition_input.input_id,
    )
    return (
        *mutations,
        RecordWorkDependency(
            record_id=dependency.dependency_id,
            record=dependency,
        ),
    )


def _fanout_context_refusal(
    *,
    transition_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
    reason: str,
    source_run: RunRecord,
    source_work_item: WorkItem,
    action_id: ActionId | None = None,
    detail: str | None = None,
) -> TransitionDecision:
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=reason,
        detail=detail,
        event_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        event_work_item_id=source_work_item.ref.work_item_id,
        event_run_id=source_run.run_ref.run_id,
        event_action_id=action_id,
    )


def _fanout_payload_refusal(
    transition_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
    source_run: RunRecord,
    source_work_item: WorkItem,
    source_action_id: ActionId,
) -> TransitionDecision:
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason="invalid_fanout_payload",
        event_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        event_work_item_id=source_work_item.ref.work_item_id,
        event_run_id=source_run.run_ref.run_id,
        event_action_id=source_action_id,
    )


def _has_unready_dependency(state: RuntimeState, work_item: WorkItem) -> bool:
    for dependency in state.work_dependencies.values():
        if dependency.dependent_work_item_id != work_item.ref.work_item_id:
            continue
        if dependency.selected_plan_ref != work_item.ref.plan_ref:
            continue
        if dependency.dependency_work_item_id not in state.closed_work_items:
            return True
    return False


def _decide_claim(
    state: RuntimeState,
    transition_input: ClaimWork,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    if state.pause is not None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="workspace_paused",
        )

    activation = state.activations.get(transition_input.activation_id)
    if activation is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="missing_activation",
        )

    work_item = state.work_items.get(activation.work_item_id)
    if work_item is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="missing_work_item",
        )
    if (
        active_lineage_quarantine_for(
            state,
            work_item.lineage_id,
            plan_ref=work_item.ref.plan_ref,
        )
        is not None
    ):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="lineage_quarantined",
            event_plan_fingerprint=work_item.ref.plan_ref.authority_fingerprint,
            event_work_item_id=work_item.ref.work_item_id,
        )
    if (
        active_operator_wait_for(
            state,
            work_item.lineage_id,
            plan_ref=work_item.ref.plan_ref,
        )
        is not None
    ):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="operator_wait_active",
            event_plan_fingerprint=work_item.ref.plan_ref.authority_fingerprint,
            event_work_item_id=work_item.ref.work_item_id,
        )
    if work_item.ref.work_item_id in state.closed_work_items:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="work_item_closed",
            event_plan_fingerprint=work_item.ref.plan_ref.authority_fingerprint,
            event_work_item_id=work_item.ref.work_item_id,
        )
    if _has_unready_dependency(state, work_item):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="dependency_not_ready",
            event_plan_fingerprint=work_item.ref.plan_ref.authority_fingerprint,
            event_work_item_id=work_item.ref.work_item_id,
        )
    if activation.claimed_by_run_id is not None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="stale_activation",
        )

    admitted = state.admitted_plans.get(activation.plan_ref.authority_fingerprint)
    if admitted is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unknown_plan_ref",
            event_plan_fingerprint=activation.plan_ref.authority_fingerprint,
            event_work_item_id=work_item.ref.work_item_id,
        )
    authority_refusal = _selected_authority_refusal(admitted.selected_plan)
    if authority_refusal is not None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_selected_authority",
            detail=authority_refusal,
            event_plan_fingerprint=activation.plan_ref.authority_fingerprint,
            event_work_item_id=work_item.ref.work_item_id,
        )
    recovery_attempt = _recovery_attempt_for_activation(state, activation)
    activation_authority_refusal = _activation_authority_refusal(
        selected_plan=admitted.selected_plan,
        activation=activation,
        work_item=work_item,
        recovery_attempt=recovery_attempt,
    )
    if activation_authority_refusal is not None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_selected_authority",
            detail=activation_authority_refusal,
            event_plan_fingerprint=activation.plan_ref.authority_fingerprint,
            event_work_item_id=work_item.ref.work_item_id,
        )
    concurrency_refusal = _claim_concurrency_refusal(
        state,
        selected_plan=admitted.selected_plan,
        activation=activation,
        work_item=work_item,
    )
    if concurrency_refusal is not None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="concurrency_policy_blocked",
            detail=concurrency_refusal,
            event_plan_fingerprint=activation.plan_ref.authority_fingerprint,
            event_work_item_id=work_item.ref.work_item_id,
        )
    capability_refusal, capability_refusal_detail = _claim_capability_refusal(
        admitted.selected_plan,
        str(activation.runner_binding_id),
        str(activation.stage_kind_id),
    )
    if capability_refusal is not None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=capability_refusal,
            detail=capability_refusal_detail,
            event_plan_fingerprint=activation.plan_ref.authority_fingerprint,
            event_work_item_id=work_item.ref.work_item_id,
        )

    run = _run_for_claim(
        transition_input=transition_input,
        context=context,
        activation=activation,
        work_item=work_item,
    )
    mutations: tuple[TransitionMutation, ...] = (CreateRun(run),)
    if recovery_attempt is not None:
        updated_attempt = replace(
            recovery_attempt,
            latest_recovery_run_id=run.run_ref.run_id,
            updated_by_input_id=transition_input.input_id,
        )
        mutations = (
            *mutations,
            RecordRecoveryAttempt(
                record_id=updated_attempt.record_id,
                attempt=updated_attempt,
            ),
        )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=mutations,
        expected_plan_fingerprint=activation.plan_ref.authority_fingerprint,
        expected_work_item_generations={
            work_item.ref.work_item_id: work_item.ref.generation
        },
        expected_activation_generations={
            activation.activation_id: activation.generation
        },
        expected_activation_unclaimed=(activation.activation_id,),
        # Claim decisions must not apply after an independently accepted
        # pause/quarantine action has stopped new claims.
        expected_pause_absent=True,
        expected_lineage_quarantine_absent=(
            (
                lineage_quarantine_scope_key(
                    work_item.ref.plan_ref,
                    work_item.lineage_id,
                ),
            )
            if work_item.lineage_id is not None
            else ()
        ),
        expected_operator_wait_absent=(
            (
                operator_wait_scope_key(
                    work_item.ref.plan_ref,
                    work_item.lineage_id,
                ),
            )
            if work_item.lineage_id is not None
            else ()
        ),
        expected_work_item_open=(work_item.ref.work_item_id,),
        expected_work_item_plan_refs={
            work_item.ref.work_item_id: work_item.ref.plan_ref
        },
        expected_activation_plan_refs={activation.activation_id: activation.plan_ref},
    )


def _recovery_attempt_for_activation(
    state: RuntimeState,
    activation: Activation,
) -> RecoveryAttemptRecord | None:
    return next(
        (
            attempt
            for attempt in state.recovery_attempts.values()
            if attempt.plan_ref == activation.plan_ref
            and attempt.lineage_id == activation.lineage_id
            and attempt.latest_recovery_activation_id == activation.activation_id
            and attempt.phase in {"active_recovery", "quarantine_eligible"}
        ),
        None,
    )


def _claim_concurrency_refusal(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
    work_item: WorkItem,
) -> str | None:
    if not selected_plan.concurrency_policies:
        return None
    candidate_stage = stage_kind_for(selected_plan, str(activation.stage_kind_id))
    if candidate_stage is None or candidate_stage.partition_id is None:
        return None
    policies_by_partition = {
        policy.partition_id: policy for policy in selected_plan.concurrency_policies
    }
    candidate_policy = policies_by_partition.get(candidate_stage.partition_id)
    if candidate_policy is None:
        return None

    same_partition_count = 0
    for active_run in _active_unobserved_runs_for_plan(state, work_item.ref.plan_ref):
        active_activation = state.activations.get(active_run.activation_id)
        active_work_item = state.work_items.get(active_run.work_item_id)
        if active_activation is None or active_work_item is None:
            continue
        active_stage = stage_kind_for(
            selected_plan,
            str(active_activation.stage_kind_id),
        )
        if active_stage is None or active_stage.partition_id is None:
            continue
        active_partition_id = active_stage.partition_id
        if active_partition_id == candidate_stage.partition_id:
            same_partition_count += 1
            continue
        active_policy = policies_by_partition.get(active_partition_id)
        candidate_allows_active = (
            active_partition_id in candidate_policy.coexist_partition_ids
        )
        active_allows_candidate = (
            active_policy is None
            or candidate_stage.partition_id in active_policy.coexist_partition_ids
        )
        if not (candidate_allows_active and active_allows_candidate):
            return (
                f"partition_coexist:{candidate_stage.partition_id}:"
                f"{active_partition_id}"
            )
    if same_partition_count >= candidate_policy.max_active_runs:
        return f"partition_max_active:{candidate_stage.partition_id}"
    return None


def _active_unobserved_runs_for_plan(
    state: RuntimeState,
    plan_ref: PlanRef,
) -> tuple[RunRecord, ...]:
    active: list[RunRecord] = []
    for run in state.runs.values():
        if run.run_ref.plan_ref != plan_ref or run_has_observation(
            state,
            run.run_ref.run_id,
        ):
            continue
        if run.work_item_id in state.closed_work_items:
            continue
        activation = state.activations.get(run.activation_id)
        if activation is None or activation.claimed_by_run_id != run.run_ref.run_id:
            continue
        active.append(run)
    return tuple(active)


def _claim_capability_refusal(
    selected_plan: SelectedCompiledPlan,
    runner_binding_id: str,
    stage_kind_id: str,
) -> tuple[str | None, str | None]:
    stage = stage_kind_for(selected_plan, stage_kind_id)
    if stage is None:
        return "missing_stage_kind", None
    selected_asset_ids = {asset.id for asset in selected_plan.assets}
    if any(asset_id not in selected_asset_ids for asset_id in stage.asset_ids):
        return "missing_selected_asset", None

    runner_binding = runner_binding_for(selected_plan, runner_binding_id)
    if runner_binding is None:
        return "missing_runner_binding", None
    capabilities = {
        capability.id: capability for capability in selected_plan.capabilities
    }
    for capability in capabilities.values():
        capability_refusal = _capability_authority_refusal(capability)
        if capability_refusal is not None:
            return "unsupported_selected_authority", capability_refusal
    if capabilities and not runner_binding.required_capability_ids:
        return "missing_runner_invoke_capability", None

    required_capabilities = tuple(
        capabilities[capability_id]
        for capability_id in runner_binding.required_capability_ids
        if capability_id in capabilities
    )
    if required_capabilities and not any(
        capability.capability_kind == "runner.invoke"
        for capability in required_capabilities
    ):
        return "missing_runner_invoke_capability", None

    for capability_id in runner_binding.required_capability_ids:
        selected_capability = capabilities.get(capability_id)
        if selected_capability is None:
            return "missing_capability", None
        if selected_capability.support_status == "unsupported":
            return "capability_unsupported", None
        if selected_capability.grant_status == "approval_pending":
            return "capability_approval_pending", None
        if selected_capability.grant_status == "denied":
            return "capability_denied", None
    return None, None


def _decide_timer_due(
    state: RuntimeState,
    transition_input: TimerDue,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    wait = state.cooldown_waits.get(transition_input.wait_id)
    if wait is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unknown_wait",
        )
    if wait.consumed_input_id is not None:
        return _cooldown_wait_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="wait_already_consumed",
            wait=wait,
        )
    if transition_input.observed_at < wait.due_at:
        return _cooldown_wait_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="wait_not_due",
            wait=wait,
        )

    attempt = state.recovery_attempts.get(wait.recovery_attempt_record_id)
    work_item = state.work_items.get(wait.source_work_item_id)
    admitted = state.admitted_plans.get(wait.plan_ref.authority_fingerprint)
    if attempt is None or work_item is None or admitted is None:
        return _cooldown_wait_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_cooldown_wait",
            wait=wait,
        )
    action = _cooldown_wait_action(admitted.selected_plan, wait)
    if action is None or attempt.phase != "pending_cooldown":
        return _cooldown_wait_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_cooldown_wait",
            wait=wait,
        )

    target_activation = Activation(
        activation_id=context.activation_id,
        work_item_id=wait.source_work_item_id,
        lineage_id=wait.lineage_id,
        plan_ref=wait.plan_ref,
        queue_family_id=attempt.source_queue_family_id,
        graph_node_id=wait.target_graph_node_id,
        stage_kind_id=wait.target_stage_kind_id,
        runner_binding_id=wait.target_runner_binding_id,
        generation=work_item.ref.generation,
        created_by_input_id=transition_input.input_id,
    )
    route_record = ActivationRouteRecord(
        record_id=f"{context.transition_id}:route",
        action_id=wait.recovery_action_id,
        source_run_id=wait.source_run_id,
        source_work_item_id=wait.source_work_item_id,
        target_work_item_id=wait.source_work_item_id,
        target_activation_id=target_activation.activation_id,
        created_by_input_id=transition_input.input_id,
    )
    consumed_wait = replace(
        wait,
        consumed_input_id=transition_input.input_id,
        consumed_at=transition_input.observed_at,
        resulting_recovery_activation_id=target_activation.activation_id,
    )
    resumed_attempt = replace(
        attempt,
        phase="active_recovery",
        latest_recovery_activation_id=target_activation.activation_id,
        latest_recovery_run_id=None,
        latest_return_action_id=None,
        updated_by_input_id=transition_input.input_id,
    )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            CreateActivation(target_activation),
            RouteActivation(record_id=route_record.record_id, route=route_record),
            RecordRecoveryAttempt(
                record_id=resumed_attempt.record_id,
                attempt=resumed_attempt,
            ),
            RecordCooldownWait(record_id=consumed_wait.wait_id, wait=consumed_wait),
        ),
        expected_plan_fingerprint=wait.plan_ref.authority_fingerprint,
        expected_work_item_generations={
            work_item.ref.work_item_id: work_item.ref.generation
        },
        expected_work_item_plan_refs={
            work_item.ref.work_item_id: work_item.ref.plan_ref
        },
        event_plan_fingerprint=wait.plan_ref.authority_fingerprint,
        event_work_item_id=wait.source_work_item_id,
        event_run_id=wait.source_run_id,
        event_action_id=wait.recovery_action_id,
        event_authority_source=AUTHORITY_SOURCE_TERMINAL_ACTION,
    )


def _cooldown_wait_action(
    selected_plan: SelectedCompiledPlan,
    wait: CooldownWaitRecord,
) -> TerminalActionDeclaration | None:
    policy = next(
        (
            policy
            for policy in selected_plan.recovery_policies
            if policy.id == wait.policy_id
        ),
        None,
    )
    if policy is None or not _recovery_action_matches_policy_source(
        selected_plan,
        policy,
        wait.recovery_action_id,
    ):
        return None
    wait_state = wait_state_for_policy(selected_plan, str(policy.id))
    if (
        wait_state is None
        or policy.cooldown_wait_state_id != wait_state.id
        or wait.attempt_count < wait_state.starts_at_attempt
        or wait.due_at - wait.created_at != wait_state.duration_seconds
    ):
        return None
    action = next(
        (
            action
            for action in selected_plan.terminal_actions
            if action.id == wait.recovery_action_id
        ),
        None,
    )
    if action is None:
        return None
    if (
        action.target_stage_kind_id != wait.target_stage_kind_id
        or action.target_graph_node_id != wait.target_graph_node_id
        or action.runner_binding_id != wait.target_runner_binding_id
        or action.target_stage_kind_id != policy.recovery_stage_kind_id
    ):
        return None
    return action


def _cooldown_wait_refused_decision(
    *,
    transition_input: TimerDue,
    context: TransitionContext,
    digest: str,
    reason: str,
    wait: CooldownWaitRecord,
) -> TransitionDecision:
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=reason,
        event_plan_fingerprint=wait.plan_ref.authority_fingerprint,
        event_work_item_id=wait.source_work_item_id,
        event_run_id=wait.source_run_id,
        event_action_id=wait.recovery_action_id,
        event_authority_source=AUTHORITY_SOURCE_TERMINAL_ACTION,
    )


_SUPPORTED_EFFECT_RECONCILIATION_STATUSES = frozenset(("applied", "no_op", "refused"))


def _decide_reconcile_effect(
    state: RuntimeState,
    transition_input: ReconcileEffect,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    proposal = state.effect_proposals.get(transition_input.effect_id)
    if proposal is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="effect_proposal_not_found",
        )
    if proposal.status != "pending":
        return _effect_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="effect_proposal_not_pending",
            proposal=proposal,
        )
    if transition_input.provider_ref != proposal.provider_ref:
        return _effect_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unselected_effect_provider",
            proposal=proposal,
        )
    if transition_input.status not in _SUPPORTED_EFFECT_RECONCILIATION_STATUSES:
        return _effect_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_effect_reconciliation_status",
            proposal=proposal,
        )
    if "requested_runtime_mutation" in transition_input.result:
        return _effect_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="effect_result_requests_runtime_mutation",
            proposal=proposal,
        )

    result_digest = operator_payload_digest(transition_input.result)
    existing = _reconciliation_for_effect(state, transition_input.effect_id)
    if existing is not None:
        if (
            existing.provider_ref == transition_input.provider_ref
            and existing.status == transition_input.status
            and existing.fake_local_result_digest == result_digest
        ):
            return _accepted_decision(
                transition_input=transition_input,
                context=context,
                digest=digest,
                mutations=(),
                expected_plan_fingerprint=proposal.selected_plan_fingerprint,
                event_plan_fingerprint=proposal.selected_plan_fingerprint,
                event_work_item_id=proposal.source_work_item_id,
                event_run_id=proposal.source_run_id,
                event_action_id=proposal.terminal_action_id,
                event_authority_source=AUTHORITY_SOURCE_TERMINAL_ACTION,
            )
        return _effect_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="effect_reconciliation_conflict",
            proposal=proposal,
        )

    record = EffectReconciliationRecord(
        reconciliation_id=f"{context.transition_id}:reconciliation",
        effect_id=proposal.effect_id,
        selected_plan_ref=proposal.selected_plan_ref,
        selected_plan_fingerprint=proposal.selected_plan_fingerprint,
        provider_ref=transition_input.provider_ref,
        status=transition_input.status,
        fake_local_result_digest=result_digest,
        created_input_id=transition_input.input_id,
        created_transition_id=context.transition_id,
    )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            RecordEffectReconciliation(
                record_id=record.reconciliation_id,
                record=record,
            ),
        ),
        expected_plan_fingerprint=proposal.selected_plan_fingerprint,
        event_plan_fingerprint=proposal.selected_plan_fingerprint,
        event_work_item_id=proposal.source_work_item_id,
        event_run_id=proposal.source_run_id,
        event_action_id=proposal.terminal_action_id,
        event_authority_source=AUTHORITY_SOURCE_TERMINAL_ACTION,
    )


def _reconciliation_for_effect(
    state: RuntimeState,
    effect_id: str,
) -> EffectReconciliationRecord | None:
    matches = tuple(
        record
        for record in state.effect_reconciliations.values()
        if record.effect_id == effect_id
    )
    return matches[0] if len(matches) == 1 else None


def _effect_refused_decision(
    *,
    transition_input: ReconcileEffect,
    context: TransitionContext,
    digest: str,
    reason: str,
    proposal: EffectProposalRecord,
) -> TransitionDecision:
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=reason,
        event_plan_fingerprint=proposal.selected_plan_fingerprint,
        event_work_item_id=proposal.source_work_item_id,
        event_run_id=proposal.source_run_id,
        event_action_id=proposal.terminal_action_id,
        event_authority_source=AUTHORITY_SOURCE_TERMINAL_ACTION,
    )


def _decide_open_closure_target(
    state: RuntimeState,
    transition_input: OpenClosureTarget,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    resolved = _completion_behavior_context(
        state,
        transition_input.selected_plan_ref,
        transition_input.completion_behavior_id,
    )
    if isinstance(resolved, str):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=resolved,
        )
    selected_plan, behavior = resolved
    refusal = _open_closure_target_refusal(
        state=state,
        transition_input=transition_input,
        selected_plan=selected_plan,
        behavior=behavior,
    )
    if refusal is not None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=refusal,
            event_plan_fingerprint=(
                transition_input.selected_plan_ref.authority_fingerprint
            ),
        )
    record = ClosureTargetRecord(
        closure_target_id=transition_input.closure_target_id,
        selected_plan_ref=transition_input.selected_plan_ref,
        completion_behavior_id=behavior.id,
        lineage_id=transition_input.lineage_id,
        root_source_kind=transition_input.root_source_kind,
        root_source_id=transition_input.root_source_id,
        closure_root_work_item_id=transition_input.closure_root_work_item_id,
        request_kind=transition_input.request_kind,
        target_graph_node_id=transition_input.target_graph_node_id,
        evidence_window=transition_input.evidence_window,
        status="open",
        opened_by_input_id=transition_input.input_id,
    )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            RecordClosureTarget(
                record_id=record.closure_target_id,
                record=record,
            ),
        ),
        expected_plan_fingerprint=(
            transition_input.selected_plan_ref.authority_fingerprint
        ),
        event_plan_fingerprint=transition_input.selected_plan_ref.authority_fingerprint,
        event_authority_source="completion_behavior",
    )


def _open_closure_target_refusal(
    *,
    state: RuntimeState,
    transition_input: OpenClosureTarget,
    selected_plan: SelectedCompiledPlan,
    behavior: CompletionBehaviorDeclaration,
) -> str | None:
    if transition_input.closure_target_id in state.closure_targets:
        return "closure_target_exists"
    if transition_input.request_kind != behavior.request_kind:
        return "closure_request_kind_mismatch"
    if transition_input.target_graph_node_id != behavior.target_graph_node_id:
        return "closure_target_mismatch"
    if transition_input.root_source_kind not in behavior.accepted_root_source_kinds:
        return "unsupported_closure_root_source"
    root_inventory_refusal = _closure_root_inventory_refusal(
        state=state,
        transition_input=transition_input,
        selected_plan=selected_plan,
        behavior=behavior,
    )
    if root_inventory_refusal is not None:
        return root_inventory_refusal
    if not _closure_evidence_window_matches(
        behavior,
        transition_input.lineage_id,
        transition_input.evidence_window,
    ):
        return "invalid_closure_evidence_window"
    if _selected_authority_refusal(selected_plan) is not None:
        return "unsupported_selected_authority"
    return None


def _closure_root_inventory_refusal(
    *,
    state: RuntimeState,
    transition_input: OpenClosureTarget,
    selected_plan: SelectedCompiledPlan,
    behavior: CompletionBehaviorDeclaration,
) -> str | None:
    return _closure_root_refusal(
        state=state,
        selected_plan=selected_plan,
        behavior=behavior,
        selected_plan_ref=transition_input.selected_plan_ref,
        lineage_id=transition_input.lineage_id,
        root_source_kind=transition_input.root_source_kind,
        root_source_id=transition_input.root_source_id,
        closure_root_work_item_id=transition_input.closure_root_work_item_id,
    )


def _closure_target_root_refusal(
    *,
    state: RuntimeState,
    target: ClosureTargetRecord,
    selected_plan: SelectedCompiledPlan,
    behavior: CompletionBehaviorDeclaration,
) -> str | None:
    return _closure_root_refusal(
        state=state,
        selected_plan=selected_plan,
        behavior=behavior,
        selected_plan_ref=target.selected_plan_ref,
        lineage_id=target.lineage_id,
        root_source_kind=target.root_source_kind,
        root_source_id=target.root_source_id,
        closure_root_work_item_id=target.closure_root_work_item_id,
    )


def _closure_root_refusal(
    *,
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    behavior: CompletionBehaviorDeclaration,
    selected_plan_ref: PlanRef,
    lineage_id: str,
    root_source_kind: str,
    root_source_id: str,
    closure_root_work_item_id: str | None,
) -> str | None:
    if behavior.root_source_resolution != "runtime_inventory":
        return None
    if root_source_kind == "manual":
        if closure_root_work_item_id is not None:
            return "manual_closure_root_work_item_unsupported"
        return None
    if closure_root_work_item_id is None:
        return "missing_closure_root_work_item"
    root_inventory_queue_ids = {
        route.queue_family_id for route in selected_plan.external_enqueue_routes
    }
    matches = tuple(
        work_item
        for work_item in state.work_items.values()
        if work_item.queue_family_id in root_inventory_queue_ids
        and _work_item_root_source_matches(
            work_item,
            root_source_kind=root_source_kind,
            root_source_id=root_source_id,
        )
        and work_item.ref.plan_ref == selected_plan_ref
    )
    if not matches:
        return "missing_closure_root_source"
    if len(matches) > 1:
        return "ambiguous_closure_root_source"
    if matches[0].ref.work_item_id != closure_root_work_item_id:
        return "closure_root_work_item_mismatch"
    if matches[0].lineage_id != lineage_id:
        return "closure_root_lineage_mismatch"
    return None


def _work_item_root_source_matches(
    work_item: WorkItem,
    *,
    root_source_kind: str,
    root_source_id: str,
) -> bool:
    raw_root_source = work_item.payload.get("root_source")
    return (
        isinstance(raw_root_source, Mapping)
        and raw_root_source.get("kind") == root_source_kind
        and raw_root_source.get("source_id") == root_source_id
    )


def _decide_evaluate_completion_behavior(
    state: RuntimeState,
    transition_input: EvaluateCompletionBehavior,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    resolved = _completion_behavior_context(
        state,
        transition_input.selected_plan_ref,
        transition_input.completion_behavior_id,
    )
    if isinstance(resolved, str):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=resolved,
        )
    selected_plan, behavior = resolved
    target = state.closure_targets.get(transition_input.closure_target_id)
    if target is None:
        return _completion_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unknown_closure_target",
        )
    target_refusal = _closure_target_behavior_refusal(
        target=target,
        behavior=behavior,
        selected_plan_ref=transition_input.selected_plan_ref,
    )
    if target_refusal is not None:
        return _completion_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=target_refusal,
            target=target,
        )
    root_refusal = _closure_target_root_refusal(
        state=state,
        target=target,
        selected_plan=selected_plan,
        behavior=behavior,
    )
    if root_refusal is not None:
        return _completion_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=root_refusal,
            target=target,
        )
    if target.status != "open":
        return _completion_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="closure_target_closed",
            target=target,
        )
    if (
        _active_closure_evaluation_for_target(
            state,
            target.closure_target_id,
        )
        is not None
    ):
        return _completion_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="closure_evaluation_already_active",
            target=target,
        )
    if _has_open_lineage_work(
        state,
        lineage_id=target.lineage_id,
        plan_ref=target.selected_plan_ref,
    ):
        return _completion_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="closure_target_not_ready",
            target=target,
        )
    stage = stage_kind_for(selected_plan, str(behavior.target_stage_kind_id))
    if stage is None:
        return _completion_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_selected_authority",
            target=target,
        )
    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id=context.work_item_id,
            plan_ref=transition_input.selected_plan_ref,
            generation=0,
        ),
        queue_family_id=behavior.request_queue_family_id,
        payload=_completion_request_payload(
            target=target,
            behavior=behavior,
            stage=stage,
        ),
        lineage_id=target.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    activation = Activation(
        activation_id=context.activation_id,
        work_item_id=context.work_item_id,
        lineage_id=target.lineage_id,
        plan_ref=transition_input.selected_plan_ref,
        queue_family_id=behavior.request_queue_family_id,
        graph_node_id=behavior.target_graph_node_id,
        stage_kind_id=behavior.target_stage_kind_id,
        runner_binding_id=behavior.runner_binding_id,
        generation=0,
        created_by_input_id=transition_input.input_id,
    )
    evaluator_record = ClosureEvaluationRecord(
        record_id=f"closure-evaluator:{activation.activation_id}",
        closure_target_id=target.closure_target_id,
        completion_behavior_id=behavior.id,
        request_kind=behavior.request_kind,
        target_work_item_id=work_item.ref.work_item_id,
        target_activation_id=activation.activation_id,
        selected_plan_ref=transition_input.selected_plan_ref,
        lineage_id=target.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            CreateWorkItem(work_item),
            CreateActivation(activation),
            RecordClosureEvaluation(
                record_id=evaluator_record.record_id,
                record=evaluator_record,
            ),
        ),
        expected_plan_fingerprint=(
            transition_input.selected_plan_ref.authority_fingerprint
        ),
        event_plan_fingerprint=transition_input.selected_plan_ref.authority_fingerprint,
        event_work_item_id=work_item.ref.work_item_id,
        event_authority_source="completion_behavior",
    )


def _completion_behavior_context(
    state: RuntimeState,
    selected_plan_ref: PlanRef,
    completion_behavior_id: str,
) -> tuple[SelectedCompiledPlan, CompletionBehaviorDeclaration] | str:
    admitted = state.admitted_plans.get(selected_plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != selected_plan_ref:
        return "unknown_plan_ref"
    authority_refusal = _selected_authority_refusal(admitted.selected_plan)
    if authority_refusal is not None:
        return "unsupported_selected_authority"
    behavior = _completion_behavior_for(
        admitted.selected_plan,
        completion_behavior_id,
    )
    if behavior is None:
        return "unknown_completion_behavior"
    return admitted.selected_plan, behavior


def _completion_behavior_for(
    selected_plan: SelectedCompiledPlan,
    completion_behavior_id: str,
) -> CompletionBehaviorDeclaration | None:
    return next(
        (
            behavior
            for behavior in selected_plan.completion_behaviors
            if str(behavior.id) == completion_behavior_id
        ),
        None,
    )


def _remediation_policy_for(
    selected_plan: SelectedCompiledPlan,
    remediation_policy_id: RemediationPolicyId,
) -> RemediationPolicyDeclaration | None:
    return next(
        (
            policy
            for policy in selected_plan.remediation_policies
            if policy.id == remediation_policy_id
        ),
        None,
    )


def _closure_evidence_window_matches(
    behavior: CompletionBehaviorDeclaration,
    lineage_id: str,
    evidence_window: Mapping[str, AuthorityValue],
) -> bool:
    if behavior.evidence_window_policy != "lineage":
        return False
    return (
        evidence_window.get("kind") == "lineage"
        and evidence_window.get("lineage_id") == lineage_id
    )


def _closure_target_behavior_refusal(
    *,
    target: ClosureTargetRecord,
    behavior: CompletionBehaviorDeclaration,
    selected_plan_ref: PlanRef,
) -> str | None:
    if target.selected_plan_ref != selected_plan_ref:
        return "selected_plan_ref_mismatch"
    if target.completion_behavior_id != behavior.id:
        return "completion_behavior_mismatch"
    if target.request_kind != behavior.request_kind:
        return "closure_request_kind_mismatch"
    if target.target_graph_node_id != behavior.target_graph_node_id:
        return "closure_target_mismatch"
    if target.root_source_kind not in behavior.accepted_root_source_kinds:
        return "unsupported_closure_root_source"
    if not _closure_evidence_window_matches(
        behavior,
        target.lineage_id,
        target.evidence_window,
    ):
        return "invalid_closure_evidence_window"
    return None


def _active_closure_evaluation_for_target(
    state: RuntimeState,
    closure_target_id: str,
) -> ClosureEvaluationRecord | None:
    return next(
        (
            record
            for record in state.closure_evaluations.values()
            if record.closure_target_id == closure_target_id
            and record.target_work_item_id in state.work_items
            and record.target_work_item_id not in state.closed_work_items
            and record.target_activation_id in state.activations
        ),
        None,
    )


def _has_open_lineage_work(
    state: RuntimeState,
    *,
    lineage_id: str,
    plan_ref: PlanRef,
) -> bool:
    return any(
        work_item.lineage_id == lineage_id
        and work_item.ref.plan_ref == plan_ref
        and work_item.ref.work_item_id not in state.closed_work_items
        for work_item in state.work_items.values()
    )


def _completion_request_payload(
    *,
    target: ClosureTargetRecord,
    behavior: CompletionBehaviorDeclaration,
    stage: StageKindDeclaration,
) -> Mapping[str, AuthorityValue]:
    asset_ids = tuple(str(asset_id) for asset_id in stage.asset_ids)
    return {
        "request_kind": behavior.request_kind,
        "closure_target_id": target.closure_target_id,
        "root_source": {
            "kind": target.root_source_kind,
            "source_id": target.root_source_id,
        },
        "closure_root_work_item_id": target.closure_root_work_item_id,
        "plan_fingerprint": target.selected_plan_ref.authority_fingerprint,
        "graph_node_id": behavior.target_graph_node_id,
        "stage_kind_id": str(behavior.target_stage_kind_id),
        "runner_binding_id": str(behavior.runner_binding_id),
        "asset_ids": asset_ids,
        "evidence_window": target.evidence_window,
    }


def _completion_refused_decision(
    *,
    transition_input: EvaluateCompletionBehavior,
    context: TransitionContext,
    digest: str,
    reason: str,
    target: ClosureTargetRecord | None = None,
) -> TransitionDecision:
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=reason,
        event_plan_fingerprint=(
            transition_input.selected_plan_ref.authority_fingerprint
        ),
        event_work_item_id=target.closure_target_id if target is not None else None,
        event_authority_source="completion_behavior",
    )


def _decide_operator_resume_lineage(
    state: RuntimeState,
    transition_input: OperatorResumeLineage,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    resolved = _operator_lineage_context(
        state,
        transition_input=transition_input,
        option_kind="resume_lineage",
    )
    if isinstance(resolved, str):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=resolved,
        )
    option, quarantine, attempt, work_item = resolved
    target_activation = Activation(
        activation_id=context.activation_id,
        work_item_id=attempt.source_work_item_id,
        lineage_id=attempt.lineage_id,
        plan_ref=attempt.plan_ref,
        queue_family_id=attempt.source_queue_family_id,
        graph_node_id=attempt.source_graph_node_id,
        stage_kind_id=attempt.source_stage_kind_id,
        runner_binding_id=attempt.source_runner_binding_id,
        generation=work_item.ref.generation,
        created_by_input_id=transition_input.input_id,
    )
    resolved_attempt = replace(
        attempt,
        phase="resolved",
        updated_by_input_id=transition_input.input_id,
    )
    intervention = _operator_intervention_record(
        transition_input=transition_input,
        digest=digest,
        option=option,
        quarantine=quarantine,
        attempt=attempt,
        result="resumed",
        target_work_item_id=None,
        target_activation_id=target_activation.activation_id,
        closed_work_item_ids=(),
        closed_activation_ids=(),
        closed_run_ids=(),
        payload_digest=EMPTY_OPERATOR_PAYLOAD_DIGEST,
        payload_reference=None,
    )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            SupersedeLineageQuarantine(
                record_id=quarantine.quarantine_id,
                lineage_id=quarantine.lineage_id,
                quarantine_id=quarantine.quarantine_id,
                superseded_input_id=transition_input.input_id,
            ),
            RecordRecoveryAttempt(
                record_id=resolved_attempt.record_id,
                attempt=resolved_attempt,
            ),
            CreateActivation(target_activation),
            RecordOperatorIntervention(
                record_id=intervention.record_id,
                record=intervention,
            ),
        ),
        expected_plan_fingerprint=quarantine.selected_plan_fingerprint,
        expected_work_item_generations={
            work_item.ref.work_item_id: work_item.ref.generation
        },
        expected_work_item_plan_refs={
            work_item.ref.work_item_id: work_item.ref.plan_ref
        },
        expected_work_item_open=(work_item.ref.work_item_id,),
        event_plan_fingerprint=quarantine.selected_plan_fingerprint,
        event_work_item_id=work_item.ref.work_item_id,
        event_run_id=quarantine.emitting_recovery_run_id,
        event_action_id=quarantine.action_id,
        event_authority_source="operator_intervention",
    )


def _decide_operator_close_lineage(
    state: RuntimeState,
    transition_input: OperatorCloseLineage,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    resolved = _operator_lineage_context(
        state,
        transition_input=transition_input,
        option_kind="close_lineage",
    )
    if isinstance(resolved, str):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=resolved,
        )
    option, quarantine, attempt, _source_work_item = resolved
    closed_work_items = _lineage_closable_work_items(
        state,
        transition_input.lineage_id,
        plan_ref=quarantine.selected_plan_ref,
    )
    if not closed_work_items:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="no_live_lineage_work",
        )
    closed_work_item_ids = tuple(sorted(closed_work_items))
    closed_activation_ids = tuple(
        sorted(
            activation.activation_id
            for activation in state.activations.values()
            if activation.work_item_id in closed_work_item_ids
        )
    )
    closed_run_ids = tuple(
        sorted(
            run.run_ref.run_id
            for run in state.runs.values()
            if run.work_item_id in closed_work_item_ids
        )
    )
    resolved_attempt = replace(
        attempt,
        phase="resolved",
        updated_by_input_id=transition_input.input_id,
    )
    intervention = _operator_intervention_record(
        transition_input=transition_input,
        digest=digest,
        option=option,
        quarantine=quarantine,
        attempt=attempt,
        result="closed",
        target_work_item_id=None,
        target_activation_id=None,
        closed_work_item_ids=closed_work_item_ids,
        closed_activation_ids=closed_activation_ids,
        closed_run_ids=closed_run_ids,
        payload_digest=EMPTY_OPERATOR_PAYLOAD_DIGEST,
        payload_reference=None,
    )
    close_mutations = tuple(
        CloseWorkItem(
            record_id=f"{context.transition_id}:close:{work_item_id}",
            record=ClosedWorkItemRecord(
                record_id=f"{context.transition_id}:close:{work_item_id}",
                work_item_id=work_item_id,
                source_run_id=None,
                action_id=None,
                created_by_input_id=transition_input.input_id,
                operator_intervention_record_id=intervention.record_id,
                close_kind="operator_intervention",
            ),
        )
        for work_item_id in closed_work_item_ids
    )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            SupersedeLineageQuarantine(
                record_id=quarantine.quarantine_id,
                lineage_id=quarantine.lineage_id,
                quarantine_id=quarantine.quarantine_id,
                superseded_input_id=transition_input.input_id,
            ),
            RecordRecoveryAttempt(
                record_id=resolved_attempt.record_id,
                attempt=resolved_attempt,
            ),
            RecordOperatorIntervention(
                record_id=intervention.record_id,
                record=intervention,
            ),
            *close_mutations,
        ),
        expected_plan_fingerprint=quarantine.selected_plan_fingerprint,
        expected_work_item_generations={
            work_item_id: state.work_items[work_item_id].ref.generation
            for work_item_id in closed_work_item_ids
        },
        expected_work_item_plan_refs={
            work_item_id: state.work_items[work_item_id].ref.plan_ref
            for work_item_id in closed_work_item_ids
        },
        expected_work_item_open=closed_work_item_ids,
        event_plan_fingerprint=quarantine.selected_plan_fingerprint,
        event_work_item_id=quarantine.original_source_work_item_id,
        event_run_id=quarantine.emitting_recovery_run_id,
        event_action_id=quarantine.action_id,
        event_authority_source="operator_intervention",
    )


def _decide_operator_revise_lineage(
    state: RuntimeState,
    transition_input: OperatorReviseLineage,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    resolved = _operator_lineage_context(
        state,
        transition_input=transition_input,
        option_kind="revise_lineage",
    )
    if isinstance(resolved, str):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=resolved,
        )
    option, quarantine, attempt, source_work_item = resolved
    if (
        option.payload_schema_id is None
        or option.target_queue_family_id is None
        or option.target_stage_kind_id is None
        or option.target_graph_node_id is None
        or option.target_runner_binding_id is None
    ):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_intervention_option",
        )
    artifact_schema = artifact_schema_for(
        state.admitted_plans[
            quarantine.selected_plan_ref.authority_fingerprint
        ].selected_plan,
        str(option.payload_schema_id),
    )
    if artifact_schema is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_intervention_option",
        )
    validation = validate_schema(artifact_schema.schema, transition_input.payload)
    if not validation.accepted:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_operator_intervention_payload_schema",
            event_plan_fingerprint=quarantine.selected_plan_fingerprint,
            event_work_item_id=quarantine.original_source_work_item_id,
            event_run_id=quarantine.emitting_recovery_run_id,
            event_action_id=quarantine.action_id,
            event_authority_source="operator_intervention",
        )
    target_work_item_ref = WorkItemRef(
        work_item_id=context.work_item_id,
        plan_ref=quarantine.selected_plan_ref,
        generation=0,
    )
    target_work_item = WorkItem(
        ref=target_work_item_ref,
        queue_family_id=option.target_queue_family_id,
        payload=transition_input.payload,
        lineage_id=quarantine.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    target_activation = Activation(
        activation_id=context.activation_id,
        work_item_id=target_work_item_ref.work_item_id,
        lineage_id=quarantine.lineage_id,
        plan_ref=quarantine.selected_plan_ref,
        queue_family_id=option.target_queue_family_id,
        graph_node_id=option.target_graph_node_id,
        stage_kind_id=option.target_stage_kind_id,
        runner_binding_id=option.target_runner_binding_id,
        generation=0,
        created_by_input_id=transition_input.input_id,
    )
    resolved_attempt = replace(
        attempt,
        phase="resolved",
        updated_by_input_id=transition_input.input_id,
    )
    intervention = _operator_intervention_record(
        transition_input=transition_input,
        digest=digest,
        option=option,
        quarantine=quarantine,
        attempt=attempt,
        result="revised",
        target_work_item_id=target_work_item.ref.work_item_id,
        target_activation_id=target_activation.activation_id,
        closed_work_item_ids=(),
        closed_activation_ids=(),
        closed_run_ids=(),
        payload_digest=operator_payload_digest(transition_input.payload),
        payload_reference=(f"work_item:{target_work_item.ref.work_item_id}:payload"),
    )
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            SupersedeLineageQuarantine(
                record_id=quarantine.quarantine_id,
                lineage_id=quarantine.lineage_id,
                quarantine_id=quarantine.quarantine_id,
                superseded_input_id=transition_input.input_id,
            ),
            RecordRecoveryAttempt(
                record_id=resolved_attempt.record_id,
                attempt=resolved_attempt,
            ),
            CreateWorkItem(target_work_item),
            CreateActivation(target_activation),
            RecordOperatorIntervention(
                record_id=intervention.record_id,
                record=intervention,
            ),
        ),
        expected_plan_fingerprint=quarantine.selected_plan_fingerprint,
        expected_work_item_generations={
            source_work_item.ref.work_item_id: source_work_item.ref.generation
        },
        expected_work_item_plan_refs={
            source_work_item.ref.work_item_id: source_work_item.ref.plan_ref
        },
        expected_work_item_open=(source_work_item.ref.work_item_id,),
        event_plan_fingerprint=quarantine.selected_plan_fingerprint,
        event_work_item_id=target_work_item.ref.work_item_id,
        event_run_id=quarantine.emitting_recovery_run_id,
        event_action_id=quarantine.action_id,
        event_authority_source="operator_intervention",
    )


def _operator_lineage_context(
    state: RuntimeState,
    *,
    transition_input: (
        OperatorResumeLineage | OperatorCloseLineage | OperatorReviseLineage
    ),
    option_kind: str,
) -> (
    tuple[
        InterventionOptionDeclaration,
        LineageQuarantineRecord,
        RecoveryAttemptRecord,
        WorkItem,
    ]
    | str
):
    if transition_input.actor_kind != "local_operator":
        return "invalid_actor_kind"
    if option_kind != "revise_lineage" and transition_input.payload:
        return "payload_forbidden"
    admitted = state.admitted_plans.get(
        transition_input.selected_plan_ref.authority_fingerprint
    )
    if admitted is None or admitted.plan_ref != transition_input.selected_plan_ref:
        return "unknown_plan_ref"
    option = intervention_option_for(admitted.selected_plan, transition_input.option_id)
    if option is None:
        return "unknown_intervention_option"
    if option.option_kind != option_kind:
        return "intervention_option_kind_mismatch"
    if option.actor_kind != "local_operator":
        return "invalid_actor_kind"
    quarantine = active_lineage_quarantine_for(
        state,
        transition_input.lineage_id,
        plan_ref=transition_input.selected_plan_ref,
        policy_id=str(option.policy_id),
    )
    if quarantine is None or quarantine.quarantine_id != transition_input.quarantine_id:
        known_quarantine = state.lineage_quarantines.get(transition_input.quarantine_id)
        if (
            known_quarantine is not None
            and known_quarantine.selected_plan_ref == transition_input.selected_plan_ref
            and known_quarantine.policy_id == option.policy_id
            and known_quarantine.lineage_id == transition_input.lineage_id
            and known_quarantine.status != "active"
        ):
            return "lineage_quarantine_not_active"
        return "lineage_not_quarantined"
    if quarantine.selected_plan_ref != transition_input.selected_plan_ref:
        return "selected_plan_ref_mismatch"
    if quarantine.policy_id != option.policy_id:
        return "intervention_policy_mismatch"
    attempt = state.recovery_attempts.get(quarantine.recovery_attempt_record_id)
    if (
        attempt is None
        or attempt.plan_ref != quarantine.selected_plan_ref
        or attempt.policy_id != quarantine.policy_id
        or attempt.lineage_id != quarantine.lineage_id
        or attempt.attempt_count != quarantine.attempt_count
        or attempt.phase != "quarantine_eligible"
    ):
        return "invalid_recovery_attempt"
    work_item = state.work_items.get(attempt.source_work_item_id)
    if work_item is None or work_item.lineage_id != attempt.lineage_id:
        return "missing_work_item"
    if work_item.ref.work_item_id in state.closed_work_items:
        return "work_item_closed"
    return option, quarantine, attempt, work_item


def _lineage_closable_work_items(
    state: RuntimeState,
    lineage_id: str,
    *,
    plan_ref: PlanRef,
) -> Mapping[str, WorkItem]:
    closable_work_item_ids: set[str] = set()
    for work_item in state.work_items.values():
        if (
            work_item.lineage_id == lineage_id
            and work_item.ref.plan_ref == plan_ref
            and work_item.ref.work_item_id not in state.closed_work_items
        ):
            closable_work_item_ids.add(work_item.ref.work_item_id)
    return {
        work_item_id: state.work_items[work_item_id]
        for work_item_id in closable_work_item_ids
    }


def _operator_intervention_record(
    *,
    transition_input: (
        OperatorResumeLineage | OperatorCloseLineage | OperatorReviseLineage
    ),
    digest: str,
    option: InterventionOptionDeclaration,
    quarantine: LineageQuarantineRecord,
    attempt: RecoveryAttemptRecord,
    result: str,
    target_work_item_id: str | None,
    target_activation_id: str | None,
    closed_work_item_ids: tuple[str, ...],
    closed_activation_ids: tuple[str, ...],
    closed_run_ids: tuple[str, ...],
    payload_digest: str,
    payload_reference: str | None,
) -> OperatorInterventionRecord:
    return OperatorInterventionRecord(
        record_id=f"operator-intervention:{transition_input.input_id}",
        created_by_input_id=transition_input.input_id,
        input_payload_digest=digest,
        option_id=transition_input.option_id,
        kind=option.option_kind,
        result=result,
        policy_id=option.policy_id,
        lineage_id=transition_input.lineage_id,
        quarantine_id=quarantine.quarantine_id,
        recovery_attempt_record_id=attempt.record_id,
        recovery_attempt_count=attempt.attempt_count,
        attempt_effect=option.attempt_effect,
        selected_plan_ref=transition_input.selected_plan_ref,
        selected_plan_fingerprint=(
            transition_input.selected_plan_ref.authority_fingerprint
        ),
        actor_kind=transition_input.actor_kind,
        actor_id=transition_input.actor_id,
        reason=transition_input.reason,
        target_work_item_id=target_work_item_id,
        target_activation_id=target_activation_id,
        closed_work_item_ids=closed_work_item_ids,
        closed_activation_ids=closed_activation_ids,
        closed_run_ids=closed_run_ids,
        payload_digest=payload_digest,
        payload_reference=payload_reference,
    )


def _run_for_claim(
    *,
    transition_input: ClaimWork,
    context: TransitionContext,
    activation: Activation,
    work_item: WorkItem,
) -> RunRecord:
    run_ref = RunRef(
        run_id=context.run_id,
        work_item_id=work_item.ref.work_item_id,
        claim_id=context.claim_id,
        plan_ref=activation.plan_ref,
        generation=0,
        fencing_token=context.fencing_token,
    )
    return RunRecord(
        run_ref=run_ref,
        work_item_id=work_item.ref.work_item_id,
        activation_id=activation.activation_id,
        stage_kind_id=activation.stage_kind_id,
        runner_binding_id=activation.runner_binding_id,
        created_by_input_id=transition_input.input_id,
    )


def _decide_runner_result(
    state: RuntimeState,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    digest: str,
) -> TransitionDecision:
    try:
        evidence = runner_result_evidence_from_payload(transition_input.payload)
    except (TypeError, ValueError):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_runner_evidence",
            event_run_id=transition_input.run_id,
        )

    run = state.runs.get(transition_input.run_id)
    if run is None:
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unknown_run",
        )
    if evidence.run_id != transition_input.run_id:
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_observation_authority",
            run=run,
            work_item=state.work_items.get(run.work_item_id),
        )

    activation = state.activations.get(run.activation_id)
    work_item = state.work_items.get(run.work_item_id)
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if activation is None or work_item is None or admitted is None:
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="missing_run_state",
            run=run,
            work_item=work_item,
        )

    if not _runner_observation_matches_run(
        evidence=evidence,
        run=run,
        activation=activation,
        work_item=work_item,
    ):
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_observation_authority",
            run=run,
            work_item=work_item,
        )

    if (
        active_lineage_quarantine_for(
            state,
            work_item.lineage_id,
            plan_ref=work_item.ref.plan_ref,
        )
        is not None
    ):
        action = _terminal_action_for_marker(
            admitted.selected_plan,
            run,
            evidence.marker,
        )
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="lineage_quarantined",
            run=run,
            work_item=work_item,
            action=action,
        )

    if (
        active_operator_wait_for(
            state,
            work_item.lineage_id,
            plan_ref=work_item.ref.plan_ref,
        )
        is not None
    ):
        action = _terminal_action_for_marker(
            admitted.selected_plan,
            run,
            evidence.marker,
        )
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="operator_wait_active",
            run=run,
            work_item=work_item,
            action=action,
        )

    if run_has_observation(state, run.run_ref.run_id):
        action = _terminal_action_for_marker(
            admitted.selected_plan,
            run,
            evidence.marker,
        )
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="duplicate_runner_observation",
            run=run,
            work_item=work_item,
            action=action,
        )

    if work_item.ref.work_item_id in state.closed_work_items:
        action = _terminal_action_for_marker(
            admitted.selected_plan,
            run,
            evidence.marker,
        )
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="work_item_closed",
            run=run,
            work_item=work_item,
            action=action,
        )

    marker = evidence.marker
    outcome = terminal_outcome_for(
        admitted.selected_plan,
        str(run.stage_kind_id),
        marker,
    )
    if outcome is None:
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="undeclared_terminal_outcome",
            run=run,
            work_item=work_item,
        )

    action = terminal_action_for(
        admitted.selected_plan,
        str(run.stage_kind_id),
        str(outcome.id),
    )
    if action is None:
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="missing_terminal_action",
            run=run,
            work_item=work_item,
        )

    result = resolve_terminal_action(
        transition_input=transition_input,
        context=context,
        selected_plan=admitted.selected_plan,
        state=state,
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        observation_payload=evidence.observation_payload,
    )
    if isinstance(result, TerminalActionRefusal):
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=result.reason,
            run=run,
            work_item=work_item,
            action=result.action,
        )
    closure_aftermath = _closure_terminal_aftermath(
        state=state,
        transition_input=transition_input,
        context=context,
        digest=digest,
        selected_plan=admitted.selected_plan,
        result=result,
        run=run,
        work_item=work_item,
        action=action,
    )
    if isinstance(closure_aftermath, TransitionDecision):
        return closure_aftermath
    if closure_aftermath:
        result = replace(result, mutations=(*result.mutations, *closure_aftermath))
    fanout_aftermath = _accepted_terminal_fanout_aftermath(
        state=state,
        transition_input=transition_input,
        context=context,
        digest=digest,
        selected_plan=admitted.selected_plan,
        result=result,
        run=run,
        work_item=work_item,
        action=action,
    )
    if isinstance(fanout_aftermath, TransitionDecision):
        return fanout_aftermath
    if fanout_aftermath:
        result = replace(result, mutations=(*result.mutations, *fanout_aftermath))
    return _accepted_terminal_action_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        result=result,
    )


def _runner_refused_decision(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    digest: str,
    reason: str,
    run: RunRecord,
    work_item: WorkItem | None,
    action: TerminalActionDeclaration | None = None,
) -> TransitionDecision:
    # Runner refusals still carry the known run context so audit records remain
    # tied to the frozen plan/run authority that produced the observation.
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=reason,
        event_plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        event_work_item_id=(
            work_item.ref.work_item_id if work_item is not None else run.work_item_id
        ),
        event_run_id=run.run_ref.run_id,
        event_action_id=action.id if action is not None else None,
        event_authority_source=(
            AUTHORITY_SOURCE_TERMINAL_ACTION if action is not None else None
        ),
    )


def _closure_terminal_aftermath(
    *,
    state: RuntimeState,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    digest: str,
    selected_plan: SelectedCompiledPlan,
    result: TerminalActionResolution,
    run: RunRecord,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
) -> tuple[TransitionMutation, ...] | TransitionDecision:
    evaluation_record = _closure_evaluation_for_run(state, run)
    if evaluation_record is None:
        return ()
    target = state.closure_targets.get(evaluation_record.closure_target_id)
    behavior = _completion_behavior_for(
        selected_plan,
        str(evaluation_record.completion_behavior_id),
    )
    if target is None or behavior is None:
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_closure_target",
            run=run,
            work_item=work_item,
            action=action,
        )
    target_refusal = _closure_target_behavior_refusal(
        target=target,
        behavior=behavior,
        selected_plan_ref=run.run_ref.plan_ref,
    )
    if target_refusal is not None or target.status != "open":
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=target_refusal or "closure_target_closed",
            run=run,
            work_item=work_item,
            action=action,
        )
    if action.id == behavior.pass_action_id:
        return _closure_pass_mutations(
            transition_input=transition_input,
            context=context,
            result=result,
            target=target,
            behavior=behavior,
            run=run,
            action=action,
        )
    if action.id == behavior.gap_action_id:
        return _closure_gap_mutations_or_refusal(
            state=state,
            transition_input=transition_input,
            context=context,
            digest=digest,
            selected_plan=selected_plan,
            result=result,
            target=target,
            behavior=behavior,
            run=run,
            work_item=work_item,
            action=action,
        )
    if action.id == behavior.blocked_action_id:
        blocked = ClosureBlockedRecord(
            record_id=f"closure-blocked:{context.transition_id}",
            closure_target_id=target.closure_target_id,
            completion_behavior_id=behavior.id,
            source_run_id=run.run_ref.run_id,
            source_action_id=action.id,
            selected_plan_ref=run.run_ref.plan_ref,
            lineage_id=target.lineage_id,
            operator_required=True,
            created_by_input_id=transition_input.input_id,
        )
        return (RecordClosureBlocked(record_id=blocked.record_id, record=blocked),)
    return _runner_refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason="closure_action_mismatch",
        run=run,
        work_item=work_item,
        action=action,
    )


def _accepted_terminal_fanout_aftermath(
    *,
    state: RuntimeState,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    digest: str,
    selected_plan: SelectedCompiledPlan,
    result: TerminalActionResolution,
    run: RunRecord,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
) -> tuple[TransitionMutation, ...] | TransitionDecision:
    source_artifact = _terminal_result_artifact(result)
    if source_artifact is None:
        return ()
    fanouts = tuple(
        fanout
        for fanout in selected_plan.fanout_declarations
        if fanout.source_action_id == action.id
        and fanout.source_state_policy == "accepted_terminal_observation"
    )
    if not fanouts:
        return ()

    mutations: list[TransitionMutation] = []
    for fanout in fanouts:
        fanout_input = FanoutFromArtifact(
            transition_input.input_id,
            fanout_id=str(fanout.id),
            source_artifact_id=source_artifact.artifact_id,
        )
        fanout_mutations = _accepted_terminal_fanout_mutations(
            state=state,
            transition_input=transition_input,
            fanout_input=fanout_input,
            context=context,
            digest=digest,
            selected_plan=selected_plan,
            source_artifact=source_artifact,
            run=run,
            work_item=work_item,
            action=action,
            fanout=fanout,
        )
        if isinstance(fanout_mutations, TransitionDecision):
            return fanout_mutations
        mutations.extend(fanout_mutations)
    return tuple(mutations)


def _accepted_terminal_fanout_mutations(
    *,
    state: RuntimeState,
    transition_input: RunnerResultObserved,
    fanout_input: FanoutFromArtifact,
    context: TransitionContext,
    digest: str,
    selected_plan: SelectedCompiledPlan,
    source_artifact: ArtifactRecord,
    run: RunRecord,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    fanout: FanoutDeclaration,
) -> tuple[TransitionMutation, ...] | TransitionDecision:
    source_activation = state.activations.get(run.activation_id)
    if source_activation is None:
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="fanout_partial_state",
            run=run,
            work_item=work_item,
            action=action,
        )
    items = _fanout_items_or_refusal(
        fanout_input,
        context,
        digest,
        artifact=source_artifact,
        source_run=run,
        source_work_item=work_item,
        fanout=fanout,
    )
    if isinstance(items, TransitionDecision):
        return _fanout_runner_refusal(
            fanout_decision=items,
            transition_input=transition_input,
            context=context,
            digest=digest,
            run=run,
            work_item=work_item,
            action=action,
        )
    if not items:
        return ()
    assessment_refusal = _fanout_assessment_refusal(
        state,
        fanout_input,
        context,
        digest,
        source_context=SourceContext(
            artifact=source_artifact,
            run=run,
            work_item=work_item,
            activation=source_activation,
            selected_plan=selected_plan,
        ),
        fanout=fanout,
    )
    if assessment_refusal is not None:
        return _fanout_runner_refusal(
            fanout_decision=assessment_refusal,
            transition_input=transition_input,
            context=context,
            digest=digest,
            run=run,
            work_item=work_item,
            action=action,
        )

    target_schema = _fanout_target_schema_or_refusal(
        selected_plan,
        fanout_input,
        context,
        digest,
        source_run=run,
        source_work_item=work_item,
        fanout=fanout,
    )
    if isinstance(target_schema, TransitionDecision):
        return _fanout_runner_refusal(
            fanout_decision=target_schema,
            transition_input=transition_input,
            context=context,
            digest=digest,
            run=run,
            work_item=work_item,
            action=action,
        )

    mutations: list[TransitionMutation] = []
    for item_key, raw_item in items:
        payload = _fanout_target_payload_or_refusal(
            fanout_input,
            context,
            digest,
            source_artifact_payload=source_artifact.payload,
            source_run=run,
            source_work_item=work_item,
            fanout=fanout,
            target_schema=target_schema,
            raw_item=raw_item,
        )
        if isinstance(payload, TransitionDecision):
            return _fanout_runner_refusal(
                fanout_decision=payload,
                transition_input=transition_input,
                context=context,
                digest=digest,
                run=run,
                work_item=work_item,
                action=action,
            )
        item_mutations = _fanout_mutations_for_item(
            state,
            fanout_input,
            context,
            digest,
            artifact=source_artifact,
            source_run=run,
            source_work_item=work_item,
            fanout=fanout,
            item_key=item_key,
            payload=payload,
        )
        if isinstance(item_mutations, TransitionDecision):
            return _fanout_runner_refusal(
                fanout_decision=item_mutations,
                transition_input=transition_input,
                context=context,
                digest=digest,
                run=run,
                work_item=work_item,
                action=action,
            )
        mutations.extend(item_mutations)
    return tuple(mutations)


def _fanout_runner_refusal(
    *,
    fanout_decision: TransitionDecision,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    digest: str,
    run: RunRecord,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
) -> TransitionDecision:
    refusal = fanout_decision.refusal
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=refusal.reason if refusal is not None else "fanout_refused",
        detail=refusal.detail if refusal is not None else None,
        event_plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        event_work_item_id=work_item.ref.work_item_id,
        event_run_id=run.run_ref.run_id,
        event_action_id=action.id,
        event_authority_source=AUTHORITY_SOURCE_TERMINAL_ACTION,
    )


def _closure_pass_mutations(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    result: TerminalActionResolution,
    target: ClosureTargetRecord,
    behavior: CompletionBehaviorDeclaration,
    run: RunRecord,
    action: TerminalActionDeclaration,
) -> tuple[TransitionMutation, ...]:
    terminal = ClosureTerminalRecord(
        record_id=f"closure-terminal:{context.transition_id}",
        closure_target_id=target.closure_target_id,
        completion_behavior_id=behavior.id,
        terminal_kind="passed",
        source_run_id=run.run_ref.run_id,
        source_action_id=action.id,
        source_artifact_id=_terminal_result_artifact_id(result),
        selected_plan_ref=run.run_ref.plan_ref,
        lineage_id=target.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    return (
        RecordClosureTerminal(record_id=terminal.record_id, record=terminal),
        CloseClosureTarget(
            record_id=target.closure_target_id,
            closure_target_id=target.closure_target_id,
            closed_by_record_id=terminal.record_id,
        ),
    )


def _closure_gap_mutations_or_refusal(
    *,
    state: RuntimeState,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    digest: str,
    selected_plan: SelectedCompiledPlan,
    result: TerminalActionResolution,
    target: ClosureTargetRecord,
    behavior: CompletionBehaviorDeclaration,
    run: RunRecord,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
) -> tuple[TransitionMutation, ...] | TransitionDecision:
    policy = _remediation_policy_for(selected_plan, behavior.remediation_policy_id)
    if policy is None or policy.source_action_id != action.id:
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_remediation_policy",
            run=run,
            work_item=work_item,
            action=action,
        )
    source_artifact = _terminal_result_artifact(result)
    if source_artifact is None and policy.guidance_source == "source_artifact":
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="missing_remediation_source_artifact",
            run=run,
            work_item=work_item,
            action=action,
        )
    source_artifact_id = (
        source_artifact.artifact_id if source_artifact is not None else None
    )
    dedupe_key = f"{target.closure_target_id}:{source_artifact_id}"
    if any(
        remediation_record.dedupe_key == dedupe_key
        and remediation_record.selected_plan_ref == run.run_ref.plan_ref
        for remediation_record in state.remediation_work_records.values()
    ):
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="duplicate_remediation_work",
            run=run,
            work_item=work_item,
            action=action,
        )
    remediation_record = RemediationWorkRecord(
        record_id=f"remediation-record:{context.transition_id}",
        remediation_policy_id=policy.id,
        closure_target_id=target.closure_target_id,
        source_run_id=run.run_ref.run_id,
        source_action_id=action.id,
        source_artifact_id=source_artifact_id,
        target_work_item_id=context.work_item_id,
        target_activation_id=context.activation_id,
        selected_plan_ref=run.run_ref.plan_ref,
        lineage_id=target.lineage_id,
        dedupe_key=dedupe_key,
        created_by_input_id=transition_input.input_id,
    )
    payload = _remediation_payload(policy, remediation_record, source_artifact)
    schema = artifact_schema_for(selected_plan, str(policy.payload_schema_id))
    if schema is None or not validate_schema(schema.schema, payload).accepted:
        return _runner_refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_remediation_payload",
            run=run,
            work_item=work_item,
            action=action,
        )
    target_work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id=context.work_item_id,
            plan_ref=run.run_ref.plan_ref,
            generation=0,
        ),
        queue_family_id=policy.target_queue_family_id,
        payload=payload,
        lineage_id=target.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    target_activation = Activation(
        activation_id=context.activation_id,
        work_item_id=context.work_item_id,
        lineage_id=target.lineage_id,
        plan_ref=run.run_ref.plan_ref,
        queue_family_id=policy.target_queue_family_id,
        graph_node_id=policy.target_graph_node_id,
        stage_kind_id=policy.target_stage_kind_id,
        runner_binding_id=policy.target_runner_binding_id,
        generation=0,
        created_by_input_id=transition_input.input_id,
    )
    route = ActivationRouteRecord(
        record_id=f"{context.transition_id}:remediation-route",
        action_id=action.id,
        source_run_id=run.run_ref.run_id,
        source_work_item_id=run.work_item_id,
        target_work_item_id=target_work_item.ref.work_item_id,
        target_activation_id=target_activation.activation_id,
        created_by_input_id=transition_input.input_id,
    )
    return (
        CreateWorkItem(target_work_item),
        CreateActivation(target_activation),
        RouteActivation(record_id=route.record_id, route=route),
        RecordRemediationWork(
            record_id=remediation_record.record_id,
            record=remediation_record,
        ),
    )


def _closure_evaluation_for_run(
    state: RuntimeState,
    run: RunRecord,
) -> ClosureEvaluationRecord | None:
    return next(
        (
            record
            for record in state.closure_evaluations.values()
            if record.target_activation_id == run.activation_id
            and record.target_work_item_id == run.work_item_id
            and record.selected_plan_ref == run.run_ref.plan_ref
        ),
        None,
    )


def _terminal_result_artifact_id(result: TerminalActionResolution) -> str | None:
    artifact = _terminal_result_artifact(result)
    return artifact.artifact_id if artifact is not None else None


def _terminal_result_artifact(
    result: TerminalActionResolution,
) -> ArtifactRecord | None:
    return next(
        (
            mutation.artifact
            for mutation in result.mutations
            if isinstance(mutation, RecordArtifact) and mutation.artifact is not None
        ),
        None,
    )


def _remediation_payload(
    policy: RemediationPolicyDeclaration,
    remediation_record: RemediationWorkRecord,
    source_artifact: ArtifactRecord | None,
) -> Mapping[str, AuthorityValue]:
    body = _source_artifact_guidance(source_artifact)
    return {
        "title": "Closure remediation needed",
        "body": body,
        "root_source": {
            "kind": policy.root_source_kind,
            "source_id": remediation_record.record_id,
        },
    }


def _source_artifact_guidance(source_artifact: ArtifactRecord | None) -> str:
    if source_artifact is None:
        return "Closure review identified a gap that requires remediation."
    summary = source_artifact.payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary
    return "Closure review identified a gap that requires remediation."


def _terminal_action_for_marker(
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    marker: str,
) -> TerminalActionDeclaration | None:
    outcome = terminal_outcome_for(
        selected_plan,
        str(run.stage_kind_id),
        marker,
    )
    if outcome is None:
        return None
    return terminal_action_for(
        selected_plan,
        str(run.stage_kind_id),
        str(outcome.id),
    )


def _accepted_terminal_action_decision(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    digest: str,
    result: TerminalActionResolution,
) -> TransitionDecision:
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=result.mutations,
        expected_plan_fingerprint=result.expected_plan_fingerprint,
        expected_work_item_generations=result.expected_work_item_generations,
        expected_activation_generations=result.expected_activation_generations,
        expected_run_generations=result.expected_run_generations,
        expected_run_fencing_tokens=result.expected_run_fencing_tokens,
        expected_run_unobserved=result.expected_run_unobserved,
        expected_lineage_quarantine_absent=result.expected_lineage_quarantine_absent,
        expected_operator_wait_absent=result.expected_operator_wait_absent,
        expected_work_item_open=result.expected_work_item_open,
        expected_work_item_plan_refs=result.expected_work_item_plan_refs,
        expected_activation_plan_refs=result.expected_activation_plan_refs,
        event_plan_fingerprint=result.event_plan_fingerprint,
        event_work_item_id=result.event_work_item_id,
        event_run_id=result.event_run_id,
        event_action_id=result.event_action_id,
        event_authority_source=result.event_authority_source,
    )


def _runner_session_accepted_decision(
    transition_input: TransitionInput,
    context: TransitionContext,
    digest: str,
    run_ref: RunRef,
    mutations: tuple[TransitionMutation, ...],
) -> TransitionDecision:
    return _accepted_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=mutations,
        expected_plan_fingerprint=run_ref.plan_ref.authority_fingerprint,
        expected_run_generations={run_ref.run_id: run_ref.generation},
        expected_run_fencing_tokens={run_ref.run_id: run_ref.fencing_token},
        event_plan_fingerprint=run_ref.plan_ref.authority_fingerprint,
        event_run_id=run_ref.run_id,
        event_authority_source="run",
    )


def _runner_session_refused_decision(
    transition_input: TransitionInput,
    context: TransitionContext,
    digest: str,
    reason: str,
) -> TransitionDecision:
    run_ref = getattr(transition_input, "run_ref", None)
    if not isinstance(run_ref, RunRef):
        return _refused_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=reason,
        )
    return _refused_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=reason,
        event_plan_fingerprint=run_ref.plan_ref.authority_fingerprint,
        event_run_id=run_ref.run_id,
    )


def _accepted_decision(
    *,
    transition_input: TransitionInput,
    context: TransitionContext,
    digest: str,
    mutations: tuple[TransitionMutation, ...],
    expected_plan_fingerprint: AuthorityFingerprint | None = None,
    expected_work_item_generations: Mapping[str, int] | None = None,
    expected_activation_generations: Mapping[str, int] | None = None,
    expected_activation_unclaimed: tuple[str, ...] = (),
    expected_run_generations: Mapping[str, int] | None = None,
    expected_run_fencing_tokens: Mapping[str, str] | None = None,
    expected_run_unobserved: tuple[str, ...] = (),
    expected_pause_absent: bool = False,
    expected_lineage_quarantine_absent: tuple[str, ...] = (),
    expected_operator_wait_absent: tuple[str, ...] = (),
    expected_work_item_open: tuple[str, ...] = (),
    expected_work_item_plan_refs: Mapping[str, PlanRef] | None = None,
    expected_activation_plan_refs: Mapping[str, PlanRef] | None = None,
    event_plan_fingerprint: AuthorityFingerprint | None = None,
    event_work_item_id: str | None = None,
    event_run_id: str | None = None,
    event_action_id: ActionId | None = None,
    event_authority_source: str | None = None,
    detail: str | None = None,
) -> TransitionDecision:
    receipt_ref = InputReceiptRef(
        transition_input.input_id,
        digest,
    )
    record = transition_record(
        transition_input=transition_input,
        context=context,
        accepted=True,
    )
    plan_fingerprint = event_plan_fingerprint or expected_plan_fingerprint
    event, trace = event_and_trace_records(
        transition_input=transition_input,
        context=context,
        disposition="accepted",
        plan_fingerprint=plan_fingerprint,
        work_item_id=event_work_item_id,
        run_id=event_run_id,
        action_id=event_action_id,
        authority_source=event_authority_source,
        refusal_reason=None,
    )
    return TransitionDecision(
        input_id=transition_input.input_id,
        input_kind=input_kind(transition_input),
        input_family=input_family(transition_input),
        input_payload_digest=digest,
        accepted=True,
        receipt_ref=receipt_ref,
        refusal=None,
        expected_plan_fingerprint=expected_plan_fingerprint,
        expected_work_item_generations=expected_work_item_generations or {},
        expected_activation_generations=expected_activation_generations or {},
        expected_activation_unclaimed=expected_activation_unclaimed,
        expected_run_generations=expected_run_generations or {},
        expected_run_fencing_tokens=expected_run_fencing_tokens or {},
        expected_run_unobserved=expected_run_unobserved,
        expected_pause_absent=expected_pause_absent,
        expected_lineage_quarantine_absent=expected_lineage_quarantine_absent,
        expected_operator_wait_absent=expected_operator_wait_absent,
        expected_work_item_open=expected_work_item_open,
        mutations=(
            RecordInputReceipt(
                InputReceipt(
                    receipt_ref=receipt_ref,
                    transition_id=context.transition_id,
                    accepted=True,
                )
            ),
            *mutations,
            RecordTransition(record),
            EmitGovernanceEvent(record_id=event.record_id, event=event),
            EmitTrace(record_id=trace.record_id, trace=trace),
        ),
        governance_events=(event,),
        trace_records=(trace,),
        expected_work_item_plan_refs=expected_work_item_plan_refs or {},
        expected_activation_plan_refs=expected_activation_plan_refs or {},
    )


def _refused_decision(
    *,
    transition_input: TransitionInput,
    context: TransitionContext,
    digest: str,
    reason: str,
    record_receipt: bool = True,
    event_plan_fingerprint: AuthorityFingerprint | None = None,
    event_work_item_id: str | None = None,
    event_run_id: str | None = None,
    event_action_id: ActionId | None = None,
    event_authority_source: str | None = None,
    detail: str | None = None,
) -> TransitionDecision:
    receipt_ref = InputReceiptRef(
        transition_input.input_id,
        digest,
    )
    refusal = TransitionRefusal(
        record_id=f"{context.transition_id}:refusal",
        input_id=transition_input.input_id,
        input_kind=input_kind(transition_input),
        input_family=input_family(transition_input),
        reason=reason,
        detail=detail,
    )
    event, trace = event_and_trace_records(
        transition_input=transition_input,
        context=context,
        disposition="refused",
        plan_fingerprint=event_plan_fingerprint,
        work_item_id=event_work_item_id,
        run_id=event_run_id,
        action_id=event_action_id,
        authority_source=event_authority_source,
        refusal_reason=reason,
    )
    return TransitionDecision(
        input_id=transition_input.input_id,
        input_kind=input_kind(transition_input),
        input_family=input_family(transition_input),
        input_payload_digest=digest,
        accepted=False,
        receipt_ref=receipt_ref if record_receipt else None,
        refusal=refusal,
        expected_plan_fingerprint=None,
        expected_work_item_generations={},
        expected_activation_generations={},
        expected_activation_unclaimed=(),
        expected_run_generations={},
        expected_run_fencing_tokens={},
        expected_run_unobserved=(),
        expected_pause_absent=False,
        expected_lineage_quarantine_absent=(),
        expected_work_item_open=(),
        mutations=(
            *(
                (
                    RecordInputReceipt(
                        InputReceipt(
                            receipt_ref=receipt_ref,
                            transition_id=context.transition_id,
                            accepted=False,
                            refusal_reason=reason,
                        )
                    ),
                )
                if record_receipt
                else ()
            ),
            RecordRefusal(refusal),
            RecordTransition(
                transition_record(
                    transition_input=transition_input,
                    context=context,
                    accepted=False,
                )
            ),
            EmitGovernanceEvent(record_id=event.record_id, event=event),
            EmitTrace(record_id=trace.record_id, trace=trace),
        ),
        governance_events=(event,),
        trace_records=(trace,),
    )


def _runner_observation_matches_run(
    *,
    evidence: RunnerResultEvidence,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> bool:
    return (
        evidence.plan_fingerprint == run.run_ref.plan_ref.authority_fingerprint
        and evidence.claim_id == run.run_ref.claim_id
        and evidence.generation == run.run_ref.generation
        and evidence.fencing_token == run.run_ref.fencing_token
        and evidence.stage_kind_id == str(run.stage_kind_id)
        and evidence.graph_node_id == activation.graph_node_id
        and evidence.runner_binding_id == str(run.runner_binding_id)
        and run.run_ref.work_item_id == run.work_item_id
        and activation.activation_id == run.activation_id
        and activation.claimed_by_run_id == run.run_ref.run_id
        and activation.work_item_id == run.work_item_id
        and activation.stage_kind_id == run.stage_kind_id
        and activation.runner_binding_id == run.runner_binding_id
        and activation.plan_ref == run.run_ref.plan_ref
        and activation.generation == run.run_ref.generation + 1
        and work_item.ref.work_item_id == run.work_item_id
        and work_item.ref.plan_ref == run.run_ref.plan_ref
        and work_item.ref.generation == run.run_ref.generation
    )


__all__ = ("decide",)
