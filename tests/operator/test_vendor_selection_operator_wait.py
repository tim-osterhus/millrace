from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from millrace.contracts.state import ClosedWorkItemRecord
from millrace.contracts.transition import OperatorCloseWait
from millrace.kernel import apply, decide
from millrace.operator import (
    OperatorInputError,
    OperatorResumeWaitInput,
    OperatorReviseWaitInput,
    build_resume_wait,
    build_revise_wait,
    operator_status,
)
from millrace.testing import fake_runner_completion_input_id
from substrate._runtime_store_support import persist_and_load_runtime_state
from support import vendor_selection


def _decision_pack_payload(status: Any, input_id: str) -> Any:
    input_id = fake_runner_completion_input_id(input_id)
    artifact = next(
        artifact
        for artifact in status.artifacts
        if artifact.source_input_id == input_id
    )
    assert artifact.schema_id == "DecisionPack"
    return artifact.payload


def test_operator_wait_projection_lists_selected_resolution_kinds() -> None:
    state, _plan, _fingerprint, wait_id = (
        vendor_selection.operator_required_wait_state()
    )
    wait = state.operator_waits[wait_id]

    status = operator_status(state)

    assert len(status.operator_waits) == 1
    projected = status.operator_waits[0]
    assert projected.wait_id == wait.wait_id
    assert projected.operator_wait_id == vendor_selection.OPERATOR_WAIT_ID
    assert projected.source_action_id == vendor_selection.OPERATOR_WAIT_ACTION_ID
    assert projected.status == "active"
    assert projected.allowed_resolution_kinds == (
        "resume_recorded_source",
        "revise_recorded_source",
    )
    assert projected.actor_kind_requirement == "local_operator"
    assert projected.audit_metadata_requirements == (
        "input_id",
        "input_digest",
        "selected_plan_fingerprint",
        "actor_id",
        "actor_kind",
        "wait_id",
        "operator_wait_id",
        "lineage_id",
        "target_activation_id",
        "empty_payload",
        "target_work_item_id",
        "payload_digest",
        "payload_reference",
    )
    assert projected.payload_schema_id == "OperatorDecision"
    assert projected.target_queue_family_id == "decision_pack"
    assert projected.target_stage_kind_id == "decision_packager"
    assert projected.target_graph_node_id == "vendor_selection.decision_packager.start"
    assert projected.target_runner_binding_id == vendor_selection.RUNNER_ID
    assert projected.status_effect == "operator_wait_active"
    assert projected.source_artifact_id == vendor_selection.artifact_id_for(
        "observe-award-operator-a"
    )
    assert projected.target_work_item_id is None
    assert projected.target_activation_id is None


def test_operator_wait_fixture_uses_operator_required_award_decision() -> None:
    state, plan, _fingerprint, wait_id = (
        vendor_selection.operator_required_wait_state()
    )
    wait = state.operator_waits[wait_id]
    action = next(
        action
        for action in plan.terminal_actions
        if str(action.id) == vendor_selection.OPERATOR_WAIT_ACTION_ID
    )
    outcome = next(
        outcome
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )
    artifact = state.artifacts[wait.source_artifact_id]

    assert outcome.marker == "OPERATOR_REQUIRED"
    assert action.action_kind == "operator_wait"
    assert str(action.artifact_schema_id) == "AwardDecision"
    assert artifact.payload["decision_kind"] == "operator_required"
    assert artifact.payload["operator_gate_required"] is True
    assert artifact.payload["required_evidence_refs"] == {
        "rubric_report_ref": vendor_selection.artifact_id_for("observe-rubric-a"),
        "conflict_report_ref": vendor_selection.artifact_id_for(
            "observe-conflict-a"
        ),
    }


def test_operator_wait_resume_path_routes_to_decision_packager_and_reloads(
    tmp_path: Path,
) -> None:
    state, _plan, fingerprint, wait_id = (
        vendor_selection.operator_resume_decision_pack_closed_state()
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)
    status = operator_status(loaded, max_events=20)

    wait = status.operator_waits[0]
    assert wait.wait_id == wait_id
    assert wait.status == "resolved"
    assert wait.resolution_kind == "resume_recorded_source"
    assert wait.actor_id == "local-operator-tim"
    assert wait.actor_kind == "local_operator"
    assert wait.target_activation_id == "activation-award-resumed-a"
    assert wait.target_work_item_id is None
    assert wait.closed_work_item_ids == ()

    decision_pack = _decision_pack_payload(
        status,
        "observe-decision-packager-resume-a",
    )
    assert decision_pack["selected_candidate_id"] == "vendor_gamma"
    assert decision_pack["final_refusal_reason"] is None
    assert decision_pack["evidence_refs"]["operator_decision_ref"] == wait_id
    assert decision_pack["selected_plan_fingerprint"] == fingerprint
    assert decision_pack["close_reason"] == "awarded"
    assert str(
        loaded.closed_work_items["work-decision-packager-resume-a"].action_id
    ) == "vendor_selection.decision_packager.decision_pack_ready"


def test_operator_wait_revise_path_routes_rejection_payload_and_reloads(
    tmp_path: Path,
) -> None:
    state, _plan, fingerprint, wait_id = (
        vendor_selection.operator_revise_decision_pack_closed_state()
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)
    status = operator_status(loaded, max_events=20)

    wait = status.operator_waits[0]
    assert wait.wait_id == wait_id
    assert wait.status == "resolved"
    assert wait.resolution_kind == "revise_recorded_source"
    assert wait.actor_id == "local-operator-tim"
    assert wait.actor_kind == "local_operator"
    assert wait.target_work_item_id == "work-operator-decision-a"
    assert wait.target_activation_id == "activation-decision-packager-revise-a"
    assert wait.closed_work_item_ids == ("work-award-a",)
    assert wait.payload_reference == "work_item:work-operator-decision-a:payload"
    assert loaded.work_items["work-operator-decision-a"].payload == (
        vendor_selection.operator_decision_payload(wait_id=wait_id)
    )

    decision_pack = _decision_pack_payload(
        status,
        "observe-decision-packager-revise-a",
    )
    assert decision_pack["selected_candidate_id"] is None
    assert decision_pack["final_refusal_reason"] == "operator_rejected"
    assert decision_pack["evidence_refs"]["operator_decision_ref"] == (
        "work_item:work-operator-decision-a:payload"
    )
    assert decision_pack["selected_plan_fingerprint"] == fingerprint
    assert decision_pack["close_reason"] == "operator_rejected"


def test_operator_wait_refuses_duplicate_stale_wrong_plan_or_status_decisions() -> (
    None
):
    state, _plan, _fingerprint, wait_id = (
        vendor_selection.operator_required_wait_state()
    )
    wait = state.operator_waits[wait_id]
    resume = build_resume_wait(
        state,
        OperatorResumeWaitInput(
            input_id="operator-resume-award-a",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
        ),
    )
    resumed_decision = decide(
        state,
        resume,
        vendor_selection.context(
            "operator-resume-award-a",
            activation_id="activation-award-resumed-a",
        ),
    )
    resumed = apply(state, resumed_decision)
    assert resumed_decision.accepted is True

    duplicate = replace(resume, input_id="operator-resume-award-a-duplicate")
    duplicate_decision = decide(
        resumed,
        duplicate,
        vendor_selection.context("operator-resume-award-a-duplicate"),
    )
    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "operator_wait_not_active"

    closed_source = replace(
        state,
        closed_work_items={
            wait.source_work_item_id: ClosedWorkItemRecord(
                record_id="closed-stale-award-source",
                work_item_id=wait.source_work_item_id,
                source_run_id=wait.source_run_id,
                action_id=wait.source_action_id,
                created_by_input_id="stale-source-close",
            )
        },
    )
    stale_decision = decide(
        closed_source,
        replace(resume, input_id="operator-resume-stale-source"),
        vendor_selection.context(
            "operator-resume-stale-source",
            activation_id="activation-should-not-exist",
        ),
    )
    after_stale = apply(closed_source, stale_decision)
    assert stale_decision.accepted is False
    assert stale_decision.refusal is not None
    assert stale_decision.refusal.reason == "work_item_closed"
    assert "activation-should-not-exist" not in after_stale.activations
    assert after_stale.operator_waits[wait.wait_id].status == "active"

    wrong_plan = replace(
        resume,
        input_id="operator-resume-wrong-plan",
        selected_plan_ref=replace(
            wait.selected_plan_ref,
            authority_fingerprint=f"sha256:{'0' * 64}",
        ),
    )
    wrong_plan_decision = decide(
        state,
        wrong_plan,
        vendor_selection.context("operator-resume-wrong-plan"),
    )
    assert wrong_plan_decision.accepted is False
    assert wrong_plan_decision.refusal is not None
    assert wrong_plan_decision.refusal.reason == "unknown_plan_ref"

    wrong_wait_decision = decide(
        state,
        replace(
            resume,
            input_id="operator-resume-wrong-wait",
            wait_id="operator-wait:missing",
        ),
        vendor_selection.context("operator-resume-wrong-wait"),
    )
    assert wrong_wait_decision.accepted is False
    assert wrong_wait_decision.refusal is not None
    assert wrong_wait_decision.refusal.reason == "unknown_operator_wait"

    wrong_actor_decision = decide(
        state,
        replace(
            resume,
            input_id="operator-resume-wrong-actor",
            actor_kind="remote_operator",
        ),
        vendor_selection.context("operator-resume-wrong-actor"),
    )
    assert wrong_actor_decision.accepted is False
    assert wrong_actor_decision.refusal is not None
    assert wrong_actor_decision.refusal.reason == "invalid_actor_kind"

    unsupported_decision = decide(
        state,
        OperatorCloseWait(
            "operator-close-unsupported-award-wait",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            payload={},
        ),
        vendor_selection.context("operator-close-unsupported-award-wait"),
    )
    assert unsupported_decision.accepted is False
    assert unsupported_decision.refusal is not None
    assert unsupported_decision.refusal.reason == "invalid_operator_wait_resolution"

    status = operator_status(state)
    with pytest.raises(OperatorInputError) as exc_info:
        build_revise_wait(
            state,
            OperatorReviseWaitInput(
                input_id="operator-revise-from-status",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=None,
                actor_id="local-operator-tim",
                actor_kind="local_operator",
                payload={"status": status.operator_waits[0].status},
            ),
        )
    assert exc_info.value.reason == "invalid_payload_schema"
