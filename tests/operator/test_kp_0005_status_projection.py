from __future__ import annotations

from dataclasses import fields, replace
from typing import Any, cast

import pytest

from kernel.kernel_ping_scenarios import (
    admit_select_enqueue_two_and_claim_first,
    bootstrap_to_taskmaster_claim,
    bootstrap_to_worker_claim,
)
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.ids import QueueFamilyId, RunnerBindingId, StageKindId
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    EnqueueWork,
    InitializeWorkspace,
    SelectDefaultPlan,
)
from millrace.kernel import empty_runtime_state
from millrace.operator import OperatorInputError, OperatorStatus, operator_status
from millrace.operator.status import QueueFamilyStatus
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping
from support import kernel_ping as kernel_ping_support
from support.kernel_ping import (
    apply_accepted_input,
    compile_kernel_ping,
    kernel_ping_context,
    runner_observation,
    task_artifact_payload,
)


class _IntSubclass(int):
    pass


def _field_names(value: Any) -> tuple[str, ...]:
    return tuple(field.name for field in fields(value))


def _family(status: OperatorStatus, queue_family_id: str) -> QueueFamilyStatus:
    return next(
        family
        for family in status.queue_families
        if family.queue_family_id == queue_family_id
    )


def _assert_family_counts(
    status: OperatorStatus,
    queue_family_id: str,
    *,
    ready: int,
    active: int,
    closed: int = 0,
    quarantined: int = 0,
) -> None:
    family = _family(status, queue_family_id)
    assert (
        family.ready_count,
        family.active_count,
        family.closed_count,
        family.quarantined_count,
    ) == (ready, active, closed, quarantined)


def _bootstrap_to_prompt_ready(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init"),
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
        EnqueueWork(
            "enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-1", "body": "Build the kernel_ping proof"},
        ),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            kernel_ping_context(transition_input.input_id),
        )
    return state


def _route_taskmaster_success(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(
                objective="Prove status route projection",
            ),
        ),
        kernel_ping_context("observe-taskmaster"),
    )


def _close_worker_success(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.close_worker_success",
            input_id="observe-worker",
            artifact_payload={},
        ),
        kernel_ping_context("observe-worker"),
    )


def test_operator_status_empty_projection_without_default_plan() -> None:
    status = operator_status(empty_runtime_state())

    assert status.selected_plan is None
    assert status.known_plans == ()
    assert status.queue_families == ()
    assert status.partitions == ()
    assert status.stage_kinds == ()
    assert status.active_runs == ()
    assert status.pause.is_paused is False
    assert status.quarantines == ()
    assert status.recent_events == ()


def test_operator_status_reports_selected_and_known_plan_identity() -> None:
    plan_a, fingerprint_a = compile_kernel_ping(kernel_ping.workflow_source())
    plan_b, fingerprint_b = compile_kernel_ping(
        kernel_ping_support.no_pause_workflow_source()
    )
    state = empty_runtime_state()
    for transition_input in (
        AdmitPlan("admit-a", plan_a, fingerprint_a),
        SelectDefaultPlan("select-a", fingerprint_a),
        AdmitPlan("admit-b", plan_b, fingerprint_b),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}",
            ),
        )

    default_status = operator_status(state)
    override_status = operator_status(state, plan_fingerprint=fingerprint_b)

    assert default_status.selected_plan is not None
    assert _field_names(default_status.selected_plan) == (
        "plan_id",
        "workflow_id",
        "workflow_version",
        "workflow_name",
        "authority_fingerprint",
        "plan_format_version",
    )
    assert default_status.selected_plan.authority_fingerprint == fingerprint_a
    assert default_status.selected_plan.workflow_id == "kernel_ping"
    assert default_status.selected_plan.workflow_version == "0.1"
    assert default_status.selected_plan.workflow_name == "Kernel Ping"
    assert [plan.authority_fingerprint for plan in default_status.known_plans] == (
        sorted((fingerprint_a, fingerprint_b))
    )
    assert _field_names(default_status.known_plans[0]) == (
        "plan_id",
        "workflow_id",
        "workflow_version",
        "workflow_name",
        "authority_fingerprint",
        "plan_format_version",
        "selected_default",
    )
    assert {
        plan.authority_fingerprint: plan.selected_default
        for plan in default_status.known_plans
    } == {fingerprint_a: True, fingerprint_b: False}

    assert override_status.selected_plan is not None
    assert override_status.selected_plan.authority_fingerprint == fingerprint_b
    assert state.default_plan_ref is not None
    assert state.default_plan_ref.authority_fingerprint == fingerprint_a


def test_operator_status_reports_compiled_metadata_sorted_by_id() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = apply_accepted_input(
        apply_accepted_input(
            empty_runtime_state(),
            AdmitPlan("admit", plan, fingerprint),
            kernel_ping_context("admit"),
        ),
        SelectDefaultPlan("select", fingerprint),
        kernel_ping_context("select"),
    )

    status = operator_status(state)

    assert [record.queue_family_id for record in status.queue_families] == [
        "prompt",
        "task_artifact",
        "task_incident",
    ]
    assert status.queue_families[0].external_enqueue is True
    assert status.queue_families[0].display_name == "Prompt"
    assert status.queue_families[0].description == "User-provided generic prompt."
    assert status.queue_families[1].external_enqueue is False
    assert status.partitions[0].partition_id == "craft"
    assert status.partitions[0].display_name == "Craft"
    assert [record.stage_kind_id for record in status.stage_kinds] == [
        "kernel_ping.taskmaster",
        "kernel_ping.worker",
    ]
    assert status.stage_kinds[0].partition_id == "craft"
    assert status.stage_kinds[0].runner_binding_id == "kernel_ping.taskmaster_runner"
    assert status.stage_kinds[0].display_name == "Taskmaster"


def test_operator_status_queue_families_follow_compiled_ids_and_metadata() -> None:
    source = kernel_ping.workflow_source()
    queue_families = source["queue_families"]
    assert isinstance(queue_families, list)
    prompt_family = queue_families[0]
    assert isinstance(prompt_family, dict)
    prompt_family["id"] = "custom_input"
    prompt_family["presentation"] = {
        "display_name": "Custom Input",
        "description": "Operator-facing custom queue.",
    }

    external_routes = source["external_enqueue_routes"]
    assert isinstance(external_routes, list)
    external_route = external_routes[0]
    assert isinstance(external_route, dict)
    external_route["queue_family_id"] = "custom_input"

    stage_kinds = source["stage_kinds"]
    assert isinstance(stage_kinds, list)
    taskmaster = stage_kinds[0]
    assert isinstance(taskmaster, dict)
    taskmaster["input_queue_family_ids"] = ("custom_input", "task_incident")

    result = compile_workflow(source)
    assert result.plan is not None
    plan = result.plan
    fingerprint = authority_fingerprint(plan)
    state = apply_accepted_input(
        apply_accepted_input(
            empty_runtime_state(),
            AdmitPlan("admit-custom", plan, fingerprint),
            kernel_ping_context("admit-custom"),
        ),
        SelectDefaultPlan("select-custom", fingerprint),
        kernel_ping_context("select-custom"),
    )

    status = operator_status(state)

    assert [family.queue_family_id for family in status.queue_families] == [
        "custom_input",
        "task_artifact",
        "task_incident",
    ]
    assert status.queue_families[0].display_name == "Custom Input"
    assert status.queue_families[0].description == "Operator-facing custom queue."
    assert "prompt" not in {family.queue_family_id for family in status.queue_families}


def test_operator_status_queue_counts_follow_live_run_and_quarantine_semantics() -> (
    None
):
    plan, fingerprint = compile_kernel_ping()
    state = admit_select_enqueue_two_and_claim_first(plan, fingerprint)
    before_block = operator_status(state)
    prompt_before = next(
        family
        for family in before_block.queue_families
        if family.queue_family_id == "prompt"
    )
    assert prompt_before.ready_count == 1
    assert prompt_before.active_count == 1
    assert prompt_before.closed_count == 0
    assert prompt_before.quarantined_count == 0
    assert [run.run_id for run in before_block.active_runs] == ["run-taskmaster-a"]

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
    after_block = operator_status(blocked)
    prompt_after = next(
        family
        for family in after_block.queue_families
        if family.queue_family_id == "prompt"
    )

    assert prompt_after.ready_count == 1
    assert prompt_after.active_count == 0
    assert prompt_after.closed_count == 0
    assert prompt_after.quarantined_count == 1
    assert after_block.active_runs == ()
    assert after_block.pause.is_paused is True
    assert after_block.pause.work_item_id == "work-prompt-a"
    assert after_block.quarantines[0].work_item_id == "work-prompt-a"
    assert after_block.quarantines[0].queue_family_id == "prompt"


def test_operator_status_counts_enqueued_unclaimed_prompt_as_ready() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = _bootstrap_to_prompt_ready(plan, fingerprint)

    status = operator_status(state)

    _assert_family_counts(status, "prompt", ready=1, active=0)
    assert status.active_runs == ()


def test_operator_status_counts_claimed_taskmaster_prompt_as_active() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)

    status = operator_status(state)

    _assert_family_counts(status, "prompt", ready=0, active=1)
    assert [run.run_id for run in status.active_runs] == ["run-taskmaster"]


def test_operator_status_does_not_resurrect_observed_taskmaster_prompt_as_ready() -> (
    None
):
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    routed = _route_taskmaster_success(state, plan, fingerprint)

    status = operator_status(routed)

    _assert_family_counts(status, "prompt", ready=0, active=0)
    _assert_family_counts(status, "task_artifact", ready=1, active=0)
    assert status.active_runs == ()


def test_operator_status_worker_claim_projects_only_task_artifact_active() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)

    status = operator_status(state)

    _assert_family_counts(status, "prompt", ready=0, active=0)
    _assert_family_counts(status, "task_artifact", ready=0, active=1)
    assert [run.run_id for run in status.active_runs] == ["run-worker"]


def test_operator_status_worker_close_does_not_resurrect_upstream_prompt() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    closed = _close_worker_success(state, plan, fingerprint)

    status = operator_status(closed)

    _assert_family_counts(status, "prompt", ready=0, active=0)
    _assert_family_counts(status, "task_artifact", ready=0, active=0, closed=1)
    assert status.active_runs == ()


def test_operator_status_active_runs_include_resolved_run_context() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)

    status = operator_status(state)

    assert len(status.active_runs) == 1
    active_run = status.active_runs[0]
    assert active_run.run_id == "run-taskmaster"
    assert active_run.work_item_id == "work-prompt"
    assert active_run.activation_id == "activation-taskmaster"
    assert active_run.lineage_id == "work-prompt"
    assert active_run.queue_family_id == "prompt"
    assert active_run.graph_node_id == "kernel_ping.taskmaster.start"
    assert active_run.stage_kind_id == "kernel_ping.taskmaster"
    assert active_run.runner_binding_id == "kernel_ping.taskmaster_runner"
    assert active_run.claim_id == "claim-taskmaster"
    assert active_run.generation == 0
    assert active_run.fencing_token == "fence-taskmaster"
    assert active_run.plan_fingerprint == fingerprint


def test_operator_status_counts_closed_work_and_filters_selected_plan() -> None:
    plan_a, fingerprint_a = compile_kernel_ping(kernel_ping.workflow_source())
    plan_b, fingerprint_b = compile_kernel_ping(
        kernel_ping_support.no_pause_workflow_source()
    )
    state = bootstrap_to_worker_claim(plan_a, fingerprint_a)
    state = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan_a,
            fingerprint=fingerprint_a,
            run_id="run-worker",
            action_id="kernel_ping.close_worker_success",
            input_id="observe-worker-success",
            artifact_payload={},
        ),
        kernel_ping_context("observe-worker-success"),
    )
    for transition_input in (
        AdmitPlan("admit-b", plan_b, fingerprint_b),
        SelectDefaultPlan("select-b", fingerprint_b),
        EnqueueWork(
            "enqueue-b",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"body": "plan b prompt"},
        ),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}",
                work_item_id="work-plan-b",
                activation_id="activation-plan-b",
            ),
        )

    default_status = operator_status(state)
    override_status = operator_status(state, plan_fingerprint=fingerprint_a)

    default_prompt = next(
        family
        for family in default_status.queue_families
        if family.queue_family_id == "prompt"
    )
    assert default_prompt.ready_count == 1
    assert default_prompt.closed_count == 0

    task_artifact = next(
        family
        for family in override_status.queue_families
        if family.queue_family_id == "task_artifact"
    )
    assert task_artifact.closed_count == 1
    assert override_status.selected_plan is not None
    assert override_status.selected_plan.authority_fingerprint == fingerprint_a


def test_operator_status_recent_events_are_bounded_and_deterministic() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)

    first = operator_status(state, max_events=3)
    second = operator_status(state, max_events=3)

    assert first == second
    assert len(first.recent_events) == 3
    assert _field_names(first.recent_events[0]) == (
        "record_id",
        "input_id",
        "input_kind",
        "input_family",
        "disposition",
        "plan_fingerprint",
        "work_item_id",
        "run_id",
        "action_id",
        "authority_source",
        "refusal_reason",
        "source",
    )

    with pytest.raises(OperatorInputError) as exc_info:
        operator_status(state, max_events=-1)
    assert exc_info.value.reason == "invalid_max_events"


@pytest.mark.parametrize(
    "max_events",
    (
        True,
        False,
        1.0,
        "1",
        None,
        _IntSubclass(1),
        -1,
    ),
)
def test_operator_status_rejects_malformed_max_events(max_events: object) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)

    with pytest.raises(OperatorInputError) as exc_info:
        operator_status(state, max_events=cast(Any, max_events))

    assert exc_info.value.reason == "invalid_max_events"


def test_operator_status_accepts_zero_max_events_as_empty_event_window() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)

    status = operator_status(state, max_events=0)

    assert status.recent_events == ()


def test_operator_status_recent_events_tail_latest_governance_and_trace_pair() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)

    status = operator_status(state, max_events=2)

    assert [(event.input_id, event.source) for event in status.recent_events] == [
        ("claim-taskmaster", "governance_event"),
        ("claim-taskmaster", "trace"),
    ]


@pytest.mark.parametrize(
    "plan_fingerprint",
    (
        "",
        "not-a-fingerprint",
        "sha256:",
        "sha256:not-hex",
    ),
)
def test_operator_status_rejects_malformed_plan_fingerprint(
    plan_fingerprint: str,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = apply_accepted_input(
        empty_runtime_state(),
        AdmitPlan("admit", plan, fingerprint),
        kernel_ping_context("admit"),
    )

    with pytest.raises(OperatorInputError) as exc_info:
        operator_status(state, plan_fingerprint=plan_fingerprint)

    assert exc_info.value.reason == "invalid_plan_fingerprint"


def test_operator_status_unknown_valid_plan_fingerprint_returns_empty_projection() -> (
    None
):
    plan, fingerprint = compile_kernel_ping()
    state = apply_accepted_input(
        empty_runtime_state(),
        AdmitPlan("admit", plan, fingerprint),
        kernel_ping_context("admit"),
    )

    status = operator_status(state, plan_fingerprint=f"sha256:{'0' * 64}")

    assert status.selected_plan is None
    assert status.known_plans[0].authority_fingerprint == fingerprint
    assert status.queue_families == ()


def test_operator_status_projection_is_data_only() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)

    status = operator_status(state)

    assert "build_enqueue_work" not in _field_names(status)
    assert "decide" not in _field_names(status)
    assert "apply" not in _field_names(status)
    assert status.queue_families[0].queue_family_id in {
        str(queue_family.id) for queue_family in plan.queue_families
    }


@pytest.mark.parametrize(
    "case",
    (
        "activation_unclaimed",
        "activation_claimed_by_different_run",
        "run_ref_work_item_id_mismatch",
        "run_activation_id_mismatch",
        "activation_work_item_id_mismatch",
        "run_plan_ref_mismatch",
        "activation_plan_ref_mismatch",
        "work_item_plan_ref_mismatch",
        "activation_generation_mismatch",
        "work_item_generation_mismatch",
        "activation_stage_kind_mismatch",
        "activation_runner_binding_mismatch",
        "activation_lineage_mismatch",
        "activation_queue_family_mismatch",
    ),
)
def test_operator_status_omits_incoherent_active_runs(case: str) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    run = state.runs["run-worker"]
    activation = state.activations[run.activation_id]
    work_item = state.work_items[run.work_item_id]
    tampered_run = run
    tampered_activation = activation
    tampered_work_item = work_item

    if case == "activation_unclaimed":
        tampered_activation = replace(activation, claimed_by_run_id=None)
    elif case == "activation_claimed_by_different_run":
        tampered_activation = replace(
            activation,
            claimed_by_run_id="run-different",
        )
    elif case == "run_ref_work_item_id_mismatch":
        tampered_run = replace(
            run,
            run_ref=replace(run.run_ref, work_item_id="work-different"),
        )
    elif case == "run_activation_id_mismatch":
        tampered_run = replace(run, activation_id="activation-different")
    elif case == "activation_work_item_id_mismatch":
        tampered_activation = replace(activation, work_item_id="work-different")
    elif case == "run_plan_ref_mismatch":
        tampered_run = replace(
            run,
            run_ref=replace(
                run.run_ref,
                plan_ref=replace(
                    run.run_ref.plan_ref,
                    authority_fingerprint=f"sha256:{'1' * 64}",
                ),
            ),
        )
    elif case == "activation_plan_ref_mismatch":
        tampered_activation = replace(
            activation,
            plan_ref=replace(
                activation.plan_ref,
                authority_fingerprint=f"sha256:{'2' * 64}",
            ),
        )
    elif case == "work_item_plan_ref_mismatch":
        tampered_work_item = replace(
            work_item,
            ref=replace(
                work_item.ref,
                plan_ref=replace(
                    work_item.ref.plan_ref,
                    authority_fingerprint=f"sha256:{'3' * 64}",
                ),
            ),
        )
    elif case == "activation_generation_mismatch":
        tampered_activation = replace(activation, generation=activation.generation + 1)
    elif case == "work_item_generation_mismatch":
        tampered_work_item = replace(
            work_item,
            ref=replace(work_item.ref, generation=work_item.ref.generation + 1),
        )
    elif case == "activation_stage_kind_mismatch":
        tampered_activation = replace(
            activation,
            stage_kind_id=StageKindId("kernel_ping.wrong_stage"),
        )
    elif case == "activation_runner_binding_mismatch":
        tampered_activation = replace(
            activation,
            runner_binding_id=RunnerBindingId("kernel_ping.wrong_runner"),
        )
    elif case == "activation_lineage_mismatch":
        tampered_activation = replace(activation, lineage_id="lineage-different")
    elif case == "activation_queue_family_mismatch":
        tampered_activation = replace(
            activation,
            queue_family_id=QueueFamilyId("prompt"),
        )
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(f"unknown active-run coherence case: {case}")

    tampered = replace(
        state,
        runs={**state.runs, run.run_ref.run_id: tampered_run},
        activations={
            **state.activations,
            activation.activation_id: tampered_activation,
        },
        work_items={
            **state.work_items,
            work_item.ref.work_item_id: tampered_work_item,
        },
    )

    status = operator_status(tampered)

    _assert_family_counts(status, "task_artifact", ready=0, active=0)
    assert status.active_runs == ()


@pytest.mark.parametrize(
    "case",
    (
        "activation_work_item_id_mismatch",
        "activation_plan_ref_mismatch",
        "activation_generation_mismatch",
        "activation_lineage_mismatch",
        "activation_queue_family_mismatch",
    ),
)
def test_operator_status_ready_projection_requires_coherent_unclaimed_activation(
    case: str,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = _bootstrap_to_prompt_ready(plan, fingerprint)
    work_item = state.work_items["work-prompt"]
    activation = state.activations["activation-taskmaster"]
    tampered_activation = activation

    if case == "activation_work_item_id_mismatch":
        tampered_activation = replace(activation, work_item_id="work-different")
    elif case == "activation_plan_ref_mismatch":
        tampered_activation = replace(
            activation,
            plan_ref=replace(
                activation.plan_ref,
                authority_fingerprint=f"sha256:{'4' * 64}",
            ),
        )
    elif case == "activation_generation_mismatch":
        tampered_activation = replace(activation, generation=activation.generation + 1)
    elif case == "activation_lineage_mismatch":
        tampered_activation = replace(activation, lineage_id="lineage-different")
    elif case == "activation_queue_family_mismatch":
        tampered_activation = replace(
            activation,
            queue_family_id=QueueFamilyId("task_artifact"),
        )
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(f"unknown ready coherence case: {case}")

    tampered = replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: tampered_activation,
        },
    )

    status = operator_status(tampered)

    assert work_item.ref.work_item_id not in {
        run.work_item_id for run in status.active_runs
    }
    _assert_family_counts(status, "prompt", ready=0, active=0)
