"""Read-only runtime event normalization and SSE helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from millrace_ai.events import read_recent_runtime_events
from pydantic import TypeAdapter

from millrace_web.models import EventSummary, WorkspaceRef

_EVENT_ADAPTER = TypeAdapter(EventSummary)
_RECENT_EVENT_READ_FLOOR = 200


def list_event_summaries(workspace: WorkspaceRef, *, limit: int = 50) -> tuple[EventSummary, ...]:
    read_limit = max(_RECENT_EVENT_READ_FLOOR, limit * 4)
    events = [_event_summary(workspace, event) for event in read_recent_runtime_events(workspace.path, limit=read_limit)]
    events.sort(key=lambda event: (event.workspace_id, event.occurred_at))
    deduped = _suppress_repeated_idle_noise(events)
    return tuple(deduped[-limit:])


async def sse_events(workspaces: tuple[WorkspaceRef, ...], *, poll_interval_seconds: float) -> AsyncIterator[str]:
    previous_payload = ""
    while True:
        events: list[EventSummary] = []
        for workspace in workspaces:
            events.extend(list_event_summaries(workspace, limit=20))
        events.sort(key=lambda event: (event.workspace_id, event.occurred_at))
        events = _suppress_repeated_idle_noise(events)
        payload = json.dumps([event.model_dump(mode="json") for event in events])
        if payload != previous_payload:
            previous_payload = payload
            yield f"event: millrace_events\ndata: {payload}\n\n"
        await asyncio.sleep(poll_interval_seconds)


def _event_summary(workspace: WorkspaceRef, event: object) -> EventSummary:
    data = getattr(event, "data")
    return EventSummary(
        workspace_id=workspace.id,
        event_type=getattr(event, "event_type"),
        occurred_at=getattr(event, "occurred_at"),
        plane=_optional_str(data.get("plane")),
        stage=_optional_str(data.get("stage") or data.get("node") or data.get("stage_kind")),
        work_item_id=_optional_str(data.get("work_item_id") or data.get("task_id") or data.get("spec_id")),
        run_id=_optional_str(data.get("run_id")),
        details=_details(data),
        artifact_path=_optional_str(data.get("artifact_path") or data.get("stage_result_path")),
    )


def _suppress_repeated_idle_noise(events: list[EventSummary]) -> list[EventSummary]:
    deduped: list[EventSummary] = []
    last_idle_signature: tuple[str, str, str] | None = None
    for event in events:
        idle_signature = (
            event.workspace_id,
            event.event_type,
            event.details,
        )
        if event.event_type in {"idle", "runtime_tick_idle"} and event.details == "reason=no_work":
            if idle_signature == last_idle_signature:
                continue
            last_idle_signature = idle_signature
        else:
            last_idle_signature = None
        deduped.append(event)
    return deduped


def _details(data: dict[str, object]) -> str:
    if "reason" in data:
        parts = [f"reason={data['reason']}"]
        parts.extend(_terminal_detail_parts(data))
        return " ".join(parts)
    preferred = ("terminal_result", "next_stage", "compiled_plan_id", "artifact_path")
    parts = [f"{key}={data[key]}" for key in preferred if key in data and data[key] is not None]
    parts.extend(_terminal_detail_parts(data))
    return " ".join(parts)


def _terminal_detail_parts(data: dict[str, object]) -> list[str]:
    preferred = (
        "terminal_state_id",
        "terminal_action_id",
        "terminal_action_router_consequence",
        "lifecycle_mutation_plan_id",
        "lifecycle_action_id",
        "terminal_writes_status",
        "runtime_operation_id",
        "failure_class",
    )
    parts = [f"{key}={data[key]}" for key in preferred if data.get(key) is not None]
    if data.get("create_incident") is True:
        parts.append("create_incident=true")
    return parts


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
