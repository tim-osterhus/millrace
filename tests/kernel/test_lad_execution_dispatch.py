from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    GraphId,
    InitializeWorkspace,
    QueueFamilyId,
    RunnerBindingId,
    SelectDefaultPlan,
    SelectedCompiledPlan,
    StageKindId,
)
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.operator import (
    OperatorEnqueueInput,
    OperatorInputError,
    build_enqueue_work,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
)
from millrace.workflows import lad_execution

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)

Source = dict[str, object]
Record = dict[str, object]


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _compile(source: Mapping[str, object]) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _admitted_state(plan: SelectedCompiledPlan, fingerprint: str):
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init-lad"),
        AdmitPlan(
            "admit-lad",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan("select-lad", authority_fingerprint=fingerprint),
    ):
        state = apply(
            state,
            decide(
                state,
                transition_input,
                deterministic_context(
                    transition_id=f"transition-{transition_input.input_id}",
                ),
            ),
        )
    return state


def _task_payload() -> Mapping[str, AuthorityValue]:
    return {
        "task_id": "task-1",
        "body": "Implement the execution proof.",
    }


def test_lad_task_intake_claims_and_dispatches_selected_builder_context() -> None:
    plan, fingerprint = _compile(lad_execution.workflow_source())
    state = _admitted_state(plan, fingerprint)

    enqueue = build_enqueue_work(
        state,
        OperatorEnqueueInput(
            input_id="enqueue-lad-task",
            queue_family_id="task",
            payload=_task_payload(),
            plan_fingerprint=fingerprint,
        ),
    )
    assert isinstance(enqueue, EnqueueWork)

    state = apply(
        state,
        decide(
            state,
            enqueue,
            deterministic_context(
                transition_id="transition-enqueue-lad-task",
                work_item_id="work-lad-task",
                activation_id="activation-lad-builder",
            ),
        ),
    )
    activation = state.activations["activation-lad-builder"]
    assert activation.queue_family_id == QueueFamilyId("task")
    assert activation.graph_node_id == "execution.lad.builder.start"
    assert str(activation.stage_kind_id) == "lad_builder"

    claim_decision = decide(
        state,
        ClaimWork("claim-lad-builder", activation_id="activation-lad-builder"),
        deterministic_context(
            transition_id="transition-claim-lad-builder",
            run_id="run-lad-builder",
            claim_id="claim-lad-builder",
            fencing_token="fence-lad-builder",
        ),
    )
    assert claim_decision.accepted is True
    state = apply(state, claim_decision)

    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-lad-builder",
    )
    assert dispatch.plan_id == "execution.lad:0.1"
    assert dispatch.workflow_id == "execution.lad"
    assert dispatch.workflow_version == "0.1"
    assert dispatch.graph_id == "execution.lad.graph"
    assert dispatch.queue_family_id == "task"
    assert dispatch.external_enqueue_route_id == "execution.lad.task"
    assert dispatch.entrypoint_asset_id == "execution.entrypoints.lad_builder"
    assert dispatch.skill_asset_ids == ("execution.skills.builder_core",)
    assert dispatch.artifact_schema_ids == (
        "execution.artifacts.task",
        "execution.artifacts.stage_result",
        "execution.artifacts.report",
    )
    context = dispatch.governance_context
    assert context["workflow"] == {
        "id": "execution.lad",
        "version": "0.1",
        "name": "LAD Execution",
    }
    assert context["queue_family_id"] == "task"
    assert context["external_enqueue_route_id"] == "execution.lad.task"
    assert context["graph_node_id"] == "execution.lad.builder.start"
    assert context["stage_kind_id"] == "lad_builder"
    assert context["runner_binding_id"] == "execution.lad.local_runner"
    assert context["stage_assets"] == (
        {
            "id": "execution.entrypoints.lad_builder",
            "kind": "prompt",
            "display_name": "LAD Builder entrypoint",
        },
        {
            "id": "execution.skills.builder_core",
            "kind": "skill",
            "display_name": "LAD Builder core skill",
        },
    )
    assert context["artifact_schema_ids"] == (
        "execution.artifacts.task",
        "execution.artifacts.stage_result",
        "execution.artifacts.report",
    )
    assert context["artifact_schemas"] == (
        {
            "id": "execution.artifacts.task",
            "display_name": "Task",
        },
        {
            "id": "execution.artifacts.stage_result",
            "display_name": "Stage result",
        },
        {
            "id": "execution.artifacts.report",
            "display_name": "Report",
        },
    )
    assert context["capabilities"] == (
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
            "approval_policy_id": None,
        },
    )
    assert {option["marker"] for option in dispatch.terminal_options} == {
        "BUILDER_COMPLETE",
        "BLOCKED",
        "RUNTIME_FAILURE",
        "RUNTIME_FAILURE_ESCALATE",
    }


def test_lad_dispatch_graph_id_comes_from_selected_graph_authority() -> None:
    plan, _fingerprint = _compile(lad_execution.workflow_source())
    selected_graph = plan.graphs[0]
    plan = replace(
        plan,
        graphs=(replace(selected_graph, id=GraphId("custom.execution.graph")),),
    )
    fingerprint = authority_fingerprint(plan)
    state = _admitted_state(plan, fingerprint)
    state = apply(
        state,
        decide(
            state,
            build_enqueue_work(
                state,
                OperatorEnqueueInput(
                    input_id="enqueue-custom-graph",
                    queue_family_id="task",
                    payload=_task_payload(),
                ),
            ),
            deterministic_context(
                transition_id="transition-enqueue-custom-graph",
                work_item_id="work-custom-graph",
                activation_id="activation-custom-graph",
            ),
        ),
    )
    state = apply(
        state,
        decide(
            state,
            ClaimWork("claim-custom-graph", activation_id="activation-custom-graph"),
            deterministic_context(
                transition_id="transition-claim-custom-graph",
                run_id="run-custom-graph",
                claim_id="claim-custom-graph",
                fencing_token="fence-custom-graph",
            ),
        ),
    )

    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-custom-graph",
    )

    assert dispatch.workflow_id == "execution.lad"
    assert dispatch.graph_node_id == "execution.lad.builder.start"
    assert dispatch.graph_id == "custom.execution.graph"


def test_corrupt_admitted_graph_authority_refuses_claim_without_dispatch() -> None:
    plan, fingerprint = _compile(lad_execution.workflow_source())
    state = _admitted_state(plan, fingerprint)
    state = apply(
        state,
        decide(
            state,
            build_enqueue_work(
                state,
                OperatorEnqueueInput(
                    input_id="enqueue-corrupt-graph",
                    queue_family_id="task",
                    payload=_task_payload(),
                ),
            ),
            deterministic_context(
                transition_id="transition-enqueue-corrupt-graph",
                work_item_id="work-corrupt-graph",
                activation_id="activation-corrupt-graph",
            ),
        ),
    )
    graph = plan.graphs[0]
    corrupt_plan = replace(
        plan,
        graphs=(
            replace(
                graph,
                node_ids=tuple(
                    node_id
                    for node_id in graph.node_ids
                    if node_id != "execution.lad.builder.start"
                ),
            ),
        ),
    )
    state = replace(
        state,
        admitted_plans={
            fingerprint: replace(
                state.admitted_plans[fingerprint],
                selected_plan=corrupt_plan,
            )
        },
    )

    claim_decision = decide(
        state,
        ClaimWork("claim-corrupt-graph", activation_id="activation-corrupt-graph"),
        deterministic_context(
            transition_id="transition-claim-corrupt-graph",
            run_id="run-corrupt-graph",
            claim_id="claim-corrupt-graph",
            fencing_token="fence-corrupt-graph",
        ),
    )

    assert claim_decision.accepted is False
    assert claim_decision.refusal is not None
    assert claim_decision.refusal.reason == "unsupported_selected_authority"
    assert claim_decision.refusal.detail == (
        "graph_node_missing:execution.lad.builder.start"
    )
    assert apply(state, claim_decision).runs == state.runs


@pytest.mark.parametrize(
    ("activation_updates", "detail"),
    (
        (
            {"graph_node_id": "execution.lad.missing.start"},
            "activation_graph_node_missing:execution.lad.missing.start",
        ),
        (
            {"graph_node_id": "execution.lad.checker.start"},
            "activation_route_target:activation-corrupt-activation",
        ),
        (
            {"stage_kind_id": StageKindId("lad_checker")},
            "activation_route_target:activation-corrupt-activation",
        ),
        (
            {"runner_binding_id": RunnerBindingId("execution.lad.other_runner")},
            "activation_route_target:activation-corrupt-activation",
        ),
        (
            {"queue_family_id": QueueFamilyId("incident")},
            "activation_queue_family:activation-corrupt-activation",
        ),
    ),
)
def test_corrupt_persisted_activation_authority_refuses_claim_before_run(
    activation_updates: dict[str, object],
    detail: str,
) -> None:
    plan, fingerprint = _compile(lad_execution.workflow_source())
    state = _admitted_state(plan, fingerprint)
    state = apply(
        state,
        decide(
            state,
            build_enqueue_work(
                state,
                OperatorEnqueueInput(
                    input_id="enqueue-corrupt-activation",
                    queue_family_id="task",
                    payload=_task_payload(),
                ),
            ),
            deterministic_context(
                transition_id="transition-enqueue-corrupt-activation",
                work_item_id="work-corrupt-activation",
                activation_id="activation-corrupt-activation",
            ),
        ),
    )
    activation = state.activations["activation-corrupt-activation"]
    state = replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: replace(
                activation,
                **activation_updates,
            ),
        },
    )

    claim_decision = decide(
        state,
        ClaimWork(
            "claim-corrupt-activation",
            activation_id="activation-corrupt-activation",
        ),
        deterministic_context(
            transition_id="transition-claim-corrupt-activation",
            run_id="run-corrupt-activation",
            claim_id="claim-corrupt-activation",
            fencing_token="fence-corrupt-activation",
        ),
    )

    assert claim_decision.accepted is False
    assert claim_decision.refusal is not None
    assert claim_decision.refusal.reason == "unsupported_selected_authority"
    assert claim_decision.refusal.detail == detail
    assert apply(state, claim_decision).runs == state.runs


def test_lad_integrator_first_claim_still_dispatches_builder() -> None:
    plan, fingerprint = _compile(lad_execution.integrator_workflow_source())
    state = _admitted_state(plan, fingerprint)
    enqueue = build_enqueue_work(
        state,
        OperatorEnqueueInput(
            input_id="enqueue-integrator-task",
            queue_family_id="task",
            payload=_task_payload(),
        ),
    )
    state = apply(
        state,
        decide(
            state,
            enqueue,
            deterministic_context(
                transition_id="transition-enqueue-integrator-task",
                work_item_id="work-integrator-task",
                activation_id="activation-integrator-builder",
            ),
        ),
    )
    state = apply(
        state,
        decide(
            state,
            ClaimWork(
                "claim-integrator-builder",
                activation_id="activation-integrator-builder",
            ),
            deterministic_context(
                transition_id="transition-claim-integrator-builder",
                run_id="run-integrator-builder",
                claim_id="claim-integrator-builder",
                fencing_token="fence-integrator-builder",
            ),
        ),
    )

    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-integrator-builder",
    )

    assert dispatch.stage_kind_id == "lad_builder"
    assert dispatch.graph_node_id == "execution.lad_integrator.builder.start"
    assert dispatch.workflow_id == "execution.lad_integrator"
    assert dispatch.graph_id == "execution.lad_integrator.graph"
    assert dispatch.governance_context["workflow"] == {
        "id": "execution.lad_integrator",
        "version": "0.1",
        "name": "LAD Execution With Integrator",
    }
    assert dispatch.governance_context["downstream_graph_node_ids"] == (
        "execution.lad_integrator.integrator.start",
        "execution.lad_integrator.checker.start",
        "execution.lad_integrator.fixer.start",
        "execution.lad_integrator.doublechecker.start",
        "execution.lad_integrator.updater.start",
        "execution.lad_integrator.troubleshooter.start",
        "execution.lad_integrator.consultant.start",
    )


def test_lad_runner_invoke_capability_must_be_granted_supported_and_ready() -> None:
    for field_name, field_value, reason in (
        ("grant_status", "denied", "capability_denied"),
        ("grant_status", "approval_pending", "capability_approval_pending"),
        ("support_status", "unsupported", "capability_unsupported"),
    ):
        source = lad_execution.workflow_source()
        capability = _records(source, "capabilities")[0]
        capability[field_name] = field_value
        plan, fingerprint = _compile(source)
        state = _admitted_state(plan, fingerprint)
        state = apply(
            state,
            decide(
                state,
                build_enqueue_work(
                    state,
                    OperatorEnqueueInput(
                        input_id=f"enqueue-{field_value}",
                        queue_family_id="task",
                        payload=_task_payload(),
                    ),
                ),
                deterministic_context(
                    transition_id=f"transition-enqueue-{field_value}",
                    work_item_id=f"work-{field_value}",
                    activation_id=f"activation-{field_value}",
                ),
            ),
        )

        claim_decision = decide(
            state,
            ClaimWork(
                f"claim-{field_value}",
                activation_id=f"activation-{field_value}",
            ),
            deterministic_context(
                transition_id=f"transition-claim-{field_value}",
                run_id=f"run-{field_value}",
                claim_id=f"claim-{field_value}",
                fencing_token=f"fence-{field_value}",
            ),
        )

        assert claim_decision.accepted is False
        assert claim_decision.refusal is not None
        assert claim_decision.refusal.reason == reason
        assert claim_decision.governance_events[0].refusal_reason == reason
        assert claim_decision.trace_records[0].refusal_reason == reason
        assert apply(state, claim_decision).runs == state.runs


def test_missing_selected_stage_asset_refuses_claim_without_dispatch() -> None:
    plan, _fingerprint = _compile(lad_execution.workflow_source())
    corrupt_plan = replace(plan, assets=())
    corrupt_fingerprint = authority_fingerprint(corrupt_plan)
    state = _admitted_state(corrupt_plan, corrupt_fingerprint)
    state = apply(
        state,
        decide(
            state,
            build_enqueue_work(
                state,
                OperatorEnqueueInput(
                    input_id="enqueue-missing-asset",
                    queue_family_id="task",
                    payload=_task_payload(),
                ),
            ),
            deterministic_context(
                transition_id="transition-enqueue-missing-asset",
                work_item_id="work-missing-asset",
                activation_id="activation-missing-asset",
            ),
        ),
    )

    claim_decision = decide(
        state,
        ClaimWork("claim-missing-asset", activation_id="activation-missing-asset"),
        deterministic_context(
            transition_id="transition-claim-missing-asset",
            run_id="run-missing-asset",
            claim_id="claim-missing-asset",
            fencing_token="fence-missing-asset",
        ),
    )

    assert claim_decision.accepted is False
    assert claim_decision.refusal is not None
    assert claim_decision.refusal.reason == "missing_selected_asset"
    assert apply(state, claim_decision).runs == state.runs


@pytest.mark.parametrize(
    ("field_values", "detail"),
    (
        ({"capability_kind": "shell.run"}, "capability_kind:capability.runner.invoke"),
        (
            {"support_status": "maybe"},
            "capability_support_status:capability.runner.invoke",
        ),
        (
            {"grant_status": "maybe"},
            "capability_grant_status:capability.runner.invoke",
        ),
        (
            {"approval_policy_id": "policy.future"},
            "capability_approval_policy:capability.runner.invoke",
        ),
    ),
)
def test_corrupt_admitted_capability_authority_refuses_claim_without_dispatch(
    field_values: dict[str, object],
    detail: str,
) -> None:
    plan, fingerprint = _compile(lad_execution.workflow_source())
    state = _admitted_state(plan, fingerprint)
    state = apply(
        state,
        decide(
            state,
            build_enqueue_work(
                state,
                OperatorEnqueueInput(
                    input_id="enqueue-corrupt-capability",
                    queue_family_id="task",
                    payload=_task_payload(),
                ),
            ),
            deterministic_context(
                transition_id="transition-enqueue-corrupt-capability",
                work_item_id="work-corrupt-capability",
                activation_id="activation-corrupt-capability",
            ),
        ),
    )
    capability = plan.capabilities[0]
    corrupt_plan = replace(
        plan,
        capabilities=(
            replace(
                cast(Any, capability),
                **field_values,
            ),
        ),
    )
    state = replace(
        state,
        admitted_plans={
            fingerprint: replace(
                state.admitted_plans[fingerprint],
                selected_plan=corrupt_plan,
            )
        },
    )

    claim_decision = decide(
        state,
        ClaimWork(
            "claim-corrupt-capability",
            activation_id="activation-corrupt-capability",
        ),
        deterministic_context(
            transition_id="transition-claim-corrupt-capability",
            run_id="run-corrupt-capability",
            claim_id="claim-corrupt-capability",
            fencing_token="fence-corrupt-capability",
        ),
    )

    assert claim_decision.accepted is False
    assert claim_decision.refusal is not None
    assert claim_decision.refusal.reason == "unsupported_selected_authority"
    assert claim_decision.refusal.detail == detail
    assert apply(state, claim_decision).runs == state.runs


def test_capability_payload_is_rejected_by_selected_task_schema() -> None:
    plan, fingerprint = _compile(lad_execution.workflow_source())
    state = _admitted_state(plan, fingerprint)

    with pytest.raises(OperatorInputError) as exc_info:
        build_enqueue_work(
            state,
            OperatorEnqueueInput(
                input_id="enqueue-runner-self-grant",
                queue_family_id="task",
                payload={
                    **_task_payload(),
                    "capabilities": {
                        "capability.shell.run": {
                            "kind": "shell.run",
                            "grant_status": "granted",
                            "supported": True,
                        }
                    },
                },
            ),
        )

    assert exc_info.value.reason == "invalid_payload_schema"


def test_work_payload_does_not_widen_dispatch_capabilities() -> None:
    plan, fingerprint = _compile(lad_execution.workflow_source())
    state = _admitted_state(plan, fingerprint)
    enqueue = build_enqueue_work(
        state,
        OperatorEnqueueInput(
            input_id="enqueue-valid-task",
            queue_family_id="task",
            payload=_task_payload(),
        ),
    )
    state = apply(
        state,
        decide(
            state,
            enqueue,
            deterministic_context(
                transition_id="transition-enqueue-valid-task",
                work_item_id="work-valid-task",
                activation_id="activation-valid-task",
            ),
        ),
    )
    state = apply(
        state,
        decide(
            state,
            ClaimWork("claim-valid-task", activation_id="activation-valid-task"),
            deterministic_context(
                transition_id="transition-claim-valid-task",
                run_id="run-valid-task",
                claim_id="claim-valid-task",
                fencing_token="fence-valid-task",
            ),
        ),
    )

    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-valid-task",
    )

    assert {
        capability["id"] for capability in dispatch.governance_context["capabilities"]
    } == {"capability.runner.invoke"}
