from __future__ import annotations

import re
from pathlib import Path

import pytest

from millrace.contracts import QueueFamilyId
from millrace.contracts.transition import EnqueueWork
from millrace.kernel import apply
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    fake_runner_completion_input_id,
)
from support import vendor_selection

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_vendor_selection_purchase_request_external_enqueue_uses_selected_route() -> (
    None
):
    state, _plan, fingerprint = vendor_selection.admit_vendor_selection()

    decision = decide(
        state,
        EnqueueWork(
            "enqueue-a",
            queue_family_id=QueueFamilyId("purchase_request"),
            payload=vendor_selection.purchase_request_payload("request-a"),
        ),
        vendor_selection.context(
            "enqueue-a",
            work_item_id="work-request-a",
            activation_id="activation-request-intake-a",
        ),
    )
    after = apply(state, decision)

    assert decision.accepted is True
    work_item = after.work_items["work-request-a"]
    activation = after.activations["activation-request-intake-a"]
    assert work_item.queue_family_id == QueueFamilyId("purchase_request")
    assert work_item.ref.plan_ref.authority_fingerprint == fingerprint
    assert work_item.lineage_id == "work-request-a"
    assert activation.queue_family_id == QueueFamilyId("purchase_request")
    assert str(activation.stage_kind_id) == "request_intake"
    assert activation.graph_node_id == "vendor_selection.request_intake.start"
    assert str(activation.runner_binding_id) == vendor_selection.RUNNER_ID


def test_vendor_selection_external_enqueue_refuses_internal_queue_or_bad_payload() -> (
    None
):
    state, _plan, _fingerprint = vendor_selection.admit_vendor_selection()

    internal_decision = decide(
        state,
        EnqueueWork(
            "enqueue-internal",
            queue_family_id=QueueFamilyId("candidate_bundle"),
            payload=vendor_selection.candidate_bundle_payload(),
        ),
        vendor_selection.context("enqueue-internal"),
    )
    after_internal = apply(state, internal_decision)

    assert internal_decision.accepted is False
    assert internal_decision.refusal is not None
    assert internal_decision.refusal.reason == "queue_family_not_external"
    assert after_internal.work_items == state.work_items
    assert after_internal.activations == state.activations

    invalid_decision = decide(
        state,
        EnqueueWork(
            "enqueue-invalid",
            queue_family_id=QueueFamilyId("purchase_request"),
            payload={
                **vendor_selection.purchase_request_payload("request-invalid"),
                "request_id": "",
            },
        ),
        vendor_selection.context("enqueue-invalid"),
    )
    after_invalid = apply(state, invalid_decision)

    assert invalid_decision.accepted is False
    assert invalid_decision.refusal is not None
    assert invalid_decision.refusal.reason == "invalid_enqueue_payload_schema"
    assert after_invalid.work_items == state.work_items
    assert after_invalid.activations == state.activations


def test_vendor_selection_happy_path_reaches_candidate_packager() -> None:
    state, plan, fingerprint = vendor_selection.admit_vendor_selection()
    state = vendor_selection.enqueue_purchase_request(state, suffix="a")

    after = vendor_selection.progress_to_candidate_packager(
        state,
        plan=plan,
        fingerprint=fingerprint,
        suffix="a",
    )

    assert "activation-packager-a" in after.activations
    assert str(after.activations["activation-packager-a"].stage_kind_id) == (
        "candidate_packager"
    )
    assert after.work_items["work-packager-a"].queue_family_id == QueueFamilyId(
        "candidate_bundle"
    )
    assert {
        fake_runner_completion_input_id("observe-request-intake-a"),
        fake_runner_completion_input_id("observe-policy-a"),
        fake_runner_completion_input_id("observe-freezer-a"),
        fake_runner_completion_input_id("observe-sourcer-a"),
    } <= set(after.receipts)


def test_vendor_selection_request_b_progresses_while_request_a_waits_for_join() -> None:
    state, plan, fingerprint = vendor_selection.packager_closed_state("a")
    from millrace.contracts.transition import FanoutFromArtifact

    for fanout_id, suffix in (
        ("vendor_selection.candidate_packager.rubric_fanout", "rubric"),
        ("vendor_selection.candidate_packager.conflict_fanout", "conflict"),
    ):
        state = vendor_selection.apply_accepted_input(
            state,
            FanoutFromArtifact(
                f"fanout-{suffix}-a",
                fanout_id=fanout_id,
                source_artifact_id=vendor_selection.artifact_id_for(
                    "observe-packager-a"
                ),
            ),
            vendor_selection.context(f"fanout-{suffix}-a"),
        )
    rubric_activation = vendor_selection.report_branch_activation_id(
        state,
        "rubric_evaluator",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=rubric_activation,
        suffix="rubric-a",
    )
    state = vendor_selection.enqueue_purchase_request(state, suffix="b")
    state = vendor_selection.progress_to_candidate_packager(
        state,
        plan=plan,
        fingerprint=fingerprint,
        suffix="b",
    )

    assert "run-rubric-a" in state.runs
    assert "activation-packager-b" in state.activations
    assert str(state.activations["activation-packager-b"].stage_kind_id) == (
        "candidate_packager"
    )


def test_vendor_selection_decision_pack_close_preserves_provenance() -> None:
    state, _plan, fingerprint = vendor_selection.full_decision_pack_closed_state()

    decision_pack_artifact_id = vendor_selection.artifact_id_for(
        "observe-decision-packager-a"
    )
    artifact = state.artifacts[decision_pack_artifact_id]
    closed = state.closed_work_items["work-decision-packager-a"]

    assert str(artifact.source_action_id) == (
        "vendor_selection.decision_packager.decision_pack_ready"
    )
    assert artifact.source_run_id == "run-decision-packager-a"
    assert str(artifact.source_stage_kind_id) == "decision_packager"
    assert artifact.payload["selected_plan_fingerprint"] == fingerprint
    assert artifact.payload["evidence_refs"] == {
        "rubric_report_ref": vendor_selection.artifact_id_for("observe-rubric-a"),
        "conflict_report_ref": vendor_selection.artifact_id_for("observe-conflict-a"),
    }
    assert closed.source_run_id == "run-decision-packager-a"
    assert str(closed.action_id) == (
        "vendor_selection.decision_packager.decision_pack_ready"
    )
    assert str(state.governance_events[-1].action_id) == (
        "vendor_selection.decision_packager.decision_pack_ready"
    )
    assert str(state.traces[-1].plan_fingerprint) == fingerprint


@pytest.mark.parametrize(
    ("payload_patch", "payload_drop"),
    (
        (None, "evidence_refs"),
        (None, "selected_plan_id"),
        (None, "selected_plan_fingerprint"),
        ({"selected_plan_id": "wrong-plan"}, None),
        ({"selected_plan_fingerprint": f"sha256:{'0' * 64}"}, None),
    ),
)
def test_vendor_selection_refuses_malformed_decision_pack_close_payload(
    payload_patch: dict[str, object] | None,
    payload_drop: str | None,
) -> None:
    state, plan, fingerprint = vendor_selection.award_decider_claimed_state()
    state = vendor_selection.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-award-a",
        action_id="vendor_selection.award_decider.award_ready",
        input_id="observe-award-a",
        artifact_payload=vendor_selection.award_decision_payload(
            rubric_ref=vendor_selection.artifact_id_for("observe-rubric-a"),
            conflict_ref=vendor_selection.artifact_id_for("observe-conflict-a"),
            decision_kind="award",
            operator_gate_required=False,
        ),
        work_item_id="work-decision-packager-a",
        activation_id="activation-decision-packager-a",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id="activation-decision-packager-a",
        suffix="decision-packager-a",
    )
    payload = dict(
        vendor_selection.decision_pack_payload(
            fingerprint=fingerprint,
            rubric_ref=vendor_selection.artifact_id_for("observe-rubric-a"),
            conflict_ref=vendor_selection.artifact_id_for("observe-conflict-a"),
        )
    )
    if payload_drop is not None:
        payload.pop(payload_drop)
    if payload_patch is not None:
        payload.update(payload_patch)

    decision = decide(
        state,
        vendor_selection.runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-decision-packager-a",
            action_id="vendor_selection.decision_packager.decision_pack_ready",
            input_id="observe-decision-packager-invalid-a",
            artifact_payload=payload,
        ),
        vendor_selection.context("observe-decision-packager-invalid-a"),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    assert after.work_items == state.work_items
    assert after.activations == state.activations
    assert after.artifacts == state.artifacts
    assert after.closed_work_items == state.closed_work_items


def test_vendor_selection_award_decider_creates_selected_operator_wait() -> None:
    state, plan, fingerprint = vendor_selection.award_decider_claimed_state()

    after = vendor_selection.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-award-a",
        action_id=vendor_selection.OPERATOR_WAIT_ACTION_ID,
        input_id="observe-award-operator-a",
        artifact_payload=vendor_selection.award_decision_payload(
            rubric_ref=vendor_selection.artifact_id_for("observe-rubric-a"),
            conflict_ref=vendor_selection.artifact_id_for("observe-conflict-a"),
            decision_kind="operator_required",
            operator_gate_required=True,
            reason="selected evidence requires local-operator confirmation",
        ),
    )

    assert len(after.operator_waits) == 1
    wait = next(iter(after.operator_waits.values()))
    assert str(wait.operator_wait_id) == vendor_selection.OPERATOR_WAIT_ID
    assert str(wait.source_action_id) == vendor_selection.OPERATOR_WAIT_ACTION_ID
    assert wait.lineage_id == "work-request-a"
    assert wait.selected_plan_fingerprint == fingerprint
    assert wait.selected_plan_ref == after.runs["run-award-a"].run_ref.plan_ref
    assert wait.source_work_item_id == "work-award-a"
    assert wait.source_activation_id == "activation-award-a"
    assert wait.source_run_id == "run-award-a"
    assert str(wait.source_stage_kind_id) == "award_decider"
    assert wait.source_graph_node_id == "vendor_selection.award_decider.start"
    assert wait.source_queue_family_id == QueueFamilyId("candidate_bundle")
    assert str(wait.source_runner_binding_id) == vendor_selection.RUNNER_ID
    assert wait.source_artifact_id == vendor_selection.artifact_id_for(
        "observe-award-operator-a"
    )
    assert wait.status == "active"
    assert wait.actor_id is None
    assert wait.actor_kind is None
    assert wait.resolution_kind is None
    assert "work-award-a" not in after.closed_work_items
    assert after.artifacts[
        vendor_selection.artifact_id_for("observe-award-operator-a")
    ].payload["operator_gate_required"] is True


def test_vendor_selection_no_provider_effect_or_workflow_specific_kernel_branch() -> (
    None
):
    state, plan, _fingerprint = vendor_selection.admit_vendor_selection()

    assert plan.effect_declarations == ()
    assert state.effect_proposals == {}
    assert state.effect_reconciliations == {}

    forbidden_literals = (
        "vendor_selection",
        "requirements",
        "sourcing",
        "evaluation",
        "authorization",
        "candidate_packager",
        "rubric_evaluator",
        "conflict_checker",
        "award_decider",
        "simple_loop",
        "kernel_ping",
    )
    matches: list[tuple[str, str]] = []
    for package_name in ("kernel", "substrate"):
        for path in (PROJECT_ROOT / "src" / "millrace" / package_name).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for literal in forbidden_literals:
                pattern = re.compile(rf"(?<![A-Za-z0-9_])(['\"]){re.escape(literal)}\1")
                if pattern.search(text):
                    matches.append(
                        (
                            str(path.relative_to(PROJECT_ROOT / "src" / "millrace")),
                            literal,
                        )
                    )

    assert matches == []
