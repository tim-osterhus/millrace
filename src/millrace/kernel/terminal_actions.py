"""Compiled terminal-action resolution for runner observations.

This module resolves compiled route, `create_incident_route`, close, and pause
actions into mutation and expectation data. It must not construct complete
`TransitionDecision` objects or import decision dispatch code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import cast

from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    AuthorityValue,
    CounterDeclaration,
    RecoveryPolicyDeclaration,
    SelectedCompiledPlan,
    TerminalActionDeclaration,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import (
    ActionId,
    QueueFamilyId,
    RecoveryPolicyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.operator_waits import _operator_wait_record_id
from millrace.contracts.state import (
    DURABLE_INT64_MAX,
    Activation,
    ActivationRouteRecord,
    ArtifactRecord,
    ClosedWorkItemRecord,
    CooldownWaitRecord,
    CounterRecord,
    EffectProposalRecord,
    LineageQuarantineRecord,
    OperatorWaitRecord,
    PauseRecord,
    PlanRef,
    QuarantineRecord,
    RecoveryAttemptRecord,
    RunnerObservationRecord,
    RunRecord,
    RuntimeState,
    WorkItem,
    WorkItemRef,
)
from millrace.contracts.transition import (
    CloseWorkItem,
    CreateActivation,
    CreateWorkItem,
    RecordArtifact,
    RecordCooldownWait,
    RecordCounter,
    RecordEffectProposal,
    RecordLineageQuarantine,
    RecordOperatorWait,
    RecordRecoveryAttempt,
    RecordRunnerObservation,
    RouteActivation,
    RunnerResultObserved,
    SetPause,
    SetQuarantine,
    TransitionContext,
    TransitionMutation,
    artifact_payload_digest,
    input_payload_digest,
)
from millrace.kernel.lookups import (
    active_lineage_quarantine_for,
    active_operator_wait_for,
    artifact_schema_for,
    counter_for_action,
    lineage_quarantine_scope_key,
    operator_wait_for_action,
    operator_wait_scope_key,
    route_contract_supported,
    runner_binding_for,
    stage_kind_for,
    wait_state_for_policy,
)
from millrace.kernel.projection import evaluate_projection, projection_context_for_run
from millrace.kernel.schema import validate_schema

_ARTIFACT_FIELD = "artifact_payload"
_ACTION_KIND_ROUTE = "route"
_ACTION_KIND_CLOSE = "close"
_ACTION_KIND_COMPLETE_WORK_ITEM = "complete_work_item"
_ACTION_KIND_CLOSE_WITH_ESCALATION = "close_with_escalation"
_ACTION_KIND_BLOCK_WORK_ITEM = "block_work_item"
_ACTION_KIND_PAUSE_QUARANTINE = "pause_quarantine"
_ACTION_KIND_CREATE_INCIDENT_ROUTE = "create_incident_route"
_ACTION_KIND_RECOVERY_ROUTE = "recovery_route"
_ACTION_KIND_RETURN_TO_RECORDED_SOURCE = "return_to_recorded_source"
_ACTION_KIND_QUARANTINE_LINEAGE = "quarantine_lineage"
_ACTION_KIND_OPERATOR_WAIT = "operator_wait"
_ACTION_KIND_CLOSURE_GAP = "closure_gap"
AUTHORITY_SOURCE_TERMINAL_ACTION = "terminal_action"
SUPPORTED_RUNTIME_TERMINAL_ACTION_KINDS = frozenset(
    (
        _ACTION_KIND_ROUTE,
        _ACTION_KIND_CREATE_INCIDENT_ROUTE,
        _ACTION_KIND_CLOSE,
        _ACTION_KIND_COMPLETE_WORK_ITEM,
        _ACTION_KIND_CLOSE_WITH_ESCALATION,
        _ACTION_KIND_BLOCK_WORK_ITEM,
        _ACTION_KIND_PAUSE_QUARANTINE,
        _ACTION_KIND_RECOVERY_ROUTE,
        _ACTION_KIND_RETURN_TO_RECORDED_SOURCE,
        _ACTION_KIND_QUARANTINE_LINEAGE,
        _ACTION_KIND_OPERATOR_WAIT,
        _ACTION_KIND_CLOSURE_GAP,
    )
)
_CLOSING_ACTION_KINDS = frozenset(
    (
        _ACTION_KIND_CLOSE,
        _ACTION_KIND_COMPLETE_WORK_ITEM,
        _ACTION_KIND_CLOSE_WITH_ESCALATION,
        _ACTION_KIND_BLOCK_WORK_ITEM,
        _ACTION_KIND_CLOSURE_GAP,
    )
)


@dataclass(frozen=True, slots=True)
class TerminalActionResolution:
    mutations: tuple[TransitionMutation, ...]
    expected_plan_fingerprint: AuthorityFingerprint | None
    expected_work_item_generations: Mapping[str, int]
    expected_activation_generations: Mapping[str, int]
    expected_run_generations: Mapping[str, int]
    expected_run_fencing_tokens: Mapping[str, str]
    expected_run_unobserved: tuple[str, ...]
    expected_lineage_quarantine_absent: tuple[str, ...]
    expected_operator_wait_absent: tuple[str, ...]
    expected_work_item_open: tuple[str, ...]
    expected_work_item_plan_refs: Mapping[str, PlanRef]
    expected_activation_plan_refs: Mapping[str, PlanRef]
    event_plan_fingerprint: AuthorityFingerprint | None
    event_work_item_id: str | None
    event_run_id: str | None
    event_action_id: ActionId | None
    event_authority_source: str | None


@dataclass(frozen=True, slots=True)
class TerminalActionRefusal:
    reason: str
    action: TerminalActionDeclaration


ThresholdRecoveryRoute = tuple[
    TerminalActionDeclaration,
    RecoveryPolicyDeclaration,
]
TerminalActionResult = TerminalActionResolution | TerminalActionRefusal


def resolve_terminal_action(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    observation_payload: Mapping[str, object] | None,
) -> TerminalActionResult:
    # Branching on compiled action_kind interprets compiler-validated
    # terminal-action authority; runner marker text never selects behavior here.
    if action.action_kind == _ACTION_KIND_ROUTE:
        counter_result = _counter_record_or_refusal(
            selected_plan=selected_plan,
            state=state,
            run=run,
            work_item=work_item,
            action=action,
            input_id=transition_input.input_id,
        )
        if isinstance(counter_result, TerminalActionRefusal):
            return counter_result
        threshold_result = _resolve_threshold_recovery_route_if_due(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            state=state,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
            counter_record=counter_result,
        )
        if threshold_result is not None:
            return threshold_result
        result = _resolve_route_action(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
            observation_payload=observation_payload,
        )
        result = _with_counter_record(result, counter_result)
        return _with_reset_recovery_attempts(
            result=result,
            selected_plan=selected_plan,
            state=state,
            run=run,
            work_item=work_item,
            action=action,
            input_id=transition_input.input_id,
        )
    if action.action_kind == _ACTION_KIND_CREATE_INCIDENT_ROUTE:
        counter_result = _counter_record_or_refusal(
            selected_plan=selected_plan,
            state=state,
            run=run,
            work_item=work_item,
            action=action,
            input_id=transition_input.input_id,
        )
        if isinstance(counter_result, TerminalActionRefusal):
            return counter_result
        threshold_result = _resolve_threshold_recovery_route_if_due(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            state=state,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
            counter_record=counter_result,
        )
        if threshold_result is not None:
            return threshold_result
        result = _resolve_projected_route_action(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
            observation_payload=observation_payload,
        )
        result = _with_counter_record(result, counter_result)
        return _with_reset_recovery_attempts(
            result=result,
            selected_plan=selected_plan,
            state=state,
            run=run,
            work_item=work_item,
            action=action,
            input_id=transition_input.input_id,
        )
    if action.action_kind in _CLOSING_ACTION_KINDS:
        counter_result = _counter_record_or_refusal(
            selected_plan=selected_plan,
            state=state,
            run=run,
            work_item=work_item,
            action=action,
            input_id=transition_input.input_id,
        )
        if isinstance(counter_result, TerminalActionRefusal):
            return counter_result
        threshold_result = _resolve_threshold_recovery_route_if_due(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            state=state,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
            counter_record=counter_result,
        )
        if threshold_result is not None:
            return threshold_result
        result = _resolve_close_action(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
            observation_payload=observation_payload,
        )
        result = _with_counter_record(result, counter_result)
        return _with_reset_recovery_attempts(
            result=result,
            selected_plan=selected_plan,
            state=state,
            run=run,
            work_item=work_item,
            action=action,
            input_id=transition_input.input_id,
        )
    if action.action_kind == _ACTION_KIND_PAUSE_QUARANTINE:
        result = _resolve_pause_quarantine_action(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
            observation_payload=observation_payload,
        )
        return _with_reset_recovery_attempts(
            result=result,
            selected_plan=selected_plan,
            state=state,
            run=run,
            work_item=work_item,
            action=action,
            input_id=transition_input.input_id,
        )
    if action.action_kind == _ACTION_KIND_RECOVERY_ROUTE:
        counter_result = _counter_record_or_refusal(
            selected_plan=selected_plan,
            state=state,
            run=run,
            work_item=work_item,
            action=action,
            input_id=transition_input.input_id,
        )
        if isinstance(counter_result, TerminalActionRefusal):
            return counter_result
        result = _resolve_recovery_route_action(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            state=state,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
        )
        return _with_counter_record(result, counter_result)
    if action.action_kind == _ACTION_KIND_RETURN_TO_RECORDED_SOURCE:
        return _resolve_return_to_recorded_source_action(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            state=state,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
        )
    if action.action_kind == _ACTION_KIND_QUARANTINE_LINEAGE:
        return _resolve_quarantine_lineage_action(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            state=state,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
        )
    if action.action_kind == _ACTION_KIND_OPERATOR_WAIT:
        result = _resolve_operator_wait_action(
            transition_input=transition_input,
            context=context,
            selected_plan=selected_plan,
            state=state,
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
        )
        return _with_reset_recovery_attempts(
            result=result,
            selected_plan=selected_plan,
            state=state,
            run=run,
            work_item=work_item,
            action=action,
            input_id=transition_input.input_id,
        )

    return TerminalActionRefusal(
        reason="unsupported_runtime_terminal_action",
        action=action,
    )


def _resolve_threshold_recovery_route_if_due(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    counter_record: CounterRecord | None,
) -> TerminalActionResult | None:
    threshold_recovery = _threshold_recovery_route_for_increment(
        selected_plan=selected_plan,
        action=action,
        counter_record=counter_record,
    )
    if threshold_recovery is None:
        return None
    threshold_action, policy = threshold_recovery
    result = _resolve_recovery_route_action(
        transition_input=transition_input,
        context=context,
        selected_plan=selected_plan,
        state=state,
        run=run,
        activation=activation,
        work_item=work_item,
        action=threshold_action,
        policy_override=policy,
    )
    return _with_counter_record(result, counter_record)


def _resolve_route_action(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    observation_payload: Mapping[str, object] | None,
) -> TerminalActionResult:
    target_stage_kind_id = action.target_stage_kind_id
    target_graph_node_id = action.target_graph_node_id
    emitted_queue_family_id = action.emitted_queue_family_id
    artifact_schema_id = action.artifact_schema_id
    runner_binding_id = action.runner_binding_id
    payload_projection = action.payload_projection
    target_fields = _route_target_fields_or_refusal(
        action=action,
        observation_payload=observation_payload,
    )
    if isinstance(target_fields, TerminalActionRefusal):
        return target_fields
    (
        target_stage_kind_id,
        target_graph_node_id,
        emitted_queue_family_id,
        runner_binding_id,
    ) = target_fields
    if (
        artifact_schema_id is None or payload_projection is None
    ):
        return TerminalActionRefusal(
            reason="unsupported_terminal_route",
            action=action,
        )

    if not route_contract_supported(
        selected_plan,
        source_stage_kind_id=str(run.stage_kind_id),
        target_stage_kind_id=str(target_stage_kind_id),
        emitted_queue_family_id=str(emitted_queue_family_id),
        artifact_schema_id=str(artifact_schema_id),
        runner_binding_id=str(runner_binding_id),
    ):
        return TerminalActionRefusal(
            reason="unsupported_terminal_route",
            action=action,
        )

    artifact_schema = artifact_schema_for(
        selected_plan,
        str(artifact_schema_id),
    )
    raw_artifact_payload = transition_input.payload.get(_ARTIFACT_FIELD)
    if artifact_schema is None or not isinstance(raw_artifact_payload, Mapping):
        return TerminalActionRefusal(
            reason="invalid_artifact_payload",
            action=action,
        )
    artifact_payload = raw_artifact_payload
    validation = validate_schema(artifact_schema.schema, raw_artifact_payload)
    if not validation.accepted:
        return TerminalActionRefusal(
            reason="invalid_artifact_payload",
            action=action,
        )

    projected = evaluate_projection(
        payload_projection,
        projection_context_for_run(
            work_item=work_item,
            run=run,
            observation_payload=(
                {} if observation_payload is None else observation_payload
            ),
            artifact_payload=artifact_payload,
        ),
    )
    if not projected.accepted or not isinstance(projected.value, Mapping):
        return TerminalActionRefusal(
            reason="invalid_route_projection",
            action=action,
        )

    routed_payload = projected.value
    if not all(isinstance(key, str) for key in routed_payload):
        return TerminalActionRefusal(
            reason="invalid_route_projection",
            action=action,
        )

    return _accepted_route_resolution(
        transition_input=transition_input,
        context=context,
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        artifact_schema=artifact_schema,
        artifact_payload=artifact_payload,
        routed_payload=routed_payload,
        target_stage_kind_id=target_stage_kind_id,
        target_graph_node_id=target_graph_node_id,
        emitted_queue_family_id=emitted_queue_family_id,
        runner_binding_id=runner_binding_id,
        artifact_work_item_id=run.work_item_id,
    )


def _resolve_projected_route_action(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    observation_payload: Mapping[str, object] | None,
) -> TerminalActionResult:
    target_stage_kind_id = action.target_stage_kind_id
    target_graph_node_id = action.target_graph_node_id
    emitted_queue_family_id = action.emitted_queue_family_id
    artifact_schema_id = action.artifact_schema_id
    runner_binding_id = action.runner_binding_id
    payload_projection = action.payload_projection
    target_fields = _route_target_fields_or_refusal(
        action=action,
        observation_payload=observation_payload,
    )
    if isinstance(target_fields, TerminalActionRefusal):
        return target_fields
    (
        target_stage_kind_id,
        target_graph_node_id,
        emitted_queue_family_id,
        runner_binding_id,
    ) = target_fields
    if (
        artifact_schema_id is None or payload_projection is None
    ):
        return TerminalActionRefusal(
            reason="unsupported_terminal_route",
            action=action,
        )

    if not route_contract_supported(
        selected_plan,
        source_stage_kind_id=str(run.stage_kind_id),
        target_stage_kind_id=str(target_stage_kind_id),
        emitted_queue_family_id=str(emitted_queue_family_id),
        artifact_schema_id=str(artifact_schema_id),
        runner_binding_id=str(runner_binding_id),
    ):
        return TerminalActionRefusal(
            reason="unsupported_terminal_route",
            action=action,
        )

    artifact_schema = artifact_schema_for(selected_plan, str(artifact_schema_id))
    if artifact_schema is None:
        return TerminalActionRefusal(
            reason="unsupported_terminal_route",
            action=action,
        )
    artifact_candidate = transition_input.payload.get(_ARTIFACT_FIELD)
    if not isinstance(artifact_candidate, Mapping):
        return TerminalActionRefusal(
            reason="invalid_route_projection",
            action=action,
        )

    projected = evaluate_projection(
        payload_projection,
        projection_context_for_run(
            work_item=work_item,
            run=run,
            observation_payload=(
                {} if observation_payload is None else observation_payload
            ),
            artifact_payload=artifact_candidate,
        ),
    )
    if not projected.accepted or not isinstance(projected.value, Mapping):
        return TerminalActionRefusal(
            reason="invalid_route_projection",
            action=action,
        )

    routed_payload = projected.value
    if not all(isinstance(key, str) for key in routed_payload):
        return TerminalActionRefusal(
            reason="invalid_route_projection",
            action=action,
        )

    validation = validate_schema(artifact_schema.schema, routed_payload)
    if not validation.accepted:
        return TerminalActionRefusal(
            reason="invalid_artifact_payload",
            action=action,
        )

    return _accepted_route_resolution(
        transition_input=transition_input,
        context=context,
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        artifact_schema=artifact_schema,
        artifact_payload=routed_payload,
        routed_payload=routed_payload,
        target_stage_kind_id=target_stage_kind_id,
        target_graph_node_id=target_graph_node_id,
        emitted_queue_family_id=emitted_queue_family_id,
        runner_binding_id=runner_binding_id,
        artifact_work_item_id=context.work_item_id,
    )


RouteTargetFields = tuple[StageKindId, str, QueueFamilyId, RunnerBindingId]


def _route_target_fields_or_refusal(
    *,
    action: TerminalActionDeclaration,
    observation_payload: Mapping[str, object] | None,
) -> RouteTargetFields | TerminalActionRefusal:
    target_stage_kind_id = action.target_stage_kind_id
    target_graph_node_id = action.target_graph_node_id
    emitted_queue_family_id = action.emitted_queue_family_id
    runner_binding_id = action.runner_binding_id
    if (
        target_stage_kind_id is None
        or target_graph_node_id is None
        or emitted_queue_family_id is None
        or runner_binding_id is None
    ):
        return TerminalActionRefusal(
            reason="unsupported_terminal_route",
            action=action,
        )

    selector = action.dynamic_target_selector
    if selector is None:
        return (
            target_stage_kind_id,
            target_graph_node_id,
            emitted_queue_family_id,
            runner_binding_id,
        )
    if not isinstance(selector, Mapping):
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )
    if selector.get("kind") != "observation_payload_route_target":
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )
    raw_field_names = selector.get("field_names")
    if not isinstance(raw_field_names, tuple) or not raw_field_names:
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )
    field_names: list[str] = []
    for raw_field_name in raw_field_names:
        if not _non_blank_text(raw_field_name):
            return TerminalActionRefusal(
                reason="invalid_dynamic_route_target",
                action=action,
            )
        field_names.append(cast(str, raw_field_name))

    requested_targets: list[str] = []
    if observation_payload is None:
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )
    for field_name in field_names:
        raw_value = observation_payload.get(field_name)
        if raw_value is None or raw_value == "":
            continue
        if not _non_blank_text(raw_value):
            return TerminalActionRefusal(
                reason="invalid_dynamic_route_target",
                action=action,
            )
        requested_targets.append(cast(str, raw_value))
    if not requested_targets:
        return (
            target_stage_kind_id,
            target_graph_node_id,
            emitted_queue_family_id,
            runner_binding_id,
        )
    if len(set(requested_targets)) != 1:
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )

    targets = selector.get("targets")
    if not isinstance(targets, Mapping):
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )
    raw_disallowed_targets = selector.get("disallowed_targets", ())
    if not isinstance(raw_disallowed_targets, tuple) or any(
        not _non_blank_text(target_name) for target_name in raw_disallowed_targets
    ):
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )
    disallowed_targets = frozenset(
        cast(str, target_name) for target_name in raw_disallowed_targets
    )
    if requested_targets[0] in disallowed_targets:
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )
    target = targets.get(requested_targets[0])
    if not isinstance(target, Mapping):
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )
    return _target_fields_from_selector_record(
        action=action,
        target=cast(Mapping[object, object], target),
    )


def _target_fields_from_selector_record(
    *,
    action: TerminalActionDeclaration,
    target: Mapping[object, object],
) -> RouteTargetFields | TerminalActionRefusal:
    raw_stage_id = target.get("target_stage_kind_id")
    raw_graph_node_id = target.get("target_graph_node_id")
    raw_queue_id = target.get("emitted_queue_family_id")
    raw_runner_binding_id = target.get("runner_binding_id")
    if not (
        _non_blank_text(raw_stage_id)
        and _non_blank_text(raw_graph_node_id)
        and _non_blank_text(raw_queue_id)
        and _non_blank_text(raw_runner_binding_id)
    ):
        return TerminalActionRefusal(
            reason="invalid_dynamic_route_target",
            action=action,
        )
    return (
        StageKindId(cast(str, raw_stage_id)),
        cast(str, raw_graph_node_id),
        QueueFamilyId(cast(str, raw_queue_id)),
        RunnerBindingId(cast(str, raw_runner_binding_id)),
    )


def _non_blank_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _accepted_route_resolution(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    artifact_schema: ArtifactSchemaDeclaration,
    artifact_payload: Mapping[str, object],
    routed_payload: Mapping[str, object],
    target_stage_kind_id: StageKindId,
    target_graph_node_id: str,
    emitted_queue_family_id: QueueFamilyId,
    runner_binding_id: RunnerBindingId,
    artifact_work_item_id: str,
) -> TerminalActionResolution:
    target_work_item_ref = WorkItemRef(
        work_item_id=context.work_item_id,
        plan_ref=run.run_ref.plan_ref,
        generation=0,
    )
    target_work_item = WorkItem(
        ref=target_work_item_ref,
        queue_family_id=emitted_queue_family_id,
        payload=cast(Mapping[str, AuthorityValue], routed_payload),
        lineage_id=work_item.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    target_activation = Activation(
        activation_id=context.activation_id,
        work_item_id=target_work_item_ref.work_item_id,
        lineage_id=work_item.lineage_id,
        plan_ref=run.run_ref.plan_ref,
        queue_family_id=emitted_queue_family_id,
        graph_node_id=target_graph_node_id,
        stage_kind_id=target_stage_kind_id,
        runner_binding_id=runner_binding_id,
        generation=0,
        created_by_input_id=transition_input.input_id,
    )
    observation_record = RunnerObservationRecord(
        observation_id=f"{context.transition_id}:observation",
        run_id=run.run_ref.run_id,
        payload=transition_input.payload,
        created_by_input_id=transition_input.input_id,
        observed_at=transition_input.observed_at,
    )
    artifact_record = ArtifactRecord(
        artifact_id=f"{context.transition_id}:artifact",
        work_item_id=artifact_work_item_id,
        schema_id=artifact_schema.id,
        payload=cast(Mapping[str, AuthorityValue], artifact_payload),
        created_by_input_id=transition_input.input_id,
        source_run_id=run.run_ref.run_id,
        source_action_id=action.id,
        source_stage_kind_id=run.stage_kind_id,
        source_graph_node_id=activation.graph_node_id,
        payload_digest=artifact_payload_digest(artifact_payload),
        transition_id=context.transition_id,
    )
    route_record = ActivationRouteRecord(
        record_id=f"{context.transition_id}:route",
        action_id=action.id,
        source_run_id=run.run_ref.run_id,
        source_work_item_id=run.work_item_id,
        target_work_item_id=target_work_item_ref.work_item_id,
        target_activation_id=target_activation.activation_id,
        created_by_input_id=transition_input.input_id,
    )
    return _accepted_resolution(
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        mutations=(
            RecordRunnerObservation(
                record_id=observation_record.observation_id,
                observation=observation_record,
            ),
            CreateWorkItem(target_work_item),
            RecordArtifact(
                record_id=artifact_record.artifact_id,
                artifact=artifact_record,
            ),
            CreateActivation(target_activation),
            RouteActivation(record_id=route_record.record_id, route=route_record),
        ),
    )


def _declared_artifact_mutations_or_refusal(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    action: TerminalActionDeclaration,
    work_item_id: str,
    run: RunRecord,
    activation: Activation,
) -> tuple[RecordArtifact, ...] | TerminalActionRefusal:
    artifact_schema_id = action.artifact_schema_id
    if artifact_schema_id is None:
        return ()

    artifact_schema = artifact_schema_for(selected_plan, str(artifact_schema_id))
    raw_artifact_payload = transition_input.payload.get(_ARTIFACT_FIELD)
    if artifact_schema is None or not isinstance(raw_artifact_payload, Mapping):
        return TerminalActionRefusal(
            reason="invalid_artifact_payload",
            action=action,
        )
    source_stage = stage_kind_for(selected_plan, str(action.stage_kind_id))
    if (
        source_stage is None
        or artifact_schema.id not in source_stage.artifact_schema_ids
    ):
        return TerminalActionRefusal(
            reason="unsupported_artifact_schema",
            action=action,
        )

    validation = validate_schema(artifact_schema.schema, raw_artifact_payload)
    if not validation.accepted:
        return TerminalActionRefusal(
            reason="invalid_artifact_payload",
            action=action,
        )
    if not _artifact_payload_matches_plan_ref(
        raw_artifact_payload,
        run.run_ref.plan_ref,
    ):
        return TerminalActionRefusal(
            reason="invalid_artifact_payload",
            action=action,
        )

    artifact_record = ArtifactRecord(
        artifact_id=f"{context.transition_id}:artifact",
        work_item_id=work_item_id,
        schema_id=artifact_schema.id,
        payload=raw_artifact_payload,
        created_by_input_id=transition_input.input_id,
        source_run_id=run.run_ref.run_id,
        source_action_id=action.id,
        source_stage_kind_id=run.stage_kind_id,
        source_graph_node_id=activation.graph_node_id,
        payload_digest=artifact_payload_digest(raw_artifact_payload),
        transition_id=context.transition_id,
    )
    return (
        RecordArtifact(
            record_id=artifact_record.artifact_id,
            artifact=artifact_record,
        ),
    )


def _artifact_payload_matches_plan_ref(
    payload: Mapping[str, object],
    plan_ref: PlanRef,
) -> bool:
    selected_plan_id = payload.get("selected_plan_id")
    if selected_plan_id is not None and selected_plan_id != plan_ref.plan_id:
        return False
    selected_plan_fingerprint = payload.get("selected_plan_fingerprint")
    return (
        selected_plan_fingerprint is None
        or selected_plan_fingerprint == plan_ref.authority_fingerprint
    )


def _resolve_close_action(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    observation_payload: Mapping[str, object] | None,
) -> TerminalActionResult:
    artifact_mutations = _declared_artifact_mutations_or_refusal(
        transition_input=transition_input,
        context=context,
        selected_plan=selected_plan,
        action=action,
        work_item_id=run.work_item_id,
        run=run,
        activation=activation,
    )
    if isinstance(artifact_mutations, TerminalActionRefusal):
        return artifact_mutations
    effect_proposal_mutations = _effect_proposal_mutations_or_refusal(
        transition_input=transition_input,
        context=context,
        selected_plan=selected_plan,
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        artifact_mutations=artifact_mutations,
    )
    if isinstance(effect_proposal_mutations, TerminalActionRefusal):
        return effect_proposal_mutations
    observation_record = RunnerObservationRecord(
        observation_id=f"{context.transition_id}:observation",
        run_id=run.run_ref.run_id,
        payload=transition_input.payload,
        created_by_input_id=transition_input.input_id,
        observed_at=transition_input.observed_at,
    )
    close_record = ClosedWorkItemRecord(
        record_id=f"{context.transition_id}:close",
        work_item_id=run.work_item_id,
        source_run_id=run.run_ref.run_id,
        action_id=action.id,
        created_by_input_id=transition_input.input_id,
    )
    return _accepted_resolution(
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        mutations=(
            RecordRunnerObservation(
                record_id=observation_record.observation_id,
                observation=observation_record,
            ),
            *artifact_mutations,
            *effect_proposal_mutations,
            CloseWorkItem(record_id=close_record.record_id, record=close_record),
        ),
    )


def _effect_proposal_mutations_or_refusal(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    artifact_mutations: tuple[RecordArtifact, ...],
) -> tuple[RecordEffectProposal, ...] | TerminalActionRefusal:
    declarations = tuple(
        declaration
        for declaration in selected_plan.effect_declarations
        if declaration.terminal_action_id == action.id
    )
    if not declarations:
        return ()
    if len(declarations) != 1:
        return TerminalActionRefusal(
            reason="ambiguous_effect_declaration",
            action=action,
        )
    declaration = declarations[0]
    if declaration.real_side_effects_allowed:
        return TerminalActionRefusal(
            reason="effect_real_side_effects_unsupported",
            action=action,
        )
    if len(artifact_mutations) != 1 or artifact_mutations[0].artifact is None:
        return TerminalActionRefusal(
            reason="effect_artifact_required",
            action=action,
        )
    artifact = artifact_mutations[0].artifact
    if (
        declaration.artifact_schema_id != artifact.schema_id
        or declaration.terminal_action_id != action.id
    ):
        return TerminalActionRefusal(
            reason="effect_declaration_mismatch",
            action=action,
        )
    target_skill_id = _optional_payload_text(artifact.payload, "target_skill_id")
    target_path_ref = _optional_payload_text(artifact.payload, "installed_path")
    proposal = EffectProposalRecord(
        effect_id=f"{context.transition_id}:effect",
        dedupe_key=f"{declaration.effect_declaration_id}:{artifact.artifact_id}",
        effect_declaration_id=declaration.effect_declaration_id,
        selected_plan_ref=run.run_ref.plan_ref,
        selected_plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        terminal_action_id=action.id,
        artifact_id=artifact.artifact_id,
        artifact_schema_id=artifact.schema_id,
        artifact_payload_digest=artifact.payload_digest,
        source_run_id=run.run_ref.run_id,
        source_action_id=action.id,
        source_input_id=transition_input.input_id,
        source_work_item_id=run.work_item_id,
        source_activation_id=run.activation_id,
        source_graph_node_id=activation.graph_node_id,
        source_stage_kind_id=run.stage_kind_id,
        source_runner_binding_id=run.runner_binding_id,
        source_queue_family_id=activation.queue_family_id,
        lineage_id=work_item.lineage_id,
        provider_ref=declaration.provider_ref,
        capability_policy_ref=declaration.capability_policy_ref,
        target_ref_kind=declaration.target_ref_kind,
        target_ref_schema=declaration.target_ref_schema,
        target_skill_id=target_skill_id,
        target_path_ref=target_path_ref,
        status="pending",
        created_input_id=transition_input.input_id,
        created_transition_id=context.transition_id,
    )
    return (RecordEffectProposal(record_id=proposal.effect_id, record=proposal),)


def _optional_payload_text(
    payload: Mapping[str, object],
    key: str,
) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _resolve_pause_quarantine_action(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    observation_payload: Mapping[str, object] | None,
) -> TerminalActionResult:
    artifact_mutations = _declared_artifact_mutations_or_refusal(
        transition_input=transition_input,
        context=context,
        selected_plan=selected_plan,
        action=action,
        work_item_id=run.work_item_id,
        run=run,
        activation=activation,
    )
    if isinstance(artifact_mutations, TerminalActionRefusal):
        return artifact_mutations
    observation_record = RunnerObservationRecord(
        observation_id=f"{context.transition_id}:observation",
        run_id=run.run_ref.run_id,
        payload=transition_input.payload,
        created_by_input_id=transition_input.input_id,
        observed_at=transition_input.observed_at,
    )
    quarantine_record = QuarantineRecord(
        record_id=f"{context.transition_id}:quarantine",
        work_item_id=run.work_item_id,
        source_run_id=run.run_ref.run_id,
        action_id=action.id,
        created_by_input_id=transition_input.input_id,
    )
    pause_record = PauseRecord(
        record_id=f"{context.transition_id}:pause",
        source_run_id=run.run_ref.run_id,
        work_item_id=run.work_item_id,
        action_id=action.id,
        created_by_input_id=transition_input.input_id,
    )
    return _accepted_resolution(
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        mutations=(
            RecordRunnerObservation(
                record_id=observation_record.observation_id,
                observation=observation_record,
            ),
            *artifact_mutations,
            SetQuarantine(
                record_id=quarantine_record.record_id,
                record=quarantine_record,
            ),
            SetPause(record_id=pause_record.record_id, record=pause_record),
        ),
    )


def _resolve_recovery_route_action(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    policy_override: RecoveryPolicyDeclaration | None = None,
) -> TerminalActionResult:
    target_stage_kind_id = action.target_stage_kind_id
    target_graph_node_id = action.target_graph_node_id
    runner_binding_id = action.runner_binding_id
    if (
        target_stage_kind_id is None
        or target_graph_node_id is None
        or runner_binding_id is None
    ):
        return TerminalActionRefusal(
            reason="unsupported_terminal_recovery_route",
            action=action,
        )

    target_stage = stage_kind_for(selected_plan, str(target_stage_kind_id))
    runner_binding = runner_binding_for(selected_plan, str(runner_binding_id))
    known_stage = _known_graph_node_stage_owner(selected_plan).get(
        target_graph_node_id
    )
    if (
        target_stage is None
        or runner_binding is None
        or str(target_stage.runner_binding_id) != str(runner_binding_id)
        or str(target_stage_kind_id)
        not in {str(stage_id) for stage_id in runner_binding.stage_kind_ids}
        or (known_stage is not None and known_stage != target_stage_kind_id)
    ):
        return TerminalActionRefusal(
            reason="unsupported_terminal_recovery_route",
            action=action,
        )

    policy = policy_override or _source_recovery_policy_for_action(
        selected_plan,
        action.id,
    )
    if policy is None:
        return TerminalActionRefusal(
            reason="unsupported_terminal_recovery_route",
            action=action,
        )
    if work_item.lineage_id is None:
        return TerminalActionRefusal(reason="lineage_required", action=action)
    wait_state = wait_state_for_policy(selected_plan, str(policy.id))
    if wait_state is None:
        return TerminalActionRefusal(
            reason="unsupported_terminal_recovery_route",
            action=action,
        )

    existing_attempt = _recovery_attempt_for(
        state=state,
        plan_ref=run.run_ref.plan_ref,
        policy_id=policy.id,
        lineage_id=work_item.lineage_id,
    )
    attempt_count = (
        1
        if existing_attempt is None or existing_attempt.phase == "resolved"
        else existing_attempt.attempt_count + 1
    )
    phase = _recovery_attempt_phase(policy, attempt_count)
    should_quarantine = attempt_count >= policy.quarantine_threshold_attempt
    should_schedule = phase == "active_recovery"
    observation_record = RunnerObservationRecord(
        observation_id=f"{context.transition_id}:observation",
        run_id=run.run_ref.run_id,
        payload=transition_input.payload,
        created_by_input_id=transition_input.input_id,
        observed_at=transition_input.observed_at,
    )
    recovery_activation_id = context.activation_id if should_schedule else None
    attempt = _recorded_recovery_attempt(
        existing_attempt=existing_attempt,
        policy=policy,
        plan_ref=run.run_ref.plan_ref,
        attempt_count=attempt_count,
        phase=phase,
        source_run=run,
        source_activation=activation,
        source_work_item=work_item,
        recovery_action_id=action.id,
        latest_recovery_activation_id=recovery_activation_id,
        input_id=transition_input.input_id,
    )
    attempt_mutation = RecordRecoveryAttempt(
        record_id=attempt.record_id,
        attempt=attempt,
    )
    if not should_schedule:
        if should_quarantine:
            if not policy.quarantine_action_ids:
                return TerminalActionRefusal(
                    reason="unsupported_terminal_recovery_route",
                    action=action,
                )
            if (
                active_lineage_quarantine_for(
                    state,
                    work_item.lineage_id,
                    plan_ref=run.run_ref.plan_ref,
                    policy_id=str(policy.id),
                )
                is not None
            ):
                return TerminalActionRefusal(
                    reason="lineage_quarantine_exists",
                    action=action,
                )
            quarantine_action_id = policy.quarantine_action_ids[0]
            quarantine = LineageQuarantineRecord(
                quarantine_id=_lineage_quarantine_record_id(
                    plan_ref=run.run_ref.plan_ref,
                    policy_id=policy.id,
                    lineage_id=work_item.lineage_id,
                    attempt_record_id=attempt.record_id,
                ),
                policy_id=policy.id,
                lineage_id=work_item.lineage_id,
                selected_plan_ref=attempt.plan_ref,
                selected_plan_fingerprint=attempt.plan_ref.authority_fingerprint,
                recovery_attempt_record_id=attempt.record_id,
                original_source_run_id=attempt.source_run_id,
                original_source_work_item_id=attempt.source_work_item_id,
                original_source_activation_id=attempt.source_activation_id,
                emitting_recovery_activation_id=activation.activation_id,
                emitting_recovery_run_id=run.run_ref.run_id,
                action_id=quarantine_action_id,
                attempt_count=attempt.attempt_count,
                created_input_id=transition_input.input_id,
                actor_kind="runtime",
                status="active",
                superseded_input_id=None,
            )
            return _accepted_resolution(
                run=run,
                activation=activation,
                work_item=work_item,
                action=action,
                mutations=(
                    RecordRunnerObservation(
                        record_id=observation_record.observation_id,
                        observation=observation_record,
                    ),
                    attempt_mutation,
                    RecordLineageQuarantine(
                        record_id=quarantine.quarantine_id,
                        record=quarantine,
                    ),
                ),
                expected_lineage_quarantine_absent=(
                    lineage_quarantine_scope_key(
                        run.run_ref.plan_ref,
                        work_item.lineage_id,
                    ),
                ),
            )
        if transition_input.observed_at is None:
            return TerminalActionRefusal(
                reason="missing_observed_at",
                action=action,
            )
        if (
            transition_input.observed_at
            > DURABLE_INT64_MAX - wait_state.duration_seconds
        ):
            return TerminalActionRefusal(
                reason="observed_at_out_of_range",
                action=action,
            )
        wait = _cooldown_wait_record(
            transition_input=transition_input,
            policy=policy,
            plan_ref=run.run_ref.plan_ref,
            attempt=attempt,
            source_run=run,
            source_activation=activation,
            source_work_item=work_item,
            target_stage_kind_id=target_stage_kind_id,
            target_graph_node_id=target_graph_node_id,
            target_runner_binding_id=runner_binding_id,
            duration_seconds=wait_state.duration_seconds,
        )
        return _accepted_resolution(
            run=run,
            activation=activation,
            work_item=work_item,
            action=action,
            mutations=(
                RecordRunnerObservation(
                    record_id=observation_record.observation_id,
                    observation=observation_record,
                ),
                attempt_mutation,
                RecordCooldownWait(record_id=wait.wait_id, wait=wait),
            ),
        )

    target_activation = Activation(
        activation_id=context.activation_id,
        work_item_id=work_item.ref.work_item_id,
        lineage_id=work_item.lineage_id,
        plan_ref=run.run_ref.plan_ref,
        queue_family_id=work_item.queue_family_id,
        graph_node_id=target_graph_node_id,
        stage_kind_id=target_stage_kind_id,
        runner_binding_id=runner_binding_id,
        generation=work_item.ref.generation,
        created_by_input_id=transition_input.input_id,
    )
    route_record = ActivationRouteRecord(
        record_id=f"{context.transition_id}:route",
        action_id=action.id,
        source_run_id=run.run_ref.run_id,
        source_work_item_id=run.work_item_id,
        target_work_item_id=work_item.ref.work_item_id,
        target_activation_id=target_activation.activation_id,
        created_by_input_id=transition_input.input_id,
    )
    return _accepted_resolution(
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        mutations=(
            RecordRunnerObservation(
                record_id=observation_record.observation_id,
                observation=observation_record,
            ),
            CreateActivation(target_activation),
            RouteActivation(record_id=route_record.record_id, route=route_record),
            attempt_mutation,
        ),
    )


def _known_graph_node_stage_owner(
    selected_plan: SelectedCompiledPlan,
) -> Mapping[str, StageKindId]:
    owners: dict[str, StageKindId] = {}
    for route in selected_plan.external_enqueue_routes:
        owners.setdefault(route.graph_node_id, route.stage_kind_id)
    for action in selected_plan.terminal_actions:
        if action.action_kind == _ACTION_KIND_RECOVERY_ROUTE:
            continue
        if (
            action.target_graph_node_id is not None
            and action.target_stage_kind_id is not None
        ):
            owners.setdefault(action.target_graph_node_id, action.target_stage_kind_id)
        selector = action.dynamic_target_selector
        if not isinstance(selector, Mapping):
            continue
        targets = selector.get("targets")
        if not isinstance(targets, Mapping):
            continue
        for target in targets.values():
            if not isinstance(target, Mapping):
                continue
            raw_stage_id = target.get("target_stage_kind_id")
            raw_graph_node_id = target.get("target_graph_node_id")
            if _non_blank_text(raw_stage_id) and _non_blank_text(raw_graph_node_id):
                owners.setdefault(
                    cast(str, raw_graph_node_id),
                    StageKindId(cast(str, raw_stage_id)),
                )
    return owners


def _resolve_return_to_recorded_source_action(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
) -> TerminalActionResult:
    resolved = _return_policy_and_attempt_for_action(
        selected_plan=selected_plan,
        state=state,
        plan_ref=run.run_ref.plan_ref,
        action_id=action.id,
        lineage_id=work_item.lineage_id,
        run_id=run.run_ref.run_id,
    )
    if resolved is None:
        return TerminalActionRefusal(
            reason="unsupported_runtime_terminal_action",
            action=action,
        )
    policy, attempt = resolved
    if attempt.phase not in set(policy.return_allowed_phases):
        return TerminalActionRefusal(
            reason="unsupported_runtime_terminal_action",
            action=action,
        )
    source_work_item = state.work_items.get(attempt.source_work_item_id)
    if source_work_item is None or source_work_item.ref.plan_ref != attempt.plan_ref:
        return TerminalActionRefusal(
            reason="unsupported_runtime_terminal_action",
            action=action,
        )
    artifact_mutations = _declared_artifact_mutations_or_refusal(
        transition_input=transition_input,
        context=context,
        selected_plan=selected_plan,
        action=action,
        work_item_id=run.work_item_id,
        run=run,
        activation=activation,
    )
    if isinstance(artifact_mutations, TerminalActionRefusal):
        return artifact_mutations
    target_activation = Activation(
        activation_id=context.activation_id,
        work_item_id=attempt.source_work_item_id,
        lineage_id=attempt.lineage_id,
        plan_ref=attempt.plan_ref,
        queue_family_id=attempt.source_queue_family_id,
        graph_node_id=attempt.source_graph_node_id,
        stage_kind_id=attempt.source_stage_kind_id,
        runner_binding_id=attempt.source_runner_binding_id,
        generation=source_work_item.ref.generation,
        created_by_input_id=transition_input.input_id,
    )
    observation_record = RunnerObservationRecord(
        observation_id=f"{context.transition_id}:observation",
        run_id=run.run_ref.run_id,
        payload=transition_input.payload,
        created_by_input_id=transition_input.input_id,
        observed_at=transition_input.observed_at,
    )
    route_record = ActivationRouteRecord(
        record_id=f"{context.transition_id}:route",
        action_id=action.id,
        source_run_id=run.run_ref.run_id,
        source_work_item_id=attempt.source_work_item_id,
        target_work_item_id=attempt.source_work_item_id,
        target_activation_id=target_activation.activation_id,
        created_by_input_id=transition_input.input_id,
    )
    updated_attempt = replace(
        attempt,
        latest_return_action_id=action.id,
        updated_by_input_id=transition_input.input_id,
    )
    return _accepted_resolution(
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        mutations=(
            RecordRunnerObservation(
                record_id=observation_record.observation_id,
                observation=observation_record,
            ),
            *artifact_mutations,
            CreateActivation(target_activation),
            RouteActivation(record_id=route_record.record_id, route=route_record),
            RecordRecoveryAttempt(
                record_id=updated_attempt.record_id,
                attempt=updated_attempt,
            ),
        ),
    )


def _resolve_quarantine_lineage_action(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
) -> TerminalActionResult:
    policy = _quarantine_policy_for_action(selected_plan, action.id)
    if policy is None:
        return TerminalActionRefusal(
            reason="unsupported_runtime_terminal_action",
            action=action,
        )
    attempt = _recovery_attempt_for(
        state=state,
        plan_ref=run.run_ref.plan_ref,
        policy_id=policy.id,
        lineage_id=work_item.lineage_id,
    )
    if attempt is None or attempt.phase not in {
        "active_recovery",
        "quarantine_eligible",
    }:
        return TerminalActionRefusal(
            reason="unsupported_runtime_terminal_action",
            action=action,
        )
    if (
        attempt.latest_recovery_activation_id != activation.activation_id
        or attempt.latest_recovery_run_id != run.run_ref.run_id
        or attempt.plan_ref != run.run_ref.plan_ref
        or attempt.lineage_id != work_item.lineage_id
    ):
        return TerminalActionRefusal(
            reason="unsupported_runtime_terminal_action",
            action=action,
        )
    if (
        active_lineage_quarantine_for(
            state,
            work_item.lineage_id,
            plan_ref=run.run_ref.plan_ref,
            policy_id=str(policy.id),
        )
        is not None
    ):
        return TerminalActionRefusal(
            reason="lineage_quarantine_exists",
            action=action,
        )

    observation_record = RunnerObservationRecord(
        observation_id=f"{context.transition_id}:observation",
        run_id=run.run_ref.run_id,
        payload=transition_input.payload,
        created_by_input_id=transition_input.input_id,
        observed_at=transition_input.observed_at,
    )
    artifact_mutations = _declared_artifact_mutations_or_refusal(
        transition_input=transition_input,
        context=context,
        selected_plan=selected_plan,
        action=action,
        work_item_id=run.work_item_id,
        run=run,
        activation=activation,
    )
    if isinstance(artifact_mutations, TerminalActionRefusal):
        return artifact_mutations
    quarantine = LineageQuarantineRecord(
        quarantine_id=_lineage_quarantine_record_id(
            plan_ref=run.run_ref.plan_ref,
            policy_id=policy.id,
            lineage_id=work_item.lineage_id,
            attempt_record_id=attempt.record_id,
        ),
        policy_id=policy.id,
        lineage_id=work_item.lineage_id,
        selected_plan_ref=attempt.plan_ref,
        selected_plan_fingerprint=attempt.plan_ref.authority_fingerprint,
        recovery_attempt_record_id=attempt.record_id,
        original_source_run_id=attempt.source_run_id,
        original_source_work_item_id=attempt.source_work_item_id,
        original_source_activation_id=attempt.source_activation_id,
        emitting_recovery_activation_id=activation.activation_id,
        emitting_recovery_run_id=run.run_ref.run_id,
        action_id=action.id,
        attempt_count=attempt.attempt_count,
        created_input_id=transition_input.input_id,
        actor_kind="runtime",
        status="active",
        superseded_input_id=None,
    )
    eligible_attempt = (
        replace(
            attempt,
            phase="quarantine_eligible",
            updated_by_input_id=transition_input.input_id,
        )
        if attempt.phase == "active_recovery"
        else attempt
    )
    return _accepted_resolution(
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        mutations=(
            RecordRunnerObservation(
                record_id=observation_record.observation_id,
                observation=observation_record,
            ),
            *artifact_mutations,
            *(
                (
                    RecordRecoveryAttempt(
                        record_id=eligible_attempt.record_id,
                        attempt=eligible_attempt,
                    ),
                )
                if eligible_attempt is not attempt
                else ()
            ),
            RecordLineageQuarantine(
                record_id=quarantine.quarantine_id,
                record=quarantine,
            ),
        ),
        expected_lineage_quarantine_absent=(
            lineage_quarantine_scope_key(
                run.run_ref.plan_ref,
                work_item.lineage_id,
            ),
        ),
    )


def _resolve_operator_wait_action(
    *,
    transition_input: RunnerResultObserved,
    context: TransitionContext,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
) -> TerminalActionResult:
    operator_wait = operator_wait_for_action(selected_plan, str(action.id))
    if operator_wait is None:
        return TerminalActionRefusal(
            reason="unsupported_runtime_terminal_action",
            action=action,
        )
    if work_item.lineage_id is None:
        return TerminalActionRefusal(reason="lineage_required", action=action)
    if operator_wait.wait_scope != "lineage":
        return TerminalActionRefusal(
            reason="unsupported_operator_wait",
            action=action,
        )
    if (
        active_operator_wait_for(
            state,
            work_item.lineage_id,
            plan_ref=run.run_ref.plan_ref,
        )
        is not None
    ):
        return TerminalActionRefusal(reason="operator_wait_exists", action=action)

    observation_record = RunnerObservationRecord(
        observation_id=f"{context.transition_id}:observation",
        run_id=run.run_ref.run_id,
        payload=transition_input.payload,
        created_by_input_id=transition_input.input_id,
        observed_at=transition_input.observed_at,
    )
    artifact_mutations = _declared_artifact_mutations_or_refusal(
        transition_input=transition_input,
        context=context,
        selected_plan=selected_plan,
        action=action,
        work_item_id=run.work_item_id,
        run=run,
        activation=activation,
    )
    if isinstance(artifact_mutations, TerminalActionRefusal):
        return artifact_mutations
    artifact_id = artifact_mutations[0].record_id if artifact_mutations else None
    wait_id = _operator_wait_record_id(
        authority_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        operator_wait_id=str(operator_wait.id),
        lineage_id=work_item.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    wait_record = OperatorWaitRecord(
        wait_id=wait_id,
        operator_wait_id=operator_wait.id,
        source_action_id=action.id,
        lineage_id=work_item.lineage_id,
        selected_plan_ref=run.run_ref.plan_ref,
        selected_plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        source_work_item_id=run.work_item_id,
        source_activation_id=activation.activation_id,
        source_run_id=run.run_ref.run_id,
        source_stage_kind_id=run.stage_kind_id,
        source_graph_node_id=activation.graph_node_id,
        source_queue_family_id=work_item.queue_family_id,
        source_runner_binding_id=run.runner_binding_id,
        source_artifact_id=artifact_id,
        status="active",
        created_input_id=transition_input.input_id,
        created_input_payload_digest=input_payload_digest(transition_input),
        resolved_input_id=None,
        resolved_input_payload_digest=None,
        actor_id=None,
        actor_kind=None,
        resolution_kind=None,
    )
    close_mutations: tuple[TransitionMutation, ...] = ()
    if operator_wait.source_work_item_behavior == "close_on_create":
        close_record = ClosedWorkItemRecord(
            record_id=f"{context.transition_id}:close",
            work_item_id=run.work_item_id,
            source_run_id=run.run_ref.run_id,
            action_id=action.id,
            created_by_input_id=transition_input.input_id,
            close_kind="terminal_action",
        )
        close_mutations = (
            CloseWorkItem(record_id=close_record.record_id, record=close_record),
        )
    return _accepted_resolution(
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        mutations=(
            RecordRunnerObservation(
                record_id=observation_record.observation_id,
                observation=observation_record,
            ),
            *artifact_mutations,
            *close_mutations,
            RecordOperatorWait(record_id=wait_record.wait_id, record=wait_record),
        ),
        expected_operator_wait_absent=(
            operator_wait_scope_key(run.run_ref.plan_ref, work_item.lineage_id),
        ),
    )


def _source_recovery_policy_for_action(
    selected_plan: SelectedCompiledPlan,
    action_id: ActionId,
) -> RecoveryPolicyDeclaration | None:
    return next(
        (
            policy
            for policy in selected_plan.recovery_policies
            if action_id in policy.source_recovery_action_ids
        ),
        None,
    )


def _threshold_recovery_route_for_increment(
    *,
    selected_plan: SelectedCompiledPlan,
    action: TerminalActionDeclaration,
    counter_record: CounterRecord | None,
) -> ThresholdRecoveryRoute | None:
    if counter_record is None:
        return None
    counter = _counter_by_id(selected_plan, counter_record.counter_id)
    if (
        counter is None
        or action.id != counter.increment_action_id
        or counter_record.value < counter.threshold_count
    ):
        return None
    threshold_action = _terminal_action_by_id(
        selected_plan,
        counter.threshold_action_id,
    )
    if (
        threshold_action is None
        or threshold_action.action_kind != _ACTION_KIND_RECOVERY_ROUTE
    ):
        return None
    for policy in selected_plan.recovery_policies:
        if (
            action.id in policy.source_recovery_action_ids
            and threshold_action.target_stage_kind_id
            == policy.recovery_stage_kind_id
        ):
            return threshold_action, policy
    return None


def _counter_by_id(
    selected_plan: SelectedCompiledPlan,
    counter_id: object,
) -> CounterDeclaration | None:
    return next(
        (
            counter
            for counter in selected_plan.counters
            if counter.id == counter_id
        ),
        None,
    )


def _terminal_action_by_id(
    selected_plan: SelectedCompiledPlan,
    action_id: ActionId,
) -> TerminalActionDeclaration | None:
    return next(
        (
            action
            for action in selected_plan.terminal_actions
            if action.id == action_id
        ),
        None,
    )


def _return_policy_for_action(
    selected_plan: SelectedCompiledPlan,
    action_id: ActionId,
) -> RecoveryPolicyDeclaration | None:
    return next(
        (
            policy
            for policy in selected_plan.recovery_policies
            if action_id in policy.return_action_ids
        ),
        None,
    )


def _return_policy_and_attempt_for_action(
    *,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    plan_ref: PlanRef,
    action_id: ActionId,
    lineage_id: str | None,
    run_id: str,
) -> tuple[RecoveryPolicyDeclaration, RecoveryAttemptRecord] | None:
    if lineage_id is None:
        return None
    for policy in selected_plan.recovery_policies:
        if action_id not in policy.return_action_ids:
            continue
        attempt = _recovery_attempt_for(
            state=state,
            plan_ref=plan_ref,
            policy_id=policy.id,
            lineage_id=lineage_id,
        )
        if attempt is None or attempt.latest_recovery_run_id != run_id:
            continue
        return policy, attempt
    return None


def _quarantine_policy_for_action(
    selected_plan: SelectedCompiledPlan,
    action_id: ActionId,
) -> RecoveryPolicyDeclaration | None:
    return next(
        (
            policy
            for policy in selected_plan.recovery_policies
            if action_id in policy.quarantine_action_ids
        ),
        None,
    )


def _counter_record_or_refusal(
    *,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    run: RunRecord,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    input_id: str,
) -> CounterRecord | TerminalActionRefusal | None:
    counter = counter_for_action(selected_plan, str(action.id))
    if counter is None:
        return None
    if work_item.lineage_id is None:
        return TerminalActionRefusal(reason="lineage_required", action=action)
    record_id = _counter_record_id(
        plan_ref=run.run_ref.plan_ref,
        counter_id=str(counter.id),
        lineage_id=work_item.lineage_id,
    )
    existing = state.counters.get(record_id)
    current_value = existing.value if existing is not None else 0
    next_value = current_value + 1
    if action.id == counter.increment_action_id:
        if next_value >= counter.threshold_count and not (
            _counter_increment_has_derived_recovery_threshold(
                selected_plan=selected_plan,
                counter=counter,
                action=action,
            )
        ):
            return TerminalActionRefusal(
                reason="counter_threshold_requires_escalation",
                action=action,
            )
    elif action.id == counter.threshold_action_id:
        if next_value < counter.threshold_count:
            return TerminalActionRefusal(
                reason="counter_threshold_not_reached",
                action=action,
            )
    else:
        return None
    return CounterRecord(
        record_id=record_id,
        counter_id=counter.id,
        selected_plan_ref=run.run_ref.plan_ref,
        lineage_id=work_item.lineage_id,
        value=next_value,
        updated_by_input_id=input_id,
    )


def _counter_increment_has_derived_recovery_threshold(
    *,
    selected_plan: SelectedCompiledPlan,
    counter: CounterDeclaration,
    action: TerminalActionDeclaration,
) -> bool:
    threshold_action = _terminal_action_by_id(
        selected_plan,
        counter.threshold_action_id,
    )
    if (
        threshold_action is None
        or threshold_action.action_kind != _ACTION_KIND_RECOVERY_ROUTE
    ):
        return False
    return any(
        action.id in policy.source_recovery_action_ids
        and threshold_action.target_stage_kind_id == policy.recovery_stage_kind_id
        for policy in selected_plan.recovery_policies
    )


def _with_counter_record(
    result: TerminalActionResult,
    counter_record: CounterRecord | None,
) -> TerminalActionResult:
    if counter_record is None or not isinstance(result, TerminalActionResolution):
        return result
    return replace(
        result,
        mutations=(
            *result.mutations,
            RecordCounter(record_id=counter_record.record_id, record=counter_record),
        ),
    )


def _recovery_attempt_for(
    *,
    state: RuntimeState,
    plan_ref: PlanRef,
    policy_id: RecoveryPolicyId,
    lineage_id: str | None,
) -> RecoveryAttemptRecord | None:
    if lineage_id is None:
        return None
    return next(
        (
            attempt
            for attempt in state.recovery_attempts.values()
            if attempt.plan_ref == plan_ref
            and attempt.policy_id == policy_id
            and attempt.lineage_id == lineage_id
            and attempt.phase != "resolved"
        ),
        None,
    )


def _recorded_recovery_attempt(
    *,
    existing_attempt: RecoveryAttemptRecord | None,
    policy: RecoveryPolicyDeclaration,
    plan_ref: PlanRef,
    attempt_count: int,
    phase: str,
    source_run: RunRecord,
    source_activation: Activation,
    source_work_item: WorkItem,
    recovery_action_id: ActionId,
    latest_recovery_activation_id: str | None,
    input_id: str,
) -> RecoveryAttemptRecord:
    if source_work_item.lineage_id is None:
        raise ValueError("recovery attempts require lineage")
    record_id = (
        existing_attempt.record_id
        if existing_attempt is not None and existing_attempt.phase != "resolved"
        else _recovery_attempt_record_id(
            plan_ref=plan_ref,
            policy_id=policy.id,
            lineage_id=source_work_item.lineage_id,
            created_by_input_id=input_id,
        )
    )
    created_by_input_id = (
        input_id
        if existing_attempt is None or existing_attempt.phase == "resolved"
        else existing_attempt.created_by_input_id
    )
    return RecoveryAttemptRecord(
        record_id=record_id,
        policy_id=policy.id,
        lineage_id=source_work_item.lineage_id,
        plan_ref=plan_ref,
        attempt_count=attempt_count,
        phase=phase,
        source_run_id=source_run.run_ref.run_id,
        source_work_item_id=source_work_item.ref.work_item_id,
        source_activation_id=source_run.activation_id,
        source_graph_node_id=source_activation.graph_node_id,
        source_stage_kind_id=source_run.stage_kind_id,
        source_runner_binding_id=source_run.runner_binding_id,
        source_queue_family_id=source_work_item.queue_family_id,
        recovery_action_id=recovery_action_id,
        latest_recovery_activation_id=latest_recovery_activation_id,
        latest_recovery_run_id=None,
        latest_return_action_id=None,
        created_by_input_id=created_by_input_id,
        updated_by_input_id=input_id,
    )


def _cooldown_wait_record(
    *,
    transition_input: RunnerResultObserved,
    policy: RecoveryPolicyDeclaration,
    plan_ref: PlanRef,
    attempt: RecoveryAttemptRecord,
    source_run: RunRecord,
    source_activation: Activation,
    source_work_item: WorkItem,
    target_stage_kind_id: StageKindId,
    target_graph_node_id: str,
    target_runner_binding_id: RunnerBindingId,
    duration_seconds: int,
) -> CooldownWaitRecord:
    created_at = transition_input.observed_at
    if created_at is None:
        raise ValueError("cooldown wait creation requires observed_at")
    if source_work_item.lineage_id is None:
        raise ValueError("cooldown wait creation requires lineage")
    wait_id = (
        "cooldown-wait:"
        f"{plan_ref.authority_fingerprint}:"
        f"{policy.id}:"
        f"{source_work_item.lineage_id}:"
        f"{attempt.attempt_count}:"
        f"{transition_input.input_id}"
    )
    return CooldownWaitRecord(
        wait_id=wait_id,
        policy_id=policy.id,
        lineage_id=source_work_item.lineage_id,
        recovery_attempt_record_id=attempt.record_id,
        attempt_count=attempt.attempt_count,
        source_run_id=source_run.run_ref.run_id,
        source_work_item_id=source_work_item.ref.work_item_id,
        source_activation_id=source_activation.activation_id,
        recovery_action_id=attempt.recovery_action_id,
        target_stage_kind_id=target_stage_kind_id,
        target_graph_node_id=target_graph_node_id,
        target_runner_binding_id=target_runner_binding_id,
        plan_ref=plan_ref,
        created_input_id=transition_input.input_id,
        created_at=created_at,
        due_at=created_at + duration_seconds,
        consumed_input_id=None,
        consumed_at=None,
        resulting_recovery_activation_id=None,
    )


def _recovery_attempt_phase(
    policy: RecoveryPolicyDeclaration,
    attempt_count: int,
) -> str:
    if attempt_count >= policy.quarantine_threshold_attempt:
        return "quarantine_eligible"
    if attempt_count > policy.immediate_recovery_limit:
        return "pending_cooldown"
    return "active_recovery"


def _recovery_attempt_record_id(
    *,
    plan_ref: PlanRef,
    policy_id: RecoveryPolicyId,
    lineage_id: str,
    created_by_input_id: str,
) -> str:
    return (
        "recovery-attempt:"
        f"{plan_ref.authority_fingerprint}:"
        f"{policy_id}:"
        f"{lineage_id}:"
        f"{created_by_input_id}"
    )


def _lineage_quarantine_record_id(
    *,
    plan_ref: PlanRef,
    policy_id: RecoveryPolicyId,
    lineage_id: str,
    attempt_record_id: str,
) -> str:
    return (
        "lineage-quarantine:"
        f"{plan_ref.authority_fingerprint}:"
        f"{policy_id}:"
        f"{lineage_id}:"
        f"{attempt_record_id}"
    )


def _counter_record_id(
    *,
    plan_ref: PlanRef,
    counter_id: str,
    lineage_id: str,
) -> str:
    return f"counter:{plan_ref.authority_fingerprint}:{counter_id}:{lineage_id}"


def _with_reset_recovery_attempts(
    *,
    result: TerminalActionResult,
    selected_plan: SelectedCompiledPlan,
    state: RuntimeState,
    run: RunRecord,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    input_id: str,
) -> TerminalActionResult:
    if not isinstance(result, TerminalActionResolution):
        return result
    reset_mutations: list[TransitionMutation] = []
    for policy in selected_plan.recovery_policies:
        if action.id not in policy.reset_trigger_action_ids:
            continue
        attempt = _recovery_attempt_for(
            state=state,
            plan_ref=run.run_ref.plan_ref,
            policy_id=policy.id,
            lineage_id=work_item.lineage_id,
        )
        if attempt is None or attempt.phase == "resolved":
            continue
        resolved = replace(
            attempt,
            phase="resolved",
            updated_by_input_id=input_id,
        )
        reset_mutations.append(
            RecordRecoveryAttempt(record_id=resolved.record_id, attempt=resolved)
        )
    if not reset_mutations:
        return result
    return replace(
        result,
        mutations=(*result.mutations, *reset_mutations),
    )


def _accepted_resolution(
    *,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    action: TerminalActionDeclaration,
    mutations: tuple[TransitionMutation, ...],
    expected_lineage_quarantine_absent: tuple[str, ...] | None = None,
    expected_operator_wait_absent: tuple[str, ...] | None = None,
) -> TerminalActionResolution:
    return TerminalActionResolution(
        mutations=mutations,
        expected_plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        expected_work_item_generations={
            work_item.ref.work_item_id: work_item.ref.generation
        },
        expected_activation_generations={
            activation.activation_id: activation.generation
        },
        expected_run_generations={run.run_ref.run_id: run.run_ref.generation},
        expected_run_fencing_tokens={run.run_ref.run_id: run.run_ref.fencing_token},
        # This prevents accepted duplicate-observation decisions from applying
        # after another decision records the same run's observation.
        expected_run_unobserved=(run.run_ref.run_id,),
        expected_lineage_quarantine_absent=(
            expected_lineage_quarantine_absent
            if expected_lineage_quarantine_absent is not None
            else ()
            if work_item.lineage_id is None
            else (
                lineage_quarantine_scope_key(
                    run.run_ref.plan_ref,
                    work_item.lineage_id,
                ),
            )
        ),
        expected_operator_wait_absent=(
            expected_operator_wait_absent
            if expected_operator_wait_absent is not None
            else ()
            if work_item.lineage_id is None
            else (
                operator_wait_scope_key(
                    run.run_ref.plan_ref,
                    work_item.lineage_id,
                ),
            )
        ),
        expected_work_item_open=(work_item.ref.work_item_id,),
        expected_work_item_plan_refs={
            work_item.ref.work_item_id: work_item.ref.plan_ref
        },
        expected_activation_plan_refs={activation.activation_id: activation.plan_ref},
        event_plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        event_work_item_id=run.work_item_id,
        event_run_id=run.run_ref.run_id,
        event_action_id=action.id,
        event_authority_source=AUTHORITY_SOURCE_TERMINAL_ACTION,
    )


__all__ = (
    "AUTHORITY_SOURCE_TERMINAL_ACTION",
    "SUPPORTED_RUNTIME_TERMINAL_ACTION_KINDS",
    "TerminalActionRefusal",
    "TerminalActionResolution",
    "TerminalActionResult",
    "resolve_terminal_action",
)
