"""Event bus and durable subscribers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
import json

from pydantic import ConfigDict, Field, field_validator

from .contracts import ContractModel
from .markdown import write_text_atomic
from .paths import RuntimePaths, format_historylog_entry_name


class EventType(str, Enum):
    ENGINE_STARTED = "engine.started"
    ENGINE_STOPPED = "engine.stopped"
    ENGINE_PAUSED = "engine.paused"
    ENGINE_RESUMED = "engine.resumed"
    CONTROL_COMMAND_RECEIVED = "control.command.received"
    CONTROL_COMMAND_APPLIED = "control.command.applied"
    TASK_PROMOTED = "execution.task.promoted"
    TASK_ARCHIVED = "execution.task.archived"
    TASK_QUARANTINED = "execution.task.quarantined"
    STAGE_STARTED = "execution.stage.started"
    STAGE_COMPLETED = "execution.stage.completed"
    STAGE_FAILED = "execution.stage.failed"
    STATUS_CHANGED = "execution.status.changed"
    QUICKFIX_ATTEMPT = "execution.quickfix.attempt"
    QUICKFIX_EXHAUSTED = "execution.quickfix.exhausted"
    BACKLOG_EMPTY = "execution.backlog.empty"
    CONFIG_CHANGED = "config.changed"
    CONFIG_APPLIED = "config.applied"
    NEEDS_RESEARCH = "handoff.needs_research"
    BACKLOG_EMPTY_AUDIT = "handoff.backlog_empty_audit"
    AUDIT_REQUESTED = "handoff.audit_requested"
    BACKLOG_REPOPULATED = "handoff.backlog_repopulated"
    RESEARCH_RECEIVED = "research.received"
    RESEARCH_DEFERRED = "research.deferred"
    RESEARCH_SCAN_COMPLETED = "research.scan.completed"
    RESEARCH_MODE_SELECTED = "research.mode.selected"
    RESEARCH_DISPATCH_COMPILED = "research.dispatch.compiled"
    RESEARCH_CHECKPOINT_RESUMED = "research.checkpoint.resumed"
    RESEARCH_IDLE = "research.idle"
    RESEARCH_BLOCKED = "research.blocked"
    RESEARCH_RETRY_SCHEDULED = "research.retry.scheduled"
    RESEARCH_LOCK_ACQUIRED = "research.lock.acquired"
    RESEARCH_LOCK_RELEASED = "research.lock.released"
    IDEA_SUBMITTED = "handoff.idea_submitted"


class EventSource(str, Enum):
    ENGINE = "engine"
    EXECUTION = "execution"
    RESEARCH = "research"
    ADAPTER = "adapter"
    CONTROL = "control"


_RESEARCH_HANDOFF_EVENT_TYPES = frozenset(
    {
        EventType.NEEDS_RESEARCH,
        EventType.BACKLOG_EMPTY_AUDIT,
        EventType.AUDIT_REQUESTED,
        EventType.BACKLOG_REPOPULATED,
        EventType.IDEA_SUBMITTED,
    }
)


def is_research_event_type(event_type: EventType | str) -> bool:
    """Return True when one event belongs in research-focused history views."""

    try:
        normalized = event_type if isinstance(event_type, EventType) else EventType(str(event_type))
    except ValueError:
        return str(event_type).startswith("research.")
    return normalized.value.startswith("research.") or normalized in _RESEARCH_HANDOFF_EVENT_TYPES


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    return str(value)


class EventRecord(ContractModel):
    """Normalized runtime event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: EventType
    timestamp: datetime
    source: EventSource
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


def render_structured_event_line(
    *,
    timestamp: datetime,
    event_type: str,
    source: str,
    payload: dict[str, Any],
) -> str:
    """Render one structured event line in CLI log-feed shape."""

    moment = _normalize_datetime(timestamp)
    return (
        f"{moment.isoformat().replace('+00:00', 'Z')} "
        f"{event_type} "
        f"source={source} "
        f"payload={json.dumps(payload, sort_keys=True)}"
    )


def render_event_record_line(event: EventRecord) -> str:
    """Render one EventRecord in CLI log-feed shape."""

    return render_structured_event_line(
        timestamp=event.timestamp,
        event_type=event.type.value,
        source=event.source.value,
        payload=event.payload,
    )


class EventSubscriber(Protocol):
    """Simple subscriber protocol."""

    def handle(self, event: EventRecord) -> None:
        """Consume one event."""


class EventBus:
    """In-process event fanout."""

    def __init__(self, subscribers: list[EventSubscriber] | None = None) -> None:
        self.subscribers = list(subscribers or [])

    def subscribe(self, subscriber: EventSubscriber) -> None:
        """Register one subscriber."""

        self.subscribers.append(subscriber)

    def emit(
        self,
        event_type: EventType,
        *,
        source: EventSource,
        payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        """Emit one normalized event to all subscribers."""

        event = EventRecord.model_validate(
            {
                "type": event_type,
                "timestamp": datetime.now(timezone.utc),
                "source": source,
                "payload": _json_safe(payload or {}),
            }
        )
        for subscriber in self.subscribers:
            subscriber.handle(event)
        return event


class JsonlEventSubscriber:
    """Append structured events to the engine event log."""

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths

    def handle(self, event: EventRecord) -> None:
        self.paths.engine_events_log.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.engine_events_log.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")


class HistorySubscriber:
    """Write concise history index lines and detailed per-event entries."""

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths

    def handle(self, event: EventRecord) -> None:
        self.paths.historylog_dir.mkdir(parents=True, exist_ok=True)
        task_label = (
            str(event.payload.get("task_id") or event.payload.get("active_task_id") or "engine")
        )
        stage_label = str(event.payload.get("stage") or event.type.value.lower())
        filename = format_historylog_entry_name(event.timestamp, stage=stage_label, task=task_label)
        detail_path = self.paths.historylog_dir / filename
        payload_json = json.dumps(event.payload, indent=2, sort_keys=True)
        detail_body = "\n".join(
            [
                f"# {event.type.value}",
                "",
                f"- **Timestamp:** {event.timestamp.isoformat().replace('+00:00', 'Z')}",
                f"- **Source:** {event.source.value}",
                f"- **Task:** {task_label}",
                f"- **Stage:** {stage_label}",
                "",
                "```json",
                payload_json,
                "```",
            ]
        )
        write_text_atomic(detail_path, detail_body.rstrip("\n") + "\n")

        existing = self.paths.historylog_file.read_text(encoding="utf-8") if self.paths.historylog_file.exists() else ""
        line = (
            f"- {event.timestamp.isoformat().replace('+00:00', 'Z')} "
            f"[{event.type.value}] task={task_label} detail=historylog/{filename}"
        )
        updated = existing.rstrip("\n")
        if updated:
            updated += "\n\n" + line + "\n"
        else:
            updated = "# History Log\n\n" + line + "\n"
        write_text_atomic(self.paths.historylog_file, updated)
