from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.ids import ActionId, ArtifactSchemaId
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    InitializeWorkspace,
    OperatorCloseWait,
    OperatorResumeWait,
    OperatorReviseWait,
)
from millrace.kernel import apply, decide, empty_runtime_state
from support import lad_learning


def _observe_blocked_wait(stage_id: str = "analyst"):
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, run_id, work_item_id = lad_learning.claimed_learning_stage_state(
        plan,
        fingerprint,
        stage_id=stage_id,
    )
    action_id = f"learning.close_{stage_id}_blocked"
    input_id = f"observe-{stage_id}-blocked"
    transition_input = lad_learning.runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        artifact_payload=lad_learning.artifact_payload(
            lad_learning.LEARNING_REPORT_SCHEMA_ID
        ),
    )
    decision = decide(state, transition_input, lad_learning.context(input_id))
    after = apply(state, decision)
    return (
        plan,
        fingerprint,
        state,
        decision,
        after,
        lad_learning.active_operator_wait(after),
        work_item_id,
    )


def _resume_wait(state, wait, *, input_id: str = "operator-resume-learning-wait"):
    return OperatorResumeWait(
        input_id,
        selected_plan_ref=wait.selected_plan_ref,
        wait_id=wait.wait_id,
        lineage_id=wait.lineage_id,
        actor_id="local_operator",
        actor_kind="local_operator",
        payload={},
    )


def _close_wait(state, wait, *, input_id: str = "operator-close-learning-wait"):
    return OperatorCloseWait(
        input_id,
        selected_plan_ref=wait.selected_plan_ref,
        wait_id=wait.wait_id,
        lineage_id=wait.lineage_id,
        actor_id="local_operator",
        actor_kind="local_operator",
        payload={},
    )


def _revise_wait(
    state,
    wait,
    *,
    input_id: str = "operator-revise-learning-wait",
    payload=None,
):
    return OperatorReviseWait(
        input_id,
        selected_plan_ref=wait.selected_plan_ref,
        wait_id=wait.wait_id,
        lineage_id=wait.lineage_id,
        actor_id="local_operator",
        actor_kind="local_operator",
        payload=payload
        or lad_learning.learning_payload(
            request_id="operator-revised-learning",
            body="Operator supplied a revised Learning request.",
        ),
    )


def test_learning_blocked_recovery_uses_generic_operator_authority() -> None:
    plan, _fingerprint, _before, decision, after, wait, work_item_id = (
        _observe_blocked_wait()
    )

    action = lad_learning.action_by_id(plan, "learning.close_analyst_blocked")
    assert action.action_kind == "operator_wait"
    assert decision.accepted is True
    assert "mutation.record_operator_wait" in lad_learning.mutation_kinds(decision)
    assert "mutation.record_artifact" in lad_learning.mutation_kinds(decision)
    assert "mutation.close_work_item" not in lad_learning.mutation_kinds(decision)
    assert "mutation.record_recovery_attempt" not in lad_learning.mutation_kinds(
        decision
    )
    assert "mutation.record_lineage_quarantine" not in lad_learning.mutation_kinds(
        decision
    )
    assert str(wait.operator_wait_id) == "learning.analyst_blocked_wait"
    assert wait.status == "active"
    assert work_item_id not in after.closed_work_items
    assert after.operator_interventions == {}


def test_learning_blocked_operator_wait_records_full_source_context() -> None:
    _plan, fingerprint, _before, _decision, after, wait, _work_id = (
        _observe_blocked_wait()
    )
    artifact = after.artifacts[wait.source_artifact_id or ""]
    source_run = after.runs[wait.source_run_id]
    source_activation = after.activations[wait.source_activation_id]
    source_work = after.work_items[wait.source_work_item_id]
    receipt = after.receipts[wait.created_input_id]

    assert wait.source_action_id == ActionId("learning.close_analyst_blocked")
    assert wait.created_input_id == "observe-analyst-blocked"
    assert wait.created_input_payload_digest == receipt.receipt_ref.input_payload_digest
    assert source_run.work_item_id == wait.source_work_item_id
    assert source_run.activation_id == wait.source_activation_id
    assert source_activation.work_item_id == wait.source_work_item_id
    assert wait.source_graph_node_id == "learning.standard.analyst"
    assert str(wait.source_stage_kind_id) == "analyst"
    assert str(wait.source_runner_binding_id) == "learning.standard.local_runner"
    assert str(wait.source_queue_family_id) == "learning_request"
    assert str(artifact.schema_id) == lad_learning.LEARNING_REPORT_SCHEMA_ID
    assert artifact.payload_digest.startswith("sha256:")
    assert artifact.source_action_id == wait.source_action_id
    assert artifact.source_run_id == wait.source_run_id
    assert source_work.lineage_id == wait.lineage_id
    assert wait.selected_plan_ref == source_work.ref.plan_ref
    assert wait.selected_plan_fingerprint == fingerprint
    assert str(wait.operator_wait_id) == "learning.analyst_blocked_wait"
    assert wait.wait_id.startswith(f"operator-wait:{fingerprint}:")
    assert wait.status == "active"


def test_learning_resume_creates_selected_audited_activation() -> None:
    _plan, _fingerprint, _before, _blocked_decision, state, wait, _work_id = (
        _observe_blocked_wait()
    )
    resume = _resume_wait(state, wait)

    decision = decide(
        state,
        resume,
        lad_learning.context(
            "operator-resume-learning-wait",
            activation_id="activation-learning-resumed",
        ),
    )
    after = apply(state, decision)
    claimed = lad_learning.apply_accepted_input(
        after,
        ClaimWork(
            "claim-learning-resumed",
            activation_id="activation-learning-resumed",
        ),
        lad_learning.context("claim-learning-resumed", run_id="run-learning-resumed"),
    )

    resolved_wait = claimed.operator_waits[wait.wait_id]
    activation = claimed.activations["activation-learning-resumed"]
    run = claimed.runs["run-learning-resumed"]
    assert decision.accepted is True
    assert "mutation.record_operator_wait" in lad_learning.mutation_kinds(decision)
    assert "mutation.create_activation" in lad_learning.mutation_kinds(decision)
    assert resolved_wait.status == "resolved"
    assert resolved_wait.actor_id == "local_operator"
    assert resolved_wait.actor_kind == "local_operator"
    assert resolved_wait.resolution_kind == "resume_recorded_source"
    assert resolved_wait.target_activation_id == activation.activation_id
    assert activation.work_item_id == wait.source_work_item_id
    assert activation.graph_node_id == wait.source_graph_node_id
    assert activation.stage_kind_id == wait.source_stage_kind_id
    assert activation.runner_binding_id == wait.source_runner_binding_id
    assert run.activation_id == activation.activation_id
    assert claimed.receipts["operator-resume-learning-wait"].accepted is True
    assert claimed.governance_events[-2].authority_source == "operator_wait"
    assert claimed.governance_events[-2].action_id == wait.source_action_id


def test_learning_resume_then_later_close_preserves_original_wait_provenance() -> None:
    plan, fingerprint, _before, _blocked_decision, state, wait, _work_id = (
        _observe_blocked_wait()
    )
    resumed = lad_learning.apply_accepted_input(
        state,
        _resume_wait(state, wait, input_id="operator-resume-before-later-close"),
        lad_learning.context(
            "operator-resume-before-later-close",
            activation_id="activation-learning-resumed-for-close",
        ),
    )
    claimed = lad_learning.apply_accepted_input(
        resumed,
        ClaimWork(
            "claim-learning-resumed-for-close",
            activation_id="activation-learning-resumed-for-close",
        ),
        lad_learning.context(
            "claim-learning-resumed-for-close",
            run_id="run-learning-resumed-for-close",
        ),
    )
    closed = lad_learning.observe(
        claimed,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-learning-resumed-for-close",
        marker="ANALYST_NOOP",
        artifact=lad_learning.artifact_payload(
            lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
        ),
        input_id="observe-resumed-learning-noop-close",
    )

    resolved_wait = closed.operator_waits[wait.wait_id]
    close = closed.closed_work_items[wait.source_work_item_id]
    assert resolved_wait.status == "resolved"
    assert resolved_wait.resolution_kind == "resume_recorded_source"
    assert resolved_wait.created_input_id == wait.created_input_id
    assert resolved_wait.source_run_id == wait.source_run_id
    assert resolved_wait.source_action_id == wait.source_action_id
    assert close.source_run_id == "run-learning-resumed-for-close"
    assert str(close.action_id) == "learning.close_analyst_noop"
    assert close.created_by_input_id == "observe-resumed-learning-noop-close"


def test_learning_close_supersedes_blocked_work_and_audits_operator() -> None:
    _plan, _fingerprint, _before, _blocked_decision, state, wait, _work_id = (
        _observe_blocked_wait()
    )
    close = _close_wait(state, wait)

    decision = decide(
        state,
        close,
        lad_learning.context("operator-close-learning-wait"),
    )
    after = apply(state, decision)

    resolved_wait = after.operator_waits[wait.wait_id]
    close_record = after.closed_work_items[wait.source_work_item_id]
    assert decision.accepted is True
    assert "mutation.record_operator_wait" in lad_learning.mutation_kinds(decision)
    assert "mutation.close_work_item" in lad_learning.mutation_kinds(decision)
    assert resolved_wait.status == "resolved"
    assert resolved_wait.actor_id == "local_operator"
    assert resolved_wait.resolution_kind == "close_recorded_source"
    assert resolved_wait.closed_work_item_ids == (wait.source_work_item_id,)
    assert close_record.source_run_id == wait.source_run_id
    assert close_record.action_id == wait.source_action_id
    assert close_record.created_by_input_id == "operator-close-learning-wait"
    assert after.governance_events[-1].authority_source == "operator_wait"


def test_learning_revise_validates_learning_request_and_preserves_provenance() -> None:
    _plan, _fingerprint, _before, _blocked_decision, state, wait, _work_id = (
        _observe_blocked_wait()
    )
    payload = lad_learning.learning_payload(
        request_id="operator-revised-learning",
        body="Retry Learning from an operator revised request.",
    )
    revise = _revise_wait(state, wait, payload=payload)

    decision = decide(
        state,
        revise,
        lad_learning.context(
            "operator-revise-learning-wait",
            work_item_id="work-operator-revised-learning",
            activation_id="activation-operator-revised-learning",
        ),
    )
    after = apply(state, decision)
    invalid_decision = decide(
        state,
        _revise_wait(
            state,
            wait,
            input_id="operator-revise-learning-invalid-payload",
            payload={"request_id": "missing-body"},
        ),
        lad_learning.context(
            "operator-revise-learning-invalid-payload",
            work_item_id="work-invalid-revised-learning",
            activation_id="activation-invalid-revised-learning",
        ),
    )
    invalid_after = apply(state, invalid_decision)

    resolved_wait = after.operator_waits[wait.wait_id]
    target_work = after.work_items["work-operator-revised-learning"]
    target_activation = after.activations["activation-operator-revised-learning"]
    assert decision.accepted is True
    assert "mutation.record_operator_wait" in lad_learning.mutation_kinds(decision)
    assert "mutation.close_work_item" in lad_learning.mutation_kinds(decision)
    assert "mutation.create_work_item" in lad_learning.mutation_kinds(decision)
    assert "mutation.create_activation" in lad_learning.mutation_kinds(decision)
    assert resolved_wait.status == "resolved"
    assert resolved_wait.created_input_id == wait.created_input_id
    assert resolved_wait.source_run_id == wait.source_run_id
    assert resolved_wait.source_action_id == wait.source_action_id
    assert resolved_wait.resolution_kind == "revise_recorded_source"
    assert resolved_wait.target_work_item_id == target_work.ref.work_item_id
    assert resolved_wait.target_activation_id == target_activation.activation_id
    assert resolved_wait.payload_digest is not None
    assert resolved_wait.payload_reference == (
        "work_item:work-operator-revised-learning:payload"
    )
    assert target_work.payload == payload
    assert str(target_work.queue_family_id) == "learning_request"
    assert target_work.lineage_id == wait.lineage_id
    assert target_activation.work_item_id == target_work.ref.work_item_id
    assert target_activation.graph_node_id == "learning.standard.analyst"
    assert str(target_activation.stage_kind_id) == "analyst"
    assert str(target_activation.runner_binding_id) == "learning.standard.local_runner"
    assert wait.source_work_item_id in after.closed_work_items
    assert invalid_decision.accepted is False
    assert invalid_decision.refusal is not None
    assert invalid_decision.refusal.reason == "invalid_operator_wait_payload_schema"
    assert "mutation.create_work_item" not in lad_learning.mutation_kinds(
        invalid_decision
    )
    assert "work-invalid-revised-learning" not in invalid_after.work_items
    assert invalid_after.operator_waits[wait.wait_id].status == "active"


def test_learning_intervention_refuses_wrong_lineage_or_source() -> None:
    _plan, _fingerprint, _before, _blocked_decision, state, wait, _work_id = (
        _observe_blocked_wait()
    )
    wrong_lineage = replace(_resume_wait(state, wait), lineage_id="foreign-lineage")
    wrong_source_wait = replace(
        wait,
        source_action_id=ActionId("learning.close_professor_blocked"),
    )
    wrong_source_state = replace(
        state,
        operator_waits={wait.wait_id: wrong_source_wait},
    )

    lineage_decision = decide(
        state,
        wrong_lineage,
        lad_learning.context("operator-resume-wrong-lineage"),
    )
    lineage_after = apply(state, lineage_decision)
    source_decision = decide(
        wrong_source_state,
        _resume_wait(wrong_source_state, wrong_source_wait),
        lad_learning.context("operator-resume-wrong-source"),
    )
    source_after = apply(wrong_source_state, source_decision)

    assert lineage_decision.accepted is False
    assert lineage_decision.refusal is not None
    assert lineage_decision.refusal.reason == "operator_wait_lineage_mismatch"
    assert source_decision.accepted is False
    assert source_decision.refusal is not None
    assert source_decision.refusal.reason == "invalid_operator_wait"
    assert lineage_after.operator_waits[wait.wait_id].status == "active"
    assert source_after.operator_waits[wait.wait_id].status == "active"
    assert "mutation.create_activation" not in lad_learning.mutation_kinds(
        lineage_decision
    )
    assert "mutation.record_operator_wait" not in lad_learning.mutation_kinds(
        source_decision
    )


def test_learning_intervention_refuses_missing_actor_or_unselected_option() -> None:
    _plan, _fingerprint, _before, _blocked_decision, state, wait, _work_id = (
        _observe_blocked_wait()
    )
    with pytest.raises(ValueError, match="actor_id"):
        OperatorResumeWait(
            "operator-resume-missing-actor",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="",
            actor_kind="local_operator",
            payload={},
        )

    wrong_actor = replace(_resume_wait(state, wait), actor_kind="remote_operator")
    drifted_waits = tuple(
        replace(
            selected_wait,
            allowed_resolution_kinds=("resume_recorded_source",),
        )
        if str(selected_wait.id) == "learning.analyst_blocked_wait"
        else selected_wait
        for selected_wait in state.admitted_plans[
            wait.selected_plan_fingerprint
        ].selected_plan.operator_waits
    )
    admitted = state.admitted_plans[wait.selected_plan_fingerprint]
    drifted_state = replace(
        state,
        admitted_plans={
            wait.selected_plan_fingerprint: replace(
                admitted,
                selected_plan=replace(
                    admitted.selected_plan,
                    operator_waits=drifted_waits,
                ),
            )
        },
    )

    actor_decision = decide(
        state,
        wrong_actor,
        lad_learning.context("operator-resume-wrong-actor-kind"),
    )
    unselected_decision = decide(
        drifted_state,
        _close_wait(drifted_state, wait),
        lad_learning.context("operator-close-unselected-resolution"),
    )

    assert actor_decision.accepted is False
    assert actor_decision.refusal is not None
    assert actor_decision.refusal.reason == "invalid_actor_kind"
    assert unselected_decision.accepted is False
    assert unselected_decision.refusal is not None
    assert unselected_decision.refusal.reason == "invalid_operator_wait_resolution"
    assert "mutation.record_operator_wait" not in lad_learning.mutation_kinds(
        actor_decision
    )
    assert "mutation.close_work_item" not in lad_learning.mutation_kinds(
        unselected_decision
    )


def test_admission_refuses_learning_operator_wait_authority_drift() -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()
    drifted_waits = tuple(
        replace(wait, actor_kind="remote_operator")
        if str(wait.id) == "learning.analyst_blocked_wait"
        else wait
        for wait in plan.operator_waits
    )
    drifted_plan = replace(plan, operator_waits=drifted_waits)
    drifted_fingerprint = authority_fingerprint(drifted_plan)
    state = lad_learning.apply_accepted_input(
        empty_runtime_state(),
        InitializeWorkspace("init-learning-authority-drift"),
        lad_learning.context("init-learning-authority-drift"),
    )

    decision = decide(
        state,
        AdmitPlan(
            "admit-learning-authority-drift",
            selected_plan=drifted_plan,
            authority_fingerprint=drifted_fingerprint,
        ),
        lad_learning.context("admit-learning-authority-drift"),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        "operator_wait_actor_kind:learning.analyst_blocked_wait"
    )
    assert drifted_fingerprint not in after.admitted_plans


def test_admission_refuses_learning_operator_wait_revise_schema_drift() -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()
    drifted_waits = tuple(
        replace(
            wait,
            payload_schema_id=ArtifactSchemaId(
                lad_learning.LEARNING_REPORT_SCHEMA_ID
            ),
        )
        if str(wait.id) == "learning.analyst_blocked_wait"
        else wait
        for wait in plan.operator_waits
    )
    drifted_plan = replace(plan, operator_waits=drifted_waits)
    drifted_fingerprint = authority_fingerprint(drifted_plan)
    state = lad_learning.apply_accepted_input(
        empty_runtime_state(),
        InitializeWorkspace("init-learning-revise-schema-drift"),
        lad_learning.context("init-learning-revise-schema-drift"),
    )

    decision = decide(
        state,
        AdmitPlan(
            "admit-learning-revise-schema-drift",
            selected_plan=drifted_plan,
            authority_fingerprint=drifted_fingerprint,
        ),
        lad_learning.context("admit-learning-revise-schema-drift"),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        "operator_wait_target:learning.analyst_blocked_wait"
    )
    assert drifted_fingerprint not in after.admitted_plans


def test_learning_recovery_after_closed_source_preserves_trigger_provenance() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, wait = lad_learning.closed_source_learning_blocked_wait_state(
        plan,
        fingerprint,
    )

    source_close = state.closed_work_items["work-consultant-closed-source"]
    fanout = lad_learning.closed_source_learning_fanout(state)

    assert str(source_close.action_id) == "execution.close_consultant_needs_plan"
    assert source_close.source_run_id == "run-consultant-closed-source"
    assert source_close.created_by_input_id == "observe-consultant-closed-source"
    assert fanout.source_work_item_id == source_close.work_item_id
    assert fanout.source_run_id == source_close.source_run_id
    assert fanout.source_action_id == source_close.action_id
    assert fanout.created_by_input_id == source_close.created_by_input_id
    assert wait.source_work_item_id == fanout.target_work_item_id
    assert wait.source_run_id == "run-closed-source-learning"
    assert str(wait.source_action_id) == "learning.close_analyst_blocked"
    assert wait.lineage_id == fanout.lineage_id
    assert wait.selected_plan_fingerprint == fingerprint
    assert state.work_items[wait.source_work_item_id].lineage_id == fanout.lineage_id


@pytest.mark.parametrize("resolution_kind", ("resume", "close", "revise"))
def test_learning_recovery_cannot_reopen_or_mutate_closed_source_work(
    resolution_kind: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, wait = lad_learning.closed_source_learning_blocked_wait_state(
        plan,
        fingerprint,
    )
    source_work = state.work_items["work-consultant-closed-source"]
    source_close = state.closed_work_items[source_work.ref.work_item_id]

    if resolution_kind == "resume":
        transition_input = _resume_wait(
            state,
            wait,
            input_id="operator-resume-closed-source-learning",
        )
        transition_context = lad_learning.context(
            "operator-resume-closed-source-learning",
            activation_id="activation-closed-source-learning-resumed",
        )
    elif resolution_kind == "close":
        transition_input = _close_wait(
            state,
            wait,
            input_id="operator-close-closed-source-learning",
        )
        transition_context = lad_learning.context(
            "operator-close-closed-source-learning"
        )
    else:
        transition_input = _revise_wait(
            state,
            wait,
            input_id="operator-revise-closed-source-learning",
        )
        transition_context = lad_learning.context(
            "operator-revise-closed-source-learning",
            work_item_id="work-closed-source-learning-revised",
            activation_id="activation-closed-source-learning-revised",
        )

    decision = decide(state, transition_input, transition_context)
    after = apply(state, decision)

    assert decision.accepted is True
    assert after.work_items[source_work.ref.work_item_id] == source_work
    assert after.closed_work_items[source_work.ref.work_item_id] == source_close
    assert after.closed_work_items[source_work.ref.work_item_id].source_run_id == (
        "run-consultant-closed-source"
    )
    assert str(
        after.closed_work_items[source_work.ref.work_item_id].action_id
    ) == "execution.close_consultant_needs_plan"
