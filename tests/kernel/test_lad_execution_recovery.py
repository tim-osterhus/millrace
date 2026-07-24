from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

import millrace.operator as operator_api
from millrace.contracts import ActionId, QueueFamilyId
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import CounterRecord, RuntimeState
from millrace.contracts.transition import OperatorReviseLineage, TimerDue
from millrace.kernel import apply, decide
from millrace.kernel.observation_policy import (
    ObservationPolicyDiagnostic,
    authenticate_runner_observation,
)
from millrace.operator import operator_status
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.lad_execution import (
    BUILDER_SUMMARY_SCHEMA_ID,
    REPORT_SCHEMA_ID,
    STAGE_RESULT_SCHEMA_ID,
    artifact_payload,
    bootstrap_builder_claim,
    bootstrap_builder_ready,
    claim_activation,
    compile_lad,
    lad_context,
    mutation_kinds,
    runner_observation,
    runtime_failure_exhausted_state,
)


def _decide_observation(
    state: RuntimeState,
    *,
    plan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    schema_id: str = STAGE_RESULT_SCHEMA_ID,
    target_work_item_id: str | None = None,
    target_activation_id: str | None = None,
    marker: str | None = None,
    artifact: Mapping[str, AuthorityValue] | None = None,
    observed_at: int | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
):
    return decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=run_id,
            action_id=action_id,
            input_id=input_id,
            marker=marker,
            observed_at=observed_at,
            observation_payload_overrides=observation_payload_overrides,
            artifact_payload=artifact or artifact_payload(schema_id),
        ),
        lad_context(
            input_id,
            work_item_id=target_work_item_id,
            activation_id=target_activation_id,
        ),
    )


def _apply_observation(
    state: RuntimeState,
    *,
    plan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    schema_id: str = STAGE_RESULT_SCHEMA_ID,
    target_work_item_id: str | None = None,
    target_activation_id: str | None = None,
    marker: str | None = None,
    observed_at: int | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
) -> tuple[RuntimeState, object]:
    decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        action_id=action_id,
        input_id=input_id,
        schema_id=schema_id,
        target_work_item_id=target_work_item_id,
        target_activation_id=target_activation_id,
        marker=marker,
        observed_at=observed_at,
        observation_payload_overrides=observation_payload_overrides,
    )
    assert decision.accepted is True
    return apply(state, decision), decision


def _counter_for(state: RuntimeState, counter_id: str) -> CounterRecord:
    matches = tuple(
        record
        for record in state.counters.values()
        if str(record.counter_id) == counter_id
    )
    assert len(matches) == 1
    return matches[0]


def test_lad_status_projects_ready_active_and_closed_selected_state() -> None:
    plan, fingerprint = compile_lad()
    ready = bootstrap_builder_ready(plan, fingerprint)

    ready_status = operator_status(ready)
    ready_task = next(
        family
        for family in ready_status.queue_families
        if family.queue_family_id == "task"
    )
    assert (ready_task.ready_count, ready_task.active_count) == (1, 0)
    assert ready_task.display_name == "Task"
    assert ready_status.active_runs == ()
    assert any(
        stage.stage_kind_id == "lad_builder"
        and stage.display_name == "LAD Builder"
        for stage in ready_status.stage_kinds
    )

    active = claim_activation(
        ready,
        activation_id="activation-builder",
        run_id="run-builder",
        input_id="claim-builder",
    )
    active_status = operator_status(active)
    active_task = next(
        family
        for family in active_status.queue_families
        if family.queue_family_id == "task"
    )
    assert (active_task.ready_count, active_task.active_count) == (0, 1)
    assert len(active_status.active_runs) == 1
    active_run = active_status.active_runs[0]
    assert active_run.run_id == "run-builder"
    assert active_run.queue_family_id == "task"
    assert active_run.graph_node_id == "execution.lad.builder.start"
    assert active_run.stage_kind_id == "lad_builder"

    closed = runtime_failure_exhausted_state(plan, fingerprint)
    closed_status = operator_status(closed)
    closed_task = next(
        family
        for family in closed_status.queue_families
        if family.queue_family_id == "task"
    )
    assert (closed_task.ready_count, closed_task.active_count) == (0, 0)
    assert closed_task.closed_count == 1
    assert closed_status.active_runs == ()


def _route_and_claim(
    state: RuntimeState,
    *,
    plan,
    fingerprint: str,
    source_run_id: str,
    action_id: str,
    input_id: str,
    target_stage: str,
    schema_id: str = STAGE_RESULT_SCHEMA_ID,
) -> RuntimeState:
    state, _decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=source_run_id,
        action_id=action_id,
        input_id=input_id,
        schema_id=schema_id,
        target_work_item_id=f"work-{target_stage}",
        target_activation_id=f"activation-{target_stage}",
    )
    return claim_activation(
        state,
        activation_id=f"activation-{target_stage}",
        run_id=f"run-{target_stage}",
        input_id=f"claim-{target_stage}",
    )


def _consultant_claim_after_troubleshooter_blocked(
    plan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )
    state, _troubleshooter_blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.route_troubleshooter_blocked",
        input_id="observe-troubleshooter-blocked",
        target_work_item_id="work-consultant",
        target_activation_id="activation-consultant",
    )
    return claim_activation(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )


def _builder_after_consultant_return_state(plan, fingerprint: str) -> RuntimeState:
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _first_block = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )
    state, _return = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.return_troubleshooter_complete",
        input_id="observe-troubleshooter-complete",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-builder-resume",
        target_activation_id="activation-builder-resume",
    )
    state = claim_activation(
        state,
        activation_id="activation-builder-resume",
        run_id="run-builder-resume",
        input_id="claim-builder-resume",
    )
    state, _threshold = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder-resume",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked-threshold",
        target_activation_id="activation-consultant",
    )
    state = claim_activation(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )
    state, _consultant_return = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.return_consultant_recovered",
        input_id="observe-consultant-recovered",
        schema_id=REPORT_SCHEMA_ID,
        target_activation_id="activation-builder-after-consultant",
    )
    state = claim_activation(
        state,
        activation_id="activation-builder-after-consultant",
        run_id="run-builder-after-consultant",
        input_id="claim-builder-after-consultant",
    )
    return state


def _lineage_quarantined_state(plan, fingerprint: str) -> RuntimeState:
    state = _builder_after_consultant_return_state(plan, fingerprint)
    state, cooldown = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder-after-consultant",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked-cooldown",
        observed_at=1000,
    )
    assert "mutation.record_cooldown_wait" in mutation_kinds(cooldown)
    wait = next(iter(state.cooldown_waits.values()))
    decision = decide(
        state,
        TimerDue("timer-lad-blocked-cooldown", wait_id=wait.wait_id, observed_at=1900),
        lad_context(
            "timer-lad-blocked-cooldown",
            activation_id="activation-consultant-after-cooldown",
        ),
    )
    assert decision.accepted is True
    state = apply(state, decision)
    state = claim_activation(
        state,
        activation_id="activation-consultant-after-cooldown",
        run_id="run-consultant-after-cooldown",
        input_id="claim-consultant-after-cooldown",
    )
    state, _consultant_return = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant-after-cooldown",
        action_id="execution.return_consultant_recovered",
        input_id="observe-consultant-after-cooldown-recovered",
        schema_id=REPORT_SCHEMA_ID,
        target_activation_id="activation-builder-after-cooldown",
    )
    state = claim_activation(
        state,
        activation_id="activation-builder-after-cooldown",
        run_id="run-builder-after-cooldown",
        input_id="claim-builder-after-cooldown",
    )
    state, _quarantine = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder-after-cooldown",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked-quarantine",
    )
    return state


def _active_quarantine(state: RuntimeState):
    active = tuple(
        quarantine
        for quarantine in state.lineage_quarantines.values()
        if quarantine.status == "active"
    )
    assert len(active) == 1
    return active[0]


def _persist_and_load(tmp_path: Path, state: RuntimeState) -> RuntimeState:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, ContentAddressedByteStore(cas_root))
    finally:
        store.close()
    store = SQLiteRuntimeStore.open(db_path)
    try:
        return store.load_runtime_state(ContentAddressedByteStore(cas_root))
    finally:
        store.close()


def _assert_threshold_action_not_runner_declared(plan, action_id: str) -> None:
    action = next(
        item for item in plan.terminal_actions if str(item.id) == action_id
    )
    stage = next(
        item for item in plan.stage_kinds if item.id == action.stage_kind_id
    )
    outcome = next(
        item for item in plan.terminal_outcomes if item.id == action.outcome_id
    )

    assert outcome.marker == ""
    assert action.outcome_id not in stage.declared_outcome_ids


def _first_builder_blocked_state(plan, fingerprint: str) -> RuntimeState:
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    return state


def _builder_observation(state: RuntimeState):
    return next(
        observation
        for observation in state.runner_observations.values()
        if observation.created_by_input_id == "observe-builder-blocked"
    )


def test_observation_authentication_refuses_unreached_selected_threshold_action(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_lad()
    state = _first_builder_blocked_state(plan, fingerprint)
    threshold_action_id = ActionId("execution.escalate_builder_blocked_exhausted")
    drifted = replace(
        state,
        governance_events=tuple(
            replace(event, action_id=threshold_action_id)
            if event.input_id == "observe-builder-blocked"
            else event
            for event in state.governance_events
        ),
        traces=tuple(
            replace(trace, action_id=threshold_action_id)
            if trace.input_id == "observe-builder-blocked"
            else trace
            for trace in state.traces
        ),
    )

    authenticated = authenticate_runner_observation(
        drifted,
        _builder_observation(drifted),
    )
    assert isinstance(authenticated, ObservationPolicyDiagnostic)
    assert authenticated.reason_code == "audit_authority"
    with pytest.raises(StorageIntegrityError, match="audit_authority"):
        _persist_and_load(tmp_path, drifted)


def test_observation_authentication_refuses_source_queue_authority_drift(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_lad()
    state = _first_builder_blocked_state(plan, fingerprint)
    run = state.runs["run-builder"]
    work_item = state.work_items[run.work_item_id]
    activation = state.activations[run.activation_id]
    drifted = replace(
        state,
        work_items={
            **state.work_items,
            work_item.ref.work_item_id: replace(
                work_item,
                queue_family_id=QueueFamilyId("wrong.queue"),
            ),
        },
        activations={
            **state.activations,
            activation.activation_id: replace(
                activation,
                queue_family_id=QueueFamilyId("wrong.queue"),
            ),
        },
    )

    authenticated = authenticate_runner_observation(
        drifted,
        _builder_observation(drifted),
    )
    assert isinstance(authenticated, ObservationPolicyDiagnostic)
    assert authenticated.reason_code == "selected_source_authority"
    assert authenticated.detail == "queue_family"
    with pytest.raises(StorageIntegrityError, match="queue_family"):
        _persist_and_load(tmp_path, drifted)


def test_builder_blocked_threshold_refuses_early_and_routes_to_consultant() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)

    _assert_threshold_action_not_runner_declared(
        plan,
        "execution.escalate_builder_blocked_exhausted",
    )

    state, first_block = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    assert "mutation.record_counter" in mutation_kinds(first_block)
    assert _counter_for(
        state,
        "execution.troubleshoot_attempt_count.builder",
    ).value == 1
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )
    state, _return = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.return_troubleshooter_complete",
        input_id="observe-troubleshooter-complete",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-builder-resume",
        target_activation_id="activation-builder-resume",
    )
    state = claim_activation(
        state,
        activation_id="activation-builder-resume",
        run_id="run-builder-resume",
        input_id="claim-builder-resume",
    )

    state, threshold = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder-resume",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked-threshold",
        target_activation_id="activation-consultant",
    )

    assert "mutation.record_recovery_attempt" in mutation_kinds(threshold)
    assert "mutation.route_activation" in mutation_kinds(threshold)
    assert _counter_for(
        state,
        "execution.troubleshoot_attempt_count.builder",
    ).value == 2
    recovery_attempt = next(iter(state.recovery_attempts.values()))
    assert str(recovery_attempt.policy_id) == "execution.blocked_recovery"
    assert recovery_attempt.recovery_action_id == ActionId(
        "execution.escalate_builder_blocked_exhausted"
    )
    assert recovery_attempt.phase == "active_recovery"
    assert str(state.activations["activation-consultant"].stage_kind_id) == (
        "lad_consultant"
    )


def test_lad_second_blocked_recovery_records_cooldown_before_quarantine() -> None:
    plan, fingerprint = compile_lad()
    state = _builder_after_consultant_return_state(plan, fingerprint)

    state, cooldown = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder-after-consultant",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked-cooldown",
        observed_at=2000,
    )

    assert "mutation.record_cooldown_wait" in mutation_kinds(cooldown)
    assert "mutation.record_lineage_quarantine" not in mutation_kinds(cooldown)
    assert "mutation.route_activation" not in mutation_kinds(cooldown)
    wait = next(iter(state.cooldown_waits.values()))
    assert wait.attempt_count == 2
    assert str(wait.policy_id) == "execution.blocked_recovery"
    assert str(wait.target_stage_kind_id) == "lad_consultant"
    assert wait.due_at == 2000 + 900


def test_troubleshooter_complete_uses_selected_resume_stage_metadata() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )

    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.return_troubleshooter_complete",
        input_id="observe-troubleshooter-complete-to-checker",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-checker-from-troubleshooter",
        target_activation_id="activation-checker-from-troubleshooter",
        observation_payload_overrides={"resume_stage": "checker"},
    )

    assert "mutation.route_activation" in mutation_kinds(decision)
    activation = state.activations["activation-checker-from-troubleshooter"]
    assert str(activation.stage_kind_id) == "lad_checker"
    assert activation.graph_node_id == "execution.lad.checker.start"


def test_consultant_complete_uses_selected_target_stage_metadata() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )
    state, _troubleshooter_blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.route_troubleshooter_blocked",
        input_id="observe-troubleshooter-blocked",
        target_work_item_id="work-consultant",
        target_activation_id="activation-consultant",
    )
    state = claim_activation(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )

    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.route_consultant_complete",
        input_id="observe-consultant-complete-to-builder",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-builder-from-consultant",
        target_activation_id="activation-builder-from-consultant",
        observation_payload_overrides={"target_stage": "builder"},
    )

    assert "mutation.route_activation" in mutation_kinds(decision)
    activation = state.activations["activation-builder-from-consultant"]
    assert str(activation.stage_kind_id) == "lad_builder"
    assert activation.graph_node_id == "execution.lad.builder.start"


def test_consultant_complete_refuses_unselected_dynamic_resume_target() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )
    state, _troubleshooter_blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.route_troubleshooter_blocked",
        input_id="observe-troubleshooter-blocked",
        target_work_item_id="work-consultant",
        target_activation_id="activation-consultant",
    )
    state = claim_activation(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )

    decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.route_consultant_complete",
        input_id="observe-consultant-complete-to-consultant",
        schema_id=REPORT_SCHEMA_ID,
        observation_payload_overrides={"target_stage": "consultant"},
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_dynamic_route_target"


def test_lad_base_refuses_integrator_dynamic_resume_target() -> None:
    plan, fingerprint = compile_lad()
    state = _consultant_claim_after_troubleshooter_blocked(plan, fingerprint)

    decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.route_consultant_complete",
        input_id="observe-consultant-complete-to-unselected-integrator",
        schema_id=REPORT_SCHEMA_ID,
        observation_payload_overrides={"target_stage": "integrator"},
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_dynamic_route_target"


def test_lad_integrator_troubleshooter_complete_can_resume_integrator() -> None:
    plan, fingerprint = compile_lad(integrator=True)
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )

    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.return_troubleshooter_complete",
        input_id="observe-troubleshooter-complete-to-integrator",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-integrator-from-troubleshooter",
        target_activation_id="activation-integrator-from-troubleshooter",
        observation_payload_overrides={"resume_stage": "integrator"},
    )

    assert "mutation.route_activation" in mutation_kinds(decision)
    activation = state.activations["activation-integrator-from-troubleshooter"]
    assert str(activation.stage_kind_id) == "lad_integrator"
    assert activation.graph_node_id == "execution.lad_integrator.integrator.start"


def test_lad_integrator_consultant_complete_can_resume_integrator() -> None:
    plan, fingerprint = compile_lad(integrator=True)
    state = _consultant_claim_after_troubleshooter_blocked(plan, fingerprint)

    state, decision = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.route_consultant_complete",
        input_id="observe-consultant-complete-to-integrator",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-integrator-from-consultant",
        target_activation_id="activation-integrator-from-consultant",
        observation_payload_overrides={"target_stage": "integrator"},
    )

    assert "mutation.route_activation" in mutation_kinds(decision)
    activation = state.activations["activation-integrator-from-consultant"]
    assert str(activation.stage_kind_id) == "lad_integrator"
    assert activation.graph_node_id == "execution.lad_integrator.integrator.start"


def test_consultant_complete_refuses_conflicting_dynamic_resume_metadata() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    state, _blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.route_builder_blocked",
        input_id="observe-builder-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )
    state, _troubleshooter_blocked = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.route_troubleshooter_blocked",
        input_id="observe-troubleshooter-blocked",
        target_work_item_id="work-consultant",
        target_activation_id="activation-consultant",
    )
    state = claim_activation(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )

    decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.route_consultant_complete",
        input_id="observe-consultant-complete-conflicting-targets",
        schema_id=REPORT_SCHEMA_ID,
        observation_payload_overrides={
            "target_stage": "builder",
            "resume_stage": "checker",
        },
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_dynamic_route_target"


@pytest.mark.parametrize(
    ("target_stage", "input_id"),
    (
        ("planner", "observe-consultant-complete-to-unknown-target"),
        (7, "observe-consultant-complete-to-non-string-target"),
    ),
)
def test_consultant_complete_refuses_invalid_dynamic_resume_metadata(
    target_stage: object,
    input_id: str,
) -> None:
    plan, fingerprint = compile_lad()
    state = _consultant_claim_after_troubleshooter_blocked(plan, fingerprint)

    decision = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        action_id="execution.route_consultant_complete",
        input_id=input_id,
        schema_id=REPORT_SCHEMA_ID,
        observation_payload_overrides={"target_stage": target_stage},
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_dynamic_route_target"


def test_checker_fix_threshold_routes_from_ordinary_fix_needed() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-complete",
        target_stage="checker",
    )

    state, first_fix = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-checker",
        action_id="execution.route_checker_fix_needed",
        input_id="observe-checker-fix-needed",
        target_work_item_id="work-fixer",
        target_activation_id="activation-fixer",
    )
    assert "mutation.record_counter" in mutation_kinds(first_fix)
    assert _counter_for(state, "execution.fix_cycle_count.checker").value == 1
    state = claim_activation(
        state,
        activation_id="activation-fixer",
        run_id="run-fixer",
        input_id="claim-fixer",
    )
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-fixer",
        action_id="execution.route_fixer_blocked",
        input_id="observe-fixer-blocked",
        target_stage="troubleshooter",
    )
    state, _resume = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.return_troubleshooter_complete",
        input_id="observe-troubleshooter-complete-to-checker",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-checker-again",
        target_activation_id="activation-checker-again",
        observation_payload_overrides={"resume_stage": "checker"},
    )
    state = claim_activation(
        state,
        activation_id="activation-checker-again",
        run_id="run-checker-again",
        input_id="claim-checker-again",
    )

    state, threshold = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-checker-again",
        action_id="execution.route_checker_fix_needed",
        input_id="observe-checker-fix-needed-threshold",
        target_activation_id="activation-checker-fix-threshold-troubleshooter",
    )

    assert "mutation.record_recovery_attempt" in mutation_kinds(threshold)
    assert "mutation.route_activation" in mutation_kinds(threshold)
    assert _counter_for(state, "execution.fix_cycle_count.checker").value == 2
    recovery_attempt = next(iter(state.recovery_attempts.values()))
    assert str(recovery_attempt.policy_id) == "execution.fix_needed_recovery"
    assert recovery_attempt.recovery_action_id == ActionId(
        "execution.escalate_checker_fix_exhausted"
    )
    assert str(
        state.activations[
            "activation-checker-fix-threshold-troubleshooter"
        ].stage_kind_id
    ) == "lad_troubleshooter"


def test_doublechecker_fix_threshold_routes_to_troubleshooter() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-complete",
        target_stage="checker",
    )
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-checker",
        action_id="execution.route_checker_fix_needed",
        input_id="observe-checker-fix-needed",
        target_stage="fixer",
    )
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-fixer",
        action_id="execution.route_fixer_complete",
        input_id="observe-fixer-complete",
        target_stage="doublechecker",
    )

    _assert_threshold_action_not_runner_declared(
        plan,
        "execution.escalate_doublechecker_fix_exhausted",
    )

    state, first_fix = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-doublechecker",
        action_id="execution.route_doublechecker_fix_needed",
        input_id="observe-doublechecker-fix-needed",
        target_work_item_id="work-fixer-again",
        target_activation_id="activation-fixer-again",
    )
    assert "mutation.record_counter" in mutation_kinds(first_fix)
    assert _counter_for(
        state,
        "execution.fix_cycle_count.doublechecker",
    ).value == 1
    state = claim_activation(
        state,
        activation_id="activation-fixer-again",
        run_id="run-fixer-again",
        input_id="claim-fixer-again",
    )
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-fixer-again",
        action_id="execution.route_fixer_complete",
        input_id="observe-fixer-again-complete",
        target_stage="doublechecker-again",
    )

    state, threshold = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-doublechecker-again",
        action_id="execution.route_doublechecker_fix_needed",
        input_id="observe-doublechecker-fix-threshold",
        target_activation_id="activation-troubleshooter",
    )

    assert "mutation.record_recovery_attempt" in mutation_kinds(threshold)
    assert "mutation.route_activation" in mutation_kinds(threshold)
    assert _counter_for(
        state,
        "execution.fix_cycle_count.doublechecker",
    ).value == 2
    recovery_attempt = next(iter(state.recovery_attempts.values()))
    assert str(recovery_attempt.policy_id) == "execution.fix_needed_recovery"
    assert recovery_attempt.recovery_action_id == ActionId(
        "execution.escalate_doublechecker_fix_exhausted"
    )
    assert recovery_attempt.phase == "active_recovery"
    assert str(state.activations["activation-troubleshooter"].stage_kind_id) == (
        "lad_troubleshooter"
    )


def test_lad_integrator_repeated_ordinary_blocked_thresholds_to_consultant() -> None:
    plan, fingerprint = compile_lad(integrator=True)
    state = bootstrap_builder_claim(plan, fingerprint)
    state = _route_and_claim(
        state,
        plan=plan,
        fingerprint=fingerprint,
        source_run_id="run-builder",
        action_id="execution.route_builder_complete",
        input_id="observe-builder-complete",
        target_stage="integrator",
        schema_id=BUILDER_SUMMARY_SCHEMA_ID,
    )

    state, first_block = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-integrator",
        action_id="execution.route_integrator_blocked",
        input_id="observe-integrator-blocked",
        target_work_item_id="work-troubleshooter",
        target_activation_id="activation-troubleshooter",
    )
    assert "mutation.record_counter" in mutation_kinds(first_block)
    assert _counter_for(
        state,
        "execution.troubleshoot_attempt_count.integrator",
    ).value == 1
    state = claim_activation(
        state,
        activation_id="activation-troubleshooter",
        run_id="run-troubleshooter",
        input_id="claim-troubleshooter",
    )
    state, _resume = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-troubleshooter",
        action_id="execution.return_troubleshooter_complete",
        input_id="observe-troubleshooter-complete-to-integrator",
        schema_id=REPORT_SCHEMA_ID,
        target_work_item_id="work-integrator-resume",
        target_activation_id="activation-integrator-resume",
        observation_payload_overrides={"resume_stage": "integrator"},
    )
    assert str(state.activations["activation-integrator-resume"].stage_kind_id) == (
        "lad_integrator"
    )
    state = claim_activation(
        state,
        activation_id="activation-integrator-resume",
        run_id="run-integrator-resume",
        input_id="claim-integrator-resume",
    )

    state, threshold = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-integrator-resume",
        action_id="execution.route_integrator_blocked",
        input_id="observe-integrator-blocked-threshold",
        target_activation_id="activation-integrator-blocked-consultant",
    )

    assert "mutation.record_recovery_attempt" in mutation_kinds(threshold)
    assert "mutation.route_activation" in mutation_kinds(threshold)
    assert _counter_for(
        state,
        "execution.troubleshoot_attempt_count.integrator",
    ).value == 2
    recovery_attempt = next(iter(state.recovery_attempts.values()))
    assert str(recovery_attempt.policy_id) == "execution.blocked_recovery"
    assert recovery_attempt.recovery_action_id == ActionId(
        "execution.escalate_integrator_blocked_exhausted"
    )
    assert str(
        state.activations[
            "activation-integrator-blocked-consultant"
        ].stage_kind_id
    ) == "lad_consultant"


def test_runtime_failure_recovery_routes_then_threshold_blocks() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)

    early = _decide_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.close_builder_runtime_failure_exhausted",
        input_id="observe-builder-runtime-failure-escalation-early",
        marker="RUNTIME_FAILURE_ESCALATE",
    )
    assert early.accepted is False
    assert early.refusal is not None
    assert early.refusal.reason == "counter_threshold_not_reached"

    state, first_failure = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder",
        action_id="execution.recover_builder_runtime_failure",
        input_id="observe-builder-runtime-failure",
        marker="RUNTIME_FAILURE",
        target_activation_id="activation-runtime-troubleshooter",
    )
    assert "mutation.route_activation" in mutation_kinds(first_failure)
    assert "mutation.record_counter" in mutation_kinds(first_failure)
    assert _counter_for(state, "execution.runtime_failure_count.builder").value == 1
    runtime_recovery_activation = state.activations["activation-runtime-troubleshooter"]
    assert str(runtime_recovery_activation.stage_kind_id) == "lad_troubleshooter"

    state = claim_activation(
        state,
        activation_id="activation-runtime-troubleshooter",
        run_id="run-runtime-troubleshooter",
        input_id="claim-runtime-troubleshooter",
    )
    state, _recovered = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-runtime-troubleshooter",
        action_id="execution.return_troubleshooter_recovered",
        input_id="observe-runtime-troubleshooter-recovered",
        schema_id=REPORT_SCHEMA_ID,
        target_activation_id="activation-builder-after-runtime-recovery",
        marker="TROUBLESHOOT_RECOVERED",
    )
    state = claim_activation(
        state,
        activation_id="activation-builder-after-runtime-recovery",
        run_id="run-builder-after-runtime-recovery",
        input_id="claim-builder-after-runtime-recovery",
    )

    state, exhausted = _apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-builder-after-runtime-recovery",
        action_id="execution.close_builder_runtime_failure_exhausted",
        input_id="observe-builder-runtime-failure-exhausted",
        marker="RUNTIME_FAILURE_ESCALATE",
    )

    assert "mutation.close_work_item" in mutation_kinds(exhausted)
    assert "mutation.record_counter" in mutation_kinds(exhausted)
    assert _counter_for(state, "execution.runtime_failure_count.builder").value == 2
    closed = next(iter(state.closed_work_items.values()))
    assert closed.action_id == ActionId(
        "execution.close_builder_runtime_failure_exhausted"
    )
    assert closed.close_kind == "terminal_action"


def test_runtime_failure_recovery_exhaustion_survives_restart(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_lad()
    state = runtime_failure_exhausted_state(plan, fingerprint)

    reloaded = _persist_and_load(tmp_path, state)

    closed = next(iter(reloaded.closed_work_items.values()))
    assert closed.work_item_id == "work-task"
    assert closed.action_id == ActionId(
        "execution.close_builder_runtime_failure_exhausted"
    )
    assert closed.close_kind == "terminal_action"
    assert _counter_for(
        reloaded,
        "execution.runtime_failure_count.builder",
    ).value == 2
    status = operator_status(reloaded)
    counter_statuses = {counter.counter_id: counter for counter in status.counters}
    assert counter_statuses["execution.runtime_failure_count.builder"].value == 2
    attempts = {
        attempt.policy_id: attempt for attempt in status.recovery_attempts
    }
    runtime_attempt = attempts["execution.runtime_failure_recovery"]
    assert runtime_attempt.recovery_action_id == (
        "execution.recover_builder_runtime_failure"
    )
    assert runtime_attempt.latest_recovery_run_id == "run-runtime-troubleshooter"
    assert runtime_attempt.latest_return_action_id == (
        "execution.return_troubleshooter_recovered"
    )


def test_lad_quarantine_status_operator_resume_and_restart(tmp_path: Path) -> None:
    plan, fingerprint = compile_lad()
    quarantined = _lineage_quarantined_state(plan, fingerprint)
    quarantine = _active_quarantine(quarantined)

    status = operator_status(quarantined)
    assert status.selected_plan is not None
    assert status.selected_plan.workflow_id == "execution.lad"
    assert status.selected_plan.authority_fingerprint == fingerprint
    assert len(status.recovery_attempts) == 1
    attempt_status = status.recovery_attempts[0]
    assert attempt_status.policy_id == "execution.blocked_recovery"
    assert attempt_status.phase == "quarantine_eligible"
    assert attempt_status.source_stage_kind_id == "lad_builder"
    assert attempt_status.recovery_action_id == (
        "execution.escalate_builder_blocked_exhausted"
    )
    assert {
        counter.counter_id: counter.value for counter in status.counters
    }["execution.troubleshoot_attempt_count.builder"] == 4
    assert len(status.cooldown_waits) == 1
    assert len(status.quarantines) == 1
    assert status.quarantines[0].record_id == quarantine.quarantine_id
    assert status.quarantines[0].quarantine_kind == "lineage"

    reloaded = _persist_and_load(tmp_path, quarantined)
    assert operator_status(reloaded).quarantines[0].record_id == (
        quarantine.quarantine_id
    )

    ResumeInput = operator_api.OperatorResumeLineageInput
    with pytest.raises(operator_api.OperatorInputError) as missing_reason:
        operator_api.build_resume_lineage(
            reloaded,
            ResumeInput(
                input_id="operator-resume-lad-lineage-missing-reason",
                option_id="execution.blocked.resume_lineage",
                selected_plan_ref=quarantine.selected_plan_ref,
                quarantine_id=quarantine.quarantine_id,
                actor_id="local-operator-tim",
                actor_kind="local_operator",
            ),
        )
    assert str(missing_reason.value) == "empty_reason"

    transition_input = operator_api.build_resume_lineage(
        reloaded,
        ResumeInput(
            input_id="operator-resume-lad-lineage",
            option_id="execution.blocked.resume_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator approved retry after LAD blocked recovery",
        ),
    )
    decision = decide(
        reloaded,
        transition_input,
        lad_context(
            "operator-resume-lad-lineage",
            activation_id="activation-operator-resumed-builder",
        ),
    )
    assert decision.accepted is True
    assert {
        "mutation.supersede_lineage_quarantine",
        "mutation.record_recovery_attempt",
        "mutation.create_activation",
        "mutation.record_operator_intervention",
    } <= set(mutation_kinds(decision))
    resumed = apply(reloaded, decision)
    intervention = next(iter(resumed.operator_interventions.values()))
    assert intervention.option_id == "execution.blocked.resume_lineage"
    assert intervention.kind == "resume_lineage"
    assert intervention.actor_id == "local-operator-tim"
    assert intervention.reason == "operator approved retry after LAD blocked recovery"
    assert intervention.selected_plan_fingerprint == fingerprint
    activation = resumed.activations["activation-operator-resumed-builder"]
    assert str(activation.stage_kind_id) == "lad_builder"
    assert activation.graph_node_id == "execution.lad.builder.start"

    reloaded_resumed = _persist_and_load(tmp_path / "after", resumed)
    assert reloaded_resumed.operator_interventions == resumed.operator_interventions
    assert (
        next(iter(reloaded_resumed.operator_interventions.values())).reason
        == "operator approved retry after LAD blocked recovery"
    )
    assert (
        reloaded_resumed.lineage_quarantines[quarantine.quarantine_id].status
        == "superseded"
    )


def test_lad_operator_close_lineage_uses_selected_option() -> None:
    plan, fingerprint = compile_lad()
    quarantined = _lineage_quarantined_state(plan, fingerprint)
    quarantine = _active_quarantine(quarantined)

    transition_input = operator_api.build_close_lineage(
        quarantined,
        operator_api.OperatorCloseLineageInput(
            input_id="operator-close-lad-lineage",
            option_id="execution.blocked.close_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator closed LAD lineage after blocked recovery",
            payload={},
        ),
    )
    decision = decide(
        quarantined,
        transition_input,
        lad_context("operator-close-lad-lineage"),
    )

    assert decision.accepted is True
    assert {
        "mutation.supersede_lineage_quarantine",
        "mutation.record_recovery_attempt",
        "mutation.record_operator_intervention",
        "mutation.close_work_item",
    } <= set(mutation_kinds(decision))
    closed = apply(quarantined, decision)
    intervention = next(iter(closed.operator_interventions.values()))
    assert intervention.option_id == "execution.blocked.close_lineage"
    assert intervention.kind == "close_lineage"
    assert set(intervention.closed_work_item_ids) == {
        "work-builder-resume",
        "work-task",
        "work-troubleshooter",
    }
    assert all(
        closed.closed_work_items[work_item_id].close_kind == "operator_intervention"
        for work_item_id in intervention.closed_work_item_ids
    )


def test_lad_operator_revise_lineage_validates_and_routes_selected_task(
    tmp_path: Path,
) -> None:
    plan, fingerprint = compile_lad()
    quarantined = _lineage_quarantined_state(plan, fingerprint)
    quarantine = _active_quarantine(quarantined)

    invalid = OperatorReviseLineage(
        "operator-revise-lad-invalid",
        option_id="execution.blocked.revise_lineage",
        selected_plan_ref=quarantine.selected_plan_ref,
        quarantine_id=quarantine.quarantine_id,
        lineage_id=quarantine.lineage_id,
        actor_id="local-operator-tim",
        actor_kind="local_operator",
        reason="operator rejected continued LAD recovery",
        payload={"task_id": "task-revised"},
    )
    invalid_decision = decide(
        quarantined,
        invalid,
        lad_context(
            "operator-revise-lad-invalid",
            work_item_id="work-invalid-revision",
            activation_id="activation-invalid-revision",
        ),
    )
    assert invalid_decision.accepted is False
    assert invalid_decision.refusal is not None
    assert invalid_decision.refusal.reason == (
        "invalid_operator_intervention_payload_schema"
    )
    refused = apply(quarantined, invalid_decision)
    reloaded_refused = _persist_and_load(tmp_path / "refused", refused)
    assert reloaded_refused.operator_interventions == {}
    assert (
        reloaded_refused.receipts["operator-revise-lad-invalid"].accepted is False
    )
    assert reloaded_refused.refusals[-1].input_id == "operator-revise-lad-invalid"
    assert reloaded_refused.refusals[-1].reason == (
        "invalid_operator_intervention_payload_schema"
    )
    assert reloaded_refused.lineage_quarantines == quarantined.lineage_quarantines

    payload = {
        "task_id": "task-revised",
        "body": "Retry the selected execution task with operator context.",
    }
    transition_input = operator_api.build_revise_lineage(
        quarantined,
        operator_api.OperatorReviseLineageInput(
            input_id="operator-revise-lad-lineage",
            option_id="execution.blocked.revise_lineage",
            selected_plan_ref=quarantine.selected_plan_ref,
            quarantine_id=quarantine.quarantine_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            reason="operator revised LAD task for another attempt",
            payload=payload,
        ),
    )
    decision = decide(
        quarantined,
        transition_input,
        lad_context(
            "operator-revise-lad-lineage",
            work_item_id="work-revised-task",
            activation_id="activation-revised-builder",
        ),
    )

    assert decision.accepted is True
    assert {
        "mutation.supersede_lineage_quarantine",
        "mutation.record_recovery_attempt",
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.record_operator_intervention",
    } <= set(mutation_kinds(decision))
    revised = apply(quarantined, decision)
    work_item = revised.work_items["work-revised-task"]
    activation = revised.activations["activation-revised-builder"]
    intervention = next(iter(revised.operator_interventions.values()))
    assert work_item.payload == payload
    assert str(work_item.queue_family_id) == "task"
    assert str(activation.stage_kind_id) == "lad_builder"
    assert activation.graph_node_id == "execution.lad.builder.start"
    assert intervention.option_id == "execution.blocked.revise_lineage"
    assert intervention.kind == "revise_lineage"
    assert intervention.payload_reference == "work_item:work-revised-task:payload"
