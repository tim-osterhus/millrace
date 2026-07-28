from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace

import pytest

from kernel.simple_loop_scenarios import (
    bootstrap_to_gap_worker_claim,
    bootstrap_to_reviewer_claim,
    bootstrap_to_worker_claim,
)
from millrace.contracts import (
    ActionId,
    ArtifactSchemaId,
    ClaimWork,
    QueueFamilyId,
    SelectedCompiledPlan,
)
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import TransitionDecision
from millrace.kernel import apply
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.testing import (
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
)
from support.simple_loop import (
    action_by_id,
    apply_accepted_input,
    compile_simple_loop,
    detail_request_payload,
    gap_packet_payload,
    incident_report_payload,
    mutation_kinds,
    runner_observation,
    simple_loop_context,
    work_packet_payload,
    work_prompt_payload,
    work_result_payload,
)

GAPS_FOUND_ACTION_ID = ActionId("simple_loop.reviewer.gaps_found")
INCIDENT_REQUIRED_ACTION_ID = ActionId("simple_loop.reviewer.incident_required")
INSUFFICIENT_SPEC_ACTION_ID = ActionId("simple_loop.worker.insufficient_spec")
INCIDENT_TRIAGED_ACTION_ID = ActionId("simple_loop.manager.incident_triaged")
PACKET_READY_ACTION_ID = ActionId("simple_loop.manager.packet_ready")
WORK_DONE_ACTION_ID = ActionId("simple_loop.worker.work_done")

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


def _assert_no_workflow_progress(decision: TransitionDecision) -> None:
    assert FORBIDDEN_PROGRESS_MUTATIONS.isdisjoint(mutation_kinds(decision))


def _assert_no_progress_state_changes(
    after: RuntimeState,
    before: RuntimeState,
) -> None:
    assert after.runner_observations == before.runner_observations
    assert after.artifacts == before.artifacts
    assert after.work_items == before.work_items
    assert after.activations == before.activations
    assert after.activation_routes == before.activation_routes
    assert after.closed_work_items == before.closed_work_items
    assert after.pause == before.pause
    assert after.quarantines == before.quarantines


def _assert_audit_context(
    decision: TransitionDecision,
    *,
    fingerprint: str,
    work_item_id: str,
    run_id: str,
    action_id: ActionId,
    refusal_reason: str | None = None,
) -> None:
    assert len(decision.governance_events) == 1
    assert len(decision.trace_records) == 1
    for record in (*decision.governance_events, *decision.trace_records):
        assert record.plan_fingerprint == fingerprint
        assert record.work_item_id == work_item_id
        assert record.run_id == run_id
        assert record.action_id == action_id
        assert record.authority_source == "terminal_action"
        assert record.refusal_reason == refusal_reason


def _assert_route_progress(decision: TransitionDecision) -> None:
    assert decision.accepted is True
    assert {
        "mutation.record_runner_observation",
        "mutation.record_artifact",
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.route_activation",
    } <= set(mutation_kinds(decision))


def _assert_completion_definition_preserved(
    *,
    routed_payload: Mapping[str, AuthorityValue],
    original_work_packet: Mapping[str, AuthorityValue],
) -> None:
    routed_work_packet = routed_payload["work_packet"]
    assert isinstance(routed_work_packet, Mapping)
    assert (
        routed_work_packet["completion_definition"]
        == original_work_packet["completion_definition"]
    )


def _assert_source_prompt_preserved(
    routed_payload: Mapping[str, AuthorityValue],
) -> None:
    assert {
        "prompt_id": routed_payload["prompt_id"],
        "body": routed_payload["body"],
    } == work_prompt_payload()


def _revised_work_packet_payload() -> Mapping[str, AuthorityValue]:
    packet = dict(work_packet_payload())
    packet["objective"] = "Prove a revised packet returns to Worker."
    packet["completion_definition"] = (
        "Worker receives the Manager-revised packet after detail request."
    )
    return packet


def _claim_activation(
    state: RuntimeState,
    *,
    input_id: str,
    activation_id: str,
    run_id: str,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        ClaimWork(input_id, activation_id=activation_id),
        deterministic_context(
            transition_id=f"transition-{input_id}",
            run_id=run_id,
            claim_id=f"claim-{run_id}",
            fencing_token=f"fence-{run_id}",
        ),
    )


def _observe_gap_retry(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    reviewer_run_id: str,
    input_id: str,
    worker_work_id: str,
    worker_activation_id: str,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=reviewer_run_id,
            action_id=str(GAPS_FOUND_ACTION_ID),
            input_id=input_id,
            artifact_payload=gap_packet_payload(),
        ),
        deterministic_context(
            transition_id=f"transition-{input_id}",
            work_item_id=worker_work_id,
            activation_id=worker_activation_id,
        ),
    )


def _observe_gap_worker_done(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    worker_run_id: str,
    input_id: str,
    reviewer_work_id: str,
    reviewer_activation_id: str,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=worker_run_id,
            action_id=str(WORK_DONE_ACTION_ID),
            input_id=input_id,
            artifact_payload=work_result_payload()
            | {"summary": f"Corrected gaps for {input_id}."},
        ),
        deterministic_context(
            transition_id=f"transition-{input_id}",
            work_item_id=reviewer_work_id,
            activation_id=reviewer_activation_id,
        ),
    )


def _after_three_reviewer_gaps(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_to_reviewer_claim(plan, fingerprint)
    reviewer_run_id = "run-reviewer"
    for attempt in range(1, 4):
        state = _observe_gap_retry(
            state,
            plan=plan,
            fingerprint=fingerprint,
            reviewer_run_id=reviewer_run_id,
            input_id=f"observe-reviewer-gaps-found-{attempt}",
            worker_work_id=f"work-worker-gap-{attempt}",
            worker_activation_id=f"activation-worker-gap-{attempt}",
        )
        state = _claim_activation(
            state,
            input_id=f"claim-worker-gap-{attempt}",
            activation_id=f"activation-worker-gap-{attempt}",
            run_id=f"run-worker-gap-{attempt}",
        )
        state = _observe_gap_worker_done(
            state,
            plan=plan,
            fingerprint=fingerprint,
            worker_run_id=f"run-worker-gap-{attempt}",
            input_id=f"observe-gap-worker-done-{attempt}",
            reviewer_work_id=f"work-reviewer-after-gap-{attempt}",
            reviewer_activation_id=f"activation-reviewer-after-gap-{attempt}",
        )
        reviewer_run_id = f"run-reviewer-after-gap-{attempt}"
        state = _claim_activation(
            state,
            input_id=f"claim-reviewer-after-gap-{attempt}",
            activation_id=f"activation-reviewer-after-gap-{attempt}",
            run_id=reviewer_run_id,
        )
    return state


def test_reviewer_gaps_found_routes_gap_packet_to_worker() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_reviewer_claim(plan, fingerprint)
    reviewer_work = state.work_items["work-reviewer"]
    original_work_packet = reviewer_work.payload["work_packet"]
    latest_work_result = reviewer_work.payload["work_result"]
    assert isinstance(original_work_packet, Mapping)
    assert isinstance(latest_work_result, Mapping)
    gap_packet = gap_packet_payload()

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer",
            action_id=str(GAPS_FOUND_ACTION_ID),
            input_id="observe-reviewer-gaps-found",
            artifact_payload=gap_packet,
            marker="GAPS_FOUND",
        ),
        simple_loop_context("observe-reviewer-gaps-found"),
    )

    _assert_route_progress(decision)
    after = apply(state, decision)

    artifact = after.artifacts["transition-observe-reviewer-gaps-found:artifact"]
    assert artifact.schema_id == ArtifactSchemaId("simple_loop.gap_packet")
    assert artifact.work_item_id == "work-reviewer"
    assert artifact.payload == gap_packet

    gap_work = after.work_items["work-worker-gap"]
    gap_activation = after.activations["activation-worker-gap"]
    assert gap_work.queue_family_id == QueueFamilyId("gap_packet")
    assert gap_work.lineage_id == "work-prompt"
    assert gap_work.ref.plan_ref.authority_fingerprint == fingerprint
    assert gap_work.payload == dict(work_prompt_payload()) | {
        "work_packet": original_work_packet,
        "latest_work_result": latest_work_result,
        "gap_packet": gap_packet,
    }
    assert gap_activation.work_item_id == "work-worker-gap"
    assert gap_activation.lineage_id == "work-prompt"
    assert str(gap_activation.stage_kind_id) == "simple_loop.worker"
    assert gap_activation.graph_node_id == "simple_loop.worker.gaps"
    assert str(gap_activation.runner_binding_id) == (
        "simple_loop.default_agent_runner"
    )

    route = after.activation_routes[-1]
    assert route.action_id == GAPS_FOUND_ACTION_ID
    assert route.source_run_id == "run-reviewer"
    assert route.source_work_item_id == "work-reviewer"
    assert route.target_work_item_id == "work-worker-gap"
    assert route.target_activation_id == "activation-worker-gap"

    worker_claimed = apply_accepted_input(
        after,
        ClaimWork("claim-worker-gap", activation_id="activation-worker-gap"),
        simple_loop_context("claim-worker-gap"),
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=worker_claimed,
        run_id="run-worker-gap",
    )
    assert set(dispatch.work_item_payload) == {
        "prompt_id",
        "body",
        "work_packet",
        "latest_work_result",
        "gap_packet",
    }
    _assert_source_prompt_preserved(dispatch.work_item_payload)
    _assert_completion_definition_preserved(
        routed_payload=dispatch.work_item_payload,
        original_work_packet=original_work_packet,
    )
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-reviewer",
        run_id="run-reviewer",
        action_id=GAPS_FOUND_ACTION_ID,
    )


def test_worker_consumes_gap_context_and_routes_new_result_to_reviewer() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_gap_worker_claim(plan, fingerprint)
    first_reviewer = state.activations["activation-reviewer"]
    original_work_packet = state.work_items["work-worker-gap"].payload["work_packet"]
    assert isinstance(original_work_packet, Mapping)
    new_work_result = {
        "artifact_kind": "simple_loop.work_result",
        "summary": "Worker corrected the review gaps.",
    }

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker-gap",
            action_id=str(WORK_DONE_ACTION_ID),
            input_id="observe-gap-worker-done",
            artifact_payload=new_work_result,
            marker="WORK_DONE",
        ),
        simple_loop_context("observe-gap-worker-done"),
    )

    _assert_route_progress(decision)
    after = apply(state, decision)
    second_reviewer = after.activations["activation-reviewer-after-gap"]
    reviewer_work = after.work_items["work-reviewer-after-gap"]

    assert second_reviewer.activation_id != first_reviewer.activation_id
    assert second_reviewer.work_item_id != first_reviewer.work_item_id
    assert second_reviewer.plan_ref.authority_fingerprint == fingerprint
    assert second_reviewer.plan_ref == first_reviewer.plan_ref
    assert second_reviewer.lineage_id == first_reviewer.lineage_id == "work-prompt"
    assert reviewer_work.queue_family_id == QueueFamilyId("work_packet")
    assert reviewer_work.lineage_id == "work-prompt"
    assert reviewer_work.payload == dict(work_prompt_payload()) | {
        "work_packet": original_work_packet,
        "work_result": new_work_result,
    }
    _assert_completion_definition_preserved(
        routed_payload=reviewer_work.payload,
        original_work_packet=original_work_packet,
    )


def test_worker_insufficient_spec_routes_detail_request_to_manager() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    worker_work = state.work_items["work-worker"]
    original_work_packet = worker_work.payload["work_packet"]
    assert isinstance(original_work_packet, Mapping)
    detail_request = detail_request_payload()

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id=str(INSUFFICIENT_SPEC_ACTION_ID),
            input_id="observe-worker-insufficient-spec",
            artifact_payload=detail_request,
            marker="INSUFFICIENT_SPEC",
        ),
        simple_loop_context("observe-worker-insufficient-spec"),
    )

    _assert_route_progress(decision)
    after = apply(state, decision)

    artifact = after.artifacts["transition-observe-worker-insufficient-spec:artifact"]
    assert artifact.schema_id == ArtifactSchemaId("simple_loop.detail_request")
    assert artifact.work_item_id == "work-worker"
    assert artifact.payload == detail_request

    detail_work = after.work_items["work-manager-detail"]
    detail_activation = after.activations["activation-manager-detail"]
    assert detail_work.queue_family_id == QueueFamilyId("work_packet")
    assert detail_work.lineage_id == "work-prompt"
    assert detail_work.payload == dict(work_prompt_payload()) | {
        "work_packet": original_work_packet,
        "detail_request": detail_request,
    }
    assert detail_activation.work_item_id == "work-manager-detail"
    assert detail_activation.lineage_id == "work-prompt"
    assert str(detail_activation.stage_kind_id) == "simple_loop.manager"
    assert detail_activation.graph_node_id == "simple_loop.manager.detail_request"

    route = after.activation_routes[-1]
    assert route.action_id == INSUFFICIENT_SPEC_ACTION_ID
    assert route.source_run_id == "run-worker"
    assert route.source_work_item_id == "work-worker"
    assert route.target_work_item_id == "work-manager-detail"
    assert route.target_activation_id == "activation-manager-detail"

    manager_claimed = apply_accepted_input(
        after,
        ClaimWork("claim-manager-detail", activation_id="activation-manager-detail"),
        simple_loop_context("claim-manager-detail"),
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=manager_claimed,
        run_id="run-manager-detail",
    )
    assert set(dispatch.work_item_payload) == {
        "prompt_id",
        "body",
        "work_packet",
        "detail_request",
    }
    _assert_source_prompt_preserved(dispatch.work_item_payload)
    _assert_completion_definition_preserved(
        routed_payload=dispatch.work_item_payload,
        original_work_packet=original_work_packet,
    )

    revised_work_packet = _revised_work_packet_payload()
    revised_decision = decide(
        manager_claimed,
        runner_observation(
            state=manager_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager-detail",
            action_id=str(PACKET_READY_ACTION_ID),
            input_id="observe-manager-detail-packet-ready",
            artifact_payload=revised_work_packet,
            marker="PACKET_READY",
        ),
        simple_loop_context("observe-manager-detail-packet-ready"),
    )
    _assert_route_progress(revised_decision)
    revised = apply(manager_claimed, revised_decision)

    revised_worker = revised.work_items["work-worker-revised"]
    revised_activation = revised.activations["activation-worker-revised"]
    assert revised_worker.queue_family_id == QueueFamilyId("work_packet")
    assert revised_worker.lineage_id == "work-prompt"
    assert revised_worker.payload == dict(work_prompt_payload()) | {
        "work_packet": revised_work_packet
    }
    assert str(revised_activation.stage_kind_id) == "simple_loop.worker"
    assert revised_activation.graph_node_id == "simple_loop.worker.start"
    assert revised.activation_routes[-1].action_id == PACKET_READY_ACTION_ID
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-worker",
        run_id="run-worker",
        action_id=INSUFFICIENT_SPEC_ACTION_ID,
    )


def test_reviewer_incident_required_before_counter_threshold_refuses() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_reviewer_claim(plan, fingerprint)
    incident_report = incident_report_payload()

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer",
            action_id=str(INCIDENT_REQUIRED_ACTION_ID),
            input_id="observe-reviewer-incident-required",
            artifact_payload=incident_report,
            marker="INCIDENT_REQUIRED",
        ),
        simple_loop_context("observe-reviewer-incident-required"),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "counter_threshold_not_reached"
    _assert_no_workflow_progress(decision)
    _assert_no_progress_state_changes(after, state)
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-reviewer",
        run_id="run-reviewer",
        action_id=INCIDENT_REQUIRED_ACTION_ID,
        refusal_reason="counter_threshold_not_reached",
    )


def test_reviewer_counter_accepts_fourth_incident_and_refuses_fourth_gap() -> None:
    plan, fingerprint = compile_simple_loop()
    state = _after_three_reviewer_gaps(plan, fingerprint)
    reviewer_work = state.work_items["work-reviewer-after-gap-3"]
    original_work_packet = reviewer_work.payload["work_packet"]
    latest_work_result = reviewer_work.payload["work_result"]
    assert isinstance(original_work_packet, Mapping)
    assert isinstance(latest_work_result, Mapping)

    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-reviewer-after-gap-3",
    )
    counters = dispatch.governance_context["counters"]
    assert isinstance(counters, Mapping)
    counter_context = counters["simple_loop.reviewer_gap_counter"]
    assert isinstance(counter_context, Mapping)
    assert counter_context == {
        "value": 3,
        "threshold_count": 4,
        "increment_action_id": str(GAPS_FOUND_ACTION_ID),
        "threshold_action_id": str(INCIDENT_REQUIRED_ACTION_ID),
        "next_increment_requires_threshold_action": True,
    }
    assert dispatch.payload()["governance_context"] == dispatch.governance_context
    terminal_options = {
        option["action_id"]: option for option in dispatch.terminal_options
    }
    incident_option = terminal_options[str(INCIDENT_REQUIRED_ACTION_ID)]
    assert incident_option["outcome_id"] == (
        "simple_loop.reviewer.incident_required"
    )
    assert incident_option["marker"] == "INCIDENT_REQUIRED"
    assert incident_option["action_kind"] == "route"
    assert incident_option["artifact_schema_id"] == "simple_loop.incident_report"
    assert incident_option["counter"] == {
        "counter_id": "simple_loop.reviewer_gap_counter",
        **counter_context,
    }

    incident_state = replace(state)
    fourth_gap = decide(
        incident_state,
        runner_observation(
            state=incident_state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer-after-gap-3",
            action_id=str(GAPS_FOUND_ACTION_ID),
            input_id="observe-reviewer-fourth-gap",
            artifact_payload=gap_packet_payload(),
        ),
        deterministic_context(transition_id="transition-observe-reviewer-fourth-gap"),
    )
    assert fourth_gap.accepted is False
    assert fourth_gap.refusal is not None
    assert fourth_gap.refusal.reason == "counter_threshold_requires_escalation"

    incident_report = incident_report_payload()
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer-after-gap-3",
            action_id=str(INCIDENT_REQUIRED_ACTION_ID),
            input_id="observe-reviewer-incident-required",
            artifact_payload=incident_report,
            marker=str(incident_option["marker"]),
        ),
        simple_loop_context("observe-reviewer-incident-required"),
    )

    _assert_route_progress(decision)
    after = apply(state, decision)

    artifact = after.artifacts[
        "transition-observe-reviewer-incident-required:artifact"
    ]
    assert artifact.schema_id == ArtifactSchemaId("simple_loop.incident_report")
    assert artifact.work_item_id == "work-reviewer-after-gap-3"
    assert artifact.payload == incident_report

    incident_work = after.work_items["work-manager-incident"]
    incident_activation = after.activations["activation-manager-incident"]
    assert incident_work.queue_family_id == QueueFamilyId("incident_report")
    assert incident_work.lineage_id == "work-prompt"
    assert incident_work.payload == dict(work_prompt_payload()) | {
        "work_packet": original_work_packet,
        "latest_work_result": latest_work_result,
        "incident_report": incident_report,
    }
    assert incident_activation.work_item_id == "work-manager-incident"
    assert incident_activation.lineage_id == "work-prompt"
    assert str(incident_activation.stage_kind_id) == "simple_loop.manager"
    assert incident_activation.graph_node_id == "simple_loop.manager.incident"

    route = after.activation_routes[-1]
    assert route.action_id == INCIDENT_REQUIRED_ACTION_ID
    assert route.source_run_id == "run-reviewer-after-gap-3"
    assert route.source_work_item_id == "work-reviewer-after-gap-3"
    assert route.target_work_item_id == "work-manager-incident"
    assert route.target_activation_id == "activation-manager-incident"

    manager_claimed = apply_accepted_input(
        after,
        ClaimWork(
            "claim-manager-incident",
            activation_id="activation-manager-incident",
        ),
        simple_loop_context("claim-manager-incident"),
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=manager_claimed,
        run_id="run-manager-incident",
    )
    assert set(dispatch.work_item_payload) == {
        "prompt_id",
        "body",
        "work_packet",
        "latest_work_result",
        "incident_report",
    }
    _assert_source_prompt_preserved(dispatch.work_item_payload)
    _assert_completion_definition_preserved(
        routed_payload=dispatch.work_item_payload,
        original_work_packet=original_work_packet,
    )
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-reviewer-after-gap-3",
        run_id="run-reviewer-after-gap-3",
        action_id=INCIDENT_REQUIRED_ACTION_ID,
    )

    runtime_field_names = {field.name for field in fields(RuntimeState)}
    assert {"review_failure_count", "retry_threshold", "incident_threshold"}.isdisjoint(
        runtime_field_names
    )


@pytest.mark.parametrize(
    (
        "bootstrap_name",
        "run_id",
        "action_id",
        "input_id",
        "artifact_payload",
        "expected_reason",
    ),
    (
        (
            "worker",
            "run-worker",
            str(INSUFFICIENT_SPEC_ACTION_ID),
            "observe-invalid-detail-request",
            {
                "artifact_kind": "simple_loop.detail_request",
                "missing_details": ("",),
            },
            "invalid_artifact_payload",
        ),
        (
            "reviewer",
            "run-reviewer",
            str(GAPS_FOUND_ACTION_ID),
            "observe-invalid-gap-packet",
            {"artifact_kind": "simple_loop.gap_packet", "gaps": ()},
            "invalid_artifact_payload",
        ),
        (
            "reviewer",
            "run-reviewer",
            str(INCIDENT_REQUIRED_ACTION_ID),
            "observe-invalid-incident-report",
            {"artifact_kind": "simple_loop.incident_report"},
            "counter_threshold_not_reached",
        ),
    ),
)
def test_invalid_detail_gap_and_incident_payloads_refuse_without_side_effects(
    bootstrap_name: str,
    run_id: str,
    action_id: str,
    input_id: str,
    artifact_payload: Mapping[str, AuthorityValue],
    expected_reason: str,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = (
        bootstrap_to_worker_claim(plan, fingerprint)
        if bootstrap_name == "worker"
        else bootstrap_to_reviewer_claim(plan, fingerprint)
    )
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        artifact_payload=artifact_payload,
    )

    decision = decide(
        state,
        observation,
        deterministic_context(transition_id=f"transition-{input_id}"),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == expected_reason
    _assert_no_workflow_progress(decision)
    _assert_no_progress_state_changes(after, state)


def test_manager_incident_triage_records_operator_wait() -> None:
    plan, fingerprint = compile_simple_loop()
    incident_triaged = action_by_id(plan, str(INCIDENT_TRIAGED_ACTION_ID))
    assert incident_triaged.action_kind == "operator_wait"
    state = _after_three_reviewer_gaps(plan, fingerprint)
    incident_ready = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer-after-gap-3",
            action_id=str(INCIDENT_REQUIRED_ACTION_ID),
            input_id="observe-reviewer-incident-required",
            artifact_payload=incident_report_payload(),
            marker="INCIDENT_REQUIRED",
        ),
        simple_loop_context("observe-reviewer-incident-required"),
    )
    state = apply_accepted_input(
        incident_ready,
        ClaimWork(
            "claim-manager-incident",
            activation_id="activation-manager-incident",
        ),
        simple_loop_context("claim-manager-incident"),
    )

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager-incident",
            action_id=str(INCIDENT_TRIAGED_ACTION_ID),
            input_id="observe-manager-incident-triaged",
            artifact_payload=incident_report_payload(),
            marker="INCIDENT_TRIAGED",
        ),
        simple_loop_context("observe-manager-incident-triaged"),
    )
    after = apply(state, decision)

    assert decision.accepted is True
    assert decision.refusal is None
    assert "mutation.record_operator_wait" in mutation_kinds(decision)
    assert "mutation.set_pause" not in mutation_kinds(decision)
    assert "mutation.set_quarantine" not in mutation_kinds(decision)
    assert "mutation.close_work_item" in mutation_kinds(decision)
    assert "mutation.create_work_item" not in mutation_kinds(decision)
    assert "mutation.create_activation" not in mutation_kinds(decision)
    assert "mutation.route_activation" not in mutation_kinds(decision)
    assert "work-manager-incident" in after.closed_work_items
    assert after.pause is None
    assert after.quarantines == {}
    assert after.lineage_quarantines == {}
    assert len(after.operator_waits) == 1
    wait = next(iter(after.operator_waits.values()))
    assert str(wait.operator_wait_id) == "simple_loop.manager_incident_wait"
    assert wait.source_work_item_id == "work-manager-incident"
    assert wait.source_run_id == "run-manager-incident"
    assert wait.lineage_id == "work-prompt"
    assert wait.status == "active"
    _assert_audit_context(
        decision,
        fingerprint=fingerprint,
        work_item_id="work-manager-incident",
        run_id="run-manager-incident",
        action_id=INCIDENT_TRIAGED_ACTION_ID,
    )
