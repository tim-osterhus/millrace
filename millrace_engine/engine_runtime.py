"""Narrow engine-launch helpers shared by control surfaces."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .config import build_runtime_paths
from .control_common import ControlError
from .control_common import load_control_config
from .control_models import RuntimeState
from .control_reports import read_control_runtime_state, read_runtime_state


def _format_started_at(state: RuntimeState) -> str:
    if state.started_at is None:
        return "unknown"
    return state.started_at.isoformat().replace("+00:00", "Z")


def format_start_collision_message(
    *,
    state: RuntimeState,
    state_path: Path,
    attempted_mode: Literal["once", "daemon"],
) -> str:
    owner_mode = state.mode
    started_at = _format_started_at(state)
    return (
        f"cannot start {attempted_mode}: workspace is already owned by a running "
        f"{owner_mode} runtime (started_at={started_at}, state_path={state_path.as_posix()}); "
        "stop the active runtime before starting another."
    )


def start_collision_message_for_state_path(
    state_path: Path | str,
    *,
    attempted_mode: Literal["once", "daemon"],
) -> str | None:
    resolved_state_path = Path(state_path)
    try:
        state = read_runtime_state(resolved_state_path)
    except Exception:  # noqa: BLE001 - TUI preflight must tolerate missing or transient snapshots
        return None
    if state is None or not state.process_running:
        return None
    return format_start_collision_message(
        state=state,
        state_path=resolved_state_path,
        attempted_mode=attempted_mode,
    )


def ensure_workspace_start_available(
    config_path: Path | str,
    *,
    attempted_mode: Literal["once", "daemon"],
) -> None:
    resolved_config_path = Path(config_path).expanduser().resolve()
    loaded = load_control_config(resolved_config_path)
    paths = build_runtime_paths(loaded.config)
    state_path = paths.runtime_dir / "state.json"
    state = read_control_runtime_state(state_path)
    if state is None or not state.process_running:
        return
    raise ControlError(
        format_start_collision_message(
            state=state,
            state_path=state_path,
            attempted_mode=attempted_mode,
        )
    )


def start_engine(
    config_path: Path | str,
    *,
    daemon: bool = False,
    once: bool = False,
) -> RuntimeState:
    """Construct and start the runtime engine with control-surface validation."""

    if daemon and once:
        raise ControlError("start may use only one of daemon or once")
    attempted_mode: Literal["once", "daemon"] = "daemon" if daemon else "once"
    ensure_workspace_start_available(config_path, attempted_mode=attempted_mode)

    from .engine import MillraceEngine

    engine = MillraceEngine(config_path)
    return engine.start(daemon=daemon, once=once)
