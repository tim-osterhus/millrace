from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.contracts import (
    ClaimWork,
    EnqueueWork,
    QueueFamilyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.state import Activation, WorkItem, WorkItemRef
from millrace.kernel import decide
from millrace.testing import fake_runner_dispatch_envelope_for_run
from support import lad_learning


def _learning_work_items(state):
    return [
        item
        for item in state.work_items.values()
        if item.queue_family_id == QueueFamilyId("learning_request")
    ]


def test_execution_terminal_result_generates_deduped_learning_request() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.ready_learning_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-analyst",
        input_id="claim-analyst",
    )
    state = lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-analyst",
        marker="ANALYST_NOOP",
        artifact=lad_learning.artifact_payload(
            lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
        ),
        input_id="observe-analyst-noop",
    )

    state = _source_doublechecker_pass_state(plan, fingerprint)

    generated = _learning_work_items(state)
    assert len(generated) == 1
    generated_item = next(
        item for item in generated if item.created_by_input_id == "observe-doublecheck"
    )
    assert generated_item.payload["request_id"] == "generated-learning-1"
    assert generated_item.lineage_id == "work-task"
    fanout_records = [
        record
        for record in state.fanout_records.values()
        if str(record.fanout_id)
        == "learning.trigger.execution.doublechecker_pass"
    ]
    assert len(fanout_records) == 1

    replay_decision = decide(
        state,
        ClaimWork(
            "claim-generated-learning",
            activation_id=fanout_records[0].target_activation_id,
        ),
        lad_learning.context(
            "claim-generated-learning",
            run_id="run-generated-learning",
        ),
    )
    assert replay_decision.accepted is True


def test_source_terminal_accepts_while_learning_active() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.ready_learning_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-active-learning",
        input_id="claim-active-learning",
    )
    state = _source_doublechecker_pass_state(plan, fingerprint, state=state)

    generated = [
        record
        for record in state.fanout_records.values()
        if str(record.fanout_id)
        == "learning.trigger.execution.doublechecker_pass"
    ][0]
    decision = decide(
        state,
        ClaimWork(
            "claim-second-learning",
            activation_id=generated.target_activation_id,
        ),
        lad_learning.context(
            "claim-second-learning",
            run_id="run-second-learning",
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "concurrency_policy_blocked"


@pytest.mark.parametrize("queue_family_id", ("spec", "task"))
def test_learning_can_claim_beside_selected_planning_or_execution(
    queue_family_id: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _active_foreground_state(
        plan,
        fingerprint,
        queue_family_id=queue_family_id,
    )
    state = lad_learning.apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-learning-request",
            queue_family_id=QueueFamilyId("learning_request"),
            payload=lad_learning.learning_payload(),
        ),
        lad_learning.context(
            "enqueue-learning-request",
            work_item_id="work-learning-request",
            activation_id="activation-learning-request",
        ),
    )

    decision = decide(
        state,
        ClaimWork("claim-learning", activation_id="activation-learning-request"),
        lad_learning.context("claim-learning", run_id="run-learning"),
    )

    assert decision.accepted is True


def test_planning_and_execution_remain_mutually_exclusive() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _active_foreground_state(plan, fingerprint, queue_family_id="spec")
    state = lad_learning.apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-task",
            queue_family_id=QueueFamilyId("task"),
            payload={"task_id": "task-1", "body": "Run execution work."},
        ),
        lad_learning.context(
            "enqueue-task",
            work_item_id="work-task",
            activation_id="activation-task",
        ),
    )

    decision = decide(
        state,
        ClaimWork("claim-execution", activation_id="activation-task"),
        lad_learning.context("claim-execution", run_id="run-execution"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "concurrency_policy_blocked"


def test_needs_planning_learning_trigger_uses_close_with_escalation_source() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.ready_learning_state(plan, fingerprint)
    state = lad_learning.claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-analyst",
        input_id="claim-analyst",
    )
    state = lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-analyst",
        marker="ANALYST_NOOP",
        artifact=lad_learning.artifact_payload(
            lad_learning.LEARNING_RESEARCH_PACKET_SCHEMA_ID
        ),
        input_id="observe-analyst-noop",
    )

    source = _source_consultant_needs_planning_state(plan, fingerprint, state=state)

    assert any(
        str(record.source_action_id) == "execution.close_consultant_needs_plan"
        and str(record.fanout_id) == "learning.trigger.execution.needs_planning"
        for record in source.fanout_records.values()
    )


@pytest.mark.parametrize(
    (
        "stage_kind_id",
        "graph_node_id",
        "source_action_id",
        "fanout_id",
        "source_artifact_schema_id",
    ),
    (
        (
            "lad_troubleshooter",
            "execution.lad.troubleshooter.start",
            "execution.route_troubleshooter_blocked",
            "learning.trigger.execution.troubleshooter_blocked",
            "execution.artifacts.stage_result",
        ),
        (
            "lad_consultant",
            "execution.lad.consultant.start",
            "execution.close_consultant_blocked",
            "learning.trigger.execution.consultant_blocked",
            "execution.artifacts.report",
        ),
    ),
)
def test_blocked_execution_results_generate_learning_requests(
    stage_kind_id: str,
    graph_node_id: str,
    source_action_id: str,
    fanout_id: str,
    source_artifact_schema_id: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _source_execution_terminal_state(
        plan,
        fingerprint,
        stage_kind_id=stage_kind_id,
        graph_node_id=graph_node_id,
        marker="BLOCKED",
        artifact={
            "artifact_kind": source_artifact_schema_id,
            "summary": "Blocked source",
            "learning_requests": (
                {
                    "request_id": f"{stage_kind_id}-blocked-learning",
                    "body": "Capture blocked execution lesson.",
                    "root_source": {
                        "kind": "trigger",
                        "source_id": f"{stage_kind_id}-blocked-learning",
                    },
                },
            ),
        },
        input_id=f"observe-{stage_kind_id}-blocked",
    )

    fanout = next(
        record
        for record in state.fanout_records.values()
        if str(record.fanout_id) == fanout_id
    )
    assert str(fanout.source_action_id) == source_action_id
    generated_work = state.work_items[fanout.target_work_item_id]
    assert generated_work.queue_family_id == QueueFamilyId("learning_request")
    assert generated_work.payload["request_id"] == f"{stage_kind_id}-blocked-learning"


def test_planner_complete_dispatches_targeted_librarian_route() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = _source_planner_complete_state(plan, fingerprint)
    fanout = next(
        record
        for record in state.fanout_records.values()
        if str(record.fanout_id) == "learning.trigger.planning.planner_complete"
    )

    state = lad_learning.claim(
        state,
        activation_id=fanout.target_activation_id,
        run_id="run-librarian",
        input_id="claim-librarian",
    )
    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-librarian",
    )

    assert dispatch.graph_node_id == "learning.standard.librarian"
    assert dispatch.stage_kind_id == "librarian"
    assert dispatch.queue_family_id == "learning_request"
    assert (
        dispatch.work_item_payload["target_skill_id"]
        == "planning.skills.planner_core"
    )
    assert dispatch.work_item_payload["preferred_output_paths"] == (
        "skills/stage/planning/planner-core/SKILL.md",
    )
    source = dispatch.governance_context["generated_work_source"]
    assert source["fanout_record_id"] == fanout.record_id
    assert source["fanout_id"] == "learning.trigger.planning.planner_complete"
    assert source["source_work_item_id"] == fanout.source_work_item_id
    assert source["source_run_id"] == "run-planner"
    assert source["source_action_id"] == "planning.route_planner_complete"
    assert source["source_artifact_id"] == fanout.source_artifact_id
    assert source["item_key"] == "planner-librarian-learning"


@pytest.mark.parametrize("active_learning", (False, True))
def test_learning_concurrency_preserves_planning_closure_root_authority(
    active_learning: bool,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.planning_closure_with_generated_learning_state(
        plan,
        fingerprint,
        active_learning=active_learning,
    )

    target = state.closure_targets["closure-target-learning"]
    root = state.work_items["root-spec-closure"]
    fanout = next(
        record
        for record in state.fanout_records.values()
        if str(record.fanout_id) == "learning.trigger.planning.planner_complete"
        and record.item_key == "closure-librarian-learning"
    )
    learning_work = state.work_items[fanout.target_work_item_id]
    learning_activation = state.activations[fanout.target_activation_id]

    assert target.status == "open"
    assert target.lineage_id == "root-spec-closure"
    assert target.root_source_kind == "spec"
    assert target.root_source_id == "closure-source-1"
    assert target.closure_root_work_item_id == root.ref.work_item_id
    assert root.lineage_id == target.lineage_id
    assert root.payload["root_source"] == {
        "kind": "spec",
        "source_id": "closure-source-1",
    }
    assert learning_work.lineage_id == target.lineage_id
    assert learning_activation.lineage_id == target.lineage_id
    if active_learning:
        run = state.runs["run-closure-librarian"]
        assert run.work_item_id == fanout.target_work_item_id
        assert run.activation_id == fanout.target_activation_id


def _source_doublechecker_pass_state(plan, fingerprint, state=None):
    state = state or lad_learning.admitted_state(plan, fingerprint)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id="work-doublechecker",
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId("stage_result"),
        payload={
            "artifact_kind": "execution.artifacts.stage_result",
            "summary": "source",
        },
        lineage_id="work-task",
        created_by_input_id="seed-doublechecker",
    )
    activation = Activation(
        activation_id="activation-doublechecker",
        work_item_id="work-doublechecker",
        lineage_id="work-task",
        plan_ref=plan_ref,
        queue_family_id=QueueFamilyId("stage_result"),
        graph_node_id="execution.lad.doublechecker.start",
        stage_kind_id=StageKindId("lad_doublechecker"),
        runner_binding_id=RunnerBindingId("execution.lad.local_runner"),
        generation=0,
        created_by_input_id="seed-doublechecker",
    )
    state = replace(
        state,
        work_items={**state.work_items, work_item.ref.work_item_id: work_item},
        activations={**state.activations, activation.activation_id: activation},
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-doublechecker",
        run_id="run-doublechecker",
        input_id="claim-doublechecker",
    )
    return lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-doublechecker",
        marker="DOUBLECHECK_PASS",
        artifact=lad_learning.source_artifact_with_learning_request(),
        input_id="observe-doublecheck",
        work_item_id="work-updater",
        activation_id="activation-updater",
    )


def _source_planner_complete_state(plan, fingerprint):
    state = lad_learning.admitted_state(plan, fingerprint)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id="work-planner",
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId("stage_result"),
        payload={
            "artifact_kind": "planning.artifacts.stage_result",
            "summary": "source",
        },
        lineage_id="work-planner",
        created_by_input_id="seed-planner",
    )
    activation = Activation(
        activation_id="activation-planner",
        work_item_id="work-planner",
        lineage_id="work-planner",
        plan_ref=plan_ref,
        queue_family_id=QueueFamilyId("stage_result"),
        graph_node_id="planning.lad.planner.start",
        stage_kind_id=StageKindId("lad_planner"),
        runner_binding_id=RunnerBindingId("planning.lad.local_runner"),
        generation=0,
        created_by_input_id="seed-planner",
    )
    state = replace(
        state,
        work_items={**state.work_items, work_item.ref.work_item_id: work_item},
        activations={**state.activations, activation.activation_id: activation},
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-planner",
        run_id="run-planner",
        input_id="claim-planner",
    )
    return lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-planner",
        marker="PLANNER_COMPLETE",
        artifact={
            "artifact_kind": "planning.artifacts.stage_result",
            "summary": "Planner complete",
            "learning_requests": (
                {
                    "request_id": "planner-librarian-learning",
                    "body": "Prepare durable planner skill update.",
                    "root_source": {
                        "kind": "trigger",
                        "source_id": "planner-librarian-learning",
                    },
                    "target_skill_id": "planning.skills.planner_core",
                    "preferred_output_paths": (
                        "skills/stage/planning/planner-core/SKILL.md",
                    ),
                },
            ),
        },
        input_id="observe-planner-complete",
        work_item_id="work-manager",
        activation_id="activation-manager",
    )


def _source_consultant_needs_planning_state(plan, fingerprint, state):
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id="work-consultant",
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId("stage_result"),
        payload={
            "artifact_kind": "execution.artifacts.stage_result",
            "summary": "source",
        },
        lineage_id="work-consultant",
        created_by_input_id="seed-consultant",
    )
    activation = Activation(
        activation_id="activation-consultant",
        work_item_id="work-consultant",
        lineage_id="work-consultant",
        plan_ref=plan_ref,
        queue_family_id=QueueFamilyId("stage_result"),
        graph_node_id="execution.lad.consultant.start",
        stage_kind_id=StageKindId("lad_consultant"),
        runner_binding_id=RunnerBindingId("execution.lad.local_runner"),
        generation=0,
        created_by_input_id="seed-consultant",
    )
    state = replace(
        state,
        work_items={**state.work_items, work_item.ref.work_item_id: work_item},
        activations={**state.activations, activation.activation_id: activation},
    )
    state = lad_learning.claim(
        state,
        activation_id="activation-consultant",
        run_id="run-consultant",
        input_id="claim-consultant",
    )
    return lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant",
        marker="NEEDS_PLANNING",
        artifact={
            "artifact_kind": "execution.artifacts.incident_report",
            "summary": "Needs planning",
            "learning_requests": (
                {
                    "request_id": "needs-planning-learning",
                    "body": "Capture planning escalation lesson.",
                    "root_source": {
                        "kind": "trigger",
                        "source_id": "needs-planning-learning",
                    },
                },
            ),
        },
        input_id="observe-consultant-needs-planning",
    )


def _source_execution_terminal_state(
    plan,
    fingerprint,
    *,
    stage_kind_id: str,
    graph_node_id: str,
    marker: str,
    artifact,
    input_id: str,
):
    state = lad_learning.admitted_state(plan, fingerprint)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    work_item_id = f"work-{stage_kind_id}"
    activation_id = f"activation-{stage_kind_id}"
    run_id = f"run-{stage_kind_id}"
    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id=work_item_id,
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId("stage_result"),
        payload={
            "artifact_kind": "execution.artifacts.stage_result",
            "summary": "source",
        },
        lineage_id=work_item_id,
        created_by_input_id=f"seed-{stage_kind_id}",
    )
    activation = Activation(
        activation_id=activation_id,
        work_item_id=work_item_id,
        lineage_id=work_item_id,
        plan_ref=plan_ref,
        queue_family_id=QueueFamilyId("stage_result"),
        graph_node_id=graph_node_id,
        stage_kind_id=StageKindId(stage_kind_id),
        runner_binding_id=RunnerBindingId("execution.lad.local_runner"),
        generation=0,
        created_by_input_id=f"seed-{stage_kind_id}",
    )
    state = replace(
        state,
        work_items={**state.work_items, work_item_id: work_item},
        activations={**state.activations, activation_id: activation},
    )
    state = lad_learning.claim(
        state,
        activation_id=activation_id,
        run_id=run_id,
        input_id=f"claim-{stage_kind_id}",
    )
    return lad_learning.observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        marker=marker,
        artifact=artifact,
        input_id=input_id,
        work_item_id=f"work-generated-{stage_kind_id}",
        activation_id=f"activation-generated-{stage_kind_id}",
    )


def _active_foreground_state(plan, fingerprint, *, queue_family_id: str):
    state = lad_learning.admitted_state(plan, fingerprint)
    payload = (
        {
            "title": "Spec input",
            "body": "Shape planning work.",
            "root_source": {"kind": "spec", "source_id": "spec-1"},
        }
        if queue_family_id == "spec"
        else {"task_id": "task-1", "body": "Run execution work."}
    )
    state = lad_learning.apply_accepted_input(
        state,
        EnqueueWork(
            f"enqueue-{queue_family_id}",
            queue_family_id=QueueFamilyId(queue_family_id),
            payload=payload,
        ),
        lad_learning.context(
            f"enqueue-{queue_family_id}",
            work_item_id=f"work-{queue_family_id}",
            activation_id=f"activation-{queue_family_id}",
        ),
    )
    return lad_learning.claim(
        state,
        activation_id=f"activation-{queue_family_id}",
        run_id=f"run-{queue_family_id}",
        input_id=f"claim-{queue_family_id}",
    )
