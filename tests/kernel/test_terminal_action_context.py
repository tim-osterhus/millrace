from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.recovery_action_context import (
    RecoveryActionContext,
    RecoveryActionContextRefusal,
    recovery_action_context_for_terminal_action,
)
from millrace.contracts.transition import RunnerResultObserved
from millrace.kernel.terminal_actions import (
    TerminalActionRefusal,
    resolve_terminal_action,
)
from millrace.testing import deterministic_context, fake_runner_observation_payload
from support import generic_admission
from tests.compiler.test_runtime_failure_mappings import _compile
from tests.kernel.test_recovery_recorded_source_return import (
    _claimed_nested_recovery_after_cooldown,
    _shared_return_policy_source,
)
from tests.kernel.test_runtime_failure_adversarial_review import (
    _apply_failure as _apply_failure_with_bound_diagnostic,
)
from tests.kernel.test_runtime_failure_adversarial_review import (
    _claim,
    _timeout_for_run,
)
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_parent_claimed_state,
)


def _current_recovery_context(monkeypatch):
    state, *_ = _claimed_nested_recovery_after_cooldown(monkeypatch)
    run = state.runs["c17-recovery-after-cooldown-run"]
    activation = state.activations[run.activation_id]
    work_item = state.work_items[run.work_item_id]
    plan = state.admitted_plans[
        run.run_ref.plan_ref.authority_fingerprint
    ].selected_plan
    action = next(
        candidate
        for candidate in plan.terminal_actions
        if str(candidate.id) == generic_admission.RECOVERY_RETURN_ACTION_ID
    )
    return state, plan, run, activation, work_item, action


def test_current_return_action_resolves_exact_policy_and_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, plan, run, activation, work_item, action = _current_recovery_context(
        monkeypatch
    )

    result = recovery_action_context_for_terminal_action(
        selected_plan=plan,
        state=state,
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
    )

    assert isinstance(result, RecoveryActionContext)
    assert str(result.policy.id) == generic_admission.RECOVERY_POLICY_ID
    assert result.attempt.latest_recovery_run_id == run.run_ref.run_id
    assert result.attempt.latest_recovery_activation_id == activation.activation_id


def test_shared_action_uses_the_unique_current_policy_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _shared_return_policy_source()
    policies = cast(list[dict[str, object]], source["recovery_policies"])
    second_policy_id = "admission.z_runtime_recovery_policy"
    policies[1]["id"] = second_policy_id
    waits = cast(list[dict[str, object]], source["wait_states"])
    waits[1]["policy_id"] = second_policy_id
    bindings = cast(list[dict[str, object]], source["runner_bindings"])
    mappings = cast(list[dict[str, object]], bindings[0]["runtime_failure_mappings"])
    parent_mapping = next(
        mapping
        for mapping in mappings
        if mapping["stage_kind_id"] == generic_admission.PARENT_STAGE_ID
    )
    parent_mapping["action_id"] = generic_admission.ALTERNATE_RECOVERY_SOURCE_ACTION_ID
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
    state, completion = _timeout_for_run(
        state,
        "run-generic-parent",
        observed_at=2000,
    )
    state, failure_command, _ = _apply_failure_with_bound_diagnostic(
        state,
        completion,
        suffix="shared-action-context",
    )
    recovery_activation_id = next(
        route.target_activation_id
        for route in state.activation_routes
        if route.created_by_input_id == failure_command.input_id
    )
    run_id = "shared-action-context-recovery-run"
    state = _claim(state, recovery_activation_id, run_id)
    run = state.runs[run_id]
    activation = state.activations[run.activation_id]
    work_item = state.work_items[run.work_item_id]
    action = next(
        candidate
        for candidate in plan.terminal_actions
        if str(candidate.id) == generic_admission.RECOVERY_QUARANTINE_ACTION_ID
    )

    result = recovery_action_context_for_terminal_action(
        selected_plan=plan,
        state=state,
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
    )

    assert isinstance(result, RecoveryActionContext)
    assert str(result.policy.id) == second_policy_id


@pytest.mark.parametrize(
    ("corruption", "expected_reason"),
    (
        ("missing", "recovery_action_context_stale"),
        ("duplicate", "recovery_action_context_ambiguous"),
        ("wrong_policy", "recovery_action_context_ownership"),
        ("wrong_plan", "recovery_action_context_ownership"),
        ("wrong_lineage", "recovery_action_context_ownership"),
        ("wrong_source", "recovery_action_context_ownership"),
        ("stale_phase", "recovery_action_context_phase"),
        ("stale_run", "recovery_action_context_stale"),
        ("stale_activation", "recovery_action_context_stale"),
    ),
)
def test_recovery_action_context_rejects_noncurrent_authority(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
    expected_reason: str,
) -> None:
    state, plan, run, activation, work_item, action = _current_recovery_context(
        monkeypatch
    )
    record_id, attempt = next(iter(state.recovery_attempts.items()))
    attempts = dict(state.recovery_attempts)
    if corruption == "missing":
        attempts.clear()
    elif corruption == "duplicate":
        attempts["duplicate-context-attempt"] = attempt
    else:
        changed_fields = {
            "wrong_policy": {"policy_id": "admission.foreign_policy"},
            "wrong_plan": {
                "plan_ref": replace(
                    attempt.plan_ref,
                    authority_fingerprint=f"sha256:{'f' * 64}",
                )
            },
            "wrong_lineage": {"lineage_id": "foreign-lineage"},
            "wrong_source": {"source_work_item_id": "foreign-work-item"},
            "stale_phase": {"phase": "pending_cooldown"},
            "stale_run": {"latest_recovery_run_id": "stale-run"},
            "stale_activation": {
                "latest_recovery_activation_id": "stale-activation"
            },
        }[corruption]
        attempts[record_id] = replace(attempt, **changed_fields)

    result = recovery_action_context_for_terminal_action(
        selected_plan=plan,
        state=replace(state, recovery_attempts=attempts),
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
    )

    assert isinstance(result, RecoveryActionContextRefusal)
    assert result.reason == expected_reason


@pytest.mark.parametrize(
    "action_id",
    (
        generic_admission.RECOVERY_RETURN_ACTION_ID,
        generic_admission.RECOVERY_QUARANTINE_ACTION_ID,
    ),
)
def test_emitted_recovery_marker_without_attempt_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    action_id: str,
) -> None:
    state, plan, run, activation, work_item, _return_action = (
        _current_recovery_context(monkeypatch)
    )
    action = next(
        candidate
        for candidate in plan.terminal_actions
        if str(candidate.id) == action_id
    )
    marker = next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )
    observation = RunnerResultObserved(
        f"emitted-without-attempt:{action_id}",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker=marker,
            artifact_payload={},
        ),
        observed_at=None,
    )

    result = resolve_terminal_action(
        transition_input=observation,
        context=deterministic_context(
            transition_id=f"refuse-without-attempt:{action_id}",
            activation_id=activation.activation_id,
        ),
        selected_plan=plan,
        state=replace(state, recovery_attempts={}),
        run=run,
        activation=activation,
        work_item=work_item,
        action=action,
        observation_payload={},
    )

    assert isinstance(result, TerminalActionRefusal)
    assert result.reason == "recovery_action_context_stale"
