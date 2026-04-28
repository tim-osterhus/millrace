"""Terminal renderers for live runtime monitor events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Mapping, TextIO

from millrace_ai.contracts.stage_metadata import STAGE_METADATA_BY_VALUE
from millrace_ai.runtime.monitoring import RuntimeMonitorEvent, RuntimeMonitorSink

_IDLE_HEARTBEAT_SECONDS = 120.0
_RUN_HANDLE_LENGTHS = (8, 12, 16)


class BasicTerminalMonitor(RuntimeMonitorSink):
    """Concise line-oriented terminal renderer for daemon progress."""

    def __init__(self, *, stream: TextIO) -> None:
        self._stream = stream
        self._lock = Lock()
        self._run_state: dict[tuple[str, str], _RunAggregate] = {}
        self._idle_state = _IdleRenderState()
        self._display_ids = _DisplayIdRegistry()

    def emit(self, event: RuntimeMonitorEvent) -> None:
        with self._lock:
            for line in _render_event_lines(
                event,
                self._run_state,
                self._idle_state,
                self._display_ids,
            ):
                self._stream.write(line + "\n")
            self._stream.flush()


@dataclass(slots=True)
class _IdleRenderState:
    reason: str | None = None
    last_emitted_at: datetime | None = None

    def reset(self) -> None:
        self.reason = None
        self.last_emitted_at = None


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


class _DisplayIdRegistry:
    """Stable short handles for long live-monitor ids."""

    def __init__(self) -> None:
        self._handles_by_run_id: dict[str, str] = {}
        self._run_id_by_handle: dict[str, str] = {}

    def run(self, run_id: object) -> str:
        raw = _string(run_id)
        if raw in self._handles_by_run_id:
            return self._handles_by_run_id[raw]

        for candidate in _run_handle_candidates(raw):
            existing = self._run_id_by_handle.get(candidate)
            if existing is None or existing == raw:
                self._handles_by_run_id[raw] = candidate
                self._run_id_by_handle[candidate] = raw
                return candidate

        self._handles_by_run_id[raw] = raw
        self._run_id_by_handle[raw] = raw
        return raw


def _render_event_lines(
    event: RuntimeMonitorEvent,
    run_state: dict[tuple[str, str], _RunAggregate],
    idle_state: _IdleRenderState,
    display_ids: _DisplayIdRegistry,
) -> tuple[str, ...]:
    prefix = f"[{event.occurred_at.strftime('%H:%M:%S')}] "
    if event.event_type != "runtime_idle":
        idle_state.reset()
    if event.event_type == "runtime_started":
        return tuple(prefix + line for line in _render_runtime_started(event.payload))
    if event.event_type == "runtime_resumed_active_run":
        return (prefix + _render_resumed_active_run(event.payload, display_ids),)
    if event.event_type == "stage_started":
        _seed_stage_started(event.payload, event.occurred_at, run_state)
        return (prefix + _render_stage_started(event.payload, display_ids),)
    if event.event_type == "stage_completed":
        run_update = _record_stage_completed(event.payload, run_state, display_ids)
        return (
            prefix + _render_stage_completed(event.payload, display_ids),
            prefix + run_update,
        )
    if event.event_type == "router_decision":
        return (prefix + _render_router_decision(event.payload),)
    if event.event_type == "status_marker_changed":
        status_line = _render_status_marker_changed(event.payload, display_ids)
        return () if status_line is None else (prefix + status_line,)
    if event.event_type == "runtime_idle":
        reason = _string(event.payload.get("reason"))
        if not _should_render_idle(reason, event.occurred_at, idle_state):
            return ()
        idle_state.reason = reason
        idle_state.last_emitted_at = event.occurred_at
        return (prefix + f"idle reason={reason}",)
    if event.event_type == "runtime_paused":
        return (prefix + f"paused reason={_string(event.payload.get('reason'))}",)
    if event.event_type == "runtime_stopped":
        return (prefix + f"stopped reason={_string(event.payload.get('reason'))}",)
    if event.event_type == "runtime_config_reload_deferred":
        active_planes = event.payload.get("active_planes")
        if isinstance(active_planes, (list, tuple)):
            active = ",".join(str(plane) for plane in active_planes) or "none"
        else:
            active = "unknown"
        return (prefix + f"reload deferred reason={_string(event.payload.get('reason'))} active={active}",)
    if event.event_type == "learning_curator_promotion_deferred":
        active_planes = event.payload.get("foreground_active_planes")
        if isinstance(active_planes, (list, tuple)):
            active = ",".join(str(plane) for plane in active_planes) or "none"
        else:
            active = "unknown"
        return (prefix + f"curator promotion deferred active={active}",)
    if event.event_type == "learning_curator_promotion_applied":
        return (prefix + f"curator promotion applied source={_string(event.payload.get('source'))}",)
    if event.event_type == "usage_governance_paused":
        return (prefix + _render_usage_governance_paused(event.payload),)
    if event.event_type == "usage_governance_resumed":
        return (prefix + _render_usage_governance_resumed(event.payload),)
    if event.event_type == "usage_governance_degraded":
        return (prefix + _render_usage_governance_degraded(event.payload),)
    return ()


def _should_render_idle(reason: str, occurred_at: datetime, idle_state: _IdleRenderState) -> bool:
    if idle_state.reason != reason or idle_state.last_emitted_at is None:
        return True
    elapsed_seconds = (occurred_at - idle_state.last_emitted_at).total_seconds()
    return elapsed_seconds >= _IDLE_HEARTBEAT_SECONDS


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
    lines.append(f"scheduler mode={_string(payload.get('scheduler_mode'))}")

    status_markers = _string_mapping(payload.get("status_markers_by_plane"))
    queue_depths = _object_mapping(payload.get("queue_depths_by_plane"))
    snapshot_parts = ["snapshot status", _format_plane_mapping(status_markers, normalize_markers=True)]
    if queue_depths:
        snapshot_parts.extend(("queue", _format_plane_mapping(queue_depths)))
    lines.append(" ".join(part for part in snapshot_parts if part))
    return tuple(lines)


def _render_resumed_active_run(
    payload: Mapping[str, object],
    display_ids: _DisplayIdRegistry,
) -> str:
    parts = [
        "resumed active",
        _stage_ref(
            plane=_string(payload.get("active_plane")),
            stage=_string(payload.get("active_stage")),
            node_id=_string(payload.get("active_node_id")),
            stage_kind_id=_string(payload.get("active_stage_kind_id")),
        ),
        f"run={display_ids.run(payload.get('active_run_id'))}",
    ]
    marker = _normalize_marker(_string(payload.get("status_marker")))
    if marker != "unknown":
        parts.append(f"status={marker}")
    return " ".join(parts)


def _render_stage_started(
    payload: Mapping[str, object],
    display_ids: _DisplayIdRegistry,
) -> str:
    parts = [
        "stage start",
        _stage_ref_from_payload(payload),
        f"run={display_ids.run(payload.get('run_id'))}",
    ]
    work_ref = _work_ref(payload)
    if work_ref is not None:
        parts.append(f"work={work_ref}")
    status = _nonredundant_running_status(payload)
    if status is not None:
        parts.append(f"status={status}")
    return " ".join(parts)


def _render_stage_completed(
    payload: Mapping[str, object],
    display_ids: _DisplayIdRegistry,
) -> str:
    duration = _float_value(payload.get("duration_seconds"))
    parts = [
        "stage done",
        _stage_ref_from_payload(payload),
        f"run={display_ids.run(payload.get('run_id'))}",
        f"result={_string(payload.get('terminal_result'))}",
    ]
    status = _nonredundant_terminal_status(payload)
    if status is not None:
        parts.append(f"status={status}")
    parts.append(f"dur={_format_seconds(duration)}")
    token_usage = _format_token_usage(payload.get("token_usage"))
    if token_usage is not None:
        parts.append(f"tokens={token_usage}")
    return " ".join(parts)


def _render_router_decision(payload: Mapping[str, object]) -> str:
    action = _string(payload.get("action"))
    plane = _string(payload.get("plane"))
    next_stage = _optional_string(payload.get("next_stage"))
    if action == "idle":
        parts = ["route", plane, "done"]
    elif action == "blocked":
        parts = ["route", plane, "blocked"]
    elif next_stage is not None:
        parts = ["route", plane, "->", _route_target(payload)]
        if action not in {"run_stage", "unknown"}:
            parts.append(f"action={action}")
    else:
        parts = ["route", plane, f"action={action}"]

    reason = _optional_string(payload.get("reason"))
    if reason is not None:
        parts.append(f"reason={reason}")
    return " ".join(parts)


def _render_status_marker_changed(
    payload: Mapping[str, object],
    display_ids: _DisplayIdRegistry,
) -> str | None:
    source = _string(payload.get("source"))
    if source in {"stage_started", "stage_completed"}:
        return None
    previous_marker = _normalize_marker(_string(payload.get("previous_marker")))
    current_marker = _normalize_marker(_string(payload.get("current_marker")))
    if (
        source == "router_idle"
        and current_marker == "IDLE"
        and previous_marker != "IDLE"
        and not previous_marker.endswith("_RUNNING")
    ):
        return None
    return (
        "status "
        f"{_string(payload.get('plane'))} "
        f"run={display_ids.run(payload.get('run_id'))} "
        f"from={previous_marker} "
        f"to={current_marker}"
    )


def _render_usage_governance_paused(payload: Mapping[str, object]) -> str:
    next_resume = _string(payload.get("next_auto_resume_at"))
    return (
        "governance pause "
        f"source={_string(payload.get('source'))} "
        f"rule={_string(payload.get('rule_id'))} "
        f"window={_string(payload.get('window'))} "
        f"observed={_number_string(payload.get('observed'))} "
        f"threshold={_number_string(payload.get('threshold'))} "
        f"next_resume={next_resume}"
    )


def _render_usage_governance_resumed(payload: Mapping[str, object]) -> str:
    return f"governance resume cleared_rules={_string(payload.get('cleared_rules'))}"


def _render_usage_governance_degraded(payload: Mapping[str, object]) -> str:
    return (
        "governance degraded "
        f"source={_string(payload.get('source'))} "
        f"policy={_string(payload.get('policy'))} "
        f"detail={_string(payload.get('detail'))}"
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
    display_ids: _DisplayIdRegistry,
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
    parts = [
        "run",
        plane,
        f"run={display_ids.run(run_id)}",
        f"elapsed={_format_seconds(elapsed)}",
    ]
    tokens = _format_aggregate_tokens(aggregate)
    if tokens is not None:
        parts.append(f"tokens={tokens}")
    return " ".join(parts)


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


def _format_aggregate_tokens(aggregate: _RunAggregate) -> str | None:
    if not aggregate.has_known_tokens:
        return None
    return (
        f"in={aggregate.input_tokens} "
        f"cached={aggregate.cached_input_tokens} "
        f"out={aggregate.output_tokens} "
        f"think={aggregate.thinking_tokens} "
        f"total={aggregate.total_tokens}"
    )


def _format_token_usage(token_usage: object) -> str | None:
    if not isinstance(token_usage, Mapping):
        return None
    return (
        f"in={_int_value(token_usage.get('input_tokens'))} "
        f"cached={_int_value(token_usage.get('cached_input_tokens'))} "
        f"out={_int_value(token_usage.get('output_tokens'))} "
        f"think={_int_value(token_usage.get('thinking_tokens'))} "
        f"total={_int_value(token_usage.get('total_tokens'))}"
    )


def _stage_ref_from_payload(payload: Mapping[str, object]) -> str:
    return _stage_ref(
        plane=_string(payload.get("plane")),
        stage=_string(payload.get("stage")),
        node_id=_string(payload.get("node_id")),
        stage_kind_id=_string(payload.get("stage_kind_id")),
    )


def _stage_ref(
    *,
    plane: str,
    stage: str,
    node_id: str,
    stage_kind_id: str,
) -> str:
    parts = [f"{plane}/{stage}"]
    if node_id not in {"unknown", stage}:
        parts.append(f"node={node_id}")
    if stage_kind_id not in {"unknown", stage, node_id}:
        parts.append(f"kind={stage_kind_id}")
    return " ".join(parts)


def _route_target(payload: Mapping[str, object]) -> str:
    next_stage = _string(payload.get("next_stage"))
    next_node = _string(payload.get("next_node_id"))
    next_kind = _string(payload.get("next_stage_kind_id"))
    current_plane = _string(payload.get("plane"))
    target_plane = _plane_for_stage(next_stage)
    target = next_stage if target_plane in {None, current_plane} else f"{target_plane}/{next_stage}"
    extras: list[str] = []
    if next_node not in {"unknown", next_stage}:
        extras.append(f"node={next_node}")
    if next_kind not in {"unknown", next_stage, next_node}:
        extras.append(f"kind={next_kind}")
    return " ".join((target, *extras))


def _plane_for_stage(stage: str) -> str | None:
    metadata = STAGE_METADATA_BY_VALUE.get(stage)
    if metadata is None:
        return None
    return metadata.plane.value


def _work_ref(payload: Mapping[str, object]) -> str | None:
    work_kind = _optional_string(payload.get("work_item_kind"))
    work_id = _optional_string(payload.get("work_item_id"))
    if work_kind is None and work_id is None:
        return None
    if work_kind is None:
        return work_id
    if work_id is None:
        return work_kind
    return f"{work_kind}:{work_id}"


def _nonredundant_running_status(payload: Mapping[str, object]) -> str | None:
    status = _normalize_marker(_string(payload.get("status_marker")))
    stage = _string(payload.get("stage"))
    if status in {"unknown", f"{stage.upper()}_RUNNING"}:
        return None
    return status


def _nonredundant_terminal_status(payload: Mapping[str, object]) -> str | None:
    status = _normalize_marker(_string(payload.get("summary_status_marker")))
    terminal_result = _string(payload.get("terminal_result"))
    if status in {"unknown", terminal_result}:
        return None
    return status


def _run_handle_candidates(run_id: str) -> tuple[str, ...]:
    if not run_id.startswith("run-"):
        return (run_id,)
    suffix = run_id.removeprefix("run-")
    if len(suffix) <= _RUN_HANDLE_LENGTHS[0] or not _is_hex_string(suffix):
        return (run_id,)
    handles = [suffix[:length] for length in _RUN_HANDLE_LENGTHS if length < len(suffix)]
    handles.append(run_id)
    return tuple(handles)


def _is_hex_string(value: str) -> bool:
    return bool(value) and all(char in "0123456789abcdefABCDEF" for char in value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


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


def _number_string(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return _string(value)


def _string(value: object) -> str:
    if value is None:
        return "unknown"
    return str(value)


__all__ = ["BasicTerminalMonitor"]
