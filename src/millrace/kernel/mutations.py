"""Mutation application and stale-decision guards.

This module owns `apply`, expectation rechecks, and the closed mutation algebra.
It must not decide transition inputs or construct complete transition decisions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import TypeVar

from millrace.contracts.compiled_plan import verify_authority_fingerprint
from millrace.contracts.state import (
    AdmittedPlan,
    GovernanceEventRecord,
    RunnerSessionRecord,
    RunRef,
    RuntimeState,
    TraceRecord,
)
from millrace.contracts.transition import (
    AdmitPlanRef,
    AdvanceRunnerSessionRecord,
    CloseClosureTarget,
    CloseWorkItem,
    CreateActivation,
    CreateRun,
    CreateRunnerSessionRecord,
    CreateWorkItem,
    EmitGovernanceEvent,
    EmitTrace,
    RecordArtifact,
    RecordClosureBlocked,
    RecordClosureEvaluation,
    RecordClosureTarget,
    RecordClosureTerminal,
    RecordCooldownWait,
    RecordCounter,
    RecordEffectProposal,
    RecordEffectReconciliation,
    RecordFanout,
    RecordInputReceipt,
    RecordLineageQuarantine,
    RecordOperatorIntervention,
    RecordOperatorWait,
    RecordQueueClosure,
    RecordRecoveryAttempt,
    RecordRefusal,
    RecordRemediationWork,
    RecordRunnerObservation,
    RecordRunnerSessionCancellation,
    RecordRunnerSessionCancellationAttemptRecord,
    RecordRunnerSessionCompletionRecord,
    RecordTransition,
    RecordWorkDependency,
    RouteActivation,
    SelectDefaultPlanRef,
    SetDispatchSuspension,
    SetPause,
    SetQuarantine,
    SupersedeLineageQuarantine,
    TransitionDecision,
    TransitionMutation,
)
from millrace.kernel.errors import StateConcurrencyError, UnsupportedMutationError
from millrace.kernel.lookups import (
    active_lineage_quarantine_for,
    active_lineage_quarantine_scope_keys,
    active_operator_wait_for,
    active_operator_wait_scope_keys,
    external_enqueue_routes,
    run_has_observation,
)

T = TypeVar("T")


def apply(state: RuntimeState, decision: TransitionDecision) -> RuntimeState:
    """Apply a transition decision after rechecking expected state facts."""
    if decision.disposition == "replayed":
        return state
    _recheck_expectations(state, decision)
    _validate_audit_record_agreement(decision)
    _validate_admit_plan_refs(decision.mutations)
    _validate_durable_id_uniqueness(state, decision.mutations)

    next_state = state
    for mutation in decision.mutations:
        if isinstance(mutation, RecordInputReceipt):
            next_state = _apply_record_receipt(next_state, mutation)
        elif isinstance(mutation, AdmitPlanRef):
            next_state = _apply_admit_plan_ref(next_state, mutation)
        elif isinstance(mutation, SelectDefaultPlanRef):
            next_state = replace(next_state, default_plan_ref=mutation.plan_ref)
        elif isinstance(mutation, CreateWorkItem):
            next_state = _apply_create_work_item(next_state, mutation)
        elif isinstance(mutation, CreateActivation):
            next_state = _apply_create_activation(next_state, mutation)
        elif isinstance(mutation, CreateRun):
            next_state = _apply_create_run(next_state, mutation)
        elif isinstance(mutation, CreateRunnerSessionRecord):
            next_state = _apply_create_runner_session(next_state, mutation)
        elif isinstance(mutation, AdvanceRunnerSessionRecord):
            next_state = _apply_advance_runner_session(next_state, mutation)
        elif isinstance(mutation, RecordRunnerSessionCancellation):
            next_state = _apply_record_runner_session_cancellation(
                next_state,
                mutation,
            )
        elif isinstance(mutation, RecordRunnerSessionCancellationAttemptRecord):
            next_state = _apply_record_runner_session_cancellation_attempt(
                next_state,
                mutation,
            )
        elif isinstance(mutation, RecordRunnerSessionCompletionRecord):
            next_state = _apply_record_runner_session_completion(
                next_state,
                mutation,
            )
        elif isinstance(mutation, RecordRunnerObservation):
            next_state = _apply_record_runner_observation(next_state, mutation)
        elif isinstance(mutation, RecordArtifact):
            next_state = _apply_record_artifact(next_state, mutation)
        elif isinstance(mutation, RecordEffectProposal):
            next_state = _apply_record_effect_proposal(next_state, mutation)
        elif isinstance(mutation, RecordEffectReconciliation):
            next_state = _apply_record_effect_reconciliation(next_state, mutation)
        elif isinstance(mutation, RouteActivation):
            next_state = _apply_route_activation(next_state, mutation)
        elif isinstance(mutation, RecordFanout):
            next_state = _apply_record_fanout(next_state, mutation)
        elif isinstance(mutation, RecordWorkDependency):
            next_state = _apply_record_work_dependency(next_state, mutation)
        elif isinstance(mutation, RecordClosureTarget):
            next_state = _apply_record_closure_target(next_state, mutation)
        elif isinstance(mutation, CloseClosureTarget):
            next_state = _apply_close_closure_target(next_state, mutation)
        elif isinstance(mutation, RecordClosureEvaluation):
            next_state = _apply_record_closure_evaluation(
                next_state,
                mutation,
            )
        elif isinstance(mutation, RecordClosureTerminal):
            next_state = _apply_record_closure_terminal(next_state, mutation)
        elif isinstance(mutation, RecordRemediationWork):
            next_state = _apply_record_remediation_work(next_state, mutation)
        elif isinstance(mutation, RecordClosureBlocked):
            next_state = _apply_record_closure_blocked(next_state, mutation)
        elif isinstance(mutation, CloseWorkItem):
            next_state = _apply_close_work_item(next_state, mutation)
        elif isinstance(mutation, RecordQueueClosure):
            next_state = _apply_record_queue_closure(next_state, mutation)
        elif isinstance(mutation, SetPause):
            next_state = _apply_set_pause(next_state, mutation)
        elif isinstance(mutation, SetDispatchSuspension):
            next_state = _apply_set_dispatch_suspension(next_state, mutation)
        elif isinstance(mutation, SetQuarantine):
            next_state = _apply_set_quarantine(next_state, mutation)
        elif isinstance(mutation, RecordLineageQuarantine):
            next_state = _apply_record_lineage_quarantine(next_state, mutation)
        elif isinstance(mutation, SupersedeLineageQuarantine):
            next_state = _apply_supersede_lineage_quarantine(next_state, mutation)
        elif isinstance(mutation, RecordRecoveryAttempt):
            next_state = _apply_record_recovery_attempt(next_state, mutation)
        elif isinstance(mutation, RecordOperatorIntervention):
            next_state = _apply_record_operator_intervention(next_state, mutation)
        elif isinstance(mutation, RecordOperatorWait):
            next_state = _apply_record_operator_wait(next_state, mutation)
        elif isinstance(mutation, RecordCooldownWait):
            next_state = _apply_record_cooldown_wait(next_state, mutation)
        elif isinstance(mutation, RecordCounter):
            next_state = _apply_record_counter(next_state, mutation)
        elif isinstance(mutation, RecordTransition):
            next_state = replace(
                next_state,
                transitions=(*next_state.transitions, mutation.transition_record),
            )
        elif isinstance(mutation, EmitGovernanceEvent):
            next_state = _apply_emit_governance_event(next_state, mutation)
        elif isinstance(mutation, EmitTrace):
            next_state = _apply_emit_trace(next_state, mutation)
        elif isinstance(mutation, RecordRefusal):
            next_state = replace(
                next_state,
                refusals=(*next_state.refusals, mutation.refusal),
            )
        else:
            raise UnsupportedMutationError(
                f"unsupported mutation kind: {type(mutation).__name__}"
            )
    return next_state


def _validate_admit_plan_refs(
    mutations: tuple[TransitionMutation, ...],
) -> None:
    for mutation in mutations:
        if isinstance(mutation, AdmitPlanRef):
            _validate_admit_plan_ref(mutation)


def _validate_admit_plan_ref(mutation: AdmitPlanRef) -> None:
    if not verify_authority_fingerprint(
        mutation.selected_plan,
        mutation.plan_ref.authority_fingerprint,
    ):
        raise UnsupportedMutationError("plan fingerprint mismatch")


def _non_empty_ids(values: Iterable[str]) -> set[str]:
    return {value for value in values if value}


def _ensure_new_durable_id(
    value: str,
    *,
    existing_ids: set[str],
    seen_ids: set[str],
    message: str,
) -> None:
    if not value:
        return
    if value in existing_ids or value in seen_ids:
        raise StateConcurrencyError(message)
    seen_ids.add(value)


def _ensure_record_id_agrees(
    wrapper_record_id: str,
    nested_record_id: str,
    *,
    message: str,
) -> None:
    if wrapper_record_id != nested_record_id:
        raise UnsupportedMutationError(message)


def _validate_durable_id_uniqueness(
    state: RuntimeState,
    mutations: tuple[TransitionMutation, ...],
) -> None:
    existing_transition_ids = _non_empty_ids(
        record.record_id for record in state.transitions
    )
    existing_refusal_ids = _non_empty_ids(record.record_id for record in state.refusals)
    existing_governance_ids = _non_empty_ids(
        event.record_id for event in state.governance_events
    )
    existing_trace_ids = _non_empty_ids(trace.record_id for trace in state.traces)
    existing_work_item_ids = _non_empty_ids(state.work_items)
    existing_activation_ids = _non_empty_ids(state.activations)
    existing_run_ids = _non_empty_ids(state.runs)
    existing_claim_ids = _non_empty_ids(
        run.run_ref.claim_id for run in state.runs.values()
    )
    existing_runner_observation_ids = _non_empty_ids(state.runner_observations)
    existing_runner_session_ids = _non_empty_ids(state.runner_sessions)
    existing_runner_session_cancellation_ids = _non_empty_ids(
        state.runner_session_cancellation_requests
    )
    existing_runner_session_attempt_ids = _non_empty_ids(
        state.runner_session_cancellation_attempts
    )
    existing_runner_session_completion_ids = _non_empty_ids(
        record.completion_id
        for record in state.runner_session_completions.values()
    )
    existing_runner_session_application_input_ids = _non_empty_ids(
        record.application_input_id
        for record in state.runner_session_completions.values()
    )
    existing_artifact_ids = _non_empty_ids(state.artifacts)
    existing_effect_proposal_ids = _non_empty_ids(state.effect_proposals)
    existing_effect_proposal_dedupe_keys = _non_empty_ids(
        proposal.dedupe_key for proposal in state.effect_proposals.values()
    )
    existing_effect_reconciliation_ids = _non_empty_ids(state.effect_reconciliations)
    existing_effect_reconciliation_effect_ids = _non_empty_ids(
        reconciliation.effect_id
        for reconciliation in state.effect_reconciliations.values()
    )
    existing_activation_route_ids = _non_empty_ids(
        route.record_id for route in state.activation_routes
    )
    existing_fanout_record_ids = _non_empty_ids(state.fanout_records)
    existing_work_dependency_ids = _non_empty_ids(state.work_dependencies)
    existing_closure_target_ids = _non_empty_ids(state.closure_targets)
    existing_closure_evaluation_ids = _non_empty_ids(
        state.closure_evaluations
    )
    existing_closure_terminal_ids = _non_empty_ids(state.closure_terminal_records)
    existing_remediation_work_ids = _non_empty_ids(state.remediation_work_records)
    existing_closure_blocked_ids = _non_empty_ids(state.closure_blocked_records)
    existing_closed_work_item_ids = _non_empty_ids(state.closed_work_items)
    existing_closed_work_item_record_ids = _non_empty_ids(
        record.record_id for record in state.closed_work_items.values()
    )
    existing_queue_closure_ids = _non_empty_ids(state.queue_closures)
    existing_pause_ids = (
        {state.pause.record_id}
        if state.pause is not None and state.pause.record_id
        else set()
    )
    existing_quarantine_ids = _non_empty_ids(state.quarantines)
    existing_quarantine_record_ids = _non_empty_ids(
        record.record_id for record in state.quarantines.values()
    )
    existing_lineage_quarantine_record_ids = _non_empty_ids(state.lineage_quarantines)
    existing_operator_intervention_ids = _non_empty_ids(state.operator_interventions)
    existing_operator_wait_ids = _non_empty_ids(state.operator_waits)
    existing_cooldown_wait_ids = _non_empty_ids(state.cooldown_waits)
    existing_counter_ids = _non_empty_ids(state.counters)

    seen_transition_ids: set[str] = set()
    seen_refusal_ids: set[str] = set()
    seen_governance_ids: set[str] = set()
    seen_trace_ids: set[str] = set()
    seen_work_item_ids: set[str] = set()
    seen_activation_ids: set[str] = set()
    seen_run_ids: set[str] = set()
    seen_claim_ids: set[str] = set()
    seen_runner_observation_ids: set[str] = set()
    seen_runner_session_ids: set[str] = set()
    seen_runner_session_cancellation_ids: set[str] = set()
    seen_runner_session_attempt_ids: set[str] = set()
    seen_runner_session_completion_ids: set[str] = set()
    seen_runner_session_application_input_ids: set[str] = set()
    seen_artifact_ids: set[str] = set()
    seen_effect_proposal_ids: set[str] = set()
    seen_effect_proposal_dedupe_keys: set[str] = set()
    seen_effect_reconciliation_ids: set[str] = set()
    seen_effect_reconciliation_effect_ids: set[str] = set()
    seen_activation_route_ids: set[str] = set()
    seen_fanout_record_ids: set[str] = set()
    seen_work_dependency_ids: set[str] = set()
    seen_closure_target_ids: set[str] = set()
    seen_closure_evaluation_ids: set[str] = set()
    seen_closure_terminal_ids: set[str] = set()
    seen_remediation_work_ids: set[str] = set()
    seen_closure_blocked_ids: set[str] = set()
    seen_closed_work_item_ids: set[str] = set()
    seen_closed_work_item_record_ids: set[str] = set()
    seen_queue_closure_ids: set[str] = set()
    seen_pause_ids: set[str] = set()
    seen_quarantine_ids: set[str] = set()
    seen_quarantine_record_ids: set[str] = set()
    seen_lineage_quarantine_record_ids: set[str] = set()
    seen_recovery_attempt_ids: set[str] = set()
    seen_operator_intervention_ids: set[str] = set()
    seen_operator_wait_ids: set[str] = set()
    seen_cooldown_wait_ids: set[str] = set()
    seen_counter_ids: set[str] = set()

    for mutation in mutations:
        if isinstance(mutation, RecordTransition):
            _ensure_new_durable_id(
                mutation.transition_record.record_id,
                existing_ids=existing_transition_ids,
                seen_ids=seen_transition_ids,
                message="transition already exists",
            )
        elif isinstance(mutation, RecordRefusal):
            _ensure_new_durable_id(
                mutation.refusal.record_id,
                existing_ids=existing_refusal_ids,
                seen_ids=seen_refusal_ids,
                message="refusal already exists",
            )
        elif isinstance(mutation, EmitGovernanceEvent):
            event = mutation.event
            if event is None:
                raise UnsupportedMutationError("governance event record is missing")
            _ensure_new_durable_id(
                event.record_id,
                existing_ids=existing_governance_ids,
                seen_ids=seen_governance_ids,
                message="governance event already exists",
            )
        elif isinstance(mutation, EmitTrace):
            trace = mutation.trace
            if trace is None:
                raise UnsupportedMutationError("trace record is missing")
            _ensure_new_durable_id(
                trace.record_id,
                existing_ids=existing_trace_ids,
                seen_ids=seen_trace_ids,
                message="trace already exists",
            )
        elif isinstance(mutation, CreateWorkItem):
            _ensure_new_durable_id(
                mutation.work_item.ref.work_item_id,
                existing_ids=existing_work_item_ids,
                seen_ids=seen_work_item_ids,
                message="work item already exists",
            )
        elif isinstance(mutation, CreateActivation):
            _ensure_new_durable_id(
                mutation.activation.activation_id,
                existing_ids=existing_activation_ids,
                seen_ids=seen_activation_ids,
                message="activation already exists",
            )
        elif isinstance(mutation, CreateRun):
            _ensure_new_durable_id(
                mutation.run.run_ref.run_id,
                existing_ids=existing_run_ids,
                seen_ids=seen_run_ids,
                message="run already exists",
            )
            _ensure_new_durable_id(
                mutation.run.run_ref.claim_id,
                existing_ids=existing_claim_ids,
                seen_ids=seen_claim_ids,
                message="claim already exists",
            )
        elif isinstance(mutation, CreateRunnerSessionRecord):
            _ensure_new_durable_id(
                mutation.session.session_id,
                existing_ids=existing_runner_session_ids,
                seen_ids=seen_runner_session_ids,
                message="runner session already exists",
            )
        elif isinstance(mutation, RecordRunnerSessionCancellation):
            _ensure_new_durable_id(
                mutation.record.request_id,
                existing_ids=existing_runner_session_cancellation_ids,
                seen_ids=seen_runner_session_cancellation_ids,
                message="runner session cancellation request already exists",
            )
        elif isinstance(mutation, RecordRunnerSessionCancellationAttemptRecord):
            _ensure_new_durable_id(
                mutation.record.attempt_id,
                existing_ids=existing_runner_session_attempt_ids,
                seen_ids=seen_runner_session_attempt_ids,
                message="runner session cancellation attempt already exists",
            )
        elif isinstance(mutation, RecordRunnerSessionCompletionRecord):
            _ensure_new_durable_id(
                mutation.record.completion_id,
                existing_ids=existing_runner_session_completion_ids,
                seen_ids=seen_runner_session_completion_ids,
                message="runner session completion already exists",
            )
            _ensure_new_durable_id(
                mutation.record.application_input_id,
                existing_ids=existing_runner_session_application_input_ids,
                seen_ids=seen_runner_session_application_input_ids,
                message="runner session completion application input already exists",
            )
        elif isinstance(mutation, RecordRunnerObservation):
            observation = mutation.observation
            if observation is None:
                raise UnsupportedMutationError("runner observation record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                observation.observation_id,
                message="runner observation record id disagrees",
            )
            _ensure_new_durable_id(
                observation.observation_id,
                existing_ids=existing_runner_observation_ids,
                seen_ids=seen_runner_observation_ids,
                message="runner observation already exists",
            )
        elif isinstance(mutation, RecordArtifact):
            artifact = mutation.artifact
            if artifact is None:
                raise UnsupportedMutationError("artifact record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                artifact.artifact_id,
                message="artifact record id disagrees",
            )
            _ensure_new_durable_id(
                artifact.artifact_id,
                existing_ids=existing_artifact_ids,
                seen_ids=seen_artifact_ids,
                message="artifact already exists",
            )
        elif isinstance(mutation, RecordEffectProposal):
            proposal = mutation.record
            if proposal is None:
                raise UnsupportedMutationError("effect proposal record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                proposal.effect_id,
                message="effect proposal record id disagrees",
            )
            _ensure_new_durable_id(
                proposal.effect_id,
                existing_ids=existing_effect_proposal_ids,
                seen_ids=seen_effect_proposal_ids,
                message="effect proposal already exists",
            )
            _ensure_new_durable_id(
                proposal.dedupe_key,
                existing_ids=existing_effect_proposal_dedupe_keys,
                seen_ids=seen_effect_proposal_dedupe_keys,
                message="effect proposal dedupe key already exists",
            )
        elif isinstance(mutation, RecordEffectReconciliation):
            reconciliation = mutation.record
            if reconciliation is None:
                raise UnsupportedMutationError(
                    "effect reconciliation record is missing"
                )
            _ensure_record_id_agrees(
                mutation.record_id,
                reconciliation.reconciliation_id,
                message="effect reconciliation record id disagrees",
            )
            _ensure_new_durable_id(
                reconciliation.reconciliation_id,
                existing_ids=existing_effect_reconciliation_ids,
                seen_ids=seen_effect_reconciliation_ids,
                message="effect reconciliation already exists",
            )
            _ensure_new_durable_id(
                reconciliation.effect_id,
                existing_ids=existing_effect_reconciliation_effect_ids,
                seen_ids=seen_effect_reconciliation_effect_ids,
                message="effect reconciliation already exists for effect",
            )
        elif isinstance(mutation, RouteActivation):
            route = mutation.route
            if route is None:
                raise UnsupportedMutationError("activation route record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                route.record_id,
                message="activation route record id disagrees",
            )
            _ensure_new_durable_id(
                route.record_id,
                existing_ids=existing_activation_route_ids,
                seen_ids=seen_activation_route_ids,
                message="activation route already exists",
            )
        elif isinstance(mutation, RecordFanout):
            fanout_record = mutation.record
            if fanout_record is None:
                raise UnsupportedMutationError("fanout record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                fanout_record.record_id,
                message="fanout record id disagrees",
            )
            _ensure_new_durable_id(
                fanout_record.record_id,
                existing_ids=existing_fanout_record_ids,
                seen_ids=seen_fanout_record_ids,
                message="fanout already exists",
            )
        elif isinstance(mutation, RecordWorkDependency):
            dependency_record = mutation.record
            if dependency_record is None:
                raise UnsupportedMutationError("work dependency record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                dependency_record.dependency_id,
                message="work dependency record id disagrees",
            )
            _ensure_new_durable_id(
                dependency_record.dependency_id,
                existing_ids=existing_work_dependency_ids,
                seen_ids=seen_work_dependency_ids,
                message="work dependency already exists",
            )
        elif isinstance(mutation, RecordClosureTarget):
            target = mutation.record
            if target is None:
                raise UnsupportedMutationError("closure target record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                target.closure_target_id,
                message="closure target record id disagrees",
            )
            _ensure_new_durable_id(
                target.closure_target_id,
                existing_ids=existing_closure_target_ids,
                seen_ids=seen_closure_target_ids,
                message="closure target already exists",
            )
        elif isinstance(mutation, CloseClosureTarget):
            if mutation.record_id != mutation.closure_target_id:
                raise UnsupportedMutationError("closure target record id disagrees")
            if mutation.closed_by_record_id not in (
                existing_closure_terminal_ids | seen_closure_terminal_ids
            ):
                raise UnsupportedMutationError(
                    "closure target close must reference closure terminal record"
                )
        elif isinstance(mutation, RecordClosureEvaluation):
            evaluator_activation = mutation.record
            if evaluator_activation is None:
                raise UnsupportedMutationError(
                    "closure evaluator activation record is missing"
                )
            _ensure_record_id_agrees(
                mutation.record_id,
                evaluator_activation.record_id,
                message="closure evaluator activation record id disagrees",
            )
            _ensure_new_durable_id(
                evaluator_activation.record_id,
                existing_ids=existing_closure_evaluation_ids,
                seen_ids=seen_closure_evaluation_ids,
                message="closure evaluator activation already exists",
            )
        elif isinstance(mutation, RecordClosureTerminal):
            terminal = mutation.record
            if terminal is None:
                raise UnsupportedMutationError("closure terminal record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                terminal.record_id,
                message="closure terminal record id disagrees",
            )
            _ensure_new_durable_id(
                terminal.record_id,
                existing_ids=existing_closure_terminal_ids,
                seen_ids=seen_closure_terminal_ids,
                message="closure terminal record already exists",
            )
        elif isinstance(mutation, RecordRemediationWork):
            remediation_record = mutation.record
            if remediation_record is None:
                raise UnsupportedMutationError("remediation record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                remediation_record.record_id,
                message="remediation record id disagrees",
            )
            _ensure_new_durable_id(
                remediation_record.record_id,
                existing_ids=existing_remediation_work_ids,
                seen_ids=seen_remediation_work_ids,
                message="remediation record already exists",
            )
        elif isinstance(mutation, RecordClosureBlocked):
            blocked = mutation.record
            if blocked is None:
                raise UnsupportedMutationError("closure blocked record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                blocked.record_id,
                message="closure blocked record id disagrees",
            )
            _ensure_new_durable_id(
                blocked.record_id,
                existing_ids=existing_closure_blocked_ids,
                seen_ids=seen_closure_blocked_ids,
                message="closure blocked record already exists",
            )
        elif isinstance(mutation, CloseWorkItem):
            record = mutation.record
            if record is None:
                raise UnsupportedMutationError("closed work item record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                record.record_id,
                message="closed work item record id disagrees",
            )
            _ensure_new_durable_id(
                record.work_item_id,
                existing_ids=existing_closed_work_item_ids,
                seen_ids=seen_closed_work_item_ids,
                message="closed work item already exists",
            )
            _ensure_new_durable_id(
                record.record_id,
                existing_ids=existing_closed_work_item_record_ids,
                seen_ids=seen_closed_work_item_record_ids,
                message="closed work item already exists",
            )
        elif isinstance(mutation, RecordQueueClosure):
            closure_record = mutation.record
            if closure_record is None:
                raise UnsupportedMutationError("queue closure record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                closure_record.closure_id,
                message="queue closure record id disagrees",
            )
            _ensure_new_durable_id(
                closure_record.closure_id,
                existing_ids=existing_queue_closure_ids,
                seen_ids=seen_queue_closure_ids,
                message="queue closure record already exists",
            )
        elif isinstance(mutation, SetPause):
            pause = mutation.record
            if pause is None:
                raise UnsupportedMutationError("pause record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                pause.record_id,
                message="pause record id disagrees",
            )
            _ensure_new_durable_id(
                pause.record_id,
                existing_ids=existing_pause_ids,
                seen_ids=seen_pause_ids,
                message="pause already exists",
            )
        elif isinstance(mutation, SetQuarantine):
            quarantine = mutation.record
            if quarantine is None:
                raise UnsupportedMutationError("quarantine record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                quarantine.record_id,
                message="quarantine record id disagrees",
            )
            _ensure_new_durable_id(
                quarantine.work_item_id,
                existing_ids=existing_quarantine_ids,
                seen_ids=seen_quarantine_ids,
                message="quarantine already exists",
            )
            _ensure_new_durable_id(
                quarantine.record_id,
                existing_ids=existing_quarantine_record_ids,
                seen_ids=seen_quarantine_record_ids,
                message="quarantine already exists",
            )
        elif isinstance(mutation, RecordLineageQuarantine):
            lineage_quarantine = mutation.record
            if lineage_quarantine is None:
                raise UnsupportedMutationError("lineage quarantine record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                lineage_quarantine.quarantine_id,
                message="lineage quarantine record id disagrees",
            )
            _ensure_new_durable_id(
                lineage_quarantine.quarantine_id,
                existing_ids=existing_lineage_quarantine_record_ids,
                seen_ids=seen_lineage_quarantine_record_ids,
                message="lineage quarantine already exists",
            )
        elif isinstance(mutation, SupersedeLineageQuarantine):
            if mutation.record_id != mutation.quarantine_id:
                raise UnsupportedMutationError("lineage quarantine record id disagrees")
        elif isinstance(mutation, RecordRecoveryAttempt):
            attempt = mutation.attempt
            if attempt is None:
                raise UnsupportedMutationError("recovery attempt record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                attempt.record_id,
                message="recovery attempt record id disagrees",
            )
            if attempt.record_id in seen_recovery_attempt_ids:
                raise StateConcurrencyError("recovery attempt changed")
            seen_recovery_attempt_ids.add(attempt.record_id)
        elif isinstance(mutation, RecordOperatorIntervention):
            intervention_record = mutation.record
            if intervention_record is None:
                raise UnsupportedMutationError(
                    "operator intervention record is missing"
                )
            _ensure_record_id_agrees(
                mutation.record_id,
                intervention_record.record_id,
                message="operator intervention record id disagrees",
            )
            _ensure_new_durable_id(
                intervention_record.record_id,
                existing_ids=existing_operator_intervention_ids,
                seen_ids=seen_operator_intervention_ids,
                message="operator intervention already exists",
            )
        elif isinstance(mutation, RecordOperatorWait):
            operator_wait = mutation.record
            if operator_wait is None:
                raise UnsupportedMutationError("operator wait record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                operator_wait.wait_id,
                message="operator wait record id disagrees",
            )
            if operator_wait.wait_id in seen_operator_wait_ids:
                raise StateConcurrencyError("operator wait changed")
            seen_operator_wait_ids.add(operator_wait.wait_id)
            if operator_wait.wait_id not in existing_operator_wait_ids:
                _ensure_new_durable_id(
                    operator_wait.wait_id,
                    existing_ids=existing_operator_wait_ids,
                    seen_ids=set(),
                    message="operator wait already exists",
                )
        elif isinstance(mutation, RecordCooldownWait):
            wait = mutation.wait
            if wait is None:
                raise UnsupportedMutationError("cooldown wait record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                wait.wait_id,
                message="cooldown wait record id disagrees",
            )
            if wait.wait_id in seen_cooldown_wait_ids:
                raise StateConcurrencyError("cooldown wait changed")
            seen_cooldown_wait_ids.add(wait.wait_id)
            if wait.wait_id not in existing_cooldown_wait_ids:
                _ensure_new_durable_id(
                    wait.wait_id,
                    existing_ids=existing_cooldown_wait_ids,
                    seen_ids=set(),
                    message="cooldown wait already exists",
                )
        elif isinstance(mutation, RecordCounter):
            counter_record = mutation.record
            if counter_record is None:
                raise UnsupportedMutationError("counter record is missing")
            _ensure_record_id_agrees(
                mutation.record_id,
                counter_record.record_id,
                message="counter record id disagrees",
            )
            if counter_record.record_id in seen_counter_ids:
                raise StateConcurrencyError("counter changed")
            seen_counter_ids.add(counter_record.record_id)
            if counter_record.record_id not in existing_counter_ids:
                _ensure_new_durable_id(
                    counter_record.record_id,
                    existing_ids=existing_counter_ids,
                    seen_ids=set(),
                    message="counter already exists",
                )


def _validate_audit_record_agreement(decision: TransitionDecision) -> None:
    mutation_events: list[GovernanceEventRecord] = []
    mutation_traces: list[TraceRecord] = []
    for mutation in decision.mutations:
        if isinstance(mutation, EmitGovernanceEvent):
            if mutation.event is None:
                raise UnsupportedMutationError("governance event record is missing")
            if mutation.record_id != mutation.event.record_id:
                raise UnsupportedMutationError("governance event record id disagrees")
            mutation_events.append(mutation.event)
        elif isinstance(mutation, EmitTrace):
            if mutation.trace is None:
                raise UnsupportedMutationError("trace record is missing")
            if mutation.record_id != mutation.trace.record_id:
                raise UnsupportedMutationError("trace record id disagrees")
            mutation_traces.append(mutation.trace)

    if tuple(mutation_events) != decision.governance_events:
        raise UnsupportedMutationError(
            "governance event records disagree with mutation records"
        )
    if tuple(mutation_traces) != decision.trace_records:
        raise UnsupportedMutationError(
            "trace records disagree with mutation records"
        )


def _recheck_expectations(
    state: RuntimeState,
    decision: TransitionDecision,
) -> None:
    if decision.expected_pause_absent and state.pause is not None:
        raise StateConcurrencyError("pause state changed")
    suspension = state.dispatch_suspension
    suspension_active = suspension is not None and suspension.status == "active"
    if decision.expected_dispatch_suspension_absent and suspension_active:
        raise StateConcurrencyError("dispatch suspension changed")
    expected_suspension_generation = (
        decision.expected_dispatch_suspension_generation
    )
    actual_suspension_generation = (
        None if suspension is None else suspension.generation
    )
    if (
        expected_suspension_generation is not None
        and actual_suspension_generation != expected_suspension_generation
    ):
        raise StateConcurrencyError("dispatch suspension changed")

    active_lineage_quarantine_keys = active_lineage_quarantine_scope_keys(state)
    for scope_key in decision.expected_lineage_quarantine_absent:
        if scope_key in active_lineage_quarantine_keys:
            raise StateConcurrencyError("lineage quarantine state changed")

    active_operator_wait_keys = active_operator_wait_scope_keys(state)
    for scope_key in decision.expected_operator_wait_absent:
        if scope_key in active_operator_wait_keys:
            raise StateConcurrencyError("operator wait state changed")

    if decision.expected_plan_fingerprint is not None:
        if decision.expected_plan_fingerprint not in state.admitted_plans:
            raise StateConcurrencyError("expected plan fingerprint is not admitted")
        if (
            _creates_work_item(decision.mutations)
            and not decision.expected_run_generations
        ):
            default_plan_ref = state.default_plan_ref
            if (
                default_plan_ref is None
                or default_plan_ref.authority_fingerprint
                != decision.expected_plan_fingerprint
            ):
                raise StateConcurrencyError("default plan fingerprint changed")

    for mutation in decision.mutations:
        if (
            isinstance(mutation, RecordQueueClosure)
            and mutation.record is not None
            and state.default_plan_ref != mutation.record.selected_plan_ref
        ):
            raise StateConcurrencyError("default plan fingerprint changed")

    for work_item_id, generation in decision.expected_work_item_generations.items():
        work_item = state.work_items.get(work_item_id)
        if work_item is None or work_item.ref.generation != generation:
            raise StateConcurrencyError("work item generation changed")

    for work_item_id, plan_ref in decision.expected_work_item_plan_refs.items():
        work_item = state.work_items.get(work_item_id)
        if work_item is None or work_item.ref.plan_ref != plan_ref:
            raise StateConcurrencyError("work item plan ref changed")

    for activation_id, generation in decision.expected_activation_generations.items():
        activation = state.activations.get(activation_id)
        if activation is None or activation.generation != generation:
            raise StateConcurrencyError("activation generation changed")

    for activation_id, plan_ref in decision.expected_activation_plan_refs.items():
        activation = state.activations.get(activation_id)
        if activation is None or activation.plan_ref != plan_ref:
            raise StateConcurrencyError("activation plan ref changed")

    for activation_id, claimed_by_run_id in decision.expected_activation_claims.items():
        activation = state.activations.get(activation_id)
        if (
            activation is None
            or activation.claimed_by_run_id != claimed_by_run_id
        ):
            raise StateConcurrencyError("activation claim state changed")

    for activation_id in decision.expected_activation_unclaimed:
        activation = state.activations.get(activation_id)
        if activation is None or activation.claimed_by_run_id is not None:
            raise StateConcurrencyError("activation claim state changed")

    for run_id, generation in decision.expected_run_generations.items():
        run = state.runs.get(run_id)
        if run is None or run.run_ref.generation != generation:
            raise StateConcurrencyError("run generation changed")

    for run_id, fencing_token in decision.expected_run_fencing_tokens.items():
        run = state.runs.get(run_id)
        if run is None or run.run_ref.fencing_token != fencing_token:
            raise StateConcurrencyError("run fencing token changed")

    for run_id, session_id in decision.expected_run_current_session_ids.items():
        run = state.runs.get(run_id)
        if run is None or run.current_session_id != session_id:
            raise StateConcurrencyError("run session state changed")

    for session_id, snapshot in decision.expected_runner_session_snapshots.items():
        session = state.runner_sessions.get(session_id)
        if (
            session is None
            or (session.state, session.cleanup_disposition) != snapshot
        ):
            raise StateConcurrencyError("runner session state changed")

    for lineage_id, expected_work_item_ids in (
        decision.expected_lineage_work_item_ids.items()
    ):
        actual_work_item_ids = tuple(
            sorted(
                work_item.ref.work_item_id
                for work_item in state.work_items.values()
                if work_item.lineage_id == lineage_id
            )
        )
        if actual_work_item_ids != expected_work_item_ids:
            raise StateConcurrencyError("lineage membership changed")

    # Runner observations are accepted once per run; stale duplicate decisions
    # must fail before a second observation mutation is attempted.
    for run_id in decision.expected_run_unobserved:
        if run_has_observation(state, run_id):
            raise StateConcurrencyError("run observation state changed")

    for work_item_id in decision.expected_work_item_open:
        if work_item_id in state.closed_work_items:
            raise StateConcurrencyError("closed work item state changed")


def _apply_record_receipt(
    state: RuntimeState,
    mutation: RecordInputReceipt,
) -> RuntimeState:
    input_id = mutation.receipt.receipt_ref.input_id
    existing = state.receipts.get(input_id)
    if existing is not None and existing != mutation.receipt:
        raise StateConcurrencyError("input receipt changed")
    return replace(
        state,
        receipts=_mapping_with(state.receipts, input_id, mutation.receipt),
    )


def _apply_admit_plan_ref(
    state: RuntimeState,
    mutation: AdmitPlanRef,
) -> RuntimeState:
    _validate_admit_plan_ref(mutation)
    fingerprint = mutation.plan_ref.authority_fingerprint
    existing = state.admitted_plans.get(fingerprint)
    admitted_plan = AdmittedPlan(
        plan_ref=mutation.plan_ref,
        selected_plan=mutation.selected_plan,
        external_enqueue_routes=external_enqueue_routes(mutation.selected_plan),
    )
    if existing is not None and existing != admitted_plan:
        raise StateConcurrencyError("admitted plan changed")
    return replace(
        state,
        admitted_plans=_mapping_with(
            state.admitted_plans,
            fingerprint,
            admitted_plan,
        ),
    )


def _apply_create_work_item(
    state: RuntimeState,
    mutation: CreateWorkItem,
) -> RuntimeState:
    work_item_id = mutation.work_item.ref.work_item_id
    if work_item_id in state.work_items:
        raise StateConcurrencyError("work item already exists")
    return replace(
        state,
        work_items=_mapping_with(state.work_items, work_item_id, mutation.work_item),
    )


def _apply_create_activation(
    state: RuntimeState,
    mutation: CreateActivation,
) -> RuntimeState:
    activation_id = mutation.activation.activation_id
    if activation_id in state.activations:
        raise StateConcurrencyError("activation already exists")
    if mutation.activation.work_item_id not in state.work_items:
        raise StateConcurrencyError("activation work item is missing")
    return replace(
        state,
        activations=_mapping_with(
            state.activations,
            activation_id,
            mutation.activation,
        ),
    )


def _apply_create_run(
    state: RuntimeState,
    mutation: CreateRun,
) -> RuntimeState:
    run = mutation.run
    if run.run_ref.run_id in state.runs:
        raise StateConcurrencyError("run already exists")
    activation = state.activations.get(run.activation_id)
    if activation is None:
        raise StateConcurrencyError("activation is missing")
    if activation.claimed_by_run_id is not None:
        raise StateConcurrencyError("activation already claimed")
    if run.work_item_id not in state.work_items:
        raise StateConcurrencyError("run work item is missing")

    claimed_activation = replace(
        activation,
        generation=activation.generation + 1,
        claimed_by_run_id=run.run_ref.run_id,
    )
    return replace(
        state,
        activations=_mapping_with(
            state.activations,
            claimed_activation.activation_id,
            claimed_activation,
        ),
        runs=_mapping_with(state.runs, run.run_ref.run_id, run),
    )


def _apply_create_runner_session(
    state: RuntimeState,
    mutation: CreateRunnerSessionRecord,
) -> RuntimeState:
    session = mutation.session
    run = state.runs.get(session.run_id)
    if run is None:
        raise StateConcurrencyError("runner session run is missing")
    if run.run_ref != mutation.expected_run_ref:
        raise StateConcurrencyError("runner session run authority changed")
    if run.current_session_id != mutation.expected_current_session_id:
        raise StateConcurrencyError("runner session pointer changed")
    if session.session_id in state.runner_sessions:
        raise StateConcurrencyError("runner session already exists")
    if session.dispatch_generation != run.last_dispatch_generation + 1:
        raise StateConcurrencyError("runner session generation changed")
    updated_run = replace(
        run,
        current_session_id=session.session_id,
        last_dispatch_generation=session.dispatch_generation,
    )
    return replace(
        state,
        runs=_mapping_with(state.runs, session.run_id, updated_run),
        runner_sessions=_mapping_with(
            state.runner_sessions,
            session.session_id,
            session,
        ),
    )


def _runner_session_for_mutation(
    state: RuntimeState,
    *,
    run_ref: RunRef,
    session_id: str,
    expected_session_state: str,
) -> RunnerSessionRecord:
    run_id = run_ref.run_id
    run = state.runs.get(run_id)
    if run is None or run.run_ref != run_ref:
        raise StateConcurrencyError("runner session run authority changed")
    if run.current_session_id != session_id:
        raise StateConcurrencyError("runner session pointer changed")
    session = state.runner_sessions.get(session_id)
    if session is None or session.state != expected_session_state:
        raise StateConcurrencyError("runner session state changed")
    return session


def _apply_advance_runner_session(
    state: RuntimeState,
    mutation: AdvanceRunnerSessionRecord,
) -> RuntimeState:
    prior = _runner_session_for_mutation(
        state,
        run_ref=mutation.expected_run_ref,
        session_id=mutation.session.session_id,
        expected_session_state=mutation.expected_session_state,
    )
    if (
        mutation.session.run_id != prior.run_id
        or mutation.session.dispatch_generation != prior.dispatch_generation
        or mutation.session.session_fencing_token != prior.session_fencing_token
    ):
        raise StateConcurrencyError("runner session authority changed")
    return replace(
        state,
        runner_sessions=_mapping_with(
            state.runner_sessions,
            mutation.session.session_id,
            mutation.session,
        ),
    )


def _apply_record_runner_session_cancellation(
    state: RuntimeState,
    mutation: RecordRunnerSessionCancellation,
) -> RuntimeState:
    record = mutation.record
    _runner_session_for_mutation(
        state,
        run_ref=mutation.expected_run_ref,
        session_id=record.session_id,
        expected_session_state=mutation.expected_session_state,
    )
    if record.request_id in state.runner_session_cancellation_requests:
        raise StateConcurrencyError("runner session cancellation request exists")
    requests = tuple(
        request
        for request in state.runner_session_cancellation_requests.values()
        if request.session_id == record.session_id
    )
    if record.request_order != len(requests) + 1:
        raise StateConcurrencyError("runner session cancellation order changed")
    return replace(
        state,
        runner_session_cancellation_requests=_mapping_with(
            state.runner_session_cancellation_requests,
            record.request_id,
            record,
        ),
    )


def _apply_record_runner_session_cancellation_attempt(
    state: RuntimeState,
    mutation: RecordRunnerSessionCancellationAttemptRecord,
) -> RuntimeState:
    record = mutation.record
    _runner_session_for_mutation(
        state,
        run_ref=mutation.expected_run_ref,
        session_id=record.session_id,
        expected_session_state=mutation.expected_session_state,
    )
    request = state.runner_session_cancellation_requests.get(record.request_id)
    if request is None or request.session_id != record.session_id:
        raise StateConcurrencyError("runner session cancellation request changed")
    attempts = tuple(
        attempt
        for attempt in state.runner_session_cancellation_attempts.values()
        if attempt.session_id == record.session_id
    )
    if (
        record.attempt_id in state.runner_session_cancellation_attempts
        or record.sequence != len(attempts) + 1
    ):
        raise StateConcurrencyError("runner session cancellation attempt changed")
    return replace(
        state,
        runner_session_cancellation_attempts=_mapping_with(
            state.runner_session_cancellation_attempts,
            record.attempt_id,
            record,
        ),
    )


def _apply_record_runner_session_completion(
    state: RuntimeState,
    mutation: RecordRunnerSessionCompletionRecord,
) -> RuntimeState:
    record = mutation.record
    _runner_session_for_mutation(
        state,
        run_ref=mutation.expected_run_ref,
        session_id=record.session_id,
        expected_session_state=mutation.expected_session_state,
    )
    if record.session_id in state.runner_session_completions:
        raise StateConcurrencyError("runner session completion exists")
    if any(
        existing.application_input_id == record.application_input_id
        for existing in state.runner_session_completions.values()
    ):
        raise StateConcurrencyError(
            "runner session completion application input exists"
        )
    return replace(
        state,
        runner_session_completions=_mapping_with(
            state.runner_session_completions,
            record.session_id,
            record,
        ),
    )


def _apply_record_runner_observation(
    state: RuntimeState,
    mutation: RecordRunnerObservation,
) -> RuntimeState:
    observation = mutation.observation
    if observation is None:
        raise UnsupportedMutationError("runner observation record is missing")
    if observation.observation_id in state.runner_observations:
        raise StateConcurrencyError("runner observation already exists")
    if run_has_observation(state, observation.run_id):
        raise StateConcurrencyError("run observation already exists")
    return replace(
        state,
        runner_observations=_mapping_with(
            state.runner_observations,
            observation.observation_id,
            observation,
        ),
    )


def _apply_record_artifact(
    state: RuntimeState,
    mutation: RecordArtifact,
) -> RuntimeState:
    artifact = mutation.artifact
    if artifact is None:
        raise UnsupportedMutationError("artifact record is missing")
    if artifact.artifact_id in state.artifacts:
        raise StateConcurrencyError("artifact already exists")
    return replace(
        state,
        artifacts=_mapping_with(state.artifacts, artifact.artifact_id, artifact),
    )


def _apply_record_effect_proposal(
    state: RuntimeState,
    mutation: RecordEffectProposal,
) -> RuntimeState:
    proposal = mutation.record
    if proposal is None:
        raise UnsupportedMutationError("effect proposal record is missing")
    if proposal.effect_id in state.effect_proposals:
        raise StateConcurrencyError("effect proposal already exists")
    if any(
        existing.dedupe_key == proposal.dedupe_key
        for existing in state.effect_proposals.values()
    ):
        raise StateConcurrencyError("effect proposal dedupe key already exists")
    if proposal.artifact_id not in state.artifacts:
        raise StateConcurrencyError("effect proposal artifact is missing")
    if proposal.source_run_id not in state.runs:
        raise StateConcurrencyError("effect proposal source run is missing")
    if proposal.source_work_item_id not in state.work_items:
        raise StateConcurrencyError("effect proposal source work item is missing")
    if proposal.source_activation_id not in state.activations:
        raise StateConcurrencyError("effect proposal source activation is missing")
    return replace(
        state,
        effect_proposals=_mapping_with(
            state.effect_proposals,
            proposal.effect_id,
            proposal,
        ),
    )


def _apply_record_effect_reconciliation(
    state: RuntimeState,
    mutation: RecordEffectReconciliation,
) -> RuntimeState:
    reconciliation = mutation.record
    if reconciliation is None:
        raise UnsupportedMutationError("effect reconciliation record is missing")
    if reconciliation.reconciliation_id in state.effect_reconciliations:
        raise StateConcurrencyError("effect reconciliation already exists")
    if reconciliation.effect_id not in state.effect_proposals:
        raise StateConcurrencyError("effect reconciliation proposal is missing")
    if any(
        existing.effect_id == reconciliation.effect_id
        for existing in state.effect_reconciliations.values()
    ):
        raise StateConcurrencyError("effect reconciliation already exists for effect")
    return replace(
        state,
        effect_reconciliations=_mapping_with(
            state.effect_reconciliations,
            reconciliation.reconciliation_id,
            reconciliation,
        ),
    )


def _apply_route_activation(
    state: RuntimeState,
    mutation: RouteActivation,
) -> RuntimeState:
    route = mutation.route
    if route is None:
        raise UnsupportedMutationError("activation route record is missing")
    return replace(
        state,
        activation_routes=(*state.activation_routes, route),
    )


def _apply_record_fanout(
    state: RuntimeState,
    mutation: RecordFanout,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("fanout record is missing")
    existing = state.fanout_records.get(record.record_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("fanout already exists")
    if record.target_work_item_id not in state.work_items:
        raise StateConcurrencyError("fanout target work item is missing")
    if record.target_activation_id not in state.activations:
        raise StateConcurrencyError("fanout target activation is missing")
    return replace(
        state,
        fanout_records=_mapping_with(
            state.fanout_records,
            record.record_id,
            record,
        ),
    )


def _apply_record_work_dependency(
    state: RuntimeState,
    mutation: RecordWorkDependency,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("work dependency record is missing")
    existing = state.work_dependencies.get(record.dependency_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("work dependency already exists")
    if record.dependent_work_item_id not in state.work_items:
        raise StateConcurrencyError("dependent work item is missing")
    if record.dependency_work_item_id not in state.work_items:
        raise StateConcurrencyError("dependency work item is missing")
    if record.fanout_record_id not in state.fanout_records:
        raise StateConcurrencyError("fanout record is missing")
    return replace(
        state,
        work_dependencies=_mapping_with(
            state.work_dependencies,
            record.dependency_id,
            record,
        ),
    )


def _apply_record_closure_target(
    state: RuntimeState,
    mutation: RecordClosureTarget,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("closure target record is missing")
    existing = state.closure_targets.get(record.closure_target_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("closure target already exists")
    return replace(
        state,
        closure_targets=_mapping_with(
            state.closure_targets,
            record.closure_target_id,
            record,
        ),
    )


def _apply_close_closure_target(
    state: RuntimeState,
    mutation: CloseClosureTarget,
) -> RuntimeState:
    target = state.closure_targets.get(mutation.closure_target_id)
    if target is None:
        raise StateConcurrencyError("closure target is missing")
    if target.status != "open":
        raise StateConcurrencyError("closure target is not open")
    closed = replace(
        target,
        status="closed",
        closed_by_record_id=mutation.closed_by_record_id,
    )
    return replace(
        state,
        closure_targets=_mapping_with(
            state.closure_targets,
            mutation.closure_target_id,
            closed,
        ),
    )


def _apply_record_closure_evaluation(
    state: RuntimeState,
    mutation: RecordClosureEvaluation,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("closure evaluator activation record is missing")
    existing = state.closure_evaluations.get(record.record_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("closure evaluator activation already exists")
    return replace(
        state,
        closure_evaluations=_mapping_with(
            state.closure_evaluations,
            record.record_id,
            record,
        ),
    )


def _apply_record_closure_terminal(
    state: RuntimeState,
    mutation: RecordClosureTerminal,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("closure terminal record is missing")
    existing = state.closure_terminal_records.get(record.record_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("closure terminal record already exists")
    return replace(
        state,
        closure_terminal_records=_mapping_with(
            state.closure_terminal_records,
            record.record_id,
            record,
        ),
    )


def _apply_record_remediation_work(
    state: RuntimeState,
    mutation: RecordRemediationWork,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("remediation record is missing")
    existing = state.remediation_work_records.get(record.record_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("remediation record already exists")
    return replace(
        state,
        remediation_work_records=_mapping_with(
            state.remediation_work_records,
            record.record_id,
            record,
        ),
    )


def _apply_record_closure_blocked(
    state: RuntimeState,
    mutation: RecordClosureBlocked,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("closure blocked record is missing")
    existing = state.closure_blocked_records.get(record.record_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("closure blocked record already exists")
    return replace(
        state,
        closure_blocked_records=_mapping_with(
            state.closure_blocked_records,
            record.record_id,
            record,
        ),
    )


def _apply_close_work_item(
    state: RuntimeState,
    mutation: CloseWorkItem,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("closed work item record is missing")
    existing = state.closed_work_items.get(record.work_item_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("closed work item already exists")
    return replace(
        state,
        closed_work_items=_mapping_with(
            state.closed_work_items,
            record.work_item_id,
            record,
        ),
    )


def _apply_record_queue_closure(
    state: RuntimeState,
    mutation: RecordQueueClosure,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("queue closure record is missing")
    existing = state.queue_closures.get(record.closure_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("queue closure record already exists")
    return replace(
        state,
        queue_closures=_mapping_with(
            state.queue_closures,
            record.closure_id,
            record,
        ),
    )


def _apply_set_pause(
    state: RuntimeState,
    mutation: SetPause,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("pause record is missing")
    if state.pause is not None and state.pause != record:
        raise StateConcurrencyError("pause already exists")
    return replace(state, pause=record)


def _apply_set_dispatch_suspension(
    state: RuntimeState,
    mutation: SetDispatchSuspension,
) -> RuntimeState:
    if state.dispatch_suspension != mutation.expected_record:
        raise StateConcurrencyError("dispatch suspension changed")
    if len(state.runs) != mutation.expected_dispatch_generation:
        raise StateConcurrencyError("dispatch generation changed")
    if (
        mutation.expected_default_plan_ref is not None
        and state.default_plan_ref != mutation.expected_default_plan_ref
    ):
        raise StateConcurrencyError("default plan fingerprint changed")
    return replace(state, dispatch_suspension=mutation.record)


def _apply_set_quarantine(
    state: RuntimeState,
    mutation: SetQuarantine,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("quarantine record is missing")
    existing = state.quarantines.get(record.work_item_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("quarantine already exists")
    return replace(
        state,
        quarantines=_mapping_with(
            state.quarantines,
            record.work_item_id,
            record,
        ),
    )


def _apply_record_lineage_quarantine(
    state: RuntimeState,
    mutation: RecordLineageQuarantine,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("lineage quarantine record is missing")
    existing = state.lineage_quarantines.get(record.quarantine_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("lineage quarantine already exists")
    active = active_lineage_quarantine_for(
        state,
        record.lineage_id,
        plan_ref=record.selected_plan_ref,
        policy_id=str(record.policy_id),
    )
    if active is not None and active.quarantine_id != record.quarantine_id:
        raise StateConcurrencyError("lineage quarantine already exists")
    return replace(
        state,
        lineage_quarantines=_mapping_with(
            state.lineage_quarantines,
            record.quarantine_id,
            record,
        ),
    )


def _apply_supersede_lineage_quarantine(
    state: RuntimeState,
    mutation: SupersedeLineageQuarantine,
) -> RuntimeState:
    record = state.lineage_quarantines.get(mutation.quarantine_id)
    if record is None:
        raise StateConcurrencyError("lineage quarantine is missing")
    if record.quarantine_id != mutation.quarantine_id:
        raise StateConcurrencyError("lineage quarantine changed")
    if record.status != "active":
        raise StateConcurrencyError("lineage quarantine state changed")
    superseded = replace(
        record,
        status="superseded",
        superseded_input_id=mutation.superseded_input_id,
    )
    return replace(
        state,
        lineage_quarantines=_mapping_with(
            state.lineage_quarantines,
            superseded.quarantine_id,
            superseded,
        ),
    )


def _apply_record_recovery_attempt(
    state: RuntimeState,
    mutation: RecordRecoveryAttempt,
) -> RuntimeState:
    attempt = mutation.attempt
    if attempt is None:
        raise UnsupportedMutationError("recovery attempt record is missing")
    existing = state.recovery_attempts.get(attempt.record_id)
    if existing is not None:
        existing_key = (
            existing.plan_ref,
            existing.policy_id,
            existing.lineage_id,
        )
        attempt_key = (
            attempt.plan_ref,
            attempt.policy_id,
            attempt.lineage_id,
        )
        if existing_key != attempt_key:
            raise StateConcurrencyError("recovery attempt key changed")
    return replace(
        state,
        recovery_attempts=_mapping_with(
            state.recovery_attempts,
            attempt.record_id,
            attempt,
        ),
    )


def _apply_record_operator_intervention(
    state: RuntimeState,
    mutation: RecordOperatorIntervention,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("operator intervention record is missing")
    existing = state.operator_interventions.get(record.record_id)
    if existing is not None and existing != record:
        raise StateConcurrencyError("operator intervention already exists")
    return replace(
        state,
        operator_interventions=_mapping_with(
            state.operator_interventions,
            record.record_id,
            record,
        ),
    )


def _apply_record_operator_wait(
    state: RuntimeState,
    mutation: RecordOperatorWait,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("operator wait record is missing")
    existing = state.operator_waits.get(record.wait_id)
    if existing is not None:
        existing_key = (
            existing.selected_plan_ref,
            existing.operator_wait_id,
            existing.source_action_id,
            existing.lineage_id,
            existing.source_work_item_id,
            existing.source_activation_id,
            existing.source_run_id,
        )
        record_key = (
            record.selected_plan_ref,
            record.operator_wait_id,
            record.source_action_id,
            record.lineage_id,
            record.source_work_item_id,
            record.source_activation_id,
            record.source_run_id,
        )
        if existing_key != record_key:
            raise StateConcurrencyError("operator wait key changed")
        if existing.status != "active" and existing != record:
            raise StateConcurrencyError("operator wait state changed")
        if existing.status == "active" and record.status == "active":
            if existing != record:
                raise StateConcurrencyError("operator wait changed")
            return replace(
                state,
                operator_waits=_mapping_with(
                    state.operator_waits,
                    record.wait_id,
                    record,
                ),
            )
    elif record.status != "active":
        raise StateConcurrencyError("operator wait is missing")

    active = active_operator_wait_for(
        state,
        record.lineage_id,
        plan_ref=record.selected_plan_ref,
    )
    if (
        record.status == "active"
        and active is not None
        and active.wait_id != record.wait_id
    ):
        raise StateConcurrencyError("operator wait already exists")

    return replace(
        state,
        operator_waits=_mapping_with(state.operator_waits, record.wait_id, record),
    )


def _apply_record_cooldown_wait(
    state: RuntimeState,
    mutation: RecordCooldownWait,
) -> RuntimeState:
    wait = mutation.wait
    if wait is None:
        raise UnsupportedMutationError("cooldown wait record is missing")
    existing = state.cooldown_waits.get(wait.wait_id)
    if existing is not None:
        existing_key = (
            existing.plan_ref,
            existing.policy_id,
            existing.lineage_id,
            existing.recovery_attempt_record_id,
        )
        wait_key = (
            wait.plan_ref,
            wait.policy_id,
            wait.lineage_id,
            wait.recovery_attempt_record_id,
        )
        if existing_key != wait_key:
            raise StateConcurrencyError("cooldown wait key changed")
        if existing.consumed_input_id is not None and existing != wait:
            raise StateConcurrencyError("cooldown wait changed")
    return replace(
        state,
        cooldown_waits=_mapping_with(
            state.cooldown_waits,
            wait.wait_id,
            wait,
        ),
    )


def _apply_record_counter(
    state: RuntimeState,
    mutation: RecordCounter,
) -> RuntimeState:
    record = mutation.record
    if record is None:
        raise UnsupportedMutationError("counter record is missing")
    existing = state.counters.get(record.record_id)
    if existing is not None:
        existing_key = (
            existing.selected_plan_ref,
            existing.counter_id,
            existing.lineage_id,
        )
        record_key = (
            record.selected_plan_ref,
            record.counter_id,
            record.lineage_id,
        )
        if existing_key != record_key:
            raise StateConcurrencyError("counter key changed")
        if record.value < existing.value:
            raise StateConcurrencyError("counter regressed")
    return replace(
        state,
        counters=_mapping_with(state.counters, record.record_id, record),
    )


def _apply_emit_governance_event(
    state: RuntimeState,
    mutation: EmitGovernanceEvent,
) -> RuntimeState:
    event = mutation.event
    if event is None:
        raise UnsupportedMutationError("governance event record is missing")
    return replace(
        state,
        governance_events=(*state.governance_events, event),
    )


def _apply_emit_trace(
    state: RuntimeState,
    mutation: EmitTrace,
) -> RuntimeState:
    trace = mutation.trace
    if trace is None:
        raise UnsupportedMutationError("trace record is missing")
    return replace(state, traces=(*state.traces, trace))


def _creates_work_item(mutations: tuple[TransitionMutation, ...]) -> bool:
    return any(isinstance(mutation, CreateWorkItem) for mutation in mutations)


def _mapping_with(
    value: Mapping[str, T],
    key: str,
    item: T,
) -> Mapping[str, T]:
    updated = dict(value)
    updated[key] = item
    return updated


__all__ = (
    "StateConcurrencyError",
    "UnsupportedMutationError",
    "apply",
)
