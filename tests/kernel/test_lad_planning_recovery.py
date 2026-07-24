from __future__ import annotations

import millrace.operator as operator_api
from millrace.kernel import apply, decide
from support.lad_planning import (
    REPORT_SCHEMA_ID,
    apply_runner_observation,
    artifact_payload,
    bootstrap_route_claim,
    claim_activation,
    compile_lad_planning,
    planning_context,
    runner_observation,
)


def _only_recovery_attempt(state):
    attempts = tuple(state.recovery_attempts.values())
    assert len(attempts) == 1
    return attempts[0]


def _planner_quarantined_state(plan, fingerprint):
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )
    state, _first_block = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner",
        action_id="planning.route_planner_blocked",
        input_id="observe-planner-blocked",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
        target_activation_id="activation-mechanic",
    )
    state = claim_activation(
        state,
        activation_id="activation-mechanic",
        run_id="run-mechanic",
        input_id="claim-mechanic",
    )
    state, _mechanic_complete = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-mechanic",
        action_id="planning.route_mechanic_complete",
        input_id="observe-mechanic-complete",
        target_work_item_id="work-planner-resume",
        target_activation_id="activation-planner-resume",
    )
    state = claim_activation(
        state,
        activation_id="activation-planner-resume",
        run_id="run-planner-resume",
        input_id="claim-planner-resume",
    )
    state, exhausted = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner-resume",
        action_id="planning.route_planner_blocked",
        input_id="observe-planner-blocked-exhausted",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
        target_activation_id="activation-should-not-exist",
    )
    assert exhausted.accepted is True
    return state


def _route_to_manager_claim(plan, fingerprint):
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )
    state, _planner_complete = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner",
        action_id="planning.route_planner_complete",
        input_id="observe-planner-complete",
        target_work_item_id="work-manager",
        target_activation_id="activation-manager",
    )
    return claim_activation(
        state,
        activation_id="activation-manager",
        run_id="run-manager",
        input_id="claim-manager",
    )


def _route_to_auditor_claim(plan, fingerprint):
    return bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="incident",
        activation_id="activation-auditor",
        run_id="run-auditor",
        work_item_id="work-incident",
    )


def test_lad_planning_mechanic_recovery_exhausts_through_selected_quarantine() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )

    state, first_block = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner",
        action_id="planning.route_planner_blocked",
        input_id="observe-planner-blocked",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
        target_activation_id="activation-mechanic",
    )

    assert first_block.accepted is True
    assert {mutation.mutation_kind for mutation in first_block.mutations} >= {
        "mutation.create_activation",
        "mutation.record_recovery_attempt",
        "mutation.record_counter",
    }
    attempt = _only_recovery_attempt(state)
    assert attempt.attempt_count == 1
    assert attempt.phase == "active_recovery"
    assert str(attempt.policy_id) == "planning.blocked.recovery"
    assert str(state.activations["activation-mechanic"].stage_kind_id) == "lad_mechanic"

    state = claim_activation(
        state,
        activation_id="activation-mechanic",
        run_id="run-mechanic",
        input_id="claim-mechanic",
    )
    state, mechanic_complete = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-mechanic",
        action_id="planning.route_mechanic_complete",
        input_id="observe-mechanic-complete",
        target_work_item_id="work-planner-resume",
        target_activation_id="activation-planner-resume",
    )
    assert mechanic_complete.accepted is True
    assert str(state.activations["activation-planner-resume"].stage_kind_id) == (
        "lad_planner"
    )

    state = claim_activation(
        state,
        activation_id="activation-planner-resume",
        run_id="run-planner-resume",
        input_id="claim-planner-resume",
    )
    state, exhausted = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner-resume",
        action_id="planning.route_planner_blocked",
        input_id="observe-planner-blocked-exhausted",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
        target_activation_id="activation-should-not-exist",
    )

    assert exhausted.accepted is True
    mutation_kinds = {mutation.mutation_kind for mutation in exhausted.mutations}
    assert "mutation.record_lineage_quarantine" in mutation_kinds
    assert "mutation.close_work_item" not in mutation_kinds
    assert "mutation.create_activation" not in mutation_kinds
    attempt = _only_recovery_attempt(state)
    assert attempt.attempt_count == 2
    assert attempt.phase == "quarantine_eligible"
    assert str(attempt.recovery_action_id) == "planning.route_planner_blocked"
    assert state.closed_work_items == {}
    quarantine = next(iter(state.lineage_quarantines.values()))
    assert quarantine.lineage_id == "work-spec"
    assert str(quarantine.policy_id) == "planning.blocked.recovery"
    assert str(quarantine.action_id) == "planning.quarantine_mechanic_blocked"
    assert quarantine.recovery_attempt_record_id == attempt.record_id
    assert quarantine.original_source_run_id == "run-planner-resume"
    assert quarantine.original_source_work_item_id == "work-planner-resume"
    assert quarantine.status == "active"
    assert "activation-should-not-exist" not in state.activations


def test_lad_planning_manager_blocked_routes_to_selected_mechanic_recovery() -> None:
    plan, fingerprint = compile_lad_planning()
    state = _route_to_manager_claim(plan, fingerprint)

    state, blocked = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-manager",
        action_id="planning.route_manager_blocked",
        input_id="observe-manager-blocked",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
        target_activation_id="activation-mechanic-manager",
    )

    assert blocked.accepted is True
    assert {
        "mutation.create_activation",
        "mutation.record_recovery_attempt",
        "mutation.record_counter",
    } <= {mutation.mutation_kind for mutation in blocked.mutations}
    attempt = _only_recovery_attempt(state)
    assert str(attempt.policy_id) == "planning.blocked.recovery"
    assert str(attempt.recovery_action_id) == "planning.route_manager_blocked"
    assert attempt.attempt_count == 1
    assert attempt.phase == "active_recovery"
    assert str(state.activations["activation-mechanic-manager"].stage_kind_id) == (
        "lad_mechanic"
    )
    counter = next(iter(state.counters.values()))
    assert str(counter.counter_id) == "planning.mechanic_attempt_count.manager"
    assert counter.value == 1


def test_lad_planning_auditor_blocked_routes_to_selected_mechanic_recovery() -> None:
    plan, fingerprint = compile_lad_planning()
    state = _route_to_auditor_claim(plan, fingerprint)

    state, blocked = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-auditor",
        action_id="planning.route_auditor_blocked",
        input_id="observe-auditor-blocked",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
        target_activation_id="activation-mechanic-auditor",
    )

    assert blocked.accepted is True
    attempt = _only_recovery_attempt(state)
    assert str(attempt.recovery_action_id) == "planning.route_auditor_blocked"
    assert attempt.attempt_count == 1
    assert attempt.phase == "active_recovery"
    assert str(state.activations["activation-mechanic-auditor"].stage_kind_id) == (
        "lad_mechanic"
    )
    counter = next(iter(state.counters.values()))
    assert str(counter.counter_id) == "planning.mechanic_attempt_count.auditor"
    assert counter.value == 1


def test_lad_planning_mechanic_blocked_uses_selected_threshold_quarantine() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )
    state, _blocked = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner",
        action_id="planning.route_planner_blocked",
        input_id="observe-planner-blocked",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
        target_activation_id="activation-mechanic",
    )
    state = claim_activation(
        state,
        activation_id="activation-mechanic",
        run_id="run-mechanic",
        input_id="claim-mechanic",
    )

    state, mechanic_blocked = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-mechanic",
        action_id="planning.route_mechanic_blocked",
        input_id="observe-mechanic-blocked",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
        target_activation_id="activation-mechanic-retry",
    )

    assert mechanic_blocked.accepted is True
    mutation_kinds = {mutation.mutation_kind for mutation in mechanic_blocked.mutations}
    assert "mutation.record_lineage_quarantine" in mutation_kinds
    assert "mutation.create_activation" not in mutation_kinds
    attempt = _only_recovery_attempt(state)
    assert attempt.attempt_count == 2
    assert attempt.phase == "quarantine_eligible"
    assert str(attempt.recovery_action_id) == "planning.route_mechanic_blocked"
    counter = next(
        counter
        for counter in state.counters.values()
        if str(counter.counter_id) == "planning.mechanic_attempt_count.mechanic"
    )
    assert counter.value == 1
    quarantine = next(iter(state.lineage_quarantines.values()))
    assert quarantine.original_source_run_id == "run-mechanic"
    assert quarantine.original_source_work_item_id == "work-spec"
    assert str(quarantine.action_id) == "planning.quarantine_mechanic_blocked"
    assert "activation-mechanic-retry" not in state.activations


def test_lad_planning_mechanic_complete_uses_selected_resume_stage_metadata() -> None:
    plan, fingerprint = compile_lad_planning()
    for resume_stage, expected_stage, expected_graph in (
        ("manager", "lad_manager", "planning.lad.manager.start"),
        ("auditor", "lad_auditor", "planning.lad.auditor.start"),
    ):
        state = bootstrap_route_claim(
            plan,
            fingerprint,
            queue_family_id="spec",
            activation_id=f"activation-planner-{resume_stage}",
            run_id=f"run-planner-{resume_stage}",
            work_item_id=f"work-spec-{resume_stage}",
        )
        state, _blocked = apply_runner_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-planner-{resume_stage}",
            action_id="planning.route_planner_blocked",
            input_id=f"observe-planner-blocked-{resume_stage}",
            artifact=artifact_payload(REPORT_SCHEMA_ID),
            target_activation_id=f"activation-mechanic-{resume_stage}",
        )
        state = claim_activation(
            state,
            activation_id=f"activation-mechanic-{resume_stage}",
            run_id=f"run-mechanic-{resume_stage}",
            input_id=f"claim-mechanic-{resume_stage}",
        )

        state, decision = apply_runner_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-mechanic-{resume_stage}",
            action_id="planning.route_mechanic_complete",
            input_id=f"observe-mechanic-complete-{resume_stage}",
            target_work_item_id=f"work-resume-{resume_stage}",
            target_activation_id=f"activation-resume-{resume_stage}",
            observation_payload_overrides={"resume_stage": resume_stage},
        )

        assert decision.accepted is True
        activation = state.activations[f"activation-resume-{resume_stage}"]
        assert str(activation.stage_kind_id) == expected_stage
        assert activation.graph_node_id == expected_graph


def test_lad_planning_mechanic_complete_refuses_disallowed_resume_stage() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )
    state, _blocked = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner",
        action_id="planning.route_planner_blocked",
        input_id="observe-planner-blocked",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
        target_activation_id="activation-mechanic",
    )
    state = claim_activation(
        state,
        activation_id="activation-mechanic",
        run_id="run-mechanic",
        input_id="claim-mechanic",
    )

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-mechanic",
            action_id="planning.route_mechanic_complete",
            input_id="observe-mechanic-complete-mechanic",
            artifact=artifact_payload(REPORT_SCHEMA_ID),
            observation_payload_overrides={"resume_stage": "mechanic"},
        ),
        planning_context(
            "observe-mechanic-complete-mechanic",
            work_item_id="work-resume-mechanic",
            activation_id="activation-resume-mechanic",
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_dynamic_route_target"


def test_lad_planning_operator_resume_lineage_uses_selected_option_and_audit() -> None:
    plan, fingerprint = compile_lad_planning()
    quarantined = _planner_quarantined_state(plan, fingerprint)
    quarantine = next(iter(quarantined.lineage_quarantines.values()))
    attempt = _only_recovery_attempt(quarantined)

    transition_input = operator_api.build_resume_lineage(
        quarantined,
        operator_api.OperatorResumeLineageInput(
            input_id="operator-resume-planning-lineage",
            option_id="planning.blocked.resume_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator approved Planning retry after selected quarantine",
        ),
    )
    decision = decide(
        quarantined,
        transition_input,
        planning_context(
            "operator-resume-planning-lineage",
            activation_id="activation-operator-resumed-planner",
        ),
    )

    assert decision.accepted is True
    assert {
        "mutation.supersede_lineage_quarantine",
        "mutation.record_recovery_attempt",
        "mutation.create_activation",
        "mutation.record_operator_intervention",
    } <= {mutation.mutation_kind for mutation in decision.mutations}
    after = apply(quarantined, decision)
    intervention = next(iter(after.operator_interventions.values()))
    assert intervention.option_id == "planning.blocked.resume_lineage"
    assert intervention.kind == "resume_lineage"
    assert intervention.actor_kind == "local_operator"
    assert intervention.actor_id == "local-operator-tim"
    assert intervention.reason == (
        "operator approved Planning retry after selected quarantine"
    )
    assert intervention.selected_plan_ref == quarantine.selected_plan_ref
    assert intervention.selected_plan_fingerprint == fingerprint
    assert intervention.recovery_attempt_record_id == attempt.record_id
    assert intervention.quarantine_id == quarantine.quarantine_id
    resumed_activation = after.activations["activation-operator-resumed-planner"]
    assert resumed_activation.work_item_id == attempt.source_work_item_id
    assert str(resumed_activation.stage_kind_id) == "lad_planner"
    assert resumed_activation.graph_node_id == "planning.lad.planner.start"
    assert after.lineage_quarantines[quarantine.quarantine_id].status == "superseded"


def test_lad_planning_operator_close_lineage_uses_selected_option() -> None:
    plan, fingerprint = compile_lad_planning()
    quarantined = _planner_quarantined_state(plan, fingerprint)
    quarantine = next(iter(quarantined.lineage_quarantines.values()))

    transition_input = operator_api.build_close_lineage(
        quarantined,
        operator_api.OperatorCloseLineageInput(
            input_id="operator-close-planning-lineage",
            option_id="planning.blocked.close_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator closed Planning lineage after selected quarantine",
            payload={},
        ),
    )
    decision = decide(
        quarantined,
        transition_input,
        planning_context("operator-close-planning-lineage"),
    )

    assert decision.accepted is True
    assert {
        "mutation.supersede_lineage_quarantine",
        "mutation.record_recovery_attempt",
        "mutation.record_operator_intervention",
        "mutation.close_work_item",
    } <= {mutation.mutation_kind for mutation in decision.mutations}
    after = apply(quarantined, decision)
    intervention = next(iter(after.operator_interventions.values()))
    assert intervention.option_id == "planning.blocked.close_lineage"
    assert intervention.kind == "close_lineage"
    assert intervention.selected_plan_fingerprint == fingerprint
    assert intervention.actor_kind == "local_operator"
    assert intervention.actor_id == "local-operator-tim"
    assert intervention.reason == (
        "operator closed Planning lineage after selected quarantine"
    )
    assert quarantine.original_source_work_item_id in intervention.closed_work_item_ids
    assert all(
        after.closed_work_items[work_item_id].close_kind == "operator_intervention"
        for work_item_id in intervention.closed_work_item_ids
    )
    assert after.lineage_quarantines[quarantine.quarantine_id].status == "superseded"


def test_lad_planning_operator_revise_lineage_routes_selected_spec() -> None:
    plan, fingerprint = compile_lad_planning()
    quarantined = _planner_quarantined_state(plan, fingerprint)
    quarantine = next(iter(quarantined.lineage_quarantines.values()))
    invalid_payload = {
        "title": "Revised Planning spec",
        "body": "Retry with operator context.",
    }

    try:
        operator_api.build_revise_lineage(
            quarantined,
            operator_api.OperatorReviseLineageInput(
                input_id="operator-revise-planning-invalid",
                option_id="planning.blocked.revise_lineage",
                selected_plan_ref=quarantine.selected_plan_ref,
                quarantine_id=quarantine.quarantine_id,
                actor_id="local-operator-tim",
                actor_kind="local_operator",
                reason="operator revised Planning spec",
                payload=invalid_payload,
            ),
        )
    except operator_api.OperatorInputError as exc:
        assert exc.reason == "invalid_payload_schema"
    else:  # pragma: no cover - fail explicitly without pytest import noise.
        raise AssertionError("invalid Planning revision payload was accepted")

    payload = {
        "title": "Revised Planning spec",
        "body": "Retry with operator context.",
        "root_source": {"kind": "spec", "source_id": "operator-revised-spec"},
    }
    transition_input = operator_api.build_revise_lineage(
        quarantined,
        operator_api.OperatorReviseLineageInput(
            input_id="operator-revise-planning-lineage",
            option_id="planning.blocked.revise_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator revised Planning spec",
            payload=payload,
        ),
    )
    decision = decide(
        quarantined,
        transition_input,
        planning_context(
            "operator-revise-planning-lineage",
            work_item_id="work-operator-revised-spec",
            activation_id="activation-operator-revised-planner",
        ),
    )

    assert decision.accepted is True
    assert {
        "mutation.supersede_lineage_quarantine",
        "mutation.record_recovery_attempt",
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.record_operator_intervention",
    } <= {mutation.mutation_kind for mutation in decision.mutations}
    after = apply(quarantined, decision)
    work_item = after.work_items["work-operator-revised-spec"]
    activation = after.activations["activation-operator-revised-planner"]
    assert work_item.payload == payload
    assert str(work_item.queue_family_id) == "spec"
    assert work_item.lineage_id == quarantine.lineage_id
    assert str(activation.stage_kind_id) == "lad_planner"
    assert activation.graph_node_id == "planning.lad.planner.start"
    intervention = next(iter(after.operator_interventions.values()))
    assert intervention.option_id == "planning.blocked.revise_lineage"
    assert intervention.kind == "revise_lineage"
    assert intervention.result == "revised"
    assert intervention.selected_plan_fingerprint == fingerprint
    assert intervention.target_work_item_id == "work-operator-revised-spec"
    assert intervention.target_activation_id == "activation-operator-revised-planner"
    assert after.lineage_quarantines[quarantine.quarantine_id].status == "superseded"
