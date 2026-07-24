from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts import QueueFamilyId, RecoveryPolicyId, SelectedCompiledPlan
from millrace.contracts.state import (
    ClosedWorkItemRecord,
    OperatorInterventionRecord,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdmitPlan,
    EnqueueWork,
    EvaluateCompletionBehavior,
    InitializeWorkspace,
    OpenClosureTarget,
    SelectDefaultPlan,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.operator import OperatorStatus, operator_status
from millrace.operator.status import QueueFamilyStatus
from millrace.substrate.errors import StorageIntegrityError
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support.lad_planning import (
    REPORT_SCHEMA_ID,
    apply_accepted_input,
    apply_runner_observation,
    artifact_payload,
    claim_activation,
    compile_lad_planning,
    planning_context,
)

COMPLETION_BEHAVIOR_ID = "planning.closure.completion"
CLOSURE_TARGET_ID = "closure-target-spec-1"


def _bootstrap_selected_plan() -> tuple[SelectedCompiledPlan, str, RuntimeState]:
    plan, fingerprint = compile_lad_planning()
    state = empty_runtime_state()
    for transition_input, context in (
        (InitializeWorkspace("init-status"), planning_context("init-status")),
        (
            AdmitPlan(
                "admit-status",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            planning_context("admit-status"),
        ),
        (
            SelectDefaultPlan("select-status", authority_fingerprint=fingerprint),
            planning_context("select-status"),
        ),
        (
            EnqueueWork(
                "enqueue-root-spec-status",
                queue_family_id=QueueFamilyId("spec"),
                payload={
                    "title": "Root spec",
                    "body": "Root source inventory for status projection.",
                    "root_source": {
                        "kind": "spec",
                        "source_id": "root-source-1",
                    },
                },
            ),
            planning_context(
                "enqueue-root-spec-status",
                work_item_id="root-spec-1",
                activation_id="activation-root-spec-status",
            ),
        ),
    ):
        state = apply_accepted_input(state, transition_input, context)
    return plan, fingerprint, state


def _open_closure_target(
    state: RuntimeState,
    *,
    input_id: str = "open-status-closure",
) -> RuntimeState:
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        input_id,
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id=CLOSURE_TARGET_ID,
        lineage_id="root-spec-1",
        root_source_kind="spec",
        root_source_id="root-source-1",
        closure_root_work_item_id="root-spec-1",
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "root-spec-1"},
    )
    decision = decide(state, transition_input, planning_context(input_id))
    assert decision.accepted is True
    return apply(state, decision)


def _evaluate_closure(
    state: RuntimeState,
    *,
    input_id: str = "evaluate-status-closure",
) -> RuntimeState:
    assert state.default_plan_ref is not None
    transition_input = EvaluateCompletionBehavior(
        input_id,
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id=CLOSURE_TARGET_ID,
    )
    decision = decide(
        state,
        transition_input,
        planning_context(
            input_id,
            work_item_id="work-arbiter-status",
            activation_id="activation-arbiter-status",
        ),
    )
    assert decision.accepted is True
    return apply(state, decision)


def _close_work_item(
    state: RuntimeState,
    work_item_id: str,
    *,
    input_id: str = "operator-close-root-status",
) -> RuntimeState:
    assert state.default_plan_ref is not None
    intervention_record_id = f"operator-close:{work_item_id}"
    intervention = OperatorInterventionRecord(
        record_id=intervention_record_id,
        created_by_input_id=input_id,
        input_payload_digest=f"sha256:{'0' * 64}",
        option_id="test.operator.close_root",
        kind="close_lineage",
        result="closed",
        policy_id=RecoveryPolicyId("test.operator.policy"),
        lineage_id=state.work_items[work_item_id].lineage_id or work_item_id,
        quarantine_id="test.operator.quarantine",
        recovery_attempt_record_id="test.operator.recovery_attempt",
        recovery_attempt_count=1,
        attempt_effect="resolve_attempt",
        selected_plan_ref=state.default_plan_ref,
        selected_plan_fingerprint=state.default_plan_ref.authority_fingerprint,
        actor_kind="local_operator",
        actor_id="local_operator",
        reason="test fixture closes selected root work",
        target_work_item_id=None,
        target_activation_id=None,
        closed_work_item_ids=(work_item_id,),
        closed_activation_ids=(),
        closed_run_ids=(),
        payload_digest=f"sha256:{'1' * 64}",
        payload_reference=None,
    )
    return replace(
        state,
        operator_interventions={
            **state.operator_interventions,
            intervention_record_id: intervention,
        },
        closed_work_items={
            **state.closed_work_items,
            work_item_id: ClosedWorkItemRecord(
                record_id=work_item_id,
                work_item_id=work_item_id,
                source_run_id=None,
                action_id=None,
                created_by_input_id=input_id,
                operator_intervention_record_id=intervention_record_id,
                close_kind="operator_intervention",
            ),
        },
    )


def _without_fixture_root_closes(state: RuntimeState) -> RuntimeState:
    return replace(
        state,
        closed_work_items={
            record_id: record
            for record_id, record in state.closed_work_items.items()
            if record.operator_intervention_record_id is None
            or not record.operator_intervention_record_id.startswith("operator-close:")
        },
        operator_interventions={
            record_id: record
            for record_id, record in state.operator_interventions.items()
            if not record_id.startswith("operator-close:")
        },
    )


def _activate_arbiter() -> tuple[SelectedCompiledPlan, str, RuntimeState]:
    plan, fingerprint, state = _bootstrap_selected_plan()
    state = _open_closure_target(state)
    state = _close_work_item(state, "root-spec-1")
    state = _evaluate_closure(state)
    state = claim_activation(
        state,
        activation_id="activation-arbiter-status",
        run_id="run-arbiter-status",
        input_id="claim-arbiter-status",
    )
    return plan, fingerprint, state


def _queue_family(status: OperatorStatus, queue_family_id: str) -> QueueFamilyStatus:
    return next(
        family
        for family in status.queue_families
        if family.queue_family_id == queue_family_id
    )


def test_lad_planning_status_projects_open_closure_and_evaluator_ready() -> None:
    _plan, fingerprint, state = _bootstrap_selected_plan()
    state = _open_closure_target(state)
    state = _close_work_item(state, "root-spec-1")
    state = _evaluate_closure(state)

    status = operator_status(state)

    assert len(status.closure_targets) == 1
    target = status.closure_targets[0]
    assert target.closure_target_id == CLOSURE_TARGET_ID
    assert target.completion_behavior_id == COMPLETION_BEHAVIOR_ID
    assert target.status == "open"
    assert target.lineage_id == "root-spec-1"
    assert target.root_source_kind == "spec"
    assert target.root_source_id == "root-source-1"
    assert target.closure_root_work_item_id == "root-spec-1"
    assert target.request_kind == "closure_target"
    assert target.target_graph_node_id == "planning.lad.arbiter.start"
    assert target.selected_plan_fingerprint == fingerprint
    assert target.active_evaluator_record_id == (
        "closure-evaluator:activation-arbiter-status"
    )
    assert target.active_evaluator_run_id is None
    assert target.latest_terminal_record_id is None
    assert target.latest_remediation_record_id is None
    assert target.operator_required is False

    assert len(status.closure_evaluations) == 1
    evaluation = status.closure_evaluations[0]
    assert evaluation.record_id == "closure-evaluator:activation-arbiter-status"
    assert evaluation.closure_target_id == CLOSURE_TARGET_ID
    assert evaluation.target_work_item_id == "work-arbiter-status"
    assert evaluation.target_activation_id == "activation-arbiter-status"
    assert evaluation.target_run_id is None
    assert evaluation.queue_family_id == "stage_result"
    assert evaluation.graph_node_id == "planning.lad.arbiter.start"
    assert evaluation.stage_kind_id == "lad_arbiter"
    assert evaluation.runner_binding_id == "planning.lad.local_runner"
    assert evaluation.status == "ready"
    assert _queue_family(status, "stage_result").ready_count == 1


def test_lad_planning_status_projects_manual_closure_without_root_work_item() -> None:
    _plan, fingerprint, state = _bootstrap_selected_plan()
    assert state.default_plan_ref is not None
    open_manual = OpenClosureTarget(
        "open-status-manual-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-manual-root",
        lineage_id="manual-lineage-1",
        root_source_kind="manual",
        root_source_id="manual-source-1",
        closure_root_work_item_id=None,
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "manual-lineage-1"},
    )
    open_decision = decide(
        state,
        open_manual,
        planning_context("open-status-manual-root"),
    )
    assert open_decision.accepted is True
    state = apply(state, open_decision)
    assert state.default_plan_ref is not None
    evaluate = EvaluateCompletionBehavior(
        "evaluate-status-manual-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-manual-root",
    )
    evaluate_decision = decide(
        state,
        evaluate,
        planning_context(
            "evaluate-status-manual-root",
            work_item_id="work-arbiter-manual-status",
            activation_id="activation-arbiter-manual-status",
        ),
    )
    assert evaluate_decision.accepted is True
    state = apply(state, evaluate_decision)

    status = operator_status(state)

    target = next(
        target
        for target in status.closure_targets
        if target.closure_target_id == "closure-target-manual-root"
    )
    assert target.root_source_kind == "manual"
    assert target.root_source_id == "manual-source-1"
    assert target.closure_root_work_item_id is None
    assert target.lineage_id == "manual-lineage-1"
    assert target.selected_plan_fingerprint == fingerprint

    evaluation = next(
        evaluation
        for evaluation in status.closure_evaluations
        if evaluation.closure_target_id == "closure-target-manual-root"
    )
    assert evaluation.target_work_item_id == "work-arbiter-manual-status"
    assert evaluation.status == "ready"


def test_status_projection_never_repairs_corrupt_closure_root_state(
    tmp_path: Path,
) -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    state = _open_closure_target(state)
    state = _close_work_item(state, "root-spec-1")
    state = _evaluate_closure(state)
    durable_state = _without_fixture_root_closes(state)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, durable_state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET lineage_id = ?
            WHERE work_item_id = ?
            """,
            ("different-lineage", "root-spec-1"),
        )

    with pytest.raises(StorageIntegrityError, match="lineage"):
        load_runtime_state(db_path, cas_root)


def test_lad_planning_status_projects_arbiter_remediation_and_restart(
    tmp_path: Path,
) -> None:
    plan, fingerprint, state = _activate_arbiter()
    after, _decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-arbiter-status",
        action_id="planning.closure_gap",
        input_id="observe-arbiter-remediation-status",
        artifact=artifact_payload(
            "planning.artifacts.incident_report",
            summary="Missing validation proof.",
        ),
        target_work_item_id="work-remediation-status",
        target_activation_id="activation-remediation-status",
    )
    legacy_path = tmp_path / "millrace-agents" / "closure-targets" / "legacy.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text('{"closure_target_id": "legacy"}', encoding="utf-8")

    status = operator_status(after)
    reloaded_status = operator_status(
        persist_and_load_runtime_state(
            tmp_path,
            _without_fixture_root_closes(after),
        )
    )

    for projected in (status, reloaded_status):
        assert len(projected.closure_targets) == 1
        target = projected.closure_targets[0]
        assert target.closure_target_id == CLOSURE_TARGET_ID
        assert target.latest_remediation_record_id == (
            "remediation-record:transition-observe-arbiter-remediation-status"
        )
        assert target.latest_remediation_work_item_id == "work-remediation-status"
        assert target.latest_blocked_record_id is None
        assert target.operator_required is False

        assert len(projected.closure_remediations) == 1
        remediation = projected.closure_remediations[0]
        assert remediation.remediation_policy_id == "planning.closure.remediation"
        assert remediation.closure_target_id == CLOSURE_TARGET_ID
        assert remediation.source_run_id == "run-arbiter-status"
        assert remediation.source_action_id == "planning.closure_gap"
        assert remediation.source_artifact_id == (
            "transition-observe-arbiter-remediation-status:artifact"
        )
        assert remediation.target_work_item_id == "work-remediation-status"
        assert remediation.target_activation_id == "activation-remediation-status"
        assert remediation.target_queue_family_id == "incident"
        assert remediation.target_graph_node_id == "planning.lad.auditor.start"
        assert remediation.target_stage_kind_id == "lad_auditor"
        assert remediation.target_runner_binding_id == "planning.lad.local_runner"
        assert remediation.selected_plan_fingerprint == fingerprint
        assert remediation.dedupe_key == (
            "closure-target-spec-1:"
            "transition-observe-arbiter-remediation-status:artifact"
        )
        assert _queue_family(projected, "incident").ready_count == 1


def test_lad_planning_status_projects_arbiter_blocked_operator_required() -> None:
    plan, fingerprint, state = _activate_arbiter()
    after, _decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-arbiter-status",
        action_id="planning.close_arbiter_blocked",
        input_id="observe-arbiter-blocked-status",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
    )

    status = operator_status(after)

    assert len(status.closure_targets) == 1
    target = status.closure_targets[0]
    assert target.closure_target_id == CLOSURE_TARGET_ID
    assert target.status == "open"
    assert target.latest_blocked_record_id == (
        "closure-blocked:transition-observe-arbiter-blocked-status"
    )
    assert target.operator_required is True

    assert len(status.closure_blocks) == 1
    blocked = status.closure_blocks[0]
    assert blocked.closure_target_id == CLOSURE_TARGET_ID
    assert blocked.completion_behavior_id == COMPLETION_BEHAVIOR_ID
    assert blocked.source_run_id == "run-arbiter-status"
    assert blocked.source_action_id == "planning.close_arbiter_blocked"
    assert blocked.lineage_id == "root-spec-1"
    assert blocked.selected_plan_fingerprint == fingerprint
    assert blocked.operator_required is True
    assert status.closure_remediations == ()
