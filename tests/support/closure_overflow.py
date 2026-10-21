"""Closure-request overflow fixtures built through real kernel transitions.

Every durable row produced here comes from an accepted kernel transition
(enqueue, claim, runner observation, fanout, open, evaluate). Nothing inserts
WorkItems, activations, runs, artifacts, or closure records directly.

Sibling Core test slices import this module read-only. Keep the public names
and their signatures stable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from millrace.contracts import SelectedCompiledPlan
from millrace.contracts.closure_request_overflow import OVERFLOW_KEY, SNAPSHOT_KEY
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import RuntimeState, WorkItem
from millrace.contracts.transition import (
    TransitionDecision,
    artifact_payload_digest,
    canonical_authority_mapping_bytes,
)
from millrace.kernel import apply, decide
from millrace.kernel.lifecycle import (
    ProjectedLifecycleCandidate,
    project_next_lifecycle_transition,
)
from support import generic_lifecycle as gl

REQUEST_LIMIT = 8192
PAGE_BYTE_LIMIT = 4096
MAX_SNAPSHOT_BYTES = 1_048_576
MAX_PAGE_COUNT = 1024
MATERIALIZATION_ROOT = "closure-evidence"
RUNNER_PAYLOAD_CAPACITY = 16_384
MAX_EVIDENCE_COUNT = 64
PASSING_MARKER = "REVIEW_PASSED"
GAP_MARKER = "REVIEW_GAP"
BLOCKED_MARKER = "REVIEW_BLOCKED"

__all__ = (
    "BLOCKED_MARKER",
    "GAP_MARKER",
    "MATERIALIZATION_ROOT",
    "MAX_EVIDENCE_COUNT",
    "MAX_PAGE_COUNT",
    "MAX_SNAPSHOT_BYTES",
    "OVERFLOW_KEY",
    "PAGE_BYTE_LIMIT",
    "PASSING_MARKER",
    "REQUEST_LIMIT",
    "RUNNER_PAYLOAD_CAPACITY",
    "SNAPSHOT_KEY",
    "OverflowState",
    "advance",
    "anchors",
    "closure_request_item",
    "closure_request_item_or_none",
    "decide_candidate",
    "durable_snapshot",
    "evaluate_candidate",
    "evaluated_state",
    "evaluator_activation",
    "evidence_state",
    "observe_evaluator",
    "opened_state",
    "overflow_source",
    "snapshot_canonical_bytes",
    "verdict_body",
    "work_payload_canonical_bytes",
)


@dataclass(frozen=True, slots=True)
class OverflowState:
    """A runtime state with the compiled plan and fingerprint it was built under."""

    state: RuntimeState
    plan: SelectedCompiledPlan
    fingerprint: str


def overflow_source(
    *,
    request_limit: int = REQUEST_LIMIT,
    page_byte_limit: int = PAGE_BYTE_LIMIT,
    max_snapshot_bytes: int = MAX_SNAPSHOT_BYTES,
    max_page_count: int = MAX_PAGE_COUNT,
    materialization_root: str = MATERIALIZATION_ROOT,
    overflow: bool = True,
) -> dict[str, object]:
    """Generic closure workflow source with an inline request limit.

    The runner component pin is raised to at least the request limit so the
    compiled plan stays admissible; the inline limit itself is the parameter.
    """
    source = gl.source_with_completion_behavior()
    source["fanout_declarations"] = [
        gl._fanout(gl.FANOUT_BETA_ID, target_route_id="route.beta")
    ]
    source["join_declarations"] = []
    behavior = _completion_behavior(source)
    behavior["request_payload_byte_limit"] = request_limit
    if overflow:
        behavior["request_overflow"] = {
            "policy": "materialized_evidence_pages",
            "materialization_root": materialization_root,
            "page_byte_limit": page_byte_limit,
            "max_snapshot_bytes": max_snapshot_bytes,
            "max_page_count": max_page_count,
        }
    _runner_component_pin(source)["max_work_item_payload_bytes"] = max(
        RUNNER_PAYLOAD_CAPACITY, request_limit
    )
    return source


def evidence_state(
    source: Mapping[str, object] | None = None,
    *,
    count: int = 3,
    summary_bytes: int = 4000,
    summary_character: str = "x",
) -> OverflowState:
    """Closure-ready lineage holding ``count`` accepted beta evidence artifacts.

    Each evidence artifact is produced by claiming a real beta activation and
    accepting its runner result. ``summary_bytes`` is the exact UTF-8 length of
    each artifact summary; ``summary_character`` fills it (ASCII tail when the
    remainder is not a multiple of the character width).
    """
    assert 1 <= count <= MAX_EVIDENCE_COUNT
    workflow = dict(source) if source is not None else overflow_source()
    plan, fingerprint = gl.compile_lifecycle(workflow)
    state, plan, fingerprint = gl.admitted_state(plan=plan, fingerprint=fingerprint)
    origin = _origin_payload(count)
    state = gl.enqueue_origin(state, payload=origin)
    state = gl.claim_activation(
        state, activation_id="activation-origin", suffix="origin"
    )
    state = gl.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-origin",
        input_id="observe-origin",
        marker="SOURCE_READY",
        artifact_payload=origin,
    )
    state = gl.apply_fanout(state, gl.FANOUT_BETA_ID, input_id="fanout-beta")
    root_digest = artifact_payload_digest(state.work_items["work-origin"].payload)
    for position, activation_id in enumerate(_beta_activation_ids(state)):
        body = _evidence_body(
            root_digest=root_digest,
            summary=_summary(
                f"evidence-{position:04d} ", summary_bytes, summary_character
            ),
        )
        state = gl.claim_activation(
            state, activation_id=activation_id, suffix=f"beta-{position}"
        )
        state = gl.apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-beta-{position}",
            input_id=f"observe-beta-{position}",
            marker="BETA_READY",
            artifact_payload=body,
        )
    return OverflowState(state=state, plan=plan, fingerprint=fingerprint)


def opened_state(
    source: Mapping[str, object] | None = None,
    **evidence: Any,
) -> OverflowState:
    """Evidence state after the real ``open`` candidate has been accepted."""
    return advance(evidence_state(source, **evidence), "open")


def evaluated_state(
    source: Mapping[str, object] | None = None,
    **evidence: Any,
) -> OverflowState:
    """Evidence state after ``open`` and an accepted ``evaluate`` candidate.

    Raises ``AssertionError`` when evaluation is refused. Use
    :func:`evaluate_candidate` plus :func:`decide_candidate` to observe refusals.
    """
    return advance(advance(evidence_state(source, **evidence), "open"), "evaluate")


def advance(fixture: OverflowState, kind: str) -> OverflowState:
    """Apply the projected lifecycle candidate of ``kind`` through the kernel."""
    candidate = _candidate_of_kind(fixture.state, kind)
    decision = decide(
        fixture.state, candidate.transition_input, candidate.transition_context
    )
    assert decision.accepted is True, decision.refusal
    return replace(fixture, state=apply(fixture.state, decision))


def evaluate_candidate(fixture: OverflowState) -> ProjectedLifecycleCandidate:
    """The projected ``evaluate`` candidate, not yet decided or applied."""
    return _candidate_of_kind(fixture.state, "evaluate")


def decide_candidate(
    state: RuntimeState,
    candidate: ProjectedLifecycleCandidate,
) -> TransitionDecision:
    """Decide a projected candidate without applying it."""
    return decide(state, candidate.transition_input, candidate.transition_context)


def closure_request_items(state: RuntimeState) -> tuple[WorkItem, ...]:
    """Every closure request work item carrying a snapshot, in creation order."""
    return tuple(
        item for item in state.work_items.values() if SNAPSHOT_KEY in item.payload
    )


def closure_request_item_or_none(state: RuntimeState) -> WorkItem | None:
    """The newest closure request work item (the latest evaluation's target)."""
    if not state.closure_evaluations:
        return None
    latest = list(state.closure_evaluations.values())[-1]
    item = state.work_items[latest.target_work_item_id]
    assert SNAPSHOT_KEY in item.payload, item.ref.work_item_id
    return item


def closure_request_item(state: RuntimeState) -> WorkItem:
    item = closure_request_item_or_none(state)
    assert item is not None
    return item


def durable_snapshot(state: RuntimeState) -> Mapping[str, Any]:
    """The full ``closure_evidence_snapshot`` held by the durable request."""
    snapshot = closure_request_item(state).payload[SNAPSHOT_KEY]
    assert isinstance(snapshot, Mapping)
    return snapshot


def evaluator_activation(state: RuntimeState) -> Any:
    """The activation that evaluates the closure request work item."""
    work_item_id = closure_request_item(state).ref.work_item_id
    matches = [
        activation
        for activation in state.activations.values()
        if activation.work_item_id == work_item_id
    ]
    assert len(matches) == 1, [activation.activation_id for activation in matches]
    return matches[0]


def observe_evaluator(
    fixture: OverflowState,
    *,
    marker: str,
    suffix: str,
    artifact_payload: Mapping[str, AuthorityValue] | None = None,
) -> OverflowState:
    """Claim the evaluator activation and accept its runner result.

    Without ``artifact_payload`` the verdict is built from the durable snapshot
    with ``marker``'s status (passed, failed for GAP, blocked for BLOCKED).
    """
    activation = evaluator_activation(fixture.state)
    state = gl.claim_activation(
        fixture.state, activation_id=activation.activation_id, suffix=suffix
    )
    body: Mapping[str, AuthorityValue] = (
        artifact_payload
        if artifact_payload is not None
        else verdict_body(durable_snapshot(state), marker=marker)
    )
    state = gl.apply_observation(
        state,
        plan=fixture.plan,
        fingerprint=fixture.fingerprint,
        run_id=f"run-{suffix}",
        input_id=f"observe-{suffix}",
        marker=marker,
        artifact_payload=body,
    )
    return replace(fixture, state=state)


def verdict_body(snapshot: Mapping[str, Any], *, marker: str) -> dict[str, Any]:
    """Closure verdict body anchored to a snapshot, shaped like the review schema."""
    gap = marker in {GAP_MARKER, "REMEDIATION_NEEDED"}
    blocked = marker in {BLOCKED_MARKER, "BLOCKED"}
    result = {
        "criterion_id": "criterion-1",
        "status": "blocked" if blocked else "failed" if gap else "passed",
        "provenance": "missing" if blocked else "fresh",
        "evidence_refs": ()
        if blocked
        else ({"evidence_id": "evidence-1", "summary": "reviewed"},),
    }
    return {
        "artifact_kind": "closure_verdict",
        "summary": "Review completed.",
        "closure_target_id": snapshot["closure_target_id"],
        "root_contract_digest": snapshot["root_contract"]["payload_digest"],
        "freshness_anchor_digest": snapshot["freshness_anchor_digest"],
        "rubric": {
            "criteria": (
                {
                    "criterion_id": "criterion-1",
                    "requirement": "The closure contract is satisfied.",
                    "evidence_rule": "Use current review evidence.",
                },
            )
        },
        "criterion_results": (result,),
        "observations": (),
        "remediation_guidance": (
            (
                {
                    "guidance_id": "guidance-1",
                    "summary": "Address criterion-1.",
                    "criterion_refs": ({"criterion_id": "criterion-1"},),
                },
            )
            if gap
            else ()
        ),
        "confidence": "high",
        "residual_uncertainty": "none",
    }


def anchors(snapshot: Mapping[str, Any]) -> dict[str, object]:
    """Authority anchors that must not change between inline and overflow."""
    return {
        "closure_target_id": snapshot["closure_target_id"],
        "lineage_id": snapshot["lineage_id"],
        "root_contract_digest": snapshot["root_contract"]["payload_digest"],
        "freshness_anchor_digest": snapshot["freshness_anchor_digest"],
        "evidence_artifact_ids": tuple(
            artifact["artifact_id"] for artifact in snapshot["evidence_artifacts"]
        ),
    }


def snapshot_canonical_bytes(snapshot: Mapping[str, Any]) -> int:
    return len(canonical_authority_mapping_bytes(snapshot))


def work_payload_canonical_bytes(item: WorkItem) -> int:
    return len(canonical_authority_mapping_bytes(item.payload))


def _candidate_of_kind(state: RuntimeState, kind: str) -> ProjectedLifecycleCandidate:
    candidate = project_next_lifecycle_transition(state).candidate
    assert candidate is not None and candidate.kind == kind, candidate
    return candidate


def _beta_activation_ids(state: RuntimeState) -> tuple[str, ...]:
    return tuple(
        sorted(
            activation.activation_id
            for activation in state.activations.values()
            if str(activation.stage_kind_id) == "beta_stage"
        )
    )


def _origin_payload(count: int) -> dict[str, AuthorityValue]:
    return {
        "bundle_id": "bundle-a",
        "items": tuple(
            {"item_id": f"item-{index}", "body": f"Body {index}"}
            for index in range(count)
        ),
        "root_source": {"kind": "origin", "source_id": "generic-root"},
    }


def _evidence_body(*, root_digest: str, summary: str) -> dict[str, Any]:
    return {
        "artifact_kind": "closure_verdict",
        "summary": summary,
        "closure_target_id": "pending",
        "root_contract_digest": root_digest,
        "freshness_anchor_digest": root_digest,
        "rubric": {
            "criteria": (
                {
                    "criterion_id": "criterion-1",
                    "requirement": "The closure contract is satisfied.",
                    "evidence_rule": "Use current review evidence.",
                },
            )
        },
        "criterion_results": (
            {
                "criterion_id": "criterion-1",
                "status": "passed",
                "provenance": "fresh",
                "evidence_refs": (
                    {"evidence_id": "evidence-1", "summary": "reviewed"},
                ),
            },
        ),
        "observations": (),
        "remediation_guidance": (),
        "confidence": "high",
        "residual_uncertainty": "none",
    }


def _summary(label: str, total_bytes: int, character: str) -> str:
    """A summary whose UTF-8 length is exactly ``total_bytes``."""
    assert len(character) == 1, character
    width = len(character.encode("utf-8"))
    remainder = total_bytes - len(label.encode("ascii"))
    assert remainder >= 0, (label, total_bytes)
    fill, tail = divmod(remainder, width)
    text = label + character * fill + "x" * tail
    assert len(text.encode("utf-8")) == total_bytes
    return text


def _completion_behavior(source: Mapping[str, object]) -> dict[str, Any]:
    behaviors = source["completion_behaviors"]
    assert isinstance(behaviors, list)
    behavior = behaviors[0]
    assert isinstance(behavior, dict)
    return behavior


def _runner_component_pin(source: Mapping[str, object]) -> dict[str, Any]:
    bindings = source["runner_bindings"]
    assert isinstance(bindings, list)
    pin = bindings[0]["component_pin"]
    assert isinstance(pin, dict)
    return pin
