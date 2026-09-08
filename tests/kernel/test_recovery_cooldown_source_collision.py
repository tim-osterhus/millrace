from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    RecordLineageQuarantine,
    RecordRecoveryAttempt,
    RunnerResultObserved,
    TimerDue,
)
from millrace.kernel import apply, decide
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.testing import (
    decide_with_fake_runner_completion as decide_with_completion,
)
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from substrate._runtime_store_support import (
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import generic_admission, generic_fanout
from tests.cli.test_cli_bounded_execution_unit import _load, _runtime
from tests.kernel.test_recovery_cooldown_reset import _claim_sibling_parent
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_cooldown_runtime_state,
)


def _sibling_failure(state: RuntimeState) -> RunnerResultObserved:
    run = state.runs["run-generic-sibling"]
    activation = state.activations[run.activation_id]
    plan = next(iter(state.admitted_plans.values())).selected_plan
    action = next(
        action
        for action in plan.terminal_actions
        if str(action.id) == generic_admission.RECOVERY_SOURCE_ACTION_ID
    )
    marker = next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )
    return RunnerResultObserved(
        "observe-generic-sibling-failure",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker=marker,
            artifact_payload=generic_fanout.packet_payload(),
        ),
        observed_at=2000,
    )


def test_sibling_failure_proposes_collision_without_mutating_valid_wait(
    tmp_path: Path,
) -> None:
    valid_state = _generic_cooldown_runtime_state()
    wait = next(iter(valid_state.cooldown_waits.values()))
    attempt = valid_state.recovery_attempts[wait.recovery_attempt_record_id]

    sibling_state = _claim_sibling_parent(valid_state)
    decision = decide_with_completion(
        sibling_state,
        _sibling_failure(sibling_state),
        deterministic_context(
            transition_id="transition-observe-generic-sibling-failure",
            run_id="run-generic-sibling",
            activation_id="activation-generic-sibling",
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "cooldown_wait_pending"
    assert decision.receipt_ref is None
    assert not any(
        isinstance(mutation, (RecordRecoveryAttempt, RecordLineageQuarantine))
        for mutation in decision.mutations
    )

    # The temporary decision is not applied by the completion path. The valid
    # pending pair remains unchanged and can still be persisted as-is.
    assert valid_state.recovery_attempts[attempt.record_id] == attempt
    assert valid_state.cooldown_waits[wait.wait_id] == wait
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    db_path, cas_root = runtime_store_paths(candidate_root)
    persist_runtime_state(db_path, cas_root, sibling_state)


def _below_threshold_after_sibling_failure(monkeypatch):
    source = generic_admission.source()
    source["recovery_policies"][0]["quarantine_threshold_attempt"] = 5
    monkeypatch.setattr(generic_admission, "source", lambda: source)
    state = _claim_sibling_parent(_generic_cooldown_runtime_state())
    wait = next(iter(state.cooldown_waits.values()))
    timer = TimerDue("old-timer", wait_id=wait.wait_id, observed_at=1900)
    decision = decide(
        state,
        timer,
        deterministic_context(
            transition_id="old-timer-transition",
            activation_id="old-recovery",
        ),
    )
    assert decision.accepted
    state = apply(state, decision)
    decision = decide_with_completion(
        state,
        _sibling_failure(state),
        deterministic_context(transition_id="sibling-after-due"),
    )
    assert decision.accepted
    return apply(state, decision)


def test_superseded_due_recovery_is_non_candidate_after_reload(tmp_path, monkeypatch):
    state = _below_threshold_after_sibling_failure(monkeypatch)
    state = persist_and_load_runtime_state(tmp_path, state)
    projection = list_ready_dispatch_candidates(state)
    assert not projection.candidates
    diagnostic = next(
        d for d in projection.diagnostics if d.activation_id == "old-recovery"
    )
    assert diagnostic.reason_code == "superseded_recovery_activation"
    assert diagnostic.severity == "non_candidate"
    assert all(d.severity == "non_candidate" for d in projection.diagnostics)
    wait = next(w for w in state.cooldown_waits.values() if w.consumed_input_id is None)
    decision = decide(
        state,
        TimerDue("new-timer", wait_id=wait.wait_id, observed_at=2900),
        deterministic_context(
            transition_id="new-timer-transition", activation_id="new-recovery"
        ),
    )
    assert decision.accepted
    state = apply(state, decision)
    later = tmp_path / "later"
    later.mkdir()
    state = persist_and_load_runtime_state(later, state)
    projection = list_ready_dispatch_candidates(state)
    assert [c.activation_id for c in projection.candidates] == ["new-recovery"]
    assert not any(d.severity == "corrupt_authority" for d in projection.diagnostics)


@pytest.mark.parametrize(
    "field,value",
    [
        ("consumed_input_id", None),
        ("consumed_at", None),
        ("attempt_count", 3),
        ("target_graph_node_id", "forged-target"),
        ("source_run_id", "missing-run"),
    ],
)
def test_superseded_recovery_requires_exact_provenance(monkeypatch, field, value):
    state = _below_threshold_after_sibling_failure(monkeypatch)
    wait = next(
        w
        for w in state.cooldown_waits.values()
        if w.resulting_recovery_activation_id == "old-recovery"
    )
    state = replace(
        state,
        cooldown_waits={
            **state.cooldown_waits,
            wait.wait_id: replace(wait, **{field: value}),
        },
    )
    diagnostic = next(
        d
        for d in list_ready_dispatch_candidates(state).diagnostics
        if d.activation_id == "old-recovery"
    )
    assert diagnostic.severity == "corrupt_authority"


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_kind", "workflow.claim_work"),
        ("input_family", "workflow_observation"),
        ("accepted", False),
    ],
)
def test_superseded_recovery_requires_accepted_timer_transition(
    monkeypatch, field, value
):
    state = _below_threshold_after_sibling_failure(monkeypatch)
    state = replace(
        state,
        transitions=tuple(
            replace(t, **{field: value}) if t.input_id == "old-timer" else t
            for t in state.transitions
        ),
    )
    diagnostic = next(
        d
        for d in list_ready_dispatch_candidates(state).diagnostics
        if d.activation_id == "old-recovery"
    )
    assert diagnostic.severity == "corrupt_authority"


@pytest.mark.parametrize(
    "refusal_reason", ["cooldown_wait_pending", "idempotency_conflict"]
)
def test_saved_completion_waits_then_applies_exactly_once(
    tmp_path, monkeypatch, refusal_reason
):
    from millrace.adapters.cli import session_completion
    from millrace.adapters.cli.session_completion import _apply_persisted_completion
    from millrace.contracts.runner import (
        runner_result_evidence_bytes,
        runner_result_evidence_from_payload,
    )
    from millrace.testing import materialize_fake_runner_session_cas

    state = _claim_sibling_parent(_generic_cooldown_runtime_state())
    runtime = _runtime(tmp_path)
    observation = _sibling_failure(state)
    state, _ = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )
    session_id = state.runs["run-generic-sibling"].current_session_id
    completion = state.runner_session_completions[session_id]
    try:
        runtime.cas_store.put_bytes(
            runner_result_evidence_bytes(
                runner_result_evidence_from_payload(observation.payload)
            )
        )
        state = materialize_fake_runner_session_cas(
            state=state, cas_store=runtime.cas_store
        )
        runtime.store.persist_runtime_state(state, runtime.cas_store)
        wait = next(iter(state.cooldown_waits.values()))
        if refusal_reason != "cooldown_wait_pending":

            def refused(*args, **kwargs):
                decision = decide(*args, **kwargs)
                return replace(
                    decision, refusal=replace(decision.refusal, reason=refusal_reason)
                )

            monkeypatch.setattr(session_completion, "decide", refused)
        result = _apply_persisted_completion(runtime, completion)
        assert result.code == (
            "runner_session_waiting"
            if refusal_reason == "cooldown_wait_pending"
            else "observation_refused"
        )
        saved = _load(runtime)
        assert completion.application_input_id not in saved.receipts
        assert saved.cooldown_waits[wait.wait_id] == wait
        assert saved.runner_session_completions[session_id] == completion
        monkeypatch.undo()
        timer = TimerDue("completion-due", wait_id=wait.wait_id, observed_at=1900)
        decision = decide(
            saved,
            timer,
            deterministic_context(
                transition_id="completion-due-transition",
                activation_id="due-recovery",
            ),
        )
        assert decision.accepted
        runtime.store.persist_runtime_state(apply(saved, decision), runtime.cas_store)
        result = _apply_persisted_completion(runtime, completion)
        assert result.code == "observation_accepted"
        assert result.accepted
        accepted = _load(runtime)
        assert (
            accepted.recovery_attempts[wait.recovery_attempt_record_id].attempt_count
            == 3
        )
        assert accepted.runner_session_completions[session_id] == completion
        result = _apply_persisted_completion(runtime, completion)
        assert result.accepted and result.transition_disposition == "replayed"
        assert _load(runtime) == accepted
    finally:
        runtime.close()
