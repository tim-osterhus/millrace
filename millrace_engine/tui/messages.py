"""Custom Textual messages for TUI background work."""

from __future__ import annotations

from datetime import datetime

from textual.message import Message

from ..health import WorkspaceHealthReport
from .models import ActionResultView, GatewayFailure, PanelId, RefreshPayload, RuntimeEventView


class HealthCheckCompleted(Message):
    """Posted when the startup health check completes."""

    bubble = True

    def __init__(self, report: WorkspaceHealthReport) -> None:
        super().__init__()
        self.report = report


class HealthCheckFailed(Message):
    """Posted when the startup health worker crashes unexpectedly."""

    bubble = True

    def __init__(self, failure: GatewayFailure) -> None:
        super().__init__()
        self.failure = failure


class RefreshSucceeded(Message):
    """Posted when one refresh worker returns shaped UI data."""

    bubble = True

    def __init__(self, payload: RefreshPayload, *, panels: tuple[PanelId, ...] = ()) -> None:
        super().__init__()
        self.payload = payload
        self.panels = tuple(panels)


class RefreshFailed(Message):
    """Posted when one refresh worker returns a typed failure."""

    bubble = True

    def __init__(self, failure: GatewayFailure, *, panels: tuple[PanelId, ...] = ()) -> None:
        super().__init__()
        self.failure = failure
        self.panels = tuple(panels)


class ActionSucceeded(Message):
    """Posted when one operator action completes successfully."""

    bubble = True

    def __init__(self, result: ActionResultView) -> None:
        super().__init__()
        self.result = result


class ActionFailed(Message):
    """Posted when one operator action fails with a UI-safe error."""

    bubble = True

    def __init__(self, failure: GatewayFailure) -> None:
        super().__init__()
        self.failure = failure


class EventsAppended(Message):
    """Posted when new runtime events should be appended into local state."""

    bubble = True

    def __init__(self, events: tuple[RuntimeEventView, ...], *, received_at: datetime | None = None) -> None:
        super().__init__()
        self.events = tuple(events)
        self.received_at = received_at


class EventStreamFailed(Message):
    """Posted when the long-lived event stream fails but the shell stays alive."""

    bubble = True

    def __init__(self, failure: GatewayFailure) -> None:
        super().__init__()
        self.failure = failure


__all__ = [
    "ActionFailed",
    "ActionSucceeded",
    "EventStreamFailed",
    "EventsAppended",
    "HealthCheckCompleted",
    "HealthCheckFailed",
    "RefreshFailed",
    "RefreshSucceeded",
]
