"""Recovery event history retains bounded structure without native payloads."""

import json

import pytest

from millrace.adapters.pi_rpc_event_diagnostics import PiEventHistory


def test_recovery_event_snapshot_keeps_exact_type_and_pre_event_state():
    history = PiEventHistory()
    state = {
        "run": False,
        "turn": False,
        "message_open": False,
        "tools_active": False,
        "continuation": False,
        "will_retry": False,
        "auxiliary": False,
        "runs": 1,
        "users": 1,
        "settled": 0,
        "recovery": "none",
    }
    history.record(
        {
            "type": "compaction_start",
            "reason": "threshold",
            "prompt": "secret prompt",
        },
        state,
    )

    snapshot = history.snapshot()
    latest = snapshot["latest_event"]
    assert isinstance(latest, dict)
    assert latest["type"] == "compaction_start"
    assert latest["status"] == "valid"
    assert latest["fields"] == {"reason": "threshold"}
    assert latest["state"]["run"] is False
    assert latest["state"]["runs"] == 1
    assert "prompt" not in json.dumps(snapshot)
    assert "secret prompt" not in json.dumps(snapshot)


@pytest.mark.parametrize(
    "kind,fields",
    [
        (
            "auto_retry_start",
            {"attempt": 1, "maxAttempts": 4, "delayMs": 10, "errorMessage": "x"},
        ),
        ("auto_retry_end", {"attempt": 2, "success": False}),
        (
            "compaction_end",
            {"reason": "overflow", "aborted": True, "willRetry": False},
        ),
        (
            "summarization_retry_attempt_start",
            {"source": "branchSummary", "reason": "threshold"},
        ),
    ],
)
def test_known_retry_and_compaction_shapes_are_structurally_retained(kind, fields):
    history = PiEventHistory()
    history.record({"type": kind, **fields}, {})

    latest = history.snapshot()["latest_event"]
    assert isinstance(latest, dict)
    assert latest["type"] == kind
    assert latest["status"] == "valid"
    expected_fields = {
        key: value for key, value in fields.items() if key != "errorMessage"
    }
    if "errorMessage" in fields:
        expected_fields["error_message_present"] = True
    if kind == "compaction_end":
        expected_fields.update(
            error_message_present=False,
            result_present=False,
            result_is_object=False,
        )
    assert latest["fields"] == expected_fields


@pytest.mark.parametrize(
    "kind",
    ["tool_execution_start", "tool_execution_update", "tool_execution_end"],
)
def test_tool_execution_types_are_fixed_and_do_not_retain_payloads(kind):
    history = PiEventHistory()
    history.record(
        {"type": kind, "command": "private command", "result": "private result"},
        {},
    )

    latest = history.snapshot()["latest_event"]
    assert latest["type"] == kind
    assert latest["status"] == "valid"
    assert latest["fields"] == {}
    assert "private command" not in json.dumps(latest)
    assert "private result" not in json.dumps(latest)


def test_projection_status_describes_structure_not_lifecycle_acceptance():
    history = PiEventHistory()
    history.record(
        {"type": "agent_start", "prompt": "private prompt"},
        {"run": True},
    )

    latest = history.snapshot()["latest_event"]
    assert latest["status"] == "valid"
    assert latest["state"] == {"run": True}
    assert "private prompt" not in json.dumps(latest)


def test_compaction_end_retains_only_optional_field_presence_and_shape():
    history = PiEventHistory()
    history.record(
        {
            "type": "compaction_end",
            "reason": "overflow",
            "aborted": False,
            "willRetry": True,
            "errorMessage": "private compaction error",
            "result": {
                "summary": "private summary",
                "details": "private details",
                "tokensBefore": 123,
            },
        },
        {},
    )
    latest = history.snapshot()["latest_event"]
    assert latest["status"] == "valid"
    assert latest["fields"] == {
        "reason": "overflow",
        "aborted": False,
        "willRetry": True,
        "error_message_present": True,
        "result_present": True,
        "result_is_object": True,
    }
    serialized = json.dumps(latest)
    assert "private compaction error" not in serialized
    assert "private summary" not in serialized
    assert "private details" not in serialized


@pytest.mark.parametrize(
    "error_message,result,expected_fields",
    [
        (
            "x" * 200_000,
            {"summary": "secret-summary-" * 50_000},
            {
                "error_message_present": True,
                "result_present": True,
                "result_is_object": True,
            },
        ),
        (
            7,
            ["secret-result-" * 50_000],
            {
                "error_message_present": True,
                "result_present": True,
                "result_is_object": False,
            },
        ),
    ],
    ids=["large-valid-result", "malformed-result"],
)
def test_compaction_optional_fields_are_safe_even_when_malformed_or_huge(
    error_message, result, expected_fields
):
    history = PiEventHistory()
    history.record(
        {
            "type": "compaction_end",
            "reason": "manual",
            "aborted": False,
            "willRetry": False,
            "errorMessage": error_message,
            "result": result,
        },
        {},
    )

    snapshot = history.snapshot()
    latest = snapshot["latest_event"]
    expected_fields = {
        "reason": "manual",
        "aborted": False,
        "willRetry": False,
        **expected_fields,
    }
    assert latest["fields"] == expected_fields
    assert latest["status"] == (
        "valid" if type(error_message) is str and type(result) is dict else "invalid"
    )
    raw = json.dumps(snapshot, ensure_ascii=True, separators=(",", ":"))
    assert len(raw.encode("utf-8")) <= 4096
    assert "secret-summary" not in raw
    assert "secret-result" not in raw
    assert "x" * 1000 not in raw


def test_summary_retry_reason_is_fixed_optional_and_payload_free():
    history = PiEventHistory()
    history.record(
        {
            "type": "summarization_retry_attempt_start",
            "source": "compaction",
            "reason": "threshold",
            "summary": "private summary",
        },
        {},
    )
    history.record(
        {
            "type": "summarization_retry_attempt_start",
            "source": "compaction",
            "reason": "private-reason",
        },
        {},
    )

    first, latest = history.snapshot()["recent_events"]
    assert first["status"] == "valid"
    assert first["fields"] == {"source": "compaction", "reason": "threshold"}
    assert latest["status"] == "invalid"
    assert latest["fields"] == {"source": "compaction"}
    assert latest["invalid_fields"] == ["reason"]
    serialized = json.dumps(history.snapshot())
    assert "private summary" not in serialized
    assert "private-reason" not in serialized


def test_history_evicts_old_events_but_keeps_latest_triggering_event():
    history = PiEventHistory()
    for index in range(12):
        history.record({"type": "turn_start", "index": index}, {"runs": index})

    snapshot = history.snapshot()
    recent = snapshot["recent_events"]
    latest = snapshot["latest_event"]
    assert snapshot["event_count"] == 12
    assert isinstance(recent, list)
    assert len(recent) == 8
    assert recent[0]["state"]["runs"] == 4
    assert recent[-1] == latest
    assert latest["state"]["runs"] == 11


def test_snapshot_mutation_does_not_change_retained_history():
    history = PiEventHistory()
    history.record(
        {"type": "auto_retry_end", "attempt": 1, "success": True}, {}
    )
    snapshot = history.snapshot()
    snapshot["latest_event"]["fields"]["attempt"] = 999
    snapshot["recent_events"][0]["fields"]["success"] = False

    retained = history.snapshot()
    assert retained["latest_event"]["fields"] == {"attempt": 1, "success": True}
    assert retained["recent_events"][0]["fields"] == {
        "attempt": 1,
        "success": True,
    }


def test_recovery_state_counters_are_strict_bounded_and_retained_after_eviction():
    max_int64 = 2**63 - 1
    state = {
        "run": False,
        "turn": False,
        "message_open": False,
        "tools_active": False,
        "continuation": True,
        "will_retry": True,
        "auxiliary": True,
        "overflow_retry": False,
        "summary_waiting": True,
        "runs": max_int64,
        "users": max_int64,
        "settled": max_int64,
        "retry_attempt": max_int64,
        "retry_max": max_int64,
        "summary_attempt": max_int64,
        "summary_max": max_int64,
        "recovery": "summary_retry",
    }
    history = PiEventHistory()
    for _ in range(16):
        history.record({"type": "turn_start"}, state)

    snapshot = history.snapshot()
    assert snapshot["event_count"] == 16
    assert 1 <= len(snapshot["recent_events"]) <= 8
    assert snapshot["recent_events"][-1] == snapshot["latest_event"]
    assert snapshot["recent_events"][-1]["state"] == state
    assert snapshot["latest_event"]["state"] == state
    assert len(
        json.dumps(snapshot, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ) <= 4096


def test_recovery_state_counter_and_flag_types_are_not_coerced():
    history = PiEventHistory()
    history.record(
        {"type": "turn_start"},
        {
            "retry_attempt": True,
            "retry_max": 2**63,
            "summary_attempt": -1,
            "summary_max": 3,
            "overflow_retry": 1,
            "summary_waiting": False,
        },
    )

    latest = history.snapshot()["latest_event"]
    assert latest["state"] == {"summary_max": 3, "summary_waiting": False}
    assert latest["state_status"] == "invalid"


def test_unknown_and_malformed_event_types_never_retain_arbitrary_text():
    history = PiEventHistory()
    secret_type = "private-event-type-secret" * 100
    history.record({"type": secret_type, "payload": "private payload"}, {})
    history.record({"type": 7, "payload": "private payload"}, {})

    snapshot = history.snapshot()
    assert snapshot["recent_events"][0]["type"] == "unknown"
    assert snapshot["recent_events"][0]["status"] == "unknown"
    assert snapshot["latest_event"]["type"] == "invalid"
    assert snapshot["latest_event"]["status"] == "invalid"
    serialized = json.dumps(snapshot, ensure_ascii=True)
    assert "private-event-type-secret" not in serialized
    assert "private payload" not in serialized


def test_malformed_fields_are_omitted_and_marked_invalid():
    history = PiEventHistory()
    history.record(
        {
            "type": "auto_retry_start",
            "attempt": True,
            "maxAttempts": 2**63,
            "delayMs": -1,
            "errorMessage": "private error detail",
        },
        {"run": 1, "turn": False, "runs": True, "unknown": "secret"},
    )

    latest = history.snapshot()["latest_event"]
    assert latest["type"] == "auto_retry_start"
    assert latest["status"] == "invalid"
    assert latest["fields"] == {"error_message_present": True}
    assert latest["invalid_fields"] == ["attempt", "maxAttempts", "delayMs"]
    assert latest["state"] == {"turn": False}
    assert latest["state_status"] == "invalid"
    serialized = json.dumps(latest, ensure_ascii=True)
    assert "private error detail" not in serialized
    assert "secret" not in serialized


@pytest.mark.parametrize(
    "fields",
    [
        {"attempt": True, "maxAttempts": 2, "delayMs": 0, "errorMessage": "x"},
        {"attempt": 2**63, "maxAttempts": 2, "delayMs": 0, "errorMessage": "x"},
        {"attempt": 0, "maxAttempts": 2, "delayMs": -1, "errorMessage": "x"},
        {"attempt": 0, "maxAttempts": 2, "delayMs": 0, "errorMessage": 1},
    ],
)
def test_retry_numbers_require_nonnegative_int64_and_error_text_is_only_shape_checked(
    fields,
):
    history = PiEventHistory()
    history.record({"type": "auto_retry_start", **fields}, {})

    latest = history.snapshot()["latest_event"]
    assert latest["status"] == "invalid"
    assert "errorMessage" not in latest["fields"]


def test_bool_event_fields_are_exact_bool_and_enums_are_fixed():
    history = PiEventHistory()
    history.record(
        {"type": "auto_retry_end", "attempt": 1, "success": 1}, {}
    )
    history.record(
        {"type": "compaction_start", "reason": "secret-reason"}, {}
    )

    first, latest = history.snapshot()["recent_events"]
    assert first["status"] == "invalid"
    assert first["fields"] == {"attempt": 1}
    assert latest["status"] == "invalid"
    assert latest["fields"] == {}
    assert "secret-reason" not in json.dumps(history.snapshot())


def test_hostile_payloads_and_state_stay_within_snapshot_byte_cap():
    history = PiEventHistory()
    secret = "secret-雪-credential-\"\\" * 200_000
    for _ in range(12):
        history.record(
            {
                "type": "auto_retry_start",
                "attempt": secret,
                "maxAttempts": 2,
                "delayMs": 0,
                "errorMessage": secret,
                "prompt": secret,
                "toolPayload": secret,
            },
            {"run": False, "recovery": secret, "extra": secret},
        )

    raw = json.dumps(history.snapshot(), ensure_ascii=True, separators=(",", ":"))
    assert len(raw.encode("utf-8")) <= 4096
    assert "secret" not in raw
    assert "credential" not in raw
    assert "\u96ea" not in raw
