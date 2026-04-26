"""Terminal renderers for live runtime monitor events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Mapping, TextIO

from millrace_ai.runtime.monitoring import RuntimeMonitorEvent, RuntimeMonitorSink


class BasicTerminalMonitor(RuntimeMonitorSink):
    """Concise line-oriented terminal renderer for daemon progress."""

    def __init__(self, *, stream: TextIO) -> None:
        self._stream = stream
        self._lock = Lock()
        self._run_state: dict[tuple[str, str], _RunAggregate] = {}

    def emit(self, event: RuntimeMonitorEvent) -> None:
        with self._lock:
            for line in _render_event_lines(event, self._run_state):
                self._stream.write(line + "\n")
            self._stream.flush()


@dataclass(slots=True)
class _RunAggregate:
    first_started_at: datetime | None = None
    latest_completed_at: datetime | None = None
    fallback_elapsed_seconds: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0
    has_known_tokens: bool = False


def _render_event_lines(
    event: RuntimeMonitorEvent,
    run_state: dict[tuple[str, str], _RunAggregate],
) -> tuple[str, ...]:
    prefix = f"[{event.occurred_at.strftime('%H:%M:%S')}] "
    if event.event_type == "runtime_started":
        return tuple(prefix + line for line in _render_runtime_started(event.payload))
    if event.event_type == "runtime_resumed_active_run":
        return (prefix + _render_resumed_active_run(event.payload),)
    if event.event_type == "stage_started":
        _seed_stage_started(event.payload, event.occurred_at, run_state)
        return (prefix + _render_stage_started(event.payload),)
    if event.event_type == "stage_completed":
        run_update = _record_stage_completed(event.payload, run_state)
        return (
            prefix + _render_stage_completed(event.payload),
            prefix + run_update,
        )
    if event.event_type == "router_decision":
        return (prefix + _render_router_decision(event.payload),)
    if event.event_type == "status_marker_changed":
        status_line = _render_status_marker_changed(event.payload)
        return () if status_line is None else (prefix + status_line,)
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


def _render_stage_started(payload: Mapping[str, object]) -> str:
    return (
        "stage start "
        f"plane={_string(payload.get('plane'))} "
        f"stage={_string(payload.get('stage'))} "
        f"node={_string(payload.get('node_id'))} "
        f"stage_kind={_string(payload.get('stage_kind_id'))} "
        f"run={_string(payload.get('run_id'))} "
        f"work={_string(payload.get('work_item_kind'))} {_string(payload.get('work_item_id'))} "
        f"status={_normalize_marker(_string(payload.get('status_marker')))}"
    )


def _render_stage_completed(payload: Mapping[str, object]) -> str:
    duration = _float_value(payload.get("duration_seconds"))
    return (
        "stage done "
        f"plane={_string(payload.get('plane'))} "
        f"stage={_string(payload.get('stage'))} "
        f"node={_string(payload.get('node_id'))} "
        f"stage_kind={_string(payload.get('stage_kind_id'))} "
        f"run={_string(payload.get('run_id'))} "
        f"result={_string(payload.get('terminal_result'))} "
        f"status={_normalize_marker(_string(payload.get('summary_status_marker')))} "
        f"dur={_format_seconds(duration)} "
        f"tokens={_format_token_usage(payload.get('token_usage'))}"
    )


def _render_router_decision(payload: Mapping[str, object]) -> str:
    return (
        "route "
        f"plane={_string(payload.get('plane'))} "
        f"action={_string(payload.get('action'))} "
        f"next={_string(payload.get('next_stage'))} "
        f"next_node={_string(payload.get('next_node_id'))} "
        f"next_stage_kind={_string(payload.get('next_stage_kind_id'))} "
        f"reason={_string(payload.get('reason'))}"
    )


def _render_status_marker_changed(payload: Mapping[str, object]) -> str | None:
    source = _string(payload.get("source"))
    if source in {"stage_started", "stage_completed"}:
        return None
    return (
        "status "
        f"plane={_string(payload.get('plane'))} "
        f"run={_string(payload.get('run_id'))} "
        f"from={_normalize_marker(_string(payload.get('previous_marker')))} "
        f"to={_normalize_marker(_string(payload.get('current_marker')))}"
    )


def _seed_stage_started(
    payload: Mapping[str, object],
    occurred_at: datetime,
    run_state: dict[tuple[str, str], _RunAggregate],
) -> None:
    plane = _string(payload.get("plane"))
    run_id = _string(payload.get("run_id"))
    aggregate = run_state.setdefault((plane, run_id), _RunAggregate())
    if aggregate.first_started_at is None or occurred_at < aggregate.first_started_at:
        aggregate.first_started_at = occurred_at


def _record_stage_completed(
    payload: Mapping[str, object],
    run_state: dict[tuple[str, str], _RunAggregate],
) -> str:
    plane = _string(payload.get("plane"))
    run_id = _string(payload.get("run_id"))
    aggregate = run_state.setdefault((plane, run_id), _RunAggregate())
    started_at = _datetime_value(payload.get("started_at"))
    completed_at = _datetime_value(payload.get("completed_at"))
    if started_at is not None and (
        aggregate.first_started_at is None or started_at < aggregate.first_started_at
    ):
        aggregate.first_started_at = started_at
    if completed_at is not None and (
        aggregate.latest_completed_at is None or completed_at > aggregate.latest_completed_at
    ):
        aggregate.latest_completed_at = completed_at
    elif completed_at is None:
        aggregate.fallback_elapsed_seconds += _float_value(payload.get("duration_seconds"))

    _add_token_usage(aggregate, payload.get("token_usage"))
    elapsed = _aggregate_elapsed_seconds(aggregate)
    return (
        f"run update plane={plane} run={run_id} "
        f"elapsed={_format_seconds(elapsed)} tokens={_format_aggregate_tokens(aggregate)}"
    )


def _add_token_usage(aggregate: _RunAggregate, token_usage: object) -> None:
    if not isinstance(token_usage, Mapping):
        return
    aggregate.input_tokens += _int_value(token_usage.get("input_tokens"))
    aggregate.cached_input_tokens += _int_value(token_usage.get("cached_input_tokens"))
    aggregate.output_tokens += _int_value(token_usage.get("output_tokens"))
    aggregate.thinking_tokens += _int_value(token_usage.get("thinking_tokens"))
    aggregate.total_tokens += _int_value(token_usage.get("total_tokens"))
    aggregate.has_known_tokens = True


def _aggregate_elapsed_seconds(aggregate: _RunAggregate) -> float:
    if aggregate.first_started_at is not None and aggregate.latest_completed_at is not None:
        return (aggregate.latest_completed_at - aggregate.first_started_at).total_seconds()
    return aggregate.fallback_elapsed_seconds


def _format_aggregate_tokens(aggregate: _RunAggregate) -> str:
    if not aggregate.has_known_tokens:
        return "unknown"
    return (
        f"in={aggregate.input_tokens} "
        f"cached={aggregate.cached_input_tokens} "
        f"out={aggregate.output_tokens} "
        f"think={aggregate.thinking_tokens} "
        f"total={aggregate.total_tokens}"
    )


def _format_token_usage(token_usage: object) -> str:
    if not isinstance(token_usage, Mapping):
        return "unknown"
    return (
        f"in={_int_value(token_usage.get('input_tokens'))} "
        f"cached={_int_value(token_usage.get('cached_input_tokens'))} "
        f"out={_int_value(token_usage.get('output_tokens'))} "
        f"think={_int_value(token_usage.get('thinking_tokens'))} "
        f"total={_int_value(token_usage.get('total_tokens'))}"
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
    parts: list[str] = []
    for plane in ("execution", "planning", "learning"):
        if plane in mapping:
            value = str(mapping[plane])
            if normalize_markers:
                value = _normalize_marker(value)
            parts.append(f"{plane}={value}")
    for key, item in mapping.items():
        if key not in {"execution", "planning", "learning"}:
            text = str(item)
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


def _datetime_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _format_seconds(value: float) -> str:
    return f"{value:.1f}s"


def _float_value(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    return 0


def _string(value: object) -> str:
    if value is None:
        return "unknown"
    return str(value)


__all__ = ["BasicTerminalMonitor"]
