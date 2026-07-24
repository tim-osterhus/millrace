from __future__ import annotations

from collections.abc import Mapping

import pytest

from kernel.simple_loop_scenarios import (
    bootstrap_to_manager_claim,
    bootstrap_to_manager_ready,
)
from millrace.contracts import (
    ActionId,
    AdmitPlan,
    ArtifactSchemaId,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    QueueFamilyId,
    SelectDefaultPlan,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.operator import operator_status
from millrace.testing import (
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
)
from support.simple_loop import (
    apply_accepted_input,
    compile_simple_loop,
    mutation_kinds,
    runner_observation,
    simple_loop_context,
    work_packet_payload,
    work_prompt_payload,
)

FORBIDDEN_PROGRESS_MUTATIONS = {
    "mutation.record_runner_observation",
    "mutation.record_artifact",
    "mutation.create_work_item",
    "mutation.create_activation",
    "mutation.route_activation",
    "mutation.close_work_item",
    "mutation.set_pause",
    "mutation.set_quarantine",
}

PACKET_READY_ACTION_ID = ActionId("simple_loop.manager.packet_ready")


@pytest.mark.parametrize(
    "payload",
    (
        {"body": "Literal requirement."},
        {"prompt_id": "prompt-1"},
        {"prompt_id": "", "body": "Literal requirement."},
        {"prompt_id": "prompt-1", "body": ""},
    ),
)
def test_external_work_prompt_refuses_missing_or_blank_required_context(
    payload: Mapping[str, object],
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init-invalid-prompt"),
        AdmitPlan(
            "admit-invalid-prompt",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(
            "select-invalid-prompt",
            authority_fingerprint=fingerprint,
        ),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}"
            ),
        )

    decision = decide(
        state,
        EnqueueWork(
            "enqueue-invalid-prompt",
            queue_family_id=QueueFamilyId("work_prompt"),
            payload=payload,
        ),
        deterministic_context(transition_id="transition-enqueue-invalid-prompt"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_enqueue_payload_schema"
    after = apply(state, decision)
    assert after.work_items == state.work_items
    assert after.activations == state.activations


def test_enqueue_creates_manager_activation_from_external_work_prompt_route() -> None:
    plan, fingerprint = compile_simple_loop()
    selected_route = plan.external_enqueue_routes[0]
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init"),
        AdmitPlan(
            "admit",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(
            "select",
            authority_fingerprint=fingerprint,
        ),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            simple_loop_context(transition_input.input_id),
        )
    selected_plan_ref = state.default_plan_ref
    assert selected_plan_ref is not None

    internal_enqueue = EnqueueWork(
        "enqueue-work-packet",
        queue_family_id=QueueFamilyId("work_packet"),
        payload={"work_packet": work_packet_payload()},
    )
    internal_decision = decide(
        state,
        internal_enqueue,
        deterministic_context(transition_id="transition-enqueue-work-packet"),
    )
    assert internal_decision.accepted is False
    assert internal_decision.refusal is not None
    assert internal_decision.refusal.reason == "queue_family_not_external"
    state = apply(state, internal_decision)
    assert state.work_items == {}
    assert state.activations == {}

    enqueue = EnqueueWork(
        "enqueue",
        queue_family_id=QueueFamilyId("work_prompt"),
        payload=work_prompt_payload(),
    )
    decision = decide(state, enqueue, simple_loop_context("enqueue"))

    assert decision.accepted is True
    after = apply(state, decision)

    assert set(after.work_items) == {"work-prompt"}
    assert set(after.activations) == {"activation-manager"}
    work_item = after.work_items["work-prompt"]
    activation = after.activations["activation-manager"]
    assert work_item.queue_family_id == QueueFamilyId("work_prompt")
    assert work_item.payload == work_prompt_payload()
    assert work_item.ref.plan_ref == selected_plan_ref
    assert work_item.lineage_id == work_item.ref.work_item_id
    assert activation.work_item_id == work_item.ref.work_item_id
    assert activation.lineage_id == work_item.ref.work_item_id
    assert activation.plan_ref == selected_plan_ref
    assert str(activation.stage_kind_id) == "simple_loop.manager"
    assert activation.graph_node_id == selected_route.graph_node_id
    assert activation.graph_node_id == "simple_loop.manager.start"
    assert str(activation.runner_binding_id) == "simple_loop.default_agent_runner"
    assert activation.stage_kind_id == selected_route.stage_kind_id
    assert activation.runner_binding_id == selected_route.runner_binding_id


def test_manager_claim_pins_selected_plan_authority_and_is_active() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_ready(plan, fingerprint)
    work_item = state.work_items["work-prompt"]
    activation = state.activations["activation-manager"]

    decision = decide(
        state,
        ClaimWork("claim-manager", activation_id="activation-manager"),
        simple_loop_context("claim-manager"),
    )

    assert decision.accepted is True
    after = apply(state, decision)
    run = after.runs["run-manager"]
    assert str(run.stage_kind_id) == "simple_loop.manager"
    assert run.run_ref.plan_ref == activation.plan_ref
    assert run.run_ref.plan_ref == work_item.ref.plan_ref
    assert run.run_ref.plan_ref.authority_fingerprint == fingerprint
    assert run.run_ref.work_item_id == work_item.ref.work_item_id
    assert after.runner_observations == {}

    status = operator_status(after)
    assert [active.run_id for active in status.active_runs] == ["run-manager"]
    active_run = status.active_runs[0]
    assert active_run.stage_kind_id == "simple_loop.manager"
    assert active_run.plan_fingerprint == fingerprint


def test_packet_ready_validates_work_packet_and_routes_worker() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    work_packet = work_packet_payload()
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-manager",
        action_id=str(PACKET_READY_ACTION_ID),
        input_id="observe-manager-packet-ready",
        artifact_payload=work_packet,
    )

    decision = decide(
        state,
        observation,
        deterministic_context(
            transition_id="transition-observe-manager-packet-ready",
            work_item_id="work-worker",
            activation_id="activation-worker",
        ),
    )

    assert decision.accepted is True
    assert {
        "mutation.record_runner_observation",
        "mutation.record_artifact",
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.route_activation",
    } <= set(mutation_kinds(decision))
    after = apply(state, decision)

    artifact = after.artifacts["transition-observe-manager-packet-ready:artifact"]
    assert artifact.schema_id == ArtifactSchemaId("simple_loop.work_packet")
    assert artifact.work_item_id == "work-prompt"
    assert artifact.payload == work_packet

    routed_work = after.work_items["work-worker"]
    routed_activation = after.activations["activation-worker"]
    assert routed_work.queue_family_id == QueueFamilyId("work_packet")
    assert routed_work.lineage_id == "work-prompt"
    assert routed_work.ref.plan_ref.authority_fingerprint == fingerprint
    assert routed_work.payload == dict(work_prompt_payload()) | {
        "work_packet": work_packet
    }
    assert routed_work.payload["work_packet"] == work_packet
    assert routed_work.payload["work_packet"]["completion_definition"] == (
        work_packet["completion_definition"]
    )
    assert routed_activation.work_item_id == "work-worker"
    assert routed_activation.lineage_id == "work-prompt"
    assert str(routed_activation.stage_kind_id) == "simple_loop.worker"
    assert routed_activation.graph_node_id == "simple_loop.worker.start"
    assert str(routed_activation.runner_binding_id) == (
        "simple_loop.default_agent_runner"
    )

    assert len(after.activation_routes) == 1
    route = after.activation_routes[0]
    assert route.action_id == PACKET_READY_ACTION_ID
    assert route.source_run_id == "run-manager"
    assert route.source_work_item_id == "work-prompt"
    assert route.target_work_item_id == "work-worker"
    assert route.target_activation_id == "activation-worker"

    worker_claimed = apply(
        after,
        decide(
            after,
            ClaimWork("claim-worker", activation_id="activation-worker"),
            deterministic_context(
                transition_id="transition-claim-worker",
                run_id="run-worker",
                claim_id="claim-worker",
                fencing_token="fence-worker",
            ),
        ),
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=worker_claimed,
        run_id="run-worker",
    )
    assert dispatch.work_item_payload == dict(work_prompt_payload()) | {
        "work_packet": work_packet
    }
    dispatch_work_packet = dispatch.work_item_payload["work_packet"]
    assert isinstance(dispatch_work_packet, Mapping)
    assert dispatch_work_packet["completion_definition"] == work_packet[
        "completion_definition"
    ]

    assert len(decision.governance_events) == 1
    assert len(decision.trace_records) == 1
    for record in (*decision.governance_events, *decision.trace_records):
        assert record.plan_fingerprint == fingerprint
        assert record.work_item_id == "work-prompt"
        assert record.run_id == "run-manager"
        assert record.action_id == PACKET_READY_ACTION_ID
        assert record.authority_source == "terminal_action"


def test_packet_ready_missing_completion_definition_refuses_without_route() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    payload = dict(work_packet_payload())
    del payload["completion_definition"]
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-manager",
        action_id=str(PACKET_READY_ACTION_ID),
        input_id="observe-manager-missing-completion",
        artifact_payload=payload,
    )

    decision = decide(
        state,
        observation,
        deterministic_context(
            transition_id="transition-observe-manager-missing-completion",
            work_item_id="work-worker",
            activation_id="activation-worker",
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    assert FORBIDDEN_PROGRESS_MUTATIONS.isdisjoint(mutation_kinds(decision))
    after = apply(state, decision)
    assert after.runner_observations == state.runner_observations
    assert after.artifacts == state.artifacts
    assert after.work_items == state.work_items
    assert after.activations == state.activations
    assert after.activation_routes == state.activation_routes
    assert after.closed_work_items == state.closed_work_items
    assert after.pause == state.pause
    assert after.quarantines == state.quarantines

    receipt = after.receipts["observe-manager-missing-completion"]
    assert receipt.accepted is False
    assert receipt.refusal_reason == "invalid_artifact_payload"
    assert len(decision.governance_events) == 1
    assert len(decision.trace_records) == 1
    for record in (*decision.governance_events, *decision.trace_records):
        assert record.plan_fingerprint == fingerprint
        assert record.work_item_id == "work-prompt"
        assert record.run_id == "run-manager"
        assert record.action_id == PACKET_READY_ACTION_ID
        assert record.authority_source == "terminal_action"
        assert record.refusal_reason == "invalid_artifact_payload"
