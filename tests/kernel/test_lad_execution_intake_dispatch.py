from __future__ import annotations

from typing import cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import QueueFamilyId, SelectedCompiledPlan
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    SelectDefaultPlan,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.testing import (
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
)
from millrace.workflows import lad_execution

Source = dict[str, object]
Record = dict[str, object]
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _compile(source: Source) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _apply_accepted(
    state: RuntimeState,
    transition_input,
    *,
    suffix: str,
) -> RuntimeState:
    decision = decide(
        state,
        transition_input,
        deterministic_context(
            transition_id=f"transition-{suffix}",
            work_item_id=f"work-{suffix}",
            activation_id=f"activation-{suffix}",
            run_id=f"run-{suffix}",
            claim_id=f"claim-{suffix}",
            fencing_token=f"fence-{suffix}",
        ),
    )
    assert decision.accepted is True
    return apply(state, decision)


def _admit_select_enqueue_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    enqueue_payload: dict[str, object] | None = None,
) -> RuntimeState:
    state = empty_runtime_state()
    state = _apply_accepted(state, InitializeWorkspace("init"), suffix="init")
    state = _apply_accepted(
        state,
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        suffix="admit",
    )
    state = _apply_accepted(
        state,
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
        suffix="select",
    )
    state = _apply_accepted(
        state,
        EnqueueWork(
            "enqueue-task",
            queue_family_id=QueueFamilyId("task"),
            payload=enqueue_payload
            or {
                "task_id": "task-1",
                "body": "Implement the execution-plane work.",
            },
        ),
        suffix="builder",
    )
    return _apply_accepted(
        state,
        ClaimWork("claim-builder", activation_id="activation-builder"),
        suffix="builder-claim",
    )


def test_lad_execution_task_enqueue_claim_dispatches_selected_builder() -> None:
    plan, fingerprint = _compile(lad_execution.workflow_source())
    state = _admit_select_enqueue_claim(plan, fingerprint)

    run = state.runs["run-builder-claim"]
    activation = state.activations[run.activation_id]
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id=run.run_ref.run_id,
    )

    assert str(run.stage_kind_id) == "lad_builder"
    assert activation.graph_node_id == "execution.lad.builder.start"
    assert dispatch.stage_kind_id == "lad_builder"
    assert dispatch.graph_node_id == "execution.lad.builder.start"
    assert dispatch.runner_binding_id == "execution.lad.local_runner"
    assert dispatch.work_item_payload["task_id"] == "task-1"

    context = dispatch.governance_context
    assert context["workflow"] == {
        "id": "execution.lad",
        "version": "0.1",
        "name": "LAD Execution",
    }
    assert context["queue_family_id"] == "task"
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


def test_lad_integrator_task_enqueue_claim_dispatches_selected_builder() -> None:
    plan, fingerprint = _compile(lad_execution.integrator_workflow_source())
    state = _admit_select_enqueue_claim(plan, fingerprint)

    run = state.runs["run-builder-claim"]
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id=run.run_ref.run_id,
    )

    assert str(run.stage_kind_id) == "lad_builder"
    assert dispatch.governance_context["workflow"] == {
        "id": "execution.lad_integrator",
        "version": "0.1",
        "name": "LAD Execution With Integrator",
    }
    assert dispatch.graph_node_id == "execution.lad_integrator.builder.start"


@pytest.mark.parametrize(
    ("field_name", "field_value", "reason"),
    [
        ("grant_status", "denied", "capability_denied"),
        ("grant_status", "approval_pending", "capability_approval_pending"),
        ("support_status", "unsupported", "capability_unsupported"),
    ],
)
def test_lad_capability_gate_blocks_claims_from_selected_plan(
    field_name: str,
    field_value: str,
    reason: str,
) -> None:
    source = lad_execution.workflow_source()
    capability = _records(source, "capabilities")[0]
    capability[field_name] = field_value
    plan, fingerprint = _compile(source)

    state = empty_runtime_state()
    for transition_input, suffix in (
        (InitializeWorkspace("init"), "init"),
        (
            AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
            "admit",
        ),
        (SelectDefaultPlan("select", authority_fingerprint=fingerprint), "select"),
        (
            EnqueueWork(
                "enqueue-task",
                queue_family_id=QueueFamilyId("task"),
                payload={
                    "task_id": "task-1",
                    "body": "Implement the execution-plane work.",
                },
            ),
            "builder",
        ),
    ):
        state = _apply_accepted(state, transition_input, suffix=suffix)

    claim = decide(
        state,
        ClaimWork("claim-builder", activation_id="activation-builder"),
        deterministic_context(
            transition_id="transition-builder-claim",
            run_id="run-builder-claim",
            claim_id="claim-builder-claim",
        ),
    )

    assert claim.accepted is False
    assert claim.refusal is not None
    assert claim.refusal.reason == reason
    after = apply(state, claim)
    assert after.runs == {}
    assert after.activations["activation-builder"].claimed_by_run_id is None
