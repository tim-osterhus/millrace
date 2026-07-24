"""Pure authentication policy for durable runner observations and artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from millrace.contracts.compiled_plan import (
    SelectedCompiledPlan,
    TerminalActionDeclaration,
)
from millrace.contracts.runner import (
    RunnerResultEvidence,
    runner_result_evidence_from_payload,
)
from millrace.contracts.schema import validate_schema
from millrace.contracts.state import (
    Activation,
    ArtifactRecord,
    RunnerObservationRecord,
    RunRecord,
    RuntimeState,
    TransitionRecord,
    WorkItem,
)
from millrace.contracts.transition import (
    RunnerResultObserved,
    artifact_payload_digest,
    input_payload_digest,
)
from millrace.kernel.lookups import terminal_action_for, terminal_outcome_for
from millrace.kernel.projection import evaluate_projection, projection_context_for_run


@dataclass(frozen=True, slots=True)
class ObservationPolicyDiagnostic:
    reason_code: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticatedRunnerObservation:
    observation: RunnerObservationRecord
    transition: TransitionRecord
    evidence: RunnerResultEvidence
    action: TerminalActionDeclaration
    run: RunRecord
    work_item: WorkItem
    activation: Activation
    selected_plan: SelectedCompiledPlan


@dataclass(frozen=True, slots=True)
class AuthenticatedArtifactProvenance:
    artifact: ArtifactRecord
    observation: AuthenticatedRunnerObservation


def authenticate_runner_observation(
    state: RuntimeState,
    observation: RunnerObservationRecord,
) -> AuthenticatedRunnerObservation | ObservationPolicyDiagnostic:
    """Authenticate one durable observation against accepted selected authority."""

    input_id = observation.created_by_input_id
    receipt = state.receipts.get(input_id)
    transitions = tuple(
        transition
        for transition in state.transitions
        if transition.input_id == input_id
    )
    if (
        receipt is None
        or receipt.receipt_ref.input_id != input_id
        or not receipt.accepted
        or receipt.refusal_reason is not None
        or len(transitions) != 1
    ):
        return _diagnostic("receipt_authority")
    transition = transitions[0]
    if (
        receipt.transition_id != transition.record_id
        or not transition.accepted
        or transition.input_kind != RunnerResultObserved.input_kind
        or transition.input_family != "workflow_observation"
    ):
        return _diagnostic("transition_authority")

    expected_observation_id = f"{transition.record_id}:observation"
    related_observations = tuple(
        candidate
        for candidate in state.runner_observations.values()
        if candidate.observation_id == expected_observation_id
        or candidate.run_id == observation.run_id
        or candidate.created_by_input_id == input_id
    )
    if (
        len(related_observations) != 1
        or related_observations[0] != observation
        or observation.observation_id != expected_observation_id
    ):
        return _diagnostic("observation_identity")

    reconstructed = RunnerResultObserved(
        input_id,
        run_id=observation.run_id,
        payload=observation.payload,
        observed_at=observation.observed_at,
    )
    if receipt.receipt_ref.input_payload_digest != input_payload_digest(reconstructed):
        return _diagnostic("receipt_authority", "input_payload_digest")
    try:
        evidence = runner_result_evidence_from_payload(observation.payload)
    except (TypeError, ValueError):
        return _diagnostic("evidence_schema")

    source = _selected_source_authority(state, observation, evidence)
    if isinstance(source, ObservationPolicyDiagnostic):
        return source
    run, work_item, activation, selected_plan = source
    outcome = terminal_outcome_for(
        selected_plan,
        str(run.stage_kind_id),
        evidence.marker,
    )
    marker_action = (
        terminal_action_for(
            selected_plan,
            str(run.stage_kind_id),
            str(outcome.id),
        )
        if outcome is not None
        else None
    )
    if marker_action is None:
        return _diagnostic("terminal_action_authority")
    action = _authenticated_audit_action(
        state,
        selected_plan=selected_plan,
        transition=transition,
        input_id=input_id,
        run=run,
        work_item=work_item,
        marker_action=marker_action,
    )
    if action is None:
        return _diagnostic("audit_authority")
    return AuthenticatedRunnerObservation(
        observation=observation,
        transition=transition,
        evidence=evidence,
        action=action,
        run=run,
        work_item=work_item,
        activation=activation,
        selected_plan=selected_plan,
    )


def authenticate_artifact_provenance(
    state: RuntimeState,
    artifact: ArtifactRecord,
) -> AuthenticatedArtifactProvenance | ObservationPolicyDiagnostic:
    """Authenticate an artifact as the exact output of an accepted observation."""

    observations = tuple(
        candidate
        for candidate in state.runner_observations.values()
        if candidate.run_id == artifact.source_run_id
        or candidate.created_by_input_id == artifact.created_by_input_id
    )
    if len(observations) != 1:
        return _diagnostic("observation_identity", "artifact_source")
    authenticated = authenticate_runner_observation(state, observations[0])
    if isinstance(authenticated, ObservationPolicyDiagnostic):
        return authenticated
    if (
        authenticated.transition.record_id != artifact.transition_id
        or authenticated.transition.input_id != artifact.created_by_input_id
        or authenticated.run.run_ref.run_id != artifact.source_run_id
        or authenticated.run.stage_kind_id != artifact.source_stage_kind_id
        or authenticated.activation.graph_node_id != artifact.source_graph_node_id
    ):
        return _diagnostic("artifact_source_authority")
    if not _artifact_work_item_matches(state, artifact, authenticated):
        return _diagnostic("artifact_source_authority", "work_item")
    if (
        authenticated.action.id != artifact.source_action_id
        or authenticated.action.artifact_schema_id != artifact.schema_id
        or artifact.payload_digest != artifact_payload_digest(artifact.payload)
    ):
        return _diagnostic("artifact_payload_authority")
    expected_payload = _expected_artifact_payload(authenticated)
    if expected_payload is None or expected_payload != artifact.payload:
        return _diagnostic("artifact_payload_authority")

    stage = next(
        (
            candidate
            for candidate in authenticated.selected_plan.stage_kinds
            if candidate.id == authenticated.run.stage_kind_id
        ),
        None,
    )
    schema = next(
        (
            candidate
            for candidate in authenticated.selected_plan.artifact_schemas
            if candidate.id == artifact.schema_id
        ),
        None,
    )
    if (
        stage is None
        or artifact.schema_id not in stage.artifact_schema_ids
        or schema is None
        or not validate_schema(schema.schema, artifact.payload).accepted
        or not _payload_plan_pin_matches(artifact, authenticated)
    ):
        return _diagnostic("artifact_schema_authority")
    return AuthenticatedArtifactProvenance(
        artifact=artifact,
        observation=authenticated,
    )


def _selected_source_authority(
    state: RuntimeState,
    observation: RunnerObservationRecord,
    evidence: RunnerResultEvidence,
) -> (
    tuple[RunRecord, WorkItem, Activation, SelectedCompiledPlan]
    | ObservationPolicyDiagnostic
):
    run = state.runs.get(observation.run_id)
    if run is None:
        return _diagnostic("source_authority", "run")
    work_item = state.work_items.get(run.work_item_id)
    activation = state.activations.get(run.activation_id)
    if work_item is None or activation is None:
        return _diagnostic("source_authority", "work_or_activation")
    if (
        work_item.ref.plan_ref != run.run_ref.plan_ref
        or activation.plan_ref != run.run_ref.plan_ref
    ):
        return _diagnostic("plan_ref_drift")
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None:
        return _diagnostic("unknown_plan_ref")
    if admitted.plan_ref != run.run_ref.plan_ref:
        return _diagnostic("plan_ref_drift")
    if (
        run.run_ref.work_item_id != work_item.ref.work_item_id
        or run.work_item_id != work_item.ref.work_item_id
        or activation.work_item_id != work_item.ref.work_item_id
        or run.run_ref.generation != work_item.ref.generation
        or activation.generation != run.run_ref.generation + 1
        or activation.claimed_by_run_id != run.run_ref.run_id
        or run.stage_kind_id != activation.stage_kind_id
        or run.runner_binding_id != activation.runner_binding_id
    ):
        return _diagnostic("source_authority", "runtime_links")

    selected_plan = admitted.selected_plan
    stage = next(
        (
            candidate
            for candidate in selected_plan.stage_kinds
            if candidate.id == run.stage_kind_id
        ),
        None,
    )
    runner = next(
        (
            candidate
            for candidate in selected_plan.runner_bindings
            if candidate.id == run.runner_binding_id
        ),
        None,
    )
    graph_node_selected = any(
        activation.graph_node_id in graph.node_ids for graph in selected_plan.graphs
    )
    if stage is None:
        return _diagnostic("selected_source_authority", "stage")
    if runner is None:
        return _diagnostic("selected_source_authority", "runner")
    if stage.runner_binding_id != run.runner_binding_id:
        return _diagnostic("selected_source_authority", "stage_runner")
    if run.stage_kind_id not in runner.stage_kind_ids:
        return _diagnostic("selected_source_authority", "runner_stage")
    if not graph_node_selected:
        return _diagnostic("selected_source_authority", "graph_node")
    if not _source_queue_family_is_selected(
        state,
        selected_plan=selected_plan,
        run=run,
        work_item=work_item,
        activation=activation,
    ):
        return _diagnostic("selected_source_authority", "queue_family")
    if (
        evidence.run_id != run.run_ref.run_id
        or evidence.plan_fingerprint
        != run.run_ref.plan_ref.authority_fingerprint
        or evidence.claim_id != run.run_ref.claim_id
        or evidence.generation != run.run_ref.generation
        or evidence.fencing_token != run.run_ref.fencing_token
        or evidence.stage_kind_id != str(run.stage_kind_id)
        or evidence.graph_node_id != activation.graph_node_id
        or evidence.runner_binding_id != str(run.runner_binding_id)
    ):
        return _diagnostic("evidence_authority")
    return run, work_item, activation, selected_plan


def _authenticated_audit_action(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    transition: TransitionRecord,
    input_id: str,
    run: RunRecord,
    work_item: WorkItem,
    marker_action: TerminalActionDeclaration,
) -> TerminalActionDeclaration | None:
    events = tuple(
        event
        for event in state.governance_events
        if event.input_id == input_id
        or event.record_id == f"{transition.record_id}:governance"
    )
    traces = tuple(
        trace
        for trace in state.traces
        if trace.input_id == input_id
        or trace.record_id == f"{transition.record_id}:trace"
    )
    if len(events) != 1 or len(traces) != 1:
        return None
    event = events[0]
    trace = traces[0]
    expected_without_action = (
        input_id,
        RunnerResultObserved.input_kind,
        "workflow_observation",
        "accepted",
        run.run_ref.plan_ref.authority_fingerprint,
        work_item.ref.work_item_id,
        run.run_ref.run_id,
        "terminal_action",
        None,
    )
    if not (
        event.record_id == f"{transition.record_id}:governance"
        and trace.record_id == f"{transition.record_id}:trace"
        and event.action_id == trace.action_id
        and event.action_id is not None
        and (
            event.input_id,
            event.input_kind,
            event.input_family,
            event.disposition,
            event.plan_fingerprint,
            event.work_item_id,
            event.run_id,
            event.authority_source,
            event.refusal_reason,
        )
        == expected_without_action
        and (
            trace.input_id,
            trace.input_kind,
            trace.input_family,
            trace.disposition,
            trace.plan_fingerprint,
            trace.work_item_id,
            trace.run_id,
            trace.authority_source,
            trace.refusal_reason,
        )
        == expected_without_action
    ):
        return None
    allowed_actions = {marker_action.id: marker_action}
    for counter in selected_plan.counters:
        if counter.increment_action_id != marker_action.id:
            continue
        threshold_action = next(
            (
                candidate
                for candidate in selected_plan.terminal_actions
                if candidate.id == counter.threshold_action_id
            ),
            None,
        )
        if (
            threshold_action is not None
            and threshold_action.action_kind == "recovery_route"
            and threshold_action.stage_kind_id == marker_action.stage_kind_id
            and any(
                marker_action.id in policy.source_recovery_action_ids
                and threshold_action.target_stage_kind_id
                == policy.recovery_stage_kind_id
                for policy in selected_plan.recovery_policies
            )
            and _threshold_action_is_proven(
                state,
                selected_plan=selected_plan,
                counter_id=counter.id,
                threshold_count=counter.threshold_count,
                input_id=input_id,
                run=run,
                work_item=work_item,
                threshold_action=threshold_action,
            )
        ):
            allowed_actions[threshold_action.id] = threshold_action
    return allowed_actions.get(event.action_id)


def _source_queue_family_is_selected(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    work_item: WorkItem,
    activation: Activation,
) -> bool:
    if work_item.queue_family_id != activation.queue_family_id:
        return False
    stage = next(
        (
            candidate
            for candidate in selected_plan.stage_kinds
            if candidate.id == run.stage_kind_id
        ),
        None,
    )
    if stage is None:
        return False
    if work_item.queue_family_id in stage.input_queue_family_ids:
        return True
    if work_item.lineage_id is None:
        return False
    policies = {
        policy.id: policy for policy in selected_plan.recovery_policies
    }
    return any(
        attempt.plan_ref == run.run_ref.plan_ref
        and attempt.lineage_id == work_item.lineage_id
        and attempt.source_work_item_id == work_item.ref.work_item_id
        and attempt.source_queue_family_id == work_item.queue_family_id
        and (policy := policies.get(attempt.policy_id)) is not None
        and policy.recovery_stage_kind_id == run.stage_kind_id
        for attempt in state.recovery_attempts.values()
    )


def _threshold_action_is_proven(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    counter_id: object,
    threshold_count: int,
    input_id: str,
    run: RunRecord,
    work_item: WorkItem,
    threshold_action: TerminalActionDeclaration,
) -> bool:
    if work_item.lineage_id is None:
        return False
    counters = tuple(
        record
        for record in state.counters.values()
        if record.counter_id == counter_id
        and record.selected_plan_ref == run.run_ref.plan_ref
        and record.lineage_id == work_item.lineage_id
    )
    if len(counters) != 1 or counters[0].value < threshold_count:
        return False
    policies = tuple(
        policy
        for policy in selected_plan.recovery_policies
        if threshold_action.target_stage_kind_id == policy.recovery_stage_kind_id
    )
    policy_ids = {policy.id for policy in policies}
    if any(
        route.created_by_input_id == input_id
        and route.action_id == threshold_action.id
        and route.source_run_id == run.run_ref.run_id
        and route.source_work_item_id == work_item.ref.work_item_id
        for route in state.activation_routes
    ):
        return True
    if any(
        wait.created_input_id == input_id
        and wait.policy_id in policy_ids
        and wait.recovery_action_id == threshold_action.id
        and wait.source_run_id == run.run_ref.run_id
        and wait.source_work_item_id == work_item.ref.work_item_id
        and wait.source_activation_id == run.activation_id
        for wait in state.cooldown_waits.values()
    ):
        return True
    if any(
        attempt.policy_id in policy_ids
        and attempt.plan_ref == run.run_ref.plan_ref
        and attempt.lineage_id == work_item.lineage_id
        and attempt.source_run_id == run.run_ref.run_id
        and attempt.source_work_item_id == work_item.ref.work_item_id
        and attempt.source_activation_id == run.activation_id
        and attempt.recovery_action_id == threshold_action.id
        and input_id in {attempt.created_by_input_id, attempt.updated_by_input_id}
        for attempt in state.recovery_attempts.values()
    ):
        return True
    return any(
        quarantine.created_input_id == input_id
        and quarantine.policy_id in policy_ids
        and quarantine.selected_plan_ref == run.run_ref.plan_ref
        and quarantine.lineage_id == work_item.lineage_id
        and quarantine.original_source_run_id == run.run_ref.run_id
        and quarantine.original_source_work_item_id == work_item.ref.work_item_id
        and quarantine.original_source_activation_id == run.activation_id
        for quarantine in state.lineage_quarantines.values()
    )


def _payload_plan_pin_matches(
    artifact: ArtifactRecord,
    authenticated: AuthenticatedRunnerObservation,
) -> bool:
    plan_ref = authenticated.run.run_ref.plan_ref
    selected_plan_id = artifact.payload.get("selected_plan_id")
    selected_fingerprint = artifact.payload.get("selected_plan_fingerprint")
    return (
        selected_plan_id is None or selected_plan_id == plan_ref.plan_id
    ) and (
        selected_fingerprint is None
        or selected_fingerprint == plan_ref.authority_fingerprint
    )


def _artifact_work_item_matches(
    state: RuntimeState,
    artifact: ArtifactRecord,
    authenticated: AuthenticatedRunnerObservation,
) -> bool:
    if authenticated.action.action_kind != "create_incident_route":
        return artifact.work_item_id == authenticated.work_item.ref.work_item_id
    routes = tuple(
        route
        for route in state.activation_routes
        if route.created_by_input_id == artifact.created_by_input_id
        or route.source_run_id == authenticated.run.run_ref.run_id
        and route.action_id == authenticated.action.id
    )
    if len(routes) != 1:
        return False
    route = routes[0]
    target_work = state.work_items.get(route.target_work_item_id)
    target_activation = state.activations.get(route.target_activation_id)
    return (
        route.created_by_input_id == artifact.created_by_input_id
        and route.created_by_input_id == authenticated.transition.input_id
        and route.source_run_id == authenticated.run.run_ref.run_id
        and route.source_work_item_id == authenticated.work_item.ref.work_item_id
        and route.action_id == authenticated.action.id
        and route.target_work_item_id == artifact.work_item_id
        and target_work is not None
        and target_work.created_by_input_id == artifact.created_by_input_id
        and target_activation is not None
        and target_activation.work_item_id == artifact.work_item_id
        and target_activation.created_by_input_id == artifact.created_by_input_id
    )


def _expected_artifact_payload(
    authenticated: AuthenticatedRunnerObservation,
) -> Mapping[str, object] | None:
    action = authenticated.action
    if action.action_kind != "create_incident_route":
        return authenticated.evidence.artifact_payload
    if action.payload_projection is None:
        return None
    projected = evaluate_projection(
        action.payload_projection,
        projection_context_for_run(
            work_item=authenticated.work_item,
            run=authenticated.run,
            observation_payload=authenticated.evidence.observation_payload,
            artifact_payload=authenticated.evidence.artifact_payload,
        ),
    )
    if projected.accepted and isinstance(projected.value, Mapping):
        return projected.value
    return None


def _diagnostic(
    reason_code: str,
    detail: str | None = None,
) -> ObservationPolicyDiagnostic:
    return ObservationPolicyDiagnostic(reason_code=reason_code, detail=detail)


__all__ = (
    "AuthenticatedArtifactProvenance",
    "AuthenticatedRunnerObservation",
    "ObservationPolicyDiagnostic",
    "authenticate_artifact_provenance",
    "authenticate_runner_observation",
)
