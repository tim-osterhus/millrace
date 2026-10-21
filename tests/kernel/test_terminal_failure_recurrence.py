from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.runtime_failures import runtime_failure_applied
from millrace.contracts.transition import RunnerResultObserved, TimerDue
from millrace.kernel import apply, decide
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_admission
from tests.compiler.test_runtime_failure_mappings import _compile
from tests.kernel.test_runtime_failure_adversarial_review import (
    _apply_failure,
    _claim,
    _timeout_for_run,
)
from tests.kernel.test_runtime_failure_recurrence_review import _recurrence_source
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_parent_claimed_state,
)


def _terminal_failure(state, run_id, kind, observed_at):
    if kind != "result_rejected":
        state, completion = _timeout_for_run(state, run_id, observed_at)
        completion = replace(completion, adapter_error_kind=kind)
        return replace(
            state,
            runner_session_completions={
                **state.runner_session_completions,
                completion.session_id: completion,
            },
        ), completion
    run = state.runs[run_id]
    observation = RunnerResultObserved(
        f"rejected-{run_id}",
        run_id=run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker="UNDECLARED_RESULT",
            artifact_payload=None,
        ),
        observed_at=observed_at,
    )
    state, observation = fake_completed_runner_observation_state(
        state=state, observation=observation
    )
    rejected = decide(
        state, observation, deterministic_context(transition_id=f"reject-{run_id}")
    )
    assert not rejected.accepted
    assert rejected.refusal.reason == "undeclared_terminal_outcome"
    state = apply(state, rejected)
    completion = state.runner_session_completions[state.runs[run_id].current_session_id]
    return state, completion


@pytest.mark.parametrize(
    "failure_kind",
    [
        "timeout",
        "invocation_failed",
        "result_parse_failed",
        "output_too_large",
        "result_rejected",
    ],
)
def test_each_terminal_failure_remains_bounded_when_recovery_itself_fails(
    monkeypatch, failure_kind
):
    source = _recurrence_source()
    for mapping in source["runner_bindings"][0]["runtime_failure_mappings"]:
        mapping["failure_kind"] = failure_kind
    compiled = _compile(source)
    assert compiled.plan is not None, compiled.diagnostics
    plan = compiled.plan
    monkeypatch.setattr(
        generic_admission, "compile_plan", lambda: (plan, authority_fingerprint(plan))
    )
    state, _, _ = _generic_parent_claimed_state()
    state, first = _terminal_failure(state, "run-generic-parent", failure_kind, 2000)
    state, first_command, first_context = _apply_failure(state, first, suffix="first")
    state = _claim(state, "review-recovery-first", "recovery-second")
    state, second = _terminal_failure(state, "recovery-second", failure_kind, 3000)
    state, second_command, second_context = _apply_failure(
        state, second, suffix="second"
    )
    attempt = next(iter(state.recovery_attempts.values()))
    assert (attempt.attempt_count, attempt.phase) == (2, "pending_cooldown")
    wait = next(iter(state.cooldown_waits.values()))
    early = decide(
        state,
        TimerDue("early", wait_id=wait.wait_id, observed_at=wait.due_at - 1),
        deterministic_context(),
    )
    assert not early.accepted
    due = decide(
        state,
        TimerDue("due", wait_id=wait.wait_id, observed_at=wait.due_at),
        deterministic_context(transition_id="due", activation_id="after-due"),
    )
    assert due.accepted
    state = apply(state, due)
    state = _claim(state, "after-due", "recovery-third")
    state, third = _terminal_failure(
        state, "recovery-third", failure_kind, wait.due_at + 1
    )
    state, third_command, third_context = _apply_failure(state, third, suffix="third")
    assert len(state.lineage_quarantines) == 1
    assert not list_ready_dispatch_candidates(state).candidates
    assert state.counters == {}
    assert state.runner_observations == {}
    assert state.artifacts == {}
    for completion, command, context in [
        (first, first_command, first_context),
        (second, second_command, second_context),
        (third, third_command, third_context),
    ]:
        assert runtime_failure_applied(state, completion.run_id)
        assert state.runner_session_completions[completion.session_id] == completion
        replay = decide(state, command, context)
        assert replay.accepted and replay.disposition == "replayed"
        assert apply(state, replay) == state
    validate_loaded_runtime_state(state)
