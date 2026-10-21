"""An authentic ordinary-stage bad marker remains a rejected candidate."""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.cli.test_cli_bounded_execution_unit import _load, _reopen_runtime, _runtime
from tests.cli.test_cli_candidate_failure_recovery import _success_config
from tests.kernel.test_recovery_recorded_source_return import _RECURRENCE_ACTION_ID
from tests.kernel.test_terminal_action_context import _current_recovery_context
from tests.operator.test_terminal_option_context import (
    _ordinary_recovery_ingress_source,
)
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_parent_claimed_state,
)

from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.compiler import authority_fingerprint, compile_workflow
from millrace.contracts.runtime_failures import runtime_failure_eligible
from millrace.contracts.transition import RunnerResultObserved
from millrace.kernel import apply, decide
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_admission, generic_fanout, generic_lifecycle


@pytest.mark.parametrize("component_bound", [False, True])
def test_attempt_owned_marker_from_ordinary_success_routes_declared_recovery(
    tmp_path,
    monkeypatch,
    component_bound,
):
    source = _ordinary_recovery_ingress_source()
    if component_bound:
        source["counters"] = []
        binding = source["runner_bindings"][0]
        binding["adapter_kind"] = "codex"
        binding["component_pin"] = generic_lifecycle.source_with_completion_behavior()[
            "runner_bindings"
        ][0]["component_pin"]
        binding["component_pin"]["legal_terminal_result_ids"] = tuple(
            sorted(
                {
                    outcome["marker"]
                    for outcome in source["terminal_outcomes"]
                    if outcome["marker"].strip()
                }
            )
        )
        binding["terminal_result_mappings"] = [
            {
                "stage_kind_id": outcome["stage_kind_id"],
                "outcome_id": outcome["id"],
                "runner_result_id": outcome["marker"],
            }
            for outcome in source["terminal_outcomes"]
            if outcome["marker"].strip()
        ]
    source["runner_bindings"][0]["runtime_failure_mappings"].append(
        {
            "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
            "failure_kind": "result_rejected",
            "action_id": _RECURRENCE_ACTION_ID,
        }
    )
    compiled = compile_workflow(
        source, selected_runner_policy=generic_admission._CODEX_POLICY
    )
    assert compiled.plan is not None, compiled.diagnostics
    plan, fingerprint = compiled.plan, authority_fingerprint(compiled.plan)
    monkeypatch.setattr(generic_admission, "compile_plan", lambda: (plan, fingerprint))
    state, _, _ = _generic_parent_claimed_state()
    runtime = _runtime(tmp_path, state)
    try:
        ordinary = run_bounded_execution_unit(
            runtime,
            activation_id="activation-generic-parent",
            local_config=_success_config(
                "ENTER_RECOVERY_ORDINARY", generic_fanout.packet_payload()
            ),
        )
        assert ordinary.accepted, ordinary
        candidate = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
        before = _load(runtime)
        rejected = run_bounded_execution_unit(
            runtime,
            activation_id=candidate.activation_id,
            local_config=_success_config("ADMISSION_UNRECOVERABLE", None),
        )
        assert rejected.code == "runtime_failure_recovery_applied", rejected
        after = _load(runtime)
        completion = after.runner_session_completions[
            after.runs[rejected.run_id].current_session_id
        ]
        receipt = after.receipts[completion.application_input_id]
        assert not receipt.accepted
        assert receipt.refusal_reason == "recovery_action_context_missing"
        assert after.lineage_quarantines == before.lineage_quarantines == {}
        assert rejected.code == "runtime_failure_recovery_applied", rejected
        assert after.artifacts == before.artifacts
        assert after.runner_observations == before.runner_observations
        recovery = list_ready_dispatch_candidates(after).candidates[0]
        recovered = run_bounded_execution_unit(
            runtime,
            activation_id=recovery.activation_id,
            local_config=_success_config(
                "ADMISSION_RECOVERED", generic_fanout.packet_payload()
            ),
        )
        assert recovered.accepted, recovered
        returned = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
        assert returned.work_item_id == candidate.work_item_id
        closed = run_bounded_execution_unit(
            runtime,
            activation_id=returned.activation_id,
            local_config=_success_config("COMPLETE", generic_fanout.packet_payload()),
        )
        assert closed.accepted, closed
        final = _load(runtime)
        assert candidate.work_item_id in final.closed_work_items
        assert final.receipts[completion.application_input_id] == receipt
        assert final.runner_session_completions[completion.session_id] == completion
        runtime = _reopen_runtime(runtime)
        assert _load(runtime) == final
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "corruption,reason",
    [
        ("missing", "recovery_action_context_stale"),
        ("phase", "recovery_action_context_phase"),
        ("foreign", "recovery_action_context_ownership"),
        ("duplicate", "recovery_action_context_ambiguous"),
        ("stale", "recovery_action_context_stale"),
    ],
)
def test_damaged_formal_attempt_cannot_become_a_recoverable_candidate(
    monkeypatch, corruption, reason
):
    state, plan, run, activation, work, action = _current_recovery_context(monkeypatch)
    record_id, attempt = next(iter(state.recovery_attempts.items()))
    attempts = dict(state.recovery_attempts)
    if corruption == "missing":
        attempts.clear()
    elif corruption == "duplicate":
        attempts["duplicate-attempt"] = attempt
    else:
        fields = {
            "phase": {"phase": "pending_cooldown"},
            "foreign": {"lineage_id": "foreign-lineage"},
            "stale": {"latest_recovery_run_id": "old-run"},
        }[corruption]
        attempts[record_id] = replace(attempt, **fields)
    bad = replace(state, recovery_attempts=attempts)
    marker = next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )
    observation = RunnerResultObserved(
        "removed-formal-attempt",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker=marker,
            artifact_payload=None,
        ),
        observed_at=8000,
    )
    bad, observation = fake_completed_runner_observation_state(
        state=bad, observation=observation
    )
    refusal = decide(
        bad, observation, deterministic_context(transition_id="removed-attempt-refused")
    )
    assert not refusal.accepted
    assert refusal.refusal.reason == reason
    refused = apply(bad, refusal)
    completion = refused.runner_session_completions[
        refused.runs[run.run_ref.run_id].current_session_id
    ]
    assert not runtime_failure_eligible(refused, completion)
    assert refused.work_items == bad.work_items
    assert refused.artifacts == bad.artifacts
    assert refused.lineage_quarantines == bad.lineage_quarantines
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(refused)
