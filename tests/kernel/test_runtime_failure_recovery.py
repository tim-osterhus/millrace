from __future__ import annotations

from dataclasses import fields, replace

import pytest

from millrace.contracts.compiled_plan import (
    RunnerBindingWithRuntimeFailuresDeclaration,
    RunnerRuntimeFailureMapping,
    authority_fingerprint,
)
from millrace.contracts.ids import ActionId, StageKindId
from millrace.contracts.runtime_failures import runtime_failure_input
from millrace.contracts.transition import RunnerResultObserved
from millrace.kernel import apply, decide
from millrace.kernel.lookups import run_is_settled
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_admission
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_parent_claimed_state,
)


def failed_state(monkeypatch, *, mapped=True):
    plan, _ = generic_admission.compile_plan()
    if mapped:
        bindings = tuple(
            RunnerBindingWithRuntimeFailuresDeclaration(
                **{f.name: getattr(b, f.name) for f in fields(b)},
                runtime_failure_mappings=(
                    RunnerRuntimeFailureMapping(
                        StageKindId(generic_admission.PARENT_STAGE_ID),
                        "timeout",
                        ActionId(generic_admission.RECOVERY_SOURCE_ACTION_ID),
                    ),
                ),
            )
            for b in plan.runner_bindings
        )
        plan = replace(plan, runner_bindings=bindings)
    monkeypatch.setattr(
        generic_admission, "compile_plan", lambda: (plan, authority_fingerprint(plan))
    )
    state, _, _ = _generic_parent_claimed_state()
    run = state.runs["run-generic-parent"]
    observation = RunnerResultObserved(
        "fake-completion-only",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker="DONE",
            artifact_payload=None,
        ),
        observed_at=2000,
    )
    state, _ = fake_completed_runner_observation_state(
        state=state, observation=observation
    )
    session = state.runner_sessions[state.runs[run.run_ref.run_id].current_session_id]
    completion = state.runner_session_completions[session.session_id]
    completion = replace(
        completion,
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind="error",
        adapter_error_kind="timeout",
        runner_result_evidence_digest=None,
    )
    state = replace(
        state,
        runner_sessions={session.session_id: replace(session, state="failed")},
        runner_session_completions={session.session_id: completion},
    )
    return state, completion


def test_timeout_creates_real_selected_recovery_without_runner_result(monkeypatch):
    state, completion = failed_state(monkeypatch)
    command = runtime_failure_input(state, completion)
    context = deterministic_context(
        transition_id="runtime-failure-applied", activation_id="recovery-after-timeout"
    )
    decision = decide(state, command, context)
    assert decision.accepted, decision.refusal
    after = apply(state, decision)
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.runner_session_completions[completion.session_id] == completion
    assert run_is_settled(after, completion.run_id)
    attempt = next(iter(after.recovery_attempts.values()))
    assert attempt.attempt_count == 1 and attempt.phase == "active_recovery"
    assert attempt.source_run_id == completion.run_id
    assert [
        c.activation_id for c in list_ready_dispatch_candidates(after).candidates
    ] == ["recovery-after-timeout"]
    replay = decide(after, command, context)
    assert replay.accepted and replay.disposition == "replayed"
    assert apply(after, replay) == after


def test_declared_recovery_action_without_mapping_does_not_infer_timeout(monkeypatch):
    state, completion = failed_state(monkeypatch, mapped=False)
    result = decide(
        state,
        runtime_failure_input(state, completion),
        deterministic_context(transition_id="unmapped-timeout"),
    )
    assert not result.accepted
    assert result.refusal.reason == "runtime_failure_unmapped"
    assert result.receipt_ref is None
    assert state.recovery_attempts == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", "wrong-session"),
        ("dispatch_generation", 2),
        ("session_fencing_token", "wrong-fence"),
        ("completion_id", "wrong-completion"),
        ("diagnostic_digest", "sha256:" + "f" * 64),
        ("observed_at", 99),
    ],
)
def test_timeout_recovery_refuses_changed_completion_authority(
    monkeypatch, field, value
):
    state, completion = failed_state(monkeypatch)
    command = replace(runtime_failure_input(state, completion), **{field: value})
    decision = decide(
        state, command, deterministic_context(transition_id="wrong-failure-authority")
    )
    assert not decision.accepted
    assert decision.refusal.reason == "runtime_failure_authority_mismatch"
    assert not any(
        type(m).__name__
        in {"CreateActivation", "RouteActivation", "RecordRecoveryAttempt"}
        for m in decision.mutations
    )
