from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

from kernel.kernel_ping_scenarios import (
    admit_select_enqueue_two_and_claim_first,
    bootstrap_to_taskmaster_claim,
    bootstrap_to_worker_claim,
)
from millrace.compiler.canonical import canonical_authority_bytes
from millrace.contracts.ids import QueueFamilyId
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    SelectDefaultPlan,
)
from millrace.kernel import apply
from millrace.operator import OperatorStatus, operator_status
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_completion_input_id,
)
from millrace.workflows import kernel_ping
from substrate._runtime_store_support import (
    persist_and_load_runtime_state,
    taskmaster_runtime_state,
)
from support import kernel_ping as kernel_ping_support
from support.kernel_ping import (
    action_by_id,
    apply_accepted_input,
    compile_kernel_ping,
    kernel_ping_context,
    mutation_kinds,
    runner_observation,
    task_artifact_payload,
)


def _component_source() -> dict[str, object]:
    source = kernel_ping.workflow_source()
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    runner = cast(list[dict[str, object]], source["runner_bindings"])[0]
    runner_id = str(runner["id"])
    source["runner_bindings"] = [runner]
    for key in ("stage_kinds", "external_enqueue_routes", "terminal_actions"):
        for record in cast(list[dict[str, object]], source[key]):
            if record.get("runner_binding_id") is not None:
                record["runner_binding_id"] = runner_id
    runner.update(
        {
            "adapter_kind": "codex",
            "stage_kind_ids": (
                "kernel_ping.taskmaster",
                "kernel_ping.worker",
            ),
            "required_capability_ids": ("capability.runner.invoke",),
            "component_pin": {
                "component_kind": "opaque.runner",
                "component_id": "example.component",
                "component_version": "1.2.3",
                "provider_distribution": "example-provider",
                "provider_version": "4.5.6",
                "descriptor_media_type": "application/vnd.example.runner+json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": ("capability.runner.invoke",),
                "legal_terminal_result_ids": ("COMPLETE", "BLOCKED"),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "COMPLETE",
                    "outcome_id": "kernel_ping.taskmaster.task_complete",
                },
            ),
        }
    )
    return source


def test_restart_preserves_exact_format_16_runner_component_and_active_plan_pin(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_kernel_ping(_component_source())
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)
    loaded_plan = loaded.admitted_plans[fingerprint].selected_plan

    assert loaded_plan.schema_version == 16
    assert canonical_authority_bytes(loaded_plan) == canonical_authority_bytes(plan)
    assert loaded_plan.runner_bindings[0].component_pin == (
        plan.runner_bindings[0].component_pin
    )
    assert loaded_plan.runner_bindings[0].terminal_result_mappings == (
        plan.runner_bindings[0].terminal_result_mappings
    )
    assert loaded.runs["run-taskmaster"].run_ref.plan_ref == (
        state.runs["run-taskmaster"].run_ref.plan_ref
    )


def test_restarted_component_pinned_run_accepts_legal_runner_observation(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_kernel_ping(_component_source())
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    loaded = persist_and_load_runtime_state(tmp_path, state)
    loaded_plan = loaded.admitted_plans[fingerprint].selected_plan

    decision = decide(
        loaded,
        runner_observation(
            state=loaded,
            plan=loaded_plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(
                objective="Continue component-pinned run after restart",
            ),
        ),
        kernel_ping_context("observe-taskmaster"),
    )
    continued = apply(loaded, decision)

    assert decision.accepted is True
    assert "mutation.create_work_item" in mutation_kinds(decision)
    assert "mutation.create_activation" in mutation_kinds(decision)
    assert continued.work_items["work-task-artifact"].created_by_input_id == (
        fake_runner_completion_input_id("observe-taskmaster")
    )


def test_restart_preserves_admitted_plan_default_plan_receipts_and_work_item(
    tmp_path: Path,
) -> None:
    state = taskmaster_runtime_state()

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.admitted_plans == state.admitted_plans
    assert loaded.default_plan_ref == state.default_plan_ref
    assert loaded.receipts == state.receipts
    assert loaded.work_items == state.work_items
    assert loaded.runner_observations == {}
    assert loaded.artifacts == {}
    assert loaded.activation_routes == ()
    assert loaded.closed_work_items == {}
    assert loaded.pause is None
    assert loaded.quarantines == {}
    assert loaded.governance_events == state.governance_events
    assert loaded.traces == state.traces
    assert loaded.transitions == state.transitions
    assert loaded.refusals == state.refusals


def test_restart_preserves_activation_claim_run_generation_and_fencing_token(
    tmp_path: Path,
) -> None:
    state = taskmaster_runtime_state()

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.activations == state.activations
    assert loaded.runs == state.runs
    assert loaded.activations["activation-taskmaster"].claimed_by_run_id == (
        "run-taskmaster"
    )
    assert loaded.activations["activation-taskmaster"].generation == (
        state.activations["activation-taskmaster"].generation
    )
    run_ref = loaded.runs["run-taskmaster"].run_ref
    assert run_ref.generation == 0
    assert run_ref.claim_id == "claim-taskmaster"
    assert run_ref.fencing_token == "fence-taskmaster"


def test_replayed_enqueue_after_restart_returns_original_receipt_without_duplicate_mutations(  # noqa: E501
    tmp_path: Path,
) -> None:
    state = taskmaster_runtime_state()
    loaded = persist_and_load_runtime_state(tmp_path, state)

    replay_decision = decide(
        loaded,
        EnqueueWork(
            "enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-1", "body": "Build the kernel_ping proof"},
        ),
        kernel_ping_context("replayed-enqueue"),
    )
    after_replay = apply(loaded, replay_decision)

    assert replay_decision.accepted is True
    assert replay_decision.disposition == "replayed"
    assert replay_decision.receipt_ref == loaded.receipts["enqueue"].receipt_ref
    assert replay_decision.mutations == ()
    assert after_replay == loaded


def test_same_input_id_different_payload_after_restart_is_refused(
    tmp_path: Path,
) -> None:
    state = taskmaster_runtime_state()
    loaded = persist_and_load_runtime_state(tmp_path, state)

    conflict_decision = decide(
        loaded,
        EnqueueWork(
            "enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-1", "body": "changed after restart"},
        ),
        kernel_ping_context("conflicting-enqueue"),
    )
    after_conflict = apply(loaded, conflict_decision)

    assert conflict_decision.accepted is False
    assert conflict_decision.refusal is not None
    assert conflict_decision.refusal.reason == "idempotency_conflict"
    assert after_conflict.receipts == loaded.receipts
    assert after_conflict.work_items == loaded.work_items
    assert after_conflict.activations == loaded.activations
    assert after_conflict.runs == loaded.runs


def test_loaded_state_can_accept_claimed_run_context_without_default_plan_rebuild(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    loaded = persist_and_load_runtime_state(tmp_path, state)

    decision = decide(
        loaded,
        runner_observation(
            state=loaded,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(
                objective="Continue after restart",
            ),
        ),
        kernel_ping_context("observe-taskmaster"),
    )
    after_observation = apply(loaded, decision)

    assert decision.accepted is True
    assert "mutation.create_work_item" in mutation_kinds(decision)
    assert "mutation.create_activation" in mutation_kinds(decision)
    assert after_observation.work_items["work-task-artifact"].created_by_input_id == (
        fake_runner_completion_input_id("observe-taskmaster")
    )
    assert after_observation.activations["activation-worker"].created_by_input_id == (
        fake_runner_completion_input_id("observe-taskmaster")
    )


def test_restart_accepts_runner_result_for_active_run_after_default_plan_changes(
    tmp_path: Path,
) -> None:
    plan_a, fingerprint_a = compile_kernel_ping(kernel_ping.workflow_source())
    plan_b, fingerprint_b = compile_kernel_ping(
        kernel_ping_support.no_pause_workflow_source()
    )
    assert fingerprint_a != fingerprint_b
    assert action_by_id(plan_a, "kernel_ping.pause_taskmaster_blocked").action_kind == (
        "pause_quarantine"
    )
    assert action_by_id(plan_b, "kernel_ping.pause_taskmaster_blocked").action_kind == (
        "route"
    )
    state = admit_select_enqueue_two_and_claim_first(plan_a, fingerprint_a)
    state = apply_accepted_input(
        state,
        AdmitPlan(
            "admit-no-pause",
            selected_plan=plan_b,
            authority_fingerprint=fingerprint_b,
        ),
        kernel_ping_context("admit-no-pause"),
    )
    state = apply_accepted_input(
        state,
        SelectDefaultPlan("select-no-pause", authority_fingerprint=fingerprint_b),
        kernel_ping_context("select-no-pause"),
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)
    assert loaded.default_plan_ref is not None
    assert loaded.default_plan_ref.authority_fingerprint == fingerprint_b
    assert (
        loaded.runs["run-taskmaster-a"].run_ref.plan_ref.authority_fingerprint
        == fingerprint_a
    )

    blocked_decision = decide(
        loaded,
        runner_observation(
            state=loaded,
            plan=plan_a,
            fingerprint=fingerprint_a,
            run_id="run-taskmaster-a",
            action_id="kernel_ping.pause_taskmaster_blocked",
            input_id="observe-taskmaster-blocked",
            artifact_payload={},
        ),
        kernel_ping_context("observe-taskmaster-blocked"),
    )
    after_blocked = apply(loaded, blocked_decision)

    assert blocked_decision.accepted is True
    assert {"mutation.set_pause", "mutation.set_quarantine"} <= set(
        mutation_kinds(blocked_decision)
    )
    assert "mutation.create_work_item" not in mutation_kinds(blocked_decision)
    assert after_blocked.pause is not None
    assert set(after_blocked.quarantines) == {"work-prompt-a"}


def test_restart_preserves_taskmaster_artifact_and_worker_activation_route(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    routed = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(objective="Persist route records"),
        ),
        kernel_ping_context("observe-taskmaster"),
    )

    loaded = persist_and_load_runtime_state(tmp_path, routed)

    assert loaded.runner_observations == routed.runner_observations
    assert loaded.artifacts == routed.artifacts
    assert loaded.activation_routes == routed.activation_routes
    assert (
        loaded.work_items["work-task-artifact"]
        == routed.work_items["work-task-artifact"]
    )
    assert (
        loaded.activations["activation-worker"]
        == routed.activations["activation-worker"]
    )

    claim_decision = decide(
        loaded,
        ClaimWork("claim-worker", activation_id="activation-worker"),
        kernel_ping_context("claim-worker"),
    )
    after_claim = apply(loaded, claim_decision)

    assert claim_decision.accepted is True
    assert after_claim.runs["run-worker"].work_item_id == "work-task-artifact"


def test_restart_preserves_worker_close_event_trace_and_transition_history(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(
        plan,
        fingerprint,
        task_artifact=task_artifact_payload(objective="Close after restart"),
    )
    loaded = persist_and_load_runtime_state(tmp_path, state)

    close_decision = decide(
        loaded,
        runner_observation(
            state=loaded,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.close_worker_success",
            input_id="observe-worker",
            artifact_payload={},
        ),
        kernel_ping_context("observe-worker"),
    )
    closed = apply(loaded, close_decision)
    reloaded = persist_and_load_runtime_state(tmp_path, closed)

    assert close_decision.accepted is True
    assert reloaded.closed_work_items == closed.closed_work_items
    assert reloaded.runner_observations == closed.runner_observations
    assert reloaded.transitions == closed.transitions
    assert reloaded.governance_events == closed.governance_events
    assert reloaded.traces == closed.traces
    assert any(
        record.input_id == fake_runner_completion_input_id("observe-worker")
        for record in reloaded.transitions
    )
    assert any(
        event.input_id == fake_runner_completion_input_id("observe-worker")
        and event.action_id == action_by_id(plan, "kernel_ping.close_worker_success").id
        for event in reloaded.governance_events
    )


def test_restart_preserves_kp_0005_status_projection_without_upstream_resurrection(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    routed = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(
                objective="Persist status route projection",
            ),
        ),
        kernel_ping_context("observe-taskmaster"),
    )

    _assert_kp_0005_status_projection(
        operator_status(routed),
        prompt=(0, 0, 0, 0),
        task_artifact=(1, 0, 0, 0),
        active_run_ids=(),
    )
    _assert_kp_0005_status_projection(
        _persist_and_project_status(tmp_path, "routed", routed),
        prompt=(0, 0, 0, 0),
        task_artifact=(1, 0, 0, 0),
        active_run_ids=(),
    )

    worker_claimed = apply_accepted_input(
        routed,
        ClaimWork("claim-worker", activation_id="activation-worker"),
        kernel_ping_context("claim-worker"),
    )
    _assert_kp_0005_status_projection(
        operator_status(worker_claimed),
        prompt=(0, 0, 0, 0),
        task_artifact=(0, 1, 0, 0),
        active_run_ids=("run-worker",),
    )
    _assert_kp_0005_status_projection(
        _persist_and_project_status(tmp_path, "worker-claimed", worker_claimed),
        prompt=(0, 0, 0, 0),
        task_artifact=(0, 1, 0, 0),
        active_run_ids=("run-worker",),
    )

    worker_closed = apply_accepted_input(
        worker_claimed,
        runner_observation(
            state=worker_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.close_worker_success",
            input_id="observe-worker",
            artifact_payload={},
        ),
        kernel_ping_context("observe-worker"),
    )
    _assert_kp_0005_status_projection(
        operator_status(worker_closed),
        prompt=(0, 0, 0, 0),
        task_artifact=(0, 0, 1, 0),
        active_run_ids=(),
    )
    _assert_kp_0005_status_projection(
        _persist_and_project_status(tmp_path, "worker-closed", worker_closed),
        prompt=(0, 0, 0, 0),
        task_artifact=(0, 0, 1, 0),
        active_run_ids=(),
    )


def test_restart_preserves_needs_review_incident_route(tmp_path: Path) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(
        plan,
        fingerprint,
        task_artifact=task_artifact_payload(objective="Trigger review route"),
    )
    reviewed = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.route_worker_review",
            input_id="observe-needs-review",
            artifact_payload={
                "worker_summary": "The task artifact lacks an acceptance command.",
                "missing_details": ("exact command", "expected output"),
            },
        ),
        kernel_ping_context("observe-needs-review"),
    )

    loaded = persist_and_load_runtime_state(tmp_path, reviewed)

    assert loaded.runner_observations == reviewed.runner_observations
    assert loaded.artifacts == reviewed.artifacts
    assert loaded.activation_routes == reviewed.activation_routes
    assert (
        loaded.work_items["work-review-incident"]
        == reviewed.work_items["work-review-incident"]
    )
    assert (
        loaded.activations["activation-review-taskmaster"]
        == reviewed.activations["activation-review-taskmaster"]
    )

    claim_decision = decide(
        loaded,
        ClaimWork(
            "claim-review-taskmaster",
            activation_id="activation-review-taskmaster",
        ),
        deterministic_context(
            transition_id="transition-claim-review-taskmaster",
            run_id="run-review-taskmaster",
            claim_id="claim-review-taskmaster",
            fencing_token="fence-review-taskmaster",
        ),
    )
    after_claim = apply(loaded, claim_decision)

    assert claim_decision.accepted is True
    assert after_claim.runs["run-review-taskmaster"].work_item_id == (
        "work-review-incident"
    )


def test_restart_preserves_blocked_pause_and_quarantine_state(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_kernel_ping(kernel_ping.workflow_source())
    state = admit_select_enqueue_two_and_claim_first(plan, fingerprint)
    blocked = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster-a",
            action_id="kernel_ping.pause_taskmaster_blocked",
            input_id="observe-taskmaster-blocked",
            artifact_payload={},
        ),
        kernel_ping_context("observe-taskmaster-blocked"),
    )

    loaded = persist_and_load_runtime_state(tmp_path, blocked)

    assert loaded.pause == blocked.pause
    assert loaded.quarantines == blocked.quarantines
    assert loaded.runner_observations == blocked.runner_observations

    claim_later = decide(
        loaded,
        ClaimWork("claim-taskmaster-b", activation_id="activation-taskmaster-b"),
        deterministic_context(
            transition_id="transition-claim-taskmaster-b",
            run_id="run-taskmaster-b",
            claim_id="claim-taskmaster-b",
            fencing_token="fence-taskmaster-b",
        ),
    )
    after_claim_later = apply(loaded, claim_later)

    assert claim_later.accepted is False
    assert claim_later.refusal is not None
    assert claim_later.refusal.reason == "workspace_paused"
    assert after_claim_later.runs == loaded.runs
    assert after_claim_later.pause == loaded.pause
    assert after_claim_later.quarantines == loaded.quarantines


def test_restart_preserves_refusal_history_without_mutating_workflow_progress(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    loaded = persist_and_load_runtime_state(
        tmp_path,
        bootstrap_to_taskmaster_claim(plan, fingerprint),
    )

    invalid_observation = runner_observation(
        state=loaded,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-taskmaster",
        action_id="kernel_ping.route_taskmaster_success",
        input_id="observe-taskmaster",
        artifact_payload=task_artifact_payload(objective="Invalid fence"),
        overrides={"fencing_token": "wrong-fence"},
    )
    refusal_decision = decide(
        loaded,
        invalid_observation,
        kernel_ping_context("observe-taskmaster"),
    )
    refused = apply(loaded, refusal_decision)
    reloaded = persist_and_load_runtime_state(tmp_path, refused)

    assert refusal_decision.accepted is False
    assert refusal_decision.refusal is not None
    assert refusal_decision.refusal.reason == "invalid_observation_authority"
    assert refused.work_items == loaded.work_items
    assert refused.activations == loaded.activations
    assert refused.runs == loaded.runs
    assert refused.runner_observations == loaded.runner_observations
    assert refused.artifacts == loaded.artifacts
    assert refused.activation_routes == loaded.activation_routes
    assert refused.closed_work_items == loaded.closed_work_items

    assert reloaded.refusals == refused.refusals
    assert reloaded.transitions == refused.transitions
    assert reloaded.governance_events == refused.governance_events
    assert reloaded.traces == refused.traces
    assert reloaded.work_items == refused.work_items
    assert reloaded.activations == refused.activations
    assert reloaded.runs == refused.runs
    expected_order = _transition_order(refused, "transition-observe-taskmaster")
    assert _audit_row_transition_orders(tmp_path, "transition-observe-taskmaster") == {
        "transition": expected_order,
        "governance": expected_order,
        "trace": expected_order,
        "refusal": expected_order,
    }


def test_restart_refuses_wrong_runner_binding_without_workflow_progress(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    loaded = persist_and_load_runtime_state(
        tmp_path,
        bootstrap_to_taskmaster_claim(plan, fingerprint),
    )

    invalid_observation = runner_observation(
        state=loaded,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-taskmaster",
        action_id="kernel_ping.route_taskmaster_success",
        input_id="observe-wrong-runner-binding",
        artifact_payload=task_artifact_payload(objective="Invalid runner binding"),
        overrides={"runner_binding_id": "wrong-runner"},
    )
    refusal_decision = decide(
        loaded,
        invalid_observation,
        kernel_ping_context("observe-wrong-runner-binding"),
    )
    refused = apply(loaded, refusal_decision)
    reloaded = persist_and_load_runtime_state(tmp_path, refused)

    assert refusal_decision.accepted is False
    assert refusal_decision.refusal is not None
    assert refusal_decision.refusal.reason == "invalid_observation_authority"
    assert "mutation.record_runner_observation" not in mutation_kinds(refusal_decision)
    assert "mutation.create_work_item" not in mutation_kinds(refusal_decision)
    assert refused.work_items == loaded.work_items
    assert refused.activations == loaded.activations
    assert refused.runs == loaded.runs
    assert refused.runner_observations == loaded.runner_observations
    assert refused.artifacts == loaded.artifacts
    assert refused.activation_routes == loaded.activation_routes
    assert refused.closed_work_items == loaded.closed_work_items
    assert reloaded.work_items == refused.work_items
    assert reloaded.activations == refused.activations
    assert reloaded.runs == refused.runs
    assert reloaded.runner_observations == refused.runner_observations


def _audit_row_transition_orders(
    tmp_path: Path,
    transition_id: str,
) -> dict[str, int]:
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        transition_order = connection.execute(
            "SELECT transition_order FROM transitions WHERE record_id = ?",
            (transition_id,),
        ).fetchone()[0]
        governance_order = connection.execute(
            "SELECT transition_order FROM governance_events WHERE record_id = ?",
            (f"{transition_id}:governance",),
        ).fetchone()[0]
        trace_order = connection.execute(
            "SELECT transition_order FROM traces WHERE record_id = ?",
            (f"{transition_id}:trace",),
        ).fetchone()[0]
        refusal_order = connection.execute(
            "SELECT transition_order FROM refusals WHERE record_id = ?",
            (f"{transition_id}:refusal",),
        ).fetchone()[0]
    return {
        "transition": transition_order,
        "governance": governance_order,
        "trace": trace_order,
        "refusal": refusal_order,
    }


def _transition_order(state: RuntimeState, transition_id: str) -> int:
    return next(
        order
        for order, record in enumerate(state.transitions)
        if record.record_id == transition_id
    )


def _persist_and_project_status(
    tmp_path: Path,
    stage: str,
    state: RuntimeState,
) -> OperatorStatus:
    stage_path = tmp_path / stage
    stage_path.mkdir()
    loaded = persist_and_load_runtime_state(stage_path, state)
    return operator_status(loaded)


def _assert_kp_0005_status_projection(
    status: OperatorStatus,
    *,
    prompt: tuple[int, int, int, int],
    task_artifact: tuple[int, int, int, int],
    active_run_ids: tuple[str, ...],
) -> None:
    assert _queue_counts(status, "prompt") == prompt
    assert _queue_counts(status, "task_artifact") == task_artifact
    assert tuple(run.run_id for run in status.active_runs) == active_run_ids


def _queue_counts(
    status: OperatorStatus,
    queue_family_id: str,
) -> tuple[int, int, int, int]:
    family = next(
        family
        for family in status.queue_families
        if family.queue_family_id == queue_family_id
    )
    return (
        family.ready_count,
        family.active_count,
        family.closed_count,
        family.quarantined_count,
    )
