"""Worker helpers for the Millrace Textual shell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import time

from pydantic import ValidationError
from textual.worker import get_current_worker

from ..control import EngineControl
from ..control_common import ControlError, expected_error_message, single_line_message, validation_error_message
from ..health import WorkspaceHealthReport
from .formatting import runtime_event_view
from .gateway import RuntimeGateway
from .messages import EventStreamFailed, EventsAppended
from .models import FailureCategory, GatewayFailure, GatewayResult, RefreshPayload, RuntimeEventView

HEALTH_CHECK_WORKER_NAME = "health.check"
INITIAL_REFRESH_WORKER_NAME = "refresh.initial"
PERIODIC_REFRESH_WORKER_NAME = "refresh.periodic"
EVENT_STREAM_WORKER_NAME = "events.stream"

REFRESH_WORKER_GROUP = "refresh"
HEALTH_WORKER_GROUP = "health"
EVENT_STREAM_WORKER_GROUP = "events"


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    refresh_interval_seconds: float = 3.0
    initial_log_limit: int = 50
    event_stream_poll_interval_seconds: float = 0.2
    event_stream_idle_timeout_seconds: float = 0.5
    event_batch_size: int = 20
    event_batch_window_seconds: float = 0.25
    event_retry_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be greater than zero")
        if self.initial_log_limit < 0:
            raise ValueError("initial_log_limit must be non-negative")
        if self.event_stream_poll_interval_seconds <= 0:
            raise ValueError("event_stream_poll_interval_seconds must be greater than zero")
        if self.event_stream_idle_timeout_seconds <= 0:
            raise ValueError("event_stream_idle_timeout_seconds must be greater than zero")
        if self.event_batch_size <= 0:
            raise ValueError("event_batch_size must be greater than zero")
        if self.event_batch_window_seconds <= 0:
            raise ValueError("event_batch_window_seconds must be greater than zero")
        if self.event_retry_delay_seconds <= 0:
            raise ValueError("event_retry_delay_seconds must be greater than zero")


def _resolve_config_path(config_path: Path | str) -> Path:
    resolved = Path(config_path).expanduser()
    return resolved.resolve() if not resolved.is_absolute() else resolved


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def gateway_failure_from_exception(operation: str, exc: Exception) -> GatewayFailure:
    if isinstance(exc, ControlError):
        message = single_line_message(exc) or "control operation failed"
        category = FailureCategory.CONTROL
    elif isinstance(exc, ValidationError):
        message = validation_error_message(exc)
        category = FailureCategory.CONTROL
    elif isinstance(exc, OSError):
        message = expected_error_message(exc) or "file system operation failed"
        category = FailureCategory.IO
    else:
        message = expected_error_message(exc) or exc.__class__.__name__
        category = FailureCategory.UNEXPECTED
    return GatewayFailure(
        operation=operation,
        category=category,
        message=message,
        exception_type=exc.__class__.__name__,
        retryable=category is not FailureCategory.INPUT,
    )


def load_health_report(config_path: Path | str) -> WorkspaceHealthReport:
    return EngineControl.health_report(_resolve_config_path(config_path))


def load_workspace_refresh(
    config_path: Path | str,
    *,
    include_events: bool,
    settings: WorkerSettings | None = None,
) -> GatewayResult[RefreshPayload]:
    active_settings = settings or WorkerSettings()
    gateway = RuntimeGateway(_resolve_config_path(config_path))
    result = gateway.load_workspace_snapshot(log_limit=(active_settings.initial_log_limit if include_events else 0))
    if include_events or not result.ok or result.value is None or result.value.events is None:
        return result

    payload = result.value
    return GatewayResult(
        value=RefreshPayload(
            refreshed_at=payload.refreshed_at,
            runtime=payload.runtime,
            config=payload.config,
            queue=payload.queue,
            research=payload.research,
            publish=payload.publish,
            runs=payload.runs,
            run_detail=payload.run_detail,
        )
    )


def _post_event_batch(
    post_message: Callable[[EventsAppended | EventStreamFailed], object],
    batch: list[RuntimeEventView],
) -> None:
    if not batch:
        return
    post_message(EventsAppended(tuple(batch), received_at=_utcnow()))


def stream_event_updates(
    config_path: Path | str,
    *,
    post_message: Callable[[EventsAppended | EventStreamFailed], object],
    settings: WorkerSettings | None = None,
    start_at_end: bool = True,
) -> None:
    active_settings = settings or WorkerSettings()
    worker = get_current_worker()
    resolved_config = _resolve_config_path(config_path)
    subscribe_from_end = start_at_end

    while not worker.cancelled_event.is_set():
        batch: list[RuntimeEventView] = []
        batch_started_at: float | None = None
        try:
            control = EngineControl(resolved_config)
            for record in control.events_subscribe(
                start_at_end=subscribe_from_end,
                poll_interval_seconds=active_settings.event_stream_poll_interval_seconds,
                idle_timeout_seconds=active_settings.event_stream_idle_timeout_seconds,
            ):
                if worker.cancelled_event.is_set():
                    break
                if not batch:
                    batch_started_at = time.monotonic()
                batch.append(runtime_event_view(record))
                elapsed = 0.0 if batch_started_at is None else time.monotonic() - batch_started_at
                if len(batch) >= active_settings.event_batch_size or elapsed >= active_settings.event_batch_window_seconds:
                    _post_event_batch(post_message, batch)
                    batch = []
                    batch_started_at = None
            _post_event_batch(post_message, batch)
            subscribe_from_end = True
        except Exception as exc:  # noqa: BLE001
            _post_event_batch(post_message, batch)
            post_message(EventStreamFailed(gateway_failure_from_exception("events.subscribe", exc)))
            if worker.cancelled_event.wait(active_settings.event_retry_delay_seconds):
                return
            subscribe_from_end = True


__all__ = [
    "EVENT_STREAM_WORKER_GROUP",
    "EVENT_STREAM_WORKER_NAME",
    "HEALTH_CHECK_WORKER_NAME",
    "HEALTH_WORKER_GROUP",
    "INITIAL_REFRESH_WORKER_NAME",
    "PERIODIC_REFRESH_WORKER_NAME",
    "REFRESH_WORKER_GROUP",
    "WorkerSettings",
    "gateway_failure_from_exception",
    "load_health_report",
    "load_workspace_refresh",
    "stream_event_updates",
]
