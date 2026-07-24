"""Reusable simple_loop runtime scenarios for staged proof tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from millrace.contracts import QueueFamilyId, SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import Activation, RunRecord, RunRef, RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    SelectDefaultPlan,
)
from millrace.kernel import empty_runtime_state
from millrace.testing import deterministic_context
from support.simple_loop import (
    apply_accepted_input,
    detail_request_payload,
    gap_packet_payload,
    incident_report_payload,
    runner_observation,
    simple_loop_context,
    troubleshooting_report_payload,
    work_packet_payload,
    work_prompt_payload,
    work_result_payload,
)


def bootstrap_to_manager_ready(
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
            queue_family_id=QueueFamilyId("work_prompt"),
            payload=work_prompt_payload(),
        ),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            simple_loop_context(transition_input.input_id),
        )
    return state


def bootstrap_to_manager_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_to_manager_ready(plan, fingerprint)
    return apply_accepted_input(
        state,
        ClaimWork("claim-manager", activation_id="activation-manager"),
        simple_loop_context("claim-manager"),
    )


def bootstrap_to_worker_ready(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_manager_claim(plan, fingerprint)
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id="simple_loop.manager.packet_ready",
            input_id="observe-manager-packet-ready",
            artifact_payload=work_packet
            if work_packet is not None
            else work_packet_payload(),
        ),
        simple_loop_context("observe-manager-packet-ready"),
    )


def bootstrap_to_worker_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_worker_ready(
        plan,
        fingerprint,
        work_packet=work_packet,
    )
    return apply_accepted_input(
        state,
        ClaimWork("claim-worker", activation_id="activation-worker"),
        simple_loop_context("claim-worker"),
    )


def bootstrap_to_manager_detail_ready(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
    detail_request: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_worker_claim(
        plan,
        fingerprint,
        work_packet=work_packet,
    )
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="simple_loop.worker.insufficient_spec",
            input_id="observe-worker-insufficient-spec",
            artifact_payload=detail_request
            if detail_request is not None
            else detail_request_payload(),
        ),
        simple_loop_context("observe-worker-insufficient-spec"),
    )


def bootstrap_to_reviewer_ready(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
    work_result: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_worker_claim(
        plan,
        fingerprint,
        work_packet=work_packet,
    )
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="simple_loop.worker.work_done",
            input_id="observe-worker-done",
            artifact_payload=work_result
            if work_result is not None
            else work_result_payload(),
        ),
        simple_loop_context("observe-worker-done"),
    )


def bootstrap_to_reviewer_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
    work_result: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_reviewer_ready(
        plan,
        fingerprint,
        work_packet=work_packet,
        work_result=work_result,
    )
    return apply_accepted_input(
        state,
        ClaimWork("claim-reviewer", activation_id="activation-reviewer"),
        simple_loop_context("claim-reviewer"),
    )


def bootstrap_to_gap_worker_ready(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
    work_result: Mapping[str, AuthorityValue] | None = None,
    gap_packet: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_reviewer_claim(
        plan,
        fingerprint,
        work_packet=work_packet,
        work_result=work_result,
    )
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer",
            action_id="simple_loop.reviewer.gaps_found",
            input_id="observe-reviewer-gaps-found",
            artifact_payload=gap_packet
            if gap_packet is not None
            else gap_packet_payload(),
        ),
        simple_loop_context("observe-reviewer-gaps-found"),
    )


def bootstrap_to_gap_worker_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
    work_result: Mapping[str, AuthorityValue] | None = None,
    gap_packet: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_gap_worker_ready(
        plan,
        fingerprint,
        work_packet=work_packet,
        work_result=work_result,
        gap_packet=gap_packet,
    )
    return apply_accepted_input(
        state,
        ClaimWork("claim-worker-gap", activation_id="activation-worker-gap"),
        simple_loop_context("claim-worker-gap"),
    )


def bootstrap_to_manager_incident_ready(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
    work_result: Mapping[str, AuthorityValue] | None = None,
    incident_report: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_reviewer_claim(
        plan,
        fingerprint,
        work_packet=work_packet,
        work_result=work_result,
    )
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer",
            action_id="simple_loop.reviewer.incident_required",
            input_id="observe-reviewer-incident-required",
            artifact_payload=incident_report
            if incident_report is not None
            else incident_report_payload(),
        ),
        simple_loop_context("observe-reviewer-incident-required"),
    )


def bootstrap_to_manager_incident_claim(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
    work_result: Mapping[str, AuthorityValue] | None = None,
    incident_report: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_manager_incident_ready(
        plan,
        fingerprint,
        work_packet=work_packet,
        work_result=work_result,
        incident_report=incident_report,
    )
    return apply_accepted_input(
        state,
        ClaimWork(
            "claim-manager-incident",
            activation_id="activation-manager-incident",
        ),
        simple_loop_context("claim-manager-incident"),
    )


def bootstrap_to_reviewer_accepted(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    work_packet: Mapping[str, AuthorityValue] | None = None,
    work_result: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    state = bootstrap_to_reviewer_claim(
        plan,
        fingerprint,
        work_packet=work_packet,
        work_result=work_result,
    )
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer",
            action_id="simple_loop.reviewer.accepted",
            input_id="observe-reviewer-accepted",
            artifact_payload={},
        ),
        simple_loop_context("observe-reviewer-accepted"),
    )


def bootstrap_to_manager_retry_claim_after_first_recovery(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_to_manager_claim(plan, fingerprint)
    recovered = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id="simple_loop.manager.blocked",
            input_id="observe-manager-blocked",
            artifact_payload={},
        ),
        simple_loop_context("observe-manager-blocked"),
    )
    troubleshooter_claimed = apply_accepted_input(
        recovered,
        ClaimWork(
            "claim-troubleshooter-manager",
            activation_id="activation-troubleshooter-manager",
        ),
        deterministic_context(
            transition_id="transition-claim-troubleshooter-manager",
            run_id="run-troubleshooter-manager",
            claim_id="claim-troubleshooter-manager",
            fencing_token="fence-troubleshooter-manager",
        ),
    )
    returned = apply_accepted_input(
        troubleshooter_claimed,
        runner_observation(
            state=troubleshooter_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-troubleshooter-manager",
            action_id="simple_loop.troubleshooter.resolved",
            input_id="observe-troubleshooter-resolved",
            artifact_payload=troubleshooting_report_payload(),
        ),
        deterministic_context(
            transition_id="transition-observe-troubleshooter-resolved",
            activation_id="activation-returned-manager",
        ),
    )
    return apply_accepted_input(
        returned,
        ClaimWork(
            "claim-returned-manager",
            activation_id="activation-returned-manager",
        ),
        deterministic_context(
            transition_id="transition-claim-returned-manager",
            run_id="run-manager-retry",
            claim_id="claim-returned-manager",
            fencing_token="fence-manager-retry",
        ),
    )


def bootstrap_to_manager_cooldown_wait(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    observed_at: int = 1000,
) -> RuntimeState:
    retry_claimed = bootstrap_to_manager_retry_claim_after_first_recovery(
        plan,
        fingerprint,
    )
    return apply_accepted_input(
        retry_claimed,
        runner_observation(
            state=retry_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager-retry",
            action_id="simple_loop.manager.blocked",
            input_id="observe-manager-blocked-2",
            artifact_payload={},
            observed_at=observed_at,
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-2",
            activation_id="activation-should-not-exist",
        ),
    )


def seed_claimed_recorded_source_retry(
    state: RuntimeState,
    *,
    activation_id: str,
    run_id: str,
    claim_id: str,
    fencing_token: str,
    created_by_input_id: str,
) -> RuntimeState:
    attempt = next(iter(state.recovery_attempts.values()))
    source_work_item = state.work_items[attempt.source_work_item_id]
    source_generation = source_work_item.ref.generation
    activation = Activation(
        activation_id=activation_id,
        work_item_id=attempt.source_work_item_id,
        lineage_id=attempt.lineage_id,
        plan_ref=source_work_item.ref.plan_ref,
        queue_family_id=attempt.source_queue_family_id,
        graph_node_id=attempt.source_graph_node_id,
        stage_kind_id=attempt.source_stage_kind_id,
        runner_binding_id=attempt.source_runner_binding_id,
        generation=source_generation + 1,
        created_by_input_id=created_by_input_id,
        claimed_by_run_id=run_id,
    )
    run = RunRecord(
        run_ref=RunRef(
            run_id=run_id,
            work_item_id=attempt.source_work_item_id,
            claim_id=claim_id,
            plan_ref=source_work_item.ref.plan_ref,
            generation=source_generation,
            fencing_token=fencing_token,
        ),
        work_item_id=attempt.source_work_item_id,
        activation_id=activation_id,
        stage_kind_id=attempt.source_stage_kind_id,
        runner_binding_id=attempt.source_runner_binding_id,
        created_by_input_id=created_by_input_id,
    )
    return replace(
        state,
        activations={**state.activations, activation_id: activation},
        runs={**state.runs, run_id: run},
    )


__all__ = (
    "bootstrap_to_gap_worker_claim",
    "bootstrap_to_gap_worker_ready",
    "bootstrap_to_manager_detail_ready",
    "bootstrap_to_manager_incident_claim",
    "bootstrap_to_manager_incident_ready",
    "bootstrap_to_manager_claim",
    "bootstrap_to_manager_cooldown_wait",
    "bootstrap_to_manager_ready",
    "bootstrap_to_manager_retry_claim_after_first_recovery",
    "bootstrap_to_reviewer_accepted",
    "bootstrap_to_reviewer_claim",
    "bootstrap_to_reviewer_ready",
    "bootstrap_to_worker_claim",
    "bootstrap_to_worker_ready",
    "seed_claimed_recorded_source_retry",
)
