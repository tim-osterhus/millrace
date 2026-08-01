from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.contracts.state import ClosedWorkItemRecord
from millrace.contracts.transition import OperatorCloseWait
from millrace.kernel import apply, decide
from millrace.operator import (
    OperatorInputError,
    OperatorResumeWaitInput,
    OperatorReviseWaitInput,
    build_resume_wait,
    build_revise_wait,
    operator_status,
)
from support import generic_operator_wait
from support import kernel_ping as kernel_ping_support


def test_operator_wait_projection_lists_selected_resolution_kinds() -> None:
    state, _plan, _fingerprint, wait_id = (
        generic_operator_wait.active_revise_wait_state()
    )
    wait = state.operator_waits[wait_id]

    status = operator_status(state)

    assert len(status.operator_waits) == 1
    projected = status.operator_waits[0]
    assert projected.wait_id == wait.wait_id
    assert projected.operator_wait_id == generic_operator_wait.REVISE_WAIT_ID
    assert projected.source_action_id == generic_operator_wait.REVISE_ACTION_ID
    assert projected.status == "active"
    assert projected.allowed_resolution_kinds == (
        "resume_recorded_source",
        "revise_recorded_source",
    )
    assert projected.actor_kind_requirement == "local_operator"
    assert projected.payload_schema_id == "kernel_ping.task_artifact"
    assert projected.target_queue_family_id == "prompt"
    assert projected.target_stage_kind_id == "kernel_ping.taskmaster"
    assert projected.target_graph_node_id == "kernel_ping.taskmaster.start"
    assert projected.target_runner_binding_id == "kernel_ping.taskmaster_runner"
    assert projected.status_effect == "operator_wait_active"
    assert projected.source_artifact_id == (
        "transition-observe-taskmaster-needs-detail:artifact"
    )
    assert projected.target_work_item_id is None
    assert projected.target_activation_id is None


def test_operator_wait_refuses_duplicate_stale_wrong_plan_or_status_decisions() -> (
    None
):
    state, _plan, _fingerprint, wait_id = (
        generic_operator_wait.active_revise_wait_state()
    )
    wait = state.operator_waits[wait_id]
    resume = build_resume_wait(
        state,
        OperatorResumeWaitInput(
            input_id="operator-resume-source",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=None,
            actor_id="local-operator",
            actor_kind="local_operator",
        ),
    )
    resumed_decision = decide(
        state,
        resume,
        kernel_ping_support.kernel_ping_context("operator-resume-source"),
    )
    resumed = apply(state, resumed_decision)
    assert resumed_decision.accepted is True

    duplicate_decision = decide(
        resumed,
        replace(resume, input_id="operator-resume-source-duplicate"),
        kernel_ping_support.kernel_ping_context("operator-resume-source-duplicate"),
    )
    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "operator_wait_not_active"

    closed_source = replace(
        state,
        closed_work_items={
            wait.source_work_item_id: ClosedWorkItemRecord(
                record_id="closed-stale-source",
                work_item_id=wait.source_work_item_id,
                source_run_id=wait.source_run_id,
                action_id=wait.source_action_id,
                created_by_input_id="stale-source-close",
            )
        },
    )
    stale_decision = decide(
        closed_source,
        replace(resume, input_id="operator-resume-stale-source"),
        kernel_ping_support.kernel_ping_context("operator-resume-stale-source"),
    )
    assert stale_decision.accepted is False
    assert stale_decision.refusal is not None
    assert stale_decision.refusal.reason == "work_item_closed"
    assert apply(closed_source, stale_decision).operator_waits[wait.wait_id].status == (
        "active"
    )

    refusal_cases = (
        (
            replace(
                resume,
                input_id="operator-resume-wrong-plan",
                selected_plan_ref=replace(
                    wait.selected_plan_ref,
                    authority_fingerprint=f"sha256:{'0' * 64}",
                ),
            ),
            "unknown_plan_ref",
        ),
        (
            replace(
                resume,
                input_id="operator-resume-wrong-wait",
                wait_id="operator-wait:missing",
            ),
            "unknown_operator_wait",
        ),
        (
            replace(
                resume,
                input_id="operator-resume-wrong-actor",
                actor_kind="remote_operator",
            ),
            "invalid_actor_kind",
        ),
    )
    for transition_input, reason in refusal_cases:
        decision = decide(
            state,
            transition_input,
            kernel_ping_support.kernel_ping_context(transition_input.input_id),
        )
        assert decision.accepted is False
        assert decision.refusal is not None
        assert decision.refusal.reason == reason

    unsupported = decide(
        state,
        OperatorCloseWait(
            "operator-close-unsupported-wait",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local-operator",
            actor_kind="local_operator",
            payload={},
        ),
        kernel_ping_support.kernel_ping_context("operator-close-unsupported-wait"),
    )
    assert unsupported.accepted is False
    assert unsupported.refusal is not None
    assert unsupported.refusal.reason == "invalid_operator_wait_resolution"

    status = operator_status(state)
    with pytest.raises(OperatorInputError) as exc_info:
        build_revise_wait(
            state,
            OperatorReviseWaitInput(
                input_id="operator-revise-from-status",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=None,
                actor_id="local-operator",
                actor_kind="local_operator",
                payload={"status": status.operator_waits[0].status},
            ),
        )
    assert exc_info.value.reason == "invalid_payload_schema"
