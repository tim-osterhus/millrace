"""Terminal renderers for live runtime monitor events."""

from __future__ import annotations

from threading import Lock
from typing import Mapping, TextIO

from millrace_ai.runtime.monitoring import RuntimeMonitorEvent, RuntimeMonitorSink


class BasicTerminalMonitor(RuntimeMonitorSink):
    """Concise line-oriented terminal renderer for daemon progress."""

    def __init__(self, *, stream: TextIO) -> None:
        self._stream = stream
        self._lock = Lock()

    def emit(self, event: RuntimeMonitorEvent) -> None:
        with self._lock:
            for line in _render_event_lines(event):
                self._stream.write(line + "\n")
            self._stream.flush()


def _render_event_lines(event: RuntimeMonitorEvent) -> tuple[str, ...]:
    prefix = f"[{event.occurred_at.strftime('%H:%M:%S')}] "
    if event.event_type == "runtime_started":
        return tuple(prefix + line for line in _render_runtime_started(event.payload))
    if event.event_type == "runtime_resumed_active_run":
        return (prefix + _render_resumed_active_run(event.payload),)
    if event.event_type == "runtime_idle":
        return (prefix + f"idle reason={_string(event.payload.get('reason'))}",)
    if event.event_type == "runtime_paused":
        return (prefix + f"paused reason={_string(event.payload.get('reason'))}",)
    if event.event_type == "runtime_stopped":
        return (prefix + f"stopped reason={_string(event.payload.get('reason'))}",)
    return ()


def _render_runtime_started(payload: Mapping[str, object]) -> tuple[str, ...]:
    lines = [
        "runtime started "
        f"mode={_string(payload.get('mode_id'))} "
        f"plan={_string(payload.get('compiled_plan_id'))} "
        f"currentness={_string(payload.get('compiled_plan_currentness'))}",
        "baseline "
        f"manifest={_string(payload.get('baseline_manifest_id'))} "
        f"seed_package={_string(payload.get('baseline_seed_package_version'))}",
    ]

    loop_ids = _string_mapping(payload.get("loop_ids_by_plane"))
    if loop_ids:
        lines.append("loops " + _format_plane_mapping(loop_ids))

    concurrency_policy = payload.get("concurrency_policy")
    if isinstance(concurrency_policy, Mapping):
        lines.append("concurrency " + _format_concurrency_policy(concurrency_policy))
    else:
        lines.append("concurrency none")

    status_markers = _string_mapping(payload.get("status_markers_by_plane"))
    queue_depths = _object_mapping(payload.get("queue_depths_by_plane"))
    snapshot_parts = ["snapshot status", _format_plane_mapping(status_markers, normalize_markers=True)]
    if queue_depths:
        snapshot_parts.extend(("queue", _format_plane_mapping(queue_depths)))
    lines.append(" ".join(part for part in snapshot_parts if part))
    return tuple(lines)


def _render_resumed_active_run(payload: Mapping[str, object]) -> str:
    return (
        "resumed active "
        f"plane={_string(payload.get('active_plane'))} "
        f"stage={_string(payload.get('active_stage'))} "
        f"node={_string(payload.get('active_node_id'))} "
        f"stage_kind={_string(payload.get('active_stage_kind_id'))} "
        f"run={_string(payload.get('active_run_id'))} "
        f"status={_normalize_marker(_string(payload.get('status_marker')))}"
    )


def _format_concurrency_policy(policy: Mapping[object, object]) -> str:
    exclusive = _format_plane_groups(policy.get("mutually_exclusive_planes"))
    concurrent = _format_plane_groups(policy.get("may_run_concurrently"))
    fragments = []
    if exclusive:
        fragments.append(f"exclusive={exclusive}")
    if concurrent:
        fragments.append(f"concurrent={concurrent}")
    return " ".join(fragments) if fragments else "none"


def _format_plane_groups(value: object) -> str:
    if not isinstance(value, (list, tuple)):
        return ""
    groups: list[str] = []
    for group in value:
        if not isinstance(group, (list, tuple)):
            continue
        members = [str(member) for member in group]
        if members:
            groups.append("+".join(members))
    return ",".join(groups)


def _format_plane_mapping(
    mapping: Mapping[str, object],
    *,
    normalize_markers: bool = False,
) -> str:
    parts = []
    for plane in ("execution", "planning", "learning"):
        if plane in mapping:
            value = str(mapping[plane])
            if normalize_markers:
                value = _normalize_marker(value)
            parts.append(f"{plane}={value}")
    for key, value in mapping.items():
        if key not in {"execution", "planning", "learning"}:
            text = str(value)
            if normalize_markers:
                text = _normalize_marker(text)
            parts.append(f"{key}={text}")
    return " ".join(parts)


def _string_mapping(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _object_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _normalize_marker(value: str) -> str:
    return value.removeprefix("### ").strip()


def _string(value: object) -> str:
    if value is None:
        return "unknown"
    return str(value)


__all__ = ["BasicTerminalMonitor"]
