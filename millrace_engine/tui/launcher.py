"""Async subprocess launcher helpers for TUI-owned lifecycle actions."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from ..config import build_runtime_paths, load_engine_config
from ..control_reports import read_runtime_state
from ..engine_runtime import reconcile_runtime_snapshot, start_collision_message_for_state_path
from .models import (
    ActionResultView,
    FailureCategory,
    GatewayFailure,
    GatewayResult,
    KeyValueView,
)

START_ONCE_OPERATION = "action.start.once"
START_DAEMON_OPERATION = "action.start.daemon"


@dataclass(frozen=True, slots=True)
class LauncherSettings:
    daemon_startup_timeout_seconds: float = 1.5
    daemon_startup_poll_interval_seconds: float = 0.1
    foreground_cancel_timeout_seconds: float = 1.0
    output_excerpt_char_limit: int = 400

    def __post_init__(self) -> None:
        if self.daemon_startup_timeout_seconds <= 0:
            raise ValueError("daemon_startup_timeout_seconds must be greater than zero")
        if self.daemon_startup_poll_interval_seconds <= 0:
            raise ValueError("daemon_startup_poll_interval_seconds must be greater than zero")
        if self.foreground_cancel_timeout_seconds <= 0:
            raise ValueError("foreground_cancel_timeout_seconds must be greater than zero")
        if self.output_excerpt_char_limit <= 0:
            raise ValueError("output_excerpt_char_limit must be greater than zero")


@dataclass(frozen=True, slots=True)
class LauncherObservationPaths:
    state_path: Path
    events_log_path: Path
    source: "ObservationPathSource"
    source_detail: str | None = None


class ObservationPathSource(StrEnum):
    CONFIG = "config"
    WORKSPACE_FALLBACK = "workspace_fallback"


def _resolve_config_path(config_path: Path | str) -> Path:
    resolved = Path(config_path).expanduser()
    return resolved.resolve() if not resolved.is_absolute() else resolved


def _launch_command(config_path: Path, *, daemon: bool) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "millrace_engine",
        "--config",
        config_path.as_posix(),
        "start",
        "--daemon" if daemon else "--once",
    )


def _load_observation_paths(config_path: Path) -> LauncherObservationPaths:
    try:
        loaded = load_engine_config(config_path)
        paths = build_runtime_paths(loaded.config)
        return LauncherObservationPaths(
            state_path=paths.runtime_dir / "state.json",
            events_log_path=paths.engine_events_log,
            source=ObservationPathSource.CONFIG,
            source_detail=loaded.source.kind,
        )
    except FileNotFoundError as exc:
        workspace_root = config_path.parent
        return LauncherObservationPaths(
            state_path=workspace_root / "agents" / ".runtime" / "state.json",
            events_log_path=workspace_root / "agents" / "engine_events.log",
            source=ObservationPathSource.WORKSPACE_FALLBACK,
            source_detail=str(exc),
        )


def _daemon_running(state_path: Path) -> bool:
    try:
        state, _liveness = reconcile_runtime_snapshot(read_runtime_state(state_path))
    except Exception:  # noqa: BLE001 - state may be missing or mid-write during startup
        return False
    return bool(state is not None and state.process_running)


def _decode_output(payload: bytes | None) -> str:
    if not payload:
        return ""
    return payload.decode("utf-8", errors="replace").strip()


def _excerpt_text(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[: max(limit - 3, 1)].rstrip()
    return f"{clipped}..."


def _best_output_excerpt(*values: str, limit: int) -> str | None:
    for value in values:
        if value.strip():
            return _excerpt_text(value, limit=limit)
    return None


def _observation_path_details(paths: LauncherObservationPaths) -> tuple[KeyValueView, ...]:
    details = [
        KeyValueView(key="state_path", value=paths.state_path.as_posix()),
        KeyValueView(key="path_source", value=paths.source.value),
    ]
    if paths.source_detail:
        details.append(
            KeyValueView(
                key="path_source_detail",
                value=_excerpt_text(paths.source_detail, limit=200),
            )
        )
    return tuple(details)


def _observation_path_message_suffix(paths: LauncherObservationPaths) -> str:
    if paths.source is ObservationPathSource.CONFIG:
        return ""
    return (
        " Observation paths came from the workspace fallback because the config "
        "could not be loaded."
    )


def _failure(
    operation: str,
    message: str,
    *,
    category: FailureCategory = FailureCategory.CONTROL,
    exception_type: str = "LauncherError",
    retryable: bool = True,
) -> GatewayResult[ActionResultView]:
    return GatewayResult(
        failure=GatewayFailure(
            operation=operation,
            category=category,
            message=message,
            exception_type=exception_type,
            retryable=retryable,
        )
    )


def _foreground_details(
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    limit: int,
) -> tuple[KeyValueView, ...]:
    details: list[KeyValueView] = [KeyValueView(key="exit_code", value=str(exit_code))]
    stdout_excerpt = _best_output_excerpt(stdout, limit=limit)
    if stdout_excerpt is not None:
        details.append(KeyValueView(key="stdout", value=stdout_excerpt))
    stderr_excerpt = _best_output_excerpt(stderr, limit=limit)
    if stderr_excerpt is not None:
        details.append(KeyValueView(key="stderr", value=stderr_excerpt))
    return tuple(details)


async def _terminate_foreground_process(
    process: asyncio.subprocess.Process,
    *,
    timeout: float,
) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                return
            await process.wait()


async def launch_start_once(
    config_path: Path | str,
    *,
    settings: LauncherSettings | None = None,
) -> GatewayResult[ActionResultView]:
    active_settings = settings or LauncherSettings()
    resolved_config = _resolve_config_path(config_path)
    command = _launch_command(resolved_config, daemon=False)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=resolved_config.parent.as_posix(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return _failure(
            START_ONCE_OPERATION,
            f"unable to launch once-mode subprocess: {exc}",
            category=FailureCategory.IO,
            exception_type=exc.__class__.__name__,
        )

    try:
        stdout_bytes, stderr_bytes = await process.communicate()
    except asyncio.CancelledError:
        await _terminate_foreground_process(
            process,
            timeout=active_settings.foreground_cancel_timeout_seconds,
        )
        raise

    stdout = _decode_output(stdout_bytes)
    stderr = _decode_output(stderr_bytes)
    if process.returncode != 0:
        excerpt = _best_output_excerpt(
            stderr,
            stdout,
            limit=active_settings.output_excerpt_char_limit,
        )
        message = f"once run failed with exit code {process.returncode}"
        if excerpt is not None:
            message = f"{message}: {excerpt}"
        return _failure(
            START_ONCE_OPERATION,
            message,
            exception_type="SubprocessExit",
        )
    return GatewayResult(
        value=ActionResultView(
            action="start.once",
            message="once run completed",
            applied=True,
            mode="foreground",
            details=_foreground_details(
                exit_code=process.returncode or 0,
                stdout=stdout,
                stderr=stderr,
                limit=active_settings.output_excerpt_char_limit,
            ),
        )
    )


def _read_tempfile_excerpt(handle: BinaryIO, *, limit: int) -> str | None:
    handle.flush()
    handle.seek(0)
    data = handle.read()
    text = _decode_output(
        data if isinstance(data, bytes) else data.encode("utf-8", errors="replace")
    )
    return _best_output_excerpt(text, limit=limit)


async def launch_start_daemon(
    config_path: Path | str,
    *,
    settings: LauncherSettings | None = None,
) -> GatewayResult[ActionResultView]:
    active_settings = settings or LauncherSettings()
    resolved_config = _resolve_config_path(config_path)
    try:
        observation_paths = await asyncio.to_thread(_load_observation_paths, resolved_config)
    except Exception as exc:  # noqa: BLE001 - launcher is a process boundary and must surface path-resolution failures cleanly
        return _failure(
            START_DAEMON_OPERATION,
            f"unable to resolve daemon observation paths: {exc}",
            category=FailureCategory.INPUT,
            exception_type=exc.__class__.__name__,
        )
    if await asyncio.to_thread(_daemon_running, observation_paths.state_path):
        collision_message = await asyncio.to_thread(
            start_collision_message_for_state_path,
            observation_paths.state_path,
            attempted_mode="daemon",
        )
        return _failure(
            START_DAEMON_OPERATION,
            collision_message
            or (
                "runtime already reports a running daemon; "
                "refresh status or use pause, resume, or stop instead."
                f"{_observation_path_message_suffix(observation_paths)}"
            ),
            exception_type="RuntimeAlreadyRunning",
            retryable=False,
        )
    command = _launch_command(resolved_config, daemon=True)

    with tempfile.TemporaryFile(mode="w+b") as output_capture:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=resolved_config.parent.as_posix(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=output_capture,
                stderr=output_capture,
                start_new_session=True,
            )
        except OSError as exc:
            return _failure(
                START_DAEMON_OPERATION,
                f"unable to launch daemon subprocess: {exc}",
                category=FailureCategory.IO,
                exception_type=exc.__class__.__name__,
            )

        deadline = (
            asyncio.get_running_loop().time() + active_settings.daemon_startup_timeout_seconds
        )
        try:
            while True:
                if await asyncio.to_thread(_daemon_running, observation_paths.state_path):
                    return GatewayResult(
                        value=ActionResultView(
                            action="start.daemon",
                            message="daemon launched",
                            applied=True,
                            mode="detached",
                            details=(KeyValueView(key="pid", value=str(process.pid)),)
                            + _observation_path_details(observation_paths),
                        )
                    )
                if process.returncode is not None:
                    await process.wait()
                    excerpt = _read_tempfile_excerpt(
                        output_capture,
                        limit=active_settings.output_excerpt_char_limit,
                    )
                    message = (
                        "daemon launch exited before runtime state reported running "
                        f"(exit code {process.returncode}). "
                        f"Check {observation_paths.events_log_path.as_posix()} or rerun "
                        f"`{' '.join(command)}` in a terminal."
                    )
                    message = f"{message}{_observation_path_message_suffix(observation_paths)}"
                    if excerpt is not None:
                        message = f"{message} Output: {excerpt}"
                    return _failure(
                        START_DAEMON_OPERATION,
                        message,
                        exception_type="SubprocessExit",
                    )
                if asyncio.get_running_loop().time() >= deadline:
                    return GatewayResult(
                        value=ActionResultView(
                            action="start.daemon",
                            message="daemon launch requested; waiting for runtime state",
                            applied=True,
                            mode="detached",
                            details=(
                                KeyValueView(key="pid", value=str(process.pid)),
                                KeyValueView(
                                    key="events_log",
                                    value=observation_paths.events_log_path.as_posix(),
                                ),
                            )
                            + _observation_path_details(observation_paths),
                        )
                    )
                await asyncio.sleep(active_settings.daemon_startup_poll_interval_seconds)
        except asyncio.CancelledError:
            raise


__all__ = [
    "LauncherObservationPaths",
    "LauncherSettings",
    "ObservationPathSource",
    "START_DAEMON_OPERATION",
    "START_ONCE_OPERATION",
    "launch_start_daemon",
    "launch_start_once",
]
