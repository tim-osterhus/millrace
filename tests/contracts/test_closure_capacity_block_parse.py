"""Closure capacity block round trip, parse refusals, and listing filters.

``closure_capacity_block`` writes the durable refusal detail and
``parse_closure_capacity_block`` reads it back. ``closure_capacity_blocks``
derives each hold from the refused receipt that suppresses the evaluation, so a
missing, foreign or forged detail can never hide the hold; it only lowers the
hold's ``detail_status`` and withholds unverified measurements. The listing cases
use one real kernel refusal, built through the public overflow fixtures.

The parser also requires an exact integer schema version, a string reason, and
measurements whose recorded value exceeds the recorded selected limit for that
reason.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from millrace.contracts.closure_request_overflow import (
    CAPACITY_BLOCK_RECORD_KIND,
    CAPACITY_DETAIL_ABSENT,
    CAPACITY_DETAIL_INVALID,
    CAPACITY_DETAIL_UNMEASURED,
    CAPACITY_DETAIL_UNVERIFIED,
    CLOSURE_CAPACITY_REFUSALS,
    closure_capacity_block,
    closure_capacity_blocks,
    parse_closure_capacity_block,
)
from millrace.contracts.state import RuntimeState
from millrace.kernel import apply
from support import closure_overflow as co

TARGET_ID = "target-probe"
PAYLOAD_LIMIT = "closure_request_payload_limit_exceeded"
NON_CAPACITY_REASON = "closure_evaluation_not_admitted"
_MISSING = object()
_MEASUREMENTS = {"request_bytes": 20000, "request_payload_byte_limit": 16384}
# Measurements that substantiate each capacity reason: measured key, then its limit.
_REASON_MEASUREMENTS = {
    "closure_evidence_item_limit_exceeded": {
        "evidence_items": 97,
        "evidence_item_limit": 96,
    },
    "closure_request_payload_limit_exceeded": dict(_MEASUREMENTS),
    "closure_request_overflow_snapshot_limit_exceeded": {
        "snapshot_bytes": 8714069,
        "max_snapshot_bytes": 8388608,
    },
    "closure_request_overflow_page_limit_exceeded": {
        "page_count": 1025,
        "max_page_count": 1024,
    },
    "closure_request_overflow_projection_limit_exceeded": {
        "request_bytes": 200000,
        "request_payload_byte_limit": 131072,
    },
}


def _record(**changes: Any) -> str:
    """A valid capacity block record, with keys replaced or removed by ``changes``."""
    record: dict[str, Any] = {
        "record_kind": CAPACITY_BLOCK_RECORD_KIND,
        "schema_version": 1,
        "closure_target_id": TARGET_ID,
        "reason": PAYLOAD_LIMIT,
        "operator_required": True,
        "measurements": dict(_MEASUREMENTS),
    }
    for key, value in changes.items():
        if value is _MISSING:
            record.pop(key)
        else:
            record[key] = value
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Round trip.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", sorted(CLOSURE_CAPACITY_REFUSALS))
def test_capacity_block_round_trips_for_every_capacity_reason(reason: str) -> None:
    measurements = _REASON_MEASUREMENTS[reason]
    detail = closure_capacity_block(
        closure_target_id=TARGET_ID,
        reason=reason,
        measurements=dict(measurements),
    )
    assert (
        closure_capacity_block(
            closure_target_id=TARGET_ID,
            reason=reason,
            measurements=dict(measurements),
        )
        == detail
    ), "the capacity block record is not deterministic"

    parsed = parse_closure_capacity_block(detail)

    assert parsed == {
        "record_kind": CAPACITY_BLOCK_RECORD_KIND,
        "schema_version": 1,
        "closure_target_id": TARGET_ID,
        "reason": reason,
        "operator_required": True,
        "measurements": measurements,
    }


# ---------------------------------------------------------------------------
# Refusals: every malformed shape parses to None.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param(None, id="none"),
        pytest.param(7, id="int"),
        pytest.param(b"{}", id="bytes"),
        pytest.param({"record_kind": CAPACITY_BLOCK_RECORD_KIND}, id="dict"),
        pytest.param(["closure_capacity_block"], id="list"),
        pytest.param("{not json", id="invalid_json"),
        pytest.param("", id="empty_string"),
        pytest.param("[1, 2]", id="json_list"),
        pytest.param("null", id="json_null"),
        pytest.param(_record(record_kind="closure_other"), id="wrong_record_kind"),
        pytest.param(_record(schema_version=2), id="schema_version_two"),
        pytest.param(_record(schema_version=0), id="schema_version_zero"),
        pytest.param(_record(schema_version="1"), id="schema_version_string"),
        pytest.param(_record(reason="not_a_capacity_reason"), id="unknown_reason"),
        pytest.param(_record(closure_target_id=_MISSING), id="missing_target_id"),
        pytest.param(_record(closure_target_id=7), id="int_target_id"),
        pytest.param(_record(closure_target_id=None), id="none_target_id"),
        pytest.param(_record(measurements=_MISSING), id="missing_measurements"),
        pytest.param(_record(measurements=[1, 2]), id="list_measurements"),
        pytest.param(_record(measurements="evidence"), id="str_measurements"),
        pytest.param(_record(measurements=None), id="none_measurements"),
    ],
)
def test_malformed_capacity_block_detail_parses_to_none(detail: object) -> None:
    assert parse_closure_capacity_block(detail) is None


# ---------------------------------------------------------------------------
# Exact types, and measurements that must substantiate the reason.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param(_record(schema_version=True), id="bool_schema_version"),
        pytest.param(_record(schema_version=1.0), id="float_schema_version"),
        pytest.param(_record(reason=[]), id="list_reason"),
        pytest.param(_record(reason={}), id="dict_reason"),
        pytest.param(_record(operator_required=False), id="operator_not_required"),
        pytest.param(_record(operator_required=_MISSING), id="missing_operator"),
        pytest.param(_record(operator_required=1), id="int_operator_required"),
    ],
)
def test_inexact_capacity_block_fields_parse_to_none(detail: str) -> None:
    assert parse_closure_capacity_block(detail) is None


@pytest.mark.parametrize("reason", sorted(CLOSURE_CAPACITY_REFUSALS))
@pytest.mark.parametrize(
    "change",
    ["equal", "below", "missing_measured", "missing_limit", "bool", "float", "zero"],
)
def test_measurements_that_do_not_substantiate_the_reason_parse_to_none(
    reason: str, change: str
) -> None:
    measurements: dict[str, Any] = dict(_REASON_MEASUREMENTS[reason])
    # Each entry lists the measured key first and its recorded limit second.
    measured_key, limit_key = measurements
    limit = measurements[limit_key]
    if change == "equal":
        measurements[measured_key] = limit
    elif change == "below":
        measurements[measured_key] = limit - 1
    elif change == "missing_measured":
        del measurements[measured_key]
    elif change == "missing_limit":
        del measurements[limit_key]
    elif change == "bool":
        measurements[measured_key] = True
        measurements[limit_key] = 0
    elif change == "float":
        measurements[measured_key] = float(measurements[measured_key])
    else:
        measurements[limit_key] = 0
    detail = _record(reason=reason, measurements=measurements)

    assert parse_closure_capacity_block(detail) is None


# ---------------------------------------------------------------------------
# Listing filter: built from one real kernel refusal.
# ---------------------------------------------------------------------------


def _capacity_refused_state() -> RuntimeState:
    """Durable state after one real over-capacity evaluate refusal."""
    fixture = co.opened_state(co.overflow_source(overflow=False))
    candidate = co.evaluate_candidate(fixture)
    decision = co.decide_candidate(fixture.state, candidate)
    assert decision.accepted is False, "over-capacity evaluation was accepted"
    assert decision.refusal is not None
    assert decision.refusal.reason == PAYLOAD_LIMIT
    return apply(fixture.state, decision)


def _only_block(state: RuntimeState) -> Any:
    blocks = closure_capacity_blocks(state)
    assert len(blocks) == 1, blocks
    return blocks[0]


def test_kernel_refusal_is_listed_with_unverified_measurements() -> None:
    durable = _capacity_refused_state()
    refusal = durable.refusals[-1]
    parsed = parse_closure_capacity_block(refusal.detail)
    assert parsed is not None, refusal.detail

    block = _only_block(durable)

    assert block.reason == PAYLOAD_LIMIT
    assert block.input_id == refusal.input_id
    assert block.refusal_record_id == refusal.record_id
    assert block.closure_target_id == parsed["closure_target_id"]
    assert block.detail_status == CAPACITY_DETAIL_UNVERIFIED
    assert block.measurements is None
    assert (
        block.selected_capacity_limits["request_payload_byte_limit"] == co.REQUEST_LIMIT
    )
    assert block.freshness_status == "unknown"


def _with_detail(state: RuntimeState, detail: str | None) -> RuntimeState:
    return replace(
        state,
        refusals=tuple(replace(entry, detail=detail) for entry in state.refusals),
    )


def _with_measurements(state: RuntimeState, **changes: object) -> RuntimeState:
    record = json.loads(str(state.refusals[-1].detail))
    record["measurements"].update(changes)
    return _with_detail(state, json.dumps(record))


def _assert_hold_kept(block: Any, genuine: Any, detail_status: str) -> None:
    assert (block.closure_target_id, block.input_id, block.reason) == (
        genuine.closure_target_id,
        genuine.input_id,
        genuine.reason,
    )
    assert block.refusal_record_id == genuine.refusal_record_id
    assert block.detail_status == detail_status
    assert block.measurements is None


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param("not a capacity block", id="plain_text"),
        pytest.param('{"record_kind":"closure_other"}', id="other_record_kind"),
        pytest.param(_record(), id="foreign_target"),
        pytest.param(
            _record(
                reason="closure_evidence_item_limit_exceeded",
                measurements={"evidence_items": 97, "evidence_item_limit": 96},
            ),
            id="foreign_target_and_reason",
        ),
    ],
)
def test_unparseable_or_foreign_detail_keeps_the_hold_visible_as_invalid(
    detail: str,
) -> None:
    durable = _capacity_refused_state()
    genuine = _only_block(durable)

    _assert_hold_kept(
        _only_block(_with_detail(durable, detail)), genuine, CAPACITY_DETAIL_INVALID
    )


def test_missing_detail_keeps_the_hold_visible_as_absent() -> None:
    durable = _capacity_refused_state()
    genuine = _only_block(durable)

    _assert_hold_kept(
        _only_block(_with_detail(durable, None)), genuine, CAPACITY_DETAIL_ABSENT
    )


def test_missing_refusal_record_keeps_the_hold_visible_as_absent() -> None:
    durable = _capacity_refused_state()
    genuine = _only_block(durable)

    block = _only_block(replace(durable, refusals=()))

    assert block.refusal_record_id is None
    assert (block.closure_target_id, block.input_id, block.reason) == (
        genuine.closure_target_id,
        genuine.input_id,
        genuine.reason,
    )
    assert block.detail_status == CAPACITY_DETAIL_ABSENT
    assert block.measurements is None


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"request_bytes": 0}, id="measured_zero"),
        pytest.param({"request_payload_byte_limit": -1}, id="limit_negative"),
        pytest.param({"request_payload_byte_limit": 1 << 30}, id="limit_not_selected"),
        pytest.param({"evidence_item_limit": 7}, id="item_limit_not_selected"),
        pytest.param({"evidence_items": -1}, id="evidence_items_negative"),
        pytest.param({"request_bytes": True}, id="measured_bool"),
        pytest.param({"snapshot_bytes": 1}, id="overflow_key_without_overflow"),
    ],
)
def test_forged_measurements_keep_the_hold_visible_as_invalid(
    changes: dict[str, object],
) -> None:
    durable = _capacity_refused_state()
    genuine = _only_block(durable)

    _assert_hold_kept(
        _only_block(_with_measurements(durable, **changes)),
        genuine,
        CAPACITY_DETAIL_INVALID,
    )


def test_detail_reason_other_than_the_receipt_reason_is_invalid() -> None:
    durable = _capacity_refused_state()
    genuine = _only_block(durable)
    record = json.loads(str(durable.refusals[-1].detail))
    record["reason"] = "closure_request_overflow_projection_limit_exceeded"

    _assert_hold_kept(
        _only_block(_with_detail(durable, json.dumps(record))),
        genuine,
        CAPACITY_DETAIL_INVALID,
    )


def test_coherent_detail_without_measurements_is_unmeasured() -> None:
    durable = _capacity_refused_state()
    genuine = _only_block(durable)
    record = json.loads(str(durable.refusals[-1].detail))
    record["measurements"] = {}

    _assert_hold_kept(
        _only_block(_with_detail(durable, json.dumps(record))),
        genuine,
        CAPACITY_DETAIL_UNMEASURED,
    )


def test_refusal_with_accepted_receipt_is_not_listed() -> None:
    durable = _capacity_refused_state()
    block = _only_block(durable)
    receipt = durable.receipts[block.input_id]

    accepted = replace(
        durable,
        receipts={
            **durable.receipts,
            block.input_id: replace(receipt, accepted=True, refusal_reason=None),
        },
    )

    assert closure_capacity_blocks(accepted) == ()


def test_refusal_with_non_capacity_receipt_reason_is_not_listed() -> None:
    durable = _capacity_refused_state()
    block = _only_block(durable)
    receipt = durable.receipts[block.input_id]

    non_capacity = replace(
        durable,
        receipts={
            **durable.receipts,
            block.input_id: replace(receipt, refusal_reason=NON_CAPACITY_REASON),
        },
    )

    assert NON_CAPACITY_REASON not in CLOSURE_CAPACITY_REFUSALS
    assert closure_capacity_blocks(non_capacity) == ()


def test_capacity_block_on_closed_target_is_not_listed() -> None:
    durable = _capacity_refused_state()
    block = _only_block(durable)
    target = durable.closure_targets[block.closure_target_id]
    assert target.status == "open"

    closed = replace(
        durable,
        closure_targets={
            **durable.closure_targets,
            block.closure_target_id: replace(target, status="closed"),
        },
    )

    assert closure_capacity_blocks(closed) == ()


def test_receipt_that_authenticates_no_target_is_listed_unlinked() -> None:
    durable = _capacity_refused_state()
    genuine = _only_block(durable)

    orphaned = replace(durable, closure_targets={})

    block = _only_block(orphaned)
    assert block.closure_target_id is None
    assert (block.input_id, block.reason) == (genuine.input_id, genuine.reason)
    assert block.detail_status == CAPACITY_DETAIL_INVALID
    assert block.measurements is None


def test_receipt_with_a_rewritten_payload_digest_is_listed_unlinked() -> None:
    durable = _capacity_refused_state()
    genuine = _only_block(durable)
    receipt = durable.receipts[genuine.input_id]
    forged_ref = replace(receipt.receipt_ref, input_payload_digest="sha256:" + "0" * 64)

    forged = replace(
        durable,
        receipts={
            **durable.receipts,
            genuine.input_id: replace(receipt, receipt_ref=forged_ref),
        },
    )

    block = _only_block(forged)
    assert block.closure_target_id is None
    assert block.input_id == genuine.input_id


@pytest.mark.parametrize(
    "measured_key", ["request_bytes", "evidence_items", "snapshot_bytes", "page_count"]
)
def test_coherent_positive_forged_sizes_are_never_verified(measured_key: str) -> None:
    fixture = co.opened_state(co.overflow_source(max_snapshot_bytes=9000))
    candidate = co.evaluate_candidate(fixture)
    durable = apply(fixture.state, co.decide_candidate(fixture.state, candidate))
    forged = _with_measurements(durable, **{measured_key: 1 << 40})

    block = _only_block(forged)

    assert block.detail_status == "unverified"
    assert block.measurements is None
    assert block.selected_capacity_limits["max_snapshot_bytes"] == 9000
    assert block.freshness_status == "unknown"


def test_schema_two_detail_keeps_receipt_hold_without_measured_truth() -> None:
    durable = _capacity_refused_state()
    record = json.loads(str(durable.refusals[-1].detail))
    record["schema_version"] = 2
    block = _only_block(_with_detail(durable, json.dumps(record)))
    assert block.detail_status == CAPACITY_DETAIL_INVALID
    assert block.measurements is None
    assert block.freshness_status == "unknown"
