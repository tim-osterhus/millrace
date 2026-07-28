from __future__ import annotations

from pathlib import Path

import pytest

from millrace.contracts import QueueFamilyId
from millrace.contracts.transition import FanoutFromArtifact
from millrace.kernel import apply
from millrace.testing import decide_with_fake_runner_completion as decide
from substrate._runtime_store_support import persist_and_load_runtime_state
from support.lad_planning import (
    apply_runner_observation,
    artifact_payload,
    bootstrap_route_claim,
    claim_activation,
    compile_lad_planning,
    planning_context,
    runner_observation,
    task_cards_payload,
)


def _manager_task_cards_state() -> tuple[object, str, object]:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="spec",
        activation_id="activation-planner",
        run_id="run-planner",
        work_item_id="work-spec",
    )
    state, _planner_decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner",
        action_id="planning.route_planner_complete",
        input_id="observe-planner-complete",
        target_work_item_id="work-manager",
        target_activation_id="activation-manager",
    )
    state = claim_activation(
        state,
        activation_id="activation-manager",
        run_id="run-manager",
        input_id="claim-manager",
    )
    state, _manager_decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-manager",
        action_id="planning.close_manager_complete",
        input_id="observe-manager-complete",
        artifact=task_cards_payload(),
    )
    return plan, fingerprint, state


def _recon_claimed_state() -> tuple[object, str, object]:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_claim(
        plan,
        fingerprint,
        queue_family_id="probe",
        activation_id="activation-recon",
        run_id="run-recon",
        work_item_id="work-probe",
    )
    return plan, fingerprint, state


def test_lad_manager_task_cards_fan_out_to_selected_execution_task_work() -> None:
    _plan, _fingerprint, state = _manager_task_cards_state()

    decision = decide(
        state,
        FanoutFromArtifact(
            "fanout-manager-task-cards",
            fanout_id="planning.manager.task_cards_to_execution",
            source_artifact_id="transition-observe-manager-complete:artifact",
        ),
        planning_context("fanout-manager-task-cards"),
    )

    assert decision.accepted is True
    after = apply(state, decision)
    generated = [
        item
        for item in after.work_items.values()
        if item.queue_family_id == QueueFamilyId("task")
    ]
    assert len(generated) == 1
    assert generated[0].payload["task_id"] == "task-card-1"
    assert generated[0].payload["body"] == "Use LAD-B-0003 to fan this out later."
    assert generated[0].lineage_id == "work-spec"
    assert len(after.fanout_records) == 1
    fanout_record = next(iter(after.fanout_records.values()))
    assert fanout_record.source_artifact_id == (
        "transition-observe-manager-complete:artifact"
    )
    assert fanout_record.source_work_item_id == "work-manager"
    assert fanout_record.target_work_item_id == generated[0].ref.work_item_id
    assert len(after.work_dependencies) == 1
    dependency = next(iter(after.work_dependencies.values()))
    assert dependency.dependent_work_item_id == generated[0].ref.work_item_id
    assert dependency.dependency_work_item_id == "work-manager"


def test_lad_manager_fanout_state_survives_restart(tmp_path: Path) -> None:
    _plan, _fingerprint, state = _manager_task_cards_state()
    state = apply(
        state,
        decide(
            state,
            FanoutFromArtifact(
                "fanout-manager-task-cards",
                fanout_id="planning.manager.task_cards_to_execution",
                source_artifact_id="transition-observe-manager-complete:artifact",
            ),
            planning_context("fanout-manager-task-cards"),
        ),
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.work_items == state.work_items
    assert loaded.activations == state.activations
    assert loaded.fanout_records == state.fanout_records
    assert loaded.work_dependencies == state.work_dependencies


@pytest.mark.parametrize(
    (
        "action_id",
        "input_id",
        "artifact",
        "target_work_item_id",
        "target_activation_id",
    ),
    (
        (
            "planning.recon_enqueue_task",
            "observe-recon-to-execution",
            {
                "task_id": "recon-task-1",
                "body": "Build the selected downstream task.",
            },
            "work-recon-task",
            "activation-recon-task",
        ),
        (
            "planning.recon_enqueue_spec",
            "observe-recon-to-planning",
            {
                "artifact_kind": "planning.artifacts.generated_spec",
                "spec_id": "recon-spec-1",
                "body": "Plan the selected downstream spec.",
            },
            "work-recon-spec",
            "activation-recon-spec",
        ),
    ),
)
def test_lad_recon_handoff_state_survives_restart(
    tmp_path: Path,
    action_id: str,
    input_id: str,
    artifact: dict[str, object],
    target_work_item_id: str,
    target_activation_id: str,
) -> None:
    plan, fingerprint, state = _recon_claimed_state()
    state, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-recon",
        action_id=action_id,
        input_id=input_id,
        artifact=artifact,
        target_work_item_id=target_work_item_id,
        target_activation_id=target_activation_id,
    )

    assert decision.accepted is True
    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.work_items == state.work_items
    assert loaded.activations == state.activations
    assert loaded.closed_work_items == state.closed_work_items
    assert loaded.runner_observations == state.runner_observations


@pytest.mark.parametrize(
    ("action_id", "input_id", "artifact"),
    (
        (
            "planning.recon_noop",
            "observe-recon-noop",
            artifact_payload("planning.artifacts.recon_packet"),
        ),
        (
            "planning.recon_blocked",
            "observe-recon-blocked",
            artifact_payload("planning.artifacts.report"),
        ),
    ),
)
def test_lad_recon_terminal_state_survives_restart(
    tmp_path: Path,
    action_id: str,
    input_id: str,
    artifact: object,
) -> None:
    plan, fingerprint, state = _recon_claimed_state()
    state, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-recon",
        action_id=action_id,
        input_id=input_id,
        artifact=artifact,
    )

    assert decision.accepted is True
    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.work_items == state.work_items
    assert loaded.activations == state.activations
    assert loaded.closed_work_items == state.closed_work_items
    assert loaded.runner_observations == state.runner_observations


def test_lad_recon_to_execution_routes_selected_generated_task() -> None:
    plan, fingerprint, state = _recon_claimed_state()

    state, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-recon",
        action_id="planning.recon_enqueue_task",
        input_id="observe-recon-to-execution",
        artifact={
            "task_id": "recon-task-1",
            "body": "Build the selected downstream task.",
        },
        target_work_item_id="work-recon-task",
        target_activation_id="activation-recon-task",
    )

    assert decision.accepted is True
    task = state.work_items["work-recon-task"]
    assert task.queue_family_id == QueueFamilyId("task")
    assert task.payload["task_id"] == "recon-task-1"
    assert task.lineage_id == "work-probe"
    activation = state.activations["activation-recon-task"]
    assert str(activation.stage_kind_id) == "lad_builder"


def test_lad_recon_to_planning_routes_selected_generated_spec() -> None:
    plan, fingerprint, state = _recon_claimed_state()

    state, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-recon",
        action_id="planning.recon_enqueue_spec",
        input_id="observe-recon-to-planning",
        artifact={
            "artifact_kind": "planning.artifacts.generated_spec",
            "spec_id": "recon-spec-1",
            "body": "Plan the selected downstream spec.",
        },
        target_work_item_id="work-recon-spec",
        target_activation_id="activation-recon-spec",
    )

    assert decision.accepted is True
    spec = state.work_items["work-recon-spec"]
    assert spec.queue_family_id == QueueFamilyId("spec")
    assert spec.payload["spec_id"] == "recon-spec-1"
    assert spec.lineage_id == "work-probe"
    activation = state.activations["activation-recon-spec"]
    assert str(activation.stage_kind_id) == "lad_planner"


def test_lad_recon_noop_closes_probe_without_downstream_work() -> None:
    plan, fingerprint, state = _recon_claimed_state()

    state, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-recon",
        action_id="planning.recon_noop",
        input_id="observe-recon-noop",
        artifact=artifact_payload("planning.artifacts.recon_packet"),
    )

    assert decision.accepted is True
    assert "work-probe" in state.closed_work_items
    assert not any(
        item.queue_family_id in {QueueFamilyId("task"), QueueFamilyId("spec")}
        and item.ref.work_item_id != "work-probe"
        for item in state.work_items.values()
    )


def test_lad_recon_blocked_records_blocked_terminal_without_recovery() -> None:
    plan, fingerprint, state = _recon_claimed_state()
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-recon",
        action_id="planning.recon_blocked",
        input_id="observe-recon-blocked",
        artifact=artifact_payload("planning.artifacts.report"),
        marker="BLOCKED",
    )
    decision = decide(state, observation, planning_context("observe-recon-blocked"))

    assert decision.accepted is True
    after = apply(state, decision)
    assert "work-probe" in after.closed_work_items
    assert after.closed_work_items["work-probe"].action_id is not None
    assert str(after.closed_work_items["work-probe"].action_id) == (
        "planning.recon_blocked"
    )
    assert not any(
        str(run.stage_kind_id) == "lad_mechanic"
        for run in after.runs.values()
    )
