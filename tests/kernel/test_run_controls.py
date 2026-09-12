from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from millrace.adapters.cli.context import transition_context
from millrace.contracts.state import ClosedWorkItemRecord, RunExecutionControl
from millrace.contracts.transition import CreateRunnerSession
from millrace.kernel import apply, decide
from millrace.kernel.errors import StateConcurrencyError
from millrace.kernel.run_controls import run_eligibility_refusal, run_hold_refusal
from support.run_controls import pause, runtime_with_run


@pytest.mark.parametrize(
    ("label", "reason"),
    [
        ("created", None),
        ("starting", "run_pause_unsupported_state"),
        ("running", "runner_pause_unsupported"),
        ("cancellation_requested", "run_cancellation_in_progress"),
        ("terminating", "run_cancellation_in_progress"),
        ("completed", "run_attempt_terminal"),
        ("interrupted", "run_attempt_terminal"),
        ("failed", "run_attempt_terminal"),
        ("lost", "run_aftermath_unknown"),
    ],
)
def test_complete_session_state_matrix(tmp_path, label, reason):
    runtime, run = runtime_with_run(tmp_path, session=True)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    prior = state.runner_sessions[run.current_session_id]
    terminal = label in {"completed", "interrupted", "failed", "lost"}
    session = replace(
        prior,
        state=label,
        start_intent_at=None if label == "created" else 100,
        started_at=100 if label in {"running", "completed"} else None,
        ended_at=101 if terminal else None,
        cleanup_disposition="orphan_risk"
        if label == "lost"
        else "complete"
        if terminal
        else "pending",
    )
    state = replace(state, runner_sessions={session.session_id: session})
    assert run_eligibility_refusal(state, run) == reason
    runtime.close()


def test_kernel_apply_rechecks_hold_after_an_earlier_create_decision(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    create = CreateRunnerSession(
        "create",
        run_ref=run.run_ref,
        session_id="session-new",
        session_fencing_token="fence-new",
        created_at=100,
        explicit_retry_intent=False,
    )
    decision = decide(
        state, create, transition_context(command="test", input_id_value="create")
    )
    assert decision.accepted
    pause(runtime, run)
    held = runtime.store.load_runtime_state(runtime.cas_store)
    with pytest.raises(StateConcurrencyError, match="run_execution_held"):
        apply(held, decision)
    runtime.close()


def test_terminal_and_unknown_authority_precedes_hold(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    pause(runtime, run)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    closed = ClosedWorkItemRecord(
        "close",
        run.work_item_id,
        run.run_ref.run_id,
        None,
        "close",
        "intervention",
        "operator_intervention",
    )
    assert (
        run_eligibility_refusal(
            replace(state, closed_work_items={run.work_item_id: closed}), run
        )
        == "run_work_closed"
    )
    unknown = RunExecutionControl(
        run.run_ref.run_id,
        2,
        str(uuid4()),
        "unknown",
        "run_aftermath_unknown",
        None,
        1,
        "2026-09-10T00:00:00Z",
    )
    state = replace(state, run_execution_controls={run.run_ref.run_id: unknown})
    assert run_hold_refusal(state, run.run_ref.run_id) == "run_aftermath_unknown"
    runtime.close()


@pytest.mark.parametrize("kind", ["operator_wait", "cooldown_wait", "quarantine"])
def test_existing_wait_and_quarantine_sources_are_ineligible(kind):
    from substrate.test_persistence_integrity_refusals import (
        _generic_cooldown_runtime_state,
        _generic_operator_needed_quarantine_state,
        _generic_operator_wait_runtime_state,
    )

    state = {
        "operator_wait": _generic_operator_wait_runtime_state,
        "cooldown_wait": _generic_cooldown_runtime_state,
        "quarantine": _generic_operator_needed_quarantine_state,
    }[kind]()
    records = {
        "operator_wait": state.operator_waits,
        "cooldown_wait": state.cooldown_waits,
        "quarantine": state.lineage_quarantines,
    }[kind]
    record = next(iter(records.values()))
    run = state.runs[
        "run-generic-recovery" if kind == "quarantine" else record.source_run_id
    ]
    assert run_eligibility_refusal(state, run) in {
        "run_observed",
        "run_quarantined",
        "run_wait_source",
    }


def test_superseded_claim_is_ineligible(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    activation = state.activations[run.activation_id]
    state = replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: replace(
                activation, claimed_by_run_id="other-run"
            ),
        },
    )
    assert run_eligibility_refusal(state, run) == "run_authority_superseded"
    runtime.close()
