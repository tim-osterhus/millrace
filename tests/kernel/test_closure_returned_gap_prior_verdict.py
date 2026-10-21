"""Closure returned-GAP prior-verdict provenance (synthetic, offline, not native).

Fixture: fake runner I/O only. The completion evaluator times out and is recovered
(recovery_route). The recovered evaluator is returned (return_to_recorded_source)
and reports a GAP verdict. The remediation alpha run then completes, so the closure
target is settled and the next closure evaluation must be able to cite the returned
GAP verdict as its prior_verdict.

Defect under test: the next-evaluation snapshot is refused with
closure_prior_verdict_provenance_mismatch even though the returned GAP verdict is
authentic. Before the product repair the positive lifecycle test, the unmutated
control and the origin-authentication test are RED. The hostile ownership
mutations are expected to refuse both before and after the repair.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.contracts.closure_evaluation_history import (
    closure_evaluation_origin_refusal,
    closure_evaluator_origin_matches,
)
from millrace.contracts.runtime_failures import runtime_failure_applied
from millrace.kernel import apply, decide
from millrace.kernel._closure_lifecycle import (
    assess_closure_readiness,
    closure_evaluation_for_run,
    closure_target_key_for,
)
from millrace.kernel.decision import _build_closure_evidence_snapshot
from millrace.kernel.lifecycle import project_next_lifecycle_transition
from millrace.operator.dispatch import list_ready_dispatch_candidates
from support import generic_lifecycle
from tests.cli.test_cli_bounded_execution_unit import _load, _reopen_runtime, _runtime
from tests.cli.test_cli_candidate_failure_recovery import (
    _recoverable_closure_source,
    _success_config,
)
from tests.operator.test_status_projection import _closure_verdict_payload

EVALUATOR_NODE_ID = "lifecycle.review.evaluate"
RESET_TRIGGER_ACTION_ID = "lifecycle.alpha.complete"
REMEDIATION_MARKER = "ALPHA_READY"
FAKE_FINGERPRINT = "sha256:" + "0" * 64

MUTATIONS = (
    "unmutated",
    "borrowed_existing_run",
    "wrong_return_activation",
    "wrong_return_plan",
    "wrong_return_work",
    "wrong_return_lineage",
    "forged_return_route",
    "recovery_receipt_rejected",
    "verdict_receipt_rejected",
    "foreign_evaluation_lineage",
)


def _unrouted(source: dict[str, Any]) -> dict[str, Any]:
    node_ids = (*source["graphs"][0]["node_ids"], EVALUATOR_NODE_ID)
    source["graphs"][0]["node_ids"] = node_ids
    source["completion_behaviors"][0]["target_graph_node_id"] = EVALUATOR_NODE_ID
    return source


@dataclass(frozen=True)
class _Remediated:
    runtime: Any
    plan: Any
    evaluation: Any
    failure_run_id: str
    recovery_run_id: str
    gap_run_id: str
    remediation_run_id: str


def _returned_gap_remediated(tmp_path) -> _Remediated:
    source = _unrouted(_recoverable_closure_source())
    source["recovery_policies"][0]["reset_trigger_action_ids"] = (
        RESET_TRIGGER_ACTION_ID,
    )
    state, plan, _ = generic_lifecycle.closure_evaluation_state(source)
    evaluation = next(iter(state.closure_evaluations.values()))
    runtime = _runtime(tmp_path, state)

    failed = run_bounded_execution_unit(
        runtime,
        activation_id=evaluation.target_activation_id,
        local_config=_success_config("UNKNOWN_REVIEW_MARKER", None),
    )
    assert failed.code == "runtime_failure_recovery_applied", failed
    recovery = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    repaired = run_bounded_execution_unit(
        runtime,
        activation_id=recovery.activation_id,
        local_config=_success_config("QA_RECOVERED", None),
    )
    assert repaired.accepted, repaired

    runtime = _reopen_runtime(runtime)
    returned = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    assert returned.activation_id != evaluation.target_activation_id
    work = _load(runtime).work_items[returned.work_item_id]
    verdict = _closure_verdict_payload(
        work.payload["closure_evidence_snapshot"], marker="REVIEW_GAP"
    )
    gap = run_bounded_execution_unit(
        runtime,
        activation_id=returned.activation_id,
        local_config=_success_config("REVIEW_GAP", verdict),
    )
    assert gap.code == "observation_accepted", gap

    runtime = _reopen_runtime(runtime)
    remediation = next(iter(_load(runtime).remediation_work_records.values()))
    remediated = run_bounded_execution_unit(
        runtime,
        activation_id=remediation.target_activation_id,
        local_config=_success_config(
            REMEDIATION_MARKER, generic_lifecycle.report_payload("alpha")
        ),
    )
    assert remediated.accepted, remediated

    runtime = _reopen_runtime(runtime)
    assert remediation.target_work_item_id in _load(runtime).closed_work_items
    return _Remediated(
        runtime=runtime,
        plan=plan,
        evaluation=evaluation,
        failure_run_id=failed.run_id,
        recovery_run_id=repaired.run_id,
        gap_run_id=gap.run_id,
        remediation_run_id=remediated.run_id,
    )


def _snapshot(state, fixture: _Remediated):
    target = state.closure_targets[fixture.evaluation.closure_target_id]
    return _build_closure_evidence_snapshot(
        state=state,
        target=target,
        behavior=fixture.plan.completion_behaviors[0],
    )


def _gap_verdict(state, gap_run_id: str):
    matches = [a for a in state.artifacts.values() if a.source_run_id == gap_run_id]
    assert len(matches) == 1, matches
    return matches[0]


def _open_next_evaluation(fixture: _Remediated):
    runtime = fixture.runtime
    before = _load(runtime)
    projection = project_next_lifecycle_transition(before)
    assert not projection.diagnostics, projection.diagnostics
    candidate = projection.candidate
    assert candidate is not None and candidate.kind == "evaluate", projection
    decision = decide(before, candidate.transition_input, candidate.transition_context)
    assert decision.accepted, decision.refusal
    after = apply(before, decision)
    runtime.store.persist_runtime_state(after, runtime.cas_store)
    runtime = _reopen_runtime(runtime)
    reloaded = _load(runtime)
    assert reloaded == after
    new_ids = set(reloaded.closure_evaluations) - set(before.closure_evaluations)
    assert len(new_ids) == 1, new_ids
    record = reloaded.closure_evaluations[next(iter(new_ids))]
    return runtime, reloaded, record


def _receipt_key(state, run_id: str) -> str:
    keys = [
        completion.application_input_id
        for completion in state.runner_session_completions.values()
        if completion.run_id == run_id
    ]
    assert len(keys) == 1, keys
    assert keys[0] in state.receipts, keys[0]
    return keys[0]


def _rejected_receipt(state, run_id: str):
    key = _receipt_key(state, run_id)
    rejected = replace(
        state.receipts[key],
        accepted=False,
        refusal_reason="synthetic_hostile_rejection",
    )
    return replace(state, receipts={**state.receipts, key: rejected})


def _mutated(state, fixture: _Remediated, mutation: str):
    gap_run = state.runs[fixture.gap_run_id]
    returned = state.activations[gap_run.activation_id]
    if mutation == "unmutated":
        return state
    if mutation == "borrowed_existing_run":
        verdict = _gap_verdict(state, fixture.gap_run_id)
        forged = replace(verdict, source_run_id=fixture.remediation_run_id)
        artifacts = {**state.artifacts, verdict.artifact_id: forged}
        return replace(state, artifacts=artifacts)
    if mutation == "wrong_return_activation":
        forged_run = replace(
            gap_run, activation_id=fixture.evaluation.target_activation_id
        )
        return replace(state, runs={**state.runs, fixture.gap_run_id: forged_run})
    if mutation == "wrong_return_plan":
        plan_ref = replace(
            gap_run.run_ref.plan_ref, authority_fingerprint=FAKE_FINGERPRINT
        )
        run_ref = replace(gap_run.run_ref, plan_ref=plan_ref)
        forged_run = replace(gap_run, run_ref=run_ref)
        return replace(state, runs={**state.runs, fixture.gap_run_id: forged_run})
    if mutation == "wrong_return_work":
        forged_run = replace(gap_run, work_item_id=f"{gap_run.work_item_id}-foreign")
        return replace(state, runs={**state.runs, fixture.gap_run_id: forged_run})
    if mutation == "wrong_return_lineage":
        forged_activation = replace(returned, lineage_id="foreign")
        activations = {
            **state.activations,
            returned.activation_id: forged_activation,
        }
        return replace(state, activations=activations)
    if mutation == "forged_return_route":
        routes = tuple(
            replace(route, source_run_id=fixture.remediation_run_id)
            if route.target_activation_id == returned.activation_id
            else route
            for route in state.activation_routes
        )
        assert routes != state.activation_routes, "no return route to forge"
        return replace(state, activation_routes=routes)
    if mutation == "recovery_receipt_rejected":
        return _rejected_receipt(state, fixture.recovery_run_id)
    if mutation == "verdict_receipt_rejected":
        return _rejected_receipt(state, fixture.gap_run_id)
    if mutation == "foreign_evaluation_lineage":
        key = next(
            k
            for k, v in state.closure_evaluations.items()
            if v.record_id == fixture.evaluation.record_id
        )
        forged = replace(state.closure_evaluations[key], lineage_id="foreign")
        evaluations = {**state.closure_evaluations, key: forged}
        return replace(state, closure_evaluations=evaluations)
    raise AssertionError(mutation)


def test_returned_gap_remediation_promotes_next_evaluation_with_returned_prior_verdict(
    tmp_path,
):
    fixture = _returned_gap_remediated(tmp_path)
    runtime = fixture.runtime
    try:
        state = _load(runtime)
        gap_run = state.runs[fixture.gap_run_id]
        assert closure_evaluation_for_run(state, gap_run) == (
            fixture.evaluation,
            None,
        )
        target = state.closure_targets[fixture.evaluation.closure_target_id]
        readiness = assess_closure_readiness(
            state,
            lineage_id=target.lineage_id,
            plan_ref=target.selected_plan_ref,
            target_key=closure_target_key_for(target),
        )
        assert readiness.status == "settled", readiness

        snapshot, refusal = _snapshot(state, fixture)
        assert refusal is None, refusal
        assert snapshot is not None
        verdict = _gap_verdict(state, fixture.gap_run_id)
        assert snapshot["prior_verdict"]["artifact_id"] == verdict.artifact_id
        assert snapshot["prior_verdict"]["source_run_id"] == fixture.gap_run_id

        runtime, state, record = _open_next_evaluation(fixture)
        assert closure_evaluator_origin_matches(
            state, fixture.plan, record.target_activation_id
        )
        activation = state.activations[record.target_activation_id]
        work = state.work_items[activation.work_item_id]
        passed_payload = _closure_verdict_payload(
            work.payload["closure_evidence_snapshot"], marker="REVIEW_PASSED"
        )
        passed = run_bounded_execution_unit(
            runtime,
            activation_id=record.target_activation_id,
            local_config=_success_config("REVIEW_PASSED", passed_payload),
        )
        assert passed.code == "observation_accepted", passed
        final = _load(runtime)
        closed = final.closure_targets[fixture.evaluation.closure_target_id]
        assert closed.status == "closed"
        assert runtime_failure_applied(final, fixture.failure_run_id)
        runtime = _reopen_runtime(runtime)
        reloaded = _load(runtime)
        assert reloaded == final
        assert runtime_failure_applied(reloaded, fixture.failure_run_id)
        assert closure_evaluation_for_run(
            reloaded, reloaded.runs[fixture.gap_run_id]
        ) == (fixture.evaluation, None)
    finally:
        runtime.store.close()


def test_sealed_returned_prior_verdict_is_authenticated_for_the_next_evaluation(
    tmp_path,
):
    fixture = _returned_gap_remediated(tmp_path)
    runtime = fixture.runtime
    try:
        runtime, state, record = _open_next_evaluation(fixture)
        activation, refusal = closure_evaluation_origin_refusal(
            state, fixture.plan, record
        )
        assert refusal is None, refusal
        assert activation is not None
        assert activation.activation_id == record.target_activation_id

        gap_run = state.runs[fixture.gap_run_id]
        forged_run = replace(
            gap_run, activation_id=fixture.evaluation.target_activation_id
        )
        forged = replace(state, runs={**state.runs, fixture.gap_run_id: forged_run})
        _, origin_refusal = closure_evaluation_origin_refusal(
            forged, fixture.plan, record
        )
        assert origin_refusal is not None, "forged returned GAP origin was accepted"
    finally:
        runtime.store.close()


@pytest.mark.parametrize("mutation", MUTATIONS, ids=MUTATIONS)
def test_returned_prior_verdict_hostile_ownership_is_refused(tmp_path, mutation):
    fixture = _returned_gap_remediated(tmp_path)
    try:
        state = _mutated(_load(fixture.runtime), fixture, mutation)
        snapshot, refusal = _snapshot(state, fixture)
        if mutation == "unmutated":
            assert refusal is None, refusal
            assert snapshot is not None
        elif mutation == "foreign_evaluation_lineage":
            assert snapshot is None or snapshot["prior_verdict"] is None, (
                "foreign evaluation lineage did not block the prior verdict"
            )
        else:
            assert refusal is not None, "snapshot accepted a forged prior verdict"
    finally:
        fixture.runtime.store.close()
