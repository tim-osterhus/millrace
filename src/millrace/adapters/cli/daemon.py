"""CLI adapter for bounded local daemon execution."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType, SimpleNamespace
from typing import Any, TextIO
from uuid import uuid4

from millrace.adapters.cli.context import (
    CliCommandError,
    CliWorkspacePaths,
    OpenRuntimeContext,
    require_nonblank,
    workspace_paths,
)
from millrace.adapters.cli.context import (
    open_runtime_context as _open_runtime_context,
)
from millrace.adapters.cli.lifecycle import run_lifecycle_transition_once
from millrace.adapters.cli.output import CliSuccess, ExitCode, success_result
from millrace.adapters.cli.run import (
    BoundedExecutionUnitResult,
    load_adapter_local_config,
    reconcile_pending_runner_sessions,
    run_bounded_execution_unit,
)
from millrace.adapters.runner_contract import AdapterLocalConfig
from millrace.substrate.errors import SubstrateError

_COMMAND = "run.daemon"
_LOCK_FILENAME = "daemon.lock"
_SUCCESS_REASONS = frozenset({"max_ticks", "signal"})
_RUNNER_FAILURE_REASONS = frozenset(
    {
        "adapter_failure",
        "adapter_conversion_refused",
        "runner_session_orphan_risk",
        "runner_session_reconciliation_contradiction",
        "session_reconciliation_required",
    }
)
_DOMAIN_REFUSAL_REASONS = frozenset(
    {
        "daemon_already_running",
        "ready_state_refused",
        "adapter_kind_refused",
        "asset_material_refused",
        "lifecycle_transition_refused",
        "observation_refused",
        "runner_session_retry_refused",
    }
)
_PERSISTENCE_FAILURE_REASONS = frozenset(
    {
        "lifecycle_state_corrupt",
        "state_open_failed",
        "ready_state_corrupt",
    }
)
_SignalHandler = Callable[[int, FrameType | None], Any] | int | None


@dataclass(frozen=True, slots=True)
class DaemonRunOptions:
    paths: CliWorkspacePaths
    idle_sleep_seconds: float
    max_ticks: int | None
    activation_id: str | None
    adapter_kind: str | None
    local_config: AdapterLocalConfig | None
    monitor: str
    actor_id: str


@dataclass(frozen=True, slots=True)
class DaemonRunSummary:
    iterations: int
    units_started: int
    units_succeeded: int
    units_refused: int
    adapter_failures: int
    idle_iterations: int
    lifecycle_transitions_applied: int
    stopped_reason: str
    workspace: str
    db_path: str
    cas_path: str
    last_result: dict[str, object] = field(default_factory=dict)
    diagnostics: tuple[dict[str, object], ...] = ()
    runner_session: dict[str, object] | None = None

    def data(self) -> dict[str, object]:
        return {
            "iterations": self.iterations,
            "units_started": self.units_started,
            "units_succeeded": self.units_succeeded,
            "units_refused": self.units_refused,
            "adapter_failures": self.adapter_failures,
            "idle_iterations": self.idle_iterations,
            "lifecycle_transitions_applied": self.lifecycle_transitions_applied,
            "stopped_reason": self.stopped_reason,
            "workspace": self.workspace,
            "db_path": self.db_path,
            "cas_path": self.cas_path,
            "last_result": self.last_result,
            "diagnostics": [dict(item) for item in self.diagnostics],
            "runner_session": (
                None if self.runner_session is None else dict(self.runner_session)
            ),
        }


class _DaemonAlreadyRunning(RuntimeError):
    pass


@dataclass(slots=True)
class _DaemonLock:
    path: Path
    token: str
    fd: int

    def release(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if isinstance(payload, dict) and payload.get("token") == self.token:
            self.path.unlink(missing_ok=True)


class _SignalStop:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._previous: dict[int, _SignalHandler] = {}
        self._enabled = False

    def __enter__(self) -> _SignalStop:
        if threading.current_thread() is not threading.main_thread():
            return self
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle)
        self._enabled = True
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        if not self._enabled:
            return
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)

    def _handle(self, _signum: int, _frame: FrameType | None) -> None:
        self._event.set()

    def wait(self, seconds: float) -> bool:
        return self._event.wait(seconds)

    @property
    def requested(self) -> bool:
        return self._event.is_set()


def handle_daemon_command(namespace: object) -> CliSuccess:
    options = daemon_options_from_namespace(namespace)
    progress_stream = (
        sys.stdout
        if options.monitor == "basic" and not bool(getattr(namespace, "json", False))
        else None
    )
    summary = run_daemon_loop(options, progress_stream=progress_stream)
    if _summary_is_success(summary):
        return success_result(
            command=_COMMAND,
            code="daemon_stopped",
            message="Daemon stopped.",
            data=summary.data(),
        )
    raise _summary_error(summary)


def daemon_options_from_namespace(namespace: object) -> DaemonRunOptions:
    idle_sleep_seconds = _idle_sleep_seconds(namespace)
    max_ticks = _max_ticks(namespace)
    activation_id = _optional_nonblank(namespace, "activation_id", "--activation-id")
    adapter_kind = _optional_nonblank(namespace, "adapter_kind", "--adapter-kind")
    if activation_id is not None and max_ticks != 1:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_activation_id",
            message="--activation-id requires exactly --max-ticks 1.",
            exit_code=ExitCode.CLI_USAGE,
            details={"max_ticks": max_ticks},
        )
    monitor = str(getattr(namespace, "monitor", "none"))
    if monitor not in {"none", "basic"}:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_monitor",
            message="--monitor must be one of: none, basic.",
            exit_code=ExitCode.CLI_USAGE,
            details={"monitor": monitor},
        )
    adapter_config_json = getattr(namespace, "adapter_config_json", None)
    local_config = None
    if adapter_config_json is not None:
        try:
            local_config = load_adapter_local_config(Path(str(adapter_config_json)))
        except CliCommandError as exc:
            raise CliCommandError(
                command=_COMMAND,
                code=exc.code,
                message=exc.message,
                exit_code=exc.exit_code,
                details=exc.details,
            ) from exc
    actor_id = require_nonblank(
        str(getattr(namespace, "actor_id", "local_operator")),
        option="--actor-id",
        command=_COMMAND,
    )
    return DaemonRunOptions(
        paths=workspace_paths(namespace),
        idle_sleep_seconds=idle_sleep_seconds,
        max_ticks=max_ticks,
        activation_id=activation_id,
        adapter_kind=adapter_kind,
        local_config=local_config,
        monitor=monitor,
        actor_id=actor_id,
    )


def run_daemon_loop(
    options: DaemonRunOptions,
    *,
    progress_stream: TextIO | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> DaemonRunSummary:
    _validate_options(options)
    try:
        daemon_lock = _acquire_daemon_lock(options.paths, monotonic=monotonic)
    except _DaemonAlreadyRunning:
        return _summary(
            options,
            stopped_reason="daemon_already_running",
            diagnostics=(
                {
                    "lock_path": str(_lock_path(options.paths)),
                    "message": "Inspect/remove the lock manually if no daemon owns it.",
                },
            ),
        )
    except OSError as exc:
        return _summary(
            options,
            stopped_reason="state_open_failed",
            diagnostics=(_exception_diagnostic(exc),),
        )

    try:
        preflight = _preflight_state_load(options)
        if preflight is not None:
            return preflight
        return _run_locked_loop(options, progress_stream=progress_stream)
    finally:
        daemon_lock.release()


def open_runtime_context(namespace: object, *, command: str) -> OpenRuntimeContext:
    if isinstance(namespace, CliWorkspacePaths):
        namespace = SimpleNamespace(
            workspace=str(namespace.workspace_path),
            db=str(namespace.db_path),
            cas=str(namespace.cas_path),
        )
    return _open_runtime_context(namespace, command=command)


def _run_locked_loop(
    options: DaemonRunOptions,
    *,
    progress_stream: TextIO | None,
) -> DaemonRunSummary:
    iterations = 0
    units_started = 0
    units_succeeded = 0
    units_refused = 0
    adapter_failures = 0
    idle_iterations = 0
    lifecycle_transitions_applied = 0
    last_result: dict[str, object] = {}
    last_handled_run_id: str | None = None

    with _SignalStop() as stop:
        startup = _reconcile_startup_sessions(
            options,
            daemon_stop_requested=lambda: stop.requested,
        )
        if startup.code != "no_runner_session_reconciliation":
            last_result = _result_data(startup)
            last_handled_run_id = _result_run_id(startup)
            stopped_reason = _non_idle_stop_reason(startup)
            if stopped_reason is not None:
                return _summary(
                    options,
                    iterations=0,
                    units_started=0,
                    units_succeeded=0,
                    units_refused=0,
                    adapter_failures=(
                        1 if startup.code in _RUNNER_FAILURE_REASONS else 0
                    ),
                    idle_iterations=0,
                    lifecycle_transitions_applied=0,
                    stopped_reason=stopped_reason,
                    last_result=last_result,
                    last_handled_run_id=last_handled_run_id,
                    diagnostics=tuple(
                        dict(item) for item in startup.diagnostics
                    ),
                )
        while options.max_ticks is None or iterations < options.max_ticks:
            result = _run_one_bounded_unit(
                options,
                daemon_stop_requested=lambda: stop.requested,
            )
            iterations += 1
            last_result = _result_data(result)
            result_run_id = _result_run_id(result)
            if result_run_id is not None:
                last_handled_run_id = result_run_id

            if _result_started(result):
                units_started += 1
            if result.code == "observation_accepted" and result.accepted:
                units_succeeded += 1
            elif result.code == "no_ready_work":
                idle_iterations += 1
            elif result.code == "lifecycle_transition_applied":
                lifecycle_transitions_applied += 1
            elif result.code in {"asset_material_refused", "observation_refused"}:
                units_refused += 1
            elif result.code in _RUNNER_FAILURE_REASONS:
                adapter_failures += 1

            if options.monitor == "basic" and progress_stream is not None:
                _render_basic_progress(
                    stream=progress_stream,
                    iteration=iterations,
                    result=result,
                )

            if (
                stop.requested
                and result.adapter_error_kind == "cancelled"
                and result.code != "runner_session_orphan_risk"
            ):
                return _summary(
                    options,
                    iterations=iterations,
                    units_started=units_started,
                    units_succeeded=units_succeeded,
                    units_refused=units_refused,
                    adapter_failures=adapter_failures,
                    idle_iterations=idle_iterations,
                    lifecycle_transitions_applied=lifecycle_transitions_applied,
                    stopped_reason="signal",
                    last_result=last_result,
                    last_handled_run_id=last_handled_run_id,
                )

            stopped_reason = _non_idle_stop_reason(result)
            if stopped_reason is not None:
                return _summary(
                    options,
                    iterations=iterations,
                    units_started=units_started,
                    units_succeeded=units_succeeded,
                    units_refused=units_refused,
                    adapter_failures=adapter_failures,
                    idle_iterations=idle_iterations,
                    lifecycle_transitions_applied=lifecycle_transitions_applied,
                    stopped_reason=stopped_reason,
                    last_result=last_result,
                    last_handled_run_id=last_handled_run_id,
                    diagnostics=tuple(dict(item) for item in result.diagnostics),
                )

            if stop.requested:
                return _summary(
                    options,
                    iterations=iterations,
                    units_started=units_started,
                    units_succeeded=units_succeeded,
                    units_refused=units_refused,
                    adapter_failures=adapter_failures,
                    idle_iterations=idle_iterations,
                    lifecycle_transitions_applied=lifecycle_transitions_applied,
                    stopped_reason="signal",
                    last_result=last_result,
                    last_handled_run_id=last_handled_run_id,
                )

            if options.max_ticks is not None and iterations >= options.max_ticks:
                break
            if result.code == "no_ready_work" and options.idle_sleep_seconds > 0:
                if stop.wait(options.idle_sleep_seconds):
                    return _summary(
                        options,
                        iterations=iterations,
                        units_started=units_started,
                        units_succeeded=units_succeeded,
                        units_refused=units_refused,
                        adapter_failures=adapter_failures,
                        idle_iterations=idle_iterations,
                        lifecycle_transitions_applied=lifecycle_transitions_applied,
                        stopped_reason="signal",
                        last_result=last_result,
                        last_handled_run_id=last_handled_run_id,
                    )

    return _summary(
        options,
        iterations=iterations,
        units_started=units_started,
        units_succeeded=units_succeeded,
        units_refused=units_refused,
        adapter_failures=adapter_failures,
        idle_iterations=idle_iterations,
        lifecycle_transitions_applied=lifecycle_transitions_applied,
        stopped_reason="max_ticks",
        last_result=last_result,
        last_handled_run_id=last_handled_run_id,
    )


def _reconcile_startup_sessions(
    options: DaemonRunOptions,
    *,
    daemon_stop_requested: Callable[[], bool],
) -> BoundedExecutionUnitResult:
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        return reconcile_pending_runner_sessions(
            runtime,
            adapter_kind=options.adapter_kind,
            local_config=options.local_config,
            actor_id=options.actor_id,
            daemon_stop_requested=daemon_stop_requested,
        )
    finally:
        runtime.close()


def _run_one_bounded_unit(
    options: DaemonRunOptions,
    *,
    daemon_stop_requested: Callable[[], bool],
) -> BoundedExecutionUnitResult:
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        if options.activation_id is None:
            lifecycle_result = run_lifecycle_transition_once(runtime)
            if lifecycle_result.code != "no_ready_work":
                return lifecycle_result
        return run_bounded_execution_unit(
            runtime,
            activation_id=options.activation_id,
            adapter_kind=options.adapter_kind,
            local_config=options.local_config,
            actor_id=options.actor_id,
            daemon_stop_requested=daemon_stop_requested,
        )
    finally:
        runtime.close()


def _preflight_state_load(options: DaemonRunOptions) -> DaemonRunSummary | None:
    try:
        runtime = open_runtime_context(options.paths, command=_COMMAND)
        try:
            runtime.store.load_runtime_state(runtime.cas_store)
        finally:
            runtime.close()
    except CliCommandError as exc:
        if exc.code == "workspace_upgrade_required":
            raise
        return _summary(
            options,
            stopped_reason="state_open_failed",
            diagnostics=(_exception_diagnostic(exc),),
        )
    except (OSError, SubstrateError) as exc:
        return _summary(
            options,
            stopped_reason="state_open_failed",
            diagnostics=(_exception_diagnostic(exc),),
        )
    return None


def _acquire_daemon_lock(
    paths: CliWorkspacePaths,
    *,
    monotonic: Callable[[], float],
) -> _DaemonLock:
    lock_path = _lock_path(paths)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise _DaemonAlreadyRunning(str(lock_path)) from exc
    payload = {
        "pid": os.getpid(),
        "workspace": str(paths.workspace_path),
        "created_monotonic": monotonic(),
        "token": token,
        "diagnostic_only": True,
    }
    try:
        os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
        os.write(fd, b"\n")
        os.fsync(fd)
    except Exception:
        os.close(fd)
        lock_path.unlink(missing_ok=True)
        raise
    return _DaemonLock(path=lock_path, token=token, fd=fd)


def _lock_path(paths: CliWorkspacePaths) -> Path:
    return paths.workspace_path / ".millrace" / _LOCK_FILENAME


def _validate_options(options: DaemonRunOptions) -> None:
    if not isinstance(options.paths, CliWorkspacePaths):
        raise TypeError("paths must be CliWorkspacePaths")
    if options.max_ticks is not None and options.max_ticks < 1:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_max_ticks",
            message="--max-ticks must be at least 1.",
            exit_code=ExitCode.CLI_USAGE,
            details={"max_ticks": options.max_ticks},
        )
    if options.idle_sleep_seconds < 0:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_idle_sleep",
            message="--idle-sleep must be non-negative.",
            exit_code=ExitCode.CLI_USAGE,
            details={"idle_sleep": options.idle_sleep_seconds},
        )
    if options.activation_id is not None and options.max_ticks != 1:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_activation_id",
            message="--activation-id requires exactly --max-ticks 1.",
            exit_code=ExitCode.CLI_USAGE,
            details={"max_ticks": options.max_ticks},
        )
    if options.monitor not in {"none", "basic"}:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_monitor",
            message="--monitor must be one of: none, basic.",
            exit_code=ExitCode.CLI_USAGE,
            details={"monitor": options.monitor},
        )


def _idle_sleep_seconds(namespace: object) -> float:
    value = float(getattr(namespace, "idle_sleep", 1.0))
    if value < 0:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_idle_sleep",
            message="--idle-sleep must be non-negative.",
            exit_code=ExitCode.CLI_USAGE,
            details={"idle_sleep": value},
        )
    return value


def _max_ticks(namespace: object) -> int | None:
    value = getattr(namespace, "max_ticks", None)
    if value is None:
        return None
    parsed = int(value)
    if parsed < 1:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_max_ticks",
            message="--max-ticks must be at least 1.",
            exit_code=ExitCode.CLI_USAGE,
            details={"max_ticks": parsed},
        )
    return parsed


def _optional_nonblank(
    namespace: object,
    attribute: str,
    option: str,
) -> str | None:
    value = getattr(namespace, attribute, None)
    if value is None:
        return None
    return require_nonblank(str(value), option=option, command=_COMMAND)


def _non_idle_stop_reason(result: BoundedExecutionUnitResult) -> str | None:
    if result.code in {
        "lifecycle_transition_applied",
        "no_ready_work",
        "observation_accepted",
    }:
        return None
    return result.code


def _result_started(result: BoundedExecutionUnitResult) -> bool:
    return result.activation_id is not None or result.run_id is not None


def _result_run_id(result: BoundedExecutionUnitResult) -> str | None:
    return result.run_id if isinstance(result.run_id, str) and result.run_id else None


def _result_data(result: BoundedExecutionUnitResult) -> dict[str, object]:
    return {
        "code": result.code,
        "accepted": result.accepted,
        "activation_id": result.activation_id,
        "run_id": result.run_id,
        "claim_id": result.claim_id,
        "fencing_token": result.fencing_token,
        "adapter_error_kind": result.adapter_error_kind,
        "observation_refusal_reason": result.observation_refusal_reason,
        "transition_disposition": result.transition_disposition,
        "diagnostics": [dict(item) for item in result.diagnostics],
    }


def _summary(
    options: DaemonRunOptions,
    *,
    iterations: int = 0,
    units_started: int = 0,
    units_succeeded: int = 0,
    units_refused: int = 0,
    adapter_failures: int = 0,
    idle_iterations: int = 0,
    lifecycle_transitions_applied: int = 0,
    stopped_reason: str,
    last_result: dict[str, object] | None = None,
    last_handled_run_id: str | None = None,
    diagnostics: tuple[dict[str, object], ...] = (),
) -> DaemonRunSummary:
    result_data = last_result or {}
    return DaemonRunSummary(
        iterations=iterations,
        units_started=units_started,
        units_succeeded=units_succeeded,
        units_refused=units_refused,
        adapter_failures=adapter_failures,
        idle_iterations=idle_iterations,
        lifecycle_transitions_applied=lifecycle_transitions_applied,
        stopped_reason=stopped_reason,
        workspace=str(options.paths.workspace_path),
        db_path=str(options.paths.db_path),
        cas_path=str(options.paths.cas_path),
        last_result=result_data,
        diagnostics=diagnostics,
        runner_session=_summary_runner_session(options, last_handled_run_id),
    )


def _summary_runner_session(
    options: DaemonRunOptions,
    last_handled_run_id: str | None,
) -> dict[str, object] | None:
    from millrace.adapters.cli.status import runner_session_projection

    if last_handled_run_id is None:
        return None
    try:
        runtime = open_runtime_context(options.paths, command=_COMMAND)
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
        finally:
            runtime.close()
    except (CliCommandError, OSError, SubstrateError):
        return None
    projection = runner_session_projection(state, last_handled_run_id)
    if projection is None:
        return None
    return projection


def _exception_diagnostic(exc: Exception) -> dict[str, object]:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", str(exc))
    return {
        "error": type(exc).__name__,
        "code": None if code is None else str(code),
        "message": str(message),
    }


def _summary_is_success(summary: DaemonRunSummary) -> bool:
    return summary.stopped_reason in _SUCCESS_REASONS


def _summary_error(summary: DaemonRunSummary) -> CliCommandError:
    return CliCommandError(
        command=_COMMAND,
        code=_error_code(summary),
        message=_error_message(summary),
        exit_code=_exit_code(summary),
        details=summary.data(),
    )


def _error_code(summary: DaemonRunSummary) -> str:
    if summary.stopped_reason == "state_open_failed":
        return "daemon_state_open_failed"
    return summary.stopped_reason


def _error_message(summary: DaemonRunSummary) -> str:
    if summary.stopped_reason == "daemon_already_running":
        return (
            "Another Millrace daemon is already running or the daemon lock is "
            "ambiguous; inspect/remove the lock manually."
        )
    if summary.stopped_reason == "state_open_failed":
        return "Daemon could not open or validate runtime state."
    return "Daemon stopped before successful completion."


def _exit_code(summary: DaemonRunSummary) -> ExitCode:
    if summary.stopped_reason in _RUNNER_FAILURE_REASONS:
        return ExitCode.RUNNER_FAILURE
    if summary.stopped_reason in _DOMAIN_REFUSAL_REASONS:
        return ExitCode.DOMAIN_REFUSAL
    if summary.stopped_reason in _PERSISTENCE_FAILURE_REASONS:
        return ExitCode.PERSISTENCE_FAILURE
    return ExitCode.INTERNAL_ERROR


def _render_basic_progress(
    *,
    stream: TextIO,
    iteration: int,
    result: BoundedExecutionUnitResult,
) -> None:
    stream.write(
        "daemon tick "
        f"iteration={iteration} "
        f"code={result.code} "
        f"activation_id={result.activation_id or ''} "
        f"run_id={result.run_id or ''}\n"
    )
