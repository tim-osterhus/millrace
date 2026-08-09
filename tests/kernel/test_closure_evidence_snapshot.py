from __future__ import annotations

from dataclasses import replace
from typing import cast

from millrace.contracts.compiled_plan import (
    CompletionBehaviorDeclaration,
    StageKindDeclaration,
)
from millrace.contracts.ids import (
    ActionId,
    ArtifactSchemaId,
    AssetId,
    CompletionBehaviorId,
    OutcomeId,
    PartitionId,
    QueueFamilyId,
    RemediationPolicyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.state import (
    ArtifactRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    PlanRef,
    RunRecord,
    RunRef,
    RuntimeState,
    TransitionRecord,
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
    _closure_verdict_refusal,
    _completion_request_payload,
)

_PLAN_REF = PlanRef("workflow:0.1", "sha256:" + "a" * 64, 16)
_TARGET_ID = "closure-target-1"
_LINEAGE_ID = "lineage-1"
_VERDICT_SCHEMA = ArtifactSchemaId("verdict")
_EVIDENCE_SCHEMA = ArtifactSchemaId("evidence")


def _behavior(
    *,
    evidence_item_limit: int = 4,
    request_payload_byte_limit: int = 16_384,
) -> CompletionBehaviorDeclaration:
    return CompletionBehaviorDeclaration(
        id=CompletionBehaviorId("completion-1"),
        trigger="backlog_drained",
        readiness_rule="no_open_lineage_work",
        request_kind="closure_target",
        target_selector="active_closure_target",
        target_stage_kind_id=StageKindId("review"),
        target_graph_node_id="review.start",
        runner_binding_id=RunnerBindingId("runner"),
        request_queue_family_id=QueueFamilyId("review"),
        pass_action_id=ActionId("review.pass"),
        gap_action_id=ActionId("review.gap"),
        blocked_action_id=ActionId("review.blocked"),
        verdict_artifact_schema_id=_VERDICT_SCHEMA,
        evidence_artifact_schema_ids=(_VERDICT_SCHEMA, _EVIDENCE_SCHEMA),
        evidence_item_limit=evidence_item_limit,
        request_payload_byte_limit=request_payload_byte_limit,
        remediation_policy_id=RemediationPolicyId("remediation"),
        accepted_root_source_kinds=("origin",),
        root_source_resolution="runtime_inventory",
        evidence_window_policy="lineage",
        rubric_policy="reuse_or_create",
        blocked_work_policy="suppress",
        skip_if_closed=True,
        presentation={},
    )


def _target() -> ClosureTargetRecord:
    return ClosureTargetRecord(
        closure_target_id=_TARGET_ID,
        selected_plan_ref=_PLAN_REF,
        completion_behavior_id=CompletionBehaviorId("completion-1"),
        lineage_id=_LINEAGE_ID,
        root_source_kind="origin",
        root_source_id="root-source-1",
        closure_root_work_item_id="root-work-1",
        request_kind="closure_target",
        target_graph_node_id="review.start",
        evidence_window={"kind": "lineage", "lineage_id": _LINEAGE_ID},
        status="open",
        opened_by_input_id="open-1",
    )


def _work_item(work_item_id: str, payload: dict[str, object]) -> WorkItem:
    return WorkItem(
        ref=WorkItemRef(work_item_id, _PLAN_REF, 0),
        queue_family_id=QueueFamilyId("review"),
        payload=payload,
        lineage_id=_LINEAGE_ID,
        created_by_input_id=f"create-{work_item_id}",
    )


def _run(run_id: str, work_item_id: str) -> RunRecord:
    return RunRecord(
        run_ref=RunRef(
            run_id,
            work_item_id,
            f"claim-{run_id}",
            _PLAN_REF,
            0,
            f"fence-{run_id}",
        ),
        work_item_id=work_item_id,
        activation_id=f"activation-{run_id}",
        stage_kind_id=StageKindId("review"),
        runner_binding_id=RunnerBindingId("runner"),
        created_by_input_id=f"claim-{run_id}",
    )


def _artifact(
    artifact_id: str,
    schema_id: ArtifactSchemaId,
    run: RunRecord,
    transition_id: str,
    payload: dict[str, object],
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        work_item_id=run.work_item_id,
        schema_id=schema_id,
        payload=payload,
        created_by_input_id=f"observe-{run.run_ref.run_id}",
        source_run_id=run.run_ref.run_id,
        source_action_id=ActionId("review.pass"),
        source_stage_kind_id=StageKindId("review"),
        source_graph_node_id="review.start",
        payload_digest=artifact_payload_digest(payload),
        transition_id=transition_id,
    )


def test_first_closure_snapshot_contains_root_and_no_prior_verdict() -> None:
    target = _target()
    behavior = _behavior()
    root = _work_item(
        "root-work-1",
        {
            "title": "Root task",
            "root_source": {"kind": "origin", "source_id": "root-source-1"},
        },
    )
    state = RuntimeState(
        work_items={root.ref.work_item_id: root},
        closure_targets={target.closure_target_id: target},
    )

    snapshot, refusal = _build_closure_evidence_snapshot(
        state=state,
        target=target,
        behavior=behavior,
    )

    assert refusal is None
    assert snapshot is not None
    assert snapshot["record_kind"] == "closure_evidence_snapshot"
    assert snapshot["schema_version"] == 1
    assert snapshot["closure_target_id"] == _TARGET_ID
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


def test_closure_snapshot_requires_an_admitted_root_work_item_for_manual_sources(
) -> None:
    target = replace(
        _target(),
        root_source_kind="manual",
        root_source_id="manual-source-1",
        closure_root_work_item_id=None,
    )

    snapshot, refusal = _build_closure_evidence_snapshot(
        state=RuntimeState(
            closure_targets={target.closure_target_id: target},
        ),
        target=target,
        behavior=_behavior(),
    )

    assert snapshot is None
    assert refusal == "missing_closure_root_work_item"


def test_later_snapshot_reuses_prior_verdict_and_selects_only_post_anchor_evidence(
) -> None:
    target = _target()
    behavior = _behavior()
    root = _work_item(
        "root-work-1",
        {
            "root": "task",
            "root_source": {"kind": "origin", "source_id": "root-source-1"},
        },
    )
    prior_work = _work_item("prior-work", {"closure_target_id": _TARGET_ID})
    pre_work = _work_item("pre-work", {"kind": "pre"})
    post_work = _work_item("post-work", {"kind": "post"})
    prior_run = _run("prior-run", prior_work.ref.work_item_id)
    pre_run = _run("pre-run", pre_work.ref.work_item_id)
    post_run = _run("post-run", post_work.ref.work_item_id)
    prior_payload = {"summary": "prior verdict"}
    prior_artifact = _artifact(
        "prior-verdict",
        _VERDICT_SCHEMA,
        prior_run,
        "transition-prior",
        prior_payload,
    )
    pre_artifact = _artifact(
        "pre-evidence",
        _EVIDENCE_SCHEMA,
        pre_run,
        "transition-pre",
        {"summary": "old evidence"},
    )
    post_artifact = _artifact(
        "post-evidence",
        _EVIDENCE_SCHEMA,
        post_run,
        "transition-post",
        {"summary": "new evidence"},
    )
    evaluation = ClosureEvaluationRecord(
        record_id="closure-evaluation-1",
        closure_target_id=_TARGET_ID,
        completion_behavior_id=behavior.id,
        request_kind=behavior.request_kind,
        target_work_item_id=prior_work.ref.work_item_id,
        target_activation_id=prior_run.activation_id,
        selected_plan_ref=_PLAN_REF,
        lineage_id=_LINEAGE_ID,
        created_by_input_id="evaluate-1",
    )
    state = RuntimeState(
        work_items={
            item.ref.work_item_id: item
            for item in (root, prior_work, pre_work, post_work)
        },
        runs={run.run_ref.run_id: run for run in (prior_run, pre_run, post_run)},
        artifacts={
            item.artifact_id: item
            for item in (prior_artifact, pre_artifact, post_artifact)
        },
        closure_targets={target.closure_target_id: target},
        closure_evaluations={evaluation.record_id: evaluation},
        transitions=tuple(
            TransitionRecord(
                record_id=transition_id,
                input_id=transition_id,
                input_kind="workflow.runner_result_observed",
                input_family="workflow_observation",
                accepted=True,
            )
                for transition_id in (
                    "transition-pre",
                    "transition-prior",
                    "transition-post",
                )
        ),
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
        {
            **snapshot,
            "prior_verdict": prior_corruption,
        },
        {
            **snapshot,
            "evidence_artifacts": (evidence_corruption,),
        },
        {
            **snapshot,
            "evidence_artifacts": (*evidence, evidence[0]),
        },
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
    target = _target()
    behavior = _behavior(evidence_item_limit=1)
    root = _work_item(
        "root-work-1",
        {
            "root": "task",
            "root_source": {"kind": "origin", "source_id": "root-source-1"},
        },
    )
    work_items = [root]
    runs = []
    artifacts = []
    transitions = []
    for index in range(2):
        work = _work_item(f"evidence-work-{index}", {"index": index})
        run = _run(f"evidence-run-{index}", work.ref.work_item_id)
        transition_id = f"transition-evidence-{index}"
        work_items.append(work)
        runs.append(run)
        artifacts.append(
            _artifact(
                f"evidence-{index}",
                _EVIDENCE_SCHEMA,
                run,
                transition_id,
                {"summary": f"evidence {index}"},
            )
        )
        transitions.append(
            TransitionRecord(
                record_id=transition_id,
                input_id=transition_id,
                input_kind="workflow.runner_result_observed",
                input_family="workflow_observation",
                accepted=True,
            )
        )
    state = RuntimeState(
        work_items={item.ref.work_item_id: item for item in work_items},
        runs={run.run_ref.run_id: run for run in runs},
        artifacts={item.artifact_id: item for item in artifacts},
        closure_targets={target.closure_target_id: target},
        transitions=tuple(transitions),
    )

    snapshot, refusal = _build_closure_evidence_snapshot(
        state=state,
        target=target,
        behavior=behavior,
    )

    assert snapshot is None
    assert refusal == "closure_evidence_item_limit_exceeded"


def test_closure_request_byte_limit_boundary_and_overflow() -> None:
    target = _target()
    root = _work_item(
        "root-work-1",
        {
            "root": "task",
            "root_source": {"kind": "origin", "source_id": "root-source-1"},
        },
    )
    state = RuntimeState(
        work_items={root.ref.work_item_id: root},
        closure_targets={target.closure_target_id: target},
    )
    stage = StageKindDeclaration(
        id=StageKindId("review"),
        partition_id=PartitionId("review"),
        runner_binding_id=RunnerBindingId("runner"),
        input_queue_family_ids=(QueueFamilyId("review"),),
        output_queue_family_ids=(),
        artifact_schema_ids=(_VERDICT_SCHEMA,),
        asset_ids=(AssetId("review"),),
        declared_outcome_ids=(OutcomeId("review.complete"),),
        presentation={},
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


def test_closure_verdict_refuses_changed_rubric_and_stale_anchor() -> None:
    snapshot = {
        "closure_target_id": _TARGET_ID,
        "root_contract": {"payload_digest": "sha256:" + "b" * 64},
        "freshness_anchor_digest": "sha256:" + "c" * 64,
    }
    different_rubric = {
        "criteria": [
            {
                "criterion_id": "different",
                "requirement": "A different requirement.",
                "evidence_rule": "Use different evidence.",
            }
        ]
    }
    verdict = {
        "artifact_kind": "verdict",
        "summary": "review",
        "closure_target_id": _TARGET_ID,
        "root_contract_digest": "sha256:" + "b" * 64,
        "freshness_anchor_digest": "sha256:" + "c" * 64,
        "rubric": {
            "criteria": [
                {
                    "criterion_id": "c1",
                    "requirement": "The requirement.",
                    "evidence_rule": "Use current evidence.",
                }
            ]
        },
        "criterion_results": [
            {
                "criterion_id": "c1",
                "status": "passed",
                "provenance": "fresh",
                "evidence_refs": [{"evidence_id": "e1", "summary": "Evidence."}],
            }
        ],
        "observations": [],
        "remediation_guidance": [],
        "confidence": "high",
        "residual_uncertainty": "none",
    }

    assert (
        _closure_verdict_refusal(
            verdict,
            snapshot=snapshot,
            prior_rubric=different_rubric,
        )
        == "closure_rubric_mismatch"
    )

    verdict["rubric"] = different_rubric
    verdict["freshness_anchor_digest"] = "sha256:" + "d" * 64
    assert (
        _closure_verdict_refusal(
            verdict,
            snapshot=snapshot,
            prior_rubric=None,
        )
        == "closure_freshness_anchor_mismatch"
    )


def test_persisted_snapshot_is_reauthenticated_against_current_runtime_state() -> None:
    target = _target()
    behavior = _behavior()
    root = _work_item(
        "root-work-1",
        {
            "root": "task",
            "root_source": {"kind": "origin", "source_id": "root-source-1"},
        },
    )
    state = RuntimeState(
        work_items={root.ref.work_item_id: root},
        closure_targets={target.closure_target_id: target},
    )
    snapshot, refusal = _build_closure_evidence_snapshot(
        state=state,
        target=target,
        behavior=behavior,
    )
    assert refusal is None
    assert snapshot is not None

    corruptions = (
        ("root_contract", {"work_item_id": "forged"}),
        ("selected_plan_fingerprint", "sha256:" + "b" * 64),
        ("lineage_id", "wrong-lineage"),
        ("freshness_anchor_digest", "sha256:" + "c" * 64),
        ("evidence_artifacts", ({"artifact_id": "forged"},)),
    )
    for field_name, value in corruptions:
        corrupted = dict(snapshot)
        if field_name == "root_contract":
            corrupted[field_name] = {**snapshot[field_name], **value}
        else:
            corrupted[field_name] = value

        assert (
            _closure_snapshot_authority_refusal(
                state=state,
                target=target,
                behavior=behavior,
                snapshot=corrupted,
            )
            == "closure_snapshot_authority_mismatch"
        )
