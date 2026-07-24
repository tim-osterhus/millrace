from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from millrace.contracts import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    QueueFamilyId,
    RuntimeState,
    SelectDefaultPlan,
    StageKindId,
)
from millrace.contracts.compiled_plan import AuthorityValue, SelectedCompiledPlan
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.operator import (
    OperatorEnqueueInput,
    OperatorInputError,
    build_enqueue_work,
)
from millrace.testing import fake_runner_dispatch_envelope_for_run
from support.lad_planning import (
    apply_runner_observation,
    bootstrap_route_ready,
    claim_activation,
    compile_lad_planning,
    planning_context,
)


def _admitted_state(plan: SelectedCompiledPlan, fingerprint: str) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init-planning"),
        AdmitPlan(
            "admit-planning",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan("select-planning", authority_fingerprint=fingerprint),
    ):
        state = apply(
            state,
            decide(
                state,
                transition_input,
                planning_context(transition_input.input_id),
            ),
        )
    return state


def _payload(kind: str) -> Mapping[str, AuthorityValue]:
    return {
        "title": f"{kind} input",
        "body": f"Shape the selected {kind} through Planning.",
        "root_source": {
            "kind": kind,
            "source_id": f"{kind}-source-1",
        },
    }


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


@pytest.mark.parametrize(
    ("queue_family_id", "activation_id", "run_id", "stage_id", "graph_node_id"),
    (
        (
            "spec",
            "activation-planner",
            "run-planner",
            "lad_planner",
            "planning.lad.planner.start",
        ),
        (
            "probe",
            "activation-recon",
            "run-recon",
            "recon",
            "planning.lad.recon.start",
        ),
        (
            "incident",
            "activation-auditor",
            "run-auditor",
            "lad_auditor",
            "planning.lad.auditor.start",
        ),
    ),
)
def test_lad_planning_selected_intake_creates_dispatch_context(
    queue_family_id: str,
    activation_id: str,
    run_id: str,
    stage_id: str,
    graph_node_id: str,
) -> None:
    plan, fingerprint = compile_lad_planning()
    state = _admitted_state(plan, fingerprint)

    enqueue = build_enqueue_work(
        state,
        OperatorEnqueueInput(
            input_id=f"enqueue-{queue_family_id}",
            queue_family_id=queue_family_id,
            payload=_payload(queue_family_id),
            plan_fingerprint=fingerprint,
        ),
    )
    assert isinstance(enqueue, EnqueueWork)

    state = apply(
        state,
        decide(
            state,
            enqueue,
            planning_context(
                enqueue.input_id,
                work_item_id=f"work-{queue_family_id}",
                activation_id=activation_id,
            ),
        ),
    )

    assert enqueue.input_id in state.receipts
    work = state.work_items[f"work-{queue_family_id}"]
    activation = state.activations[activation_id]
    assert work.queue_family_id == QueueFamilyId(queue_family_id)
    assert work.payload["root_source"] == {
        "kind": queue_family_id,
        "source_id": f"{queue_family_id}-source-1",
    }
    assert activation.queue_family_id == QueueFamilyId(queue_family_id)
    assert activation.graph_node_id == graph_node_id
    assert activation.stage_kind_id == StageKindId(stage_id)

    state = apply(
        state,
        decide(
            state,
            ClaimWork(f"claim-{queue_family_id}", activation_id=activation_id),
            planning_context(
                f"claim-{queue_family_id}",
                run_id=run_id,
                claim_id=f"claim-{queue_family_id}",
                fencing_token=f"fence-{queue_family_id}",
            ),
        ),
    )

    dispatch = fake_runner_dispatch_envelope_for_run(state=state, run_id=run_id)
    assert dispatch.plan_fingerprint == fingerprint
    assert dispatch.workflow_id == "planning.lad"
    assert dispatch.graph_id == "planning.lad.graph"
    assert dispatch.queue_family_id == queue_family_id
    assert dispatch.external_enqueue_route_id == queue_family_id
    assert dispatch.graph_node_id == graph_node_id
    assert dispatch.stage_kind_id == stage_id
    assert dispatch.runner_binding_id == "planning.lad.local_runner"
    assert dispatch.work_item_payload["root_source"] == {
        "kind": queue_family_id,
        "source_id": f"{queue_family_id}-source-1",
    }
    assert "planning.artifacts.stage_result" in dispatch.artifact_schema_ids
    assert dispatch.governance_context["workflow"] == {
        "id": "planning.lad",
        "version": "0.1",
        "name": "LAD Planning",
    }
    assert dispatch.governance_context["queue_family_id"] == queue_family_id
    assert dispatch.governance_context["graph_node_id"] == graph_node_id
    assert dispatch.governance_context["stage_kind_id"] == stage_id
    assert dispatch.governance_context["capabilities"] == (
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
            "approval_policy_id": None,
        },
    )


def test_planner_manager_dispatch_preserves_exact_source_when_summary_is_lossy(
) -> None:
    plan, fingerprint = compile_lad_planning()
    source_request = {
        "title": "Preserve exact source",
        "body": "Create result.txt containing exactly: preserved literal.",
        "root_source": {"kind": "spec", "source_id": "spec-source-1"},
    }
    planning_result = {
        "artifact_kind": "planning.artifacts.stage_result",
        "summary": "Ready for decomposition without repeating the literal.",
    }
    state = bootstrap_route_ready(
        plan,
        fingerprint,
        queue_family_id="spec",
        payload=source_request,
        work_item_id="work-planner",
        activation_id="activation-planner",
    )
    state = claim_activation(
        state,
        activation_id="activation-planner",
        run_id="run-planner",
        input_id="claim-planner",
    )

    state, _decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner",
        action_id="planning.route_planner_complete",
        input_id="observe-planner",
        artifact=planning_result,
        target_work_item_id="work-manager",
        target_activation_id="activation-manager",
    )

    assert state.work_items["work-manager"].payload == {
        "planning_result": planning_result,
        "source_request": source_request,
    }
    state = claim_activation(
        state,
        activation_id="activation-manager",
        run_id="run-manager",
        input_id="claim-manager",
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-manager",
    )
    assert dispatch.work_item_payload == {
        "planning_result": planning_result,
        "source_request": source_request,
    }
    assert dispatch.work_item_payload["source_request"]["body"] == (
        "Create result.txt containing exactly: preserved literal."
    )


@pytest.mark.parametrize(
    ("queue_family_id", "expected_reason"),
    (
        ("idea", "unknown_queue_family"),
        ("task_cards", "queue_family_not_external"),
    ),
)
def test_lad_planning_operator_refuses_unselected_intake_routes(
    queue_family_id: str,
    expected_reason: str,
) -> None:
    plan, fingerprint = compile_lad_planning()
    state = _admitted_state(plan, fingerprint)

    with pytest.raises(OperatorInputError) as error:
        build_enqueue_work(
            state,
            OperatorEnqueueInput(
                input_id=f"enqueue-{queue_family_id}",
                queue_family_id=queue_family_id,
                payload=_payload(queue_family_id),
                plan_fingerprint=fingerprint,
            ),
        )

    assert error.value.reason == expected_reason


def test_lad_planning_operator_refuses_missing_external_route() -> None:
    plan, fingerprint = compile_lad_planning()
    admitted = _admitted_state(plan, fingerprint)
    admitted_plan = admitted.admitted_plans[fingerprint]
    route_less_plan = replace(
        admitted_plan.selected_plan,
        external_enqueue_routes=tuple(
            route
            for route in admitted_plan.selected_plan.external_enqueue_routes
            if route.id != "spec"
        ),
    )
    state = replace(
        admitted,
        admitted_plans={
            fingerprint: replace(
                admitted_plan,
                selected_plan=route_less_plan,
                external_enqueue_routes={
                    queue_id: route
                    for queue_id, route in admitted_plan.external_enqueue_routes.items()
                    if str(queue_id) != "spec"
                },
            )
        },
    )

    with pytest.raises(OperatorInputError) as error:
        build_enqueue_work(
            state,
            OperatorEnqueueInput(
                input_id="enqueue-spec",
                queue_family_id="spec",
                payload=_payload("spec"),
                plan_fingerprint=fingerprint,
            ),
        )

    assert error.value.reason == "missing_external_enqueue_route"


@pytest.mark.parametrize(
    ("queue_family_id", "payload"),
    (
        (
            "spec",
            {
                "title": "Spec missing root source",
                "body": "This cannot be admitted without provenance.",
            },
        ),
        (
            "probe",
            {
                "title": "Probe wrong root source",
                "body": "This cannot masquerade as another source kind.",
                "root_source": {"kind": "spec", "source_id": "source-1"},
            },
        ),
    ),
)
def test_lad_planning_operator_refuses_invalid_intake_payload_schema(
    queue_family_id: str,
    payload: Mapping[str, AuthorityValue],
) -> None:
    plan, fingerprint = compile_lad_planning()
    state = _admitted_state(plan, fingerprint)

    with pytest.raises(OperatorInputError) as error:
        build_enqueue_work(
            state,
            OperatorEnqueueInput(
                input_id=f"enqueue-invalid-{queue_family_id}",
                queue_family_id=queue_family_id,
                payload=payload,
                plan_fingerprint=fingerprint,
            ),
        )

    assert error.value.reason == "invalid_payload_schema"


@pytest.mark.parametrize(
    ("queue_family_id", "payload"),
    (
        (
            "spec",
            {
                "title": "Spec missing root source",
                "body": "This cannot be admitted without provenance.",
            },
        ),
        (
            "incident",
            {
                "title": "Incident wrong root source",
                "body": "This cannot masquerade as another source kind.",
                "root_source": {"kind": "probe", "source_id": "source-1"},
            },
        ),
    ),
)
def test_lad_planning_kernel_refuses_invalid_intake_payload_schema_without_progress(
    queue_family_id: str,
    payload: Mapping[str, AuthorityValue],
) -> None:
    plan, fingerprint = compile_lad_planning()
    state = _admitted_state(plan, fingerprint)

    transition_input = EnqueueWork(
        f"enqueue-invalid-{queue_family_id}",
        queue_family_id=QueueFamilyId(queue_family_id),
        payload=payload,
    )
    decision = decide(
        state,
        transition_input,
        planning_context(
            f"enqueue-invalid-{queue_family_id}",
            work_item_id=f"work-invalid-{queue_family_id}",
            activation_id=f"activation-invalid-{queue_family_id}",
        ),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_enqueue_payload_schema"
    _assert_no_progress_after_refusal(state, after)


def test_lad_planning_operator_refuses_wrong_plan_and_missing_default() -> None:
    plan, fingerprint = compile_lad_planning()
    state = _admitted_state(plan, fingerprint)

    with pytest.raises(OperatorInputError) as wrong_plan:
        build_enqueue_work(
            state,
            OperatorEnqueueInput(
                input_id="enqueue-wrong-plan",
                queue_family_id="spec",
                payload=_payload("spec"),
                plan_fingerprint="sha256:not-current",
            ),
        )
    assert wrong_plan.value.reason == "plan_fingerprint_mismatch"

    with pytest.raises(OperatorInputError) as missing_default:
        build_enqueue_work(
            empty_runtime_state(),
            OperatorEnqueueInput(
                input_id="enqueue-missing-default",
                queue_family_id="spec",
                payload=_payload("spec"),
            ),
        )
    assert missing_default.value.reason == "missing_default_plan"


def test_lad_planning_admit_refuses_wrong_fingerprint() -> None:
    plan, _fingerprint = compile_lad_planning()
    state = apply(
        empty_runtime_state(),
        decide(
            empty_runtime_state(),
            InitializeWorkspace("init-planning"),
            planning_context("init-planning"),
        ),
    )

    decision = decide(
        state,
        AdmitPlan(
            "admit-wrong-fingerprint",
            selected_plan=plan,
            authority_fingerprint="sha256:not-the-plan",
        ),
        planning_context("admit-wrong-fingerprint"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "plan_fingerprint_mismatch"


def test_lad_planning_claim_refuses_stale_plan_authority() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_ready(
        plan,
        fingerprint,
        queue_family_id="spec",
        work_item_id="work-spec",
        activation_id="activation-planner",
    )
    admitted = state.admitted_plans[fingerprint]
    stale_plan = replace(
        admitted.selected_plan,
        stage_kinds=tuple(
            stage
            for stage in admitted.selected_plan.stage_kinds
            if str(stage.id) != "lad_planner"
        ),
    )
    state = replace(
        state,
        admitted_plans={fingerprint: replace(admitted, selected_plan=stale_plan)},
    )

    decision = decide(
        state,
        ClaimWork("claim-stale-planner", activation_id="activation-planner"),
        planning_context(
            "claim-stale-planner",
            run_id="run-stale-planner",
            claim_id="claim-stale-planner",
            fencing_token="fence-stale-planner",
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
