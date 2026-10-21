"""Sealed closure-evaluator history authority (synthetic/offline, not native evidence).

The fixture is the synthetic portable variant from
``test_closure_evaluator_runtime_failure_authority`` with two BETA review artifacts
injected into the closure lineage between the open and evaluate transitions, so the
evaluator's sealed ``evidence_artifacts`` list is non-empty. Without the injection the
portable evaluator snapshot carries no evidence at all.

Two gaps are exposed by the hostile cases:

* ``closure_creator_audit_refusal`` inspects only the governance event and trace whose
  ``record_id`` is ``<transition_id>:governance`` / ``:trace``. A second accepted
  governance event or trace that shares the creator ``input_id`` under a different
  ``record_id`` is ignored, so the origin is wrongly authenticated.
* ``closure_evidence_anchor`` authenticates only ``root_contract`` and
  ``prior_verdict``. The sealed ``evidence_artifacts`` tuple is never checked, so
  emptying, removing, duplicating, reordering or changing envelope fields is wrongly
  authenticated.

Every hostile case must fail origin authentication and be refused by runtime-failure
authority with ``runtime_failure_authority_mismatch``. The control, the refused-replay
positive, and the historical post-evaluate positive keep the authority honest in the
other direction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

import pytest

from millrace.contracts.closure_evaluation_history import (
    closure_evaluator_origin_matches,
)
from millrace.contracts.ids import ActionId, QueueFamilyId
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_authority_refusal,
)
from millrace.contracts.state import WorkItem, WorkItemRef
from millrace.contracts.transition import artifact_payload_digest
from millrace.kernel.lifecycle import project_next_lifecycle_transition
from support import generic_lifecycle
from tests.kernel.test_closure_evaluator_runtime_failure_authority import (
    _evaluator_timeout,
    _portable_source,
    _timeout_decision,
)
from tests.operator.test_status_projection import _closure_verdict_payload

# case id -> (state field holding the audit rows, creator whose input id is duplicated)
_AUDIT_CASES = {
    "evaluate_governance_extra_accepted": ("governance_events", "evaluate"),
    "evaluate_trace_extra_accepted": ("traces", "evaluate"),
    "open_governance_extra_accepted": ("governance_events", "open"),
    "open_trace_extra_accepted": ("traces", "open"),
}


def _thaw(value: object) -> object:
    """Return a mutable copy of a frozen authority value (mappings become dicts)."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in value)
    return value


def _rows_for(rows, input_id):
    return tuple(row for row in rows if row.input_id == input_id)


def _edit_evaluator_payload(state, work_id, edit):
    work = state.work_items[work_id]
    payload = _thaw(work.payload)
    edit(payload)
    return replace(
        state,
        work_items={**state.work_items, work_id: replace(work, payload=payload)},
    )


def _inject_lineage_evidence(state, plan, fingerprint, *, suffix, summary):
    """Observe one BETA review artifact on a fresh lineage work item (no closure)."""
    behavior = plan.completion_behaviors[0]
    target = next(iter(state.closure_targets.values()))
    root = state.work_items["work-origin"]
    root_digest = artifact_payload_digest(root.payload)
    work = WorkItem(
        ref=WorkItemRef(f"work-{suffix}", root.ref.plan_ref, 0),
        queue_family_id=QueueFamilyId("joined_bundle"),
        payload={"kind": suffix},
        lineage_id=root.lineage_id,
        created_by_input_id=f"create-work-{suffix}",
    )
    artifact_payload = _closure_verdict_payload(
        {
            "closure_target_id": target.closure_target_id,
            "root_contract": {"payload_digest": root_digest},
            "freshness_anchor_digest": root_digest,
        },
        marker="REVIEW_PASSED",
    )
    artifact_payload["summary"] = summary
    activation = replace(
        state.activations["activation-origin"],
        activation_id=f"activation-{suffix}",
        work_item_id=work.ref.work_item_id,
        lineage_id=work.lineage_id,
        queue_family_id=work.queue_family_id,
        graph_node_id=behavior.target_graph_node_id,
        stage_kind_id=behavior.target_stage_kind_id,
        generation=0,
        created_by_input_id=work.created_by_input_id,
        claimed_by_run_id=None,
    )
    state = replace(
        state,
        work_items={**state.work_items, work.ref.work_item_id: work},
        activations={**state.activations, activation.activation_id: activation},
    )
    state = generic_lifecycle.claim_activation(
        state, activation_id=activation.activation_id, suffix=suffix
    )
    return generic_lifecycle.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=f"run-{suffix}",
        input_id=f"observe-{suffix}",
        marker="REVIEW_PASSED",
        artifact_payload=artifact_payload,
    )


@pytest.fixture
def sealed_state():
    """Origin, open, two BETA lineage artifacts, then evaluate (sealed evidence)."""
    state, plan, fingerprint = generic_lifecycle.closure_opened_state(
        _portable_source()
    )
    state = _inject_lineage_evidence(
        state, plan, fingerprint, suffix="evidence-a", summary="first lineage evidence"
    )
    state = _inject_lineage_evidence(
        state, plan, fingerprint, suffix="evidence-b", summary="second lineage evidence"
    )
    candidate = project_next_lifecycle_transition(state).candidate
    assert candidate is not None and candidate.kind == "evaluate"
    state = generic_lifecycle.apply_candidate(state, candidate)
    record = next(iter(state.closure_evaluations.values()))
    activation = state.activations[record.target_activation_id]
    return state, plan, fingerprint, record, activation


def _sealed_evidence(state, record):
    snapshot = state.work_items[record.target_work_item_id].payload[
        "closure_evidence_snapshot"
    ]
    return snapshot["evidence_artifacts"]


def _with_refused_replay_rows(state, input_id):
    """Append refused governance and trace rows that share the creator input id."""
    (governance,) = _rows_for(state.governance_events, input_id)
    (trace,) = _rows_for(state.traces, input_id)
    return replace(
        state,
        governance_events=(
            *state.governance_events,
            replace(
                governance,
                record_id=f"{governance.record_id}:replay",
                disposition="refused",
                refusal_reason="duplicate_input",
            ),
        ),
        traces=(
            *state.traces,
            replace(
                trace,
                record_id=f"{trace.record_id}:replay",
                disposition="refused",
                refusal_reason="duplicate_input",
            ),
        ),
    )


def _with_extra_accepted_row(state, record, case):
    """Append a second accepted governance or trace row under the creator input id."""
    field, creator = _AUDIT_CASES[case]
    if creator == "evaluate":
        input_id = record.created_by_input_id
    else:
        input_id = state.closure_targets[record.closure_target_id].opened_by_input_id
    rows = getattr(state, field)
    matches = _rows_for(rows, input_id)
    assert len(matches) == 1, f"baseline {field} rows for {input_id}: {len(matches)}"
    (row,) = matches
    assert row.disposition == "accepted"
    extra = replace(row, record_id=f"{row.record_id}:extra")
    return replace(state, **{field: (*rows, extra)})


def _entry(evidence, index, **changes):
    return (*evidence[:index], {**evidence[index], **changes}, *evidence[index + 1 :])


_EVIDENCE_CASES: dict[str, Callable[[tuple], tuple]] = {
    "sealed_evidence_emptied": lambda evidence: (),
    "sealed_evidence_first_removed": lambda evidence: evidence[1:],
    "sealed_evidence_first_duplicated": lambda evidence: (*evidence, evidence[0]),
    "sealed_evidence_reordered": lambda evidence: tuple(reversed(evidence)),
    "sealed_evidence_payload_changed": lambda evidence: _entry(
        evidence, 0, payload={**evidence[0]["payload"], "forged": "forged"}
    ),
    "sealed_evidence_payload_digest_changed": lambda evidence: _entry(
        evidence, 0, payload_digest="sha256:" + "e" * 64
    ),
    "sealed_evidence_source_work_item_changed": lambda evidence: _entry(
        evidence, 0, source_work_item_id="work-foreign"
    ),
    "sealed_evidence_schema_changed": lambda evidence: _entry(
        evidence, 0, artifact_schema_id="LifecycleSourceBundle"
    ),
    "sealed_evidence_source_action_changed": lambda evidence: _entry(
        evidence, 0, source_action_id="forged.action"
    ),
    "sealed_evidence_transition_changed": lambda evidence: _entry(
        evidence, 0, transition_id="transition-forged"
    ),
}


def _sealed_evidence_edit(transform):
    def edit(payload):
        snapshot = payload["closure_evidence_snapshot"]
        assert len(snapshot["evidence_artifacts"]) == 2
        snapshot["evidence_artifacts"] = transform(
            tuple(snapshot["evidence_artifacts"])
        )

    return edit


def test_sealed_fixture_carries_two_lineage_evidence_artifacts(sealed_state) -> None:
    # Precondition: the sealed evaluator snapshot must hold real BETA evidence.
    state, _, _, record, _ = sealed_state
    evidence = _sealed_evidence(state, record)
    assert len(evidence) == 2
    assert [item["source_work_item_id"] for item in evidence] == [
        "work-evidence-a",
        "work-evidence-b",
    ]
    assert all(
        item["artifact_schema_id"] == generic_lifecycle.BETA_REPORT_SCHEMA_ID
        for item in evidence
    )


def test_sealed_closure_origin_control_is_accepted(sealed_state) -> None:
    state, plan, _, _, activation = sealed_state
    state, completion = _evaluator_timeout(state, activation, "sealed-control")
    assert closure_evaluator_origin_matches(state, plan, activation.activation_id)
    command, decision = _timeout_decision(state, completion, "sealed-control")
    assert runtime_failure_authority_refusal(state, command) is None
    assert decision.accepted, decision.refusal


def test_refused_replay_audit_rows_remain_lawful(sealed_state) -> None:
    # Refused replays of the creator input id are lawful history and must not refuse.
    state, plan, _, record, activation = sealed_state
    state, completion = _evaluator_timeout(state, activation, "refused-replay")
    state = _with_refused_replay_rows(state, record.created_by_input_id)
    assert closure_evaluator_origin_matches(state, plan, activation.activation_id)
    command, decision = _timeout_decision(state, completion, "refused-replay")
    assert runtime_failure_authority_refusal(state, command) is None
    assert decision.accepted, decision.refusal


@pytest.mark.parametrize("case", tuple(_AUDIT_CASES))
def test_hostile_extra_accepted_audit_row_is_refused(sealed_state, case) -> None:
    state, plan, _, record, activation = sealed_state
    state, completion = _evaluator_timeout(state, activation, f"audit-{case}")
    assert closure_evaluator_origin_matches(state, plan, activation.activation_id)
    state = _with_extra_accepted_row(state, record, case)
    assert not closure_evaluator_origin_matches(state, plan, activation.activation_id)
    command, decision = _timeout_decision(state, completion, f"audit-{case}")
    assert (
        runtime_failure_authority_refusal(state, command)
        == "runtime_failure_authority_mismatch"
    )
    assert not decision.accepted
    assert not runtime_failure_applied(state, completion.run_id)


@pytest.mark.parametrize("case", tuple(_EVIDENCE_CASES))
def test_hostile_sealed_evidence_is_refused(sealed_state, case) -> None:
    state, plan, _, record, activation = sealed_state
    state, completion = _evaluator_timeout(state, activation, f"evidence-{case}")
    assert closure_evaluator_origin_matches(state, plan, activation.activation_id)
    state = _edit_evaluator_payload(
        state,
        record.target_work_item_id,
        _sealed_evidence_edit(_EVIDENCE_CASES[case]),
    )
    assert not closure_evaluator_origin_matches(state, plan, activation.activation_id)
    command, decision = _timeout_decision(state, completion, f"evidence-{case}")
    assert (
        runtime_failure_authority_refusal(state, command)
        == "runtime_failure_authority_mismatch"
    )
    assert not decision.accepted
    assert not runtime_failure_applied(state, completion.run_id)


def test_historical_sealed_evidence_origin_remains_authentic(sealed_state) -> None:
    state, plan, fingerprint, record, activation = sealed_state
    sealed_before = _sealed_evidence(state, record)
    sealed_ids = [item["artifact_id"] for item in sealed_before]
    state = _inject_lineage_evidence(
        state,
        plan,
        fingerprint,
        suffix="post-evaluate",
        summary="post-evaluate evidence",
    )
    post = next(
        item
        for item in state.artifacts.values()
        if item.work_item_id == "work-post-evaluate"
    )
    evaluate_transition_id = state.receipts[record.created_by_input_id].transition_id
    positions = {item.record_id: index for index, item in enumerate(state.transitions)}
    assert positions[post.transition_id] > positions[evaluate_transition_id]
    # Post-evaluate history is not sealed into the evaluator snapshot.
    assert [
        item["artifact_id"] for item in _sealed_evidence(state, record)
    ] == sealed_ids
    assert post.artifact_id not in sealed_ids
    assert closure_evaluator_origin_matches(state, plan, activation.activation_id)
    state, completion = _evaluator_timeout(state, activation, "historical")
    command, decision = _timeout_decision(state, completion, "historical")
    assert runtime_failure_authority_refusal(state, command) is None
    assert decision.accepted, decision.refusal


_SOURCE_AUTHORITY_CASES = (
    "source_receipt_rejected",
    "source_receipt_digest_changed",
    "source_transition_refused",
    "source_observation_removed",
    "source_audit_action_changed",
)


def _with_source_authority_drift(state, artifact, case):
    """Keep the artifact and sealed envelope; drift only its accepted source."""
    input_id = artifact.created_by_input_id
    receipt = state.receipts[input_id]
    if case == "source_receipt_rejected":
        return replace(
            state,
            receipts={**state.receipts, input_id: replace(receipt, accepted=False)},
        )
    if case == "source_receipt_digest_changed":
        changed = replace(
            receipt,
            receipt_ref=replace(
                receipt.receipt_ref, input_payload_digest="sha256:" + "0" * 64
            ),
        )
        return replace(state, receipts={**state.receipts, input_id: changed})
    if case == "source_transition_refused":
        return replace(
            state,
            transitions=tuple(
                replace(item, accepted=False) if item.input_id == input_id else item
                for item in state.transitions
            ),
        )
    if case == "source_audit_action_changed":
        forged = ActionId("forged.action")
        return replace(
            state,
            governance_events=tuple(
                replace(item, action_id=forged) if item.input_id == input_id else item
                for item in state.governance_events
            ),
            traces=tuple(
                replace(item, action_id=forged) if item.input_id == input_id else item
                for item in state.traces
            ),
        )
    return replace(
        state,
        runner_observations={
            key: value
            for key, value in state.runner_observations.items()
            if value.created_by_input_id != input_id
        },
    )


@pytest.mark.parametrize("case", _SOURCE_AUTHORITY_CASES)
def test_captured_evidence_source_authority_drift_is_refused(
    sealed_state, case
) -> None:
    state, plan, _, record, activation = sealed_state
    state, completion = _evaluator_timeout(state, activation, f"source-{case}")
    artifact = state.artifacts[_sealed_evidence(state, record)[0]["artifact_id"]]
    assert closure_evaluator_origin_matches(state, plan, activation.activation_id)
    state = _with_source_authority_drift(state, artifact, case)
    assert not closure_evaluator_origin_matches(state, plan, activation.activation_id)
    command, decision = _timeout_decision(state, completion, f"source-{case}")
    assert (
        runtime_failure_authority_refusal(state, command)
        == "runtime_failure_authority_mismatch"
    )
    assert not decision.accepted


def test_rewritten_artifact_payload_must_be_the_observed_payload(sealed_state) -> None:
    """Artifact payload, digest and sealed envelope rewritten together still refuse."""
    state, plan, _, record, activation = sealed_state
    state, completion = _evaluator_timeout(state, activation, "source-payload")
    artifact = state.artifacts[_sealed_evidence(state, record)[0]["artifact_id"]]
    payload = {**_thaw(artifact.payload), "summary": "rewritten evidence"}
    rewritten = replace(
        artifact, payload=payload, payload_digest=artifact_payload_digest(payload)
    )
    state = replace(
        state, artifacts={**state.artifacts, artifact.artifact_id: rewritten}
    )
    state = _edit_evaluator_payload(
        state,
        record.target_work_item_id,
        _sealed_evidence_edit(
            lambda evidence: _entry(
                evidence,
                0,
                payload=payload,
                payload_digest=rewritten.payload_digest,
            )
        ),
    )
    assert not closure_evaluator_origin_matches(state, plan, activation.activation_id)
    command, decision = _timeout_decision(state, completion, "source-payload")
    assert (
        runtime_failure_authority_refusal(state, command)
        == "runtime_failure_authority_mismatch"
    )
    assert not decision.accepted
