from __future__ import annotations

import pytest

from kernel.kernel_ping_scenarios import (
    admit_select_enqueue_two_and_claim_first,
    bootstrap_two_prompt_state_to_worker_claim,
)
from millrace.contracts.transition import ClaimWork
from millrace.kernel import StateConcurrencyError, apply
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping
from support import kernel_ping as kernel_ping_support
from support.kernel_ping import (
    action_by_id,
    compile_kernel_ping,
    kernel_ping_context,
    mutation_kinds,
    runner_observation,
    task_artifact_payload,
)


def test_taskmaster_blocked_declared_action_pauses_quarantines_and_stops_claims() -> (
    None
):
    plan, fingerprint = compile_kernel_ping(kernel_ping.workflow_source())
    state = admit_select_enqueue_two_and_claim_first(plan, fingerprint)

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster-a",
            action_id="kernel_ping.pause_taskmaster_blocked",
            input_id="observe-taskmaster-blocked",
            artifact_payload={},
        ),
        kernel_ping_context("observe-taskmaster-blocked"),
    )

    assert decision.accepted is True
    assert {"mutation.set_pause", "mutation.set_quarantine"} <= set(
        mutation_kinds(decision)
    )
    paused = apply(state, decision)
    assert paused.pause is not None
    assert (
        paused.pause.action_id
        == action_by_id(
            plan,
            "kernel_ping.pause_taskmaster_blocked",
        ).id
    )
    assert set(paused.quarantines) == {"work-prompt-a"}

    claim_later = decide(
        paused,
        ClaimWork("claim-taskmaster-b", activation_id="activation-taskmaster-b"),
        deterministic_context(
            transition_id="transition-claim-taskmaster-b",
            run_id="run-taskmaster-b",
            claim_id="claim-taskmaster-b",
            fencing_token="fence-taskmaster-b",
        ),
    )
    after_claim_later = apply(paused, claim_later)
    assert claim_later.accepted is False
    assert claim_later.refusal is not None
    assert claim_later.refusal.reason == "workspace_paused"
    assert after_claim_later.runs == paused.runs
    assert after_claim_later.pause == paused.pause
    assert after_claim_later.quarantines == paused.quarantines


def test_stale_claim_decision_cannot_apply_after_pause_is_set() -> None:
    plan, fingerprint = compile_kernel_ping(kernel_ping.workflow_source())
    state = admit_select_enqueue_two_and_claim_first(plan, fingerprint)
    stale_claim_b = decide(
        state,
        ClaimWork("claim-taskmaster-b", activation_id="activation-taskmaster-b"),
        deterministic_context(
            transition_id="transition-claim-taskmaster-b",
            run_id="run-taskmaster-b",
            claim_id="claim-taskmaster-b",
            fencing_token="fence-taskmaster-b",
        ),
    )
    assert stale_claim_b.accepted is True

    paused = apply(
        state,
        decide(
            state,
            runner_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-taskmaster-a",
                action_id="kernel_ping.pause_taskmaster_blocked",
                input_id="observe-taskmaster-blocked",
                artifact_payload={},
            ),
            kernel_ping_context("observe-taskmaster-blocked"),
        ),
    )

    with pytest.raises(StateConcurrencyError, match="pause state changed"):
        apply(paused, stale_claim_b)


def test_worker_blocked_declared_action_pauses_and_quarantines_current_work() -> None:
    plan, fingerprint = compile_kernel_ping(kernel_ping.workflow_source())
    state = bootstrap_two_prompt_state_to_worker_claim(plan, fingerprint)

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.pause_worker_blocked",
            input_id="observe-worker-blocked",
            artifact_payload={},
        ),
        kernel_ping_context("observe-worker-blocked"),
    )

    assert decision.accepted is True
    assert {"mutation.set_pause", "mutation.set_quarantine"} <= set(
        mutation_kinds(decision)
    )
    after = apply(state, decision)
    assert after.pause is not None
    assert set(after.quarantines) == {"work-task-artifact"}
    assert after.quarantines["work-task-artifact"].source_run_id == "run-worker"


def test_no_pause_revision_changes_fingerprint_and_blocked_marker_does_not_pause() -> (
    None
):
    base_plan, base_fingerprint = compile_kernel_ping(kernel_ping.workflow_source())
    no_pause_plan, no_pause_fingerprint = compile_kernel_ping(
        kernel_ping_support.no_pause_workflow_source()
    )
    assert no_pause_fingerprint != base_fingerprint

    state = admit_select_enqueue_two_and_claim_first(
        no_pause_plan, no_pause_fingerprint
    )
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=no_pause_plan,
            fingerprint=no_pause_fingerprint,
            run_id="run-taskmaster-a",
            action_id="kernel_ping.pause_taskmaster_blocked",
            input_id="observe-taskmaster-no-pause",
            artifact_payload=task_artifact_payload(
                source_prompt_id="prompt-a",
                objective="Prove blocked behavior",
            ),
        ),
        kernel_ping_context("observe-taskmaster-no-pause"),
    )

    assert decision.accepted is True
    assert "mutation.set_pause" not in mutation_kinds(decision)
    assert "mutation.set_quarantine" not in mutation_kinds(decision)
    after = apply(state, decision)
    assert after.pause is None
    assert after.quarantines == {}
    assert "activation-no-pause-route" in after.activations
    assert base_plan.terminal_actions != no_pause_plan.terminal_actions
