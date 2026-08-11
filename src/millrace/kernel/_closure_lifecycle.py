"""Private identity, readiness, and progress policy for selected closures."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from millrace.contracts.compiled_plan import (
    CompletionBehaviorDeclaration,
    verify_authority_fingerprint,
)
from millrace.contracts.state import (
    Activation,
    ArtifactRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    PlanRef,
    RunRecord,
    RuntimeState,
    WorkItem,
)
from millrace.contracts.transition import (
    EnqueueWork,
    OpenClosureTarget,
    TransitionContext,
    artifact_payload_digest,
    canonical_authority_mapping_bytes,
    input_family,
    input_payload_digest,
)
from millrace.kernel.observation_policy import (
    ObservationPolicyDiagnostic,
    authenticate_artifact_provenance,
    authenticate_runner_observation,
)

ClosureReadinessStatus = Literal["settled", "pending", "corrupt"]
_TARGET_DOMAIN = b"millrace-selected-lifecycle-closure-target-v1\0"
_OPEN_DOMAIN = b"millrace-selected-lifecycle-closure-open-v1\0"
_EVALUATE_DOMAIN = b"millrace-selected-lifecycle-closure-evaluate-v1\0"
_SETTLED_SESSION_STATES = {"completed", "interrupted", "failed", "lost"}
_SETTLED_CLEANUP_DISPOSITIONS = {"not_required", "complete"}


@dataclass(frozen=True, slots=True)
class ClosureReadiness:
    status: ClosureReadinessStatus
    anchor_digest: str
    work_item_ids: tuple[str, ...]
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ClosureLogicalTargetKey:
    selected_plan_ref: PlanRef
    completion_behavior_id: str
    lineage_id: str
    root_source_kind: str
    root_source_id: str
    root_work_item_id: str


@dataclass(frozen=True, slots=True)
class ClosureProgress:
    status: str
    detail: str | None = None
    source_artifact_id: str | None = None
    evidence_anchor: Mapping[str, object] | None = None


def closure_target_key_for(value: object) -> ClosureLogicalTargetKey:
    if not isinstance(value, (OpenClosureTarget, ClosureTargetRecord)):
        raise TypeError("closure target key requires an open command or target")
    return ClosureLogicalTargetKey(
        value.selected_plan_ref,
        str(value.completion_behavior_id),
        value.lineage_id,
        value.root_source_kind,
        value.root_source_id,
        value.closure_root_work_item_id or "",
    )


def closure_target_id(key: ClosureLogicalTargetKey) -> str:
    return "closure-target:" + _suffix(_TARGET_DOMAIN, _target_key_value(key))


def closure_lifecycle_identity(
    kind: Literal["open", "evaluate"],
    key: ClosureLogicalTargetKey,
    readiness_anchor_digest: str,
    evidence_anchor: Mapping[str, object] | None = None,
) -> tuple[str, TransitionContext]:
    value: dict[str, object] = {
        "target_key": _target_key_value(key),
        "readiness_anchor_digest": readiness_anchor_digest,
    }
    if evidence_anchor is not None:
        value["evidence_anchor"] = evidence_anchor
    suffix = _suffix(
        _OPEN_DOMAIN if kind == "open" else _EVALUATE_DOMAIN,
        value,
    )
    prefix = f"closure-{kind}:{suffix}"
    return (
        f"cli:run.daemon:lifecycle:{kind}:{suffix}",
        TransitionContext(
            transition_id=f"transition:cli:run.daemon:lifecycle:{kind}:{suffix}",
            work_item_id=f"lifecycle-work:{prefix}",
            activation_id=f"lifecycle-activation:{prefix}",
            run_id=f"lifecycle-run:{prefix}",
            claim_id=f"lifecycle-claim:{prefix}",
            fencing_token=f"lifecycle-fence:{prefix}",
        ),
    )


def _suffix(domain: bytes, value: Mapping[str, object]) -> str:
    return sha256(domain + canonical_authority_mapping_bytes(value)).hexdigest()[:32]


def _plan_ref_value(plan_ref: PlanRef) -> dict[str, object]:
    return {
        "plan_id": plan_ref.plan_id,
        "authority_fingerprint": plan_ref.authority_fingerprint,
        "plan_format_version": plan_ref.plan_format_version,
    }


def _target_key_value(key: ClosureLogicalTargetKey) -> dict[str, object]:
    return {
        "selected_plan_ref": _plan_ref_value(key.selected_plan_ref),
        "completion_behavior_id": key.completion_behavior_id,
        "lineage_id": key.lineage_id,
        "root_source_kind": key.root_source_kind,
        "root_source_id": key.root_source_id,
        "root_work_item_id": key.root_work_item_id,
    }


def closure_root_source_matches(
    work_item: WorkItem,
    *,
    root_source_kind: str,
    root_source_id: str,
) -> bool:
    source = work_item.payload.get("root_source")
    return isinstance(source, Mapping) and (
        source.get("kind"),
        source.get("source_id"),
    ) == (root_source_kind, root_source_id)


def closure_creator_refusal(state: RuntimeState, expected_input: object) -> str | None:
    input_id = expected_input.input_id
    receipt = state.receipts.get(input_id)
    if receipt is None:
        return "invalid_closure_creator"
    try:
        digest = input_payload_digest(expected_input)
    except (RecursionError, TypeError, ValueError):
        return "invalid_closure_creator"
    transition = next(
        (item for item in state.transitions if item.record_id == receipt.transition_id),
        None,
    )
    if transition is None or not (
        receipt.receipt_ref.input_id == input_id
        and receipt.receipt_ref.input_payload_digest == digest
        and receipt.accepted
        and receipt.refusal_reason is None
        and transition.accepted
        and (transition.input_id, transition.input_kind, transition.input_family)
        == (input_id, type(expected_input).input_kind, input_family(expected_input))
    ):
        return "invalid_closure_creator"
    return None


def closure_enqueue_creator_refusal(
    state: RuntimeState,
    command: EnqueueWork | WorkItem,
    *,
    require_default_plan: bool = False,
) -> str | None:
    if isinstance(command, WorkItem):
        command = EnqueueWork(
            command.created_by_input_id, command.queue_family_id, command.payload
        )
    if closure_creator_refusal(state, command) is not None:
        return "invalid_closure_creator"
    work_items, activations = (
        tuple(item for item in values if item.created_by_input_id == command.input_id)
        for values in (state.work_items.values(), state.activations.values())
    )
    if len(work_items) != 1 or len(activations) != 1:
        return (
            "missing_or_ambiguous_work_item"
            if len(work_items) != 1
            else "missing_or_ambiguous_activation"
        )
    work_item, activation = work_items[0], activations[0]
    if not (
        activation.work_item_id == work_item.ref.work_item_id
        and work_item.queue_family_id
        == activation.queue_family_id
        == command.queue_family_id
        and work_item.ref.plan_ref == activation.plan_ref
    ):
        return "enqueue_creator_relation_mismatch"
    if require_default_plan and state.default_plan_ref != work_item.ref.plan_ref:
        return "default_plan_mismatch"
    admitted = state.admitted_plans.get(work_item.ref.plan_ref.authority_fingerprint)
    if (
        admitted is None
        or admitted.plan_ref != work_item.ref.plan_ref
        or not verify_authority_fingerprint(
            admitted.selected_plan, work_item.ref.plan_ref.authority_fingerprint
        )
    ):
        return "selected_plan_authority_mismatch"
    route = next(
        (
            route
            for route in admitted.selected_plan.external_enqueue_routes
            if route.queue_family_id == command.queue_family_id
        ),
        None,
    )
    cached_route = admitted.external_enqueue_routes.get(command.queue_family_id)
    if route is None or cached_route is None:
        return "missing_external_enqueue_route"
    if (
        cached_route.queue_family_id,
        cached_route.graph_node_id,
        cached_route.stage_kind_id,
        cached_route.runner_binding_id,
        cached_route.payload_schema_id,
    ) != (
        route.queue_family_id,
        route.graph_node_id,
        route.stage_kind_id,
        route.runner_binding_id,
        route.payload_schema_id,
    ):
        return "selected_route_authority_mismatch"
    if activation.graph_node_id != route.graph_node_id:
        return "selected_route_graph_node_mismatch"
    if activation.stage_kind_id != route.stage_kind_id:
        return "selected_route_stage_kind_mismatch"
    if activation.runner_binding_id != route.runner_binding_id:
        return "selected_route_runner_binding_mismatch"
    return None


def closure_evidence_anchor(
    snapshot: object,
    *,
    target: ClosureTargetRecord,
    root_item: WorkItem | None = None,
    prior_artifact: ArtifactRecord | None = None,
) -> Mapping[str, object] | None:
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("record_kind") != "closure_evidence_snapshot"
        or snapshot.get("schema_version") != 1
        or not isinstance(snapshot.get("evidence_artifacts"), (tuple, list))
    ):
        return None
    identity = tuple(
        snapshot.get(field)
        for field in ("closure_target_id", "selected_plan_fingerprint", "lineage_id")
    )
    if identity != (
        target.closure_target_id,
        target.selected_plan_ref.authority_fingerprint,
        target.lineage_id,
    ) or any(not isinstance(value, str) or not value.strip() for value in identity):
        return None
    root, prior = snapshot.get("root_contract"), snapshot.get("prior_verdict")
    if not isinstance(root, Mapping) or not (
        prior is None or isinstance(prior, Mapping)
    ):
        return None
    contracts = (root,) if prior is None else (root, prior)
    for contract in contracts:
        payload = contract.get("payload")
        if not isinstance(payload, Mapping) or contract.get(
            "payload_digest"
        ) != artifact_payload_digest(payload):
            return None
    source = contracts[-1]
    if snapshot.get("freshness_anchor_digest") != source.get("payload_digest"):
        return None
    if root_item is not None and (
        root.get("work_item_id"),
        root.get("payload"),
        root.get("payload_digest"),
    ) != (
        root_item.ref.work_item_id,
        root_item.payload,
        artifact_payload_digest(root_item.payload),
    ):
        return None
    if prior is None:
        return {"kind": "root_contract", "payload_digest": root["payload_digest"]}
    prior_id = prior.get("artifact_id")
    if not isinstance(prior_id, str) or not prior_id.strip():
        return None
    if prior_artifact is not None and (
        (prior.get("payload"), prior.get("payload_digest"))
        != (prior_artifact.payload, prior_artifact.payload_digest)
        or prior_artifact.payload_digest
        != artifact_payload_digest(prior_artifact.payload)
        or prior_artifact.payload.get("closure_target_id")
        != snapshot["closure_target_id"]
    ):
        return None
    return {
        "kind": "prior_verdict",
        "artifact_id": prior_id,
        "payload_digest": prior["payload_digest"],
    }


def assess_closure_readiness(
    state: RuntimeState,
    *,
    lineage_id: str,
    plan_ref: PlanRef,
    target_key: ClosureLogicalTargetKey,
) -> ClosureReadiness:
    work = tuple(
        sorted(
            (
                item
                for item in state.work_items.values()
                if item.ref.plan_ref == plan_ref and item.lineage_id == lineage_id
            ),
            key=lambda item: item.ref.work_item_id,
        )
    )
    ids = tuple(item.ref.work_item_id for item in work)
    statuses = tuple(_assess_work_item(state, item) for item in work)
    anchor = sha256(
        canonical_authority_mapping_bytes(
            _readiness_witness(
                state,
                plan_ref=plan_ref,
                lineage_id=lineage_id,
                target_key=target_key,
                work=work,
                statuses=statuses,
            )
        )
    ).hexdigest()
    if "corrupt" in statuses:
        return ClosureReadiness(
            "corrupt", anchor, ids, "work_item:" + ids[statuses.index("corrupt")]
        )
    pending = (
        any(
            item.status == "active"
            for item in state.operator_waits.values()
            if item.selected_plan_ref == plan_ref and item.lineage_id == lineage_id
        )
        or any(
            item.consumed_input_id is None
            for item in state.cooldown_waits.values()
            if item.plan_ref == plan_ref and item.lineage_id == lineage_id
        )
        or any(
            item.status == "active"
            for item in state.lineage_quarantines.values()
            if item.selected_plan_ref == plan_ref and item.lineage_id == lineage_id
        )
        or any(
            item.phase in {"active_recovery", "quarantine_eligible"}
            for item in state.recovery_attempts.values()
            if item.plan_ref == plan_ref and item.lineage_id == lineage_id
        )
    )
    return ClosureReadiness(
        "pending" if "pending" in statuses or pending else "settled", anchor, ids
    )


def _readiness_witness(
    state: RuntimeState,
    *,
    plan_ref: PlanRef,
    lineage_id: str,
    target_key: ClosureLogicalTargetKey,
    work: tuple[WorkItem, ...],
    statuses: tuple[ClosureReadinessStatus, ...],
) -> dict[str, object]:
    work_ids = frozenset(item.ref.work_item_id for item in work)
    relations: list[tuple[object, ...]] = [
        (
            "work",
            item.ref.work_item_id,
            item.ref.generation,
            item.created_by_input_id,
            status,
        )
        for item, status in zip(work, statuses)
    ]

    def add(kind: str, rows: Iterable[tuple[object, ...]]) -> None:
        relations.extend((kind, *row) for row in rows)

    add(
        "closed_work",
        (
            (item.record_id, item.work_item_id, item.created_by_input_id)
            for item in state.closed_work_items.values()
            if item.work_item_id in work_ids
        ),
    )
    add(
        "queue_closure",
        (
            (
                item.closure_id,
                item.target_kind,
                item.target_id,
                item.created_by_input_id,
            )
            for item in state.queue_closures.values()
            if item.selected_plan_ref == plan_ref
            and (
                item.target_kind == "lineage"
                and item.target_id == lineage_id
                or bool(work_ids.intersection(item.closed_work_item_ids))
            )
        ),
    )
    activations = tuple(
        item for item in state.activations.values() if item.work_item_id in work_ids
    )
    add(
        "activation",
        (
            (
                item.activation_id,
                item.work_item_id,
                item.generation,
                item.claimed_by_run_id,
            )
            for item in activations
        ),
    )
    activation_ids = frozenset(item.activation_id for item in activations)
    for run in state.runs.values():
        if run.work_item_id not in work_ids and run.activation_id not in activation_ids:
            continue
        session = state.runner_sessions.get(run.current_session_id or "")
        observations = tuple(
            sorted(
                (item.observation_id, item.created_by_input_id)
                for item in state.runner_observations.values()
                if item.run_id == run.run_ref.run_id
            )
        )
        relations.append(
            (
                "run",
                run.run_ref.run_id,
                run.work_item_id,
                run.activation_id,
                run.run_ref.generation,
                observations,
                run.current_session_id,
                session.state if session is not None else None,
                session.cleanup_disposition if session is not None else None,
            )
        )
    add(
        "operator_wait",
        (
            (item.wait_id, item.status, item.created_input_id, item.resolved_input_id)
            for item in state.operator_waits.values()
            if item.selected_plan_ref == plan_ref and item.lineage_id == lineage_id
        ),
    )
    add(
        "cooldown_wait",
        (
            (item.wait_id, item.created_input_id, item.consumed_input_id)
            for item in state.cooldown_waits.values()
            if item.plan_ref == plan_ref and item.lineage_id == lineage_id
        ),
    )
    add(
        "recovery",
        (
            (
                item.record_id,
                item.phase,
                item.created_by_input_id,
                item.updated_by_input_id,
            )
            for item in state.recovery_attempts.values()
            if item.plan_ref == plan_ref and item.lineage_id == lineage_id
        ),
    )
    add(
        "lineage_quarantine",
        (
            (
                item.quarantine_id,
                item.status,
                item.created_input_id,
                item.superseded_input_id,
            )
            for item in state.lineage_quarantines.values()
            if item.selected_plan_ref == plan_ref and item.lineage_id == lineage_id
        ),
    )
    add(
        "remediation",
        (
            (
                item.record_id,
                item.source_artifact_id,
                item.target_work_item_id,
                item.target_activation_id,
                item.created_by_input_id,
            )
            for item in state.remediation_work_records.values()
            if item.selected_plan_ref == plan_ref and item.lineage_id == lineage_id
        ),
    )
    add(
        "closure_target",
        (
            (
                item.closure_target_id,
                item.status,
                item.opened_by_input_id,
                item.closed_by_record_id,
            )
            for item in state.closure_targets.values()
            if item.selected_plan_ref == plan_ref and item.lineage_id == lineage_id
        ),
    )
    evaluations = tuple(
        item
        for item in state.closure_evaluations.values()
        if item.selected_plan_ref == plan_ref and item.lineage_id == lineage_id
    )
    add(
        "closure_evaluation",
        (
            (
                item.record_id,
                item.target_work_item_id,
                item.target_activation_id,
                item.created_by_input_id,
            )
            for item in evaluations
        ),
    )
    evaluation_work_ids = frozenset(item.target_work_item_id for item in evaluations)
    add(
        "closure_verdict",
        (
            (item.artifact_id, item.source_action_id, item.created_by_input_id)
            for item in state.artifacts.values()
            if item.work_item_id in evaluation_work_ids
        ),
    )
    add(
        "closure_terminal",
        (
            (item.record_id, item.source_artifact_id, item.created_by_input_id)
            for item in state.closure_terminal_records.values()
            if item.selected_plan_ref == plan_ref and item.lineage_id == lineage_id
        ),
    )
    add(
        "closure_block",
        (
            (
                item.record_id,
                item.source_run_id,
                item.operator_required,
                item.created_by_input_id,
            )
            for item in state.closure_blocked_records.values()
            if item.selected_plan_ref == plan_ref and item.lineage_id == lineage_id
        ),
    )
    return {
        "plan_ref": _plan_ref_value(plan_ref),
        "lineage_id": lineage_id,
        "logical_target_key": _target_key_value(target_key),
        "relations": tuple(sorted(relations)),
    }


def _assess_work_item(state: RuntimeState, work: WorkItem) -> ClosureReadinessStatus:
    work_id = work.ref.work_item_id
    closed_records = tuple(
        (key, item)
        for key, item in state.closed_work_items.items()
        if key == work_id or item.work_item_id == work_id
    )
    queue_closures = tuple(
        item
        for item in state.queue_closures.values()
        if work_id in item.closed_work_item_ids
    )
    if any(
        key != work_id or item.work_item_id != work_id for key, item in closed_records
    ) or any(
        item.selected_plan_ref != work.ref.plan_ref
        or (item.target_kind == "work_item" and item.target_id != work_id)
        for item in queue_closures
    ):
        return "corrupt"
    closed = bool(closed_records or queue_closures)
    activations = tuple(
        item
        for item in state.activations.values()
        if item.work_item_id == work.ref.work_item_id
    )
    statuses = tuple(
        _activation_status(state, work, item, closed) for item in activations
    )
    if "corrupt" in statuses:
        return "corrupt"
    return (
        "pending" if "pending" in statuses or not (closed or activations) else "settled"
    )


def _claimed_run(
    state: RuntimeState, work: WorkItem, activation: Activation
) -> tuple[RunRecord | None, str | None]:
    runs = tuple(
        item
        for item in state.runs.values()
        if item.activation_id == activation.activation_id
    )
    if activation.claimed_by_run_id is None:
        return None, "closure_evaluator_claim_invalid" if runs else None
    if len(runs) != 1:
        return None, "closure_evaluator_claim_invalid"
    run = runs[0]
    if (
        activation.claimed_by_run_id != run.run_ref.run_id
        or run.work_item_id != work.ref.work_item_id
        or run.run_ref.work_item_id != work.ref.work_item_id
        or run.run_ref.plan_ref != work.ref.plan_ref
    ):
        return None, "closure_evaluator_claim_invalid"
    return run, None


def _activation_status(
    state: RuntimeState, work: WorkItem, activation: Activation, closed: bool
) -> ClosureReadinessStatus:
    if (activation.plan_ref, activation.lineage_id) != (
        work.ref.plan_ref,
        work.lineage_id,
    ):
        return "corrupt"
    run, refusal = _claimed_run(state, work, activation)
    if refusal:
        return "corrupt"
    if run is None:
        return "pending"
    observations = tuple(
        item
        for item in state.runner_observations.values()
        if item.run_id == run.run_ref.run_id
    )
    if len(observations) != 1:
        return "pending" if not closed and not observations else "corrupt"
    if isinstance(
        authenticate_runner_observation(state, observations[0]),
        ObservationPolicyDiagnostic,
    ):
        return "corrupt"
    if run.current_session_id is None:
        return "settled"
    session = state.runner_sessions.get(run.current_session_id)
    if session is None or session.run_id != run.run_ref.run_id:
        return "corrupt"
    settled = (
        session.state in _SETTLED_SESSION_STATES
        and session.cleanup_disposition in _SETTLED_CLEANUP_DISPOSITIONS
    )
    return "settled" if settled else "corrupt" if closed else "pending"


def _evaluation_parts(
    state: RuntimeState,
    evaluation: ClosureEvaluationRecord,
    target: ClosureTargetRecord,
    behavior: CompletionBehaviorDeclaration,
) -> tuple[WorkItem | None, Activation | None, str | None]:
    work = state.work_items.get(evaluation.target_work_item_id)
    activation = state.activations.get(evaluation.target_activation_id)
    if work is None or activation is None:
        return work, activation, "closure_evaluation_relation_missing"
    plan, lineage = target.selected_plan_ref, target.lineage_id
    if (
        evaluation.record_id != f"closure-evaluator:{evaluation.target_activation_id}"
        or (
            evaluation.closure_target_id,
            evaluation.selected_plan_ref,
            evaluation.lineage_id,
            evaluation.completion_behavior_id,
            evaluation.request_kind,
        )
        != (
            target.closure_target_id,
            plan,
            lineage,
            behavior.id,
            behavior.request_kind,
        )
        or len({evaluation.created_by_input_id, work.created_by_input_id,
                activation.created_by_input_id}) != 1
        or activation.work_item_id != evaluation.target_work_item_id
        or (work.ref.plan_ref, activation.plan_ref) != (plan, plan)
        or (work.lineage_id, activation.lineage_id) != (lineage, lineage)
        or (work.queue_family_id, activation.queue_family_id)
        != (behavior.request_queue_family_id,) * 2
        or (
            activation.graph_node_id,
            activation.stage_kind_id,
            activation.runner_binding_id,
        )
        != (
            behavior.target_graph_node_id,
            behavior.target_stage_kind_id,
            behavior.runner_binding_id,
        )
    ):
        return work, activation, "closure_evaluation_relation_missing"
    _run, refusal = _claimed_run(state, work, activation)
    return work, activation, refusal


def _verdict_artifact(
    state, *, work_item_id: str, schema_id, activation_id: str, action_ids
) -> tuple[ArtifactRecord | None, str | None]:
    artifacts = tuple(
        item
        for item in state.artifacts.values()
        if item.work_item_id == work_item_id and item.schema_id == schema_id
    )
    if len(artifacts) != 1:
        return None, "relation"
    artifact = artifacts[0]
    provenance = authenticate_artifact_provenance(state, artifact)
    if isinstance(provenance, ObservationPolicyDiagnostic):
        return artifact, "provenance"
    return artifact, None if (
        provenance.observation.run.activation_id == activation_id
        and artifact.source_action_id in action_ids
    ) else "provenance"


def closure_remediation_refusal(
    state: RuntimeState,
    *,
    target: ClosureTargetRecord,
    behavior: CompletionBehaviorDeclaration,
    record,
    source_artifact: ArtifactRecord,
) -> str | None:
    plan_ref = target.selected_plan_ref
    admitted = state.admitted_plans.get(plan_ref.authority_fingerprint)
    if admitted is None or (
        admitted.plan_ref,
        record.selected_plan_ref,
        record.lineage_id,
    ) != (plan_ref, plan_ref, target.lineage_id):
        return "closure_remediation_plan_ref_invalid"
    policy = next(
        (
            item
            for item in admitted.selected_plan.remediation_policies
            if item.id == behavior.remediation_policy_id
        ),
        None,
    )
    if policy is None:
        return "closure_remediation_policy_invalid"
    if (
        record.remediation_policy_id,
        record.source_action_id,
        record.source_artifact_id,
    ) != (policy.id, behavior.gap_action_id, source_artifact.artifact_id) or (
        policy.dedupe_key == "closure_target_and_source_artifact"
        and record.dedupe_key
        != f"{record.closure_target_id}:{record.source_artifact_id}"
    ):
        return "closure_remediation_policy_invalid"
    provenance = authenticate_artifact_provenance(state, source_artifact)
    if isinstance(provenance, ObservationPolicyDiagnostic):
        return "closure_remediation_creator_invalid"
    if (
        record.created_by_input_id != source_artifact.created_by_input_id
        or record.source_run_id != source_artifact.source_run_id
        or record.source_action_id != source_artifact.source_action_id
    ):
        return "closure_remediation_creator_invalid"
    source_work = provenance.observation.work_item
    source_activation = provenance.observation.activation
    evaluations = tuple(
        item
        for item in state.closure_evaluations.values()
        if item.closure_target_id == target.closure_target_id
        and item.target_work_item_id == source_work.ref.work_item_id
        and item.target_activation_id == source_activation.activation_id
    )
    if (
        len(evaluations) != 1
        or _evaluation_parts(state, evaluations[0], target, behavior)[2] is not None
    ):
        return "closure_remediation_source_invalid"
    work = state.work_items.get(record.target_work_item_id)
    activation = state.activations.get(record.target_activation_id)
    if work is None or activation is None:
        return "closure_remediation_target_invalid"
    actual = (
        (activation.work_item_id, work.ref.plan_ref, activation.plan_ref),
        (work.lineage_id, activation.lineage_id),
        (work.created_by_input_id, activation.created_by_input_id),
        (work.queue_family_id, activation.queue_family_id),
        (
            activation.stage_kind_id,
            activation.graph_node_id,
            activation.runner_binding_id,
        ),
    )
    expected = (
        (record.target_work_item_id, plan_ref, plan_ref),
        (target.lineage_id,) * 2,
        (record.created_by_input_id,) * 2,
        (policy.target_queue_family_id,) * 2,
        (
            policy.target_stage_kind_id,
            policy.target_graph_node_id,
            policy.target_runner_binding_id,
        ),
    )
    if actual != expected:
        return "closure_remediation_target_invalid"
    return None


def closure_target_progress(
    state: RuntimeState,
    *,
    target: ClosureTargetRecord,
    behavior: CompletionBehaviorDeclaration,
) -> ClosureProgress:
    key = closure_target_key_for(target)
    target_id = target.closure_target_id
    if target_id != closure_target_id(key):
        return _bad("closure_target_identity_mismatch")
    if target.status not in {"open", "closed"}:
        return _bad("invalid_closure_target_status")
    if (
        overlay_error := closure_block_overlay_error(
            state, target=target, behavior=behavior
        )
    ) is not None:
        return _bad(overlay_error)
    if target.status == "closed":
        terminals = tuple(
            item
            for item in state.closure_terminal_records.values()
            if item.closure_target_id == target_id
        )
        if len(terminals) != 1:
            return _bad("closure_terminal_relation_invalid")
        terminal, run = terminals[0], state.runs.get(terminals[0].source_run_id)
        evaluations = tuple(
            item
            for item in state.closure_evaluations.values()
            if run is not None
            and (item.target_work_item_id, item.target_activation_id)
            == (run.work_item_id, run.activation_id)
        )
        if run is None or len(evaluations) != 1:
            return _bad("closure_terminal_relation_invalid")
        evaluation = evaluations[0]
        work, _activation, refusal = _evaluation_parts(
            state, evaluation, target, behavior
        )
        artifact, artifact_error = _verdict_artifact(
            state,
            work_item_id=evaluation.target_work_item_id,
            schema_id=behavior.verdict_artifact_schema_id,
            activation_id=evaluation.target_activation_id,
            action_ids=(behavior.pass_action_id,),
        )
        if refusal is not None or artifact_error is not None:
            return _bad("closure_terminal_relation_invalid")
        if (
            terminal.record_id != target.closed_by_record_id
            or (terminal.completion_behavior_id, terminal.terminal_kind)
            != (behavior.id, "passed")
            or terminal.selected_plan_ref != target.selected_plan_ref
            or terminal.lineage_id != target.lineage_id
            or artifact.artifact_id != terminal.source_artifact_id
            or (
                artifact.created_by_input_id,
                artifact.source_run_id,
                artifact.source_action_id,
            )
            != (
                terminal.created_by_input_id,
                terminal.source_run_id,
                behavior.pass_action_id,
            )
        ):
            return _bad("closure_terminal_relation_invalid")
        return ClosureProgress("closed")
    positions = {item.record_id: index for index, item in enumerate(state.transitions)}

    def position(input_id: str) -> int:
        receipt = state.receipts.get(input_id)
        return positions.get(receipt.transition_id if receipt else input_id, -1)

    evaluations = tuple(
        sorted(
            (
                item
                for item in state.closure_evaluations.values()
                if item.closure_target_id == target_id
            ),
            key=lambda item: position(item.created_by_input_id),
        )
    )
    seen: set[tuple[object, ...]] = set()
    artifact: ArtifactRecord | None = None
    for evaluation in evaluations:
        work, _activation, refusal = _evaluation_parts(
            state, evaluation, target, behavior
        )
        if refusal is not None:
            return _bad(refusal)
        snapshot = work.payload.get("closure_evidence_snapshot")
        prior = snapshot.get("prior_verdict") if isinstance(snapshot, Mapping) else None
        prior_artifact = (
            state.artifacts.get(prior.get("artifact_id"))
            if isinstance(prior, Mapping)
            else None
        )
        if isinstance(prior, Mapping) and prior_artifact is None:
            return _bad("closure_snapshot_invalid")
        anchor = closure_evidence_anchor(
            snapshot,
            root_item=state.work_items.get(target.closure_root_work_item_id or ""),
            prior_artifact=prior_artifact,
            target=target,
        )
        if anchor is None:
            return _bad("closure_snapshot_invalid")
        marker = tuple(sorted(anchor.items()))
        if marker in seen:
            return _bad("duplicate_closure_evaluation_anchor")
        seen.add(marker)
        status = _assess_work_item(state, work)
        if status == "corrupt":
            return _bad("closure_evaluation_readiness_corrupt")
        if status == "pending":
            return ClosureProgress("pending")
        verdict, artifact_error = _verdict_artifact(
            state,
            work_item_id=work.ref.work_item_id,
            schema_id=behavior.verdict_artifact_schema_id,
            activation_id=evaluation.target_activation_id,
            action_ids=(
                behavior.pass_action_id,
                behavior.gap_action_id,
                behavior.blocked_action_id,
            ),
        )
        if artifact_error == "relation":
            return _bad("closure_verdict_artifact_relation_invalid")
        if artifact_error is not None:
            return _bad("closure_verdict_provenance_invalid", verdict.artifact_id)
        artifact = verdict
    if artifact is None:
        root = state.work_items.get(target.closure_root_work_item_id or "")
        if root is None:
            return _bad("closure_root_missing")
        return ClosureProgress(
            "ready",
            evidence_anchor={
                "kind": "root_contract",
                "payload_digest": artifact_payload_digest(root.payload),
            },
        )
    if artifact.source_action_id == behavior.pass_action_id:
        return _bad("closure_pass_target_not_closed", artifact.artifact_id)
    if artifact.source_action_id == behavior.blocked_action_id:
        return ClosureProgress("blocked", source_artifact_id=artifact.artifact_id)
    if artifact.source_action_id != behavior.gap_action_id:
        return _bad("closure_verdict_action_invalid", artifact.artifact_id)
    remediation = tuple(
        item
        for item in state.remediation_work_records.values()
        if item.closure_target_id == target.closure_target_id
        and item.source_artifact_id == artifact.artifact_id
    )
    if len(remediation) != 1:
        return _bad("closure_remediation_relation_invalid", artifact.artifact_id)
    record = remediation[0]
    refusal = closure_remediation_refusal(
        state, target=target, behavior=behavior, record=record, source_artifact=artifact
    )
    if refusal is not None:
        return _bad(refusal, artifact.artifact_id)
    work = state.work_items[record.target_work_item_id]
    status = _assess_work_item(state, work)
    if status == "corrupt":
        return _bad("closure_remediation_readiness_corrupt", artifact.artifact_id)
    if status == "pending":
        return ClosureProgress("pending")
    return ClosureProgress(
        "ready",
        source_artifact_id=artifact.artifact_id,
        evidence_anchor={
            "kind": "prior_verdict",
            "artifact_id": artifact.artifact_id,
            "payload_digest": artifact_payload_digest(artifact.payload),
        },
    )


def closure_block_overlay_error(
    state: RuntimeState,
    *,
    target: ClosureTargetRecord | None = None,
    behavior: CompletionBehaviorDeclaration | None = None,
) -> str | None:
    invalid = "invalid_closure_block_overlay"
    records = tuple(
        item
        for item in state.closure_blocked_records.values()
        if target is None or item.closure_target_id == target.closure_target_id
    )
    if len(records) != len({item.closure_target_id for item in records}):
        return "duplicate_closure_block_overlay"
    for block in records:
        current = target or state.closure_targets.get(block.closure_target_id)
        if current is None:
            return invalid
        selected = behavior
        if selected is None:
            admitted = state.admitted_plans.get(
                current.selected_plan_ref.authority_fingerprint
            )
            if admitted is None:
                return invalid
            selected = next(
                (
                    item
                    for item in admitted.selected_plan.completion_behaviors
                    if item.id == current.completion_behavior_id
                ),
                None,
            )
        if selected is None:
            return invalid
        if current.status != "open" or not block.operator_required:
            return invalid
        if (
            block.selected_plan_ref,
            block.completion_behavior_id,
            block.lineage_id,
            block.source_action_id,
        ) != (
            current.selected_plan_ref,
            current.completion_behavior_id,
            current.lineage_id,
            selected.blocked_action_id,
        ):
            return invalid
        observations = tuple(
            item
            for item in state.runner_observations.values()
            if item.created_by_input_id == block.created_by_input_id
            and item.run_id == block.source_run_id
        )
        if len(observations) != 1:
            return invalid
        authenticated = authenticate_runner_observation(state, observations[0])
        if isinstance(authenticated, ObservationPolicyDiagnostic):
            return invalid
        if (
            authenticated.action.id != block.source_action_id
            or authenticated.run.run_ref.plan_ref != current.selected_plan_ref
            or block.record_id
            != f"closure-blocked:{authenticated.transition.record_id}"
        ):
            return invalid
        run, activation = authenticated.run, authenticated.activation
        evaluations = tuple(
            item
            for item in state.closure_evaluations.values()
            if item.closure_target_id == current.closure_target_id
            and item.target_work_item_id == run.work_item_id
            and item.target_activation_id == activation.activation_id
        )
        if len(evaluations) != 1:
            return invalid
        if _evaluation_parts(state, evaluations[0], current, selected)[2]:
            return invalid
        artifact, artifact_error = _verdict_artifact(
            state,
            work_item_id=run.work_item_id,
            schema_id=selected.verdict_artifact_schema_id,
            activation_id=activation.activation_id,
            action_ids=(block.source_action_id,),
        )
        if artifact_error:
            return invalid
        if (
            artifact.source_action_id != block.source_action_id
            or artifact.source_run_id != block.source_run_id
            or artifact.created_by_input_id != block.created_by_input_id
        ):
            return invalid
    return None


def _bad(detail: str, artifact_id: str | None = None) -> ClosureProgress:
    return ClosureProgress("corrupt", detail, artifact_id)
