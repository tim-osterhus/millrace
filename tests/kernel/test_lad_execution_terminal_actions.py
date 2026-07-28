from __future__ import annotations

from collections.abc import Mapping

from millrace.contracts.compiled_plan import AuthorityValue, TerminalActionDeclaration
from millrace.contracts.ids import ArtifactSchemaId
from millrace.contracts.transition import artifact_payload_digest
from millrace.kernel import apply, decide
from millrace.testing import (
    fake_completed_runner_observation_state,
    fake_runner_completion_input_id,
    fake_runner_dispatch_envelope_for_run,
)
from support.lad_execution import (
    BUILDER_SUMMARY_SCHEMA_ID,
    INCIDENT_REPORT_SCHEMA_ID,
    INTEGRATION_REPORT_SCHEMA_ID,
    REPORT_SCHEMA_ID,
    STAGE_RESULT_SCHEMA_ID,
    artifact_payload,
    bootstrap_builder_claim,
    claim_activation,
    compile_lad,
    lad_context,
    mutation_kinds,
    runner_observation,
)


def _action_by_id(
    actions: tuple[TerminalActionDeclaration, ...],
    action_id: str,
) -> TerminalActionDeclaration:
    return next(action for action in actions if str(action.id) == action_id)


def _decide_observation(
    state,
    *,
    plan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    schema_id: str,
    target_work_item_id: str | None = None,
    target_activation_id: str | None = None,
    marker: str | None = None,
    overrides: Mapping[str, AuthorityValue] | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
):
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        marker=marker,
        artifact_payload=artifact_payload(schema_id),
        overrides=overrides,
        observation_payload_overrides=observation_payload_overrides,
    )
    state, observation = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )
    return state, decide(
        state,
        observation,
        lad_context(
            input_id,
            work_item_id=target_work_item_id,
            activation_id=target_activation_id,
        ),
    )


def _apply_observation(
    state,
    *,
    plan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    schema_id: str = STAGE_RESULT_SCHEMA_ID,
    target_work_item_id: str | None = None,
    target_activation_id: str | None = None,
    marker: str | None = None,
    overrides: Mapping[str, AuthorityValue] | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
):
    state, decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        schema_id=schema_id,
        target_work_item_id=target_work_item_id,
        target_activation_id=target_activation_id,
        marker=marker,
        overrides=overrides,
        observation_payload_overrides=observation_payload_overrides,
    )
    assert decision.accepted is True
    return apply(state, decision), decision


def _assert_latest_route(
    state,
    *,
    action_id: str,
    source_run_id: str,
    source_work_item_id: str,
    target_work_item_id: str,
    target_activation_id: str,
) -> None:
    route = state.activation_routes[-1]
    assert str(route.action_id) == action_id
    assert route.source_run_id == source_run_id
    assert route.source_work_item_id == source_work_item_id
    assert route.target_work_item_id == target_work_item_id
    assert route.target_activation_id == target_activation_id


def _assert_artifact_provenance(
    state,
    *,
    artifact_id: str,
    schema_id: str,
    source_run_id: str,
    source_action_id: str,
    transition_id: str,
) -> None:
    artifact = state.artifacts[artifact_id]
    run = state.runs[source_run_id]
    activation = state.activations[run.activation_id]
    assert artifact.schema_id == ArtifactSchemaId(schema_id)
    assert artifact.source_run_id == source_run_id
    assert str(artifact.source_action_id) == source_action_id
    assert artifact.source_stage_kind_id == run.stage_kind_id
    assert artifact.source_graph_node_id == activation.graph_node_id
    assert artifact.payload_digest == artifact_payload_digest(artifact.payload)
    assert artifact.created_by_input_id == fake_runner_completion_input_id(
        transition_id.removeprefix("transition-")
    )
    assert artifact.transition_id == transition_id


def _route_and_claim(
    state,
    *,
    plan,
    fingerprint: str,
    source_run_id: str,
    action_id: str,
    input_id: str,
    target_stage: str,
    schema_id: str = STAGE_RESULT_SCHEMA_ID,
) -> tuple[object, object]:
    target_work_item_id = f"work-{target_stage}"
    target_activation_id = f"activation-{target_stage}"
    source_work_item_id = state.runs[source_run_id].work_item_id
    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=source_run_id,
        action_id=action_id,
        input_id=input_id,
        schema_id=schema_id,
        target_work_item_id=target_work_item_id,
        target_activation_id=target_activation_id,
    )
    assert "mutation.route_activation" in mutation_kinds(decision)
    _assert_latest_route(
        state,
        action_id=action_id,
        source_run_id=source_run_id,
        source_work_item_id=source_work_item_id,
        target_work_item_id=target_work_item_id,
        target_activation_id=target_activation_id,
    )
    state = claim_activation(
        state,
        activation_id=target_activation_id,
        run_id=f"run-{target_stage}",
        input_id=f"claim-{target_stage}",
    )
    return state, decision


def _claimed_stage_state(
    stage: str,
    *,
    integrator: bool = False,
):
    plan, fingerprint = compile_lad(integrator=integrator)
    state = bootstrap_builder_claim(plan, fingerprint)
    if stage == "builder":
        return plan, fingerprint, state, "run-builder"
    if stage == "troubleshooter":
        state, _ = _apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-builder",
            action_id="execution.route_builder_blocked",
            input_id="observe-builder-blocked",
            target_work_item_id="work-troubleshooter",
            target_activation_id="activation-troubleshooter",
        )
        state = claim_activation(
            state,
            activation_id="activation-troubleshooter",
            run_id="run-troubleshooter",
            input_id="claim-troubleshooter",
        )
        return plan, fingerprint, state, "run-troubleshooter"

    if integrator:
        state, _ = _route_and_claim(
            state,
            plan=plan,
            fingerprint=fingerprint,
            source_run_id="run-builder",
            action_id="execution.route_builder_complete",
            input_id="observe-builder-summary",
            target_stage="integrator",
            schema_id=BUILDER_SUMMARY_SCHEMA_ID,
        )
        if stage == "integrator":
            return plan, fingerprint, state, "run-integrator"
        state, _ = _route_and_claim(
            state,
            plan=plan,
            fingerprint=fingerprint,
            source_run_id="run-integrator",
            action_id="execution.route_integrator_complete",
            input_id="observe-integration-complete",
            target_stage="checker",
            schema_id=INTEGRATION_REPORT_SCHEMA_ID,
        )
    else:
        state, _ = _route_and_claim(
            state,
            plan=plan,
            fingerprint=fingerprint,
            source_run_id="run-builder",
            action_id="execution.route_builder_complete",
            input_id="observe-builder-complete",
            target_stage="checker",
        )

    if stage == "checker":
        return plan, fingerprint, state, "run-checker"
    if stage == "updater":
        state, _ = _route_and_claim(
            state,
            plan=plan,
            fingerprint=fingerprint,
            source_run_id="run-checker",
            action_id="execution.route_checker_pass",
            input_id="observe-checker-pass",
            target_stage="updater",
        )
        return plan, fingerprint, state, "run-updater"
    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-checker",
        action_id="execution.route_checker_fix_needed",
        input_id="observe-checker-fix-needed",
        target_stage="fixer",
    )
    if stage == "fixer":
        return plan, fingerprint, state, "run-fixer"
    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-fixer",
        action_id="execution.route_fixer_complete",
        input_id="observe-fixer-complete",
        target_stage="doublechecker",
    )
    if stage == "doublechecker":
        return plan, fingerprint, state, "run-doublechecker"
    raise AssertionError(f"unsupported stage fixture {stage!r}")


def _claimed_consultant_state():
    plan, fingerprint, state, _run_id = _claimed_stage_state("troubleshooter")
    state, _ = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.route_troubleshooter_blocked",
        input_id="observe-troubleshooter-blocked",
        target_work_item_id="work-consultant",
        target_activation_id="activation-consultant",
    )
    state = claim_activation(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )
    return plan, fingerprint, state


def test_base_lad_happy_path_routes_builder_checker_updater_and_closes() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)

    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-complete",
        target_stage="checker",
    )
    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-checker",
        action_id="execution.route_checker_pass",
        input_id="observe-checker-pass",
        target_stage="updater",
    )

    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-updater",
        action_id="execution.close_updater_complete",
        input_id="observe-updater-complete",
        schema_id=REPORT_SCHEMA_ID,
    )

    close = state.closed_work_items["work-updater"]
    assert "mutation.close_work_item" in mutation_kinds(decision)
    assert close.action_id == _action_by_id(
        plan.terminal_actions,
        "execution.close_updater_complete",
    ).id
    assert close.close_kind == "terminal_action"
    _assert_artifact_provenance(
        state,
        artifact_id="transition-observe-updater-complete:artifact",
        schema_id=REPORT_SCHEMA_ID,
        source_run_id="run-updater",
        source_action_id="execution.close_updater_complete",
        transition_id="transition-observe-updater-complete",
    )
    assert state.governance_events[-1].action_id == close.action_id
    assert state.traces[-1].action_id == close.action_id


def test_integrator_path_uses_builder_summary_and_integration_report() -> None:
    plan, fingerprint = compile_lad(integrator=True)
    state = bootstrap_builder_claim(plan, fingerprint)

    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-summary",
        target_stage="integrator",
        schema_id=BUILDER_SUMMARY_SCHEMA_ID,
    )
    assert str(state.work_items["work-integrator"].queue_family_id) == (
        "builder_summary"
    )
    _assert_artifact_provenance(
        state,
        artifact_id="transition-observe-builder-summary:artifact",
        schema_id=BUILDER_SUMMARY_SCHEMA_ID,
        source_run_id="run-builder",
        source_action_id="execution.route_builder_complete",
        transition_id="transition-observe-builder-summary",
    )
    assert (
        fake_runner_dispatch_envelope_for_run(
            state=state,
            run_id="run-integrator",
        ).queue_family_id
        == "builder_summary"
    )

    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-integrator",
        action_id="execution.route_integrator_complete",
        input_id="observe-integration-complete",
        target_stage="checker",
        schema_id=INTEGRATION_REPORT_SCHEMA_ID,
    )

    _assert_artifact_provenance(
        state,
        artifact_id="transition-observe-integration-complete:artifact",
        schema_id=INTEGRATION_REPORT_SCHEMA_ID,
        source_run_id="run-integrator",
        source_action_id="execution.route_integrator_complete",
        transition_id="transition-observe-integration-complete",
    )
    assert "execution.artifacts.integration_report" in (
        fake_runner_dispatch_envelope_for_run(
            state=state,
            run_id="run-checker",
        ).artifact_schema_ids
    )
    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-checker",
        action_id="execution.route_checker_pass",
        input_id="observe-integrator-checker-pass",
        target_stage="updater",
    )
    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-updater",
        action_id="execution.close_updater_complete",
        input_id="observe-integrator-updater-complete",
        schema_id=REPORT_SCHEMA_ID,
    )
    assert "mutation.close_work_item" in mutation_kinds(decision)
    assert "work-updater" in state.closed_work_items


def test_repair_path_and_doublechecker_fix_needed_routes_selected_targets() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)

    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-complete",
        target_stage="checker",
    )
    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-checker",
        action_id="execution.route_checker_fix_needed",
        input_id="observe-checker-fix-needed",
        target_stage="fixer",
    )
    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-fixer",
        action_id="execution.route_fixer_complete",
        input_id="observe-fixer-complete",
        target_stage="doublechecker",
    )
    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-doublechecker",
        action_id="execution.route_doublechecker_fix_needed",
        input_id="observe-doublechecker-fix-needed",
        target_work_item_id="work-fixer-again",
        target_activation_id="activation-fixer-again",
    )

    assert "mutation.route_activation" in mutation_kinds(decision)
    _assert_latest_route(
        state,
        action_id="execution.route_doublechecker_fix_needed",
        source_run_id="run-doublechecker",
        source_work_item_id="work-doublechecker",
        target_work_item_id="work-fixer-again",
        target_activation_id="activation-fixer-again",
    )
    assert str(state.activations["activation-fixer-again"].stage_kind_id) == (
        "lad_fixer"
    )


def test_repair_path_can_route_doublechecker_pass_to_update_complete() -> None:
    plan, fingerprint, state, run_id = _claimed_stage_state("doublechecker")
    assert run_id == "run-doublechecker"

    state, _ = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-doublechecker",
        action_id="execution.route_doublechecker_pass",
        input_id="observe-doublechecker-pass",
        target_stage="updater",
    )
    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-updater",
        action_id="execution.close_updater_complete",
        input_id="observe-repair-updater-complete",
        schema_id=REPORT_SCHEMA_ID,
    )

    assert "mutation.close_work_item" in mutation_kinds(decision)
    assert state.closed_work_items["work-updater"].source_run_id == "run-updater"


def test_selected_blocked_routes_target_declared_recovery_stages() -> None:
    cases = (
        (
            "builder",
            False,
            "execution.route_builder_blocked",
            "troubleshooter",
            "lad_troubleshooter",
        ),
        (
            "checker",
            False,
            "execution.route_checker_blocked",
            "troubleshooter",
            "lad_troubleshooter",
        ),
        (
            "fixer",
            False,
            "execution.route_fixer_blocked",
            "troubleshooter",
            "lad_troubleshooter",
        ),
        (
            "doublechecker",
            False,
            "execution.route_doublechecker_blocked",
            "troubleshooter",
            "lad_troubleshooter",
        ),
        (
            "updater",
            False,
            "execution.route_updater_blocked",
            "troubleshooter",
            "lad_troubleshooter",
        ),
        (
            "troubleshooter",
            False,
            "execution.route_troubleshooter_blocked",
            "consultant",
            "lad_consultant",
        ),
        (
            "integrator",
            True,
            "execution.route_integrator_blocked",
            "troubleshooter",
            "lad_troubleshooter",
        ),
    )
    for stage, integrator, action_id, target_stage, target_stage_kind in cases:
        plan, fingerprint, state, run_id = _claimed_stage_state(
            stage,
            integrator=integrator,
        )
        source_work_item_id = state.runs[run_id].work_item_id
        target_work_item_id = f"work-{stage}-blocked-{target_stage}"
        target_activation_id = f"activation-{stage}-blocked-{target_stage}"

        state, decision = _apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=run_id,
            action_id=action_id,
            input_id=f"observe-{stage}-blocked",
            target_work_item_id=target_work_item_id,
            target_activation_id=target_activation_id,
        )

        assert "mutation.route_activation" in mutation_kinds(decision)
        _assert_latest_route(
            state,
            action_id=action_id,
            source_run_id=run_id,
            source_work_item_id=source_work_item_id,
            target_work_item_id=target_work_item_id,
            target_activation_id=target_activation_id,
        )
        assert str(state.activations[target_activation_id].stage_kind_id) == (
            target_stage_kind
        )


def test_blocked_marker_is_source_scoped_and_runner_override_is_ignored() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)

    state, builder_decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
        observation_payload_overrides={
            "target_graph_node_id": "execution.lad.consultant.start",
        },
    )
    assert "mutation.route_activation" in mutation_kinds(builder_decision)
    assert (
        state.activations["activation-troubleshooter"].graph_node_id
        == "execution.lad.troubleshooter.start"
    )

    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )
    state, _ = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.route_troubleshooter_blocked",
        input_id="observe-troubleshooter-blocked",
        target_work_item_id="work-consultant",
        target_activation_id="activation-consultant",
    )
    state = claim_activation(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )

    _state, wrong_source_decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.route_builder_blocked",
        input_id="observe-consultant-with-global-blocked",
        schema_id=REPORT_SCHEMA_ID,
        marker="BLOCKED",
        target_work_item_id="work-should-not-exist",
        target_activation_id="activation-should-not-exist",
        overrides={"stage_kind_id": "lad_builder"},
    )

    assert wrong_source_decision.accepted is False
    assert wrong_source_decision.refusal is not None
    assert wrong_source_decision.refusal.reason == "invalid_observation_authority"
    after = apply(state, wrong_source_decision)
    assert "work-should-not-exist" not in after.work_items
    assert "activation-should-not-exist" not in after.activations


def test_troubleshooter_and_consultant_complete_default_routes_are_selected() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _ = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )

    state, _ = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.return_troubleshooter_complete",
        input_id="observe-troubleshooter-complete",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-builder-resume",
        target_activation_id="activation-builder-resume",
    )
    assert str(state.activations["activation-builder-resume"].stage_kind_id) == (
        "lad_builder"
    )

    state = claim_activation(
        state,
        activation_id="activation-builder-resume",
        run_id="run-builder-resume",
        input_id="claim-builder-resume",
    )
    state, _ = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder-resume",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-resume-blocked-threshold",
        target_activation_id="activation-consultant",
    )
    assert str(state.activations["activation-consultant"].stage_kind_id) == (
        "lad_consultant"
    )
    state = claim_activation(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )

    state, _ = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.route_consultant_complete",
        input_id="observe-consultant-complete",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-troubleshooter-default",
        target_activation_id="activation-troubleshooter-default",
    )

    default_activation = state.activations["activation-troubleshooter-default"]
    assert str(default_activation.stage_kind_id) == "lad_troubleshooter"


def test_consultant_terminal_actions_close_without_planning_enqueue() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _ = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )
    state, _ = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.route_troubleshooter_blocked",
        input_id="observe-troubleshooter-blocked",
        target_work_item_id="work-consultant",
        target_activation_id="activation-consultant",
    )
    state = claim_activation(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )
    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.close_consultant_needs_plan",
        input_id="observe-consultant-needs-plan",
        schema_id=INCIDENT_REPORT_SCHEMA_ID,
    )

    action = _action_by_id(
        plan.terminal_actions,
        "execution.close_consultant_needs_plan",
    )
    assert action.action_kind == "close_with_escalation"
    assert mutation_kinds(decision) == (
        "mutation.record_input_receipt",
        "mutation.record_runner_observation",
        "mutation.record_artifact",
        "mutation.close_work_item",
        "mutation.record_transition",
        "mutation.emit_governance_event",
        "mutation.emit_trace",
    )
    excluded_mutations = {
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.route_activation",
        "mutation.record_fanout",
        "mutation.record_closure_evaluation",
        "mutation.record_remediation_work",
        "mutation.record_operator_wait",
    }
    assert excluded_mutations.isdisjoint(mutation_kinds(decision))
    assert state.closed_work_items["work-consultant"].action_id == action.id
    assert state.closed_work_items["work-consultant"].source_run_id == "run-consultant"
    assert {
        str(item.queue_family_id) for item in state.work_items.values()
    } == {"task", "stage_result"}
    assert "work-consultant" in state.closed_work_items
    assert "work-consultant" not in state.quarantines
    assert state.pause is None
    assert state.closure_evaluations == {}
    assert state.remediation_work_records == {}
    queue_family_ids = {
        str(item.queue_family_id) for item in state.work_items.values()
    }
    assert all(
        "planning" not in queue_family_id for queue_family_id in queue_family_ids
    )
    assert all(
        "planning" not in activation.graph_node_id
        for activation in state.activations.values()
    )
    assert all(
        "planning" not in str(event.action_id) for event in state.governance_events
    )


def test_consultant_blocked_applies_block_work_item_without_planning_enqueue() -> None:
    plan, fingerprint, state = _claimed_consultant_state()
    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.close_consultant_blocked",
        input_id="observe-consultant-blocked",
        schema_id=REPORT_SCHEMA_ID,
    )

    action = _action_by_id(
        plan.terminal_actions,
        "execution.close_consultant_blocked",
    )
    close = state.closed_work_items["work-consultant"]
    assert action.action_kind == "block_work_item"
    assert "mutation.close_work_item" in mutation_kinds(decision)
    assert close.action_id == action.id
    assert close.source_run_id == "run-consultant"
    assert state.governance_events[-1].action_id == action.id
    assert state.traces[-1].action_id == action.id
    assert {
        str(item.queue_family_id) for item in state.work_items.values()
    } == {"task", "stage_result"}
    assert all(
        "planning" not in activation.graph_node_id
        for activation in state.activations.values()
    )


def test_invalid_artifact_payload_refuses_without_workflow_progress() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)

    before_routes = state.activation_routes
    before_artifacts = state.artifacts
    before_observations = state.runner_observations
    _state, decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-wrong-artifact",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-should-not-exist",
        target_activation_id="activation-should-not-exist",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    after = apply(state, decision)
    assert after.activation_routes == before_routes
    assert after.artifacts == before_artifacts
    assert after.runner_observations == before_observations
    assert "work-should-not-exist" not in after.work_items
    assert "activation-should-not-exist" not in after.activations
    assert after.governance_events[-1].disposition == "refused"
    assert after.traces[-1].refusal_reason == "invalid_artifact_payload"


def test_undeclared_lad_marker_refuses_without_workflow_progress() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    _state, decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-undeclared-marker",
        schema_id=STAGE_RESULT_SCHEMA_ID,
        marker="CHECKER_PASS",
        target_work_item_id="work-should-not-exist",
        target_activation_id="activation-should-not-exist",
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "undeclared_terminal_outcome"
    after = apply(state, decision)
    assert after.activation_routes == state.activation_routes
    assert "work-should-not-exist" not in after.work_items
    assert after.traces[-1].refusal_reason == "undeclared_terminal_outcome"


def test_wrong_lad_graph_evidence_refuses_without_workflow_progress() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    _state, decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-wrong-graph",
        schema_id=STAGE_RESULT_SCHEMA_ID,
        target_work_item_id="work-should-not-exist",
        target_activation_id="activation-should-not-exist",
        overrides={"graph_node_id": "execution.lad.checker.start"},
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_observation_authority"
    after = apply(state, decision)
    assert after.activation_routes == state.activation_routes
    assert "work-should-not-exist" not in after.work_items
    assert after.traces[-1].refusal_reason == "invalid_observation_authority"


def test_runner_selected_action_top_level_override_is_invalid_evidence() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    _state, decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-action-override",
        schema_id=STAGE_RESULT_SCHEMA_ID,
        target_work_item_id="work-should-not-exist",
        target_activation_id="activation-should-not-exist",
        overrides={"action_id": "execution.route_builder_blocked"},
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_runner_evidence"
    after = apply(state, decision)
    assert after.activation_routes == state.activation_routes
    assert "work-should-not-exist" not in after.work_items


def test_duplicate_lad_observation_refuses_without_second_progress() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _ = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-complete",
        target_work_item_id="work-checker",
        target_activation_id="activation-checker",
    )
    before_routes = state.activation_routes

    _state, duplicate = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-complete-duplicate",
        schema_id=STAGE_RESULT_SCHEMA_ID,
        target_work_item_id="work-duplicate",
        target_activation_id="activation-duplicate",
    )

    assert duplicate.accepted is False
    assert duplicate.refusal is not None
    assert duplicate.refusal.reason == "invalid_observation_authority"
    after = apply(state, duplicate)
    assert after.activation_routes == before_routes
    assert "work-duplicate" not in after.work_items
    assert "activation-duplicate" not in after.activations
