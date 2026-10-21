"""Kernel acceptance for closure request payload overflow.

Every case drives real kernel transitions through ``tests/support/closure_overflow``.
Hostile cases mutate durable state with ``dataclasses.replace`` and then ask the
kernel to decide the already-projected evaluate candidate. No durable row is
inserted by hand.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from functools import cache
from typing import Any, TypeVar

import pytest

from millrace.contracts.closure_evaluation_history import (
    closure_artifact_envelope,
    closure_evaluation_origin_refusal,
)
from millrace.contracts.closure_request_overflow import (
    OVERFLOW_KEY,
    SNAPSHOT_KEY,
    closure_capacity_blocked,
    closure_capacity_blocks,
    parse_closure_capacity_block,
)
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import EnqueueWork, TransitionDecision
from millrace.kernel import apply, decide
from millrace.kernel.lifecycle import (
    ProjectedLifecycleCandidate,
    project_next_lifecycle_transition,
)
from support import closure_overflow as co
from support import generic_lifecycle as gl

EVALUATE_INPUT_KIND = "workflow.evaluate_completion_behavior"
LIMIT_REFUSAL = "closure_request_payload_limit_exceeded"
T = TypeVar("T")


@cache
def _inline_request_bytes() -> int:
    """Canonical work-payload bytes of the request when the inline limit is generous.

    The compiled plan fingerprint is fixed-length hex, so the measured size is the
    same size a plan with the exact limit would produce.
    """
    fixture = co.evaluated_state(
        co.overflow_source(request_limit=65536, overflow=False)
    )
    return co.work_payload_canonical_bytes(co.closure_request_item(fixture.state))


def _decide_evaluate(fixture: co.OverflowState) -> TransitionDecision:
    candidate = co.evaluate_candidate(fixture)
    return decide(
        fixture.state, candidate.transition_input, candidate.transition_context
    )


def _single(values: Iterable[T]) -> T:
    items = list(values)
    assert len(items) == 1, len(items)
    return items[0]


def _single_entry(mapping: Mapping[str, T]) -> tuple[str, T]:
    key, value = _single(mapping.items())
    return key, value


def _with_entry(mapping: Mapping[str, T], key: str, value: T) -> dict[str, T]:
    updated = dict(mapping)
    updated[key] = value
    return updated


def _evidence_identity(snapshot: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [
        (str(artifact["artifact_id"]), str(artifact["payload"]["summary"]))
        for artifact in snapshot["evidence_artifacts"]
    ]


def _beta_artifact_entry(state: RuntimeState) -> tuple[str, Any]:
    matches = [
        (key, artifact)
        for key, artifact in state.artifacts.items()
        if str(artifact.schema_id) == gl.BETA_REPORT_SCHEMA_ID
    ]
    assert len(matches) == 3, len(matches)
    return matches[0]


def _unclaimed_activation(state: RuntimeState, stage_kind: str) -> Any:
    return _single(
        activation
        for activation in state.activations.values()
        if str(activation.stage_kind_id) == stage_kind
        and activation.claimed_by_run_id is None
    )


def _mutate_artifact_payload(state: RuntimeState) -> RuntimeState:
    key, artifact = _beta_artifact_entry(state)
    payload = dict(artifact.payload)
    payload["summary"] = "tampered summary"
    return replace(
        state,
        artifacts=_with_entry(state.artifacts, key, replace(artifact, payload=payload)),
    )


def _mutate_artifact_payload_with_matching_digest(state: RuntimeState) -> RuntimeState:
    key, artifact = _beta_artifact_entry(state)
    payload = dict(artifact.payload)
    payload["summary"] = "tampered summary"
    forged = replace(
        artifact,
        payload=payload,
        payload_digest="sha256:" + "0" * 64,
    )
    return replace(state, artifacts=_with_entry(state.artifacts, key, forged))


def _mutate_target_plan(state: RuntimeState) -> RuntimeState:
    key, target = _single_entry(state.closure_targets)
    ref = target.selected_plan_ref
    fingerprint = ref.authority_fingerprint
    other = fingerprint[:-1] + ("0" if fingerprint[-1] != "0" else "1")
    swapped = replace(
        target, selected_plan_ref=replace(ref, authority_fingerprint=other)
    )
    return replace(
        state,
        closure_targets=_with_entry(state.closure_targets, key, swapped),
    )


def _mutate_target_lineage(state: RuntimeState) -> RuntimeState:
    key, target = _single_entry(state.closure_targets)
    return replace(
        state,
        closure_targets=_with_entry(
            state.closure_targets, key, replace(target, lineage_id="lineage-other")
        ),
    )


def _mutate_target_root_source(state: RuntimeState) -> RuntimeState:
    key, target = _single_entry(state.closure_targets)
    return replace(
        state,
        closure_targets=_with_entry(
            state.closure_targets, key, replace(target, root_source_id="other-root")
        ),
    )


def _mutate_root_payload(state: RuntimeState) -> RuntimeState:
    root = state.work_items["work-origin"]
    payload = dict(root.payload)
    payload["bundle_id"] = "bundle-tampered"
    return replace(
        state,
        work_items=_with_entry(
            state.work_items, "work-origin", replace(root, payload=payload)
        ),
    )


def _mutate_root_lineage(state: RuntimeState) -> RuntimeState:
    root = state.work_items["work-origin"]
    return replace(
        state,
        work_items=_with_entry(
            state.work_items,
            "work-origin",
            replace(root, lineage_id="lineage-other"),
        ),
    )


HOSTILE_MUTATIONS: tuple[Any, ...] = (
    pytest.param(
        _mutate_artifact_payload,
        "closure_evidence_artifact_provenance_mismatch",
        id="alter-artifact-payload",
    ),
    pytest.param(
        _mutate_artifact_payload_with_matching_digest,
        "closure_evidence_artifact_provenance_mismatch",
        id="alter-artifact-digest-consistent",
    ),
    pytest.param(
        _mutate_target_plan,
        "selected_plan_ref_mismatch",
        id="swap-target-plan",
    ),
    pytest.param(
        _mutate_target_lineage,
        "invalid_closure_evidence_window",
        id="swap-target-lineage",
    ),
    pytest.param(
        _mutate_target_root_source,
        "closure_target_identity_mismatch",
        id="swap-target-root-source",
    ),
    pytest.param(
        _mutate_root_payload,
        "invalid_closure_root_creator",
        id="alter-root-payload",
    ),
    pytest.param(
        _mutate_root_lineage,
        "missing_closure_root_source",
        id="swap-root-lineage",
    ),
)


@pytest.mark.parametrize("count, summary_bytes", [(1, 200), (2, 1000)])
@pytest.mark.parametrize("overflow", [False, True], ids=["no-overflow", "overflow"])
def test_inline_request_below_limit_stays_inline(
    count: int, summary_bytes: int, overflow: bool
) -> None:
    fixture = co.opened_state(
        co.overflow_source(overflow=overflow),
        count=count,
        summary_bytes=summary_bytes,
    )
    decision = _decide_evaluate(fixture)
    assert decision.accepted is True, decision.refusal
    applied = apply(fixture.state, decision)
    item = co.closure_request_item(applied)
    assert OVERFLOW_KEY not in item.payload
    assert SNAPSHOT_KEY in item.payload
    assert co.work_payload_canonical_bytes(item) <= co.REQUEST_LIMIT


def test_inline_request_at_exact_limit_is_accepted_without_overflow() -> None:
    limit = _inline_request_bytes()
    fixture = co.opened_state(co.overflow_source(request_limit=limit, overflow=False))
    decision = _decide_evaluate(fixture)
    assert decision.accepted is True, decision.refusal
    applied = apply(fixture.state, decision)
    item = co.closure_request_item(applied)
    assert OVERFLOW_KEY not in item.payload
    assert co.work_payload_canonical_bytes(item) == limit


def test_inline_request_one_byte_over_limit_without_overflow_is_refused() -> None:
    limit = _inline_request_bytes()
    fixture = co.opened_state(
        co.overflow_source(request_limit=limit - 1, overflow=False)
    )
    decision = _decide_evaluate(fixture)
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == LIMIT_REFUSAL


CAPACITY_MEASUREMENT_KEYS = frozenset(
    {
        "request_bytes",
        "request_payload_byte_limit",
        "evidence_items",
        "evidence_item_limit",
        "snapshot_bytes",
        "max_snapshot_bytes",
        "page_count",
        "max_page_count",
        "page_byte_limit",
    }
)


def _assert_durable_capacity_block(
    fixture: co.OverflowState,
    candidate: ProjectedLifecycleCandidate,
    *,
    reason: str,
    measurements: Mapping[str, object],
) -> RuntimeState:
    """Refuse an over-capacity evaluate candidate and prove the block is durable.

    Returns the durable state after the first refusal so a caller can add evidence.
    """
    input_id = candidate.transition_input.input_id
    decision = decide(
        fixture.state, candidate.transition_input, candidate.transition_context
    )
    assert decision.accepted is False, "over-capacity evaluation was accepted"
    assert decision.disposition == "refused"
    assert decision.refusal is not None
    assert decision.refusal.reason == reason

    block = parse_closure_capacity_block(decision.refusal.detail)
    assert block is not None, decision.refusal.detail
    assert block["reason"] == reason
    assert block["record_kind"] == "closure_capacity_block"
    assert block["schema_version"] == 1
    assert block["operator_required"] is True
    recorded = block["measurements"]
    assert isinstance(recorded, Mapping)
    assert set(recorded) <= CAPACITY_MEASUREMENT_KEYS, sorted(recorded)
    for key, value in measurements.items():
        assert recorded[key] == value, (key, dict(recorded))

    durable = apply(fixture.state, decision)
    assert len(durable.refusals) == len(fixture.state.refusals) + 1
    stored = durable.refusals[-1]
    assert stored.reason == reason
    assert stored.input_kind == EVALUATE_INPUT_KIND
    assert stored.input_id == input_id
    assert len(durable.closure_evaluations) == len(fixture.state.closure_evaluations)
    assert len(durable.work_items) == len(fixture.state.work_items)

    listed = [
        entry
        for entry in closure_capacity_blocks(durable)
        if entry.input_id == input_id
    ]
    assert len(listed) == 1, listed
    assert listed[0].reason == reason
    assert listed[0].closure_target_id == block["closure_target_id"]
    assert listed[0].detail_status == "unverified"
    assert listed[0].measurements is None
    assert listed[0].selected_capacity_limits is not None
    assert closure_capacity_blocked(durable, input_id)

    # Over capacity, the lifecycle projection stops proposing this evaluation.
    assert project_next_lifecycle_transition(durable).candidate is None

    # Replaying the identical input returns the same refusal and writes nothing new.
    replay = decide(durable, candidate.transition_input, candidate.transition_context)
    assert replay.accepted is False
    assert replay.refusal is not None
    assert replay.refusal.reason == reason
    assert replay.receipt_ref == decision.receipt_ref
    assert apply(durable, replay) == durable
    return durable


def test_above_limit_without_overflow_is_refused_durably_without_exception() -> None:
    fixture = co.opened_state(co.overflow_source(overflow=False))
    candidate = co.evaluate_candidate(fixture)
    _assert_durable_capacity_block(
        fixture,
        candidate,
        reason=LIMIT_REFUSAL,
        measurements={
            "request_bytes": _inline_request_bytes(),
            "request_payload_byte_limit": co.REQUEST_LIMIT,
            "evidence_items": 3,
            "evidence_item_limit": 64,
        },
    )


def test_evidence_items_over_item_limit_are_capacity_blocked() -> None:
    source = co.overflow_source(overflow=False)
    source["completion_behaviors"][0]["evidence_item_limit"] = 2
    fixture = co.opened_state(source, count=3, summary_bytes=200)
    candidate = co.evaluate_candidate(fixture)
    _assert_durable_capacity_block(
        fixture,
        candidate,
        reason="closure_evidence_item_limit_exceeded",
        measurements={
            "request_bytes": 5206,
            "request_payload_byte_limit": co.REQUEST_LIMIT,
            "evidence_items": 3,
            "evidence_item_limit": 2,
        },
    )


def test_snapshot_over_max_snapshot_bytes_is_capacity_blocked() -> None:
    fixture = co.opened_state(
        co.overflow_source(max_snapshot_bytes=9000), count=3, summary_bytes=4000
    )
    candidate = co.evaluate_candidate(fixture)
    _assert_durable_capacity_block(
        fixture,
        candidate,
        reason="closure_request_overflow_snapshot_limit_exceeded",
        measurements={
            "snapshot_bytes": 16076,
            "max_snapshot_bytes": 9000,
            "page_count": 12,
            "max_page_count": co.MAX_PAGE_COUNT,
            "page_byte_limit": co.PAGE_BYTE_LIMIT,
        },
    )


def test_page_count_over_max_page_count_is_capacity_blocked() -> None:
    fixture = co.opened_state(
        co.overflow_source(max_page_count=2), count=3, summary_bytes=4000
    )
    candidate = co.evaluate_candidate(fixture)
    _assert_durable_capacity_block(
        fixture,
        candidate,
        reason="closure_request_overflow_page_limit_exceeded",
        measurements={
            "page_count": 12,
            "max_page_count": 2,
            "page_byte_limit": co.PAGE_BYTE_LIMIT,
        },
    )


def test_projection_over_request_limit_is_capacity_blocked() -> None:
    # Roughly 0.6 s: the projection index alone is about 1051 pages.
    fixture = co.opened_state(
        co.overflow_source(
            request_limit=4096,
            max_snapshot_bytes=8_000_000,
            max_page_count=16384,
        ),
        count=8,
        summary_bytes=500_000,
    )
    candidate = co.evaluate_candidate(fixture)
    _assert_durable_capacity_block(
        fixture,
        candidate,
        reason="closure_request_overflow_projection_limit_exceeded",
        measurements={
            "request_payload_byte_limit": 4096,
            "snapshot_bytes": 4009791,
            "page_count": 1051,
            "max_page_count": 16384,
            "max_snapshot_bytes": 8_000_000,
            "page_byte_limit": co.PAGE_BYTE_LIMIT,
        },
    )


def test_new_legal_evidence_changes_anchor_and_is_capacity_blocked_again() -> None:
    source = co.overflow_source(overflow=False)
    source["completion_behaviors"][0]["evidence_artifact_schema_ids"] = [
        gl.BETA_REPORT_SCHEMA_ID,
        gl.ALPHA_REPORT_SCHEMA_ID,
    ]
    fixture = co.evidence_state(source, count=1, summary_bytes=200)
    fixture = co.advance(fixture, "open")
    first = co.evaluate_candidate(fixture)
    fixture = co.advance(fixture, "evaluate")
    fixture = co.observe_evaluator(fixture, marker=co.GAP_MARKER, suffix="gap-1")
    assert project_next_lifecycle_transition(fixture.state).candidate is None

    # Only evidence recorded after the GAP verdict counts, so this new alpha
    # artifact changes the anchor and proposes a fresh candidate. It is too large
    # for the inline request, so the new candidate is capacity-blocked as well.
    activation = _unclaimed_activation(fixture.state, "alpha_stage")
    claimed = gl.claim_activation(
        fixture.state, activation_id=activation.activation_id, suffix="alpha-big"
    )
    payload = dict(gl.report_payload("alpha"))
    payload["verdict"] = "A" * 9000
    observed = gl.apply_observation(
        claimed,
        plan=fixture.plan,
        fingerprint=fixture.fingerprint,
        run_id="run-alpha-big",
        input_id="observe-alpha-big",
        marker="ALPHA_READY",
        artifact_payload=payload,
    )
    fixture = replace(fixture, state=observed)
    second = co.evaluate_candidate(fixture)
    assert second.transition_input.input_id != first.transition_input.input_id
    _assert_durable_capacity_block(
        fixture,
        second,
        reason=LIMIT_REFUSAL,
        measurements={
            "request_payload_byte_limit": co.REQUEST_LIMIT,
            "evidence_items": 1,
            "evidence_item_limit": 64,
        },
    )


def test_above_limit_with_overflow_is_accepted_with_full_durable_snapshot() -> None:
    fixture = co.evaluated_state()
    item = co.closure_request_item(fixture.state)
    snapshot = co.durable_snapshot(fixture.state)

    assert OVERFLOW_KEY not in item.payload
    assert co.work_payload_canonical_bytes(item) > co.REQUEST_LIMIT
    assert len(snapshot["evidence_artifacts"]) == 3
    assert co.snapshot_canonical_bytes(snapshot) > co.REQUEST_LIMIT

    reference = co.durable_snapshot(
        co.evaluated_state(
            co.overflow_source(request_limit=65536, overflow=False)
        ).state
    )
    overflow_anchors = co.anchors(snapshot)
    inline_anchors = co.anchors(reference)
    assert overflow_anchors.pop("closure_target_id") is not None
    assert inline_anchors.pop("closure_target_id") is not None
    assert overflow_anchors == inline_anchors
    # Evidence bodies embed the plan-derived closure_target_id, so compare the
    # artifact identities and the evidence text rather than whole payload digests.
    assert _evidence_identity(snapshot) == _evidence_identity(reference)
    for _artifact_id, summary in _evidence_identity(snapshot):
        assert len(summary) >= 4000

    target_id = snapshot["closure_target_id"]
    evaluation = _single(fixture.state.closure_evaluations.values())
    assert evaluation.closure_target_id == target_id
    assert target_id in {
        target.closure_target_id for target in fixture.state.closure_targets.values()
    }


def test_identical_evaluate_replay_is_idempotent() -> None:
    pre = co.opened_state()
    candidate = co.evaluate_candidate(pre)
    first = decide(pre.state, candidate.transition_input, candidate.transition_context)
    assert first.accepted is True, first.refusal
    post = apply(pre.state, first)

    replay = decide(post, candidate.transition_input, candidate.transition_context)
    assert replay.accepted is True, replay.refusal
    assert replay.disposition == "replayed"
    assert replay.receipt_ref == first.receipt_ref
    assert apply(post, replay) == post
    assert len(post.closure_evaluations) == 1
    assert len(co.closure_request_items(post)) == 1


def test_evaluation_after_gap_with_new_evidence_carries_gap_prior_verdict() -> None:
    source = co.overflow_source()
    source["completion_behaviors"][0]["evidence_artifact_schema_ids"] = [
        gl.BETA_REPORT_SCHEMA_ID,
        gl.ALPHA_REPORT_SCHEMA_ID,
    ]
    fixture = co.evaluated_state(source)
    first_request = co.closure_request_item(fixture.state)

    fixture = co.observe_evaluator(fixture, marker=co.GAP_MARKER, suffix="gap-1")
    gap_verdicts = [
        artifact
        for artifact in fixture.state.artifacts.values()
        if artifact.work_item_id == first_request.ref.work_item_id
    ]
    gap_verdict = _single(gap_verdicts)

    activation = _unclaimed_activation(fixture.state, "alpha_stage")
    claimed = gl.claim_activation(
        fixture.state, activation_id=activation.activation_id, suffix="alpha-gap"
    )
    claimed = gl.apply_observation(
        claimed,
        plan=fixture.plan,
        fingerprint=fixture.fingerprint,
        run_id="run-alpha-gap",
        input_id="observe-alpha-gap",
        marker="ALPHA_READY",
        artifact_payload=gl.report_payload("alpha"),
    )
    fixture = replace(fixture, state=claimed)
    # The target is still open after GAP, so the next projected step is evaluate.
    fixture = co.advance(fixture, "evaluate")

    snapshot = co.durable_snapshot(fixture.state)
    evidence = snapshot["evidence_artifacts"]
    assert len(evidence) == 1
    assert str(evidence[0]["artifact_schema_id"]) == gl.ALPHA_REPORT_SCHEMA_ID
    prior = snapshot["prior_verdict"]
    assert prior is not None
    assert prior["artifact_id"] == gap_verdict.artifact_id
    assert snapshot["freshness_anchor_digest"] == prior["payload_digest"]


@pytest.mark.parametrize(("mutate", "reason"), HOSTILE_MUTATIONS)
def test_hostile_durable_mutation_is_refused_without_crash(
    mutate: Callable[[RuntimeState], RuntimeState], reason: str
) -> None:
    pre = co.opened_state()
    candidate = co.evaluate_candidate(pre)
    hostile = mutate(pre.state)

    decision = decide(hostile, candidate.transition_input, candidate.transition_context)
    assert decision.accepted is False, "hostile durable mutation was accepted"
    assert decision.refusal is not None
    assert decision.refusal.reason == reason

    applied = apply(hostile, decision)
    assert len(applied.refusals) == len(hostile.refusals) + 1
    assert len(applied.closure_evaluations) == len(hostile.closure_evaluations)
    assert len(applied.work_items) == len(hostile.work_items)


@pytest.mark.parametrize("request_limit", [8192, 65536], ids=["overflow", "inline"])
def test_deleted_accepted_evidence_cannot_be_hidden_from_new_capture(
    request_limit: int,
) -> None:
    fixture = co.opened_state(co.overflow_source(request_limit=request_limit))
    candidate = co.evaluate_candidate(fixture)
    artifact_id, _artifact = _beta_artifact_entry(fixture.state)
    corrupted = replace(
        fixture.state,
        artifacts={
            key: value
            for key, value in fixture.state.artifacts.items()
            if key != artifact_id
        },
    )
    decision = co.decide_candidate(corrupted, candidate)
    assert not decision.accepted
    assert decision.refusal is not None
    assert decision.refusal.reason == "closure_evidence_artifact_provenance_mismatch"


@pytest.mark.parametrize("request_limit", [8192, 65536], ids=["overflow", "inline"])
@pytest.mark.parametrize("keep_canonical", [False, True], ids=["replace", "add"])
@pytest.mark.parametrize("copy_identity", [False, True], ids=["new-id", "copied-id"])
def test_copied_artifact_alias_cannot_enter_new_capture(
    request_limit: int, keep_canonical: bool, copy_identity: bool
) -> None:
    fixture = co.opened_state(co.overflow_source(request_limit=request_limit))
    candidate = co.evaluate_candidate(fixture)
    artifact_id, artifact = _beta_artifact_entry(fixture.state)
    alias_key = "stateful-shadow"
    alias = replace(
        artifact, artifact_id=artifact_id if copy_identity else alias_key
    )
    artifacts = dict(fixture.state.artifacts)
    if not keep_canonical:
        del artifacts[artifact_id]
    artifacts[alias_key] = alias
    corrupted = replace(fixture.state, artifacts=artifacts)

    decision = co.decide_candidate(corrupted, candidate)
    assert not decision.accepted
    assert decision.refusal is not None
    assert decision.refusal.reason == "closure_evidence_artifact_provenance_mismatch"
    refused = apply(corrupted, decision)
    assert refused.closure_evaluations == corrupted.closure_evaluations
    assert refused.work_items == corrupted.work_items
    assert not closure_capacity_blocks(refused)


@pytest.mark.parametrize("request_limit", [8192, 65536], ids=["overflow", "inline"])
@pytest.mark.parametrize("copy_identity", [False, True], ids=["new-id", "copied-id"])
def test_copied_artifact_alias_and_matching_seal_cannot_authenticate_origin(
    request_limit: int, copy_identity: bool
) -> None:
    fixture = co.evaluated_state(co.overflow_source(request_limit=request_limit))
    artifact_id, artifact = _beta_artifact_entry(fixture.state)
    evaluation = _single(fixture.state.closure_evaluations.values())
    assert closure_evaluation_origin_refusal(
        fixture.state, fixture.plan, evaluation
    )[1] is None
    alias_key = "stateful-shadow"
    alias = replace(
        artifact, artifact_id=artifact_id if copy_identity else alias_key
    )
    work = fixture.state.work_items[evaluation.target_work_item_id]
    snapshot = dict(work.payload[SNAPSHOT_KEY])
    evidence = []
    for envelope in snapshot["evidence_artifacts"]:
        evidence.append(envelope)
        if envelope["artifact_id"] == artifact_id:
            evidence.append(closure_artifact_envelope(alias))
    snapshot["evidence_artifacts"] = tuple(evidence)
    corrupted = replace(
        fixture.state,
        artifacts={**fixture.state.artifacts, alias_key: alias},
        work_items={
            **fixture.state.work_items,
            work.ref.work_item_id: replace(
                work, payload={**work.payload, SNAPSHOT_KEY: snapshot}
            ),
        },
    )
    _activation, refusal = closure_evaluation_origin_refusal(
        corrupted, fixture.plan, evaluation
    )
    assert refusal == "closure_snapshot_authority_mismatch"


@pytest.mark.parametrize("request_limit", [8192, 65536], ids=["overflow", "inline"])
def test_deleting_artifact_and_sealed_envelope_together_cannot_hide_accepted_output(
    request_limit: int,
) -> None:
    fixture = co.evaluated_state(co.overflow_source(request_limit=request_limit))
    artifact_id, _artifact = _beta_artifact_entry(fixture.state)
    evaluation = _single(fixture.state.closure_evaluations.values())
    work = fixture.state.work_items[evaluation.target_work_item_id]
    snapshot = dict(work.payload[SNAPSHOT_KEY])
    snapshot["evidence_artifacts"] = tuple(
        item
        for item in snapshot["evidence_artifacts"]
        if item["artifact_id"] != artifact_id
    )
    corrupted = replace(
        fixture.state,
        artifacts={
            key: value
            for key, value in fixture.state.artifacts.items()
            if key != artifact_id
        },
        work_items={
            **fixture.state.work_items,
            work.ref.work_item_id: replace(
                work, payload={**work.payload, SNAPSHOT_KEY: snapshot}
            ),
        },
    )
    _activation, refusal = closure_evaluation_origin_refusal(
        corrupted, fixture.plan, evaluation
    )
    assert refusal == "closure_snapshot_authority_mismatch"


def test_earlier_capacity_refusal_stays_unverified_after_later_legal_history() -> None:
    fixture = co.opened_state(co.overflow_source(overflow=False))
    first = co.evaluate_candidate(fixture)
    durable = apply(fixture.state, co.decide_candidate(fixture.state, first))
    assert closure_capacity_blocked(durable, first.transition_input.input_id)
    payload = dict(gl.source_payload())
    payload["body"] = "new later evidence " * 1000
    changed = gl.apply_accepted_input(
        durable,
        EnqueueWork(
            "enqueue-later",
            queue_family_id=fixture.plan.external_enqueue_routes[0].queue_family_id,
            payload=payload,
        ),
        gl.context(
            "enqueue-later", work_item_id="work-later", activation_id="activation-later"
        ),
    )
    changed = gl.claim_activation(
        changed, activation_id="activation-later", suffix="later"
    )
    changed = gl.apply_observation(
        changed,
        plan=fixture.plan,
        fingerprint=fixture.fingerprint,
        run_id="run-later",
        input_id="observe-later",
        marker="SOURCE_READY",
        artifact_payload=payload,
    )
    block = _single(closure_capacity_blocks(changed))
    assert block.input_id == first.transition_input.input_id
    assert block.freshness_status == "unknown"
    assert block.measurements is None
    assert (
        block.selected_capacity_limits["request_payload_byte_limit"] == co.REQUEST_LIMIT
    )
