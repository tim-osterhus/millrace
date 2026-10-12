from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace

import pytest

from millrace.contracts.compiled_plan import (
    RunnerBindingWithRuntimeFailuresDeclaration,
    RunnerRuntimeFailureMapping,
)
from millrace.contracts.ids import ActionId, RunnerBindingId, StageKindId
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_input,
    runtime_failure_mapping_refusal,
)
from millrace.contracts.transition import (
    ClaimWork,
    OperatorCloseLineage,
    OperatorResumeLineage,
    RunnerResultObserved,
    TimerDue,
)
from millrace.kernel import StateConcurrencyError, apply, decide
from millrace.kernel.lookups import run_is_settled
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_admission
from tests.compiler.test_runtime_failure_mappings import _compile, _mapped_source
from tests.kernel.test_runtime_failure_recovery import failed_state
from tests.substrate.test_persistence_integrity_refusals import (
    _apply_accepted_input,
    _apply_generic_observation,
)


def _apply_failure(state, completion, *, suffix="first"):
    command = runtime_failure_input(state, completion)
    context = deterministic_context(
        transition_id=f"review-timeout-{suffix}",
        activation_id=f"review-recovery-{suffix}",
    )
    decision = decide(state, command, context)
    assert decision.accepted, decision.refusal
    return apply(state, decision), command, context


def _timeout_for_run(state, run_id, observed_at):
    run = state.runs[run_id]
    observation = RunnerResultObserved(
        f"review-completion-only-{run_id}",
        run_id=run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker="DONE",
            artifact_payload=None,
        ),
        observed_at=observed_at,
    )
    state, _ = fake_completed_runner_observation_state(
        state=state, observation=observation
    )
    session = state.runner_sessions[state.runs[run_id].current_session_id]
    completion = replace(
        state.runner_session_completions[session.session_id],
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind="error",
        adapter_error_kind="timeout",
        runner_result_evidence_digest=None,
    )
    return replace(
        state,
        runner_sessions={
            **state.runner_sessions,
            session.session_id: replace(session, state="failed"),
        },
        runner_session_completions={
            **state.runner_session_completions,
            session.session_id: completion,
        },
    ), completion


def _claim(state, activation_id, run_id):
    return _apply_accepted_input(
        state,
        ClaimWork(f"review-claim-{run_id}", activation_id=activation_id),
        deterministic_context(
            transition_id=f"review-claim-transition-{run_id}",
            run_id=run_id,
            claim_id=f"review-claim-{run_id}",
            fencing_token=f"review-fence-{run_id}",
        ),
    )


def _return(state, recovery_activation_id, suffix):
    run_id = f"review-recovery-run-{suffix}"
    state = _claim(state, recovery_activation_id, run_id)
    plan = next(iter(state.admitted_plans.values())).selected_plan
    return _apply_generic_observation(
        state,
        plan,
        next(iter(state.admitted_plans)),
        run_id=run_id,
        action_id=generic_admission.RECOVERY_RETURN_ACTION_ID,
        input_id=f"review-return-{suffix}",
        context=deterministic_context(
            transition_id=f"review-return-transition-{suffix}",
            activation_id=f"review-parent-{suffix}",
        ),
    )


def test_enabled_counter_redirect_has_compiler_and_selected_admission_parity():
    source = _mapped_source()
    runner = source["runner_bindings"][0]
    runner.update(
        {
            "adapter_kind": "codex",
            "component_pin": {
                "component_kind": "runner",
                "component_id": "bounded-runner",
                "component_version": "1",
                "provider_distribution": "bounded-provider",
                "provider_version": "1",
                "descriptor_media_type": "application/json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": (),
                "legal_terminal_result_ids": ("RUNTIME_FAILURE",),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": generic_admission.PARENT_STAGE_ID,
                    "runner_result_id": "RUNTIME_FAILURE",
                    "outcome_id": "admission.needs_recovery",
                },
            ),
        }
    )
    threshold_outcome = "review.runtime_failure.threshold"
    threshold_action = "review.runtime_failure.block"
    stage = next(
        s for s in source["stage_kinds"] if s["id"] == generic_admission.PARENT_STAGE_ID
    )
    stage["declared_outcome_ids"] = (*stage["declared_outcome_ids"], threshold_outcome)
    source["terminal_outcomes"].append(
        {
            "id": threshold_outcome,
            "stage_kind_id": generic_admission.PARENT_STAGE_ID,
            "marker": "REVIEW_RUNTIME_THRESHOLD",
        }
    )
    source["terminal_actions"].append(
        {
            "id": threshold_action,
            "stage_kind_id": generic_admission.PARENT_STAGE_ID,
            "outcome_id": threshold_outcome,
            "kind": "block_work_item",
        }
    )
    counter = next(
        c
        for c in source["counters"]
        if c["id"] == generic_admission.RECOVERY_COUNTER_ID
    )
    counter["threshold_action_id"] = threshold_action
    unmapped = deepcopy(source)
    unmapped["runner_bindings"][0].pop("runtime_failure_mappings")
    baseline = _compile(unmapped)
    assert baseline.plan is not None, baseline.diagnostics
    rejected = _compile(source)
    assert rejected.plan is None
    assert any(
        d.code == "invalid_runner_runtime_failure_mapping" for d in rejected.diagnostics
    ), rejected.diagnostics
    binding = baseline.plan.runner_bindings[0]
    mapped = RunnerBindingWithRuntimeFailuresDeclaration(
        **{f.name: getattr(binding, f.name) for f in fields(binding)},
        runtime_failure_mappings=(
            RunnerRuntimeFailureMapping(
                StageKindId(generic_admission.PARENT_STAGE_ID),
                "timeout",
                ActionId(generic_admission.RECOVERY_SOURCE_ACTION_ID),
            ),
        ),
    )
    corrupted = replace(
        baseline.plan,
        runner_bindings=(mapped, *baseline.plan.runner_bindings[1:]),
    )
    assert runtime_failure_mapping_refusal(corrupted).startswith(
        "runtime_failure_mapping_counter_redirect:"
    )


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "work_item_id",
        "claim_id",
        "generation",
        "fencing_token",
        "session_id",
        "dispatch_generation",
        "session_fencing_token",
        "completion_id",
        "diagnostic_digest",
        "observed_at",
    ],
)
def test_accepted_timeout_changed_payload_never_replays(monkeypatch, field):
    state, completion = failed_state(monkeypatch)
    state, command, context = _apply_failure(state, completion)
    if field in {"run_id", "work_item_id", "claim_id", "generation", "fencing_token"}:
        value = 1 if field == "generation" else f"forged-{field}"
        changed = replace(command, run_ref=replace(command.run_ref, **{field: value}))
    else:
        value = {
            "dispatch_generation": 2,
            "diagnostic_digest": "sha256:" + "f" * 64,
            "observed_at": 99,
        }.get(field, f"forged-{field}")
        changed = replace(command, **{field: value})
    result = decide(state, changed, context)
    assert not result.accepted
    assert result.refusal.reason == "idempotency_conflict"
    assert result.receipt_ref is None


@pytest.mark.parametrize("removed", ["all_effects", "attempt", "route", "activation"])
def test_accepted_timeout_without_actual_effects_is_not_settled(monkeypatch, removed):
    state, completion = failed_state(monkeypatch)
    state, command, context = _apply_failure(state, completion)
    route = next(
        r for r in state.activation_routes if r.created_by_input_id == command.input_id
    )
    if removed == "all_effects":
        corrupt = replace(
            state,
            recovery_attempts={},
            activation_routes=(),
            activations={
                k: v
                for k, v in state.activations.items()
                if k != route.target_activation_id
            },
        )
    elif removed == "attempt":
        corrupt = replace(state, recovery_attempts={})
    elif removed == "route":
        corrupt = replace(state, activation_routes=())
    else:
        corrupt = replace(
            state,
            activations={
                k: v
                for k, v in state.activations.items()
                if k != route.target_activation_id
            },
        )
    assert not runtime_failure_applied(corrupt, completion.run_id)
    assert not run_is_settled(corrupt, completion.run_id)
    replay = decide(corrupt, command, context)
    assert not replay.accepted
    assert replay.receipt_ref is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("stage_kind_id", StageKindId(generic_admission.CHILD_STAGE_ID)),
        ("graph_node_id", generic_admission.CHILD_NODE_ID),
        ("runner_binding_id", RunnerBindingId("review.foreign.runner")),
        ("queue_family_id", "child"),
        ("lineage_id", "review-foreign-lineage"),
        ("created_by_input_id", "review-foreign-input"),
    ],
)
def test_coherent_recovery_target_drift_is_not_settlement(monkeypatch, field, value):
    state, completion = failed_state(monkeypatch)
    state, command, context = _apply_failure(state, completion)
    route = next(
        r for r in state.activation_routes if r.created_by_input_id == command.input_id
    )
    activation = state.activations[route.target_activation_id]
    if field == "queue_family_id":
        value = state.work_items[activation.work_item_id].queue_family_id.__class__(
            value
        )
    corrupt = replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: replace(activation, **{field: value}),
        },
    )
    assert not runtime_failure_applied(corrupt, completion.run_id)
    assert not decide(corrupt, command, context).accepted


@pytest.mark.parametrize("field", ["input_kind", "input_family", "action_id", "run_id"])
def test_runtime_failure_audit_drift_is_not_settlement(monkeypatch, field):
    state, completion = failed_state(monkeypatch)
    state, command, context = _apply_failure(state, completion)
    value = (
        ActionId("review.foreign.action") if field == "action_id" else "review.foreign"
    )
    corrupt = replace(
        state,
        traces=tuple(
            replace(record, **{field: value})
            if record.input_id == command.input_id
            else record
            for record in state.traces
        ),
    )
    assert not runtime_failure_applied(corrupt, completion.run_id)
    assert not decide(corrupt, command, context).accepted


def test_failure_source_node_must_have_exact_selected_stage_owner(monkeypatch):
    state, completion = failed_state(monkeypatch)
    command = runtime_failure_input(state, completion)
    run = state.runs[completion.run_id]
    activation = state.activations[run.activation_id]
    corrupt = replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: replace(
                activation, graph_node_id=generic_admission.CHILD_NODE_ID
            ),
        },
    )
    result = decide(corrupt, command, deterministic_context())
    assert not result.accepted
    assert result.refusal.reason == "runtime_failure_authority_mismatch"
    assert result.receipt_ref is None


def test_accepted_attempt_cannot_authorize_coherently_wrong_source_node(monkeypatch):
    state, completion = failed_state(monkeypatch)
    state, command, context = _apply_failure(state, completion)
    run = state.runs[completion.run_id]
    activation = state.activations[run.activation_id]
    attempt = next(iter(state.recovery_attempts.values()))
    corrupt = replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: replace(
                activation, graph_node_id=generic_admission.CHILD_NODE_ID
            ),
        },
        recovery_attempts={
            **state.recovery_attempts,
            attempt.record_id: replace(
                attempt, source_graph_node_id=generic_admission.CHILD_NODE_ID
            ),
        },
    )
    assert not runtime_failure_applied(corrupt, completion.run_id)
    assert not decide(corrupt, command, context).accepted


@pytest.mark.parametrize(
    "updates",
    [
        {"phase": "resolved"},
        {"attempt_count": 999},
        {"updated_by_input_id": "review-no-such-update"},
    ],
)
def test_forged_attempt_update_cannot_reset_or_bypass_recovery_limit(
    monkeypatch, updates
):
    state, completion = failed_state(monkeypatch)
    state, command, context = _apply_failure(state, completion)
    attempt = next(iter(state.recovery_attempts.values()))
    corrupt = replace(
        state,
        recovery_attempts={
            **state.recovery_attempts,
            attempt.record_id: replace(attempt, **updates),
        },
    )
    assert not runtime_failure_applied(corrupt, completion.run_id)
    assert not decide(corrupt, command, context).accepted


@pytest.mark.parametrize(
    "corruption",
    ["route_id", "target_generation", "target_claim", "claimed_run_generation"],
)
def test_runtime_failure_origin_effect_identity_is_authenticated(
    monkeypatch, corruption
):
    state, completion = failed_state(monkeypatch)
    state, command, context = _apply_failure(state, completion)
    route = next(
        r for r in state.activation_routes if r.created_by_input_id == command.input_id
    )
    if corruption == "claimed_run_generation":
        state = _claim(state, route.target_activation_id, "review-target-claimed")
        run = state.runs["review-target-claimed"]
        corrupt = replace(
            state,
            runs={
                **state.runs,
                run.run_ref.run_id: replace(
                    run, run_ref=replace(run.run_ref, generation=999)
                ),
            },
        )
    elif corruption == "route_id":
        corrupt = replace(
            state,
            activation_routes=tuple(
                replace(r, record_id="review-foreign-route") if r == route else r
                for r in state.activation_routes
            ),
        )
    else:
        activation = state.activations[route.target_activation_id]
        updates = (
            {"generation": 1, "claimed_by_run_id": "review-no-such-claim"}
            if corruption == "target_claim"
            else {"generation": 999}
        )
        corrupt = replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(activation, **updates),
            },
        )
    assert not runtime_failure_applied(corrupt, completion.run_id)
    assert not decide(corrupt, command, context).accepted


@pytest.mark.parametrize("race", ["current_session", "cleanup"])
def test_stale_failure_decision_cannot_apply_after_session_authority_changes(
    monkeypatch, race
):
    state, completion = failed_state(monkeypatch)
    command = runtime_failure_input(state, completion)
    decision = decide(state, command, deterministic_context())
    assert decision.accepted
    if race == "current_session":
        run = state.runs[completion.run_id]
        corrupt = replace(
            state,
            runs={
                **state.runs,
                completion.run_id: replace(
                    run, current_session_id="review-next-session"
                ),
            },
        )
    else:
        session = state.runner_sessions[completion.session_id]
        corrupt = replace(
            state,
            runner_sessions={
                **state.runner_sessions,
                session.session_id: replace(
                    session,
                    cleanup_disposition=(
                        "not_required"
                        if session.cleanup_disposition == "complete"
                        else "complete"
                    ),
                ),
            },
        )
    with pytest.raises(StateConcurrencyError):
        apply(corrupt, decision)


def test_timeout_recovery_return_later_completion_preserves_historical_settlement(
    monkeypatch,
):
    state, completion = failed_state(monkeypatch)
    state, command, context = _apply_failure(state, completion)
    state = _return(state, "review-recovery-first", "first")
    assert runtime_failure_applied(state, completion.run_id)
    state = _claim(state, "review-parent-first", "review-source-completes")
    plan = next(iter(state.admitted_plans.values())).selected_plan
    state = _apply_generic_observation(
        state,
        plan,
        next(iter(state.admitted_plans)),
        run_id="review-source-completes",
        action_id="admission.complete",
        input_id="review-source-completed",
        context=deterministic_context(
            transition_id="review-source-completed-transition"
        ),
    )
    assert runtime_failure_applied(state, completion.run_id)
    assert state.runner_session_completions[completion.session_id] == completion
    assert next(iter(state.recovery_attempts.values())).phase == "resolved"
    validate_loaded_runtime_state(state)
    assert decide(state, command, context).accepted


def _quarantined_failure(monkeypatch):
    state, first = failed_state(monkeypatch)
    state, first_command, first_context = _apply_failure(state, first)
    state = _return(state, "review-recovery-first", "first")
    state = _claim(state, "review-parent-first", "review-source-second-timeout")
    state, second = _timeout_for_run(state, "review-source-second-timeout", 3000)
    state, second_command, second_context = _apply_failure(
        state, second, suffix="second"
    )
    wait = next(iter(state.cooldown_waits.values()))
    state = _apply_accepted_input(
        state,
        TimerDue("review-due", wait_id=wait.wait_id, observed_at=wait.due_at),
        deterministic_context(
            transition_id="review-due-transition", activation_id="review-due-recovery"
        ),
    )
    state = _return(state, "review-due-recovery", "second")
    state = _claim(state, "review-parent-second", "review-source-third-timeout")
    state, third = _timeout_for_run(state, "review-source-third-timeout", 5000)
    state, third_command, third_context = _apply_failure(state, third, suffix="third")
    return state, (
        (first, first_command, first_context),
        (second, second_command, second_context),
        (third, third_command, third_context),
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"attempt_count": 999},
        {"status": "foreign"},
        {"status": "superseded"},
        {"superseded_input_id": "review-no-such-intervention"},
        {
            "status": "superseded",
            "superseded_input_id": "review-no-such-intervention",
        },
    ],
)
def test_quarantine_requires_exact_count_and_authorized_status(monkeypatch, updates):
    state, failures = _quarantined_failure(monkeypatch)
    completion, command, context = failures[-1]
    quarantine = next(iter(state.lineage_quarantines.values()))
    corrupt = replace(
        state,
        lineage_quarantines={quarantine.quarantine_id: replace(quarantine, **updates)},
    )
    assert not runtime_failure_applied(corrupt, completion.run_id)
    assert not decide(corrupt, command, context).accepted


@pytest.mark.parametrize("kind", ["resume", "close"])
def test_authorized_quarantine_intervention_preserves_timeout_history(
    monkeypatch, kind
):
    state, failures = _quarantined_failure(monkeypatch)
    quarantine = next(iter(state.lineage_quarantines.values()))
    constructor = OperatorResumeLineage if kind == "resume" else OperatorCloseLineage
    intervention = constructor(
        f"review-operator-{kind}",
        option_id=f"admission.{kind}",
        selected_plan_ref=quarantine.selected_plan_ref,
        quarantine_id=quarantine.quarantine_id,
        lineage_id=quarantine.lineage_id,
        actor_id="local-operator",
        actor_kind="local_operator",
        reason=f"review operator {kind}",
        payload={},
    )
    state = _apply_accepted_input(
        state,
        intervention,
        deterministic_context(
            transition_id=f"review-operator-{kind}-transition",
            activation_id=f"review-operator-{kind}-activation",
        ),
    )
    for completion, command, context in failures:
        assert runtime_failure_applied(state, completion.run_id)
        replay = decide(state, command, context)
        assert replay.accepted and replay.disposition == "replayed"
        assert apply(state, replay) == state
    validate_loaded_runtime_state(state)
    if kind == "resume":
        state = _claim(
            state, "review-operator-resume-activation", "review-resumed-source"
        )
        state, resumed = _timeout_for_run(state, "review-resumed-source", 8000)
        state, _, _ = _apply_failure(state, resumed, suffix="resumed")
        assert len(state.recovery_attempts) == 2
        current = next(
            a for a in state.recovery_attempts.values() if a.phase != "resolved"
        )
        assert current.attempt_count == 1
        for completion, command, context in failures:
            assert runtime_failure_applied(state, completion.run_id)
            assert decide(state, command, context).accepted
        assert runtime_failure_applied(state, resumed.run_id)
        validate_loaded_runtime_state(state)


def test_mixed_sibling_failure_and_agent_reset_preserve_cooldown_history(monkeypatch):
    state, first = failed_state(monkeypatch)
    state, _, _ = _apply_failure(state, first)
    state = _return(state, "review-recovery-first", "first")
    state = _claim(state, "review-parent-first", "review-source-second-timeout")
    state, second = _timeout_for_run(state, "review-source-second-timeout", 3000)
    state, command, context = _apply_failure(state, second, suffix="second")
    wait = next(iter(state.cooldown_waits.values()))
    attempt = next(iter(state.recovery_attempts.values()))
    source = state.activations["review-parent-first"]
    for suffix in ("failure", "success"):
        activation = replace(
            source,
            activation_id=f"review-sibling-{suffix}",
            generation=0,
            created_by_input_id=f"review-synthetic-sibling-{suffix}",
            claimed_by_run_id=None,
        )
        state = replace(
            state,
            activations={**state.activations, activation.activation_id: activation},
        )
        state = _claim(state, activation.activation_id, f"review-sibling-{suffix}")
    state, pending = _timeout_for_run(state, "review-sibling-failure", 3500)
    pending_command = runtime_failure_input(state, pending)
    pending_decision = decide(state, pending_command, deterministic_context())
    assert not pending_decision.accepted
    assert pending_decision.refusal.reason == "cooldown_wait_pending"
    assert pending_decision.receipt_ref is None
    assert pending_command.input_id not in state.receipts
    plan = next(iter(state.admitted_plans.values())).selected_plan
    state = _apply_generic_observation(
        state,
        plan,
        next(iter(state.admitted_plans)),
        run_id="review-sibling-success",
        action_id="admission.complete",
        input_id="review-sibling-agent-reset",
        context=deterministic_context(
            transition_id="review-sibling-agent-reset-transition"
        ),
    )
    assert state.recovery_attempts[attempt.record_id] == attempt
    assert state.cooldown_waits[wait.wait_id] == wait
    state = _apply_accepted_input(
        state,
        TimerDue("review-mixed-due", wait_id=wait.wait_id, observed_at=wait.due_at),
        deterministic_context(
            transition_id="review-mixed-due-transition",
            activation_id="review-mixed-due-recovery",
        ),
    )
    assert state.recovery_attempts[attempt.record_id].phase == "active_recovery"
    assert runtime_failure_applied(state, first.run_id)
    assert runtime_failure_applied(state, second.run_id)
    assert pending_command.input_id not in state.receipts
    assert decide(state, command, context).accepted
    validate_loaded_runtime_state(state)


def test_runtime_timeouts_cooldown_timer_and_quarantine_preserve_all_prior_settlement(
    monkeypatch,
):
    state, first = failed_state(monkeypatch)
    state, first_command, first_context = _apply_failure(state, first)
    state = _return(state, "review-recovery-first", "first")
    state = _claim(state, "review-parent-first", "review-source-second-timeout")
    state, second = _timeout_for_run(state, "review-source-second-timeout", 3000)
    state, second_command, second_context = _apply_failure(
        state, second, suffix="second"
    )
    wait = next(iter(state.cooldown_waits.values()))
    assert next(iter(state.recovery_attempts.values())).phase == "pending_cooldown"
    assert runtime_failure_applied(state, first.run_id)
    assert runtime_failure_applied(state, second.run_id)
    early = decide(
        state,
        TimerDue("review-too-early", wait_id=wait.wait_id, observed_at=wait.due_at - 1),
        deterministic_context(),
    )
    assert not early.accepted
    state = _apply_accepted_input(
        state,
        TimerDue("review-due", wait_id=wait.wait_id, observed_at=wait.due_at),
        deterministic_context(
            transition_id="review-due-transition", activation_id="review-due-recovery"
        ),
    )
    assert runtime_failure_applied(state, second.run_id)
    state = _return(state, "review-due-recovery", "second")
    state = _claim(state, "review-parent-second", "review-source-third-timeout")
    state, third = _timeout_for_run(state, "review-source-third-timeout", 5000)
    state, third_command, third_context = _apply_failure(state, third, suffix="third")
    assert next(iter(state.recovery_attempts.values())).phase == "quarantine_eligible"
    assert len(state.lineage_quarantines) == 1
    assert not list_ready_dispatch_candidates(state).candidates
    for completion, command, context in (
        (first, first_command, first_context),
        (second, second_command, second_context),
        (third, third_command, third_context),
    ):
        assert runtime_failure_applied(state, completion.run_id)
        replay = decide(state, command, context)
        assert replay.accepted and replay.disposition == "replayed"
        assert apply(state, replay) == state
    validate_loaded_runtime_state(state)
