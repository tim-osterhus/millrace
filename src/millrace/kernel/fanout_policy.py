"""Pure kernel policy for selected-fanout participation and aftermath."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, cast

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    FanoutDeclaration,
    SelectedCompiledPlan,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import ActionId
from millrace.contracts.state import (
    Activation,
    ArtifactRecord,
    FanoutRecord,
    PlanRef,
    RunRecord,
    RuntimeState,
    TransitionRecord,
    WorkItem,
)
from millrace.contracts.transition import (
    FanoutFromArtifact,
    JoinFromArtifact,
    RunnerResultObserved,
    input_family,
    input_kind,
    input_payload_digest,
)
from millrace.kernel.observation_policy import (
    AuthenticatedArtifactProvenance,
    authenticate_artifact_provenance,
)

PolicyStatus = Literal["not_ready", "ready", "complete", "partial_or_corrupt"]
FanoutItems = tuple[tuple[str, Mapping[object, object]], ...]

_MISSING = object()


@dataclass(frozen=True, slots=True)
class PolicyAssessment:
    status: PolicyStatus
    reason_code: str | None = None
    source_artifact_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class SourceContext:
    artifact: ArtifactRecord
    run: RunRecord
    work_item: WorkItem
    activation: Activation
    selected_plan: SelectedCompiledPlan


@dataclass(frozen=True, slots=True)
class FanoutItemIdentity:
    target_work_item_id: str
    target_activation_id: str
    route_record_id: str
    fanout_record_id: str
    dependency_id: str


@dataclass(frozen=True, slots=True)
class SelectedFanoutSourceProjection:
    fanout_record_id: str
    fanout_id: str
    source_work_item_id: str
    source_run_id: str
    source_action_id: str
    source_artifact_id: str
    source_artifact_digest: str
    created_by_input_id: str
    item_key: str


@dataclass(frozen=True, slots=True)
class AuthenticatedLifecycleCommand:
    transition: TransitionRecord
    transition_input: FanoutFromArtifact | JoinFromArtifact
    source_context: SourceContext
    plan_ref: PlanRef


def sorted_artifacts(state: RuntimeState) -> tuple[ArtifactRecord, ...]:
    return tuple(
        sorted(state.artifacts.values(), key=lambda artifact: artifact.artifact_id)
    )


def artifact_relevant_to_fanout(
    artifact: ArtifactRecord,
    fanout: FanoutDeclaration,
) -> bool:
    return str(artifact.source_action_id) == str(fanout.source_action_id) and str(
        artifact.schema_id
    ) == str(fanout.source_artifact_schema_id)


def source_context_for_artifact(
    state: RuntimeState,
    artifact: ArtifactRecord,
) -> SourceContext | PolicyAssessment:
    authenticated = authenticate_artifact_provenance(state, artifact)
    if not isinstance(authenticated, AuthenticatedArtifactProvenance):
        reason_code = (
            authenticated.reason_code
            if authenticated.reason_code in {"plan_ref_drift", "unknown_plan_ref"}
            else "wrong_source_artifact"
        )
        return _partial(reason_code, artifact, detail=authenticated.reason_code)
    observation = authenticated.observation
    return SourceContext(
        artifact=artifact,
        run=observation.run,
        work_item=observation.work_item,
        activation=observation.activation,
        selected_plan=observation.selected_plan,
    )


def authenticate_lifecycle_command(
    state: RuntimeState,
    transition_input: FanoutFromArtifact | JoinFromArtifact,
    *,
    source_context: SourceContext,
    expected_action_id: ActionId | None,
    expected_authority_source: str,
) -> AuthenticatedLifecycleCommand | str:
    input_id = transition_input.input_id
    receipt = state.receipts.get(input_id)
    transitions = tuple(
        transition
        for transition in state.transitions
        if transition.input_id == input_id
    )
    expected_kind = input_kind(transition_input)
    expected_family = input_family(transition_input)
    if (
        receipt is None
        or receipt.receipt_ref.input_id != input_id
        or receipt.receipt_ref.input_payload_digest
        != input_payload_digest(transition_input)
        or not receipt.accepted
        or receipt.refusal_reason is not None
        or len(transitions) != 1
    ):
        return "command_receipt"
    transition = transitions[0]
    if (
        receipt.transition_id != transition.record_id
        or transition.input_kind != expected_kind
        or transition.input_family != expected_family
        or not transition.accepted
    ):
        return "command_transition"

    events = tuple(
        event for event in state.governance_events if event.input_id == input_id
    )
    traces = tuple(trace for trace in state.traces if trace.input_id == input_id)
    if len(events) != 1 or len(traces) != 1:
        return "command_audit"
    event = events[0]
    trace = traces[0]
    expected_audit = (
        input_id,
        expected_kind,
        expected_family,
        "accepted",
        source_context.run.run_ref.plan_ref.authority_fingerprint,
        source_context.work_item.ref.work_item_id,
        source_context.run.run_ref.run_id,
        expected_action_id,
        expected_authority_source,
        None,
    )
    if event.record_id != f"{transition.record_id}:governance" or (
        event.input_id,
        event.input_kind,
        event.input_family,
        event.disposition,
        event.plan_fingerprint,
        event.work_item_id,
        event.run_id,
        event.action_id,
        event.authority_source,
        event.refusal_reason,
    ) != expected_audit:
        return "command_event"
    if trace.record_id != f"{transition.record_id}:trace" or (
        trace.input_id,
        trace.input_kind,
        trace.input_family,
        trace.disposition,
        trace.plan_fingerprint,
        trace.work_item_id,
        trace.run_id,
        trace.action_id,
        trace.authority_source,
        trace.refusal_reason,
    ) != expected_audit:
        return "command_trace"
    return AuthenticatedLifecycleCommand(
        transition=transition,
        transition_input=transition_input,
        source_context=source_context,
        plan_ref=source_context.run.run_ref.plan_ref,
    )


def fanout_item_identity(
    *,
    plan_fingerprint: AuthorityFingerprint,
    fanout_id: str,
    source_artifact_id: str,
    item_key: str,
) -> FanoutItemIdentity:
    raw = (
        f"{plan_fingerprint}\0{fanout_id}\0{source_artifact_id}\0{item_key}"
    ).encode()
    suffix = sha256(raw).hexdigest()[:32]
    return FanoutItemIdentity(
        target_work_item_id=f"generated-work:{suffix}",
        target_activation_id=f"generated-activation:{suffix}",
        route_record_id=f"generated-route:{suffix}",
        fanout_record_id=f"fanout:{suffix}",
        dependency_id=f"dependency:{suffix}",
    )


def fanout_items(
    artifact: ArtifactRecord,
    fanout: FanoutDeclaration,
) -> FanoutItems | None:
    raw_items = _read_mapping_path(
        cast(Mapping[object, object], artifact.payload),
        fanout.item_source_path,
    )
    if raw_items is _MISSING:
        return ()
    if not isinstance(raw_items, tuple) or not raw_items:
        return None
    items: list[tuple[str, Mapping[object, object]]] = []
    seen_item_keys: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            return None
        item_key = raw_item.get(fanout.item_id_key)
        if (
            not isinstance(item_key, str)
            or not item_key
            or item_key in seen_item_keys
        ):
            return None
        seen_item_keys.add(item_key)
        items.append((item_key, raw_item))
    return tuple(items)


def fanout_target_payload(
    mapping: Mapping[str, AuthorityValue],
    raw_item: Mapping[object, object],
    source_artifact_payload: Mapping[str, AuthorityValue],
) -> Mapping[str, AuthorityValue] | None:
    payload: dict[str, object] = {}
    for target_key, raw_path in mapping.items():
        if not isinstance(target_key, str) or not isinstance(raw_path, tuple):
            return None
        path = tuple(path_item for path_item in raw_path if isinstance(path_item, str))
        if len(path) != len(raw_path):
            return None
        value = _read_mapping_path(raw_item, path)
        if value is _MISSING:
            value = _read_mapping_path(
                cast(Mapping[object, object], source_artifact_payload),
                path,
            )
        if value is _MISSING:
            continue
        payload[target_key] = value
    return cast(Mapping[str, AuthorityValue], payload)


def assess_fanout(
    state: RuntimeState,
    source_context: SourceContext,
    fanout: FanoutDeclaration,
) -> PolicyAssessment:
    artifact = source_context.artifact
    if not artifact_relevant_to_fanout(artifact, fanout):
        return _partial("wrong_source_artifact", artifact)
    if fanout.source_state_policy == "source_closed":
        closed = state.closed_work_items.get(artifact.work_item_id)
        if closed is None:
            return PolicyAssessment(
                "not_ready",
                source_artifact_id=artifact.artifact_id,
            )
        if (
            closed.source_run_id != artifact.source_run_id
            or closed.action_id != artifact.source_action_id
        ):
            return _partial("wrong_source_aftermath", artifact)
    elif fanout.source_state_policy != "accepted_terminal_observation":
        return _partial("fanout_partial_state", artifact, detail="source_policy")

    items = fanout_items(artifact, fanout)
    if items is None:
        return _partial("fanout_partial_state", artifact)
    existing = tuple(
        record
        for record in state.fanout_records.values()
        if str(record.fanout_id) == str(fanout.id)
        and record.source_artifact_id == artifact.artifact_id
    )
    if not items:
        if existing:
            return _partial("fanout_partial_state", artifact, detail="item_key")
        if fanout.source_state_policy == "source_closed":
            creator_detail = _fanout_creator_mismatch(
                state,
                source_context,
                fanout,
                existing,
            )
            if creator_detail is not None:
                return _partial(
                    "fanout_partial_state",
                    artifact,
                    detail=creator_detail,
                )
        return PolicyAssessment("complete", source_artifact_id=artifact.artifact_id)
    if _has_orphan_dependency(state):
        return _partial("fanout_partial_state", artifact)
    creator_detail = _fanout_creator_mismatch(
        state,
        source_context,
        fanout,
        existing,
    )
    if creator_detail is not None:
        return _partial("fanout_partial_state", artifact, detail=creator_detail)
    if not existing:
        return PolicyAssessment("ready", source_artifact_id=artifact.artifact_id)
    expected_items = dict(items)
    if len(existing) != len(expected_items):
        return _partial("fanout_partial_state", artifact)
    if {record.item_key for record in existing} != set(expected_items):
        return _partial("fanout_partial_state", artifact)
    if len({record.item_key for record in existing}) != len(existing):
        return _partial("fanout_partial_state", artifact)
    for record in existing:
        expected_payload = fanout_target_payload(
            fanout.target_payload_mapping,
            expected_items[record.item_key],
            artifact.payload,
        )
        if expected_payload is None or _fanout_record_corrupt(
            state,
            source_context,
            fanout,
            record,
            expected_payload=expected_payload,
        ):
            return _partial("fanout_partial_state", artifact)
    return PolicyAssessment("complete", source_artifact_id=artifact.artifact_id)


def project_selected_fanout_source_for_target(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> SelectedFanoutSourceProjection | PolicyAssessment | None:
    target_records = tuple(
        record
        for record in state.fanout_records.values()
        if record.target_work_item_id == work_item.ref.work_item_id
        or record.target_activation_id == activation.activation_id
    )
    if not target_records:
        if _has_selected_fanout_target_provenance(
            state,
            selected_plan=selected_plan,
            run=run,
            activation=activation,
            work_item=work_item,
        ) or _has_selected_fanout_shaped_target_route(
            state,
            selected_plan=selected_plan,
            activation=activation,
            work_item=work_item,
        ):
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="fanout_partial_state",
                detail="target_route",
            )
        return None

    fanout_by_id = {
        str(fanout.id): fanout for fanout in selected_plan.fanout_declarations
    }
    selected_records: list[tuple[FanoutRecord, FanoutDeclaration]] = []
    for record in target_records:
        fanout = fanout_by_id.get(str(record.fanout_id))
        if (
            fanout is None
            or record.target_work_item_id != work_item.ref.work_item_id
            or record.target_activation_id != activation.activation_id
        ):
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="fanout_partial_state",
                source_artifact_id=record.source_artifact_id,
                detail="target_record",
            )
        selected_records.append((record, fanout))
    if len(selected_records) != 1:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="fanout_partial_state",
            detail="target_record",
        )

    record, fanout = selected_records[0]
    if (
        record.selected_plan_ref != run.run_ref.plan_ref
        or work_item.ref.plan_ref != run.run_ref.plan_ref
        or activation.plan_ref != run.run_ref.plan_ref
        or record.lineage_id != work_item.lineage_id
        or record.target_queue_family_id != work_item.queue_family_id
        or record.target_queue_family_id != activation.queue_family_id
        or record.target_stage_kind_id != activation.stage_kind_id
        or record.target_graph_node_id != activation.graph_node_id
        or fanout.target_runner_binding_id != activation.runner_binding_id
        or record.created_by_input_id != work_item.created_by_input_id
        or record.created_by_input_id != activation.created_by_input_id
    ):
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="fanout_partial_state",
            source_artifact_id=record.source_artifact_id,
            detail="target_authority",
        )

    source_artifact = state.artifacts.get(record.source_artifact_id)
    if source_artifact is None:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="fanout_partial_state",
            source_artifact_id=record.source_artifact_id,
            detail="source_artifact",
        )
    source_context = source_context_for_artifact(state, source_artifact)
    if isinstance(source_context, PolicyAssessment):
        return source_context
    assessment = assess_fanout(state, source_context, fanout)
    if assessment.status != "complete":
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code=assessment.reason_code or "fanout_partial_state",
            source_artifact_id=source_artifact.artifact_id,
            detail=assessment.detail,
        )
    if (
        record.source_artifact_digest != source_artifact.payload_digest
        or record.source_work_item_id != source_context.work_item.ref.work_item_id
        or record.source_run_id != source_context.run.run_ref.run_id
        or record.source_action_id != source_artifact.source_action_id
    ):
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="fanout_partial_state",
            source_artifact_id=source_artifact.artifact_id,
            detail="source_authority",
        )
    return SelectedFanoutSourceProjection(
        fanout_record_id=record.record_id,
        fanout_id=str(record.fanout_id),
        source_work_item_id=record.source_work_item_id,
        source_run_id=record.source_run_id,
        source_action_id=str(record.source_action_id),
        source_artifact_id=record.source_artifact_id,
        source_artifact_digest=record.source_artifact_digest,
        created_by_input_id=record.created_by_input_id,
        item_key=record.item_key,
    )


def _fanout_record_corrupt(
    state: RuntimeState,
    source_context: SourceContext,
    fanout: FanoutDeclaration,
    record: FanoutRecord,
    *,
    expected_payload: Mapping[str, AuthorityValue],
) -> bool:
    artifact = source_context.artifact
    run = source_context.run
    work_item = source_context.work_item
    if (
        record.selected_plan_ref != run.run_ref.plan_ref
        or record.source_artifact_digest != artifact.payload_digest
        or record.source_work_item_id != work_item.ref.work_item_id
        or record.source_run_id != run.run_ref.run_id
        or str(record.source_action_id) != str(fanout.source_action_id)
        or str(record.target_queue_family_id) != str(fanout.target_queue_family_id)
        or str(record.target_stage_kind_id) != str(fanout.target_stage_kind_id)
        or record.target_graph_node_id != fanout.target_graph_node_id
        or record.lineage_id != work_item.lineage_id
    ):
        return True
    target_work = state.work_items.get(record.target_work_item_id)
    target_activation = state.activations.get(record.target_activation_id)
    if target_work is None or target_activation is None:
        return True
    if (
        target_work.ref.plan_ref != record.selected_plan_ref
        or str(target_work.queue_family_id) != str(fanout.target_queue_family_id)
        or dict(target_work.payload) != dict(expected_payload)
        or target_work.lineage_id != record.lineage_id
        or target_work.created_by_input_id != record.created_by_input_id
        or target_activation.work_item_id != target_work.ref.work_item_id
        or target_activation.plan_ref != record.selected_plan_ref
        or str(target_activation.queue_family_id) != str(fanout.target_queue_family_id)
        or target_activation.graph_node_id != fanout.target_graph_node_id
        or str(target_activation.stage_kind_id) != str(fanout.target_stage_kind_id)
        or str(target_activation.runner_binding_id)
        != str(fanout.target_runner_binding_id)
        or target_activation.lineage_id != record.lineage_id
        or target_activation.created_by_input_id != record.created_by_input_id
    ):
        return True
    routes = tuple(
        route
        for route in state.activation_routes
        if route.target_work_item_id == record.target_work_item_id
        or route.target_activation_id == record.target_activation_id
    )
    if len(routes) != 1:
        return True
    route = routes[0]
    if (
        route.target_work_item_id != record.target_work_item_id
        or route.target_activation_id != record.target_activation_id
        or route.record_id != _expected_fanout_route_record_id(record)
        or str(route.action_id) != str(fanout.source_action_id)
        or route.source_run_id != run.run_ref.run_id
        or route.source_work_item_id != work_item.ref.work_item_id
        or route.created_by_input_id != record.created_by_input_id
    ):
        return True
    dependencies = tuple(
        dependency
        for dependency in state.work_dependencies.values()
        if dependency.fanout_record_id == record.record_id
    )
    if fanout.dependency_policy != "depends_on_source_work_item":
        return bool(dependencies)
    if len(dependencies) != 1:
        return True
    dependency = dependencies[0]
    return (
        dependency.dependent_work_item_id != record.target_work_item_id
        or dependency.dependency_work_item_id != work_item.ref.work_item_id
        or dependency.selected_plan_ref != record.selected_plan_ref
        or dependency.lineage_id != record.lineage_id
        or dependency.created_by_input_id != record.created_by_input_id
    )


def _fanout_creator_mismatch(
    state: RuntimeState,
    source_context: SourceContext,
    fanout: FanoutDeclaration,
    existing: tuple[FanoutRecord, ...],
) -> str | None:
    artifact = source_context.artifact
    if fanout.source_state_policy == "source_closed":
        commands, command_detail = _authenticated_fanout_commands(state)
        if command_detail is not None:
            return command_detail
        operation_commands = commands.get(
            (
                source_context.run.run_ref.plan_ref,
                str(fanout.id),
                artifact.artifact_id,
            ),
            (),
        )
        if len(operation_commands) > 1:
            return "duplicate_command"
        if not operation_commands:
            return None if not existing else "creator_transition"
        command = operation_commands[0]
        if not existing:
            return "orphan_transition"
        creator_input_ids = {record.created_by_input_id for record in existing}
        if creator_input_ids != {command.transition.input_id}:
            return "mixed_creators"
        if _fanout_output_closure_mismatch(
            state,
            source_context=source_context,
            fanout=fanout,
            creator_input_id=command.transition.input_id,
        ):
            return "output_closure"
        return None
    elif fanout.source_state_policy == "accepted_terminal_observation":
        expected_input_kind = RunnerResultObserved.input_kind
        if not existing and _accepted_creator_transition(
            state,
            artifact.created_by_input_id,
            expected_input_kind=expected_input_kind,
        ) is not None:
            return "orphan_transition"
    else:
        return "source_policy"

    if not existing:
        return None
    creator_input_ids = {record.created_by_input_id for record in existing}
    if len(creator_input_ids) != 1:
        return "mixed_creators"
    creator_input_id = next(iter(creator_input_ids))
    if fanout.source_state_policy == "accepted_terminal_observation" and (
        creator_input_id != artifact.created_by_input_id
    ):
        return "creator_source"
    if _accepted_creator_transition(
        state,
        creator_input_id,
        expected_input_kind=expected_input_kind,
    ) is None:
        return "creator_transition"
    return None


def _authenticated_fanout_commands(
    state: RuntimeState,
) -> tuple[
    Mapping[
        tuple[PlanRef, str, str],
        tuple[AuthenticatedLifecycleCommand, ...],
    ],
    str | None,
]:
    candidates: list[
        tuple[FanoutFromArtifact, SourceContext, FanoutDeclaration]
    ] = []
    for artifact in sorted_artifacts(state):
        source_context = source_context_for_artifact(state, artifact)
        if isinstance(source_context, PolicyAssessment):
            continue
        for fanout in source_context.selected_plan.fanout_declarations:
            if not artifact_relevant_to_fanout(artifact, fanout):
                continue
            candidates.append(
                (
                    FanoutFromArtifact(
                        "candidate",
                        fanout_id=str(fanout.id),
                        source_artifact_id=artifact.artifact_id,
                    ),
                    source_context,
                    fanout,
                )
            )

    commands: dict[
        tuple[PlanRef, str, str],
        list[AuthenticatedLifecycleCommand],
    ] = {}
    accepted_input_ids = sorted(
        {
            transition.input_id
            for transition in state.transitions
            if transition.accepted
            and transition.input_kind == FanoutFromArtifact.input_kind
        }
        | {
            input_id
            for input_id, receipt in state.receipts.items()
            if receipt.accepted
            and any(
                input_payload_digest(
                    FanoutFromArtifact(
                        input_id,
                        fanout_id=template.fanout_id,
                        source_artifact_id=template.source_artifact_id,
                    )
                )
                == receipt.receipt_ref.input_payload_digest
                for template, _source_context, _fanout in candidates
            )
        }
    )
    for input_id in accepted_input_ids:
        receipt = state.receipts.get(input_id)
        if receipt is None or not receipt.accepted:
            return {}, "command_receipt"
        matches: list[
            tuple[FanoutFromArtifact, SourceContext, FanoutDeclaration]
        ] = []
        for template, source_context, fanout in candidates:
            candidate = FanoutFromArtifact(
                input_id,
                fanout_id=template.fanout_id,
                source_artifact_id=template.source_artifact_id,
            )
            if input_payload_digest(candidate) == (
                receipt.receipt_ref.input_payload_digest
            ):
                matches.append((candidate, source_context, fanout))
        if len(matches) != 1:
            return {}, "command_digest"
        candidate, source_context, fanout = matches[0]
        authenticated = authenticate_lifecycle_command(
            state,
            candidate,
            source_context=source_context,
            expected_action_id=fanout.source_action_id,
            expected_authority_source="fanout_declaration",
        )
        if isinstance(authenticated, str):
            return {}, authenticated
        key = (
            authenticated.plan_ref,
            str(fanout.id),
            source_context.artifact.artifact_id,
        )
        commands.setdefault(key, []).append(authenticated)
    return {key: tuple(value) for key, value in commands.items()}, None


def _fanout_output_closure_mismatch(
    state: RuntimeState,
    *,
    source_context: SourceContext,
    fanout: FanoutDeclaration,
    creator_input_id: str,
) -> bool:
    items = fanout_items(source_context.artifact, fanout)
    if items is None:
        return True
    identities = {
        item_key: fanout_item_identity(
            plan_fingerprint=(
                source_context.run.run_ref.plan_ref.authority_fingerprint
            ),
            fanout_id=str(fanout.id),
            source_artifact_id=source_context.artifact.artifact_id,
            item_key=item_key,
        )
        for item_key, _raw_item in items
    }
    if {
        work_item.ref.work_item_id
        for work_item in state.work_items.values()
        if work_item.created_by_input_id == creator_input_id
    } != {identity.target_work_item_id for identity in identities.values()}:
        return True
    if {
        activation.activation_id
        for activation in state.activations.values()
        if activation.created_by_input_id == creator_input_id
    } != {identity.target_activation_id for identity in identities.values()}:
        return True
    if {
        route.record_id
        for route in state.activation_routes
        if route.created_by_input_id == creator_input_id
    } != {identity.route_record_id for identity in identities.values()}:
        return True
    creator_records = {
        record.record_id: record
        for record in state.fanout_records.values()
        if record.created_by_input_id == creator_input_id
    }
    if set(creator_records) != {
        identity.fanout_record_id for identity in identities.values()
    }:
        return True
    expected_dependencies = (
        {identity.dependency_id for identity in identities.values()}
        if fanout.dependency_policy == "depends_on_source_work_item"
        else set()
    )
    if {
        dependency.dependency_id
        for dependency in state.work_dependencies.values()
        if dependency.created_by_input_id == creator_input_id
    } != expected_dependencies:
        return True
    return any(
        str(creator_records[identity.fanout_record_id].fanout_id) != str(fanout.id)
        or creator_records[identity.fanout_record_id].source_artifact_id
        != source_context.artifact.artifact_id
        or creator_records[identity.fanout_record_id].item_key != item_key
        for item_key, identity in identities.items()
    )


def _accepted_creator_transition(
    state: RuntimeState,
    input_id: str,
    *,
    expected_input_kind: str,
) -> TransitionRecord | None:
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
        or receipt.transition_id != transitions[0].record_id
        or not transitions[0].accepted
        or transitions[0].input_kind != expected_input_kind
        or transitions[0].input_family
        != (
            "workflow_observation"
            if expected_input_kind == RunnerResultObserved.input_kind
            else "workflow_kernel_command"
        )
    ):
        return None
    return transitions[0]


def _expected_fanout_route_record_id(record: FanoutRecord) -> str:
    prefix = "fanout:"
    if not record.record_id.startswith(prefix):
        return ""
    return f"generated-route:{record.record_id.removeprefix(prefix)}"


def _has_orphan_dependency(state: RuntimeState) -> bool:
    return any(
        dependency.fanout_record_id not in state.fanout_records
        for dependency in state.work_dependencies.values()
    )


def _has_selected_fanout_shaped_target_route(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
    work_item: WorkItem,
) -> bool:
    return any(
        (
            route.target_work_item_id == work_item.ref.work_item_id
            or route.target_activation_id == activation.activation_id
        )
        and any(
            str(route.action_id) == str(fanout.source_action_id)
            and work_item.queue_family_id == fanout.target_queue_family_id
            and activation.queue_family_id == fanout.target_queue_family_id
            and activation.stage_kind_id == fanout.target_stage_kind_id
            and activation.graph_node_id == fanout.target_graph_node_id
            and activation.runner_binding_id == fanout.target_runner_binding_id
            for fanout in selected_plan.fanout_declarations
        )
        for route in state.activation_routes
    )


def _has_selected_fanout_target_provenance(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> bool:
    if (
        work_item.ref.plan_ref != run.run_ref.plan_ref
        or activation.plan_ref != run.run_ref.plan_ref
        or activation.work_item_id != work_item.ref.work_item_id
    ):
        return False
    creator_input_ids = {
        work_item.created_by_input_id,
        activation.created_by_input_id,
    }
    for fanout in selected_plan.fanout_declarations:
        if fanout.source_state_policy == "source_closed":
            expected_input_kind = FanoutFromArtifact.input_kind
        elif fanout.source_state_policy == "accepted_terminal_observation":
            expected_input_kind = RunnerResultObserved.input_kind
        else:
            continue
        for creator_input_id in creator_input_ids:
            if _accepted_creator_transition(
                state,
                creator_input_id,
                expected_input_kind=expected_input_kind,
            ) is None:
                continue
            receipt = state.receipts[creator_input_id]
            for artifact in sorted_artifacts(state):
                if not artifact_relevant_to_fanout(artifact, fanout):
                    continue
                source_run = state.runs.get(artifact.source_run_id)
                if (
                    source_run is None
                    or source_run.run_ref.plan_ref != run.run_ref.plan_ref
                ):
                    continue
                if fanout.source_state_policy == "source_closed":
                    candidate = FanoutFromArtifact(
                        creator_input_id,
                        fanout_id=str(fanout.id),
                        source_artifact_id=artifact.artifact_id,
                    )
                    if input_payload_digest(candidate) != (
                        receipt.receipt_ref.input_payload_digest
                    ):
                        continue
                    return True
                elif artifact.created_by_input_id != creator_input_id:
                    continue
                items = fanout_items(artifact, fanout)
                if items is None:
                    continue
                for item_key, _raw_item in items:
                    identity = fanout_item_identity(
                        plan_fingerprint=(
                            run.run_ref.plan_ref.authority_fingerprint
                        ),
                        fanout_id=str(fanout.id),
                        source_artifact_id=artifact.artifact_id,
                        item_key=item_key,
                    )
                    if (
                        identity.target_work_item_id
                        == work_item.ref.work_item_id
                        and identity.target_activation_id
                        == activation.activation_id
                    ):
                        return True
    return False


def _read_mapping_path(
    payload: Mapping[object, object],
    path: tuple[str, ...],
) -> object:
    current: object = payload
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _partial(
    reason_code: str,
    artifact: ArtifactRecord,
    *,
    detail: str | None = None,
) -> PolicyAssessment:
    return PolicyAssessment(
        "partial_or_corrupt",
        reason_code=reason_code,
        source_artifact_id=artifact.artifact_id,
        detail=detail,
    )


__all__ = (
    "AuthenticatedLifecycleCommand",
    "FanoutItems",
    "FanoutItemIdentity",
    "PolicyAssessment",
    "SelectedFanoutSourceProjection",
    "SourceContext",
    "artifact_relevant_to_fanout",
    "assess_fanout",
    "authenticate_lifecycle_command",
    "fanout_item_identity",
    "fanout_items",
    "fanout_target_payload",
    "project_selected_fanout_source_for_target",
    "sorted_artifacts",
    "source_context_for_artifact",
)
