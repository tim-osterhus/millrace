"""Closure-request overflow: compact dispatch projection and evidence pages.

``request_payload_byte_limit`` bounds the canonical request body that a runner
receives inline. A completion behavior that also selects ``request_overflow``
keeps the complete sealed ``closure_evidence_snapshot`` in its durable closure
request; dispatch replaces only that member with a ``closure_evidence_overflow``
projection. The projection's digests authenticate read-only page files rendered
deterministically from the durable snapshot for the owning runner session.
Pages are derived evidence views, never workflow authority: verdicts are still
checked against the durable snapshot.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    ClosureRequestOverflowDeclaration,
    CompletionBehaviorDeclaration,
    completion_behavior_request_overflow,
    freeze_authority_mapping,
    verify_authority_fingerprint,
)
from millrace.contracts.state import ClosureTargetRecord, RuntimeState
from millrace.contracts.transition import (
    EvaluateCompletionBehavior,
    canonical_authority_mapping_bytes,
    input_payload_digest,
)

SNAPSHOT_KEY = "closure_evidence_snapshot"
OVERFLOW_KEY = "closure_evidence_overflow"
OVERFLOW_RECORD_KIND = "closure_evidence_overflow"
PAGE_INDEX_RECORD_KIND = "closure_evidence_page_index"
_COMPONENT_HEX_LENGTH = 32
_PLACEHOLDER_COMPONENT = "0" * _COMPONENT_HEX_LENGTH
_ARTIFACT_INDEX_FIELDS = (
    "artifact_id",
    "artifact_schema_id",
    "payload_digest",
    "source_action_id",
    "source_stage_kind_id",
)
_REASSEMBLY = (
    "Concatenate one segment's pages in listed order to obtain its document: the "
    "UTF-8 JSON (indent 2, sorted keys) of that sealed closure_evidence_snapshot "
    "member. Each page lists its exact sha256 and byte length."
)


# Selected capacity exhaustion: the durable evidence is authentic but exceeds what
# the selected completion behavior can deliver to an evaluator. These refusals are
# durable closure capacity blocks (see ``closure_capacity_block``), never retried
# automatically and never turned into an evaluator verdict.
CLOSURE_CAPACITY_REFUSALS = frozenset(
    {
        "closure_evidence_item_limit_exceeded",
        "closure_request_payload_limit_exceeded",
        "closure_request_overflow_page_limit_exceeded",
        "closure_request_overflow_projection_limit_exceeded",
        "closure_request_overflow_snapshot_limit_exceeded",
    }
)
CAPACITY_BLOCK_RECORD_KIND = "closure_capacity_block"


class ClosureRequestOverflowError(ValueError):
    """The durable request cannot be represented through its selected overflow."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ClosureEvidencePage:
    """One workspace-relative page file and its exact bytes."""

    path: str
    data: bytes

    def descriptor(self) -> dict[str, AuthorityValue]:
        return {
            "path": self.path,
            "sha256": sha256(self.data).hexdigest(),
            "bytes": len(self.data),
        }


def materialization_component(session_id: str, dispatch_generation: int) -> str:
    """Fixed-length session-owned directory name; never derived from content."""
    identity = f"{session_id}\n{dispatch_generation}".encode()
    return sha256(identity).hexdigest()[:_COMPONENT_HEX_LENGTH]


def materialization_directory(
    overflow: ClosureRequestOverflowDeclaration,
    *,
    session_id: str,
    dispatch_generation: int,
) -> str:
    component = materialization_component(session_id, dispatch_generation)
    return f"{overflow.materialization_root}/{component}"


def closure_request_payload_refusal(
    payload: Mapping[str, AuthorityValue],
    *,
    behavior: CompletionBehaviorDeclaration,
) -> str | None:
    """Admission predicate used when the kernel creates a closure request."""
    try:
        _dispatch_projection(payload, behavior=behavior, component=None)
    except ClosureRequestOverflowError as exc:
        return exc.reason
    return None


def closure_request_dispatch_payload(
    payload: Mapping[str, AuthorityValue],
    *,
    behavior: CompletionBehaviorDeclaration,
    session_id: str,
    dispatch_generation: int,
) -> Mapping[str, AuthorityValue]:
    """The exact work-item payload a runner session receives inline."""
    projected, _pages = _dispatch_projection(
        payload,
        behavior=behavior,
        component=materialization_component(session_id, dispatch_generation),
    )
    return projected


def closure_request_evidence_pages(
    payload: Mapping[str, AuthorityValue],
    *,
    behavior: CompletionBehaviorDeclaration,
    session_id: str,
    dispatch_generation: int,
) -> tuple[ClosureEvidencePage, ...]:
    """Page files a session must materialize; empty when the body is inline."""
    _projected, pages = _dispatch_projection(
        payload,
        behavior=behavior,
        component=materialization_component(session_id, dispatch_generation),
    )
    return pages


def closure_capacity_measurements(
    payload: Mapping[str, AuthorityValue],
    *,
    behavior: CompletionBehaviorDeclaration,
) -> dict[str, AuthorityValue]:
    """Measured sizes versus selected limits for a full, uncapped request."""
    snapshot = payload.get(SNAPSHOT_KEY)
    evidence = (
        snapshot.get("evidence_artifacts") if isinstance(snapshot, Mapping) else None
    )
    measured: dict[str, AuthorityValue] = {
        "request_bytes": len(_canonical(payload)),
        "request_payload_byte_limit": behavior.request_payload_byte_limit,
        "evidence_items": (
            len(evidence) if isinstance(evidence, (list, tuple)) else None
        ),
        "evidence_item_limit": behavior.evidence_item_limit,
    }
    overflow = completion_behavior_request_overflow(behavior)
    if overflow is None or not isinstance(snapshot, Mapping):
        return measured
    snapshot_bytes = _canonical(snapshot)
    index_pages, segment_pages = _evidence_pages(
        snapshot,
        snapshot_digest=f"sha256:{sha256(snapshot_bytes).hexdigest()}",
        directory=f"{overflow.materialization_root}/{_PLACEHOLDER_COMPONENT}",
        page_byte_limit=overflow.page_byte_limit,
    )
    measured.update(
        snapshot_bytes=len(snapshot_bytes),
        max_snapshot_bytes=overflow.max_snapshot_bytes,
        page_count=len(index_pages) + len(segment_pages),
        max_page_count=overflow.max_page_count,
        page_byte_limit=overflow.page_byte_limit,
    )
    return measured


def closure_capacity_block(
    *,
    closure_target_id: str,
    reason: str,
    measurements: Mapping[str, AuthorityValue],
) -> str:
    """Deterministic bounded refusal detail that durably records a capacity block."""
    return json.dumps(
        {
            "record_kind": CAPACITY_BLOCK_RECORD_KIND,
            "schema_version": 1,
            "closure_target_id": closure_target_id,
            "reason": reason,
            "operator_required": True,
            "measurements": dict(measurements),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_closure_capacity_block(detail: object) -> Mapping[str, object] | None:
    """Return a capacity block recorded by ``closure_capacity_block``, else None.

    The record must be exact and its measurements must substantiate its reason.
    """
    value = _capacity_record(detail)
    if value is None or not _capacity_exceeded(
        str(value["reason"]), _measurements(value)
    ):
        return None
    return value


def _capacity_record(detail: object) -> dict[str, object] | None:
    if not isinstance(detail, str):
        return None
    try:
        value = json.loads(detail)
    except ValueError:
        return None
    if (
        not isinstance(value, dict)
        or value.get("record_kind") != CAPACITY_BLOCK_RECORD_KIND
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or not isinstance(value.get("reason"), str)
        or value.get("reason") not in CLOSURE_CAPACITY_REFUSALS
        or not isinstance(value.get("closure_target_id"), str)
        or value.get("operator_required") is not True
        or not isinstance(value.get("measurements"), dict)
    ):
        return None
    return value


def _measurements(record: Mapping[str, object]) -> Mapping[str, object]:
    measurements = record["measurements"]
    assert isinstance(measurements, Mapping)
    return measurements


# The measured value and recorded selected limit that substantiate each reason.
# A projection refusal is substantiated by the inline request exceeding its limit;
# the projection size itself is not recorded.
_CAPACITY_EVIDENCE = {
    "closure_evidence_item_limit_exceeded": ("evidence_items", "evidence_item_limit"),
    "closure_request_payload_limit_exceeded": (
        "request_bytes",
        "request_payload_byte_limit",
    ),
    "closure_request_overflow_snapshot_limit_exceeded": (
        "snapshot_bytes",
        "max_snapshot_bytes",
    ),
    "closure_request_overflow_page_limit_exceeded": ("page_count", "max_page_count"),
    "closure_request_overflow_projection_limit_exceeded": (
        "request_bytes",
        "request_payload_byte_limit",
    ),
}


def _capacity_exceeded(reason: str, measurements: Mapping[str, object]) -> bool:
    measured_key, limit_key = _CAPACITY_EVIDENCE[reason]
    measured = measurements.get(measured_key)
    limit = measurements.get(limit_key)
    return type(measured) is int and type(limit) is int and 0 < limit < measured


# Refusal detail is not authenticated by the receipt. Coherent measurements
# remain unverified; the recorded refusal never depends on its detail.
CAPACITY_DETAIL_VERIFIED = "verified"
CAPACITY_DETAIL_UNVERIFIED = "unverified"
CAPACITY_DETAIL_UNMEASURED = "unmeasured"
CAPACITY_DETAIL_ABSENT = "absent"
CAPACITY_DETAIL_INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ClosureCapacityBlock:
    """Durable capacity hold derived from an exact refused evaluation receipt.

    ``closure_target_id``, ``input_id`` and ``reason`` come from the refused receipt
    whose input payload digest authenticates an evaluation of that target, the
    same receipt that suppresses the evaluation; ``closure_target_id`` is None
    when the receipt authenticates no target (inconsistent durable state).
    ``measurements`` is withheld: the receipt authenticates the evaluation input
    and refusal reason, but not the measured values in its mutable detail.
    Coherent detail is "unverified", empty detail is "unmeasured", missing detail
    is "absent", and inconsistent detail is "invalid". Selected capacity limits
    come independently from the target's admitted plan. Freshness is "unknown":
    recorded refusals may precede newer evidence and do not prove that the current
    evaluation input is suppressed.
    """

    closure_target_id: str | None
    input_id: str
    refusal_record_id: str | None
    reason: str
    detail_status: str
    measurements: Mapping[str, object] | None
    selected_capacity_limits: Mapping[str, int] | None
    freshness_status: str = "unknown"


def closure_capacity_blocked(state: RuntimeState, input_id: str) -> bool:
    """Whether this exact evaluation input has a durable capacity refusal."""
    receipt = state.receipts.get(input_id)
    return (
        receipt is not None
        and not receipt.accepted
        and receipt.refusal_reason in CLOSURE_CAPACITY_REFUSALS
    )


def closure_capacity_blocks(state: RuntimeState) -> tuple[ClosureCapacityBlock, ...]:
    """Recorded capacity refusals for open targets, with unknown current freshness.

    Holds are derived from refused receipts, never from refusal detail, so a
    missing or altered detail cannot hide a hold; holds of closed targets are
    omitted because a closed target is never evaluated.
    """
    refusals = {
        refusal.input_id: refusal
        for refusal in state.refusals
        if refusal.input_kind == EvaluateCompletionBehavior.input_kind
    }
    blocks: list[ClosureCapacityBlock] = []
    for input_id, receipt in state.receipts.items():
        if not closure_capacity_blocked(state, input_id):
            continue
        reason = str(receipt.refusal_reason)
        target = _evaluated_target(
            state, input_id, receipt.receipt_ref.input_payload_digest
        )
        if target is not None and target.status != "open":
            continue
        refusal = refusals.get(input_id)
        detail_status, measurements = _capacity_detail(
            None if refusal is None else refusal.detail,
            state=state,
            target=target,
            reason=reason,
        )
        blocks.append(
            ClosureCapacityBlock(
                closure_target_id=(
                    None if target is None else target.closure_target_id
                ),
                input_id=input_id,
                refusal_record_id=None if refusal is None else refusal.record_id,
                reason=reason,
                detail_status=detail_status,
                measurements=measurements,
                selected_capacity_limits=(
                    None if target is None else _selected_capacity_limits(state, target)
                ),
            )
        )
    return tuple(blocks)


def _evaluated_target(
    state: RuntimeState, input_id: str, digest: str
) -> ClosureTargetRecord | None:
    """The target whose evaluation input has exactly this id and payload digest."""
    for target in state.closure_targets.values():
        candidate = EvaluateCompletionBehavior(
            input_id=input_id,
            selected_plan_ref=target.selected_plan_ref,
            completion_behavior_id=str(target.completion_behavior_id),
            closure_target_id=target.closure_target_id,
        )
        if input_payload_digest(candidate) == digest:
            return target
    return None


def _capacity_detail(
    detail: str | None,
    *,
    state: RuntimeState,
    target: ClosureTargetRecord | None,
    reason: str,
) -> tuple[str, Mapping[str, object] | None]:
    if detail is None:
        return CAPACITY_DETAIL_ABSENT, None
    record = _capacity_record(detail)
    limits = None if target is None else _selected_capacity_limits(state, target)
    if (
        record is None
        or target is None
        or limits is None
        or record["closure_target_id"] != target.closure_target_id
        or record["reason"] != reason
    ):
        return CAPACITY_DETAIL_INVALID, None
    measurements = _measurements(record)
    if not measurements:
        return CAPACITY_DETAIL_UNMEASURED, None
    measured = {"request_bytes", "evidence_items"}
    if "max_snapshot_bytes" in limits:
        measured |= {"snapshot_bytes", "page_count"}
    if (
        set(measurements) != set(limits) | measured
        or any(
            type(measurements[key]) is not int or measurements[key] != value
            for key, value in limits.items()
        )
        or not all(
            _non_negative_int(measurements[key])
            for key in measured - {"evidence_items"}
        )
        or not (
            measurements["evidence_items"] is None
            or _non_negative_int(measurements["evidence_items"])
        )
        or not _capacity_exceeded(reason, measurements)
    ):
        return CAPACITY_DETAIL_INVALID, None
    # Selected limits and numeric coherence cannot authenticate refusal-time
    # measurements. Recomputing from current state would also misdate history.
    return CAPACITY_DETAIL_UNVERIFIED, None


def _non_negative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _selected_capacity_limits(
    state: RuntimeState, target: ClosureTargetRecord
) -> dict[str, int] | None:
    admitted = state.admitted_plans.get(target.selected_plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != target.selected_plan_ref:
        return None
    if not verify_authority_fingerprint(
        admitted.selected_plan, target.selected_plan_ref.authority_fingerprint
    ):
        return None
    behavior = next(
        (
            behavior
            for behavior in admitted.selected_plan.completion_behaviors
            if behavior.id == target.completion_behavior_id
        ),
        None,
    )
    if behavior is None:
        return None
    limits = {
        "request_payload_byte_limit": behavior.request_payload_byte_limit,
        "evidence_item_limit": behavior.evidence_item_limit,
    }
    overflow = completion_behavior_request_overflow(behavior)
    if overflow is not None:
        limits.update(
            max_snapshot_bytes=overflow.max_snapshot_bytes,
            max_page_count=overflow.max_page_count,
            page_byte_limit=overflow.page_byte_limit,
        )
    return limits


def _dispatch_projection(
    payload: Mapping[str, AuthorityValue],
    *,
    behavior: CompletionBehaviorDeclaration,
    component: str | None,
) -> tuple[Mapping[str, AuthorityValue], tuple[ClosureEvidencePage, ...]]:
    limit = behavior.request_payload_byte_limit
    if len(_canonical(payload)) <= limit:
        return payload, ()
    overflow = completion_behavior_request_overflow(behavior)
    if overflow is None:
        raise ClosureRequestOverflowError("closure_request_payload_limit_exceeded")
    snapshot = payload.get(SNAPSHOT_KEY)
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("record_kind") != "closure_evidence_snapshot"
        or OVERFLOW_KEY in payload
    ):
        raise ClosureRequestOverflowError("closure_request_overflow_snapshot_invalid")
    snapshot_bytes = _canonical(snapshot)
    if len(snapshot_bytes) > overflow.max_snapshot_bytes:
        raise ClosureRequestOverflowError(
            "closure_request_overflow_snapshot_limit_exceeded"
        )
    directory = (
        f"{overflow.materialization_root}/"
        f"{_PLACEHOLDER_COMPONENT if component is None else component}"
    )
    index_pages, segment_pages = _evidence_pages(
        snapshot,
        snapshot_digest=f"sha256:{sha256(snapshot_bytes).hexdigest()}",
        directory=directory,
        page_byte_limit=overflow.page_byte_limit,
    )
    pages = index_pages + segment_pages
    if len(pages) > overflow.max_page_count:
        raise ClosureRequestOverflowError(
            "closure_request_overflow_page_limit_exceeded"
        )
    root = _mapping(snapshot.get("root_contract"))
    prior = snapshot.get("prior_verdict")
    projection: dict[str, AuthorityValue] = {
        "record_kind": OVERFLOW_RECORD_KIND,
        "schema_version": 1,
        "snapshot_digest": f"sha256:{sha256(snapshot_bytes).hexdigest()}",
        "snapshot_bytes": len(snapshot_bytes),
        "closure_target_id": snapshot.get("closure_target_id"),
        "selected_plan_fingerprint": snapshot.get("selected_plan_fingerprint"),
        "lineage_id": snapshot.get("lineage_id"),
        "freshness_anchor_digest": snapshot.get("freshness_anchor_digest"),
        "root_contract_digest": root.get("payload_digest"),
        "prior_verdict_digest": (
            _mapping(prior).get("payload_digest") if prior is not None else None
        ),
        "evidence_artifact_count": len(_sequence(snapshot.get("evidence_artifacts"))),
        "page_byte_limit": overflow.page_byte_limit,
        "page_count": len(pages),
        "page_bytes": sum(len(page.data) for page in pages),
        "materialization_directory": directory,
        "index_pages": tuple(page.descriptor() for page in index_pages),
    }
    projected = freeze_authority_mapping(
        {
            **{key: value for key, value in payload.items() if key != SNAPSHOT_KEY},
            OVERFLOW_KEY: projection,
        }
    )
    if len(_canonical(projected)) > behavior.request_payload_byte_limit:
        raise ClosureRequestOverflowError(
            "closure_request_overflow_projection_limit_exceeded"
        )
    return projected, pages


def _evidence_pages(
    snapshot: Mapping[str, AuthorityValue],
    *,
    snapshot_digest: str,
    directory: str,
    page_byte_limit: int,
) -> tuple[tuple[ClosureEvidencePage, ...], tuple[ClosureEvidencePage, ...]]:
    segments: list[tuple[str, dict[str, AuthorityValue], object]] = []
    root = _mapping(snapshot.get("root_contract"))
    segments.append(
        (
            "root-contract",
            {
                "segment": "root_contract",
                "work_item_id": root.get("work_item_id"),
                "payload_digest": root.get("payload_digest"),
            },
            root,
        )
    )
    prior = snapshot.get("prior_verdict")
    if prior is not None:
        prior_entry: dict[str, AuthorityValue] = {
            "segment": "prior_verdict",
            **_artifact_index(prior),
        }
        segments.append(("prior-verdict", prior_entry, prior))
    for position, artifact in enumerate(_sequence(snapshot.get("evidence_artifacts"))):
        segments.append(
            (
                f"evidence-{position + 1:04d}",
                {
                    "segment": "evidence_artifact",
                    "position": position,
                    **_artifact_index(artifact),
                },
                artifact,
            )
        )
    segment_pages: list[ClosureEvidencePage] = []
    index_segments: list[dict[str, AuthorityValue]] = []
    for name, entry, value in segments:
        document = _document_bytes(value)
        pages = tuple(
            ClosureEvidencePage(f"{directory}/{name}-{number:05d}.txt", data)
            for number, data in enumerate(_split_pages(document, page_byte_limit), 1)
        )
        segment_pages.extend(pages)
        index_segments.append(
            {
                **entry,
                "document_sha256": sha256(document).hexdigest(),
                "document_bytes": len(document),
                "pages": tuple(page.descriptor() for page in pages),
            }
        )
    index_document = _document_bytes(
        {
            "record_kind": PAGE_INDEX_RECORD_KIND,
            "schema_version": 1,
            "snapshot_digest": snapshot_digest,
            "closure_target_id": snapshot.get("closure_target_id"),
            "reassembly": _REASSEMBLY,
            "segments": tuple(index_segments),
        }
    )
    index_pages = tuple(
        ClosureEvidencePage(f"{directory}/index-{number:05d}.txt", data)
        for number, data in enumerate(_split_pages(index_document, page_byte_limit), 1)
    )
    return index_pages, tuple(segment_pages)


def _artifact_index(value: object) -> dict[str, AuthorityValue]:
    artifact = _mapping(value)
    return {field: artifact.get(field) for field in _ARTIFACT_INDEX_FIELDS}


def _document_bytes(value: object) -> bytes:
    text = json.dumps(_plain(value), ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _split_pages(document: bytes, limit: int) -> tuple[bytes, ...]:
    """Split at line boundaries; an over-long line splits at a UTF-8 boundary."""
    pages: list[bytes] = []
    current = bytearray()
    for line in document.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                pages.append(bytes(current))
                current = bytearray()
            cut = limit
            while (line[cut] & 0xC0) == 0x80:
                cut -= 1
            pages.append(line[:cut])
            line = line[cut:]
        if len(current) + len(line) > limit:
            pages.append(bytes(current))
            current = bytearray()
        current += line
    if current:
        pages.append(bytes(current))
    return tuple(pages)


def _canonical(value: Mapping[str, object]) -> bytes:
    try:
        return canonical_authority_mapping_bytes(value)
    except (TypeError, ValueError) as exc:
        raise ClosureRequestOverflowError(
            "closure_request_overflow_snapshot_invalid"
        ) from exc


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _mapping(value: object) -> Mapping[str, AuthorityValue]:
    if not isinstance(value, Mapping):
        raise ClosureRequestOverflowError("closure_request_overflow_snapshot_invalid")
    return value


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ClosureRequestOverflowError("closure_request_overflow_snapshot_invalid")
    return tuple(value)


__all__ = (
    "CAPACITY_BLOCK_RECORD_KIND",
    "CLOSURE_CAPACITY_REFUSALS",
    "OVERFLOW_KEY",
    "OVERFLOW_RECORD_KIND",
    "PAGE_INDEX_RECORD_KIND",
    "SNAPSHOT_KEY",
    "ClosureCapacityBlock",
    "ClosureEvidencePage",
    "ClosureRequestOverflowError",
    "closure_capacity_block",
    "closure_capacity_blocked",
    "closure_capacity_blocks",
    "closure_capacity_measurements",
    "closure_request_dispatch_payload",
    "closure_request_evidence_pages",
    "closure_request_payload_refusal",
    "materialization_component",
    "materialization_directory",
    "parse_closure_capacity_block",
)
