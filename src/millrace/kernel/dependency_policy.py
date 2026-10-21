"""Exact prerequisite success and authenticated routed-descendant provenance."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.contracts.compiled_plan import SiblingDependencyFanoutDeclaration
from millrace.contracts.runtime_failure_history import (
    recorded_source_return_source,
)
from millrace.contracts.runtime_failures import recovery_activation_origin_matches
from millrace.contracts.state import (
    Activation,
    ActivationRouteRecord,
    FanoutRecord,
    RuntimeState,
    WorkDependencyRecord,
    WorkItem,
)
from millrace.kernel.fanout_policy import (
    PolicyAssessment,
    artifact_relevant_to_fanout,
    assess_fanout,
    fanout_item_identity,
    fanout_items,
    source_context_for_artifact,
)
from millrace.kernel.observation_policy import (
    AuthenticatedArtifactProvenance,
    AuthenticatedRunnerObservation,
    authenticate_artifact_provenance,
    authenticate_runner_observation,
)
from millrace.kernel.projection import evaluate_projection, projection_context_for_run
from millrace.kernel.terminal_actions import (
    TerminalActionRefusal,
    _route_target_fields_or_refusal,
)


def has_unready_dependency(
    state: RuntimeState,
    work_item: WorkItem,
    *,
    before_input_id: str | None = None,
) -> bool:
    """Gate claims on complete declared edges, including missing/corrupt rows."""
    records = [
        r
        for r in state.fanout_records.values()
        if r.target_work_item_id == work_item.ref.work_item_id
    ]
    if len(records) > 1:
        return True
    declaration = None
    record = records[0] if records else None
    if record is None and _missing_selected_fanout_record(state, work_item):
        return True
    if record is not None:
        admitted = state.admitted_plans.get(
            record.selected_plan_ref.authority_fingerprint
        )
        declaration = (
            next(
                (
                    f
                    for f in admitted.selected_plan.fanout_declarations
                    if f.id == record.fanout_id
                ),
                None,
            )
            if admitted
            else None
        )
        if declaration is None or record.selected_plan_ref != work_item.ref.plan_ref:
            return True
        artifact = state.artifacts.get(record.source_artifact_id)
        if artifact is None:
            return True
        source = source_context_for_artifact(state, artifact)
        if (
            isinstance(source, PolicyAssessment)
            or assess_fanout(state, source, declaration).status != "complete"
        ):
            return True
    for dependency in state.work_dependencies.values():
        if dependency.dependent_work_item_id != work_item.ref.work_item_id:
            continue
        if dependency.selected_plan_ref != work_item.ref.plan_ref or record is None:
            return True
        if dependency.dependency_work_item_id == record.source_work_item_id:
            if dependency.dependency_work_item_id not in state.closed_work_items:
                return True
        elif not isinstance(
            declaration, SiblingDependencyFanoutDeclaration
        ) or not sibling_dependency_ready(
            state,
            dependency,
            declaration,
            before_input_id=before_input_id,
        ):
            return True
    return False


def _missing_selected_fanout_record(state: RuntimeState, work_item: WorkItem) -> bool:
    admitted = state.admitted_plans.get(work_item.ref.plan_ref.authority_fingerprint)
    if admitted is None:
        return False
    for declaration in admitted.selected_plan.fanout_declarations:
        if not isinstance(declaration, SiblingDependencyFanoutDeclaration):
            continue
        for artifact in state.artifacts.values():
            if not artifact_relevant_to_fanout(artifact, declaration):
                continue
            run = state.runs.get(artifact.source_run_id)
            if run is None or run.run_ref.plan_ref != work_item.ref.plan_ref:
                continue
            for key, _item in fanout_items(artifact, declaration) or ():
                identity = fanout_item_identity(
                    plan_fingerprint=work_item.ref.plan_ref.authority_fingerprint,
                    fanout_id=str(declaration.id),
                    source_artifact_id=artifact.artifact_id,
                    item_key=key,
                )
                if identity.target_work_item_id == work_item.ref.work_item_id:
                    return True
    return False


def sibling_dependency_ready(
    state: RuntimeState,
    dependency: WorkDependencyRecord,
    declaration: SiblingDependencyFanoutDeclaration,
    *,
    before_input_id: str | None = None,
) -> bool:
    dependent = state.fanout_records.get(dependency.fanout_record_id)
    if dependent is None:
        return False
    originals = [
        r
        for r in state.fanout_records.values()
        if r.target_work_item_id == dependency.dependency_work_item_id
        and r.selected_plan_ref == dependent.selected_plan_ref
        and r.fanout_id == dependent.fanout_id
        and r.source_artifact_id == dependent.source_artifact_id
    ]
    if len(originals) != 1:
        return False
    original = originals[0]
    indices = {
        transition.input_id: index for index, transition in enumerate(state.transitions)
    }
    ceiling = (
        len(state.transitions)
        if before_input_id is None
        else indices.get(before_input_id, -1)
    )
    for artifact in state.artifacts.values():
        if artifact.source_action_id not in declaration.dependency_success_action_ids:
            continue
        authenticated = authenticate_artifact_provenance(state, artifact)
        if not isinstance(authenticated, AuthenticatedArtifactProvenance):
            continue
        observation = authenticated.observation
        closed = state.closed_work_items.get(observation.run.work_item_id)
        if (
            observation.run.run_ref.plan_ref != dependency.selected_plan_ref
            or observation.action.action_kind not in {"close", "complete_work_item"}
            or closed is None
            or closed.close_kind != "terminal_action"
            or closed.record_id != f"{observation.transition.record_id}:close"
            or closed.source_run_id != observation.run.run_ref.run_id
            or closed.action_id != observation.action.id
            or closed.created_by_input_id != artifact.created_by_input_id
            or not 0 <= indices.get(artifact.created_by_input_id, -1) < ceiling
        ):
            continue
        if _descends_from_original(state, observation.activation, original):
            return True
    return False


def _descends_from_original(
    state: RuntimeState, activation: Activation, original: FanoutRecord
) -> bool:
    plan = state.admitted_plans[
        original.selected_plan_ref.authority_fingerprint
    ].selected_plan
    seen: set[str] = set()
    while activation.activation_id not in seen:
        seen.add(activation.activation_id)
        work = state.work_items.get(activation.work_item_id)
        if (
            work is None
            or activation.plan_ref != original.selected_plan_ref
            or work.ref.plan_ref != original.selected_plan_ref
            or activation.lineage_id != original.lineage_id
            or work.lineage_id != original.lineage_id
        ):
            return False
        if activation.activation_id == original.target_activation_id:
            return activation.work_item_id == original.target_work_item_id
        routes = [
            r
            for r in state.activation_routes
            if r.target_activation_id == activation.activation_id
        ]
        if len(routes) != 1:
            return False
        route = routes[0]
        source_run = state.runs.get(route.source_run_id)
        action = next(
            (a for a in plan.terminal_actions if a.id == route.action_id), None
        )
        if (
            source_run is None
            or action is None
            or source_run.run_ref.plan_ref != original.selected_plan_ref
            or route.target_work_item_id != activation.work_item_id
            or route.created_by_input_id != activation.created_by_input_id
        ):
            return False
        if action.action_kind == "return_to_recorded_source":
            source_run = recorded_source_return_source(
                state, plan, activation.activation_id
            )
            if source_run is None:
                return False
        elif action.action_kind == "recovery_route":
            work_origins = [
                candidate
                for candidate in state.activations.values()
                if candidate.work_item_id == work.ref.work_item_id
                and candidate.created_by_input_id == work.created_by_input_id
            ]
            if len(work_origins) != 1:
                return False
            work_origin = work_origins[0]
            if (
                route.source_work_item_id != work.ref.work_item_id
                or route.source_work_item_id != source_run.work_item_id
                or not recovery_activation_origin_matches(
                    state,
                    plan,
                    activation.activation_id,
                    {
                        (
                            work_origin.queue_family_id,
                            work_origin.stage_kind_id,
                            work_origin.graph_node_id,
                            work_origin.runner_binding_id,
                        )
                    },
                    origin_activation_id=work_origin.activation_id,
                )
            ):
                return False
        elif action.action_kind in {"route", "create_incident_route"}:
            if not _ordinary_route_matches(state, activation, route, work):
                return False
        else:
            return False
        source_activation = state.activations.get(source_run.activation_id)
        if source_activation is None:
            return False
        activation = source_activation
    return False


def _ordinary_route_matches(
    state: RuntimeState,
    activation: Activation,
    route: ActivationRouteRecord,
    work: WorkItem,
) -> bool:
    observations = [
        o
        for o in state.runner_observations.values()
        if o.run_id == route.source_run_id
        or o.created_by_input_id == route.created_by_input_id
    ]
    if len(observations) != 1:
        return False
    observation = authenticate_runner_observation(state, observations[0])
    if not isinstance(observation, AuthenticatedRunnerObservation):
        return False
    action = observation.action
    artifact = state.artifacts.get(f"{observation.transition.record_id}:artifact")
    if (
        artifact is None
        or not isinstance(
            authenticate_artifact_provenance(state, artifact),
            AuthenticatedArtifactProvenance,
        )
        or observation.run.run_ref.run_id != route.source_run_id
        or observation.work_item.ref.work_item_id != route.source_work_item_id
        or observation.transition.input_id != route.created_by_input_id
        or action.id != route.action_id
        or route.record_id != f"{observation.transition.record_id}:route"
        or work.created_by_input_id != route.created_by_input_id
    ):
        return False
    target = _route_target_fields_or_refusal(
        action=action, observation_payload=observation.evidence.observation_payload
    )
    if isinstance(target, TerminalActionRefusal) or target != (
        activation.stage_kind_id,
        activation.graph_node_id,
        activation.queue_family_id,
        activation.runner_binding_id,
    ):
        return False
    payload = observation.evidence.artifact_payload
    if payload is None or action.payload_projection is None:
        return False
    projected = evaluate_projection(
        action.payload_projection,
        projection_context_for_run(
            work_item=observation.work_item,
            run=observation.run,
            observation_payload=observation.evidence.observation_payload or {},
            artifact_payload=payload,
        ),
    )
    return (
        projected.accepted
        and isinstance(projected.value, Mapping)
        and projected.value == work.payload
    )


__all__ = ("has_unready_dependency", "sibling_dependency_ready")
