from __future__ import annotations

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.contracts.transition import ClaimWork
from millrace.kernel import apply, decide
from support.kernel_ping import (
    action_by_id,
    apply_accepted_input,
    compile_kernel_ping,
    kernel_ping_context,
    mutation_kinds,
    runner_observation,
    task_artifact_payload,
)


def test_prompt_to_taskmaster_to_worker_closes_without_pause_or_quarantine() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(
        plan,
        fingerprint,
        body="Build the full in-memory close proof",
    )
    taskmaster_run = state.runs["run-taskmaster"]
    assert taskmaster_run.run_ref.plan_ref.authority_fingerprint == fingerprint

    taskmaster_decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(objective="Prove the close route"),
        ),
        kernel_ping_context("observe-taskmaster"),
    )
    assert taskmaster_decision.accepted is True
    state = apply(state, taskmaster_decision)

    state = apply_accepted_input(
        state,
        ClaimWork("claim-worker", activation_id="activation-worker"),
        kernel_ping_context("claim-worker"),
    )
    worker_run = state.runs["run-worker"]
    assert worker_run.run_ref.plan_ref.authority_fingerprint == fingerprint

    close_action = action_by_id(plan, "kernel_ping.close_worker_success")
    close_decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.close_worker_success",
            input_id="observe-worker",
            artifact_payload={},
        ),
        kernel_ping_context("observe-worker"),
    )

    assert close_decision.accepted is True
    assert "mutation.close_work_item" in mutation_kinds(close_decision)
    assert "mutation.set_pause" not in mutation_kinds(close_decision)
    assert "mutation.set_quarantine" not in mutation_kinds(close_decision)

    closed = apply(state, close_decision)

    assert closed.closed_work_items["work-task-artifact"].action_id == close_action.id
    assert closed.pause is None
    assert closed.quarantines == {}
    assert {
        "enqueue",
        "claim-taskmaster",
        "observe-taskmaster",
        "claim-worker",
        "observe-worker",
    } <= {record.input_id for record in closed.transitions}
    assert {event.input_id for event in closed.governance_events} >= {
        "observe-taskmaster",
        "observe-worker",
    }
    assert {trace.input_id for trace in closed.traces} >= {
        "observe-taskmaster",
        "observe-worker",
    }
    assert any(
        event.input_id == "observe-worker"
        and event.disposition == "accepted"
        and event.action_id == close_action.id
        for event in closed.governance_events
    )
