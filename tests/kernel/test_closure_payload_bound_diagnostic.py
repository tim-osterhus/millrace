from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from millrace.contracts.transition import canonical_authority_mapping_bytes
from millrace.kernel.decision import _completion_request_payload
from tests.support import generic_lifecycle

_AUTH_SOURCE = generic_lifecycle.source_with_completion_behavior()
_AUTH_PLAN, _AUTH_FINGERPRINT = generic_lifecycle.compile_lifecycle(_AUTH_SOURCE)


def test_root_contract_can_exceed_declared_completion_bound_without_evidence() -> None:
    behavior = _AUTH_PLAN.completion_behaviors[0]
    stage = next(
        item
        for item in _AUTH_PLAN.stage_kinds
        if item.id == behavior.target_stage_kind_id
    )
    state, _plan, fingerprint = generic_lifecycle.admitted_state(
        plan=_AUTH_PLAN,
        fingerprint=_AUTH_FINGERPRINT,
    )
    root_payload = {
        **generic_lifecycle.source_payload(),
        "body": "x" * behavior.request_payload_byte_limit,
        "root_source": {"kind": "origin", "source_id": "generic-root"},
    }
    state = generic_lifecycle.enqueue_origin(state, payload=root_payload)
    state = generic_lifecycle.claim_activation(
        state, activation_id="activation-origin", suffix="origin"
    )
    state = generic_lifecycle.apply_observation(
        state,
        plan=_AUTH_PLAN,
        fingerprint=fingerprint,
        run_id="run-origin",
        input_id="observe-origin",
        marker="SOURCE_READY",
        artifact_payload=root_payload,
    )
    candidate = generic_lifecycle.project_next_lifecycle_transition(state).candidate
    assert candidate is not None and candidate.kind == "open"
    state = generic_lifecycle.apply_candidate(state, candidate)
    target = next(iter(state.closure_targets.values()))

    wide_behavior = replace(behavior, request_payload_byte_limit=1_000_000)
    wide_request, wide_refusal = _completion_request_payload(
        state=state,
        target=target,
        behavior=wide_behavior,
        stage=stage,
    )
    assert wide_refusal is None
    assert wide_request is not None
    snapshot = cast(
        Mapping[str, object], wide_request["closure_evidence_snapshot"]
    )
    assert snapshot["evidence_artifacts"] == ()
    root_contract = cast(Mapping[str, object], snapshot["root_contract"])
    assert (
        len(canonical_authority_mapping_bytes(root_contract))
        > behavior.request_payload_byte_limit
    )
    assert (
        len(canonical_authority_mapping_bytes(wide_request))
        > behavior.request_payload_byte_limit
    )

    request, refusal = _completion_request_payload(
        state=state,
        target=target,
        behavior=behavior,
        stage=stage,
    )
    assert request is None
    assert refusal == "closure_request_payload_limit_exceeded"
