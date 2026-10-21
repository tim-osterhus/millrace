"""Durable provenance of selected closure-evaluator work.

Pure history checks shared by the kernel closure lifecycle, durable-load
validation and runtime-failure authority. An evaluator activation is authentic
only when its whole immutable closure origin agrees:

- exactly one accepted ``EvaluateCompletionBehavior`` creator (receipt, one
  transition, payload digest) with exactly one paired accepted governance event
  and trace owned by the evaluator work;
- one ``ClosureEvaluationRecord`` whose work, activation, plan, lineage, queue,
  stage, graph node, runner binding and claim match the selected completion
  behavior;
- the closure target's own accepted ``OpenClosureTarget`` creator (with paired
  audit owned by the closure root) and selected root contract;
- the evaluator's sealed ``closure_evidence_snapshot``: identity, immutable root
  contract, the prior verdict and the ordered evidence artifacts, recaptured
  from accepted history before the creator transition, each artifact accepted by
  the canonical ``authenticate_artifact_provenance`` and the prior verdict emitted
  by the exact original or recorded-source-returned evaluator of its evaluation.

Everything is checked against recorded history; current target status or
readiness is never re-demanded, so authority survives lawful failure, recovery
and closure. The static evaluator target tuple alone never grants authority.
"""

from __future__ import annotations

from collections.abc import Mapping

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    CompletionBehaviorDeclaration,
    SelectedCompiledPlan,
    verify_authority_fingerprint,
)
from millrace.contracts.observation_policy import (
    AuthenticatedRunnerObservation,
    ObservationPolicyDiagnostic,
    authenticate_artifact_provenance,
    authenticate_runner_observation,
    observation_artifact_refusal,
)
from millrace.contracts.runtime_failure_history import (
    claimed_activation_matches,
    recorded_source_return_source,
)
from millrace.contracts.state import (
    Activation,
    ArtifactRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    RunRecord,
    RuntimeState,
    WorkItem,
)
from millrace.contracts.transition import (
    EnqueueWork,
    EvaluateCompletionBehavior,
    OpenClosureTarget,
    TransitionInput,
    artifact_payload_digest,
    canonical_authority_mapping_bytes,
    input_family,
    input_payload_digest,
)

_CLOSURE_AUTHORITY_SOURCE = "completion_behavior"


def closure_creator_refusal(
    state: RuntimeState, expected_input: TransitionInput
) -> str | None:
    """Exactly one accepted creator transition bound to its receipt and digest."""
    input_id = expected_input.input_id
    receipt = state.receipts.get(input_id)
    if receipt is None:
        return "invalid_closure_creator"
    try:
        digest = input_payload_digest(expected_input)
    except (RecursionError, TypeError, ValueError):
        return "invalid_closure_creator"
    # Refused replays of the same input id are lawful history; the accepted
    # creator transition and its receipt-referenced record must both be unique.
    accepted = [
        item
        for item in state.transitions
        if item.input_id == input_id and item.accepted
    ]
    by_record = [
        item for item in state.transitions if item.record_id == receipt.transition_id
    ]
    if len(accepted) != 1 or len(by_record) != 1 or accepted[0] is not by_record[0]:
        return "invalid_closure_creator"
    transition = accepted[0]
    if not (
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


def closure_creator_audit_refusal(
    state: RuntimeState,
    expected_input: TransitionInput,
    *,
    plan_fingerprint: str,
    work_item_id: str | None,
) -> str | None:
    """Exactly one paired accepted governance event and trace for the creator."""
    receipt = state.receipts.get(expected_input.input_id)
    if receipt is None:
        return "invalid_closure_creator_audit"
    expected = (
        expected_input.input_id,
        type(expected_input).input_kind,
        input_family(expected_input),
        "accepted",
        plan_fingerprint,
        work_item_id,
        None,
        None,
        _CLOSURE_AUTHORITY_SOURCE,
        None,
    )
    for records, suffix in (
        (state.governance_events, "governance"),
        (state.traces, "trace"),
    ):
        # Refused replays may share the input id; any second ACCEPTED audit for the
        # same creator input is duplicate authority, whatever its record id.
        accepted = [
            record
            for record in records
            if record.input_id == expected_input.input_id
            and record.disposition == "accepted"
        ]
        linked = [
            record
            for record in records
            if record.record_id == f"{receipt.transition_id}:{suffix}"
        ]
        if len(accepted) != 1 or len(linked) != 1 or accepted[0] is not linked[0]:
            return "invalid_closure_creator_audit"
        record = linked[0]
        if (
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
        ) != expected:
            return "invalid_closure_creator_audit"
    return None


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
    work_items = tuple(
        item
        for item in state.work_items.values()
        if item.created_by_input_id == command.input_id
    )
    activations = tuple(
        item
        for item in state.activations.values()
        if item.created_by_input_id == command.input_id
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


def closure_artifact_envelope(artifact: ArtifactRecord) -> Mapping[str, AuthorityValue]:
    """The sealed envelope of one artifact captured into a closure snapshot."""
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_schema_id": str(artifact.schema_id),
        "payload_digest": artifact.payload_digest,
        "payload": artifact.payload,
        "source_work_item_id": artifact.work_item_id,
        "source_run_id": artifact.source_run_id,
        "source_action_id": str(artifact.source_action_id),
        "source_stage_kind_id": str(artifact.source_stage_kind_id),
        "transition_id": artifact.transition_id,
    }


def _captured_artifact_accepted(state: RuntimeState, artifact: ArtifactRecord) -> bool:
    """The canonical artifact provenance authentication accepts the artifact."""
    return not isinstance(
        authenticate_artifact_provenance(state, artifact), ObservationPolicyDiagnostic
    )


def closure_artifact_history_complete(
    state: RuntimeState,
    target: ClosureTargetRecord,
    *,
    before_transition_id: str | None = None,
) -> bool:
    """Accepted outputs in the target's history cannot vanish from the inventory."""
    positions = {item.record_id: index for index, item in enumerate(state.transitions)}
    ceiling = (
        None if before_transition_id is None else positions.get(before_transition_id)
    )
    if before_transition_id is not None and ceiling is None:
        return False
    for observation in state.runner_observations.values():
        run = state.runs.get(observation.run_id)
        work = None if run is None else state.work_items.get(run.work_item_id)
        if (
            run is None
            or work is None
            or run.run_ref.plan_ref != target.selected_plan_ref
            or work.lineage_id != target.lineage_id
        ):
            continue
        authenticated = authenticate_runner_observation(state, observation)
        if not isinstance(authenticated, AuthenticatedRunnerObservation):
            return False
        position = positions.get(authenticated.transition.record_id)
        if position is None:
            return False
        if ceiling is not None and position >= ceiling:
            continue
        if observation_artifact_refusal(state, authenticated) is not None:
            return False
    return True


def closure_sealed_evidence_matches(
    state: RuntimeState,
    behavior: CompletionBehaviorDeclaration,
    target: ClosureTargetRecord,
    evaluation: ClosureEvaluationRecord,
    snapshot: object,
) -> bool:
    """Whether the sealed prior verdict and evidence equal immutable capture history.

    Recomputes the selected capture rule over history strictly before the
    evaluation's accepted creator transition: the latest prior verdict of the
    target, then the ordered accepted evidence artifacts after it in the target's
    plan and lineage. Later lawful history is ignored; current readiness is never
    consulted.
    """
    if not isinstance(snapshot, Mapping):
        return False
    receipt = state.receipts.get(evaluation.created_by_input_id)
    positions = {item.record_id: index for index, item in enumerate(state.transitions)}
    ceiling = None if receipt is None else positions.get(receipt.transition_id)
    if ceiling is None or not closure_artifact_history_complete(
        state, target, before_transition_id=state.transitions[ceiling].record_id
    ):
        return False
    prior: tuple[int, ArtifactRecord] | None = None
    for other in state.closure_evaluations.values():
        if (
            other.target_work_item_id == evaluation.target_work_item_id
            or other.closure_target_id != target.closure_target_id
            or other.selected_plan_ref != target.selected_plan_ref
            or other.lineage_id != target.lineage_id
        ):
            continue
        verdicts = [
            (positions.get(artifact.transition_id), artifact)
            for artifact in state.artifacts.values()
            if artifact.work_item_id == other.target_work_item_id
            and artifact.schema_id == behavior.verdict_artifact_schema_id
        ]
        if any(position is None for position, _ in verdicts):
            return False
        earlier = [
            (position, artifact)
            for position, artifact in verdicts
            if position is not None and position < ceiling
        ]
        if len(earlier) > 1:
            return False
        if not earlier:
            continue
        verdict_position, verdict = earlier[0]
        source_run = state.runs.get(verdict.source_run_id)
        source_work = state.work_items.get(verdict.work_item_id)
        if (
            source_run is None
            or source_work is None
            or not state.transitions[verdict_position].accepted
            or closure_verdict_run_refusal(state, target, behavior, other, source_run)
            is not None
            or source_run.run_ref.plan_ref != target.selected_plan_ref
            or source_work.ref.plan_ref != target.selected_plan_ref
            or source_work.lineage_id != target.lineage_id
            or verdict.source_stage_kind_id != behavior.target_stage_kind_id
            or verdict.source_action_id
            not in {
                behavior.pass_action_id,
                behavior.gap_action_id,
                behavior.blocked_action_id,
            }
            or verdict.payload_digest != artifact_payload_digest(verdict.payload)
            or not _captured_artifact_accepted(state, verdict)
        ):
            return False
        if prior is None or verdict_position > prior[0]:
            prior = (verdict_position, verdict)
    sealed_prior = snapshot.get("prior_verdict")
    if prior is None or sealed_prior is None:
        if prior is not None or sealed_prior is not None:
            return False
    elif not _same_authority_value(sealed_prior, closure_artifact_envelope(prior[1])):
        return False
    evidence: list[Mapping[str, AuthorityValue]] = []
    for artifact in sorted(
        state.artifacts.values(),
        key=lambda item: positions.get(item.transition_id, -1),
    ):
        if (
            artifact.work_item_id == evaluation.target_work_item_id
            or artifact.schema_id not in behavior.evidence_artifact_schema_ids
        ):
            continue
        position = positions.get(artifact.transition_id)
        if position is None:
            return False
        if position >= ceiling:
            continue
        if not state.transitions[position].accepted:
            return False
        if prior is not None and position <= prior[0]:
            continue
        source_run = state.runs.get(artifact.source_run_id)
        source_work = state.work_items.get(artifact.work_item_id)
        if (
            source_run is None
            or source_work is None
            or source_run.run_ref.plan_ref != target.selected_plan_ref
            or source_run.work_item_id != artifact.work_item_id
            or source_work.ref.plan_ref != target.selected_plan_ref
            or source_work.lineage_id != target.lineage_id
        ):
            continue
        if artifact.payload_digest != artifact_payload_digest(
            artifact.payload
        ) or not _captured_artifact_accepted(state, artifact):
            return False
        evidence.append(closure_artifact_envelope(artifact))
    sealed = snapshot.get("evidence_artifacts")
    return (
        len(evidence) <= behavior.evidence_item_limit
        and isinstance(sealed, (tuple, list))
        and _same_authority_value(tuple(sealed), tuple(evidence))
    )


def _same_authority_value(sealed: object, recomputed: object) -> bool:
    try:
        return canonical_authority_mapping_bytes(
            {"value": sealed}
        ) == canonical_authority_mapping_bytes({"value": recomputed})
    except (RecursionError, TypeError, ValueError):
        return False


def claimed_evaluator_run(
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


def closure_evaluation_parts(
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
        or len(
            {
                evaluation.created_by_input_id,
                work.created_by_input_id,
                activation.created_by_input_id,
            }
        )
        != 1
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
    _run, refusal = claimed_evaluator_run(state, work, activation)
    return work, activation, refusal


def closure_target_origin_refusal(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    target: ClosureTargetRecord,
) -> str | None:
    """Authenticate a target from its accepted creator and selected root contract."""
    behaviors = [
        behavior
        for behavior in plan.completion_behaviors
        if behavior.id == target.completion_behavior_id
    ]
    if len(behaviors) != 1:
        return "closure_target_mismatch"
    behavior = behaviors[0]
    if (
        target.target_graph_node_id != behavior.target_graph_node_id
        or target.request_kind != behavior.request_kind
        or target.root_source_kind not in behavior.accepted_root_source_kinds
        or target.evidence_window
        != {"kind": "lineage", "lineage_id": target.lineage_id}
    ):
        return "closure_target_mismatch"
    creator = OpenClosureTarget(
        target.opened_by_input_id,
        target.selected_plan_ref,
        str(target.completion_behavior_id),
        target.closure_target_id,
        target.lineage_id,
        target.root_source_kind,
        target.root_source_id,
        target.closure_root_work_item_id,
        target.request_kind,
        target.target_graph_node_id,
        target.evidence_window,
    )
    if (
        closure_creator_refusal(state, creator) is not None
        or closure_creator_audit_refusal(
            state,
            creator,
            plan_fingerprint=target.selected_plan_ref.authority_fingerprint,
            work_item_id=target.closure_root_work_item_id,
        )
        is not None
    ):
        return "closure_target_mismatch"
    if behavior.root_source_resolution == "runtime_inventory":
        root = state.work_items.get(target.closure_root_work_item_id or "")
        root_queues = {route.queue_family_id for route in plan.external_enqueue_routes}
        if (
            root is None
            or root.queue_family_id not in root_queues
            or root.ref.plan_ref != target.selected_plan_ref
            or root.lineage_id != root.ref.work_item_id
            or root.lineage_id != target.lineage_id
            or not closure_root_source_matches(
                root,
                root_source_kind=target.root_source_kind,
                root_source_id=target.root_source_id,
            )
            or closure_enqueue_creator_refusal(state, root) is not None
        ):
            return "closure_target_mismatch"
    return None


def closure_sealed_snapshot_anchor(
    state: RuntimeState,
    target: ClosureTargetRecord,
    work: WorkItem,
) -> Mapping[str, object] | None:
    """Anchor of the sealed evaluator snapshot against immutable root/prior."""
    snapshot = work.payload.get("closure_evidence_snapshot")
    root_item = (
        None
        if target.closure_root_work_item_id is None
        else state.work_items.get(target.closure_root_work_item_id)
    )
    if target.closure_root_work_item_id is not None and root_item is None:
        return None
    prior = snapshot.get("prior_verdict") if isinstance(snapshot, Mapping) else None
    prior_artifact = None
    if isinstance(prior, Mapping):
        prior_artifact = state.artifacts.get(str(prior.get("artifact_id")))
        if prior_artifact is None:
            return None
    return closure_evidence_anchor(
        snapshot, target=target, root_item=root_item, prior_artifact=prior_artifact
    )


def closure_evaluation_return_refusal(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    behavior: CompletionBehaviorDeclaration,
    original_activation: Activation,
    run: RunRecord,
) -> str | None:
    """Whether ``run`` is the original evaluator run or returns to it exactly.

    Walks authenticated recorded-source returns from ``run`` back to the run that
    claimed ``original_activation``. Every returned activation keeps the original
    evaluator lineage, and every source is the same work, plan, stage and runner.
    """
    current = run
    seen: set[str] = set()
    while current.activation_id != original_activation.activation_id:
        activation = state.activations.get(current.activation_id)
        if (
            activation is None
            or activation.lineage_id != original_activation.lineage_id
            or current.activation_id in seen
            or not claimed_activation_matches(state, current.activation_id)
        ):
            return "closure_evaluation_return_origin_invalid"
        seen.add(current.activation_id)
        source = recorded_source_return_source(state, plan, current.activation_id)
        if source is None or (
            source.work_item_id != run.work_item_id
            or source.run_ref.plan_ref != run.run_ref.plan_ref
            or source.stage_kind_id != behavior.target_stage_kind_id
            or source.runner_binding_id != behavior.runner_binding_id
        ):
            return "closure_evaluation_return_origin_invalid"
        current = source
    if (
        current.run_ref.run_id != original_activation.claimed_by_run_id
        or not claimed_activation_matches(state, current.activation_id)
    ):
        return "closure_evaluation_return_origin_invalid"
    return None


def closure_verdict_run_refusal(
    state: RuntimeState,
    target: ClosureTargetRecord,
    behavior: CompletionBehaviorDeclaration,
    evaluation: ClosureEvaluationRecord,
    run: RunRecord,
) -> str | None:
    """Whether ``run`` emitted verdicts as the exact evaluator of ``evaluation``.

    The run is the evaluation's original claimed run, or a recorded-source return
    of it. Shared by kernel snapshot capture and the sealed prior-verdict check;
    matching work, plan or lineage alone never grants it.
    """
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if (
        admitted is None
        or run.work_item_id != evaluation.target_work_item_id
        or run.run_ref.plan_ref != evaluation.selected_plan_ref
        or run.stage_kind_id != behavior.target_stage_kind_id
        or run.runner_binding_id != behavior.runner_binding_id
    ):
        return "closure_evaluation_relation_missing"
    _work, activation, refusal = closure_evaluation_parts(
        state, evaluation, target, behavior
    )
    if refusal is not None or activation is None:
        return refusal or "closure_evaluation_relation_missing"
    return closure_evaluation_return_refusal(
        state, admitted.selected_plan, behavior, activation, run
    )


def closure_evaluation_origin_refusal(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    evaluation: ClosureEvaluationRecord,
) -> tuple[Activation | None, str | None]:
    """Authenticate one evaluation record's immutable origin; return its activation."""
    behaviors = [
        behavior
        for behavior in plan.completion_behaviors
        if behavior.id == evaluation.completion_behavior_id
    ]
    target = state.closure_targets.get(evaluation.closure_target_id)
    if len(behaviors) != 1 or target is None:
        return None, "closure_evaluation_relation_missing"
    work, activation, refusal = closure_evaluation_parts(
        state, evaluation, target, behaviors[0]
    )
    if refusal is not None or activation is None or work is None:
        return None, refusal or "closure_evaluation_relation_missing"
    creator = EvaluateCompletionBehavior(
        evaluation.created_by_input_id,
        evaluation.selected_plan_ref,
        str(evaluation.completion_behavior_id),
        evaluation.closure_target_id,
    )
    if (
        closure_creator_refusal(state, creator) is not None
        or closure_creator_audit_refusal(
            state,
            creator,
            plan_fingerprint=evaluation.selected_plan_ref.authority_fingerprint,
            work_item_id=evaluation.target_work_item_id,
        )
        is not None
    ):
        return None, "closure_evaluation_creator_invalid"
    target_refusal = closure_target_origin_refusal(state, plan, target)
    if target_refusal is not None:
        return None, target_refusal
    anchor = closure_sealed_snapshot_anchor(state, target, work)
    if anchor is None or not closure_sealed_evidence_matches(
        state,
        behaviors[0],
        target,
        evaluation,
        work.payload.get("closure_evidence_snapshot"),
    ):
        return None, "closure_snapshot_authority_mismatch"
    for other in state.closure_evaluations.values():
        other_work = state.work_items.get(other.target_work_item_id)
        if (
            other.record_id != evaluation.record_id
            and other.closure_target_id == evaluation.closure_target_id
            and other_work is not None
            and closure_evidence_anchor(
                other_work.payload.get("closure_evidence_snapshot"), target=target
            )
            == anchor
        ):
            return None, "closure_snapshot_authority_mismatch"
    return activation, None


def closure_evaluation_for_run(
    state: RuntimeState,
    run: RunRecord,
) -> tuple[ClosureEvaluationRecord | None, str | None]:
    """Keep evaluator authority through exact authenticated source returns."""
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    work = state.work_items.get(run.work_item_id)
    if admitted is None or work is None:
        return None, "closure_evaluation_relation_missing"
    plan = admitted.selected_plan
    behaviors = {behavior.id: behavior for behavior in plan.completion_behaviors}
    records = tuple(
        record
        for record in state.closure_evaluations.values()
        if record.target_work_item_id == run.work_item_id
    )
    owned = tuple(
        record
        for record in records
        if (behavior := behaviors.get(record.completion_behavior_id)) is not None
        and behavior.target_stage_kind_id == run.stage_kind_id
    )
    evaluator_work = any(
        transition.input_id == work.created_by_input_id
        and transition.input_kind == EvaluateCompletionBehavior.input_kind
        for transition in state.transitions
    ) and any(
        behavior.target_stage_kind_id == run.stage_kind_id
        for behavior in behaviors.values()
    )
    if not owned and not evaluator_work:
        return None, None
    if len(owned) != 1:
        return None, "closure_evaluation_relation_missing"
    record = owned[0]
    behavior = behaviors[record.completion_behavior_id]
    if record.selected_plan_ref != run.run_ref.plan_ref:
        return None, "closure_evaluation_relation_missing"
    # Authenticate a returned run before inspecting the sealed capture. A damaged
    # later return must retain its return-provenance refusal, even when that same
    # later observation also fails the broader artifact-history check.
    if run.activation_id != record.target_activation_id:
        original = state.activations.get(record.target_activation_id)
        if original is None:
            return None, "closure_evaluation_relation_missing"
        return_refusal = closure_evaluation_return_refusal(
            state, plan, behavior, original, run
        )
        if return_refusal is not None:
            return None, return_refusal
    original_activation, refusal = closure_evaluation_origin_refusal(
        state, plan, record
    )
    if refusal is not None or original_activation is None:
        return None, refusal or "closure_evaluation_relation_missing"
    return_refusal = closure_evaluation_return_refusal(
        state, plan, behavior, original_activation, run
    )
    if return_refusal is not None:
        return None, return_refusal
    return record, None


def closure_evaluator_origin_matches(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    activation_id: str,
) -> bool:
    """Whether the activation is the authentic original selected closure evaluator.

    Exactly one evaluation record may exist for the evaluator work item; it must
    target this activation under the activation's plan and pass the complete
    immutable origin authentication above.
    """
    activation = state.activations.get(activation_id)
    if activation is None:
        return False
    records = [
        record
        for record in state.closure_evaluations.values()
        if record.target_work_item_id == activation.work_item_id
    ]
    if (
        len(records) != 1
        or records[0].target_activation_id != activation_id
        or records[0].selected_plan_ref != activation.plan_ref
    ):
        return False
    original, refusal = closure_evaluation_origin_refusal(state, plan, records[0])
    return (
        refusal is None
        and original is not None
        and original.activation_id == activation_id
    )


__all__ = (
    "claimed_evaluator_run",
    "closure_artifact_envelope",
    "closure_artifact_history_complete",
    "closure_creator_audit_refusal",
    "closure_creator_refusal",
    "closure_enqueue_creator_refusal",
    "closure_evaluation_for_run",
    "closure_evaluation_origin_refusal",
    "closure_evaluation_parts",
    "closure_evaluation_return_refusal",
    "closure_evaluator_origin_matches",
    "closure_evidence_anchor",
    "closure_root_source_matches",
    "closure_sealed_evidence_matches",
    "closure_sealed_snapshot_anchor",
    "closure_target_origin_refusal",
    "closure_verdict_run_refusal",
)
