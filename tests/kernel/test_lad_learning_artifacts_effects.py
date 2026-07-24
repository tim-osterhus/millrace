from __future__ import annotations

from collections.abc import Mapping

import pytest

from millrace.contracts.compiled_plan import AuthorityValue, SelectedCompiledPlan
from millrace.contracts.state import ArtifactRecord, RuntimeState
from millrace.kernel import apply, decide
from support import lad_learning


def _apply_observation(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    artifact: dict[str, AuthorityValue],
    work_item_id: str | None = None,
    activation_id: str | None = None,
) -> tuple[RuntimeState, ArtifactRecord]:
    decision = decide(
        state,
        lad_learning.runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=run_id,
            action_id=action_id,
            input_id=input_id,
            artifact_payload=artifact,
        ),
        lad_learning.context(
            input_id,
            work_item_id=work_item_id,
            activation_id=activation_id,
        ),
    )
    assert decision.accepted is True
    after = apply(state, decision)
    artifact_record = _artifact_for_input(after, input_id)
    assert str(artifact_record.source_action_id) == action_id
    assert artifact_record.payload == artifact
    return after, artifact_record


def _artifact_for_input(state: RuntimeState, input_id: str) -> ArtifactRecord:
    artifacts = tuple(
        artifact
        for artifact in state.artifacts.values()
        if artifact.created_by_input_id == input_id
    )
    assert len(artifacts) == 1
    return artifacts[0]


def _effect_proposal_for_input(state: RuntimeState, input_id: str):
    proposals = tuple(
        proposal
        for proposal in state.effect_proposals.values()
        if proposal.created_input_id == input_id
    )
    assert len(proposals) == 1
    return proposals[0]


def _proposal_state(
    stage_id: str,
    *,
    input_id: str,
) -> tuple[SelectedCompiledPlan, str, RuntimeState, str]:
    plan, fingerprint, state, run_id, _work_item_id = _claimed_stage(stage_id)
    action_id, schema_id = {
        "curator": (
            "learning.close_curator_complete",
            lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID,
        ),
        "librarian": (
            "learning.close_librarian_complete",
            lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
        ),
    }[stage_id]
    after, _artifact = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        artifact=dict(lad_learning.artifact_payload(schema_id)),
    )
    proposal = _effect_proposal_for_input(after, input_id)
    return plan, fingerprint, after, proposal.effect_id


def _reconcile_effect(
    state: RuntimeState,
    *,
    effect_id: str,
    provider_ref: str = lad_learning.FAKE_LOCAL_EFFECT_PROVIDER_REF,
    status: str = "applied",
    result: Mapping[str, AuthorityValue] | None = None,
    input_id: str = "reconcile-effect",
):
    from millrace.contracts.transition import ReconcileEffect

    transition_input = ReconcileEffect(
        input_id,
        effect_id=effect_id,
        provider_ref=provider_ref,
        status=status,
        result=result or {
            "provider_result_id": "fake-local-result-1",
            "summary": "Recorded as fake local evidence only.",
        },
    )
    decision = decide(state, transition_input, lad_learning.context(input_id))
    return decision, apply(state, decision)


def _claimed_stage(
    stage_id: str,
) -> tuple[SelectedCompiledPlan, str, RuntimeState, str, str]:
    plan, fingerprint = lad_learning.compile_lad_learning()
    if stage_id == "librarian":
        state = lad_learning.planning_closure_with_generated_learning_state(
            plan,
            fingerprint,
            active_learning=True,
        )
        run = state.runs["run-closure-librarian"]
        return plan, fingerprint, state, "run-closure-librarian", run.work_item_id

    state = lad_learning.ready_learning_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-analyst",
        input_id="claim-analyst",
    )
    if stage_id == "analyst":
        return plan, fingerprint, state, "run-analyst", "work-learning-request"

    state, _artifact = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-analyst",
        action_id="learning.route_analyst_complete",
        input_id="observe-analyst-complete-for-professor",
        artifact=dict(
            lad_learning.artifact_payload(
                lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
            )
        ),
        work_item_id="work-professor",
        activation_id="activation-professor",
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-professor",
        run_id="run-professor",
        input_id="claim-professor",
    )
    if stage_id == "professor":
        return plan, fingerprint, state, "run-professor", "work-professor"

    state, _artifact = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-professor",
        action_id="learning.route_professor_complete",
        input_id="observe-professor-complete-for-curator",
        artifact=dict(
            lad_learning.artifact_payload(
                lad_learning.LEARNING_SKILL_CANDIDATE_SCHEMA_ID
            )
        ),
        work_item_id="work-curator",
        activation_id="activation-curator",
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-curator",
        run_id="run-curator",
        input_id="claim-curator",
    )
    return plan, fingerprint, state, "run-curator", "work-curator"


@pytest.mark.parametrize(
    (
        "stage_id",
        "action_id",
        "input_id",
        "schema_id",
        "target_work_item_id",
        "target_activation_id",
    ),
    (
        (
            "analyst",
            "learning.route_analyst_complete",
            "observe-analyst-complete",
            lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID,
            "work-professor",
            "activation-professor",
        ),
        (
            "analyst",
            "learning.close_analyst_noop",
            "observe-analyst-noop",
            lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID,
            None,
            None,
        ),
        (
            "professor",
            "learning.route_professor_complete",
            "observe-professor-complete",
            lad_learning.LEARNING_SKILL_CANDIDATE_SCHEMA_ID,
            "work-curator",
            "activation-curator",
        ),
        (
            "professor",
            "learning.close_professor_noop",
            "observe-professor-noop",
            lad_learning.LEARNING_PROFESSOR_NOTES_SCHEMA_ID,
            None,
            None,
        ),
        (
            "curator",
            "learning.close_curator_complete",
            "observe-curator-complete",
            lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID,
            None,
            None,
        ),
        (
            "curator",
            "learning.close_curator_noop",
            "observe-curator-noop",
            lad_learning.LEARNING_CURATOR_DECISION_SCHEMA_ID,
            None,
            None,
        ),
        (
            "librarian",
            "learning.close_librarian_complete",
            "observe-librarian-complete",
            lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
            None,
            None,
        ),
        (
            "librarian",
            "learning.close_librarian_noop",
            "observe-librarian-noop",
            lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
            None,
            None,
        ),
    ),
)
def test_learning_stage_artifacts_route_and_close_by_selected_schema(
    stage_id: str,
    action_id: str,
    input_id: str,
    schema_id: str,
    target_work_item_id: str | None,
    target_activation_id: str | None,
) -> None:
    plan, fingerprint, state, run_id, work_item_id = _claimed_stage(stage_id)

    after, artifact = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        artifact=dict(lad_learning.artifact_payload(schema_id)),
        work_item_id=target_work_item_id,
        activation_id=target_activation_id,
    )

    assert str(artifact.schema_id) == schema_id
    if target_activation_id is None:
        close = after.closed_work_items[work_item_id]
        assert str(close.action_id) == action_id
        assert close.source_run_id == run_id
    else:
        assert target_activation_id in after.activations
        assert target_work_item_id in after.work_items


@pytest.mark.parametrize(
    "artifact",
    (
        {"artifact_kind": lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID},
        {
            "artifact_kind": "learning.artifacts.wrong_kind",
            "summary": "Wrong schema marker",
            "research_notes": "This should not be accepted.",
        },
        {
            "artifact_kind": lad_learning.LEARNING_STAGE_RESULT_SCHEMA_ID,
            "summary": "Stage result must not be terminal evidence.",
        },
    ),
)
def test_learning_artifact_payload_refusals_have_no_side_effects(
    artifact: dict[str, AuthorityValue],
) -> None:
    plan, fingerprint, state, _run_id, _work_item_id = _claimed_stage("analyst")

    decision = decide(
        state,
        lad_learning.runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-analyst",
            action_id="learning.route_analyst_complete",
            input_id=f"observe-invalid-{artifact['artifact_kind']}",
            artifact_payload=artifact,
        ),
        lad_learning.context(
            f"observe-invalid-{artifact['artifact_kind']}",
            work_item_id="work-professor-invalid",
            activation_id="activation-professor-invalid",
        ),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    assert "mutation.record_artifact" not in lad_learning.mutation_kinds(decision)
    assert "mutation.create_work_item" not in lad_learning.mutation_kinds(decision)
    assert "mutation.create_activation" not in lad_learning.mutation_kinds(decision)
    assert "mutation.route_activation" not in lad_learning.mutation_kinds(decision)
    assert after.artifacts == state.artifacts
    assert after.work_items == state.work_items
    assert after.activations == state.activations
    assert after.closed_work_items == state.closed_work_items


@pytest.mark.parametrize("stage_id", ("analyst", "professor", "curator", "librarian"))
def test_learning_blocked_records_selected_report_artifact(stage_id: str) -> None:
    plan, fingerprint, state, run_id, work_item_id = _claimed_stage(stage_id)
    action_id = f"learning.close_{stage_id}_blocked"

    after, artifact = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=f"observe-{stage_id}-blocked",
        artifact=dict(
            lad_learning.artifact_payload(lad_learning.LEARNING_REPORT_SCHEMA_ID)
        ),
    )

    assert str(artifact.schema_id) == lad_learning.LEARNING_REPORT_SCHEMA_ID
    wait = lad_learning.active_operator_wait(after)
    assert str(wait.source_action_id) == action_id
    assert wait.source_run_id == run_id
    assert wait.source_work_item_id == work_item_id
    assert wait.source_artifact_id == artifact.artifact_id
    assert wait.status == "active"
    assert work_item_id not in after.closed_work_items


@pytest.mark.parametrize(
    ("stage_id", "action_id", "input_id", "schema_id", "effect_declaration_id"),
    (
        (
            "curator",
            "learning.close_curator_complete",
            "observe-curator-complete-effect",
            lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID,
            lad_learning.CURATOR_EFFECT_DECLARATION_ID,
        ),
        (
            "librarian",
            "learning.close_librarian_complete",
            "observe-librarian-complete-effect",
            lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
            lad_learning.LIBRARIAN_EFFECT_DECLARATION_ID,
        ),
    ),
)
def test_curator_records_selected_fake_local_effect_proposal(
    stage_id: str,
    action_id: str,
    input_id: str,
    schema_id: str,
    effect_declaration_id: str,
) -> None:
    plan, fingerprint, state, run_id, work_item_id = _claimed_stage(stage_id)

    after, artifact = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        artifact=dict(lad_learning.artifact_payload(schema_id)),
    )

    proposal = _effect_proposal_for_input(after, input_id)
    run, activation = lad_learning.run_activation(after, run_id)
    effect_declaration = next(
        effect
        for effect in plan.effect_declarations
        if str(effect.effect_declaration_id) == effect_declaration_id
    )

    assert proposal.effect_id == f"transition-{input_id}:effect"
    assert proposal.dedupe_key == f"{effect_declaration_id}:{artifact.artifact_id}"
    assert str(proposal.effect_declaration_id) == effect_declaration_id
    assert proposal.selected_plan_ref == run.run_ref.plan_ref
    assert proposal.selected_plan_fingerprint == fingerprint
    assert str(proposal.terminal_action_id) == action_id
    assert proposal.artifact_id == artifact.artifact_id
    assert str(proposal.artifact_schema_id) == schema_id
    assert proposal.artifact_payload_digest == artifact.payload_digest
    assert proposal.source_run_id == run_id
    assert str(proposal.source_action_id) == action_id
    assert proposal.source_input_id == input_id
    assert proposal.source_work_item_id == work_item_id
    assert proposal.source_activation_id == run.activation_id
    assert proposal.source_graph_node_id == activation.graph_node_id
    assert proposal.source_stage_kind_id == run.stage_kind_id
    assert proposal.source_runner_binding_id == run.runner_binding_id
    assert proposal.source_queue_family_id == activation.queue_family_id
    assert proposal.lineage_id == after.work_items[work_item_id].lineage_id
    assert proposal.provider_ref == effect_declaration.provider_ref
    assert proposal.capability_policy_ref == effect_declaration.capability_policy_ref
    assert proposal.target_ref_kind == effect_declaration.target_ref_kind
    assert proposal.target_ref_schema == effect_declaration.target_ref_schema
    assert proposal.target_skill_id == artifact.payload["target_skill_id"]
    assert proposal.status == "pending"
    assert proposal.created_input_id == input_id
    assert proposal.created_transition_id == f"transition-{input_id}"
    close = after.closed_work_items[work_item_id]
    assert str(close.action_id) == action_id


def test_librarian_records_selected_fake_local_effect_proposal() -> None:
    plan, fingerprint, state, run_id, _work_item_id = _claimed_stage("librarian")

    after, artifact = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id="learning.close_librarian_complete",
        input_id="observe-librarian-complete-path-effect",
        artifact=dict(
            lad_learning.artifact_payload(
                lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID
            )
        ),
    )

    proposal = _effect_proposal_for_input(
        after,
        "observe-librarian-complete-path-effect",
    )
    assert str(proposal.effect_declaration_id) == (
        lad_learning.LIBRARIAN_EFFECT_DECLARATION_ID
    )
    assert proposal.target_skill_id == artifact.payload["target_skill_id"]
    assert proposal.target_path_ref == artifact.payload["installed_path"]


def test_librarian_effect_reconciliation_is_idempotent() -> None:
    _plan, _fingerprint, state, effect_id = _proposal_state(
        "librarian",
        input_id="observe-librarian-complete-for-reconcile",
    )

    decision, reconciled = _reconcile_effect(
        state,
        effect_id=effect_id,
        input_id="reconcile-librarian-effect",
    )
    replay_decision, replayed = _reconcile_effect(
        reconciled,
        effect_id=effect_id,
        input_id="reconcile-librarian-effect-replay",
    )

    assert decision.accepted is True
    assert replay_decision.accepted is True
    assert len(reconciled.effect_reconciliations) == 1
    assert len(replayed.effect_reconciliations) == 1
    reconciliation = next(iter(replayed.effect_reconciliations.values()))
    assert reconciliation.effect_id == effect_id
    assert reconciliation.status == "applied"
    assert reconciliation.provider_ref == lad_learning.FAKE_LOCAL_EFFECT_PROVIDER_REF
    assert reconciliation.fake_local_result_digest.startswith("sha256:")


@pytest.mark.parametrize("status", ("no_op", "refused"))
def test_learning_effect_reconciliation_accepts_selected_non_applied_statuses(
    status: str,
) -> None:
    _plan, _fingerprint, state, effect_id = _proposal_state(
        "librarian",
        input_id=f"observe-librarian-complete-for-{status}-reconcile",
    )

    decision, after = _reconcile_effect(
        state,
        effect_id=effect_id,
        status=status,
        input_id=f"reconcile-librarian-effect-{status}",
    )

    assert decision.accepted is True
    reconciliation = next(iter(after.effect_reconciliations.values()))
    assert reconciliation.effect_id == effect_id
    assert reconciliation.status == status


def test_learning_effect_conflicting_reconciliation_replay_is_refused() -> None:
    _plan, _fingerprint, state, effect_id = _proposal_state(
        "librarian",
        input_id="observe-librarian-complete-for-conflict",
    )
    _decision, reconciled = _reconcile_effect(
        state,
        effect_id=effect_id,
        input_id="reconcile-librarian-effect-conflict-base",
    )

    decision, after = _reconcile_effect(
        reconciled,
        effect_id=effect_id,
        status="no_op",
        input_id="reconcile-librarian-effect-conflict",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "effect_reconciliation_conflict"
    assert after.effect_reconciliations == reconciled.effect_reconciliations


def test_learning_effect_reconciliation_refuses_unselected_provider_result() -> None:
    _plan, _fingerprint, state, effect_id = _proposal_state(
        "librarian",
        input_id="observe-librarian-complete-for-provider-refusal",
    )

    decision, after = _reconcile_effect(
        state,
        effect_id=effect_id,
        provider_ref="provider.real.workspace",
        input_id="reconcile-librarian-effect-invalid-provider",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unselected_effect_provider"
    assert after.effect_reconciliations == state.effect_reconciliations


def test_learning_effect_attempt_refuses_real_side_effect_authority() -> None:
    _plan, _fingerprint, state, effect_id = _proposal_state(
        "curator",
        input_id="observe-curator-complete-for-real-effect-refusal",
    )

    decision, after = _reconcile_effect(
        state,
        effect_id=effect_id,
        result={
            "provider_result_id": "fake-local-result-2",
            "requested_runtime_mutation": "install_or_update_skill",
        },
        input_id="reconcile-curator-real-effect-attempt",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "effect_result_requests_runtime_mutation"
    assert after.effect_reconciliations == state.effect_reconciliations


def test_learning_effect_reconciliation_refuses_missing_proposal() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.ready_learning_state(plan, fingerprint)

    decision, after = _reconcile_effect(
        state,
        effect_id="missing-effect",
        input_id="reconcile-missing-effect",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "effect_proposal_not_found"
    assert after.effect_reconciliations == state.effect_reconciliations


def test_learning_effect_reconciliation_refuses_unsupported_status() -> None:
    _plan, _fingerprint, state, effect_id = _proposal_state(
        "librarian",
        input_id="observe-librarian-complete-for-status-refusal",
    )

    decision, after = _reconcile_effect(
        state,
        effect_id=effect_id,
        status="pending",
        input_id="reconcile-librarian-effect-invalid-status",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_effect_reconciliation_status"
    assert after.effect_reconciliations == state.effect_reconciliations


def test_learning_effect_after_closed_source_preserves_trigger_provenance() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.closed_source_learning_effect_state(
        plan,
        fingerprint,
        reconciliation_status="applied",
    )

    source_close = state.closed_work_items["work-consultant-closed-source"]
    fanout = lad_learning.closed_source_learning_fanout(state)
    proposal = next(iter(state.effect_proposals.values()))
    reconciliation = next(iter(state.effect_reconciliations.values()))

    assert str(source_close.action_id) == "execution.close_consultant_needs_plan"
    assert source_close.source_run_id == "run-consultant-closed-source"
    assert source_close.created_by_input_id == "observe-consultant-closed-source"
    assert source_close.close_kind == "terminal_action"
    assert fanout.source_work_item_id == source_close.work_item_id
    assert fanout.source_run_id == source_close.source_run_id
    assert fanout.source_action_id == source_close.action_id
    assert fanout.created_by_input_id == source_close.created_by_input_id
    assert (
        state.work_items[fanout.target_work_item_id].lineage_id
        == proposal.lineage_id
    )
    assert proposal.source_work_item_id == "work-closed-source-curator"
    assert proposal.lineage_id == fanout.lineage_id
    assert str(proposal.source_action_id) == "learning.close_curator_complete"
    assert proposal.source_input_id == "observe-closed-source-curator-complete"
    assert proposal.status == "pending"
    assert reconciliation.effect_id == proposal.effect_id
    assert reconciliation.status == "applied"
    assert state.work_items[source_close.work_item_id].lineage_id == fanout.lineage_id
