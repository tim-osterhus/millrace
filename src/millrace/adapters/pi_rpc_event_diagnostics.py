"""Bounded structural history for native Pi RPC events."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

__all__ = ["PiEventHistory"]

_MAX_INT64 = 2**63 - 1
_MAX_RECENT_EVENTS = 8
_MAX_SNAPSHOT_BYTES = 4096

_KNOWN_EVENT_TYPES = frozenset(
    {
        "agent_start",
        "turn_start",
        "message_start",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
        "agent_settled",
        "auto_retry_start",
        "auto_retry_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
        "compaction_start",
        "compaction_end",
        "summarization_retry_scheduled",
        "summarization_retry_attempt_start",
        "summarization_retry_finished",
        "queue_update",
        "thinking_level_changed",
        "entry_appended",
        "session_info_changed",
    }
)
_RETRY_START_FIELDS = ("attempt", "maxAttempts", "delayMs", "errorMessage")
_EVENT_REQUIRED_FIELDS = {
    "auto_retry_start": _RETRY_START_FIELDS,
    "auto_retry_end": ("attempt", "success"),
    "compaction_start": ("reason",),
    "compaction_end": ("reason", "aborted", "willRetry"),
    "summarization_retry_scheduled": _RETRY_START_FIELDS,
    "summarization_retry_attempt_start": ("source",),
}
_NUMERIC_FIELDS = frozenset({"attempt", "maxAttempts", "delayMs"})
_BOOLEAN_FIELDS = frozenset({"success", "aborted", "willRetry"})
_REASONS = frozenset({"manual", "threshold", "overflow"})
_SOURCES = frozenset({"branchSummary", "compaction"})
_STATE_BOOLEAN_FIELDS = (
    "run",
    "turn",
    "message_open",
    "tools_active",
    "continuation",
    "will_retry",
    "auxiliary",
    "overflow_retry",
    "summary_waiting",
)
_STATE_COUNT_FIELDS = (
    "runs",
    "users",
    "settled",
    "retry_attempt",
    "retry_max",
    "summary_attempt",
    "summary_max",
)
_RECOVERY_STATES = frozenset({"none", "retry", "compaction", "summary_retry"})


def _safe_event_type(event: dict[str, Any]) -> tuple[str, str]:
    if type(event) is not dict or "type" not in event:
        return "invalid", "invalid"
    kind = event["type"]
    if type(kind) is not str or not kind:
        return "invalid", "invalid"
    if kind not in _KNOWN_EVENT_TYPES:
        return "unknown", "unknown"
    return kind, "valid"


def _safe_state(
    state: dict[str, bool | int | str | None],
) -> tuple[dict[str, object], str]:
    safe: dict[str, object] = {}
    invalid = False
    if type(state) is not dict:
        return safe, "invalid"

    for field in _STATE_BOOLEAN_FIELDS:
        if field not in state:
            continue
        value = state[field]
        if type(value) is bool:
            safe[field] = value
        else:
            invalid = True
    for field in _STATE_COUNT_FIELDS:
        if field not in state:
            continue
        value = state[field]
        if type(value) is int and 0 <= value <= _MAX_INT64:
            safe[field] = value
        else:
            invalid = True
    if "recovery" in state:
        value = state["recovery"]
        if value is None:
            safe["recovery"] = "none"
        elif type(value) is str and value in _RECOVERY_STATES:
            safe["recovery"] = value
        else:
            invalid = True
    return safe, "invalid" if invalid else "valid"


def _safe_fields(
    kind: str, event: dict[str, Any]
) -> tuple[dict[str, object], list[str]]:
    fields: dict[str, object] = {}
    invalid: list[str] = []
    required = _EVENT_REQUIRED_FIELDS.get(kind, ())
    for field in required:
        if field not in event:
            invalid.append(field)
            continue
        value = event[field]
        if field in _NUMERIC_FIELDS:
            if type(value) is int and 0 <= value <= _MAX_INT64:
                fields[field] = value
            else:
                invalid.append(field)
        elif field in _BOOLEAN_FIELDS:
            if type(value) is bool:
                fields[field] = value
            else:
                invalid.append(field)
        elif field == "reason":
            if type(value) is str and value in _REASONS:
                fields[field] = value
            else:
                invalid.append(field)
        elif field == "source":
            if type(value) is str and value in _SOURCES:
                fields[field] = value
            else:
                invalid.append(field)
        elif field == "errorMessage":
            if type(value) is str:
                fields["error_message_present"] = True
            else:
                invalid.append(field)
    if kind == "compaction_end":
        fields["error_message_present"] = "errorMessage" in event
        error_message = event.get("errorMessage")
        if "errorMessage" in event and type(error_message) is not str:
            invalid.append("errorMessage")
        result_present = "result" in event
        result_is_object = type(event.get("result")) is dict
        fields["result_present"] = result_present
        fields["result_is_object"] = result_is_object
        if result_present and not result_is_object:
            invalid.append("result")
    if kind == "summarization_retry_attempt_start" and "reason" in event:
        reason = event["reason"]
        if type(reason) is str and reason in _REASONS:
            fields["reason"] = reason
        else:
            invalid.append("reason")
    return fields, invalid


def _clone_event(record: dict[str, object]) -> dict[str, object]:
    fields = record["fields"]
    state = record["state"]
    clone: dict[str, object] = {
        "type": record["type"],
        "status": record["status"],
        "fields": fields.copy() if type(fields) is dict else {},
        "state": state.copy() if type(state) is dict else {},
        "state_status": record["state_status"],
    }
    invalid_fields = record.get("invalid_fields")
    if type(invalid_fields) is list and invalid_fields:
        clone["invalid_fields"] = invalid_fields.copy()
    return clone


class PiEventHistory:
    """Keep a small allowlisted event window and the latest structural event.

    A record's ``status`` describes only the safe projection's structural
    shape; it does not mean ``PiLifecycle`` accepted the event.
    """

    __slots__ = ("_event_count", "_latest", "_recent")

    def __init__(self) -> None:
        self._event_count = 0
        self._recent: deque[dict[str, object]] = deque(maxlen=_MAX_RECENT_EVENTS)
        self._latest: dict[str, object] | None = None

    def record(
        self,
        event: dict[str, Any],
        state: dict[str, bool | int | str | None],
    ) -> None:
        """Retain only fixed event fields and the supplied pre-event state."""
        kind, type_status = _safe_event_type(event)
        safe_state, state_status = _safe_state(state)
        if type(event) is dict:
            safe_fields, invalid_fields = _safe_fields(kind, event)
        else:
            safe_fields, invalid_fields = {}, []
        status = type_status
        if type_status == "valid" and invalid_fields:
            status = "invalid"
        record: dict[str, object] = {
            "type": kind,
            "status": status,
            "fields": safe_fields,
            "state": safe_state,
            "state_status": state_status,
        }
        if invalid_fields:
            record["invalid_fields"] = invalid_fields
        self._latest = record
        self._recent.append(record)
        self._event_count = min(_MAX_INT64, self._event_count + 1)

    def snapshot(self) -> dict[str, object]:
        """Return a detached, JSON-safe snapshot no larger than 4 KiB."""
        latest = _clone_event(self._latest) if self._latest is not None else None
        recent = [_clone_event(record) for record in self._recent]
        snapshot: dict[str, object] = {
            "schema": 1,
            "event_count": self._event_count,
            "recent_events": recent,
            "latest_event": latest,
        }
        while (
            len(
                json.dumps(snapshot, ensure_ascii=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            > _MAX_SNAPSHOT_BYTES
            and recent
        ):
            recent.pop(0)
        return snapshot
