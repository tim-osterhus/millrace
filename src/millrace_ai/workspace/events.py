"""Append-only runtime event helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from .paths import WorkspacePaths, workspace_paths

EVENT_LOG_FILENAME = "runtime_events.jsonl"


@dataclass(frozen=True, slots=True)
class RuntimeEventRecord:
    """A single append-only runtime event."""

    event_type: str
    occurred_at: datetime
    data: dict[str, JsonValue]


def write_runtime_event(
    target: WorkspacePaths | Path | str,
    *,
    event_type: str,
    data: dict[str, JsonValue] | None = None,
    occurred_at: datetime | None = None,
) -> Path:
    """Append one deterministic runtime event record."""

    paths = _resolve_paths(target)
    log_path = paths.logs_dir / EVENT_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned_event_type = event_type.strip()
    if not cleaned_event_type:
        raise ValueError("runtime event payload is missing event_type")
    event_occurred_at = occurred_at or datetime.now(timezone.utc)
    if event_occurred_at.tzinfo is None or event_occurred_at.utcoffset() is None:
        raise ValueError("runtime event payload has invalid occurred_at")
    payload = {
        "schema_version": "1.0",
        "kind": "runtime_event",
        "event_type": cleaned_event_type,
        "occurred_at": event_occurred_at.isoformat(),
        "data": data or {},
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return log_path


def read_runtime_events(target: WorkspacePaths | Path | str) -> tuple[RuntimeEventRecord, ...]:
    """Read runtime events in file order."""

    paths = _resolve_paths(target)
    log_path = paths.logs_dir / EVENT_LOG_FILENAME
    if not log_path.exists():
        return ()

    records: list[RuntimeEventRecord] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("runtime event log contains malformed JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("runtime event payload must be an object")
        records.append(_parse_event_record(payload))
    return tuple(records)


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


def _parse_event_record(payload: dict[str, Any]) -> RuntimeEventRecord:
    schema_version = payload.get("schema_version")
    kind = payload.get("kind")
    event_type = payload.get("event_type")
    occurred_at = payload.get("occurred_at")
    data = payload.get("data", {})

    if schema_version != "1.0":
        raise ValueError("runtime event payload has unsupported schema_version")
    if kind != "runtime_event":
        raise ValueError("runtime event payload has invalid kind")
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("runtime event payload is missing event_type")
    if not isinstance(occurred_at, str) or not occurred_at.strip():
        raise ValueError("runtime event payload is missing occurred_at")
    if not isinstance(data, dict):
        raise ValueError("runtime event payload data must be an object")

    try:
        parsed_occurred_at = datetime.fromisoformat(occurred_at)
    except ValueError as exc:
        raise ValueError("runtime event payload has invalid occurred_at") from exc
    if parsed_occurred_at.tzinfo is None or parsed_occurred_at.utcoffset() is None:
        raise ValueError("runtime event payload has invalid occurred_at")

    return RuntimeEventRecord(
        event_type=event_type,
        occurred_at=parsed_occurred_at,
        data=data,
    )


__all__ = ["EVENT_LOG_FILENAME", "RuntimeEventRecord", "read_runtime_events", "write_runtime_event"]
