from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

import millrace.contracts.transition as transition_contracts
import millrace.operator as operator_api
from kernel.simple_loop_scenarios import (
    bootstrap_to_gap_worker_ready,
    bootstrap_to_manager_claim,
    bootstrap_to_manager_cooldown_wait,
    bootstrap_to_manager_detail_ready,
    bootstrap_to_manager_ready,
    bootstrap_to_reviewer_accepted,
    bootstrap_to_reviewer_claim,
    bootstrap_to_reviewer_ready,
    bootstrap_to_worker_ready,
)
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.runner import (
    RunnerAdapterProvenance,
    runner_result_evidence_from_payload,
)
from millrace.contracts.state import LineageQuarantineRecord, RuntimeState
from millrace.contracts.transition import (
    ClaimWork,
    RunnerResultObserved,
    TransitionInput,
    input_payload_digest,
)
from millrace.kernel import apply
from millrace.operator import operator_status
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import dumps_cas_object, encode_payload
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_completion_input_id,
    fake_runner_dispatch_envelope_for_run,
)
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support.simple_loop import (
    apply_accepted_input,
    compile_simple_loop,
    detail_request_payload,
    gap_packet_payload,
    incident_report_payload,
    mutation_kinds,
    runner_observation,
    simple_loop_context,
    stage_kind_by_id,
    troubleshooting_report_payload,
    work_packet_payload,
    work_prompt_payload,
    work_result_payload,
)


def _queue_counts(
    state: RuntimeState,
    queue_family_id: str,
) -> tuple[int, int, int, int]:
    family = next(
        family
        for family in operator_status(state).queue_families
        if family.queue_family_id == queue_family_id
    )
    return (
        family.ready_count,
        family.active_count,
        family.closed_count,
        family.quarantined_count,
    )


def _timer_due(input_id: str, *, wait_id: str, observed_at: int) -> TransitionInput:
    timer_type = getattr(transition_contracts, "TimerDue")
    return cast(
        TransitionInput,
        timer_type(input_id, wait_id=wait_id, observed_at=observed_at),
    )


def _lineage_live_or_ready_work_item_ids(
    state: RuntimeState,
    lineage_id: str,
) -> set[str]:
    status = operator_status(state)
    live_or_ready = {
        active.work_item_id
        for active in status.active_runs
        if active.lineage_id == lineage_id
    }
    closed_work_item_ids = set(state.closed_work_items)
    quarantined_work_item_ids = set(state.quarantines)
    active_lineage_quarantine_ids = {
        quarantine.lineage_id
        for quarantine in state.lineage_quarantines.values()
        if quarantine.status == "active"
    }
    for activation in state.activations.values():
        work_item = state.work_items.get(activation.work_item_id)
        if work_item is None or work_item.lineage_id != lineage_id:
            continue
        if work_item.lineage_id in active_lineage_quarantine_ids:
            continue
        if activation.lineage_id != lineage_id:
            continue
        if activation.claimed_by_run_id is not None:
            continue
        if work_item.ref.work_item_id in closed_work_item_ids:
            continue
        if work_item.ref.work_item_id in quarantined_work_item_ids:
            continue
        if activation.plan_ref != work_item.ref.plan_ref:
            continue
        if activation.queue_family_id != work_item.queue_family_id:
            continue
        if activation.generation != work_item.ref.generation:
            continue
        live_or_ready.add(work_item.ref.work_item_id)
    return live_or_ready


def _active_lineage_quarantine(
    state: RuntimeState,
    lineage_id: str,
) -> LineageQuarantineRecord:
    records = tuple(
        quarantine
        for quarantine in state.lineage_quarantines.values()
        if quarantine.lineage_id == lineage_id and quarantine.status == "active"
    )
    assert len(records) == 1
    return records[0]


def _observation_for_input(state: RuntimeState, input_id: str):
    input_id = fake_runner_completion_input_id(input_id)
    return next(
        observation
        for observation in state.runner_observations.values()
        if observation.created_by_input_id == input_id
    )


def _completion_definition(payload: Mapping[str, object]) -> object:
    work_packet = payload["work_packet"]
    assert isinstance(work_packet, Mapping)
    return work_packet["completion_definition"]


def _assert_source_prompt_preserved(payload: Mapping[str, object]) -> None:
    assert {
        "prompt_id": payload["prompt_id"],
        "body": payload["body"],
    } == work_prompt_payload()


def _lineage_quarantined_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=1000,
    )
    wait = next(iter(waiting.cooldown_waits.values()))
    resumed = apply_accepted_input(
        waiting,
        _timer_due("timer-cooldown-due", wait_id=wait.wait_id, observed_at=1900),
        deterministic_context(
            transition_id="transition-timer-cooldown-due",
            activation_id="activation-troubleshooter-manager-resumed",
        ),
    )
    claimed = apply_accepted_input(
        resumed,
        ClaimWork(
            "claim-troubleshooter-manager-resumed",
            activation_id="activation-troubleshooter-manager-resumed",
        ),
        deterministic_context(
            transition_id="transition-claim-troubleshooter-manager-resumed",
            run_id="run-troubleshooter-manager-resumed",
            claim_id="claim-troubleshooter-manager-resumed",
            fencing_token="fence-troubleshooter-manager-resumed",
        ),
    )
    returned = apply_accepted_input(
        claimed,
        runner_observation(
            state=claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-troubleshooter-manager-resumed",
            action_id="simple_loop.troubleshooter.resolved",
            input_id="observe-troubleshooter-resolved-after-cooldown",
            artifact_payload=troubleshooting_report_payload(),
        ),
        deterministic_context(
            transition_id="transition-observe-troubleshooter-resolved-after-cooldown",
            activation_id="activation-returned-manager-2",
        ),
    )
    third_source = apply_accepted_input(
        returned,
        ClaimWork(
            "claim-returned-manager-2",
            activation_id="activation-returned-manager-2",
        ),
        deterministic_context(
            transition_id="transition-claim-returned-manager-2",
            run_id="run-source-retry-3",
            claim_id="claim-source-retry-3",
            fencing_token="fence-source-retry-3",
        ),
    )
    third = apply_accepted_input(
        third_source,
        runner_observation(
            state=third_source,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-source-retry-3",
            action_id="simple_loop.manager.blocked",
            input_id="observe-manager-blocked-3",
            artifact_payload={},
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-3",
            activation_id="activation-troubleshooter-manager-3",
        ),
    )
    assert _active_lineage_quarantine(third, "work-prompt").status == "active"
    return third


def _active_operator_needed_quarantined_state(
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
    quarantined = apply_accepted_input(
        troubleshooter_claimed,
        runner_observation(
            state=troubleshooter_claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-troubleshooter-manager",
            action_id="simple_loop.troubleshooter.operator_needed",
            input_id="observe-troubleshooter-operator-needed",
            artifact_payload=troubleshooting_report_payload(
                result="operator needed",
                next_route="operator_intervention",
            ),
        ),
        deterministic_context(
            transition_id="transition-observe-troubleshooter-operator-needed",
        ),
    )
    attempt = next(iter(quarantined.recovery_attempts.values()))
    assert attempt.phase == "quarantine_eligible"
    assert _active_lineage_quarantine(quarantined, "work-prompt").status == "active"
    return quarantined


def _manager_detail_wait_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_to_manager_claim(plan, fingerprint)
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id="simple_loop.manager.needs_operator_detail",
            input_id="observe-manager-detail",
            artifact_payload=detail_request_payload(),
        ),
        simple_loop_context("observe-manager-detail"),
    )


def _manager_incident_ready_after_counter_threshold(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_to_reviewer_claim(plan, fingerprint)
    reviewer_run_id = "run-reviewer"
    for attempt in range(1, 4):
        worker_work_id = f"work-worker-gap-{attempt}"
        worker_activation_id = f"activation-worker-gap-{attempt}"
        state = apply_accepted_input(
            state,
            runner_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id=reviewer_run_id,
                action_id="simple_loop.reviewer.gaps_found",
                input_id=f"observe-reviewer-gaps-found-{attempt}",
                artifact_payload=gap_packet_payload(),
            ),
            deterministic_context(
                transition_id=f"transition-observe-reviewer-gaps-found-{attempt}",
                work_item_id=worker_work_id,
                activation_id=worker_activation_id,
            ),
        )
        worker_run_id = f"run-worker-gap-{attempt}"
        state = apply_accepted_input(
            state,
            ClaimWork(
                f"claim-worker-gap-{attempt}",
                activation_id=worker_activation_id,
            ),
            deterministic_context(
                transition_id=f"transition-claim-worker-gap-{attempt}",
                run_id=worker_run_id,
                claim_id=f"claim-worker-gap-{attempt}",
                fencing_token=f"fence-worker-gap-{attempt}",
            ),
        )
        reviewer_work_id = f"work-reviewer-after-gap-{attempt}"
        reviewer_activation_id = f"activation-reviewer-after-gap-{attempt}"
        state = apply_accepted_input(
            state,
            runner_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id=worker_run_id,
                action_id="simple_loop.worker.work_done",
                input_id=f"observe-gap-worker-done-{attempt}",
                artifact_payload=work_result_payload()
                | {"summary": f"Corrected gaps for attempt {attempt}."},
            ),
            deterministic_context(
                transition_id=f"transition-observe-gap-worker-done-{attempt}",
                work_item_id=reviewer_work_id,
                activation_id=reviewer_activation_id,
            ),
        )
        reviewer_run_id = f"run-reviewer-after-gap-{attempt}"
        state = apply_accepted_input(
            state,
            ClaimWork(
                f"claim-reviewer-after-gap-{attempt}",
                activation_id=reviewer_activation_id,
            ),
            deterministic_context(
                transition_id=f"transition-claim-reviewer-after-gap-{attempt}",
                run_id=reviewer_run_id,
                claim_id=f"claim-reviewer-after-gap-{attempt}",
                fencing_token=f"fence-reviewer-after-gap-{attempt}",
            ),
        )
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=reviewer_run_id,
            action_id="simple_loop.reviewer.incident_required",
            input_id="observe-reviewer-incident-required",
            artifact_payload=incident_report_payload(),
            marker="INCIDENT_REQUIRED",
        ),
        simple_loop_context("observe-reviewer-incident-required"),
    )


def _operator_revised_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> tuple[RuntimeState, LineageQuarantineRecord]:
    quarantined = _lineage_quarantined_state(plan, fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    ReviseInput = getattr(operator_api, "OperatorReviseLineageInput")
    transition_input = operator_api.build_revise_lineage(
        quarantined,
        ReviseInput(
            input_id="operator-revise-lineage",
            option_id="simple_loop.revise_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator revised simple loop packet",
            payload=work_packet_payload(),
        ),
    )
    revised = apply(
        quarantined,
        decide(
            quarantined,
            transition_input,
            deterministic_context(
                transition_id="transition-operator-revise-lineage",
                work_item_id="work-operator-revised-packet",
                activation_id="activation-operator-revised-worker",
            ),
        ),
    )
    return revised, quarantine


def test_restart_preserves_first_recovery_attempt_state(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    dispatch = fake_runner_dispatch_envelope_for_run(state=state, run_id="run-manager")
    provenance = RunnerAdapterProvenance(
        adapter_kind="millforge",
        component_descriptor_sha256="a" * 64,
        invocation_evidence_sha256="b" * 64,
        correlation_id="corr-manager",
    )
    recovered = apply(
        state,
        decide(
            state,
            runner_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-manager",
                action_id="simple_loop.manager.blocked",
                input_id="observe-manager-blocked",
                artifact_payload={},
                overrides={"adapter_provenance": provenance.payload()},
            ),
            simple_loop_context("observe-manager-blocked"),
        ),
    )

    loaded = persist_and_load_runtime_state(tmp_path, recovered)

    stored_observation = _observation_for_input(
        loaded,
        "observe-manager-blocked",
    )
    evidence = runner_result_evidence_from_payload(stored_observation.payload)

    assert loaded.recovery_attempts == recovered.recovery_attempts
    assert loaded.admitted_plans == recovered.admitted_plans
    assert loaded.runner_observations == recovered.runner_observations
    assert evidence.adapter_provenance == provenance
    assert (
        evidence.run_id,
        evidence.plan_fingerprint,
        evidence.claim_id,
        evidence.generation,
        evidence.fencing_token,
        evidence.stage_kind_id,
        evidence.graph_node_id,
        evidence.runner_binding_id,
    ) == (
        dispatch.run_id,
        dispatch.plan_fingerprint,
        dispatch.claim_id,
        dispatch.generation,
        dispatch.fencing_token,
        dispatch.stage_kind_id,
        dispatch.graph_node_id,
        dispatch.runner_binding_id,
    )
    attempt = next(iter(loaded.recovery_attempts.values()))
    assert str(attempt.policy_id) == "simple_loop.blocked_recovery"
    assert attempt.plan_ref.authority_fingerprint == fingerprint
    assert attempt.attempt_count == 1
    assert attempt.phase == "active_recovery"
    assert attempt.source_run_id == "run-manager"
    assert attempt.latest_recovery_activation_id == (
        "activation-troubleshooter-manager"
    )
    assert operator_status(loaded).recovery_attempts[0].phase == "active_recovery"


def test_restart_preserves_claimed_recovery_attempt_latest_run(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    recovered = apply(
        state,
        decide(
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
        ),
    )
    claimed = apply(
        recovered,
        decide(
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
        ),
    )

    loaded = persist_and_load_runtime_state(tmp_path, claimed)

    assert loaded.recovery_attempts == claimed.recovery_attempts
    attempt = next(iter(loaded.recovery_attempts.values()))
    assert attempt.latest_recovery_activation_id == (
        "activation-troubleshooter-manager"
    )
    assert attempt.latest_recovery_run_id == "run-troubleshooter-manager"


def test_restart_preserves_pending_cooldown_wait_and_due_timer_resumes(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=1000,
    )

    loaded = persist_and_load_runtime_state(tmp_path, waiting)

    assert loaded.cooldown_waits == waiting.cooldown_waits
    wait = next(iter(loaded.cooldown_waits.values()))
    early_decision = decide(
        loaded,
        _timer_due("timer-cooldown-early", wait_id=wait.wait_id, observed_at=1899),
        deterministic_context(transition_id="transition-timer-cooldown-early"),
    )
    assert early_decision.accepted is False
    assert early_decision.refusal is not None
    assert early_decision.refusal.reason == "wait_not_due"

    due_decision = decide(
        loaded,
        _timer_due("timer-cooldown-due", wait_id=wait.wait_id, observed_at=1900),
        deterministic_context(
            transition_id="transition-timer-cooldown-due",
            activation_id="activation-troubleshooter-manager-resumed",
        ),
    )
    resumed = apply(loaded, due_decision)

    assert due_decision.accepted is True
    consumed_wait = resumed.cooldown_waits[wait.wait_id]
    assert consumed_wait.consumed_input_id == "timer-cooldown-due"
    assert consumed_wait.consumed_at == 1900
    assert consumed_wait.resulting_recovery_activation_id == (
        "activation-troubleshooter-manager-resumed"
    )
    attempt = next(iter(resumed.recovery_attempts.values()))
    assert attempt.phase == "active_recovery"
    assert attempt.latest_recovery_activation_id == (
        "activation-troubleshooter-manager-resumed"
    )
    assert (
        resumed.activations[
            "activation-troubleshooter-manager-resumed"
        ].plan_ref.authority_fingerprint
        == fingerprint
    )


def test_restart_preserves_no_artifact_pause_observation_receipt_authority(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=1000,
    )
    observation = _observation_for_input(waiting, "observe-manager-blocked-2")

    loaded = persist_and_load_runtime_state(tmp_path, waiting)
    loaded_observation = _observation_for_input(
        loaded,
        "observe-manager-blocked-2",
    )
    reconstructed = RunnerResultObserved(
        loaded_observation.created_by_input_id,
        run_id=loaded_observation.run_id,
        payload=loaded_observation.payload,
        observed_at=loaded_observation.observed_at,
    )

    assert loaded_observation == observation
    assert loaded_observation.observed_at == 1000
    assert loaded.cooldown_waits
    assert all(
        artifact.created_by_input_id != loaded_observation.created_by_input_id
        for artifact in loaded.artifacts.values()
    )
    assert (
        loaded.receipts[loaded_observation.created_by_input_id]
        .receipt_ref.input_payload_digest
        == input_payload_digest(reconstructed)
    )


@pytest.mark.parametrize("field", ("payload", "observed_at"))
def test_restart_refuses_no_artifact_pause_observation_payload_or_time_drift(
    tmp_path: Path,
    field: str,
) -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=1000,
    )
    observation = _observation_for_input(waiting, "observe-manager-blocked-2")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, waiting)

    if field == "payload":
        payload_digest = ContentAddressedByteStore(cas_root).put_bytes(
            dumps_cas_object(
                encode_payload(
                    {**observation.payload, "marker": "CORRUPT_MARKER"}
                )
            )
        )
        with sqlite3.connect(db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE runner_observations
                SET payload_digest = ?
                WHERE observation_id = ?
                """,
                (payload_digest, observation.observation_id),
            )
            assert cursor.rowcount == 1
    else:
        with sqlite3.connect(db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE runner_observations
                SET observed_at = 1001
                WHERE observation_id = ?
                """,
                (observation.observation_id,),
            )
            assert cursor.rowcount == 1

    with pytest.raises(
        StorageIntegrityError,
        match="runner_observations accepted-input authority invalid: receipt_authority",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_preserves_consumed_cooldown_wait_and_claimed_recovery_run(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=1000,
    )
    wait = next(iter(waiting.cooldown_waits.values()))
    resumed = apply(
        waiting,
        decide(
            waiting,
            _timer_due("timer-cooldown-due", wait_id=wait.wait_id, observed_at=1900),
            deterministic_context(
                transition_id="transition-timer-cooldown-due",
                activation_id="activation-troubleshooter-manager-resumed",
            ),
        ),
    )
    claimed = apply_accepted_input(
        resumed,
        ClaimWork(
            "claim-troubleshooter-manager-resumed",
            activation_id="activation-troubleshooter-manager-resumed",
        ),
        deterministic_context(
            transition_id="transition-claim-troubleshooter-manager-resumed",
            run_id="run-troubleshooter-manager-resumed",
            claim_id="claim-troubleshooter-manager-resumed",
            fencing_token="fence-troubleshooter-manager-resumed",
        ),
    )

    loaded = persist_and_load_runtime_state(tmp_path, claimed)

    assert loaded.cooldown_waits == claimed.cooldown_waits
    loaded_wait = loaded.cooldown_waits[wait.wait_id]
    assert loaded_wait.consumed_input_id == "timer-cooldown-due"
    assert loaded_wait.resulting_recovery_activation_id == (
        "activation-troubleshooter-manager-resumed"
    )
    attempt = next(iter(loaded.recovery_attempts.values()))
    assert attempt.phase == "active_recovery"
    assert attempt.latest_recovery_activation_id == (
        "activation-troubleshooter-manager-resumed"
    )
    assert attempt.latest_recovery_run_id == "run-troubleshooter-manager-resumed"
    duplicate = decide(
        loaded,
        _timer_due("timer-cooldown-again", wait_id=wait.wait_id, observed_at=1901),
        deterministic_context(transition_id="transition-timer-cooldown-again"),
    )
    assert duplicate.accepted is False
    assert duplicate.refusal is not None
    assert duplicate.refusal.reason == "wait_already_consumed"


def test_restart_preserves_consumed_cooldown_wait_after_attempt_advances(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=1000,
    )
    wait = next(iter(waiting.cooldown_waits.values()))
    resumed = apply_accepted_input(
        waiting,
        _timer_due("timer-cooldown-due", wait_id=wait.wait_id, observed_at=1900),
        deterministic_context(
            transition_id="transition-timer-cooldown-due",
            activation_id="activation-troubleshooter-manager-resumed",
        ),
    )
    claimed = apply_accepted_input(
        resumed,
        ClaimWork(
            "claim-troubleshooter-manager-resumed",
            activation_id="activation-troubleshooter-manager-resumed",
        ),
        deterministic_context(
            transition_id="transition-claim-troubleshooter-manager-resumed",
            run_id="run-troubleshooter-manager-resumed",
            claim_id="claim-troubleshooter-manager-resumed",
            fencing_token="fence-troubleshooter-manager-resumed",
        ),
    )
    returned = apply_accepted_input(
        claimed,
        runner_observation(
            state=claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-troubleshooter-manager-resumed",
            action_id="simple_loop.troubleshooter.resolved",
            input_id="observe-troubleshooter-resolved-after-cooldown",
            artifact_payload=troubleshooting_report_payload(),
        ),
        deterministic_context(
            transition_id="transition-observe-troubleshooter-resolved-after-cooldown",
            activation_id="activation-returned-manager-2",
        ),
    )
    third_source = apply_accepted_input(
        returned,
        ClaimWork(
            "claim-returned-manager-2",
            activation_id="activation-returned-manager-2",
        ),
        deterministic_context(
            transition_id="transition-claim-returned-manager-2",
            run_id="run-source-retry-3",
            claim_id="claim-source-retry-3",
            fencing_token="fence-source-retry-3",
        ),
    )
    attempt3 = apply_accepted_input(
        third_source,
        runner_observation(
            state=third_source,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-source-retry-3",
            action_id="simple_loop.manager.blocked",
            input_id="observe-manager-blocked-3",
            artifact_payload={},
        ),
        deterministic_context(
            transition_id="transition-observe-manager-blocked-3",
            activation_id="activation-troubleshooter-manager-3",
        ),
    )

    attempt = next(iter(attempt3.recovery_attempts.values()))
    assert attempt.attempt_count == 3
    assert attempt.phase == "quarantine_eligible"
    consumed_wait = attempt3.cooldown_waits[wait.wait_id]
    assert consumed_wait.consumed_input_id == "timer-cooldown-due"

    loaded = persist_and_load_runtime_state(tmp_path, attempt3)

    loaded_wait = loaded.cooldown_waits[wait.wait_id]
    assert loaded_wait == consumed_wait
    duplicate = decide(
        loaded,
        _timer_due("timer-cooldown-again", wait_id=wait.wait_id, observed_at=1901),
        deterministic_context(transition_id="transition-timer-cooldown-again"),
    )
    assert duplicate.accepted is False
    assert duplicate.refusal is not None
    assert duplicate.refusal.reason == "wait_already_consumed"


def test_restart_preserves_lineage_quarantine_and_status(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, quarantined)

    assert loaded.lineage_quarantines == quarantined.lineage_quarantines
    assert loaded.quarantines == {}
    assert loaded.pause is None
    assert _queue_counts(loaded, "work_prompt") == (0, 0, 0, 1)
    assert operator_status(loaded).active_runs == ()
    assert _lineage_live_or_ready_work_item_ids(loaded, "work-prompt") == set()
    status = operator_status(loaded)
    assert len(status.quarantines) == 1
    quarantine = status.quarantines[0]
    assert quarantine.quarantine_kind == "lineage"
    assert quarantine.lineage_id == "work-prompt"
    assert quarantine.policy_id == "simple_loop.blocked_recovery"
    assert quarantine.selected_plan_fingerprint == fingerprint
    assert quarantine.original_source_run_id == "run-source-retry-3"
    assert quarantine.original_source_work_item_id == "work-prompt"
    assert quarantine.emitting_recovery_run_id == "run-source-retry-3"
    assert quarantine.status == "active"


def test_restart_preserves_active_operator_needed_quarantine_and_allows_resume(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _active_operator_needed_quarantined_state(plan, fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")

    loaded = persist_and_load_runtime_state(tmp_path, quarantined)

    assert loaded.lineage_quarantines == quarantined.lineage_quarantines
    assert loaded.recovery_attempts == quarantined.recovery_attempts
    assert loaded.recovery_attempts[
        quarantine.recovery_attempt_record_id
    ].phase == "quarantine_eligible"
    status = operator_status(loaded)
    assert len(status.quarantines) == 1
    assert status.quarantines[0].record_id == quarantine.quarantine_id
    ResumeInput = getattr(operator_api, "OperatorResumeLineageInput")
    transition_input = operator_api.build_resume_lineage(
        loaded,
        ResumeInput(
            input_id="operator-resume-operator-needed-lineage",
            option_id="simple_loop.resume_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator resumed operator-needed lineage",
            payload=None,
        ),
    )
    resumed = apply_accepted_input(
        loaded,
        transition_input,
        deterministic_context(
            transition_id="transition-operator-resume-operator-needed-lineage",
            activation_id="activation-operator-needed-resumed-manager",
        ),
    )

    assert resumed.lineage_quarantines[quarantine.quarantine_id].status == (
        "superseded"
    )
    assert operator_status(resumed).quarantines == ()


def test_restart_refuses_operator_needed_quarantine_with_active_recovery_attempt(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _active_operator_needed_quarantined_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, quarantined)

    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE recovery_attempts SET phase = 'active_recovery'")

    with pytest.raises(
        StorageIntegrityError,
        match="lineage_quarantines active record must match quarantine_eligible",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_preserves_operator_resume_intervention_state(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan, fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    ResumeInput = getattr(operator_api, "OperatorResumeLineageInput")
    transition_input = operator_api.build_resume_lineage(
        quarantined,
        ResumeInput(
            input_id="operator-resume-lineage",
            option_id="simple_loop.resume_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator resumed simple loop lineage",
            payload=None,
        ),
    )
    resumed = apply(
        quarantined,
        decide(
            quarantined,
            transition_input,
            deterministic_context(
                transition_id="transition-operator-resume-lineage",
                activation_id="activation-operator-resumed-manager",
            ),
        ),
    )

    loaded = persist_and_load_runtime_state(tmp_path, resumed)

    assert loaded.lineage_quarantines == resumed.lineage_quarantines
    assert loaded.recovery_attempts == resumed.recovery_attempts
    assert loaded.activations["activation-operator-resumed-manager"] == (
        resumed.activations["activation-operator-resumed-manager"]
    )
    assert getattr(loaded, "operator_interventions") == getattr(
        resumed,
        "operator_interventions",
    )
    assert _queue_counts(loaded, "work_prompt") == (1, 0, 0, 0)
    assert operator_status(loaded).quarantines == ()


def test_restart_preserves_operator_close_intervention_state(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan, fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    CloseInput = getattr(operator_api, "OperatorCloseLineageInput")
    transition_input = operator_api.build_close_lineage(
        quarantined,
        CloseInput(
            input_id="operator-close-lineage",
            option_id="simple_loop.close_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator closed simple loop lineage",
            payload={},
        ),
    )
    closed = apply(
        quarantined,
        decide(
            quarantined,
            transition_input,
            deterministic_context(transition_id="transition-operator-close-lineage"),
        ),
    )

    loaded = persist_and_load_runtime_state(tmp_path, closed)

    assert loaded.lineage_quarantines == closed.lineage_quarantines
    assert loaded.recovery_attempts == closed.recovery_attempts
    assert loaded.closed_work_items == closed.closed_work_items
    assert getattr(loaded, "operator_interventions") == getattr(
        closed,
        "operator_interventions",
    )
    assert _queue_counts(loaded, "work_prompt") == (0, 0, 1, 0)
    assert operator_status(loaded).quarantines == ()


def test_restart_preserves_operator_revise_intervention_state_and_status(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    revised, quarantine = _operator_revised_state(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, revised)

    assert loaded.lineage_quarantines == revised.lineage_quarantines
    assert loaded.recovery_attempts == revised.recovery_attempts
    assert loaded.work_items["work-operator-revised-packet"] == (
        revised.work_items["work-operator-revised-packet"]
    )
    assert loaded.activations["activation-operator-revised-worker"] == (
        revised.activations["activation-operator-revised-worker"]
    )
    assert getattr(loaded, "operator_interventions") == getattr(
        revised,
        "operator_interventions",
    )
    quarantine_after = loaded.lineage_quarantines[quarantine.quarantine_id]
    assert quarantine_after.status == "superseded"
    assert quarantine_after.superseded_input_id == "operator-revise-lineage"
    attempt = next(iter(loaded.recovery_attempts.values()))
    assert attempt.phase == "resolved"
    assert attempt.updated_by_input_id == "operator-revise-lineage"
    assert _queue_counts(loaded, "work_packet") == (1, 0, 0, 0)
    assert _queue_counts(loaded, "work_prompt") == (0, 0, 0, 0)

    status = operator_status(loaded)
    assert status.quarantines == ()
    assert len(status.interventions) == 1
    intervention = status.interventions[0]
    assert intervention.kind == "revise_lineage"
    assert intervention.result == "revised"
    assert intervention.option_id == "simple_loop.revise_lineage"
    assert intervention.policy_id == "simple_loop.blocked_recovery"
    assert intervention.lineage_id == "work-prompt"
    assert intervention.quarantine_id == quarantine.quarantine_id
    assert intervention.recovery_attempt_record_id == attempt.record_id
    assert intervention.recovery_attempt_count == 3
    assert intervention.attempt_effect == "resolve_attempt"
    assert intervention.selected_plan_fingerprint == fingerprint
    assert intervention.actor_kind == "local_operator"
    assert intervention.actor_id == "local-operator-tim"
    assert intervention.target_work_item_id == "work-operator-revised-packet"
    assert intervention.target_activation_id == "activation-operator-revised-worker"
    assert intervention.closed_work_item_ids == ()
    assert intervention.payload_digest.startswith("sha256:")
    assert intervention.payload_reference == (
        "work_item:work-operator-revised-packet:payload"
    )


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        (
            "action_id",
            "missing-action",
            "lineage_quarantines.action_id",
        ),
        (
            "original_source_run_id",
            "missing-run",
            "lineage_quarantines.original_source_run_id must reference runs",
        ),
        (
            "emitting_recovery_run_id",
            "missing-run",
            "lineage_quarantines.emitting_recovery_run_id must reference runs",
        ),
    ),
)
def test_restart_refuses_corrupt_lineage_quarantine_rows(
    tmp_path: Path,
    column_name: str,
    corrupt_value: str,
    expected_message: str,
) -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, quarantined)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE lineage_quarantines SET {column_name} = ?",
            (corrupt_value,),
        )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        ("policy_id", "missing.policy", "operator_interventions.policy_id"),
        ("option_id", "missing.option", "operator_interventions.option_id"),
        ("actor_kind", "runtime", "operator_interventions.actor_kind"),
        ("quarantine_id", "missing-quarantine", "operator_interventions.quarantine_id"),
        (
            "recovery_attempt_record_id",
            "missing-attempt",
            "operator_interventions.recovery_attempt_record_id",
        ),
        (
            "target_work_item_id",
            "missing-work",
            "operator_interventions.target_work_item_id",
        ),
    ),
)
def test_restart_refuses_corrupt_operator_intervention_rows(
    tmp_path: Path,
    column_name: str,
    corrupt_value: str,
    expected_message: str,
) -> None:
    plan, fingerprint = compile_simple_loop()
    quarantined = _lineage_quarantined_state(plan, fingerprint)
    quarantine = _active_lineage_quarantine(quarantined, "work-prompt")
    CloseInput = getattr(operator_api, "OperatorCloseLineageInput")
    transition_input = operator_api.build_close_lineage(
        quarantined,
        CloseInput(
            input_id="operator-close-lineage",
            option_id="simple_loop.close_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator closed simple loop lineage",
            payload={},
        ),
    )
    closed = apply(
        quarantined,
        decide(
            quarantined,
            transition_input,
            deterministic_context(transition_id="transition-operator-close-lineage"),
        ),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, closed)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE operator_interventions SET {column_name} = ?",
            (corrupt_value,),
        )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column_name", "corrupt_value", "expected_message"),
    (
        (
            "target_activation_id",
            "missing-activation",
            "operator_interventions.target_activation_id must reference activations",
        ),
        (
            "payload_reference",
            "work_item:other-work:payload",
            "operator_interventions payload_reference must match revise target",
        ),
        (
            "payload_digest",
            f"sha256:{'0' * 64}",
            "operator_interventions payload_digest must match revise target",
        ),
    ),
)
def test_restart_refuses_corrupt_operator_revise_intervention_rows(
    tmp_path: Path,
    column_name: str,
    corrupt_value: str,
    expected_message: str,
) -> None:
    plan, fingerprint = compile_simple_loop()
    revised, _quarantine = _operator_revised_state(plan, fingerprint)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, revised)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE operator_interventions SET {column_name} = ?",
            (corrupt_value,),
        )

    with pytest.raises(StorageIntegrityError, match=expected_message):
        load_runtime_state(db_path, cas_root)


def test_restart_after_manager_route_resumes_worker_ready_state(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_worker_ready(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.work_items["work-worker"] == state.work_items["work-worker"]
    assert loaded.activations["activation-worker"] == state.activations[
        "activation-worker"
    ]
    assert loaded.artifacts[
        "transition-observe-manager-packet-ready:artifact"
    ] == state.artifacts["transition-observe-manager-packet-ready:artifact"]
    assert loaded.activation_routes == state.activation_routes
    assert loaded.governance_events == state.governance_events
    assert loaded.traces == state.traces
    assert loaded.transitions == state.transitions
    assert (
        loaded.work_items["work-worker"].ref.plan_ref.authority_fingerprint
        == fingerprint
    )
    assert (
        loaded.activations["activation-worker"].plan_ref.authority_fingerprint
        == fingerprint
    )
    assert (
        loaded.runs["run-manager"].run_ref.plan_ref.authority_fingerprint
        == fingerprint
    )

    status = operator_status(loaded)
    assert _queue_counts(loaded, "work_packet") == (1, 0, 0, 0)
    assert status.active_runs == ()

    worker_claimed = apply_accepted_input(
        loaded,
        ClaimWork("claim-worker", activation_id="activation-worker"),
        simple_loop_context("claim-worker"),
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=worker_claimed,
        run_id="run-worker",
    )
    assert dispatch.plan_fingerprint == fingerprint
    assert set(dispatch.work_item_payload) == {"prompt_id", "body", "work_packet"}
    _assert_source_prompt_preserved(dispatch.work_item_payload)
    assert _completion_definition(dispatch.work_item_payload) == (
        "Worker receives the Manager-authored packet."
    )


def test_restart_after_worker_route_resumes_reviewer_ready_state(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_reviewer_ready(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.work_items["work-reviewer"] == state.work_items["work-reviewer"]
    assert loaded.activations["activation-reviewer"] == state.activations[
        "activation-reviewer"
    ]
    assert loaded.artifacts["transition-observe-worker-done:artifact"] == (
        state.artifacts["transition-observe-worker-done:artifact"]
    )
    assert loaded.activation_routes == state.activation_routes
    assert loaded.governance_events == state.governance_events
    assert loaded.traces == state.traces
    assert loaded.transitions == state.transitions
    assert (
        loaded.work_items["work-reviewer"].ref.plan_ref.authority_fingerprint
        == fingerprint
    )
    assert (
        loaded.activations["activation-reviewer"].plan_ref.authority_fingerprint
        == fingerprint
    )
    assert (
        loaded.runs["run-worker"].run_ref.plan_ref.authority_fingerprint
        == fingerprint
    )
    assert str(loaded.activations["activation-reviewer"].stage_kind_id) == (
        "simple_loop.reviewer"
    )

    status = operator_status(loaded)
    assert _queue_counts(loaded, "work_packet") == (1, 0, 0, 0)
    assert status.active_runs == ()

    reviewer_claimed = apply_accepted_input(
        loaded,
        ClaimWork("claim-reviewer", activation_id="activation-reviewer"),
        simple_loop_context("claim-reviewer"),
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=reviewer_claimed,
        run_id="run-reviewer",
    )
    assert dispatch.plan_fingerprint == fingerprint
    assert set(dispatch.work_item_payload) == {
        "prompt_id",
        "body",
        "work_packet",
        "work_result",
    }
    _assert_source_prompt_preserved(dispatch.work_item_payload)
    assert _completion_definition(dispatch.work_item_payload) == (
        "Worker receives the Manager-authored packet."
    )


def test_restart_preserves_partitionless_selected_authority_and_manager_claim(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_ready(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.admitted_plans == state.admitted_plans
    assert loaded.default_plan_ref == state.default_plan_ref
    assert loaded.default_plan_ref is not None
    assert loaded.default_plan_ref.authority_fingerprint == fingerprint
    assert loaded.work_items["work-prompt"] == state.work_items["work-prompt"]
    assert loaded.activations["activation-manager"] == state.activations[
        "activation-manager"
    ]
    loaded_plan = loaded.admitted_plans[fingerprint].selected_plan
    assert (
        stage_kind_by_id(
            loaded_plan,
            "simple_loop.troubleshooter",
        ).partition_id
        is None
    )

    claim_decision = decide(
        loaded,
        ClaimWork("claim-manager", activation_id="activation-manager"),
        simple_loop_context("claim-manager"),
    )
    after_claim = apply(loaded, claim_decision)

    assert claim_decision.accepted is True
    assert (
        after_claim.runs["run-manager"].run_ref.plan_ref.authority_fingerprint
        == fingerprint
    )
    troubleshooter_status = next(
        stage
        for stage in operator_status(after_claim).stage_kinds
        if stage.stage_kind_id == "simple_loop.troubleshooter"
    )
    assert troubleshooter_status.partition_id is None


def test_restart_preserves_simple_loop_reviewer_accepted_close(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_reviewer_accepted(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.runner_observations == state.runner_observations
    assert loaded.artifacts == state.artifacts
    assert loaded.activation_routes == state.activation_routes
    assert loaded.closed_work_items == state.closed_work_items
    assert loaded.governance_events == state.governance_events
    assert loaded.traces == state.traces
    assert loaded.transitions == state.transitions
    reviewer_close = loaded.closed_work_items["work-reviewer"]
    assert reviewer_close.close_kind == "terminal_action"
    assert reviewer_close.action_id is not None
    assert reviewer_close.action_id.value == "simple_loop.reviewer.accepted"
    assert reviewer_close.source_run_id == "run-reviewer"
    assert any(
        observation.run_id == "run-reviewer"
        for observation in loaded.runner_observations.values()
    )
    assert _lineage_live_or_ready_work_item_ids(loaded, "work-prompt") == set()

    status = operator_status(loaded)
    work_packet = next(
        family
        for family in status.queue_families
        if family.queue_family_id == "work_packet"
    )
    assert (
        work_packet.ready_count,
        work_packet.active_count,
        work_packet.closed_count,
        work_packet.quarantined_count,
    ) == (0, 0, 1, 0)
    assert _queue_counts(loaded, "work_prompt") == (0, 0, 0, 0)
    assert status.active_runs == ()


def test_restart_after_worker_detail_request_resumes_manager_ready_state(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_detail_ready(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.work_items["work-manager-detail"] == state.work_items[
        "work-manager-detail"
    ]
    assert loaded.activations["activation-manager-detail"] == state.activations[
        "activation-manager-detail"
    ]
    assert loaded.artifacts[
        "transition-observe-worker-insufficient-spec:artifact"
    ] == state.artifacts["transition-observe-worker-insufficient-spec:artifact"]
    assert loaded.activation_routes == state.activation_routes
    assert loaded.governance_events == state.governance_events
    assert loaded.traces == state.traces
    assert loaded.transitions == state.transitions
    assert loaded.work_items["work-manager-detail"].lineage_id == "work-prompt"
    assert loaded.activations["activation-manager-detail"].lineage_id == (
        "work-prompt"
    )
    assert (
        loaded.work_items[
            "work-manager-detail"
        ].ref.plan_ref.authority_fingerprint
        == fingerprint
    )
    assert (
        loaded.activations[
            "activation-manager-detail"
        ].plan_ref.authority_fingerprint
        == fingerprint
    )
    assert str(loaded.activations["activation-manager-detail"].stage_kind_id) == (
        "simple_loop.manager"
    )
    assert loaded.activations["activation-manager-detail"].graph_node_id == (
        "simple_loop.manager.detail_request"
    )
    assert loaded.artifacts[
        "transition-observe-worker-insufficient-spec:artifact"
    ].schema_id.value == "simple_loop.detail_request"
    assert loaded.activation_routes[-1].action_id.value == (
        "simple_loop.worker.insufficient_spec"
    )
    assert _queue_counts(loaded, "work_packet") == (1, 0, 0, 0)
    assert operator_status(loaded).active_runs == ()

    manager_claimed = apply_accepted_input(
        loaded,
        ClaimWork(
            "claim-manager-detail",
            activation_id="activation-manager-detail",
        ),
        simple_loop_context("claim-manager-detail"),
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=manager_claimed,
        run_id="run-manager-detail",
    )
    assert dispatch.plan_fingerprint == fingerprint
    assert set(dispatch.work_item_payload) == {
        "prompt_id",
        "body",
        "work_packet",
        "detail_request",
    }
    _assert_source_prompt_preserved(dispatch.work_item_payload)
    assert _completion_definition(dispatch.work_item_payload) == (
        "Worker receives the Manager-authored packet."
    )


def test_restart_preserves_simple_loop_gap_route_history_and_status(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_gap_worker_ready(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.runner_observations == state.runner_observations
    assert loaded.artifacts == state.artifacts
    assert loaded.activation_routes == state.activation_routes
    assert loaded.work_items["work-worker-gap"] == state.work_items["work-worker-gap"]
    assert loaded.activations["activation-worker-gap"] == state.activations[
        "activation-worker-gap"
    ]
    assert loaded.governance_events == state.governance_events
    assert loaded.traces == state.traces
    assert loaded.transitions == state.transitions
    assert loaded.work_items["work-worker-gap"].lineage_id == "work-prompt"
    assert loaded.activations["activation-worker-gap"].lineage_id == "work-prompt"
    assert loaded.artifacts[
        "transition-observe-reviewer-gaps-found:artifact"
    ].schema_id.value == "simple_loop.gap_packet"
    assert loaded.activation_routes[-1].action_id.value == (
        "simple_loop.reviewer.gaps_found"
    )
    assert _queue_counts(loaded, "gap_packet") == (1, 0, 0, 0)

    worker_claimed = apply_accepted_input(
        loaded,
        ClaimWork("claim-worker-gap", activation_id="activation-worker-gap"),
        simple_loop_context("claim-worker-gap"),
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=worker_claimed,
        run_id="run-worker-gap",
    )
    assert dispatch.plan_fingerprint == fingerprint
    assert set(dispatch.work_item_payload) == {
        "prompt_id",
        "body",
        "work_packet",
        "latest_work_result",
        "gap_packet",
    }
    _assert_source_prompt_preserved(dispatch.work_item_payload)
    assert _completion_definition(dispatch.work_item_payload) == (
        "Worker receives the Manager-authored packet."
    )


def test_restart_preserves_simple_loop_incident_route_history_and_status(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = _manager_incident_ready_after_counter_threshold(plan, fingerprint)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.runner_observations == state.runner_observations
    assert loaded.artifacts == state.artifacts
    assert loaded.activation_routes == state.activation_routes
    assert loaded.work_items["work-manager-incident"] == state.work_items[
        "work-manager-incident"
    ]
    assert loaded.activations["activation-manager-incident"] == state.activations[
        "activation-manager-incident"
    ]
    assert loaded.governance_events == state.governance_events
    assert loaded.traces == state.traces
    assert loaded.transitions == state.transitions
    assert loaded.work_items["work-manager-incident"].lineage_id == "work-prompt"
    assert loaded.activations["activation-manager-incident"].lineage_id == (
        "work-prompt"
    )
    assert loaded.artifacts[
        "transition-observe-reviewer-incident-required:artifact"
    ].schema_id.value == "simple_loop.incident_report"
    assert loaded.activation_routes[-1].action_id.value == (
        "simple_loop.reviewer.incident_required"
    )
    assert _queue_counts(loaded, "incident_report") == (1, 0, 0, 0)

    manager_claimed = apply_accepted_input(
        loaded,
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
    assert dispatch.plan_fingerprint == fingerprint
    assert set(dispatch.work_item_payload) == {
        "prompt_id",
        "body",
        "work_packet",
        "latest_work_result",
        "incident_report",
    }
    _assert_source_prompt_preserved(dispatch.work_item_payload)
    assert _completion_definition(dispatch.work_item_payload) == (
        "Worker receives the Manager-authored packet."
    )


def test_restart_preserves_active_operator_wait_and_resolves_after_load(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = _manager_detail_wait_state(plan, fingerprint)
    wait = next(iter(state.operator_waits.values()))

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.operator_waits == state.operator_waits
    status = operator_status(loaded)
    assert len(status.operator_waits) == 1
    assert status.operator_waits[0].wait_id == wait.wait_id
    assert status.operator_waits[0].status == "active"

    ResumeInput = getattr(operator_api, "OperatorResumeWaitInput")
    resume = operator_api.build_resume_wait(
        loaded,
        ResumeInput(
            input_id="operator-resume-wait-after-load",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
        ),
    )
    decision = decide(
        loaded,
        resume,
        deterministic_context(
            transition_id="transition-operator-resume-wait-after-load",
            activation_id="activation-manager-resumed-after-load",
        ),
    )
    after = apply(loaded, decision)

    assert decision.accepted is True
    assert "mutation.record_operator_wait" in mutation_kinds(decision)
    assert "mutation.create_activation" in mutation_kinds(decision)
    assert after.operator_waits[wait.wait_id].status == "resolved"
    assert after.operator_waits[wait.wait_id].resolved_input_id == (
        "operator-resume-wait-after-load"
    )
    assert after.activations["activation-manager-resumed-after-load"].work_item_id == (
        wait.source_work_item_id
    )


def test_restart_preserves_resolved_operator_wait_and_refuses_second_resolution(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = _manager_detail_wait_state(plan, fingerprint)
    wait = next(iter(state.operator_waits.values()))
    ResumeInput = getattr(operator_api, "OperatorResumeWaitInput")
    resume = operator_api.build_resume_wait(
        state,
        ResumeInput(
            input_id="operator-resume-wait-before-load",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
        ),
    )
    resolved = apply_accepted_input(
        state,
        resume,
        deterministic_context(
            transition_id="transition-operator-resume-wait-before-load",
            activation_id="activation-manager-resumed-before-load",
        ),
    )

    loaded = persist_and_load_runtime_state(tmp_path, resolved)

    assert loaded.operator_waits[wait.wait_id].status == "resolved"
    assert loaded.operator_waits[wait.wait_id].resolved_input_id == (
        "operator-resume-wait-before-load"
    )

    ResumeWait = getattr(transition_contracts, "OperatorResumeWait")
    duplicate = ResumeWait(
        "operator-resume-wait-after-resolved-load",
        selected_plan_ref=wait.selected_plan_ref,
        wait_id=wait.wait_id,
        lineage_id=wait.lineage_id,
        actor_id="local-operator-tim",
        actor_kind="local_operator",
        payload={},
    )
    decision = decide(
        loaded,
        duplicate,
        deterministic_context(
            transition_id="transition-operator-resume-wait-after-resolved-load",
        ),
    )
    after = apply(loaded, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "operator_wait_not_active"
    assert "mutation.record_operator_wait" not in mutation_kinds(decision)
    assert after.operator_waits[wait.wait_id].status == "resolved"


def test_restart_allows_resolved_operator_wait_source_to_close_by_compiled_action(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_simple_loop()
    state = _manager_detail_wait_state(plan, fingerprint)
    wait = next(iter(state.operator_waits.values()))
    ResumeInput = getattr(operator_api, "OperatorResumeWaitInput")
    resume = operator_api.build_resume_wait(
        state,
        ResumeInput(
            input_id="operator-resume-wait-before-compiled-close",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
        ),
    )
    resumed = apply_accepted_input(
        state,
        resume,
        deterministic_context(
            transition_id="transition-operator-resume-wait-before-compiled-close",
            activation_id="activation-manager-resumed-before-compiled-close",
        ),
    )
    claimed = apply_accepted_input(
        resumed,
        ClaimWork(
            "claim-manager-resumed-before-compiled-close",
            activation_id="activation-manager-resumed-before-compiled-close",
        ),
        deterministic_context(
            transition_id="transition-claim-manager-resumed-before-compiled-close",
            run_id="run-manager-resumed-before-compiled-close",
            claim_id="claim-manager-resumed-before-compiled-close",
            fencing_token="fence-manager-resumed-before-compiled-close",
        ),
    )
    close_decision = decide(
        claimed,
        runner_observation(
            state=claimed,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager-resumed-before-compiled-close",
            action_id="simple_loop.manager.invalid_prompt",
            input_id="observe-manager-invalid-prompt-after-resume",
            artifact_payload={},
        ),
        deterministic_context(
            transition_id="transition-observe-manager-invalid-prompt-after-resume",
        ),
    )
    closed = apply(claimed, close_decision)

    assert close_decision.accepted is True
    assert "mutation.close_work_item" in mutation_kinds(close_decision)
    assert wait.source_work_item_id in closed.closed_work_items
    assert closed.operator_waits[wait.wait_id].status == "resolved"

    loaded = persist_and_load_runtime_state(tmp_path, closed)

    assert loaded.operator_waits[wait.wait_id] == closed.operator_waits[wait.wait_id]
    assert wait.source_work_item_id in loaded.closed_work_items
    resolved_wait = loaded.operator_waits[wait.wait_id]
    closed_source = loaded.closed_work_items[wait.source_work_item_id]
    assert closed_source.source_run_id != wait.source_run_id
    assert closed_source.source_run_id is not None
    assert loaded.runs[closed_source.source_run_id].activation_id == (
        resolved_wait.target_activation_id
    )
    assert closed_source.action_id != wait.source_action_id
    assert str(closed_source.action_id) == "simple_loop.manager.invalid_prompt"
    assert closed_source.created_by_input_id not in {
        wait.created_input_id,
        resolved_wait.resolved_input_id,
    }
