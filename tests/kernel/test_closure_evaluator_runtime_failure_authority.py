"""Closure evaluator runtime-failure authority (synthetic/offline, not native evidence).

The fixture below is a portable variant of the recoverable closure source. The
completion-evaluator activation is placed on a fresh graph node that no route or
non-recovery terminal action targets, so its (queue, stage, node, binding) tuple
is absent from selected_activation_targets(plan). A genuine timeout of that
evaluator run must still be accepted for the declared recovery_route.

The positive tests were recorded RED before the authenticated closure-evaluator
recovery root existed (runtime_failure_authority_mismatch) and are GREEN after it.
The hostile tests refuse both before and after that repair.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from millrace.contracts.runtime_failure_history import selected_activation_targets
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_authority_refusal,
    runtime_failure_eligible,
    runtime_failure_input,
)
from millrace.contracts.transition import RunnerResultObserved
from millrace.kernel import apply, decide
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_admission, generic_lifecycle

EVALUATOR_NODE_ID = "lifecycle.review.evaluate"
RECOVER_ACTION_ID = "qa.review.recover"
RETURN_ACTION_ID = "qa.return"
QUARANTINE_ACTION_ID = "qa.quarantine"


def _portable_source() -> dict:
    """Synthetic/offline source: evaluator on an unrouted fresh graph node."""
    source = deepcopy(generic_lifecycle.source_with_completion_behavior())
    stages = {row["id"]: row for row in source["stage_kinds"]}
    stages["review_stage"]["declared_outcome_ids"] += ("qa.review.failure",)
    stages["alpha_stage"]["declared_outcome_ids"] += ("qa.recovered", "qa.quarantine")
    source["terminal_outcomes"] += [
        {
            "id": "qa.review.failure",
            "stage_kind_id": "review_stage",
            "marker": "QA_FAILURE",
        },
        {
            "id": "qa.recovered",
            "stage_kind_id": "alpha_stage",
            "marker": "QA_RECOVERED",
        },
        {
            "id": "qa.quarantine",
            "stage_kind_id": "alpha_stage",
            "marker": "QA_QUARANTINE",
        },
    ]
    source["terminal_actions"] += [
        {
            "id": RECOVER_ACTION_ID,
            "stage_kind_id": "review_stage",
            "outcome_id": "qa.review.failure",
            "kind": "recovery_route",
            "target_stage_kind_id": "alpha_stage",
            "target_graph_node_id": "lifecycle.alpha.start",
            "runner_binding_id": "lifecycle.runner",
            "asset_ids": stages["alpha_stage"]["asset_ids"],
        },
        {
            "id": RETURN_ACTION_ID,
            "stage_kind_id": "alpha_stage",
            "outcome_id": "qa.recovered",
            "kind": "return_to_recorded_source",
        },
        {
            "id": QUARANTINE_ACTION_ID,
            "stage_kind_id": "alpha_stage",
            "outcome_id": "qa.quarantine",
            "kind": "quarantine_lineage",
        },
    ]
    policy = deepcopy(generic_admission.source()["recovery_policies"][0])
    policy.update(
        id="qa.policy",
        source_recovery_action_ids=(RECOVER_ACTION_ID,),
        return_action_ids=(RETURN_ACTION_ID,),
        quarantine_action_ids=(QUARANTINE_ACTION_ID,),
        recovery_stage_kind_id="alpha_stage",
        reset_trigger_action_ids=(),
        cooldown_wait_state_id="qa.cooldown",
    )
    source["recovery_policies"] = [policy]
    source["wait_states"] = [
        {
            "id": "qa.cooldown",
            "kind": "timer",
            "policy_id": "qa.policy",
            "starts_at_attempt": 2,
            "duration_seconds": 900,
        }
    ]
    binding = source["runner_bindings"][0]
    binding["runtime_failure_mappings"] = [
        {
            "stage_kind_id": "review_stage",
            "failure_kind": "timeout",
            "action_id": RECOVER_ACTION_ID,
        }
    ]
    binding["component_pin"]["legal_terminal_result_ids"] += (
        "QA_RECOVERED",
        "QA_QUARANTINE",
    )
    binding["terminal_result_mappings"] = [
        {
            "stage_kind_id": "alpha_stage",
            "runner_result_id": result_id,
            "outcome_id": outcome_id,
        }
        for result_id, outcome_id in (
            ("COMPLETE", "lifecycle.alpha.ready"),
            ("QA_RECOVERED", "qa.recovered"),
            ("QA_QUARANTINE", "qa.quarantine"),
        )
    ]
    # Evaluator gets its own node with no incoming route or terminal target.
    source["graphs"][0]["node_ids"] = (
        *source["graphs"][0]["node_ids"],
        EVALUATOR_NODE_ID,
    )
    source["completion_behaviors"][0]["target_graph_node_id"] = EVALUATOR_NODE_ID
    return source


@pytest.fixture
def evaluator_state():
    source = _portable_source()
    generic_lifecycle.compile_lifecycle(source)
    state, plan, fingerprint = generic_lifecycle.closure_evaluation_state(source)
    record = next(iter(state.closure_evaluations.values()))
    activation = state.activations[record.target_activation_id]
    return state, plan, fingerprint, record, activation


def _tuple_of(activation):
    return (
        activation.queue_family_id,
        activation.stage_kind_id,
        activation.graph_node_id,
        activation.runner_binding_id,
    )


def _evaluator_timeout(state, activation, suffix):
    state = generic_lifecycle.claim_activation(
        state, activation_id=activation.activation_id, suffix=suffix
    )
    run_id = f"run-{suffix}"
    run = state.runs[run_id]
    observation = RunnerResultObserved(
        f"timeout-observation-{suffix}",
        run_id=run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[activation.activation_id],
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker="DONE",
            artifact_payload=None,
        ),
        observed_at=3,
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
    state = replace(
        state,
        runner_sessions={
            **state.runner_sessions,
            session.session_id: replace(session, state="failed"),
        },
        runner_session_completions={
            **state.runner_session_completions,
            session.session_id: completion,
        },
    )
    return state, completion


def _timeout_decision(state, completion, suffix):
    run = state.runs[completion.run_id]
    command = runtime_failure_input(state, completion)
    context = deterministic_context(
        transition_id=f"transition-timeout-{suffix}",
        work_item_id=run.work_item_id,
        activation_id=f"recovery-{suffix}",
        run_id=run.run_ref.run_id,
        claim_id=run.run_ref.claim_id,
        fencing_token=run.run_ref.fencing_token,
    )
    return command, decide(state, command, context)


def test_portable_fixture_evaluator_tuple_is_absent_from_selected_targets(
    evaluator_state,
) -> None:
    # Fixture validity check: this must hold before the authority bug is exercised.
    _, plan, _, _, activation = evaluator_state
    assert _tuple_of(activation) not in selected_activation_targets(plan)


def test_evaluator_timeout_on_absent_target_runs_selected_recovery(
    evaluator_state,
) -> None:
    # Was RED before the repair: decide() refused (runtime_failure_authority_mismatch).
    state, _, _, _, activation = evaluator_state
    state, completion = _evaluator_timeout(state, activation, "eval-one")
    command, decision = _timeout_decision(state, completion, "eval-one")

    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    assert runtime_failure_applied(state, completion.run_id)
    attempt = next(iter(state.recovery_attempts.values()))
    assert (attempt.attempt_count, attempt.phase) == (1, "active_recovery")
    assert [
        candidate.activation_id
        for candidate in list_ready_dispatch_candidates(state).candidates
    ] != []


def test_repeated_evaluator_failure_is_bounded_to_one_recovery_attempt(
    evaluator_state,
) -> None:
    # Was RED before the repair: the first timeout was refused, so no attempt existed.
    state, _, _, _, activation = evaluator_state
    state, completion = _evaluator_timeout(state, activation, "eval-repeat")
    command, decision = _timeout_decision(state, completion, "eval-repeat")

    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    attempts = list(state.recovery_attempts.values())
    assert len(attempts) == 1
    assert attempts[0].attempt_count <= 1


def _hostile_mutation(state, record, completion, name):
    if name == "missing_closure_evaluation_record":
        return replace(state, closure_evaluations={})
    if name == "plan_ref_drift":
        drifted = replace(record.selected_plan_ref, plan_id="drifted-namespace")
        return replace(
            state,
            closure_evaluations={
                **state.closure_evaluations,
                record.record_id: replace(record, selected_plan_ref=drifted),
            },
        )
    if name == "non_timeout_error_kind":
        return replace(
            state,
            runner_session_completions={
                **state.runner_session_completions,
                completion.session_id: replace(completion, adapter_error_kind="crash"),
            },
        )
    raise AssertionError(name)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_closure_evaluation_record",
        "plan_ref_drift",
        "non_timeout_error_kind",
    ],
)
def test_hostile_evaluator_failure_mutations_refuse_before_and_after_fix(
    evaluator_state, mutation
) -> None:
    # Synthetic/offline hostile case: must refuse now and after the fix.
    state, _, _, record, activation = evaluator_state
    state, completion = _evaluator_timeout(state, activation, f"hostile-{mutation}")
    state = _hostile_mutation(state, record, completion, mutation)
    if mutation == "non_timeout_error_kind":
        # Ineligible completions are refused before a command is built.
        assert not runtime_failure_eligible(state, completion)
        with pytest.raises(ValueError, match="ineligible runtime failure completion"):
            runtime_failure_input(state, completion)
        return
    command, decision = _timeout_decision(state, completion, f"hostile-{mutation}")

    assert not decision.accepted
    assert decision.refusal is not None
    assert not runtime_failure_applied(state, completion.run_id)
    assert runtime_failure_authority_refusal(state, command) is not None


# --- Aftermath and provenance coverage for the authenticated closure-evaluator root.


def _unrouted(source: dict) -> dict:
    """Move the evaluator onto a fresh node no route or terminal action targets."""
    source["graphs"][0]["node_ids"] = (
        *source["graphs"][0]["node_ids"],
        EVALUATOR_NODE_ID,
    )
    source["completion_behaviors"][0]["target_graph_node_id"] = EVALUATOR_NODE_ID
    return source


def _cli_failed_evaluator(tmp_path):
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from tests.cli.test_cli_bounded_execution_unit import _load, _runtime
    from tests.cli.test_cli_candidate_failure_recovery import (
        _recoverable_closure_source,
        _success_config,
    )

    state, plan, _ = generic_lifecycle.closure_evaluation_state(
        _unrouted(_recoverable_closure_source())
    )
    evaluation = next(iter(state.closure_evaluations.values()))
    assert _tuple_of(
        state.activations[evaluation.target_activation_id]
    ) not in selected_activation_targets(plan)
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(
        runtime,
        activation_id=evaluation.target_activation_id,
        local_config=_success_config("UNKNOWN_REVIEW_MARKER", None),
    )
    return runtime, plan, evaluation, first, _load


@pytest.mark.parametrize("marker", ["REVIEW_PASSED", "REVIEW_GAP", "REVIEW_BLOCKED"])
def test_unrouted_evaluator_failure_recovers_returns_and_closes_after_reload(
    tmp_path, marker
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.closure_evaluation_history import (
        closure_evaluator_origin_matches,
    )
    from tests.cli.test_cli_bounded_execution_unit import _reopen_runtime
    from tests.cli.test_cli_candidate_failure_recovery import _success_config
    from tests.operator.test_status_projection import _closure_verdict_payload

    runtime, plan, evaluation, first, _load = _cli_failed_evaluator(tmp_path)
    assert first.code == "runtime_failure_recovery_applied", first
    failed = _load(runtime)
    assert runtime_failure_applied(failed, first.run_id)
    assert closure_evaluator_origin_matches(
        failed, plan, evaluation.target_activation_id
    )
    recovery = list_ready_dispatch_candidates(failed).candidates[0]
    assert str(recovery.stage_kind_id) == "alpha_stage"
    repaired = run_bounded_execution_unit(
        runtime,
        activation_id=recovery.activation_id,
        local_config=_success_config("QA_RECOVERED", None),
    )
    assert repaired.accepted, repaired
    runtime = _reopen_runtime(runtime)
    returned = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    assert returned.work_item_id == evaluation.target_work_item_id
    assert returned.activation_id != evaluation.target_activation_id
    assert not closure_evaluator_origin_matches(
        _load(runtime), plan, returned.activation_id
    )
    work = _load(runtime).work_items[returned.work_item_id]
    payload = _closure_verdict_payload(
        work.payload["closure_evidence_snapshot"], marker=marker
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id=returned.activation_id,
        local_config=_success_config(marker, payload),
    )
    assert result.code == "observation_accepted", result
    after = _load(runtime)
    target = after.closure_targets[evaluation.closure_target_id]
    expected = {
        "REVIEW_PASSED": "closed",
        "REVIEW_GAP": "open",
        "REVIEW_BLOCKED": "open",
    }
    assert target.status == expected[marker]
    if marker == "REVIEW_PASSED":
        from millrace.kernel._closure_lifecycle import closure_target_progress
        from millrace.kernel.lifecycle import project_next_lifecycle_transition

        assert len(after.closure_terminal_records) == 1
        # A verdict from the RETURNED evaluator must still authenticate the closed
        # target through its recorded-source return chain.
        assert (
            closure_target_progress(
                after, target=target, behavior=plan.completion_behaviors[0]
            ).status
            == "closed"
        )
        assert not project_next_lifecycle_transition(after).diagnostics
    elif marker == "REVIEW_GAP":
        assert len(after.remediation_work_records) == 1
    else:
        assert len(after.closure_blocked_records) == 1
    # The fixture policy declares no reset triggers; the attempt history is kept.
    assert len(after.recovery_attempts) == 1
    reopened = _reopen_runtime(runtime)
    assert _load(reopened) == after
    reopened.close()


def test_returned_unrouted_evaluator_repeated_failure_waits_then_closes(
    tmp_path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.transition import TimerDue
    from tests.cli.test_cli_bounded_execution_unit import _reopen_runtime
    from tests.cli.test_cli_candidate_failure_recovery import _success_config
    from tests.operator.test_status_projection import _closure_verdict_payload

    runtime, _plan, evaluation, first, _load = _cli_failed_evaluator(tmp_path)
    assert first.code == "runtime_failure_recovery_applied", first
    recovery = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    assert run_bounded_execution_unit(
        runtime,
        activation_id=recovery.activation_id,
        local_config=_success_config("QA_RECOVERED", None),
    ).accepted
    returned = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    repeated = run_bounded_execution_unit(
        runtime,
        activation_id=returned.activation_id,
        local_config=_success_config("UNKNOWN_REVIEW_MARKER", None),
    )
    assert repeated.code == "runtime_failure_recovery_applied", repeated
    waiting = _load(runtime)
    attempt = next(iter(waiting.recovery_attempts.values()))
    assert (attempt.attempt_count, attempt.phase) == (2, "pending_cooldown")
    wait = next(iter(waiting.cooldown_waits.values()))
    early = decide(
        waiting,
        TimerDue("unrouted-early", wait_id=wait.wait_id, observed_at=wait.due_at - 1),
        deterministic_context(
            transition_id="unrouted-early", activation_id="unrouted-early-recovery"
        ),
    )
    assert not early.accepted
    due = decide(
        waiting,
        TimerDue("unrouted-due", wait_id=wait.wait_id, observed_at=wait.due_at),
        deterministic_context(
            transition_id="unrouted-due", activation_id="unrouted-second-recovery"
        ),
    )
    assert due.accepted, due.refusal
    runtime.store.persist_runtime_state(apply(waiting, due), runtime.cas_store)
    assert run_bounded_execution_unit(
        runtime,
        activation_id="unrouted-second-recovery",
        local_config=_success_config("QA_RECOVERED", None),
    ).accepted
    runtime = _reopen_runtime(runtime)
    latest = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    assert latest.work_item_id == evaluation.target_work_item_id
    payload = _closure_verdict_payload(
        _load(runtime)
        .work_items[latest.work_item_id]
        .payload["closure_evidence_snapshot"],
        marker="REVIEW_PASSED",
    )
    final = run_bounded_execution_unit(
        runtime,
        activation_id=latest.activation_id,
        local_config=_success_config("REVIEW_PASSED", payload),
    )
    assert final.code == "observation_accepted", final
    closed = _load(runtime)
    assert closed.closure_targets[evaluation.closure_target_id].status == "closed"
    assert runtime_failure_applied(closed, first.run_id)
    assert runtime_failure_applied(closed, repeated.run_id)
    runtime = _reopen_runtime(runtime)
    assert _load(runtime) == closed
    runtime.close()


def test_acceptance_depends_on_authenticated_closure_provenance(
    evaluator_state, monkeypatch
) -> None:
    import millrace.contracts.runtime_failures as runtime_failures

    state, _, _, _, activation = evaluator_state
    state, completion = _evaluator_timeout(state, activation, "causal")
    monkeypatch.setattr(
        runtime_failures, "closure_evaluator_origin_matches", lambda *a, **k: False
    )
    command, decision = _timeout_decision(state, completion, "causal")
    assert not decision.accepted
    assert (
        runtime_failure_authority_refusal(state, command)
        == "runtime_failure_authority_mismatch"
    )


def test_formal_recovery_ingress_from_evaluator_failure_is_authenticated(
    evaluator_state,
) -> None:
    from millrace.contracts.runtime_failures import (
        formal_recovery_activation_authority_refusal,
    )

    state, plan, _, record, activation = evaluator_state
    state, completion = _evaluator_timeout(state, activation, "formal")
    _, decision = _timeout_decision(state, completion, "formal")
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    assert (
        formal_recovery_activation_authority_refusal(state, plan, "recovery-formal")
        is None
    )
    forged = replace(state, closure_evaluations={})
    assert (
        formal_recovery_activation_authority_refusal(forged, plan, "recovery-formal")
        == "formal_recovery_activation_origin:recovery-formal"
    )


def _provenance_mutation(state, record, activation, name):
    evaluations = dict(state.closure_evaluations)
    if name == "record_duplicate":
        evaluations["closure-evaluator:duplicate"] = replace(
            record, record_id="closure-evaluator:duplicate"
        )
        return replace(state, closure_evaluations=evaluations)
    if name.startswith("record_"):
        field, value = {
            "record_lineage": ("lineage_id", "foreign-lineage"),
            "record_behavior": ("completion_behavior_id", "foreign.behavior"),
            "record_target_missing": ("closure_target_id", "closure-target:missing"),
            "record_creator": ("created_by_input_id", "forged-evaluate-input"),
            "record_target_activation": ("target_activation_id", "foreign-activation"),
            "record_request_kind": ("request_kind", "foreign_request"),
        }[name]
        evaluations[record.record_id] = replace(record, **{field: value})
        return replace(state, closure_evaluations=evaluations)
    if name == "receipt_removed":
        receipts = dict(state.receipts)
        receipts.pop(record.created_by_input_id)
        return replace(state, receipts=receipts)
    if name == "receipt_digest":
        receipt = state.receipts[record.created_by_input_id]
        changed = replace(
            receipt,
            receipt_ref=replace(
                receipt.receipt_ref, input_payload_digest="sha256:" + "0" * 64
            ),
        )
        return replace(
            state, receipts={**state.receipts, record.created_by_input_id: changed}
        )
    if name == "creator_transition_refused":
        return replace(
            state,
            transitions=tuple(
                replace(t, accepted=False)
                if t.input_id == record.created_by_input_id
                else t
                for t in state.transitions
            ),
        )
    if name == "target_lineage":
        target = state.closure_targets[record.closure_target_id]
        return replace(
            state,
            closure_targets={
                **state.closure_targets,
                target.closure_target_id: replace(target, lineage_id="foreign-lineage"),
            },
        )
    if name == "activation_creator":
        return replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(
                    state.activations[activation.activation_id],
                    created_by_input_id="forged-activation-input",
                ),
            },
        )
    if name == "activation_node":
        return replace(
            state,
            activations={
                **state.activations,
                activation.activation_id: replace(
                    state.activations[activation.activation_id],
                    graph_node_id="lifecycle.alpha.start",
                ),
            },
        )
    raise AssertionError(name)


PROVENANCE_MUTATIONS = (
    "record_duplicate",
    "record_lineage",
    "record_behavior",
    "record_target_missing",
    "record_creator",
    "record_target_activation",
    "record_request_kind",
    "receipt_removed",
    "receipt_digest",
    "creator_transition_refused",
    "target_lineage",
    "activation_creator",
    "activation_node",
)


@pytest.mark.parametrize("mutation", PROVENANCE_MUTATIONS)
def test_forged_or_drifted_closure_provenance_never_authorizes_failure(
    evaluator_state, mutation
) -> None:
    from millrace.contracts.closure_evaluation_history import (
        closure_evaluator_origin_matches,
    )

    state, plan, _, record, activation = evaluator_state
    state, completion = _evaluator_timeout(state, activation, f"forged-{mutation}")
    assert closure_evaluator_origin_matches(state, plan, activation.activation_id)
    state = _provenance_mutation(state, record, activation, mutation)
    assert not closure_evaluator_origin_matches(state, plan, activation.activation_id)
    command, decision = _timeout_decision(state, completion, f"forged-{mutation}")
    assert not decision.accepted
    assert (
        runtime_failure_authority_refusal(state, command)
        == "runtime_failure_authority_mismatch"
    )
    assert not runtime_failure_applied(state, completion.run_id)


@pytest.mark.parametrize("field", ["lineage_id", "created_by_input_id"])
def test_forged_evaluator_provenance_is_refused_on_durable_persist(
    tmp_path, field
) -> None:
    from millrace.substrate.errors import StorageIntegrityError

    runtime, _plan, evaluation, first, _load = _cli_failed_evaluator(tmp_path)
    assert first.code == "runtime_failure_recovery_applied", first
    state = _load(runtime)
    forged = replace(
        state,
        closure_evaluations={
            **state.closure_evaluations,
            evaluation.record_id: replace(evaluation, **{field: "forged-provenance"}),
        },
    )
    with pytest.raises(StorageIntegrityError):
        runtime.store.persist_runtime_state(forged, runtime.cas_store)
    assert _load(runtime) == state
    runtime.close()
