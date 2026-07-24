from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import (
    ArtifactSchemaId,
    ClaimWork,
    PartitionId,
    QueueFamilyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.transition import AdmitPlan, InitializeWorkspace
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.operator import (
    OperatorEnqueueInput,
    OperatorInputError,
    build_enqueue_work,
)
from millrace.testing import fake_runner_dispatch_envelope_for_run
from support import lad_learning


def _admission_refusal_detail(corrupt_plan) -> str | None:
    fingerprint = authority_fingerprint(corrupt_plan)
    state = lad_learning.apply_accepted_input(
        empty_runtime_state(),
        InitializeWorkspace("init-corrupt-learning"),
        lad_learning.context("init-corrupt-learning"),
    )
    decision = decide(
        state,
        AdmitPlan(
            "admit-corrupt-learning",
            selected_plan=corrupt_plan,
            authority_fingerprint=fingerprint,
        ),
        lad_learning.context("admit-corrupt-learning"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    return decision.refusal.detail


def test_learning_request_intake_dispatches_analyst_by_selected_route() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.admitted_state(plan, fingerprint)

    enqueue = build_enqueue_work(
        state,
        OperatorEnqueueInput(
            input_id="enqueue-learning-request",
            queue_family_id="learning_request",
            payload=lad_learning.learning_payload(),
            plan_fingerprint=fingerprint,
        ),
    )
    state = lad_learning.apply_accepted_input(
        state,
        enqueue,
        lad_learning.context(
            "enqueue-learning-request",
            work_item_id="work-learning-request",
            activation_id="activation-learning-request",
        ),
    )

    work = state.work_items["work-learning-request"]
    activation = state.activations["activation-learning-request"]
    assert work.queue_family_id == QueueFamilyId("learning_request")
    assert activation.graph_node_id == "learning.standard.analyst"
    assert activation.stage_kind_id == StageKindId("analyst")
    assert activation.queue_family_id == QueueFamilyId("learning_request")

    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-analyst",
        input_id="claim-analyst",
    )
    dispatch = fake_runner_dispatch_envelope_for_run(state=state, run_id="run-analyst")
    assert dispatch.plan_fingerprint == fingerprint
    assert dispatch.workflow_id == "lad.full"
    assert dispatch.graph_node_id == "learning.standard.analyst"
    assert dispatch.stage_kind_id == "analyst"
    assert dispatch.queue_family_id == "learning_request"
    assert dispatch.external_enqueue_route_id == "learning_request"
    assert dispatch.runner_binding_id == "learning.standard.local_runner"
    assert dispatch.work_item_payload["request_id"] == "learning-request-1"
    assert dispatch.entrypoint_asset_id == "learning.entrypoints.analyst"
    assert dispatch.skill_asset_ids == ("learning.skills.analyst_core",)


def test_operator_supplied_learning_target_stage_is_refused() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.admitted_state(plan, fingerprint)
    payload = {
        **lad_learning.learning_payload(),
        "target_stage_kind_id": "librarian",
    }

    with pytest.raises(OperatorInputError) as error:
        build_enqueue_work(
            state,
            OperatorEnqueueInput(
                input_id="enqueue-targeted-learning",
                queue_family_id="learning_request",
                payload=payload,
                plan_fingerprint=fingerprint,
            ),
        )

    assert error.value.reason == "invalid_payload_schema"


def test_admission_refuses_generated_route_missing_queue_family() -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()
    generated_routes = list(plan.generated_work_routes)
    generated_routes[0] = replace(
        generated_routes[0],
        queue_family_id=QueueFamilyId("missing-learning-queue"),
    )
    corrupt_plan = replace(plan, generated_work_routes=tuple(generated_routes))

    detail = _admission_refusal_detail(corrupt_plan)

    assert detail == "selected_enqueue_route_queue_family:learning.trigger.analyst"


def test_admission_refuses_cross_route_id_ambiguity() -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()
    generated_routes = list(plan.generated_work_routes)
    generated_routes[0] = replace(generated_routes[0], id="learning_request")
    corrupt_plan = replace(plan, generated_work_routes=tuple(generated_routes))

    detail = _admission_refusal_detail(corrupt_plan)

    assert detail == "selected_enqueue_route_duplicate:learning_request"


@pytest.mark.parametrize(
    ("mutate", "detail"),
    (
        (
            lambda plan, route: replace(
                route,
                graph_node_id="missing.learning.node",
            ),
            "graph_node_missing:missing.learning.node",
        ),
        (
            lambda plan, route: replace(
                route,
                stage_kind_id=StageKindId("missing_learning_stage"),
            ),
            "selected_enqueue_route_stage_kind:learning.trigger.analyst",
        ),
        (
            lambda plan, route: replace(
                route,
                payload_schema_id=ArtifactSchemaId("missing.learning.payload"),
            ),
            "generated_work_route_payload_schema:learning.trigger.analyst",
        ),
        (
            lambda plan, route: replace(
                route,
                stage_kind_id=StageKindId("professor"),
                graph_node_id="learning.standard.professor",
            ),
            "selected_enqueue_route_stage_input:learning.trigger.analyst",
        ),
        (
            lambda plan, route: replace(
                route,
                runner_binding_id=RunnerBindingId("planning.lad.local_runner"),
            ),
            "selected_enqueue_route_stage_runner:learning.trigger.analyst",
        ),
        (
            lambda plan, route: route,
            "selected_enqueue_route_runner_stage:learning.trigger.librarian",
        ),
    ),
)
def test_admission_refuses_generated_route_structural_corruption(
    mutate,
    detail: str,
) -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()
    generated_routes = list(plan.generated_work_routes)
    route_index = (
        next(
            index
            for index, route in enumerate(generated_routes)
            if route.id == "learning.trigger.librarian"
        )
        if detail == "selected_enqueue_route_runner_stage:learning.trigger.librarian"
        else 0
    )
    generated_routes[route_index] = mutate(plan, generated_routes[route_index])
    runner_bindings = (
        tuple(
            replace(
                runner,
                stage_kind_ids=tuple(
                    stage_id
                    for stage_id in runner.stage_kind_ids
                    if stage_id != StageKindId("librarian")
                ),
            )
            if runner.id == RunnerBindingId("learning.standard.local_runner")
            else runner
            for runner in plan.runner_bindings
        )
        if detail == "selected_enqueue_route_runner_stage:learning.trigger.librarian"
        else plan.runner_bindings
    )
    corrupt_plan = replace(
        plan,
        generated_work_routes=tuple(generated_routes),
        runner_bindings=runner_bindings,
    )

    assert _admission_refusal_detail(corrupt_plan) == detail


@pytest.mark.parametrize(
    ("mutate", "detail"),
    (
        (
            lambda policies: [
                replace(policy, coexist_partition_ids=(PartitionId("learning"),))
                if policy.partition_id == PartitionId("learning")
                else policy
                for policy in policies
            ],
            "concurrency_policy_self_coexist:learning.standard",
        ),
        (
            lambda policies: [
                replace(
                    policy,
                    coexist_partition_ids=(
                        PartitionId("planning"),
                        PartitionId("planning"),
                    ),
                )
                if policy.partition_id == PartitionId("learning")
                else policy
                for policy in policies
            ],
            "concurrency_policy_coexist_partition:learning.standard",
        ),
        (
            lambda policies: [
                replace(policy, coexist_partition_ids=())
                if policy.partition_id == PartitionId("planning")
                else policy
                for policy in policies
            ],
            "concurrency_policy_asymmetric_coexist:learning.standard",
        ),
    ),
)
def test_admission_refuses_concurrency_policy_shape_corruption(
    mutate,
    detail: str,
) -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()
    corrupt_plan = replace(
        plan,
        concurrency_policies=tuple(mutate(list(plan.concurrency_policies))),
    )

    assert _admission_refusal_detail(corrupt_plan) == detail


@pytest.mark.parametrize(
    ("artifact_schema_id", "extra_source_stage_id"),
    (
        (lad_learning.LEARNING_PROFESSOR_NOTES_SCHEMA_ID, None),
        (lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID, "analyst"),
    ),
)
def test_admission_refuses_learning_route_artifact_schema_contract_drift(
    artifact_schema_id: str,
    extra_source_stage_id: str | None,
) -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()
    stage_kinds = (
        tuple(
            replace(
                stage,
                artifact_schema_ids=(
                    *stage.artifact_schema_ids,
                    ArtifactSchemaId(artifact_schema_id),
                ),
            )
            if str(stage.id) == extra_source_stage_id
            else stage
            for stage in plan.stage_kinds
        )
        if extra_source_stage_id is not None
        else plan.stage_kinds
    )
    terminal_actions = tuple(
        replace(
            action,
            artifact_schema_id=ArtifactSchemaId(artifact_schema_id),
        )
        if str(action.id) == "learning.route_analyst_complete"
        else action
        for action in plan.terminal_actions
    )
    corrupt_plan = replace(
        plan,
        stage_kinds=stage_kinds,
        terminal_actions=terminal_actions,
    )

    assert _admission_refusal_detail(corrupt_plan) == (
        "terminal_route_artifact_schema:learning.route_analyst_complete"
    )


@pytest.mark.parametrize(
    ("action_id", "detail"),
    (
        (
            "learning.route_analyst_complete",
            "terminal_route_artifact_schema:learning.route_analyst_complete",
        ),
        (
            "learning.close_analyst_noop",
            "terminal_action_artifact_schema:learning.close_analyst_noop",
        ),
    ),
)
def test_admission_refuses_learning_stage_result_terminal_reselection(
    action_id: str,
    detail: str,
) -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()
    terminal_actions = tuple(
        replace(
            action,
            artifact_schema_id=ArtifactSchemaId(
                lad_learning.LEARNING_STAGE_RESULT_SCHEMA_ID
            ),
        )
        if str(action.id) == action_id
        else action
        for action in plan.terminal_actions
    )
    corrupt_plan = replace(plan, terminal_actions=terminal_actions)

    assert _admission_refusal_detail(corrupt_plan) == detail


@pytest.mark.parametrize(
    ("mutate", "detail"),
    (
        (
            lambda plan, actions, stages, runners: (
                tuple(
                    replace(action, target_graph_node_id=None)
                    if str(action.id) == "learning.route_analyst_complete"
                    else action
                    for action in actions
                ),
                stages,
                runners,
            ),
            "terminal_route_authority:learning.route_analyst_complete",
        ),
        (
            lambda plan, actions, stages, runners: (
                tuple(
                    replace(
                        action,
                        target_graph_node_id="learning.standard.analyst",
                    )
                    if str(action.id) == "learning.route_analyst_complete"
                    else action
                    for action in actions
                ),
                stages,
                runners,
            ),
            "terminal_route_graph_node_stage:learning.route_analyst_complete",
        ),
        (
            lambda plan, actions, stages, runners: (
                actions,
                tuple(
                    replace(
                        stage,
                        output_queue_family_ids=tuple(
                            queue_id
                            for queue_id in stage.output_queue_family_ids
                            if queue_id != QueueFamilyId("stage_result")
                        ),
                    )
                    if str(stage.id) == "analyst"
                    else stage
                    for stage in stages
                ),
                runners,
            ),
            "terminal_route_artifact_schema:learning.route_analyst_complete",
        ),
        (
            lambda plan, actions, stages, runners: (
                actions,
                tuple(
                    replace(
                        stage,
                        input_queue_family_ids=tuple(
                            queue_id
                            for queue_id in stage.input_queue_family_ids
                            if queue_id != QueueFamilyId("stage_result")
                        ),
                    )
                    if str(stage.id) == "professor"
                    else stage
                    for stage in stages
                ),
                runners,
            ),
            "terminal_route_artifact_schema:learning.route_analyst_complete",
        ),
        (
            lambda plan, actions, stages, runners: (
                tuple(
                    replace(
                        action,
                        runner_binding_id=RunnerBindingId("planning.lad.local_runner"),
                    )
                    if str(action.id) == "learning.route_analyst_complete"
                    else action
                    for action in actions
                ),
                stages,
                runners,
            ),
            "terminal_route_artifact_schema:learning.route_analyst_complete",
        ),
        (
            lambda plan, actions, stages, runners: (
                actions,
                stages,
                tuple(
                    replace(
                        runner,
                        stage_kind_ids=tuple(
                            stage_id
                            for stage_id in runner.stage_kind_ids
                            if stage_id != StageKindId("professor")
                        ),
                    )
                    if str(runner.id) == "learning.standard.local_runner"
                    else runner
                    for runner in runners
                ),
            ),
            "terminal_route_artifact_schema:learning.route_analyst_complete",
        ),
    ),
)
def test_admission_refuses_learning_static_route_authority_drift(
    mutate,
    detail: str,
) -> None:
    plan, _fingerprint = lad_learning.compile_lad_learning()
    terminal_actions, stage_kinds, runner_bindings = mutate(
        plan,
        plan.terminal_actions,
        plan.stage_kinds,
        plan.runner_bindings,
    )
    corrupt_plan = replace(
        plan,
        terminal_actions=terminal_actions,
        stage_kinds=stage_kinds,
        runner_bindings=runner_bindings,
    )

    assert _admission_refusal_detail(corrupt_plan) == detail


def test_analyst_completion_routes_professor_by_selected_terminal_action() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.ready_learning_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-analyst",
        input_id="claim-analyst",
    )

    state = lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-analyst",
        marker="ANALYST_COMPLETE",
        artifact=lad_learning.artifact_payload(
            lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
        ),
        input_id="observe-analyst-complete",
        work_item_id="work-professor",
        activation_id="activation-professor",
    )

    state = lad_learning.claim(
        state,
        activation_id="activation-professor",
        run_id="run-professor",
        input_id="claim-professor",
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-professor",
    )
    assert dispatch.graph_node_id == "learning.standard.professor"
    assert dispatch.stage_kind_id == "professor"


def test_learning_claim_refuses_unselected_target_authority() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.ready_learning_state(plan, fingerprint)
    activation = state.activations["activation-learning-request"]
    state = replace(
        state,
        activations={
            **state.activations,
            "activation-learning-request": replace(
                activation,
                stage_kind_id=StageKindId("missing-learning-stage"),
            ),
        },
    )

    decision = decide(
        state,
        ClaimWork(
            "claim-corrupt-learning",
            activation_id="activation-learning-request",
        ),
        lad_learning.context(
            "claim-corrupt-learning",
            run_id="run-corrupt-learning",
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    after = apply(state, decision)
    assert "run-corrupt-learning" not in after.runs
