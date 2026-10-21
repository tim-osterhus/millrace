"""Returned closure evaluators retain exact durable validation authority."""

import pytest
from tests.cli.test_cli_bounded_execution_unit import _load, _reopen_runtime, _runtime
from tests.cli.test_cli_candidate_failure_recovery import (
    _recoverable_closure_source,
    _success_config,
)
from tests.operator.test_status_projection import _closure_verdict_payload

from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.contracts.runtime_failures import (
    runtime_failure_eligible,
)
from millrace.contracts.transition import RunnerResultObserved
from millrace.kernel import apply, decide
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_lifecycle


def test_returned_closure_review_still_refuses_invalid_criterion_status(tmp_path):
    source = _recoverable_closure_source()
    state, _, _ = generic_lifecycle.closure_evaluation_state(source)
    evaluation = next(iter(state.closure_evaluations.values()))
    runtime = _runtime(tmp_path, state)
    failed = run_bounded_execution_unit(
        runtime,
        activation_id=evaluation.target_activation_id,
        local_config=_success_config("UNKNOWN_REVIEW_MARKER", None),
    )
    assert failed.code == "runtime_failure_recovery_applied", failed
    routed = _load(runtime)
    completion = routed.runner_session_completions[
        routed.runs[failed.run_id].current_session_id
    ]
    refusal = routed.receipts[completion.application_input_id]
    recovery = list_ready_dispatch_candidates(routed).candidates[0]
    recovered = run_bounded_execution_unit(
        runtime,
        activation_id=recovery.activation_id,
        local_config=_success_config("QA_RECOVERED", None),
    )
    assert recovered.accepted, recovered
    runtime = _reopen_runtime(runtime)
    returned = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    assert returned.stage_kind_id == "review_stage"
    work = _load(runtime).work_items[returned.work_item_id]
    payload = _closure_verdict_payload(
        work.payload["closure_evidence_snapshot"], marker="REVIEW_PASSED"
    )
    payload["criterion_results"][0]["status"] = "failed"
    repeated = run_bounded_execution_unit(
        runtime,
        activation_id=returned.activation_id,
        local_config=_success_config("REVIEW_PASSED", payload),
    )
    after = _load(runtime)
    repeated_completion = after.runner_session_completions[
        after.runs[repeated.run_id].current_session_id
    ]
    repeated_refusal = after.receipts[repeated_completion.application_input_id]
    assert (
        not repeated_refusal.accepted
        and repeated_refusal.refusal_reason == "closure_marker_status_mismatch"
    ), repeated
    assert after.runner_session_completions[completion.session_id] == completion
    assert after.receipts[completion.application_input_id] == refusal
    assert evaluation.target_work_item_id not in after.closed_work_items
    reopened = _reopen_runtime(runtime)
    assert _load(reopened) == after
    reopened.close()


@pytest.mark.parametrize("marker", ["REVIEW_PASSED", "REVIEW_GAP", "REVIEW_BLOCKED"])
def test_valid_returned_closure_source_keeps_terminal_contract_and_reloads(
    tmp_path, marker
):
    from millrace.kernel._closure_lifecycle import closure_target_progress
    from millrace.kernel.lifecycle import (
        project_next_lifecycle_transition,
    )

    state, plan, _ = generic_lifecycle.closure_evaluation_state(
        _recoverable_closure_source()
    )
    evaluation = next(iter(state.closure_evaluations.values()))
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(
        runtime,
        activation_id=evaluation.target_activation_id,
        local_config=_success_config("UNKNOWN_REVIEW_MARKER", None),
    )
    assert first.code == "runtime_failure_recovery_applied"
    source = _load(runtime)
    completion = source.runner_session_completions[
        source.runs[first.run_id].current_session_id
    ]
    original = source.receipts[completion.application_input_id]
    recovery = list_ready_dispatch_candidates(source).candidates[0]
    repaired = run_bounded_execution_unit(
        runtime,
        activation_id=recovery.activation_id,
        local_config=_success_config("QA_RECOVERED", None),
    )
    assert repaired.accepted
    runtime = _reopen_runtime(runtime)
    returned = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
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
    assert after.runner_session_completions[completion.session_id] == completion
    assert after.receipts[completion.application_input_id] == original
    target = after.closure_targets[evaluation.closure_target_id]
    if marker == "REVIEW_PASSED":
        assert target.status == "closed"
        assert len(after.closure_terminal_records) == 1
    elif marker == "REVIEW_GAP":
        assert target.status == "open"
        assert len(after.remediation_work_records) == 1
        assert (
            closure_target_progress(
                after, target=target, behavior=plan.completion_behaviors[0]
            ).status
            == "pending"
        )
    else:
        assert target.status == "open"
        assert len(after.closure_blocked_records) == 1
        assert project_next_lifecycle_transition(after).candidate is None
    reopened = _reopen_runtime(runtime)
    assert _load(reopened) == after
    reopened.close()


def _returned_closure(tmp_path):
    from millrace.contracts.runtime_failures import runtime_failure_applied

    state, plan, _ = generic_lifecycle.closure_evaluation_state(
        _recoverable_closure_source()
    )
    evaluation = next(iter(state.closure_evaluations.values()))
    runtime = _runtime(tmp_path, state)
    failure = run_bounded_execution_unit(
        runtime,
        activation_id=evaluation.target_activation_id,
        local_config=_success_config("UNKNOWN_REVIEW_MARKER", None),
    )
    assert failure.code == "runtime_failure_recovery_applied"
    assert runtime_failure_applied(_load(runtime), failure.run_id)
    recovery = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    assert run_bounded_execution_unit(
        runtime,
        activation_id=recovery.activation_id,
        local_config=_success_config("QA_RECOVERED", None),
    ).accepted
    runtime = _reopen_runtime(runtime)
    returned = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    return runtime, plan, evaluation, returned


@pytest.mark.parametrize(
    "part",
    [
        "missing_return",
        "duplicate_return",
        "foreign_emitter",
        "foreign_action",
        "missing_receipt",
        "foreign_fence",
        "missing_evaluation",
        "duplicate_evaluation",
        "foreign_plan",
        "durable_target_node",
    ],
)
def test_returned_evaluator_corruption_never_becomes_an_ordinary_result(tmp_path, part):
    from dataclasses import replace

    from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
    from millrace.substrate.errors import StorageIntegrityError

    runtime, plan, evaluation, returned = _returned_closure(tmp_path)
    state = generic_lifecycle.claim_activation(
        _load(runtime), activation_id=returned.activation_id, suffix="corrupt-return"
    )
    run = state.runs["run-corrupt-return"]
    route = next(
        r
        for r in state.activation_routes
        if r.target_activation_id == returned.activation_id
    )
    bad = state
    if part == "missing_return":
        bad = replace(
            state,
            activation_routes=tuple(r for r in state.activation_routes if r != route),
        )
    elif part == "duplicate_return":
        bad = replace(
            state,
            activation_routes=(
                *state.activation_routes,
                replace(route, record_id="duplicate"),
            ),
        )
    elif part in {"foreign_emitter", "foreign_action"}:
        change = (
            {"source_run_id": "run-origin"}
            if part == "foreign_emitter"
            else {"action_id": plan.terminal_actions[0].id}
        )
        bad = replace(
            state,
            activation_routes=tuple(
                replace(r, **change) if r == route else r
                for r in state.activation_routes
            ),
        )
    elif part == "missing_receipt":
        bad = replace(
            state,
            receipts={
                k: v
                for k, v in state.receipts.items()
                if k != route.created_by_input_id
            },
        )
    elif part == "foreign_fence":
        emitter = state.runs[route.source_run_id]
        bad = replace(
            state,
            runs={
                **state.runs,
                route.source_run_id: replace(
                    emitter, run_ref=replace(emitter.run_ref, fencing_token="foreign")
                ),
            },
        )
    elif part == "missing_evaluation":
        bad = replace(state, closure_evaluations={})
    elif part == "duplicate_evaluation":
        duplicate = replace(evaluation, record_id="duplicate-evaluation")
        bad = replace(
            state,
            closure_evaluations={
                **state.closure_evaluations,
                duplicate.record_id: duplicate,
            },
        )
    elif part == "foreign_plan":
        bad = replace(
            state,
            closure_evaluations={
                evaluation.record_id: replace(
                    evaluation,
                    selected_plan_ref=replace(
                        evaluation.selected_plan_ref, plan_id="foreign"
                    ),
                )
            },
        )
    else:
        target = state.closure_targets[evaluation.closure_target_id]
        bad = replace(
            state,
            closure_targets={
                target.closure_target_id: replace(
                    target, target_graph_node_id="lifecycle.alpha.start"
                )
            },
        )
    payload = _closure_verdict_payload(
        state.work_items[run.work_item_id].payload["closure_evidence_snapshot"],
        marker="REVIEW_PASSED",
    )
    observation = RunnerResultObserved(
        "corrupt-return-observe",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker="REVIEW_PASSED",
            artifact_payload=payload,
        ),
        observed_at=2000,
    )
    bad, observation = fake_completed_runner_observation_state(
        state=bad, observation=observation
    )
    decision = decide(
        bad, observation, deterministic_context(transition_id="corrupt-return-refused")
    )
    assert not decision.accepted, part
    assert (
        decision.refusal.reason == "closure_target_mismatch"
        if part == "durable_target_node"
        else decision.refusal.reason.startswith("closure_evaluation_")
    )
    refused = apply(bad, decision)
    completion = refused.runner_session_completions[
        refused.runs[run.run_ref.run_id].current_session_id
    ]
    assert not runtime_failure_eligible(refused, completion)
    assert refused.artifacts == bad.artifacts
    assert refused.runner_observations == bad.runner_observations
    assert refused.closed_work_items == bad.closed_work_items
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(refused)
    runtime.close()


def test_repeated_closure_failure_return_then_valid_pass_preserves_history(tmp_path):
    from millrace.contracts.runtime_failures import runtime_failure_applied
    from millrace.contracts.transition import TimerDue

    runtime, _plan, evaluation, returned = _returned_closure(tmp_path)
    before = _load(runtime)
    original = before.runner_session_completions[
        before.runs[
            next(
                r.run_ref.run_id
                for r in before.runs.values()
                if r.activation_id == evaluation.target_activation_id
            )
        ].current_session_id
    ]
    bad_payload = _closure_verdict_payload(
        before.work_items[returned.work_item_id].payload["closure_evidence_snapshot"],
        marker="REVIEW_PASSED",
    )
    bad_payload["criterion_results"][0]["status"] = "failed"
    repeated = run_bounded_execution_unit(
        runtime,
        activation_id=returned.activation_id,
        local_config=_success_config("REVIEW_PASSED", bad_payload),
    )
    assert repeated.code == "runtime_failure_recovery_applied"
    waiting = _load(runtime)
    repeated_completion = waiting.runner_session_completions[
        waiting.runs[repeated.run_id].current_session_id
    ]
    assert (
        waiting.receipts[repeated_completion.application_input_id].refusal_reason
        == "closure_marker_status_mismatch"
    )
    wait = next(iter(waiting.cooldown_waits.values()))
    command = TimerDue(
        "closure-second-due", wait_id=wait.wait_id, observed_at=wait.due_at
    )
    due = decide(
        waiting,
        command,
        deterministic_context(
            transition_id="closure-second-due", activation_id="closure-second-recovery"
        ),
    )
    assert due.accepted
    runtime.store.persist_runtime_state(apply(waiting, due), runtime.cas_store)
    assert run_bounded_execution_unit(
        runtime,
        activation_id="closure-second-recovery",
        local_config=_success_config("QA_RECOVERED", None),
    ).accepted
    runtime = _reopen_runtime(runtime)
    latest = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    good_payload = _closure_verdict_payload(
        _load(runtime)
        .work_items[latest.work_item_id]
        .payload["closure_evidence_snapshot"],
        marker="REVIEW_PASSED",
    )
    final = run_bounded_execution_unit(
        runtime,
        activation_id=latest.activation_id,
        local_config=_success_config("REVIEW_PASSED", good_payload),
    )
    assert final.code == "observation_accepted", final
    closed = _load(runtime)
    assert closed.closure_targets[evaluation.closure_target_id].status == "closed"
    assert closed.runner_session_completions[original.session_id] == original
    assert (
        closed.runner_session_completions[repeated_completion.session_id]
        == repeated_completion
    )
    assert runtime_failure_applied(closed, original.run_id)
    assert runtime_failure_applied(closed, repeated.run_id)
    assert closed.receipts[original.application_input_id].accepted is False
    assert closed.receipts[repeated_completion.application_input_id].accepted is False
    runtime = _reopen_runtime(runtime)
    assert _load(runtime) == closed
    replay = run_bounded_execution_unit(
        runtime,
        activation_id=returned.activation_id,
        local_config=_success_config("REVIEW_PASSED", good_payload),
    )
    assert replay.code == "runtime_failure_recovery_applied"
    assert replay.transition_disposition == "replayed"
    assert _load(runtime) == closed
    runtime.close()
