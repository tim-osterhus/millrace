"""Live runtime monitor event contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

_RUNTIME_EFFECT_MONITOR_KEYS = (
    "runtime_effect_handler_id",
    "runtime_effect_decision",
    "runtime_effect_failure_class",
    "runtime_effect_failure_message",
    "runtime_effect_mutation_phase",
    "runtime_effect_failure_policy_id",
    "runtime_effect_recovery_action",
)


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


def runtime_effect_monitor_payload(metadata: Mapping[str, object]) -> dict[str, object]:
    """Extract operator-visible runtime-effect metadata for monitor event payloads."""

    payload: dict[str, object] = {}
    for key in _RUNTIME_EFFECT_MONITOR_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value:
            payload[key] = value
    created_paths = metadata.get("runtime_effect_created_paths")
    if isinstance(created_paths, (list, tuple)):
        paths = tuple(item for item in created_paths if isinstance(item, str) and item)
        if paths:
            payload["runtime_effect_created_paths"] = paths
    return payload


def unexpected_daemon_exit_event(*, phase: str, exc: BaseException) -> RuntimeMonitorEvent:
    """Build a monitor event for daemon exceptions caught at the CLI boundary."""

    return RuntimeMonitorEvent(
        event_type="runtime_unexpected_exit",
        occurred_at=datetime.now(timezone.utc),
        payload={
            "phase": phase,
            "exception_type": type(exc).__name__,
            "error": str(exc),
        },
    )


__all__ = [
    "NullRuntimeMonitorSink",
    "RuntimeMonitorEvent",
    "RuntimeMonitorSink",
    "runtime_effect_monitor_payload",
    "unexpected_daemon_exit_event",
]
