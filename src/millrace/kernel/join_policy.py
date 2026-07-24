"""Pure kernel policy for selected-join participation and completion."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    FanoutDeclaration,
    GeneratedWorkRouteDeclaration,
    JoinDeclaration,
    SelectedCompiledPlan,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.schema import validate_schema
from millrace.contracts.state import (
    Activation,
    ActivationRouteRecord,
    ArtifactRecord,
    FanoutRecord,
    PlanRef,
    RunRecord,
    RuntimeState,
    WorkItem,
)
from millrace.contracts.transition import JoinFromArtifact, input_payload_digest
from millrace.kernel.fanout_policy import (
    AuthenticatedLifecycleCommand,
    PolicyAssessment,
    SourceContext,
    _accepted_creator_transition,
    artifact_relevant_to_fanout,
    assess_fanout,
    authenticate_lifecycle_command,
    sorted_artifacts,
    source_context_for_artifact,
)

_DIGEST_DOMAIN = b"millrace-selected-join-policy-v1\0"


@dataclass(frozen=True, order=True, slots=True)
class SelectedJoinEvidence:
    schema_id: str
    artifact_id: str
    artifact_digest: str
    source_action_id: str
    source_run_id: str
    source_work_item_id: str
    fanout_id: str
    fanout_record_id: str


@dataclass(frozen=True, slots=True)
class SelectedJoinEvidenceArtifactProjection:
    schema_id: str
    artifact_id: str
    artifact_digest: str
    payload: Mapping[str, AuthorityValue]
    source_action_id: str
    source_run_id: str
    source_work_item_id: str
    fanout_id: str
    fanout_record_id: str
    item_key: str


@dataclass(frozen=True, slots=True)
class SelectedJoinEvidenceProjection:
    join_id: str
    correlation_key: str
    correlation_value: str
    correlation_identity: str
    lineage_id: str | None
    bundle_artifact_id: str
    bundle_artifact_schema_id: str
    bundle_artifact_digest: str
    required_artifact_schema_ids: tuple[str, ...]
    evidence_artifacts: tuple[SelectedJoinEvidenceArtifactProjection, ...]


@dataclass(frozen=True, slots=True)
class LogicalJoinKey:
    plan_ref: PlanRef
    join_id: str
    bundle_artifact_id: str
    bundle_artifact_digest: str
    lineage_id: str | None
    correlation_identity: str
    selected_evidence: tuple[SelectedJoinEvidence, ...]


@dataclass(frozen=True, slots=True)
class JoinEvidence:
    artifact: ArtifactRecord
    source_context: SourceContext
    fanout_record: FanoutRecord
    fanout: FanoutDeclaration
    bundle_artifact: ArtifactRecord
    bundle_context: SourceContext
    correlation: str


@dataclass(frozen=True, slots=True)
class ExpectedJoinSlot:
    allowed_schema_ids: tuple[str, ...]
    fanout_record: FanoutRecord


@dataclass(frozen=True, slots=True)
class JoinGroup:
    plan_ref: PlanRef
    lineage_id: str | None
    bundle_artifact: ArtifactRecord
    bundle_work_item: WorkItem
    correlation: str
    required_schema_ids: tuple[str, ...]
    expected_slots: tuple[ExpectedJoinSlot, ...]
    evidence_by_schema: Mapping[str, tuple[JoinEvidence, ...]]


@dataclass(frozen=True, slots=True)
class JoinEvidenceProgress:
    observed_schema_ids: tuple[str, ...]
    ready: bool


@dataclass(frozen=True, slots=True)
class _AuthenticatedJoinCommand:
    authenticated: AuthenticatedLifecycleCommand
    join_id: str
    source_artifact_id: str


def canonical_correlation_identity(correlation: str) -> str:
    serialized = json.dumps(correlation, separators=(",", ":"), sort_keys=True).encode()
    return sha256(_DIGEST_DOMAIN + b"correlation\0" + serialized).hexdigest()


def join_target_route(
    selected_plan: SelectedCompiledPlan,
    join: JoinDeclaration,
) -> GeneratedWorkRouteDeclaration | None:
    routes = tuple(
        route
        for route in selected_plan.generated_work_routes
        if str(route.stage_kind_id) == str(join.target_stage_kind_id)
    )
    return routes[0] if len(routes) == 1 else None


def join_groups_for_declaration(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    plan_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
) -> tuple[JoinGroup, ...] | PolicyAssessment:
    expected_by_group: dict[
        tuple[PlanRef, str | None, str, str, str],
        list[ExpectedJoinSlot],
    ] = {}
    group_context: dict[
        tuple[PlanRef, str | None, str, str, str],
        tuple[ArtifactRecord, WorkItem, str],
    ] = {}
    required = {str(schema_id) for schema_id in join.required_artifact_schema_ids}
    for record in sorted(
        state.fanout_records.values(),
        key=lambda candidate: candidate.record_id,
    ):
        if (
            record.selected_plan_ref.authority_fingerprint != plan_fingerprint
            or (fanout := _fanout_for(selected_plan, str(record.fanout_id))) is None
        ):
            continue
        emitted_required = _required_schemas_emitted_by_stage(
            selected_plan,
            stage_kind_id=str(record.target_stage_kind_id),
            required=required,
        )
        if not emitted_required:
            continue
        bundle_artifact = state.artifacts.get(record.source_artifact_id)
        if bundle_artifact is None:
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="fanout_partial_state",
                source_artifact_id=record.source_artifact_id,
                detail="bundle_source",
            )
        bundle_context = source_context_for_artifact(state, bundle_artifact)
        if isinstance(bundle_context, PolicyAssessment):
            return bundle_context
        if (
            bundle_context.run.run_ref.plan_ref.authority_fingerprint
            != plan_fingerprint
        ):
            return _partial("fanout_partial_state", bundle_artifact, detail="bundle")
        if not artifact_relevant_to_fanout(bundle_artifact, fanout):
            return _partial(
                "wrong_source_artifact",
                bundle_artifact,
                detail="bundle",
            )
        fanout_assessment = assess_fanout(state, bundle_context, fanout)
        if fanout_assessment.status != "complete":
            return _partial(
                fanout_assessment.reason_code or "fanout_partial_state",
                bundle_artifact,
                detail=fanout_assessment.detail or "fanout_provenance",
            )
        correlation = _join_correlation_value(bundle_artifact, join)
        if correlation is None:
            return _partial(
                "invalid_join_evidence",
                bundle_artifact,
                detail="correlation",
            )
        key = (
            record.selected_plan_ref,
            record.lineage_id,
            bundle_artifact.artifact_id,
            bundle_artifact.payload_digest,
            correlation,
        )
        expected_by_group.setdefault(key, []).append(
            ExpectedJoinSlot(
                allowed_schema_ids=emitted_required,
                fanout_record=record,
            )
        )
        group_context[key] = (
            bundle_artifact,
            bundle_context.work_item,
            correlation,
        )

    evidence_by_group: dict[
        tuple[PlanRef, str | None, str, str, str],
        dict[str, list[JoinEvidence]],
    ] = {key: {} for key in expected_by_group}
    for artifact in sorted_artifacts(state):
        if str(artifact.schema_id) not in required:
            continue
        participation = _selected_fanout_participation(
            state,
            selected_plan=selected_plan,
            artifact=artifact,
        )
        if participation is None:
            continue
        if isinstance(participation, PolicyAssessment):
            return participation
        source_context = source_context_for_artifact(state, artifact)
        if isinstance(source_context, PolicyAssessment):
            return source_context
        if (
            source_context.run.run_ref.plan_ref.authority_fingerprint
            != plan_fingerprint
        ):
            continue
        evidence = _join_evidence_for_participant(
            state,
            selected_plan=selected_plan,
            join=join,
            source_context=source_context,
            participation=participation,
        )
        if isinstance(evidence, PolicyAssessment):
            return evidence
        key = (
            source_context.run.run_ref.plan_ref,
            source_context.work_item.lineage_id,
            evidence.bundle_artifact.artifact_id,
            evidence.bundle_artifact.payload_digest,
            evidence.correlation,
        )
        expected_slots_by_target = {
            slot.fanout_record.record_id: slot
            for slot in expected_by_group.get(key, ())
        }
        schema_id = str(artifact.schema_id)
        expected_slot = expected_slots_by_target.get(evidence.fanout_record.record_id)
        if (
            key not in expected_by_group
            or expected_slot is None
            or schema_id not in expected_slot.allowed_schema_ids
        ):
            return _partial(
                "join_evidence_mismatch",
                artifact,
                detail="expected_target",
            )
        evidence_by_group[key].setdefault(schema_id, []).append(evidence)
    groups = tuple(
        JoinGroup(
            plan_ref=plan_ref,
            lineage_id=lineage_id,
            bundle_artifact=group_context[key][0],
            bundle_work_item=group_context[key][1],
            correlation=group_context[key][2],
            required_schema_ids=tuple(sorted(required)),
            expected_slots=tuple(
                sorted(
                    expected_slots,
                    key=lambda slot: (
                        slot.fanout_record.record_id,
                        slot.allowed_schema_ids,
                    ),
                )
            ),
            evidence_by_schema={
                schema_id: tuple(evidence)
                for schema_id, evidence in evidence_by_group[key].items()
            },
        )
        for key, expected_slots in expected_by_group.items()
        for plan_ref, lineage_id, _bundle_id, _bundle_digest, _correlation in (key,)
    )
    transition_assessment = _join_transition_integrity(
        state,
        plan_fingerprint=plan_fingerprint,
        join=join,
        groups=groups,
    )
    return transition_assessment or groups


def join_group_for_source(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    plan_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
    source_artifact_id: str,
) -> JoinGroup | PolicyAssessment:
    groups = join_groups_for_declaration(
        state,
        selected_plan=selected_plan,
        plan_fingerprint=plan_fingerprint,
        join=join,
    )
    if isinstance(groups, PolicyAssessment):
        return groups
    for group in groups:
        if _group_contains_artifact(group, source_artifact_id):
            return group
    artifact = state.artifacts.get(source_artifact_id)
    return PolicyAssessment(
        "partial_or_corrupt",
        reason_code="wrong_source_artifact",
        source_artifact_id=None if artifact is None else artifact.artifact_id,
    )


def logical_join_key(join: JoinDeclaration, group: JoinGroup) -> LogicalJoinKey:
    selected_evidence = tuple(
        sorted(
            SelectedJoinEvidence(
                schema_id=schema_id,
                artifact_id=evidence.artifact.artifact_id,
                artifact_digest=evidence.artifact.payload_digest,
                source_action_id=str(evidence.artifact.source_action_id),
                source_run_id=evidence.source_context.run.run_ref.run_id,
                source_work_item_id=(
                    evidence.source_context.work_item.ref.work_item_id
                ),
                fanout_id=str(evidence.fanout.id),
                fanout_record_id=evidence.fanout_record.record_id,
            )
            for schema_id, evidence_items in group.evidence_by_schema.items()
            for evidence in evidence_items
        )
    )
    return LogicalJoinKey(
        plan_ref=group.plan_ref,
        join_id=str(join.id),
        bundle_artifact_id=group.bundle_artifact.artifact_id,
        bundle_artifact_digest=group.bundle_artifact.payload_digest,
        lineage_id=group.lineage_id,
        correlation_identity=canonical_correlation_identity(group.correlation),
        selected_evidence=selected_evidence,
    )


def assess_join_group(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    plan_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
    group: JoinGroup,
) -> PolicyAssessment:
    evidence_assessment = _join_evidence_assessment(group)
    if evidence_assessment.status != "ready":
        return evidence_assessment
    key = logical_join_key(join, group)
    all_groups = join_groups_for_declaration(
        state,
        selected_plan=selected_plan,
        plan_fingerprint=plan_fingerprint,
        join=join,
    )
    if isinstance(all_groups, PolicyAssessment):
        return all_groups
    matching_routes: list[ActivationRouteRecord] = []
    for route in state.activation_routes:
        if not _route_participates_in_join_completion(join, route):
            continue
        route_group = _group_for_route(all_groups, route)
        if route_group is None:
            continue
        if _join_evidence_assessment(route_group).status != "ready":
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="route_evidence",
            )
        if logical_join_key(join, route_group) != key:
            continue
        if str(route.action_id) != str(join.id):
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="route_action",
            )
        matching_routes.append(route)
    if not matching_routes:
        return PolicyAssessment("ready")
    if len(matching_routes) != 1:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_partial_state",
            detail="duplicate_completion",
        )
    detail = _completion_route_mismatch(
        state,
        selected_plan=selected_plan,
        join=join,
        group=group,
        route=matching_routes[0],
    )
    if detail is not None:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_partial_state",
            detail=detail,
        )
    return PolicyAssessment("complete")


def project_join_evidence_progress(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    plan_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
    bundle_artifact_id: str,
) -> JoinEvidenceProgress | PolicyAssessment:
    """Project slot-aware evidence progress from selected join authority."""
    groups = join_groups_for_declaration(
        state,
        selected_plan=selected_plan,
        plan_fingerprint=plan_fingerprint,
        join=join,
    )
    if isinstance(groups, PolicyAssessment):
        return groups
    matching_groups = tuple(
        group
        for group in groups
        if group.bundle_artifact.artifact_id == bundle_artifact_id
    )
    if len(matching_groups) != 1:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_partial_state",
            source_artifact_id=bundle_artifact_id,
            detail="bundle_group",
        )
    group = matching_groups[0]
    assessment = assess_join_group(
        state,
        selected_plan=selected_plan,
        plan_fingerprint=plan_fingerprint,
        join=join,
        group=group,
    )
    if assessment.status == "partial_or_corrupt":
        return assessment
    observed = tuple(
        str(schema_id)
        for schema_id in join.required_artifact_schema_ids
        if str(schema_id) in group.evidence_by_schema
    )
    return JoinEvidenceProgress(
        observed_schema_ids=observed,
        ready=assessment.status in {"ready", "complete"},
    )


def project_selected_join_evidence_for_target(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> SelectedJoinEvidenceProjection | PolicyAssessment | None:
    join_by_id = {str(join.id): join for join in selected_plan.join_declarations}
    target_routes = tuple(
        route
        for route in state.activation_routes
        if route.target_work_item_id == work_item.ref.work_item_id
        or route.target_activation_id == activation.activation_id
    )
    join_routes = tuple(
        route for route in target_routes if str(route.action_id) in join_by_id
    )
    if not join_routes:
        if _has_accepted_join_target_provenance(
            state,
            activation=activation,
            work_item=work_item,
        ) or any(
            _route_created_by_join_transition(state, route)
            for route in target_routes
        ):
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="route_action",
            )
        return None
    if len(join_routes) != 1:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_partial_state",
            detail="duplicate_completion",
        )

    route = join_routes[0]
    if (
        route.target_work_item_id != work_item.ref.work_item_id
        or route.target_activation_id != activation.activation_id
    ):
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_partial_state",
            detail="target_route",
        )
    join = join_by_id[str(route.action_id)]
    groups = join_groups_for_declaration(
        state,
        selected_plan=selected_plan,
        plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        join=join,
    )
    if isinstance(groups, PolicyAssessment):
        return _join_completion_partial(groups)

    matching_groups: list[JoinGroup] = []
    for group in groups:
        if not _group_contains_source_pair(
            group,
            route.source_run_id,
            route.source_work_item_id,
        ):
            continue
        assessment = assess_join_group(
            state,
            selected_plan=selected_plan,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            join=join,
            group=group,
        )
        if assessment.status == "partial_or_corrupt":
            return assessment
        if assessment.status == "complete":
            matching_groups.append(group)
    if len(matching_groups) != 1:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_partial_state",
            detail="target_group",
        )
    group = matching_groups[0]
    if (
        group.plan_ref != run.run_ref.plan_ref
        or group.lineage_id != work_item.lineage_id
    ):
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_partial_state",
            detail="target_authority",
        )
    return _join_evidence_projection(join, group)


def _join_completion_partial(assessment: PolicyAssessment) -> PolicyAssessment:
    return PolicyAssessment(
        "partial_or_corrupt",
        reason_code="join_partial_state",
        source_artifact_id=assessment.source_artifact_id,
        detail=assessment.reason_code or assessment.detail,
    )


def _join_evidence_projection(
    join: JoinDeclaration,
    group: JoinGroup,
) -> SelectedJoinEvidenceProjection:
    evidence_artifacts = tuple(
        SelectedJoinEvidenceArtifactProjection(
            schema_id=schema_id,
            artifact_id=evidence.artifact.artifact_id,
            artifact_digest=evidence.artifact.payload_digest,
            payload=evidence.artifact.payload,
            source_action_id=str(evidence.artifact.source_action_id),
            source_run_id=evidence.source_context.run.run_ref.run_id,
            source_work_item_id=evidence.source_context.work_item.ref.work_item_id,
            fanout_id=str(evidence.fanout.id),
            fanout_record_id=evidence.fanout_record.record_id,
            item_key=evidence.fanout_record.item_key,
        )
        for schema_id, evidence_items in sorted(group.evidence_by_schema.items())
        for evidence in sorted(
            evidence_items,
            key=lambda item: (
                item.fanout_record.item_key,
                item.artifact.artifact_id,
                item.fanout_record.record_id,
            ),
        )
    )
    return SelectedJoinEvidenceProjection(
        join_id=str(join.id),
        correlation_key=join.correlation_key,
        correlation_value=group.correlation,
        correlation_identity=canonical_correlation_identity(group.correlation),
        lineage_id=group.lineage_id,
        bundle_artifact_id=group.bundle_artifact.artifact_id,
        bundle_artifact_schema_id=str(group.bundle_artifact.schema_id),
        bundle_artifact_digest=group.bundle_artifact.payload_digest,
        required_artifact_schema_ids=group.required_schema_ids,
        evidence_artifacts=evidence_artifacts,
    )


def _join_evidence_assessment(group: JoinGroup) -> PolicyAssessment:
    expected = {
        slot.fanout_record.record_id: slot for slot in group.expected_slots
    }
    if len(expected) != len(group.expected_slots):
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_evidence_mismatch",
            detail="expected_target",
        )
    evidence_counts: dict[str, int] = {}
    actual_schema_ids: set[str] = set()
    for schema_id, evidence_items in group.evidence_by_schema.items():
        for evidence in evidence_items:
            target_id = evidence.fanout_record.record_id
            slot = expected.get(target_id)
            if slot is None or schema_id not in slot.allowed_schema_ids:
                return PolicyAssessment(
                    "partial_or_corrupt",
                    reason_code="join_evidence_mismatch",
                    detail="expected_target",
                )
            evidence_counts[target_id] = evidence_counts.get(target_id, 0) + 1
            actual_schema_ids.add(schema_id)
    duplicate = next(
        (slot for slot, count in evidence_counts.items() if count != 1),
        None,
    )
    if duplicate is not None:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_evidence_duplicate",
            detail=duplicate,
        )
    if set(evidence_counts) != set(expected):
        return PolicyAssessment("not_ready")
    if not set(group.required_schema_ids).issubset(actual_schema_ids):
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_evidence_mismatch",
            detail="required_schema_coverage",
        )
    return PolicyAssessment("ready")


def _join_transition_integrity(
    state: RuntimeState,
    *,
    plan_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
    groups: tuple[JoinGroup, ...],
) -> PolicyAssessment | None:
    commands, command_detail = _authenticated_join_commands(state)
    if command_detail is not None:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_partial_state",
            detail=command_detail,
        )
    plan_commands = tuple(
        command
        for command in commands
        if command.authenticated.plan_ref.authority_fingerprint == plan_fingerprint
        and command.join_id == str(join.id)
    )
    for command in plan_commands:
        transition = command.authenticated.transition
        source_context = command.authenticated.source_context
        routes = tuple(
            route
            for route in state.activation_routes
            if route.created_by_input_id == transition.input_id
        )
        if len(routes) != 1:
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="transition_route",
            )
        route = routes[0]
        if (
            route.record_id != f"{transition.record_id}:route"
            or route.source_run_id != source_context.run.run_ref.run_id
            or route.source_work_item_id
            != source_context.work_item.ref.work_item_id
        ):
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="route_identity",
            )
        if str(route.action_id) != str(join.id):
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="route_action",
            )
        matching_groups = tuple(
            group
            for group in groups
            if _group_contains_artifact(group, command.source_artifact_id)
        )
        if len(matching_groups) != 1:
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="route_source",
            )
    admitted = state.admitted_plans.get(plan_fingerprint)
    if admitted is None:
        return PolicyAssessment(
            "partial_or_corrupt",
            reason_code="join_partial_state",
            detail="route_plan",
        )
    for route in state.activation_routes:
        if str(route.action_id) != str(join.id):
            continue
        source_run = state.runs.get(route.source_run_id)
        if source_run is None:
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="route_source",
            )
        if source_run.run_ref.plan_ref != admitted.plan_ref:
            if (
                source_run.run_ref.plan_ref.authority_fingerprint
                == plan_fingerprint
            ):
                return PolicyAssessment(
                    "partial_or_corrupt",
                    reason_code="join_partial_state",
                    detail="route_plan",
                )
            continue
        matching_commands = tuple(
            command
            for command in plan_commands
            if command.authenticated.transition.input_id
            == route.created_by_input_id
        )
        if len(matching_commands) != 1:
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="route_creator",
            )
        command = matching_commands[0]
        source_context = command.authenticated.source_context
        matching_groups = tuple(
            group
            for group in groups
            if _group_contains_artifact(group, command.source_artifact_id)
        )
        if (
            route.source_run_id != source_context.run.run_ref.run_id
            or route.source_work_item_id
            != source_context.work_item.ref.work_item_id
            or len(matching_groups) != 1
        ):
            return PolicyAssessment(
                "partial_or_corrupt",
                reason_code="join_partial_state",
                detail="route_source",
            )
    return None


def _authenticated_join_commands(
    state: RuntimeState,
) -> tuple[tuple[_AuthenticatedJoinCommand, ...], str | None]:
    candidates: list[
        tuple[JoinFromArtifact, SourceContext, JoinDeclaration]
    ] = []
    for artifact in sorted_artifacts(state):
        source_context = source_context_for_artifact(state, artifact)
        if isinstance(source_context, PolicyAssessment):
            continue
        for join in source_context.selected_plan.join_declarations:
            candidates.append(
                (
                    JoinFromArtifact(
                        "candidate",
                        join_id=str(join.id),
                        source_artifact_id=artifact.artifact_id,
                    ),
                    source_context,
                    join,
                )
            )

    commands: list[_AuthenticatedJoinCommand] = []
    accepted_input_ids = sorted(
        {
            transition.input_id
            for transition in state.transitions
            if transition.accepted
            and transition.input_kind == JoinFromArtifact.input_kind
        }
        | {
            input_id
            for input_id, receipt in state.receipts.items()
            if receipt.accepted
            and any(
                input_payload_digest(
                    JoinFromArtifact(
                        input_id,
                        join_id=template.join_id,
                        source_artifact_id=template.source_artifact_id,
                    )
                )
                == receipt.receipt_ref.input_payload_digest
                for template, _source_context, _join in candidates
            )
        }
    )
    for input_id in accepted_input_ids:
        receipt = state.receipts.get(input_id)
        if receipt is None or not receipt.accepted:
            return (), "command_receipt"
        matches: list[tuple[JoinFromArtifact, SourceContext, JoinDeclaration]] = []
        for template, source_context, join in candidates:
            candidate = JoinFromArtifact(
                input_id,
                join_id=template.join_id,
                source_artifact_id=template.source_artifact_id,
            )
            if input_payload_digest(candidate) == (
                receipt.receipt_ref.input_payload_digest
            ):
                matches.append((candidate, source_context, join))
        if len(matches) != 1:
            return (), "command_digest"
        candidate, source_context, join = matches[0]
        authenticated = authenticate_lifecycle_command(
            state,
            candidate,
            source_context=source_context,
            expected_action_id=None,
            expected_authority_source="join_declaration",
        )
        if isinstance(authenticated, str):
            return (), authenticated
        commands.append(
            _AuthenticatedJoinCommand(
                authenticated=authenticated,
                join_id=str(join.id),
                source_artifact_id=source_context.artifact.artifact_id,
            )
        )
    return tuple(commands), None


def _route_participates_in_join_completion(
    join: JoinDeclaration,
    route: ActivationRouteRecord,
) -> bool:
    return str(route.action_id) == str(join.id)


def _route_created_by_join_transition(
    state: RuntimeState,
    route: ActivationRouteRecord,
) -> bool:
    return any(
        transition.input_id == route.created_by_input_id
        and transition.accepted
        and transition.input_kind == JoinFromArtifact.input_kind
        for transition in state.transitions
    )


def _has_accepted_join_target_provenance(
    state: RuntimeState,
    *,
    activation: Activation,
    work_item: WorkItem,
) -> bool:
    return any(
        _accepted_creator_transition(
            state,
            creator_input_id,
            expected_input_kind=JoinFromArtifact.input_kind,
        )
        is not None
        for creator_input_id in {
            work_item.created_by_input_id,
            activation.created_by_input_id,
        }
    )


def _selected_fanout_participation(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    artifact: ArtifactRecord,
) -> tuple[FanoutRecord, FanoutDeclaration] | PolicyAssessment | None:
    records = tuple(
        record
        for record in state.fanout_records.values()
        if record.target_work_item_id == artifact.work_item_id
    )
    selected_records = tuple(
        (record, fanout)
        for record in records
        if (fanout := _fanout_for(selected_plan, str(record.fanout_id))) is not None
    )
    if selected_records:
        if len(records) != 1 or len(selected_records) != 1:
            return _partial(
                "fanout_partial_state",
                artifact,
                detail="fanout_provenance",
            )
        return selected_records[0]

    routes = tuple(
        route
        for route in state.activation_routes
        if route.target_work_item_id == artifact.work_item_id
    )
    exact_routes = tuple(
        route
        for route in routes
        if _route_matches_selected_fanout(
            state,
            selected_plan=selected_plan,
            artifact=artifact,
            route=route,
        )
    )
    if exact_routes:
        return _partial("fanout_partial_state", artifact, detail="fanout_provenance")
    return None


def _route_matches_selected_fanout(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    artifact: ArtifactRecord,
    route: ActivationRouteRecord,
) -> bool:
    work_item = state.work_items.get(artifact.work_item_id)
    run = state.runs.get(artifact.source_run_id)
    if work_item is None or run is None:
        return False
    activation = state.activations.get(run.activation_id)
    if activation is None or route.target_activation_id != activation.activation_id:
        return False
    for fanout in selected_plan.fanout_declarations:
        if (
            str(route.action_id) != str(fanout.source_action_id)
            or str(work_item.queue_family_id) != str(fanout.target_queue_family_id)
            or str(activation.queue_family_id) != str(fanout.target_queue_family_id)
            or str(activation.stage_kind_id) != str(fanout.target_stage_kind_id)
            or activation.graph_node_id != fanout.target_graph_node_id
            or str(activation.runner_binding_id) != str(fanout.target_runner_binding_id)
        ):
            continue
        source_artifacts = tuple(
            source_artifact
            for source_artifact in state.artifacts.values()
            if source_artifact.source_run_id == route.source_run_id
            and source_artifact.work_item_id == route.source_work_item_id
            and artifact_relevant_to_fanout(source_artifact, fanout)
        )
        if len(source_artifacts) == 1:
            return True
    return False


def _join_evidence_for_participant(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    join: JoinDeclaration,
    source_context: SourceContext,
    participation: tuple[FanoutRecord, FanoutDeclaration],
) -> JoinEvidence | PolicyAssessment:
    artifact = source_context.artifact
    fanout_record, fanout = participation
    correlation = _join_correlation_value(artifact, join)
    if correlation is None:
        return _partial("invalid_join_evidence", artifact, detail="correlation")
    if not _terminal_action_matches_artifact(
        selected_plan,
        artifact,
        source_context.run,
    ):
        return _partial("join_evidence_mismatch", artifact, detail="artifact_action")
    if (
        fanout_record.selected_plan_ref != source_context.run.run_ref.plan_ref
        or fanout_record.target_work_item_id
        != source_context.work_item.ref.work_item_id
        or fanout_record.target_activation_id != source_context.activation.activation_id
        or fanout_record.lineage_id != source_context.work_item.lineage_id
        or str(fanout_record.target_queue_family_id)
        != str(source_context.activation.queue_family_id)
        or str(fanout_record.target_stage_kind_id)
        != str(source_context.activation.stage_kind_id)
        or fanout_record.target_graph_node_id != source_context.activation.graph_node_id
        or str(source_context.work_item.queue_family_id)
        != str(fanout.target_queue_family_id)
    ):
        return _partial("fanout_partial_state", artifact, detail="fanout_target")
    bundle_artifact = state.artifacts.get(fanout_record.source_artifact_id)
    if bundle_artifact is None:
        return _partial("fanout_partial_state", artifact, detail="bundle_source")
    bundle_context = source_context_for_artifact(state, bundle_artifact)
    if isinstance(bundle_context, PolicyAssessment):
        return _partial(
            bundle_context.reason_code or "fanout_partial_state",
            artifact,
            detail=bundle_context.detail or "bundle_source",
        )
    if not artifact_relevant_to_fanout(bundle_artifact, fanout):
        return _partial(
            "wrong_source_artifact",
            bundle_artifact,
            detail="bundle_source",
        )
    fanout_assessment = assess_fanout(state, bundle_context, fanout)
    if fanout_assessment.status != "complete":
        return _partial(
            fanout_assessment.reason_code or "fanout_partial_state",
            artifact,
            detail=fanout_assessment.detail or "fanout_provenance",
        )
    bundle_correlation = _join_correlation_value(bundle_artifact, join)
    if bundle_correlation != correlation:
        return _partial("join_evidence_mismatch", artifact, detail="correlation")
    return JoinEvidence(
        artifact=artifact,
        source_context=source_context,
        fanout_record=fanout_record,
        fanout=fanout,
        bundle_artifact=bundle_artifact,
        bundle_context=bundle_context,
        correlation=correlation,
    )


def _completion_route_mismatch(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    join: JoinDeclaration,
    group: JoinGroup,
    route: ActivationRouteRecord,
) -> str | None:
    if not _group_contains_source_pair(
        group,
        route.source_run_id,
        route.source_work_item_id,
    ):
        return "route_source"
    target_routes = tuple(
        candidate
        for candidate in state.activation_routes
        if candidate.target_work_item_id == route.target_work_item_id
        or candidate.target_activation_id == route.target_activation_id
    )
    if len(target_routes) != 1:
        return "duplicate_completion"
    target_route = join_target_route(selected_plan, join)
    target_work = state.work_items.get(route.target_work_item_id)
    target_activation = state.activations.get(route.target_activation_id)
    if target_route is None or target_work is None or target_activation is None:
        return "target_route"
    if (
        target_work.ref.plan_ref != group.plan_ref
        or str(target_work.queue_family_id) != str(target_route.queue_family_id)
        or target_work.lineage_id != group.lineage_id
        or dict(target_work.payload) != dict(group.bundle_artifact.payload)
        or target_work.created_by_input_id != route.created_by_input_id
        or target_activation.work_item_id != target_work.ref.work_item_id
        or target_activation.plan_ref != group.plan_ref
        or str(target_activation.queue_family_id) != str(target_route.queue_family_id)
        or target_activation.graph_node_id != target_route.graph_node_id
        or str(target_activation.stage_kind_id) != str(target_route.stage_kind_id)
        or str(target_activation.runner_binding_id)
        != str(target_route.runner_binding_id)
        or target_activation.lineage_id != group.lineage_id
        or target_activation.created_by_input_id != route.created_by_input_id
    ):
        return "target_route"
    if target_route.payload_schema_id is None:
        return None
    target_schema = next(
        (
            schema
            for schema in selected_plan.artifact_schemas
            if schema.id == target_route.payload_schema_id
        ),
        None,
    )
    if target_schema is None or not validate_schema(
        target_schema.schema,
        target_work.payload,
    ).accepted:
        return "target_route"
    return None


def _group_for_route(
    groups: tuple[JoinGroup, ...],
    route: ActivationRouteRecord,
) -> JoinGroup | None:
    matching = tuple(
        group
        for group in groups
        if _group_contains_source_pair(
            group,
            route.source_run_id,
            route.source_work_item_id,
        )
    )
    return matching[0] if len(matching) == 1 else None


def _group_contains_source_pair(
    group: JoinGroup,
    run_id: str,
    work_item_id: str,
) -> bool:
    return any(
        evidence.source_context.run.run_ref.run_id == run_id
        and evidence.source_context.work_item.ref.work_item_id == work_item_id
        for evidence_items in group.evidence_by_schema.values()
        for evidence in evidence_items
    )


def _group_contains_artifact(group: JoinGroup, artifact_id: str) -> bool:
    return any(
        evidence.artifact.artifact_id == artifact_id
        for evidence_items in group.evidence_by_schema.values()
        for evidence in evidence_items
    )


def _fanout_for(
    selected_plan: SelectedCompiledPlan,
    fanout_id: str,
) -> FanoutDeclaration | None:
    return next(
        (
            fanout
            for fanout in selected_plan.fanout_declarations
            if str(fanout.id) == fanout_id
        ),
        None,
    )


def _required_schemas_emitted_by_stage(
    selected_plan: SelectedCompiledPlan,
    *,
    stage_kind_id: str,
    required: set[str],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(action.artifact_schema_id)
                for action in selected_plan.terminal_actions
                if str(action.stage_kind_id) == stage_kind_id
                and action.artifact_schema_id is not None
                and str(action.artifact_schema_id) in required
            }
        )
    )


def _terminal_action_matches_artifact(
    selected_plan: SelectedCompiledPlan,
    artifact: ArtifactRecord,
    run: RunRecord,
) -> bool:
    return any(
        str(action.id) == str(artifact.source_action_id)
        and str(action.stage_kind_id) == str(run.stage_kind_id)
        and str(action.artifact_schema_id) == str(artifact.schema_id)
        for action in selected_plan.terminal_actions
    )


def _join_correlation_value(
    artifact: ArtifactRecord,
    join: JoinDeclaration,
) -> str | None:
    value = artifact.payload.get(join.correlation_key)
    return value if isinstance(value, str) and value else None


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
    "JoinEvidence",
    "JoinGroup",
    "LogicalJoinKey",
    "PolicyAssessment",
    "SelectedJoinEvidence",
    "SelectedJoinEvidenceArtifactProjection",
    "SelectedJoinEvidenceProjection",
    "assess_join_group",
    "canonical_correlation_identity",
    "join_group_for_source",
    "join_groups_for_declaration",
    "join_target_route",
    "logical_join_key",
    "project_join_evidence_progress",
    "project_selected_join_evidence_for_target",
)
