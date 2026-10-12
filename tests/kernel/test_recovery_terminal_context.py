from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.transition import RunnerResultObserved
from millrace.kernel import apply, decide
from millrace.operator.dispatch import DispatchProjectionError
from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_dispatch_envelope_for_run,
    fake_runner_observation_payload,
)
from support import generic_admission, generic_fanout
from tests.compiler.test_runtime_failure_mappings import _compile
from tests.kernel.test_recovery_recorded_source_return import (
    _claimed_nested_recovery_after_cooldown,
    _persist_and_load_under,
    _shared_return_policy_source,
)
from tests.kernel.test_runtime_failure_adversarial_review import (
    _apply_failure as _apply_failure_with_bound_diagnostic,
)
from tests.kernel.test_runtime_failure_adversarial_review import (
    _claim,
    _timeout_for_run,
)
from tests.kernel.test_runtime_failure_recovery import failed_state
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_parent_claimed_state,
)


@pytest.mark.parametrize(
    "recurrence_targets_later_node",
    [False, True],
    ids=["same-node", "authenticated-later-node"],
)
@pytest.mark.parametrize(
    "accepted_runtime_failure_outcome",
    [False, True],
    ids=["nested-timeout", "accepted-runtime-failure"],
)
def test_after_cooldown_dispatch_accepts_authenticated_recovery_source(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    recurrence_targets_later_node: bool,
    accepted_runtime_failure_outcome: bool,
) -> None:
    (
        state,
        _first_completion,
        first_command,
        first_context,
        _second_completion,
        second_command,
        second_context,
    ) = _claimed_nested_recovery_after_cooldown(
        monkeypatch,
        recurrence_targets_later_node=recurrence_targets_later_node,
        accepted_runtime_failure_outcome=accepted_runtime_failure_outcome,
    )
    state = _persist_and_load_under(tmp_path, "after-cooldown-recovery-dispatch", state)
    validate_loaded_runtime_state(state)

    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="c17-recovery-after-cooldown-run",
    )
    assert dispatch.stage_kind_id == generic_admission.RECOVERY_STAGE_ID
    assert {str(option["action_id"]) for option in dispatch.terminal_options} == {
        generic_admission.RECOVERY_RETURN_ACTION_ID,
        generic_admission.RECOVERY_QUARANTINE_ACTION_ID,
        "admission.recovery_timeout",
        "admission.recovery.complete_work",
        "admission.recovery.domain_close",
    }

    # Historical accepted failures still replay exactly after durable reload.
    for command, context in (
        (first_command, first_context),
        (second_command, second_context),
    ):
        replay = decide(state, command, context)
        assert replay.accepted and replay.disposition == "replayed"
        assert apply(state, replay) == state


def test_shared_quarantine_action_uses_the_active_recovery_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _shared_return_policy_source()
    policies = source["recovery_policies"]
    policies[1]["id"] = "admission.z_runtime_recovery_policy"
    waits = source["wait_states"]
    waits[1]["policy_id"] = "admission.z_runtime_recovery_policy"
    compiled = _compile(source)
    assert compiled.plan is not None, compiled.diagnostics
    plan = compiled.plan
    fingerprint = authority_fingerprint(plan)
    monkeypatch.setattr(
        generic_admission,
        "compile_plan",
        lambda: (plan, fingerprint),
    )
    state, _, _ = _generic_parent_claimed_state()
    state, completion = _timeout_for_run(state, "run-generic-parent", observed_at=2000)
    state, source_command, _source_context = _apply_failure_with_bound_diagnostic(
        state,
        completion,
        suffix="shared-quarantine-policy",
    )
    attempt = next(iter(state.recovery_attempts.values()))
    assert str(attempt.policy_id) == "admission.z_runtime_recovery_policy"
    recovery_activation_id = next(
        route.target_activation_id
        for route in state.activation_routes
        if route.created_by_input_id == source_command.input_id
    )
    recovery_run_id = "c19-shared-quarantine-recovery-run"
    state = _claim(state, recovery_activation_id, recovery_run_id)

    action = next(
        item
        for item in plan.terminal_actions
        if str(item.id) == generic_admission.RECOVERY_QUARANTINE_ACTION_ID
    )
    marker = next(
        item.marker for item in plan.terminal_outcomes if item.id == action.outcome_id
    )
    run = state.runs[recovery_run_id]
    observation = RunnerResultObserved(
        "shared-quarantine-observation",
        run_id=recovery_run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=fingerprint,
            marker=marker,
            artifact_payload=generic_fanout.packet_payload(),
        ),
        observed_at=None,
    )
    state, observation = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )
    context = deterministic_context(
        transition_id="shared-quarantine-transition",
        activation_id="unused-after-quarantine",
    )
    decision = decide(state, observation, context)
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    updated = state.recovery_attempts[attempt.record_id]
    assert updated.policy_id == attempt.policy_id
    assert updated.phase == "quarantine_eligible"
    assert len(state.lineage_quarantines) == 1


@pytest.mark.parametrize(
    "corruption",
    [
        "missing-attempt",
        "stale-phase",
        "wrong-source-work-item",
        "wrong-lineage",
        "stale-recovery-run",
        "stale-recovery-activation",
        "duplicate-current-attempt",
    ],
)
def test_immediate_recovery_dispatch_refuses_invalid_current_attempt(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    state, completion = failed_state(monkeypatch)
    state, failure_command, _ = _apply_failure_with_bound_diagnostic(state, completion)
    recovery_activation_id = next(
        route.target_activation_id
        for route in state.activation_routes
        if route.created_by_input_id == failure_command.input_id
    )
    run_id = "c19-immediate-recovery-dispatch-run"
    state = _claim(state, recovery_activation_id, run_id)

    record_id, attempt = next(iter(state.recovery_attempts.items()))
    attempts = dict(state.recovery_attempts)
    if corruption == "missing-attempt":
        attempts.clear()
    elif corruption == "duplicate-current-attempt":
        attempts["duplicate-current-attempt"] = attempt
    else:
        changed_fields = {
            "stale-phase": {"phase": "resolved"},
            "wrong-source-work-item": {"source_work_item_id": "sibling-work-item"},
            "wrong-lineage": {"lineage_id": "sibling-lineage"},
            "stale-recovery-run": {"latest_recovery_run_id": "stale-run"},
            "stale-recovery-activation": {
                "latest_recovery_activation_id": "stale-activation"
            },
        }[corruption]
        attempts[record_id] = replace(attempt, **changed_fields)
    forged = replace(state, recovery_attempts=attempts)
    with pytest.raises(DispatchProjectionError):
        fake_runner_dispatch_envelope_for_run(state=forged, run_id=run_id)
