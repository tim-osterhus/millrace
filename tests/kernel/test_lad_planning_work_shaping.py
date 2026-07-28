from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from millrace.contracts.compiled_plan import AuthorityValue, SelectedCompiledPlan
from millrace.contracts.ids import ArtifactSchemaId, QueueFamilyId
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import TransitionDecision, artifact_payload_digest
from millrace.kernel import apply
from millrace.testing import decide_with_fake_runner_completion as decide
from substrate._runtime_store_support import persist_and_load_runtime_state
from support.lad_planning import (
    REPORT_SCHEMA_ID,
    STAGE_RESULT_SCHEMA_ID,
    TASK_CARDS_SCHEMA_ID,
    apply_runner_observation,
    artifact_payload,
    bootstrap_route_claim,
    claim_activation,
    compile_lad_planning,
    planning_context,
    runner_observation,
    task_cards_payload,
)


def _mutation_kinds(decision: TransitionDecision) -> set[str]:
    return {mutation.mutation_kind for mutation in decision.mutations}


def _assert_no_progress_after_refusal(
    before: RuntimeState,
    after: RuntimeState,
) -> None:
    assert after.work_items == before.work_items
    assert after.activations == before.activations
    assert after.runs == before.runs
    assert after.activation_routes == before.activation_routes
    assert after.closed_work_items == before.closed_work_items
    assert after.artifacts == before.artifacts
    assert after.runner_observations == before.runner_observations
    assert after.pause == before.pause
    assert after.quarantines == before.quarantines


def _route_and_claim(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    source_run_id: str,
    action_id: str,
    input_id: str,
    target_stage: str,
) -> RuntimeState:
    state, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=source_run_id,
        action_id=action_id,
        input_id=input_id,
        target_work_item_id=f"work-{target_stage}",
        target_activation_id=f"activation-{target_stage}",
    )
    assert "mutation.route_activation" in _mutation_kinds(decision)
    return claim_activation(
        state,
        activation_id=f"activation-{target_stage}",
        run_id=f"run-{target_stage}",
        input_id=f"claim-{target_stage}",
    )


def test_lad_planner_complete_routes_to_manager() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )

    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-planner",
        action_id="planning.route_planner_complete",
        input_id="observe-planner-complete",
        target_stage="manager",
    )

    manager = state.runs["run-manager"]
    activation = state.activations[manager.activation_id]
    assert str(manager.stage_kind_id) == "lad_manager"
    assert activation.graph_node_id == "planning.lad.manager.start"
    assert str(state.work_items["work-manager"].queue_family_id) == "stage_result"
    assert state.activation_routes[-1].source_run_id == "run-planner"


def test_lad_auditor_complete_routes_to_planner() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="incident",
        activation_id="activation-auditor",
        run_id="run-auditor",
        work_item_id="work-incident",
    )

    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-auditor",
        action_id="planning.route_auditor_complete",
        input_id="observe-auditor-complete",
        target_stage="planner",
    )

    planner = state.runs["run-planner"]
    activation = state.activations[planner.activation_id]
    assert str(planner.stage_kind_id) == "lad_planner"
    assert activation.graph_node_id == "planning.lad.planner.start"


@pytest.mark.parametrize(
    ("queue_family_id", "activation_id", "run_id", "action_id"),
    (
        (
            "spec",
            "activation-planner",
            "run-planner",
            "planning.route_planner_blocked",
        ),
        (
            "spec",
            "activation-manager",
            "run-manager",
            "planning.route_manager_blocked",
        ),
        (
            "incident",
            "activation-auditor",
            "run-auditor",
            "planning.route_auditor_blocked",
        ),
    ),
)
def test_lad_planning_blocked_routes_are_source_scoped_to_mechanic(
    queue_family_id: str,
    activation_id: str,
    run_id: str,
    action_id: str,
) -> None:
    plan, fingerprint = compile_lad_planning()
    if run_id == "run-manager":
        state = bootstrap_route_claim(
            plan,
            fingerprint,
            queue_family_id="spec",
            activation_id="activation-planner",
            run_id="run-planner",
            work_item_id="work-spec",
        )
        state = _route_and_claim(
            state,
            plan=plan,
            fingerprint=fingerprint,
            source_run_id="run-planner",
            action_id="planning.route_planner_complete",
            input_id="observe-planner-complete",
            target_stage="manager",
        )
    else:
        state = bootstrap_route_claim(
            plan,
            fingerprint,
            queue_family_id=queue_family_id,
            activation_id=activation_id,
            run_id=run_id,
            work_item_id=f"work-{queue_family_id}",
        )

    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id=run_id,
        action_id=action_id,
        input_id=f"observe-{run_id.removeprefix('run-')}-blocked",
        target_stage="mechanic",
    )

    mechanic = state.runs["run-mechanic"]
    activation = state.activations[mechanic.activation_id]
    assert str(mechanic.stage_kind_id) == "lad_mechanic"
    assert activation.graph_node_id == "planning.lad.mechanic.start"
    assert state.activation_routes[-1].source_run_id == run_id


def test_lad_planning_global_blocked_marker_wrong_source_is_refused() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="incident",
        activation_id="activation-auditor",
        run_id="run-auditor",
        work_item_id="work-incident",
    )
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-auditor",
            action_id="planning.route_planner_blocked",
            input_id="observe-auditor-global-blocked",
            artifact=artifact_payload(STAGE_RESULT_SCHEMA_ID),
            marker="BLOCKED",
            overrides={"stage_kind_id": "lad_planner"},
            observation_payload_overrides={},
        ),
        planning_context(
            "observe-auditor-global-blocked",
            work_item_id="work-should-not-exist",
            activation_id="activation-should-not-exist",
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_observation_authority"
    after = apply(state, decision)
    assert "work-should-not-exist" not in after.work_items
    assert "activation-should-not-exist" not in after.activations


def test_lad_manager_complete_records_task_cards_and_closes_without_fanout(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-planner",
        action_id="planning.route_planner_complete",
        input_id="observe-planner-complete",
        target_stage="manager",
    )

    state, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-manager",
        action_id="planning.close_manager_complete",
        input_id="observe-manager-complete",
        artifact=task_cards_payload(),
    )

    assert "mutation.close_work_item" in _mutation_kinds(decision)
    assert "mutation.create_work_item" not in _mutation_kinds(decision)
    assert "mutation.create_activation" not in _mutation_kinds(decision)
    assert state.closed_work_items["work-manager"].source_run_id == "run-manager"
    artifact = state.artifacts["transition-observe-manager-complete:artifact"]
    assert artifact.schema_id == ArtifactSchemaId(TASK_CARDS_SCHEMA_ID)
    assert artifact.source_run_id == "run-manager"
    assert str(artifact.source_action_id) == "planning.close_manager_complete"
    assert artifact.payload == task_cards_payload()
    assert artifact.payload_digest == artifact_payload_digest(task_cards_payload())
    assert {
        str(work_item.queue_family_id) for work_item in state.work_items.values()
    } == {"spec", "stage_result"}
    loaded = persist_and_load_runtime_state(tmp_path, state)
    assert loaded.closed_work_items == state.closed_work_items
    assert loaded.artifacts == state.artifacts
    assert loaded.activation_routes == state.activation_routes
    assert loaded.work_items == state.work_items
    loaded_plan = loaded.admitted_plans[fingerprint]
    spec_route = loaded_plan.external_enqueue_routes[QueueFamilyId("spec")]
    assert str(spec_route.payload_schema_id) == "planning.intake.spec"
    assert not any(
        str(work_item.queue_family_id) == "task"
        for work_item in loaded.work_items.values()
    )
    assert "work-manager" in loaded.closed_work_items
    assert "transition-observe-manager-complete:artifact" in loaded.artifacts


@pytest.mark.parametrize(
    ("payload", "input_id"),
    (
        (
            {
                "artifact_kind": "task_cards",
                "cards": (),
            },
            "observe-manager-empty-task-cards",
        ),
        (
            {
                "artifact_kind": "task_cards",
                "cards": (
                    {
                        "task_card_id": "task-card-1",
                        "title": "Duplicate",
                        "body": "First",
                    },
                    {
                        "task_card_id": "task-card-1",
                        "title": "Duplicate",
                        "body": "Second",
                    },
                ),
            },
            "observe-manager-duplicate-task-cards",
        ),
        (
            {
                "artifact_kind": "wrong",
                "cards": (
                    {
                        "task_card_id": "task-card-1",
                        "title": "Wrong kind",
                        "body": "No progress",
                    },
                ),
            },
            "observe-manager-wrong-task-cards-kind",
        ),
    ),
)
def test_lad_manager_invalid_task_cards_are_refused(
    payload: Mapping[str, AuthorityValue],
    input_id: str,
) -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-planner",
        action_id="planning.route_planner_complete",
        input_id="observe-planner-complete",
        target_stage="manager",
    )
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id="planning.close_manager_complete",
            input_id=input_id,
            artifact=payload,
        ),
        planning_context(input_id),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    after = apply(state, decision)
    assert after.closed_work_items == state.closed_work_items
    assert after.artifacts == state.artifacts


def test_lad_manager_wrong_source_artifact_label_is_refused() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-planner",
        action_id="planning.route_planner_complete",
        input_id="observe-planner-complete",
        target_stage="manager",
    )
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id="planning.close_manager_complete",
            input_id="observe-manager-report",
            artifact=artifact_payload(REPORT_SCHEMA_ID),
        ),
        planning_context("observe-manager-report"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"


def test_lad_manager_runner_selected_target_is_ignored() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-planner",
        action_id="planning.route_planner_complete",
        input_id="observe-planner-complete",
        target_stage="manager",
    )
    state, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-manager",
        action_id="planning.close_manager_complete",
        input_id="observe-manager-runner-target",
        artifact=task_cards_payload(),
        observation_payload_overrides={
            "target_queue_family_id": "task",
            "target_graph_node_id": "planning.lad.arbiter.start",
        },
    )

    assert decision.accepted is True
    assert "mutation.create_work_item" not in _mutation_kinds(decision)
    assert "mutation.create_activation" not in _mutation_kinds(decision)
    assert state.closed_work_items["work-manager"].source_run_id == "run-manager"
    assert {str(item.queue_family_id) for item in state.work_items.values()} == {
        "spec",
        "stage_result",
    }


@pytest.mark.parametrize(
    ("action_id", "artifact", "expected_queue_family_id", "expected_stage_kind_id"),
    (
        (
            "planning.recon_enqueue_task",
            {
                "task_id": "task-1",
                "body": "Generated task body",
            },
            "task",
            "lad_builder",
        ),
        (
            "planning.recon_enqueue_spec",
            {
                "artifact_kind": "planning.artifacts.generated_spec",
                "spec_id": "spec-1",
                "body": "Generated spec body",
            },
            "spec",
            "lad_planner",
        ),
    ),
)
def test_lad_recon_handoff_actions_route_selected_downstream_work(
    action_id: str,
    artifact: Mapping[str, AuthorityValue],
    expected_queue_family_id: str,
    expected_stage_kind_id: str,
) -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="probe",
        activation_id="activation-recon",
        run_id="run-recon",
        work_item_id="work-probe",
    )
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-recon",
            action_id=action_id,
            input_id=f"observe-{action_id.removeprefix('planning.')}",
            artifact=artifact,
        ),
        planning_context(f"observe-{action_id.removeprefix('planning.')}"),
    )
    after = apply(state, decision)

    assert decision.accepted is True
    assert decision.refusal is None
    assert "mutation.create_work_item" in _mutation_kinds(decision)
    assert "mutation.create_activation" in _mutation_kinds(decision)
    assert "mutation.route_activation" in _mutation_kinds(decision)
    assert "mutation.record_artifact" in _mutation_kinds(decision)
    created_work_item_ids = set(after.work_items) - set(state.work_items)
    created_activation_ids = set(after.activations) - set(state.activations)
    assert len(created_work_item_ids) == 1
    assert len(created_activation_ids) == 1
    created_work_item = after.work_items[next(iter(created_work_item_ids))]
    created_activation = after.activations[next(iter(created_activation_ids))]
    assert created_work_item.queue_family_id == QueueFamilyId(
        expected_queue_family_id
    )
    assert str(created_activation.stage_kind_id) == expected_stage_kind_id
    assert created_activation.work_item_id == created_work_item.ref.work_item_id
