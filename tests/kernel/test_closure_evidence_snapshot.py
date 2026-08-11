from __future__ import annotations

from dataclasses import replace
from typing import cast

from millrace.contracts.compiled_plan import CompletionBehaviorDeclaration
from millrace.contracts.ids import QueueFamilyId
from millrace.contracts.state import (
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    RuntimeState,
    WorkItem,
    WorkItemRef,
)
from millrace.contracts.transition import (
    artifact_payload_digest,
    canonical_authority_mapping_bytes,
)
from millrace.kernel.decision import (
    _build_closure_evidence_snapshot,
    _closure_snapshot_authority_refusal,
    _completion_request_payload,
)
from millrace.kernel.lookups import plan_ref_for
from support import generic_lifecycle
from tests.operator.test_status_projection import _closure_verdict_payload

_AUTH_SOURCE = generic_lifecycle.source_with_completion_behavior()
_AUTH_PLAN, _AUTH_FINGERPRINT = generic_lifecycle.compile_lifecycle(_AUTH_SOURCE)
_PLAN_REF = plan_ref_for(_AUTH_PLAN, _AUTH_FINGERPRINT)
_LINEAGE_ID = "work-origin"
_BEHAVIOR = _AUTH_PLAN.completion_behaviors[0]


def _behavior(
    *,
    evidence_item_limit: int = 4,
    request_payload_byte_limit: int = 16_384,
) -> CompletionBehaviorDeclaration:
    return replace(
        _BEHAVIOR,
        evidence_item_limit=evidence_item_limit,
        request_payload_byte_limit=request_payload_byte_limit,
    )


def _work_item(work_item_id: str, payload: dict[str, object]) -> WorkItem:
    return WorkItem(
        ref=WorkItemRef(work_item_id, _PLAN_REF, 0),
        queue_family_id=QueueFamilyId("joined_bundle"),
        payload=payload,
        lineage_id=_LINEAGE_ID,
        created_by_input_id=f"create-{work_item_id}",
    )


def _observe(
    state: RuntimeState,
    work: WorkItem,
    suffix: str,
    summary: str,
) -> RuntimeState:
    run_id = f"run-{suffix}"
    activation_id = f"activation-{suffix}"
    target = next(iter(state.closure_targets.values()))
    root = state.work_items["work-origin"]
    root_digest = artifact_payload_digest(root.payload)
    artifact_payload = _closure_verdict_payload(
        {
            "closure_target_id": target.closure_target_id,
            "root_contract": {"payload_digest": root_digest},
            "freshness_anchor_digest": root_digest,
        },
        marker="REVIEW_PASSED",
    )
    artifact_payload["summary"] = summary
    activation = replace(
        state.activations["activation-origin"],
        activation_id=activation_id,
        work_item_id=work.ref.work_item_id,
        lineage_id=work.lineage_id,
        queue_family_id=work.queue_family_id,
        graph_node_id=_BEHAVIOR.target_graph_node_id,
        stage_kind_id=_BEHAVIOR.target_stage_kind_id,
        generation=0,
        created_by_input_id=work.created_by_input_id,
        claimed_by_run_id=None,
    )
    state = replace(
        state,
        work_items={**state.work_items, work.ref.work_item_id: work},
        activations={**state.activations, activation.activation_id: activation},
    )
    state = generic_lifecycle.claim_activation(
        state, activation_id=activation_id, suffix=suffix
    )
    return generic_lifecycle.apply_observation(
        state,
        plan=_AUTH_PLAN,
        fingerprint=_AUTH_FINGERPRINT,
        run_id=run_id,
        input_id=f"observe-{suffix}",
        marker="REVIEW_PASSED",
        artifact_payload=artifact_payload,
    )


def _base_state() -> tuple[RuntimeState, ClosureTargetRecord, WorkItem]:
    state, _plan, _fingerprint = generic_lifecycle.closure_opened_state(_AUTH_SOURCE)
    target = next(iter(state.closure_targets.values()))
    return state, target, state.work_items["work-origin"]


def test_first_closure_snapshot_contains_root_and_no_prior_verdict() -> None:
    behavior = _behavior()
    state, target, root = _base_state()

    snapshot, refusal = _build_closure_evidence_snapshot(
        state=state,
        target=target,
        behavior=behavior,
    )

    assert refusal is None
    assert snapshot is not None
    assert snapshot["record_kind"] == "closure_evidence_snapshot"
    assert snapshot["schema_version"] == 1
    assert snapshot["closure_target_id"] == target.closure_target_id
    assert snapshot["selected_plan_fingerprint"] == _PLAN_REF.authority_fingerprint
    assert snapshot["lineage_id"] == _LINEAGE_ID
    assert snapshot["root_contract"] == {
        "work_item_id": root.ref.work_item_id,
        "payload_digest": artifact_payload_digest(root.payload),
        "payload": root.payload,
    }
    assert snapshot["prior_verdict"] is None
    assert snapshot["freshness_anchor_digest"] == artifact_payload_digest(root.payload)
    assert snapshot["evidence_artifacts"] == ()


def test_later_snapshot_reuses_prior_verdict_and_filters_post_anchor_evidence() -> None:
    behavior = _behavior()
    state, target, root = _base_state()
    prior_work = _work_item(
        "prior-work", {"closure_target_id": target.closure_target_id}
    )
    pre_work = _work_item("pre-work", {"kind": "pre"})
    post_work = _work_item("post-work", {"kind": "post"})
    root_snapshot, root_refusal = _build_closure_evidence_snapshot(
        state=state,
        target=target,
        behavior=behavior,
    )
    assert root_refusal is None and root_snapshot is not None
    prior_work = replace(
        prior_work,
        payload={
            **prior_work.payload,
            "closure_evidence_snapshot": root_snapshot,
        },
    )
    evaluation = ClosureEvaluationRecord(
        record_id="closure-evaluation-1",
        closure_target_id=target.closure_target_id,
        completion_behavior_id=behavior.id,
        request_kind=behavior.request_kind,
        target_work_item_id=prior_work.ref.work_item_id,
        target_activation_id="activation-prior",
        selected_plan_ref=_PLAN_REF,
        lineage_id=_LINEAGE_ID,
        created_by_input_id="evaluate-1",
    )
    state = replace(
        state,
        closure_evaluations={evaluation.record_id: evaluation},
    )
    state = _observe(state, pre_work, "pre", "old evidence")
    current_snapshot, current_refusal = _build_closure_evidence_snapshot(
        state=state,
        target=target,
        behavior=behavior,
    )
    assert current_refusal is None and current_snapshot is not None
    prior_work = replace(
        prior_work,
        payload={**prior_work.payload, "closure_evidence_snapshot": current_snapshot},
    )
    state = replace(
        state,
        work_items={prior_work.ref.work_item_id: prior_work, **state.work_items},
    )
    state = _observe(state, prior_work, "prior", "prior verdict")
    prior_artifact = next(
        item
        for item in state.artifacts.values()
        if item.work_item_id == prior_work.ref.work_item_id
    )
    state = _observe(state, post_work, "post", "new evidence")
    post_artifact = next(
        item
        for item in state.artifacts.values()
        if item.work_item_id == post_work.ref.work_item_id
    )

    snapshot, refusal = _build_closure_evidence_snapshot(
        state=state,
        target=target,
        behavior=behavior,
    )

    assert refusal is None
    assert snapshot is not None
    assert snapshot["prior_verdict"] == {
        "artifact_id": prior_artifact.artifact_id,
        "artifact_schema_id": str(prior_artifact.schema_id),
        "payload_digest": prior_artifact.payload_digest,
        "payload": prior_artifact.payload,
        "source_work_item_id": prior_artifact.work_item_id,
        "source_run_id": prior_artifact.source_run_id,
        "source_action_id": str(prior_artifact.source_action_id),
        "source_stage_kind_id": str(prior_artifact.source_stage_kind_id),
        "transition_id": prior_artifact.transition_id,
    }
    assert snapshot["freshness_anchor_digest"] == prior_artifact.payload_digest
    evidence = cast(tuple[dict[str, object], ...], snapshot["evidence_artifacts"])
    assert [item["artifact_id"] for item in evidence] == [post_artifact.artifact_id]

    prior_corruption = dict(cast(dict[str, object], snapshot["prior_verdict"]))
    prior_corruption["payload_digest"] = "sha256:" + "d" * 64
    evidence_corruption = dict(evidence[0])
    evidence_corruption["payload_digest"] = "sha256:" + "e" * 64
    for corrupted_snapshot in (
        {**snapshot, "prior_verdict": prior_corruption},
        {**snapshot, "evidence_artifacts": (evidence_corruption,)},
        {**snapshot, "evidence_artifacts": (*evidence, evidence[0])},
        *(
            {
                **snapshot,
                field_name: (
                    {**snapshot[field_name], **value}
                    if field_name == "root_contract"
                    else value
                ),
            }
            for field_name, value in (
                ("root_contract", {"work_item_id": "forged"}),
                ("selected_plan_fingerprint", "sha256:" + "b" * 64),
                ("lineage_id", "wrong-lineage"),
                ("freshness_anchor_digest", "sha256:" + "c" * 64),
                ("evidence_artifacts", ({"artifact_id": "forged"},)),
            )
        ),
    ):
        assert (
            _closure_snapshot_authority_refusal(
                state=state,
                target=target,
                behavior=behavior,
                snapshot=corrupted_snapshot,
            )
            == "closure_snapshot_authority_mismatch"
        )


def test_snapshot_refuses_evidence_item_overflow_before_truncation() -> None:
    behavior = _behavior(evidence_item_limit=1)
    state, target, _root = _base_state()
    for index in range(2):
        work = _work_item(f"evidence-work-{index}", {"index": index})
        state = _observe(state, work, f"evidence-{index}", f"evidence {index}")

    snapshot, refusal = _build_closure_evidence_snapshot(
        state=state,
        target=target,
        behavior=behavior,
    )

    assert snapshot is None
    assert refusal == "closure_evidence_item_limit_exceeded"


def test_closure_request_byte_limit_boundary_and_overflow() -> None:
    state, target, _root = _base_state()
    stage = next(
        item
        for item in _AUTH_PLAN.stage_kinds
        if item.id == _BEHAVIOR.target_stage_kind_id
    )
    wide_payload, wide_refusal = _completion_request_payload(
        state=state,
        target=target,
        behavior=_behavior(request_payload_byte_limit=1_000_000),
        stage=stage,
    )
    assert wide_refusal is None
    assert wide_payload is not None
    exact_limit = len(canonical_authority_mapping_bytes(wide_payload))

    exact_payload, exact_refusal = _completion_request_payload(
        state=state,
        target=target,
        behavior=_behavior(request_payload_byte_limit=exact_limit),
        stage=stage,
    )
    assert exact_refusal is None
    assert exact_payload == wide_payload

    overflow_payload, overflow_refusal = _completion_request_payload(
        state=state,
        target=target,
        behavior=_behavior(request_payload_byte_limit=exact_limit - 1),
        stage=stage,
    )
    assert overflow_payload is None
    assert overflow_refusal == "closure_request_payload_limit_exceeded"
