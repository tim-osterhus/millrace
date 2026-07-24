from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts import EnqueueWork, QueueFamilyId
from millrace.contracts.ids import ActionId, RunnerBindingId
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    OperatorCloseWait,
    OperatorResumeWait,
    OperatorReviseWait,
    SelectDefaultPlan,
)
from millrace.kernel import apply, decide
from millrace.operator import OperatorStatus, operator_status
from millrace.operator.status import ArtifactStatus, QueueFamilyStatus
from millrace.substrate.errors import StorageIntegrityError
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import lad_learning
from support.simple_loop import compile_simple_loop, simple_loop_context


def _queue_family(status: OperatorStatus, queue_family_id: str) -> QueueFamilyStatus:
    return next(
        family
        for family in status.queue_families
        if family.queue_family_id == queue_family_id
    )


def _ready_active_terminal_state(plan, fingerprint: str) -> RuntimeState:
    state = lad_learning.admitted_state(plan, fingerprint)
    state = lad_learning.apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-learning-ready-status",
            queue_family_id=QueueFamilyId("learning_request"),
            payload=lad_learning.learning_payload(request_id="learning-ready-status"),
        ),
        lad_learning.context(
            "enqueue-learning-ready-status",
            work_item_id="work-learning-ready-status",
            activation_id="activation-learning-ready-status",
        ),
    )
    state = lad_learning.apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-learning-terminal-status",
            queue_family_id=QueueFamilyId("learning_request"),
            payload=lad_learning.learning_payload(
                request_id="learning-terminal-status"
            ),
        ),
        lad_learning.context(
            "enqueue-learning-terminal-status",
            work_item_id="work-learning-terminal-status",
            activation_id="activation-learning-terminal-status",
        ),
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-terminal-status",
        run_id="run-learning-terminal-status",
        input_id="claim-learning-terminal-status",
    )
    state = lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-learning-terminal-status",
        marker="ANALYST_NOOP",
        artifact=lad_learning.artifact_payload(
            lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
        ),
        input_id="observe-learning-terminal-status",
    )
    state = lad_learning.apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-learning-active-status",
            queue_family_id=QueueFamilyId("learning_request"),
            payload=lad_learning.learning_payload(request_id="learning-active-status"),
        ),
        lad_learning.context(
            "enqueue-learning-active-status",
            work_item_id="work-learning-active-status",
            activation_id="activation-learning-active-status",
        ),
    )
    return lad_learning.claim(
        state,
        activation_id="activation-learning-active-status",
        run_id="run-learning-active-status",
        input_id="claim-learning-active-status",
    )


def _learning_effect_status(
    *,
    reconciliation_status: str | None,
):
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.learning_effect_proposal_state(plan, fingerprint)
    if reconciliation_status is not None:
        state = lad_learning.reconcile_first_effect(
            state,
            status=reconciliation_status,
            input_id=f"reconcile-learning-effect-{reconciliation_status}",
            result_id=f"fake-local-result-{reconciliation_status}",
        )
    status = operator_status(state)
    assert len(status.effects) == 1
    return status.effects[0]


def _artifact_status_by_input(
    status: OperatorStatus,
    input_id: str,
) -> ArtifactStatus:
    return next(row for row in status.artifacts if row.source_input_id == input_id)


def _artifact_record_by_input(state: RuntimeState, input_id: str):
    return next(
        artifact
        for artifact in state.artifacts.values()
        if artifact.created_by_input_id == input_id
    )


def _effect_id_by_input(state: RuntimeState, input_id: str) -> str:
    return next(
        effect.effect_id
        for effect in state.effect_proposals.values()
        if effect.source_input_id == input_id
    )


def _first_generated_work_id(state: RuntimeState) -> str:
    return next(iter(state.fanout_records))


def _replace_observation_marker(
    state: RuntimeState,
    *,
    run_id: str,
    marker: str,
) -> RuntimeState:
    observation = next(
        candidate
        for candidate in state.runner_observations.values()
        if candidate.run_id == run_id
    )
    return replace(
        state,
        runner_observations={
            **state.runner_observations,
            observation.observation_id: replace(
                observation,
                payload={**observation.payload, "marker": marker},
            ),
        },
    )


def _blocked_terminal_state(plan, fingerprint: str) -> RuntimeState:
    state = lad_learning.ready_learning_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-analyst",
        input_id="claim-analyst",
    )
    return lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-analyst",
        marker="BLOCKED",
        artifact=lad_learning.artifact_payload(lad_learning.LEARNING_REPORT_SCHEMA_ID),
        input_id="observe-analyst-blocked-status",
    )


def _select_simple_loop_default(state: RuntimeState) -> RuntimeState:
    simple_plan, simple_fingerprint = compile_simple_loop()
    for transition_input in (
        AdmitPlan(
            "admit-simple-loop-for-status-filter",
            selected_plan=simple_plan,
            authority_fingerprint=simple_fingerprint,
        ),
        SelectDefaultPlan(
            "select-simple-loop-for-status-filter",
            authority_fingerprint=simple_fingerprint,
        ),
    ):
        decision = decide(
            state,
            transition_input,
            simple_loop_context(transition_input.input_id),
        )
        assert decision.accepted is True
        state = apply(state, decision)
    return state


def _assert_artifact_context(
    artifact: ArtifactStatus,
    *,
    fingerprint: str,
    schema_id: str,
    source_action_id: str,
    source_input_id: str,
    source_run_id: str,
    source_activation_id: str,
    source_work_item_id: str,
    queue_family_id: str,
    stage_kind_id: str,
    graph_node_id: str,
    marker: str,
) -> None:
    assert artifact.workflow_id == "lad.full"
    assert artifact.selected_plan_fingerprint == fingerprint
    assert artifact.schema_id == schema_id
    assert artifact.source_action_id == source_action_id
    assert artifact.terminal_action_id == source_action_id
    assert artifact.source_input_id == source_input_id
    assert artifact.source_run_id == source_run_id
    assert artifact.source_activation_id == source_activation_id
    assert artifact.work_item_id == source_work_item_id
    assert artifact.queue_family_id == queue_family_id
    assert artifact.source_stage_kind_id == stage_kind_id
    assert artifact.source_graph_node_id == graph_node_id
    assert artifact.source_runner_binding_id == "learning.standard.local_runner"
    assert artifact.latest_marker == marker
    assert artifact.payload_digest.startswith("sha256:")


def test_learning_status_projects_ready_active_terminal_and_queue_count_rows_after_restart(  # noqa: E501
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _ready_active_terminal_state(plan, fingerprint)

    status = operator_status(persist_and_load_runtime_state(tmp_path, state))

    family = _queue_family(status, "learning_request")
    assert (
        family.ready_count,
        family.active_count,
        family.closed_count,
        family.quarantined_count,
        family.operator_wait_count,
    ) == (1, 1, 1, 0, 0)
    assert {run.run_id for run in status.active_runs} == {
        "run-learning-active-status"
    }
    _assert_artifact_context(
        _artifact_status_by_input(status, "observe-learning-terminal-status"),
        fingerprint=fingerprint,
        schema_id=lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID,
        source_action_id="learning.close_analyst_noop",
        source_input_id="observe-learning-terminal-status",
        source_run_id="run-learning-terminal-status",
        source_activation_id="activation-learning-terminal-status",
        source_work_item_id="work-learning-terminal-status",
        queue_family_id="learning_request",
        stage_kind_id="analyst",
        graph_node_id="learning.standard.analyst",
        marker="ANALYST_NOOP",
    )


@pytest.mark.parametrize(
    (
        "state_kind",
        "input_id",
        "schema_id",
        "source_action_id",
        "source_run_id",
        "source_activation_id",
        "source_work_item_id",
        "queue_family_id",
        "stage_kind_id",
        "graph_node_id",
        "marker",
    ),
    (
        (
            "complete",
            "observe-curator-complete",
            lad_learning.LEARNING_SKILL_UPDATE_SCHEMA_ID,
            "learning.close_curator_complete",
            "run-curator",
            "activation-curator",
            "work-curator",
            "stage_result",
            "curator",
            "learning.standard.curator",
            "CURATOR_COMPLETE",
        ),
        (
            "noop",
            "observe-learning-terminal-status",
            lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID,
            "learning.close_analyst_noop",
            "run-learning-terminal-status",
            "activation-learning-terminal-status",
            "work-learning-terminal-status",
            "learning_request",
            "analyst",
            "learning.standard.analyst",
            "ANALYST_NOOP",
        ),
        (
            "blocked",
            "observe-analyst-blocked-status",
            lad_learning.LEARNING_REPORT_SCHEMA_ID,
            "learning.close_analyst_blocked",
            "run-analyst",
            "activation-learning-request",
            "work-learning-request",
            "learning_request",
            "analyst",
            "learning.standard.analyst",
            "BLOCKED",
        ),
    ),
)
def test_learning_status_projects_terminal_artifacts_after_restart(
    tmp_path: Path,
    state_kind: str,
    input_id: str,
    schema_id: str,
    source_action_id: str,
    source_run_id: str,
    source_activation_id: str,
    source_work_item_id: str,
    queue_family_id: str,
    stage_kind_id: str,
    graph_node_id: str,
    marker: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    if state_kind == "complete":
        state = lad_learning.learning_effect_proposal_state(plan, fingerprint)
    elif state_kind == "noop":
        state = _ready_active_terminal_state(plan, fingerprint)
    else:
        state = _blocked_terminal_state(plan, fingerprint)

    status = operator_status(persist_and_load_runtime_state(tmp_path, state))

    _assert_artifact_context(
        _artifact_status_by_input(status, input_id),
        fingerprint=fingerprint,
        schema_id=schema_id,
        source_action_id=source_action_id,
        source_input_id=input_id,
        source_run_id=source_run_id,
        source_activation_id=source_activation_id,
        source_work_item_id=source_work_item_id,
        queue_family_id=queue_family_id,
        stage_kind_id=stage_kind_id,
        graph_node_id=graph_node_id,
        marker=marker,
    )


def test_learning_status_projects_operator_required_wait_and_intervention_rows(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    waiting, wait = lad_learning.learning_blocked_wait_state(plan, fingerprint)
    waiting_path = tmp_path / "waiting"
    waiting_path.mkdir()

    waiting_status = operator_status(
        persist_and_load_runtime_state(waiting_path, waiting)
    )

    family = _queue_family(waiting_status, "learning_request")
    assert (
        family.ready_count,
        family.active_count,
        family.closed_count,
        family.quarantined_count,
        family.operator_wait_count,
    ) == (0, 0, 0, 0, 1)
    active = waiting_status.operator_waits[0]
    assert active.wait_id == wait.wait_id
    assert active.operator_wait_id == "learning.analyst_blocked_wait"
    assert active.source_action_id == "learning.close_analyst_blocked"
    assert active.source_work_item_id == wait.source_work_item_id
    assert active.source_activation_id == wait.source_activation_id
    assert active.source_run_id == wait.source_run_id
    assert active.source_stage_kind_id == "analyst"
    assert active.source_graph_node_id == "learning.standard.analyst"
    assert active.source_queue_family_id == "learning_request"
    assert active.source_runner_binding_id == "learning.standard.local_runner"
    assert active.source_artifact_id == wait.source_artifact_id
    assert active.status == "active"
    assert active.created_input_id == "observe-analyst-blocked"
    assert active.resolved_input_id is None
    assert active.actor_id is None
    assert active.actor_kind is None
    assert active.resolution_kind is None

    resumed = lad_learning.apply_accepted_input(
        waiting,
        OperatorResumeWait(
            "operator-resume-learning-status",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local_operator",
            actor_kind="local_operator",
            payload={},
        ),
        lad_learning.context(
            "operator-resume-learning-status",
            activation_id="activation-learning-status-resumed",
        ),
    )
    resumed_path = tmp_path / "resumed"
    resumed_path.mkdir()
    resumed_status = operator_status(
        persist_and_load_runtime_state(resumed_path, resumed)
    )
    resolved = resumed_status.operator_waits[0]
    assert resolved.status == "resolved"
    assert resolved.resolution_kind == "resume_recorded_source"
    assert resolved.actor_id == "local_operator"
    assert resolved.actor_kind == "local_operator"
    assert resolved.resolved_input_id == "operator-resume-learning-status"
    assert resolved.target_activation_id == "activation-learning-status-resumed"
    assert resolved.target_work_item_id is None
    assert resolved.closed_work_item_ids == ()
    assert resolved.payload_digest is not None
    assert resolved.payload_reference is None

    close_waiting, close_wait = lad_learning.learning_blocked_wait_state(
        plan,
        fingerprint,
        input_id="observe-analyst-blocked-for-status-close",
    )
    closed = lad_learning.apply_accepted_input(
        close_waiting,
        OperatorCloseWait(
            "operator-close-learning-status",
            selected_plan_ref=close_wait.selected_plan_ref,
            wait_id=close_wait.wait_id,
            lineage_id=close_wait.lineage_id,
            actor_id="local_operator",
            actor_kind="local_operator",
            payload={},
        ),
        lad_learning.context("operator-close-learning-status"),
    )
    closed_path = tmp_path / "closed"
    closed_path.mkdir()
    closed_status = operator_status(
        persist_and_load_runtime_state(closed_path, closed)
    )
    close_row = closed_status.operator_waits[0]
    assert close_row.status == "resolved"
    assert close_row.resolution_kind == "close_recorded_source"
    assert close_row.closed_work_item_ids == (close_wait.source_work_item_id,)

    revise_waiting, revise_wait = lad_learning.learning_blocked_wait_state(
        plan,
        fingerprint,
        input_id="observe-analyst-blocked-for-status-revise",
    )
    revised = lad_learning.apply_accepted_input(
        revise_waiting,
        OperatorReviseWait(
            "operator-revise-learning-status",
            selected_plan_ref=revise_wait.selected_plan_ref,
            wait_id=revise_wait.wait_id,
            lineage_id=revise_wait.lineage_id,
            actor_id="local_operator",
            actor_kind="local_operator",
            payload=lad_learning.learning_payload(
                request_id="operator-revised-status",
                body="Operator revised the Learning request.",
            ),
        ),
        lad_learning.context(
            "operator-revise-learning-status",
            work_item_id="work-operator-revised-status",
            activation_id="activation-operator-revised-status",
        ),
    )
    revised_path = tmp_path / "revised"
    revised_path.mkdir()
    revised_status = operator_status(
        persist_and_load_runtime_state(revised_path, revised)
    )
    revise_row = revised_status.operator_waits[0]
    assert revise_row.status == "resolved"
    assert revise_row.resolution_kind == "revise_recorded_source"
    assert revise_row.target_work_item_id == "work-operator-revised-status"
    assert revise_row.target_activation_id == "activation-operator-revised-status"
    assert revise_row.closed_work_item_ids == (revise_wait.source_work_item_id,)
    assert revise_row.payload_digest is not None
    assert revise_row.payload_reference == (
        "work_item:work-operator-revised-status:payload"
    )


def test_learning_status_projects_effect_pending_applied_noop_and_refused() -> None:
    pending = _learning_effect_status(reconciliation_status=None)
    applied = _learning_effect_status(reconciliation_status="applied")
    noop = _learning_effect_status(reconciliation_status="no_op")
    refused = _learning_effect_status(reconciliation_status="refused")

    assert pending.status == "pending"
    assert pending.reconciliation_id is None
    assert applied.status == "applied"
    assert noop.status == "no_op"
    assert refused.status == "refused"
    assert {
        pending.effect_declaration_id,
        applied.effect_declaration_id,
        noop.effect_declaration_id,
        refused.effect_declaration_id,
    } == {lad_learning.CURATOR_EFFECT_DECLARATION_ID}
    assert applied.source_action_id == "learning.close_curator_complete"
    assert applied.terminal_action_id == "learning.close_curator_complete"
    assert applied.source_input_id == "observe-curator-complete"
    assert applied.provider_ref == lad_learning.FAKE_LOCAL_EFFECT_PROVIDER_REF
    assert applied.capability_policy_ref == (
        lad_learning.FAKE_LOCAL_EFFECT_CAPABILITY_POLICY_REF
    )


def test_learning_status_projects_trigger_and_concurrency_context() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    generated_state = lad_learning.planning_closure_with_generated_learning_state(
        plan,
        fingerprint,
        active_learning=False,
    )

    generated_status = operator_status(generated_state)

    generated = next(
        row
        for row in generated_status.generated_work
        if row.item_key == "closure-librarian-learning"
    )
    assert generated.workflow_id == "lad.full"
    assert generated.selected_plan_fingerprint == fingerprint
    assert generated.fanout_id == "learning.trigger.planning.planner_complete"
    assert generated.target_route_id == "learning.trigger.librarian"
    assert generated.source_action_id == "planning.route_planner_complete"
    assert generated.source_input_id == "observe-closure-planner-complete"
    assert generated.target_queue_family_id == "learning_request"
    assert generated.target_stage_kind_id == "librarian"
    assert generated.target_graph_node_id == "learning.standard.librarian"
    assert generated.target_runner_binding_id == "learning.standard.local_runner"

    concurrent = lad_learning.active_foreground_state(
        plan,
        fingerprint,
        queue_family_id="spec",
    )
    concurrent = lad_learning.apply_accepted_input(
        concurrent,
        EnqueueWork(
            "enqueue-learning-concurrent-status",
            queue_family_id=QueueFamilyId("learning_request"),
            payload=lad_learning.learning_payload(
                request_id="learning-concurrent-status"
            ),
        ),
        lad_learning.context(
            "enqueue-learning-concurrent-status",
            work_item_id="work-learning-concurrent-status",
            activation_id="activation-learning-concurrent-status",
        ),
    )
    concurrent = lad_learning.claim(
        concurrent,
        activation_id="activation-learning-concurrent-status",
        run_id="run-learning-concurrent-status",
        input_id="claim-learning-concurrent-status",
    )

    concurrent_status = operator_status(concurrent)

    assert {run.run_id for run in concurrent_status.active_runs} == {
        "run-spec",
        "run-learning-concurrent-status",
    }
    assert _queue_family(concurrent_status, "spec").active_count == 1
    assert _queue_family(concurrent_status, "learning_request").active_count == 1


def test_learning_status_projects_generated_work_waiting_while_learning_active_after_restart(  # noqa: E501
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.active_learning_with_generated_waiting_state(
        plan,
        fingerprint,
    )

    status = operator_status(persist_and_load_runtime_state(tmp_path, state))

    family = _queue_family(status, "learning_request")
    assert (family.ready_count, family.active_count) == (1, 1)
    assert {run.run_id for run in status.active_runs} == {"run-active-learning"}
    generated = next(
        row for row in status.generated_work if row.item_key == "generated-learning-1"
    )
    assert generated.workflow_id == "lad.full"
    assert generated.selected_plan_fingerprint == fingerprint
    assert generated.fanout_id == "learning.trigger.execution.doublechecker_pass"
    assert generated.target_route_id == "learning.trigger.analyst"
    assert generated.source_action_id == "execution.route_doublechecker_pass"
    assert generated.source_input_id == "observe-doublecheck"
    assert generated.target_queue_family_id == "learning_request"
    assert generated.target_stage_kind_id == "analyst"
    assert generated.target_graph_node_id == "learning.standard.analyst"
    assert generated.target_runner_binding_id == "learning.standard.local_runner"
    assert generated.lineage_id == "work-task"


def test_learning_status_ignores_old_status_queue_request_index_files_and_aliases(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.ready_learning_state(plan, fingerprint)
    legacy_files = {
        tmp_path / "millrace-agents" / "learning" / "status.json": (
            '{"ready_count": 99}'
        ),
        tmp_path / "millrace-agents" / "queues" / "learning_request" / "ghost.md": (
            "old queue item"
        ),
        tmp_path / "millrace-agents" / "learning" / "requests" / "ghost.md": (
            "old request"
        ),
        tmp_path / "millrace-agents" / "skills" / "remote_skills_index.md": (
            "# old skill index"
        ),
        tmp_path / "millrace-agents" / "modes" / "learning_lad_codex.json": (
            '{"alias": true}'
        ),
    }
    for path, body in legacy_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    status = operator_status(state)

    assert status.selected_plan is not None
    assert status.selected_plan.authority_fingerprint == fingerprint
    family = _queue_family(status, "learning_request")
    assert (
        family.ready_count,
        family.active_count,
        family.closed_count,
        family.quarantined_count,
        family.operator_wait_count,
    ) == (1, 0, 0, 0, 0)
    assert status.artifacts == ()
    assert status.effects == ()
    assert status.generated_work == ()


def test_learning_status_projects_terminal_action_source_action_and_input_context() -> (
    None
):
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.learning_route_artifact_state(plan, fingerprint)

    status = operator_status(state)

    artifact = next(
        row
        for row in status.artifacts
        if row.source_input_id == "observe-analyst-complete"
    )
    assert artifact.workflow_id == "lad.full"
    assert artifact.selected_plan_fingerprint == fingerprint
    assert artifact.schema_id == lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
    assert artifact.source_action_id == "learning.route_analyst_complete"
    assert artifact.terminal_action_id == "learning.route_analyst_complete"
    assert artifact.source_input_id == "observe-analyst-complete"
    assert artifact.source_run_id == "run-analyst"
    assert artifact.source_activation_id == "activation-learning-request"
    assert artifact.source_stage_kind_id == "analyst"
    assert artifact.source_graph_node_id == "learning.standard.analyst"
    assert artifact.source_runner_binding_id == "learning.standard.local_runner"
    assert artifact.lineage_id == "work-learning-request"
    assert artifact.latest_marker == "ANALYST_COMPLETE"
    assert artifact.payload_digest.startswith("sha256:")


def test_learning_status_refuses_or_excludes_projection_source_drift() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.learning_effect_proposal_state(plan, fingerprint)
    artifact = next(
        row
        for row in state.artifacts.values()
        if row.created_by_input_id == "observe-curator-complete"
    )
    effect = next(iter(state.effect_proposals.values()))
    drifted_artifact = replace(artifact, source_run_id="missing-source-run")
    drifted_effect = replace(effect, source_run_id="missing-source-run")
    drifted_state = replace(
        state,
        artifacts={
            **state.artifacts,
            drifted_artifact.artifact_id: drifted_artifact,
        },
        effect_proposals={
            **state.effect_proposals,
            drifted_effect.effect_id: drifted_effect,
        },
    )

    status = operator_status(drifted_state)

    assert drifted_artifact.artifact_id not in {
        row.artifact_id for row in status.artifacts
    }
    assert drifted_effect.effect_id not in {row.effect_id for row in status.effects}


@pytest.mark.parametrize(
    "mutate",
    (
        lambda state, artifact: replace(
            artifact,
            source_action_id=ActionId("learning.close_analyst_noop"),
        ),
        lambda state, artifact: replace(
            artifact,
            created_by_input_id="claim-analyst",
        ),
        lambda state, artifact: replace(
            artifact,
            payload_digest=f"sha256:{'0' * 64}",
        ),
    ),
)
def test_learning_status_excludes_artifact_rows_with_corrupt_projection_links(
    mutate,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.learning_route_artifact_state(plan, fingerprint)
    artifact = _artifact_record_by_input(state, "observe-analyst-complete")
    drifted_artifact = mutate(state, artifact)
    drifted_state = replace(
        state,
        artifacts={**state.artifacts, artifact.artifact_id: drifted_artifact},
    )

    status = operator_status(drifted_state)

    assert artifact.artifact_id not in {row.artifact_id for row in status.artifacts}


def test_learning_status_excludes_artifacts_with_wrong_runner_binding() -> (
    None
):
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.learning_route_artifact_state(plan, fingerprint)
    run = state.runs["run-analyst"]
    activation = state.activations[run.activation_id]
    drifted_state = replace(
        state,
        runs={
            **state.runs,
            run.run_ref.run_id: replace(
                run,
                runner_binding_id=RunnerBindingId("planning.lad.local_runner"),
            ),
        },
        activations={
            **state.activations,
            activation.activation_id: replace(
                activation,
                runner_binding_id=RunnerBindingId("planning.lad.local_runner"),
            ),
        },
    )

    status = operator_status(drifted_state)

    assert status.artifacts == ()


@pytest.mark.parametrize(
    "mutate",
    (
        lambda state, effect: replace(effect, provider_ref="provider.real.workspace"),
        lambda state, effect: replace(effect, status="applied"),
        lambda state, effect: replace(effect, target_skill_id="wrong.skill"),
        lambda state, effect: replace(
            effect,
            created_transition_id="transition-claim-curator",
        ),
    ),
)
def test_learning_status_excludes_effect_rows_with_corrupt_proposal_links(
    mutate,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.learning_effect_proposal_state(plan, fingerprint)
    effect = next(iter(state.effect_proposals.values()))
    drifted_effect = mutate(state, effect)
    drifted_state = replace(
        state,
        effect_proposals={effect.effect_id: drifted_effect},
    )

    status = operator_status(drifted_state)

    assert effect.effect_id not in {row.effect_id for row in status.effects}


@pytest.mark.parametrize(
    "mutate",
    (
        lambda reconciliation: replace(reconciliation, status="stale"),
        lambda reconciliation: replace(
            reconciliation,
            fake_local_result_digest="not-a-digest",
        ),
        lambda reconciliation: replace(
            reconciliation,
            created_input_id="observe-curator-complete",
        ),
    ),
)
def test_learning_status_excludes_effect_rows_with_corrupt_reconciliation_links(
    mutate,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.reconcile_first_effect(
        lad_learning.learning_effect_proposal_state(plan, fingerprint),
    )
    effect_id = _effect_id_by_input(state, "observe-curator-complete")
    reconciliation = next(iter(state.effect_reconciliations.values()))
    drifted_state = replace(
        state,
        effect_reconciliations={
            reconciliation.reconciliation_id: mutate(reconciliation),
        },
    )

    status = operator_status(drifted_state)

    assert effect_id not in {row.effect_id for row in status.effects}


def test_learning_status_excludes_effect_rows_with_terminal_observation_drift() -> (
    None
):
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.learning_effect_proposal_state(plan, fingerprint)
    effect_id = _effect_id_by_input(state, "observe-curator-complete")

    status = operator_status(
        _replace_observation_marker(
            state,
            run_id="run-curator",
            marker="CURATOR_NOOP",
        ),
    )

    assert effect_id not in {row.effect_id for row in status.effects}


@pytest.mark.parametrize(
    "mutate",
    (
        lambda state, fanout: replace(
            state,
            work_items={
                **state.work_items,
                fanout.target_work_item_id: replace(
                    state.work_items[fanout.target_work_item_id],
                    lineage_id="wrong-lineage",
                ),
            },
        ),
        lambda state, fanout: replace(
            state,
            activations={
                **state.activations,
                fanout.target_activation_id: replace(
                    state.activations[fanout.target_activation_id],
                    lineage_id="wrong-lineage",
                ),
            },
        ),
        lambda state, fanout: replace(
            state,
            activations={
                **state.activations,
                fanout.target_activation_id: replace(
                    state.activations[fanout.target_activation_id],
                    runner_binding_id=RunnerBindingId("planning.lad.local_runner"),
                ),
            },
        ),
        lambda state, fanout: replace(
            state,
            fanout_records={
                **state.fanout_records,
                fanout.record_id: replace(
                    fanout,
                    target_graph_node_id="learning.standard.professor",
                ),
            },
        ),
    ),
)
def test_learning_status_excludes_generated_work_with_corrupt_projection_links(
    mutate,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.active_learning_with_generated_waiting_state(
        plan,
        fingerprint,
    )
    fanout = state.fanout_records[_first_generated_work_id(state)]

    status = operator_status(mutate(state, fanout))

    assert fanout.record_id not in {
        row.generated_work_id for row in status.generated_work
    }


def test_learning_status_excludes_generated_work_with_terminal_observation_drift() -> (
    None
):
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.active_learning_with_generated_waiting_state(
        plan,
        fingerprint,
    )
    fanout = state.fanout_records[_first_generated_work_id(state)]

    status = operator_status(
        _replace_observation_marker(
            state,
            run_id="run-doublechecker",
            marker="FIX_NEEDED",
        ),
    )

    assert fanout.record_id not in {
        row.generated_work_id for row in status.generated_work
    }


def test_learning_status_excludes_rows_when_default_plan_is_not_lad_learning() -> (
    None
):
    plan, fingerprint = lad_learning.compile_lad_learning()
    generated_state = lad_learning.active_learning_with_generated_waiting_state(
        plan,
        fingerprint,
    )
    generated_status = operator_status(_select_simple_loop_default(generated_state))

    assert generated_status.selected_plan is not None
    assert generated_status.selected_plan.workflow_id == "simple_loop"
    assert generated_status.artifacts == ()
    assert generated_status.generated_work == ()

    effect_state = lad_learning.reconcile_first_effect(
        lad_learning.learning_effect_proposal_state(plan, fingerprint),
    )
    effect_status = operator_status(_select_simple_loop_default(effect_state))

    assert effect_status.selected_plan is not None
    assert effect_status.selected_plan.workflow_id == "simple_loop"
    assert effect_status.artifacts == ()
    assert effect_status.effects == ()


def test_learning_status_projects_c3_family_combined_after_restart(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.closed_source_learning_effect_state(
        plan,
        fingerprint,
        reconciliation_status="applied",
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)
    status = operator_status(loaded)

    generated = next(
        row
        for row in status.generated_work
        if row.item_key == "closed-source-learning"
    )
    effect = next(iter(status.effects))
    artifacts = {artifact.source_input_id: artifact for artifact in status.artifacts}
    source_close = loaded.closed_work_items["work-consultant-closed-source"]

    assert str(source_close.action_id) == "execution.close_consultant_needs_plan"
    assert generated.fanout_id == "learning.trigger.execution.needs_planning"
    assert generated.source_action_id == "execution.close_consultant_needs_plan"
    assert generated.source_input_id == "observe-consultant-closed-source"
    assert generated.target_stage_kind_id == "analyst"
    assert generated.lineage_id == "work-consultant-closed-source"
    assert artifacts["observe-consultant-closed-source"].latest_marker == (
        "NEEDS_PLANNING"
    )
    assert artifacts["observe-closed-source-analyst-complete"].latest_marker == (
        "ANALYST_COMPLETE"
    )
    assert artifacts["observe-closed-source-professor-complete"].latest_marker == (
        "PROFESSOR_COMPLETE"
    )
    assert artifacts["observe-closed-source-curator-complete"].latest_marker == (
        "CURATOR_COMPLETE"
    )
    assert effect.status == "applied"
    assert effect.source_input_id == "observe-closed-source-curator-complete"
    assert effect.lineage_id == generated.lineage_id
    assert effect.reconciliation_id == (
        "transition-reconcile-closed-source-effect-applied:reconciliation"
    )
    assert _queue_family(status, "learning_request").quarantined_count == 0
    assert _queue_family(status, "stage_result").closed_count == 2


def test_learning_status_does_not_mask_closure_root_drift(tmp_path: Path) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.planning_closure_with_generated_learning_state(
        plan,
        fingerprint,
        active_learning=True,
    )
    state = lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-closure-librarian",
        marker="LIBRARIAN_COMPLETE",
        artifact=lad_learning.artifact_payload(
            lad_learning.LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID
        ),
        input_id="observe-librarian-complete-status",
    )
    legal_status = operator_status(state)
    assert legal_status.effects
    assert legal_status.artifacts
    closure = next(
        target
        for target in legal_status.closure_targets
        if target.closure_target_id == "closure-target-learning"
    )
    assert closure.closure_root_work_item_id == "root-spec-closure"
    assert closure.root_source_kind == "spec"
    assert closure.root_source_id == "closure-source-1"
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = ?
            WHERE closure_target_id = ?
            """,
            ("wrong-root-work-item", "closure-target-learning"),
        )

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        load_runtime_state(db_path, cas_root)


def _closure_root_c3e_status_state(
    state_kind: str,
):
    plan, fingerprint = lad_learning.compile_lad_learning()
    if state_kind == "active_wait":
        state, _wait = lad_learning.closure_librarian_blocked_wait_state(
            plan,
            fingerprint,
            input_id="observe-closure-librarian-blocked-c3e-status",
        )
        return state
    if state_kind in {"resolved_close", "resolved_revise"}:
        state, wait = lad_learning.closure_librarian_blocked_wait_state(
            plan,
            fingerprint,
            input_id=f"observe-closure-librarian-blocked-{state_kind}",
        )
        if state_kind == "resolved_close":
            transition_input = OperatorCloseWait(
                "operator-close-closure-librarian-c3e-status",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local_operator",
                actor_kind="local_operator",
                payload={},
            )
            transition_context = lad_learning.context(
                "operator-close-closure-librarian-c3e-status"
            )
        else:
            transition_input = OperatorReviseWait(
                "operator-revise-closure-librarian-c3e-status",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local_operator",
                actor_kind="local_operator",
                payload=lad_learning.learning_payload(
                    request_id="closure-librarian-revised-status",
                    body="Operator revised the closure-root Learning request.",
                ),
            )
            transition_context = lad_learning.context(
                "operator-revise-closure-librarian-c3e-status",
                work_item_id="work-closure-librarian-revised-status",
                activation_id="activation-closure-librarian-revised-status",
            )
        return lad_learning.apply_accepted_input(
            state,
            transition_input,
            transition_context,
        )
    if state_kind == "terminal_noop":
        return lad_learning.closure_librarian_terminal_state(
            plan,
            fingerprint,
            outcome="noop",
            input_id="observe-closure-librarian-noop-c3e-status",
        )
    if state_kind == "effect_pending":
        return lad_learning.closure_librarian_effect_state(
            plan,
            fingerprint,
        )
    if state_kind.startswith("effect_"):
        return lad_learning.closure_librarian_effect_state(
            plan,
            fingerprint,
            reconciliation_status=state_kind.removeprefix("effect_"),
        )
    raise AssertionError(f"unhandled C3E closure-root state kind: {state_kind}")


@pytest.mark.parametrize(
    ("state_kind", "expected_effect_status", "expected_wait_resolution"),
    (
        ("active_wait", None, None),
        ("resolved_close", None, "close_recorded_source"),
        ("resolved_revise", None, "revise_recorded_source"),
        ("terminal_noop", None, None),
        ("effect_pending", "pending", None),
        ("effect_applied", "applied", None),
        ("effect_no_op", "no_op", None),
        ("effect_refused", "refused", None),
    ),
)
def test_learning_status_projects_closure_root_with_recovery_wait_intervention_and_quarantine(  # noqa: E501
    tmp_path: Path,
    state_kind: str,
    expected_effect_status: str | None,
    expected_wait_resolution: str | None,
) -> None:
    state = _closure_root_c3e_status_state(state_kind)
    status = operator_status(persist_and_load_runtime_state(tmp_path, state))
    closure = next(
        target
        for target in status.closure_targets
        if target.closure_target_id == "closure-target-learning"
    )

    assert closure.status == "open"
    assert closure.closure_root_work_item_id == "root-spec-closure"
    assert closure.root_source_kind == "spec"
    assert closure.root_source_id == "closure-source-1"
    assert status.quarantines == ()
    assert _queue_family(status, "learning_request").quarantined_count == 0

    if expected_effect_status is None:
        assert status.effects == ()
    else:
        effect = next(iter(status.effects))
        generated = next(
            row
            for row in status.generated_work
            if row.item_key == "closure-librarian-learning"
        )
        assert effect.status == expected_effect_status
        assert effect.lineage_id == closure.lineage_id
        assert effect.source_work_item_id == generated.target_work_item_id

    if state_kind == "active_wait":
        active_wait = next(iter(status.operator_waits))
        assert active_wait.status == "active"
        assert active_wait.operator_wait_id == "learning.librarian_blocked_wait"
        assert active_wait.lineage_id == closure.lineage_id
        assert _queue_family(status, "learning_request").operator_wait_count == 1
    elif expected_wait_resolution is not None:
        resolved_wait = next(iter(status.operator_waits))
        assert resolved_wait.status == "resolved"
        assert resolved_wait.resolution_kind == expected_wait_resolution
        assert resolved_wait.actor_id == "local_operator"
        assert resolved_wait.actor_kind == "local_operator"
        assert resolved_wait.lineage_id == closure.lineage_id
    else:
        assert status.operator_waits == ()
