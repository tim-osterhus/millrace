"""Closure-evaluator origin provenance (synthetic/offline, never native evidence).

Each hostile case starts from a genuine, unmutated selected-evaluator timeout that
the authority accepts, then corrupts exactly one durable creator, target or
snapshot relation. The corrupted state must not authenticate the evaluator as a
closure origin, and runtime-failure authority must refuse it with
``runtime_failure_authority_mismatch``. The control and the historical
post-close positive keep the authority honest in the other direction.

The evaluator fixture is the synthetic portable variant from
``test_closure_evaluator_runtime_failure_authority``; nothing here is native
evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from millrace.contracts.closure_evaluation_history import (
    closure_evaluator_origin_matches,
)
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_authority_refusal,
)
from millrace.contracts.transition import artifact_payload_digest
from millrace.kernel._closure_lifecycle import closure_target_progress
from millrace.operator.dispatch import list_ready_dispatch_candidates
from support import generic_lifecycle
from tests.kernel.test_closure_evaluator_runtime_failure_authority import (
    _cli_failed_evaluator,
    _evaluator_timeout,
    _portable_source,
    _timeout_decision,
)

# (state field, creator, row mode) for the creator-row hostile cases.
_ROW_CASES = {
    "evaluate_governance_missing": ("governance_events", "evaluate", "missing"),
    "evaluate_governance_duplicated": ("governance_events", "evaluate", "duplicated"),
    "evaluate_governance_foreign_work": ("governance_events", "evaluate", "foreign"),
    "evaluate_trace_missing": ("traces", "evaluate", "missing"),
    "evaluate_trace_duplicated": ("traces", "evaluate", "duplicated"),
    "evaluate_trace_foreign_work": ("traces", "evaluate", "foreign"),
    "open_governance_missing": ("governance_events", "open", "missing"),
    "open_governance_foreign_work": ("governance_events", "open", "foreign"),
    "open_trace_missing": ("traces", "open", "missing"),
    "open_trace_foreign_work": ("traces", "open", "foreign"),
}

HOSTILE_CASES = (
    "target_closure_root_work_item_id",
    "evaluate_creator_transition_duplicated",
    "evaluator_snapshot_emptied",
    "evaluator_snapshot_target_id",
    "evaluator_root_contract_redigested",
    *_ROW_CASES,
    "open_creator_transition_duplicated",
    "open_creator_receipt_removed",
)


@pytest.fixture
def evaluator_state():
    source = _portable_source()
    generic_lifecycle.compile_lifecycle(source)
    state, plan, fingerprint = generic_lifecycle.closure_evaluation_state(source)
    record = next(iter(state.closure_evaluations.values()))
    activation = state.activations[record.target_activation_id]
    return state, plan, fingerprint, record, activation


def _thaw(value: object) -> object:
    """Return a mutable copy of a frozen authority value (mappings become dicts)."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in value)
    return value


def _rows_for(rows, input_id):
    return tuple(row for row in rows if row.input_id == input_id)


def _creator_rows(state, field, input_id, mode):
    rows = getattr(state, field)
    matches = _rows_for(rows, input_id)
    assert len(matches) == 1, f"baseline {field} rows for {input_id}: {len(matches)}"
    (row,) = matches
    if mode == "missing":
        updated = tuple(item for item in rows if item is not row)
        expected = 0
    elif mode == "duplicated":
        updated = (*rows, row)
        expected = 2
    else:
        updated = tuple(
            replace(item, work_item_id="foreign") if item is row else item
            for item in rows
        )
        expected = 1
    survivors = _rows_for(updated, input_id)
    assert len(survivors) == expected
    if mode == "foreign":
        assert [item.work_item_id for item in survivors] == ["foreign"]
    return replace(state, **{field: updated})


def _duplicated_transition(state, input_id):
    matches = _rows_for(state.transitions, input_id)
    assert len(matches) == 1, f"baseline transitions for {input_id}: {len(matches)}"
    return replace(state, transitions=(*state.transitions, matches[0]))


def _removed_receipt(state, input_id):
    assert input_id in state.receipts
    return replace(
        state,
        receipts={
            key: value for key, value in state.receipts.items() if key != input_id
        },
    )


def _edit_evaluator_payload(state, work_id, edit):
    work = state.work_items[work_id]
    payload = _thaw(work.payload)
    edit(payload)
    return replace(
        state,
        work_items={**state.work_items, work_id: replace(work, payload=payload)},
    )


def _hostile_origin_mutation(state, record, case):
    target = state.closure_targets[record.closure_target_id]
    work_id = record.target_work_item_id
    evaluate_input = record.created_by_input_id
    open_input = target.opened_by_input_id

    if case == "target_closure_root_work_item_id":
        assert target.closure_root_work_item_id == "work-origin"
        retargeted = replace(target, closure_root_work_item_id="work-foreign-root")
        return replace(
            state,
            closure_targets={
                **state.closure_targets,
                target.closure_target_id: retargeted,
            },
        )
    if case == "evaluate_creator_transition_duplicated":
        return _duplicated_transition(state, evaluate_input)
    if case == "evaluator_snapshot_emptied":

        def empty_snapshot(payload):
            assert payload["closure_evidence_snapshot"]["record_kind"]
            payload["closure_evidence_snapshot"] = {}

        return _edit_evaluator_payload(state, work_id, empty_snapshot)
    if case == "evaluator_snapshot_target_id":

        def foreign_snapshot_target(payload):
            snapshot = payload["closure_evidence_snapshot"]
            assert snapshot["closure_target_id"] == record.closure_target_id
            snapshot["closure_target_id"] = "closure-target:foreign"

        return _edit_evaluator_payload(state, work_id, foreign_snapshot_target)
    if case == "evaluator_root_contract_redigested":

        def redigested_root(payload):
            contract = payload["closure_evidence_snapshot"]["root_contract"]
            assert contract["payload_digest"] == artifact_payload_digest(
                contract["payload"]
            )
            contract["payload"] = {
                **contract["payload"],
                "forged_root_field": "forged",
            }
            contract["payload_digest"] = artifact_payload_digest(contract["payload"])

        return _edit_evaluator_payload(state, work_id, redigested_root)
    if case in _ROW_CASES:
        field, creator, mode = _ROW_CASES[case]
        input_id = evaluate_input if creator == "evaluate" else open_input
        return _creator_rows(state, field, input_id, mode)
    if case == "open_creator_transition_duplicated":
        return _duplicated_transition(state, open_input)
    if case == "open_creator_receipt_removed":
        return _removed_receipt(state, open_input)
    raise AssertionError(case)


def test_unmutated_closure_origin_control_is_accepted(evaluator_state) -> None:
    state, plan, _, _, activation = evaluator_state
    state, completion = _evaluator_timeout(state, activation, "origin-control")
    assert closure_evaluator_origin_matches(state, plan, activation.activation_id)
    command, decision = _timeout_decision(state, completion, "origin-control")
    assert runtime_failure_authority_refusal(state, command) is None
    assert decision.accepted, decision.refusal


@pytest.mark.parametrize("case", HOSTILE_CASES)
def test_hostile_closure_origin_provenance_is_refused(evaluator_state, case) -> None:
    state, plan, _, record, activation = evaluator_state
    state, completion = _evaluator_timeout(state, activation, f"origin-{case}")
    assert closure_evaluator_origin_matches(state, plan, activation.activation_id)
    state = _hostile_origin_mutation(state, record, case)
    assert not closure_evaluator_origin_matches(state, plan, activation.activation_id)
    command, decision = _timeout_decision(state, completion, f"origin-{case}")
    assert (
        runtime_failure_authority_refusal(state, command)
        == "runtime_failure_authority_mismatch"
    )
    assert not decision.accepted
    assert not runtime_failure_applied(state, completion.run_id)


def test_historical_closed_evaluator_origin_remains_authentic(tmp_path) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from tests.cli.test_cli_bounded_execution_unit import _reopen_runtime
    from tests.cli.test_cli_candidate_failure_recovery import _success_config
    from tests.operator.test_status_projection import _closure_verdict_payload

    runtime, plan, evaluation, first, _load = _cli_failed_evaluator(tmp_path)
    assert first.code == "runtime_failure_recovery_applied", first
    recovery = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    assert run_bounded_execution_unit(
        runtime,
        activation_id=recovery.activation_id,
        local_config=_success_config("QA_RECOVERED", None),
    ).accepted
    runtime = _reopen_runtime(runtime)
    returned = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    work = _load(runtime).work_items[returned.work_item_id]
    payload = _closure_verdict_payload(
        work.payload["closure_evidence_snapshot"], marker="REVIEW_PASSED"
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id=returned.activation_id,
        local_config=_success_config("REVIEW_PASSED", payload),
    )
    assert result.code == "observation_accepted", result
    closed = _load(runtime)
    target = closed.closure_targets[evaluation.closure_target_id]
    assert target.status == "closed"
    assert closure_evaluator_origin_matches(
        closed, plan, evaluation.target_activation_id
    )
    assert runtime_failure_applied(closed, first.run_id)
    assert (
        closure_target_progress(
            closed, target=target, behavior=plan.completion_behaviors[0]
        ).status
        == "closed"
    )
    reopened = _reopen_runtime(runtime)
    assert _load(reopened) == closed
    reopened.close()
