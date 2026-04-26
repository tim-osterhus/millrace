"""Live runtime monitor event contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RuntimeMonitorEvent:
    """Structured live event emitted by the runtime monitor seam."""

    event_type: str
    occurred_at: datetime
    payload: Mapping[str, object] = field(default_factory=dict)


class RuntimeMonitorSink:
    """Consumer for structured live runtime monitor events."""

    def emit(self, event: RuntimeMonitorEvent) -> None:
        raise NotImplementedError


class NullRuntimeMonitorSink(RuntimeMonitorSink):
    """Monitor sink that intentionally discards live runtime monitor events."""

    def emit(self, event: RuntimeMonitorEvent) -> None:
        del event
        return


__all__ = ["NullRuntimeMonitorSink", "RuntimeMonitorEvent", "RuntimeMonitorSink"]
