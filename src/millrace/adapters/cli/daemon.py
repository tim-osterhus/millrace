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

from millrace.adapters.cli import session_cancellation as cancellation
from millrace.adapters.cli import session_coordinator as sessions
from millrace.adapters.cli.context import (
    CliCommandError,
    CliWorkspacePaths,
    OpenRuntimeContext,
    require_nonblank,
    terminalize_daemon_budget_with_suspension,
    workspace_paths,
)
from millrace.adapters.cli.context import (
    open_runtime_context as _open_runtime_context,
)
from millrace.adapters.cli.daemon_control import ACTIVE_LIFECYCLE, DaemonLifecycle
from millrace.adapters.cli.lifecycle import run_lifecycle_transition_once
from millrace.adapters.cli.output import (
    CliSuccess,
    ExitCode,
    json_ready,
    success_result,
)
from millrace.adapters.cli.run import (
    BoundedExecutionUnitResult,
    classify_daemon_startup,
    load_adapter_local_config,
    reconcile_pending_runner_completions,
    reconcile_pending_runner_sessions,
    run_bounded_execution_unit,
)
from millrace.adapters.runner_contract import (
    AdapterLocalConfig,
    AdapterResolverError,
    has_reviewed_token_usage_mapping,
    resolve_adapter,
)
from millrace.contracts.state import (
    DURABLE_INT64_MAX,
    RUNNER_SESSION_TEXT_MAX_BYTES,
    DaemonBudgetEpochRecord,
    RunnerSessionRecord,
    RuntimeState,
)
from millrace.substrate.errors import (
    ControlOperationError,
    StorageIntegrityError,
    SubstrateError,
)

_COMMAND = "run.daemon"
_LOCK_FILENAME = "daemon.lock"
_BUDGET_STOP_ID_MAX_BYTES = RUNNER_SESSION_TEXT_MAX_BYTES - len(
    b"daemon-budget::suspend"
)
_SUCCESS_REASONS = frozenset({"max_ticks", "signal", "budget_exhausted"})
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
    budget_id: str | None = None
    max_wall_seconds: int | None = None
    max_invocations: int | None = None
    max_total_tokens: int | None = None


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
    budget: dict[str, object] | None = None

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
            "budget": None if self.budget is None else dict(self.budget),
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
        self._signal_received = False
        self._signal_recorded = False

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
        try:
            if self._enabled:
                for signum, handler in self._previous.items():
                    signal.signal(signum, handler)
        finally:
            # Restoring handlers closes this receiver before its final durable drain.
            # Every terminal return/exception leaves this context before finalization.
            self._persist_received_signal()

    def _handle(self, _signum: int, _frame: FrameType | None) -> None:
        self._signal_received = True
        self._event.set()

    def wait(self, seconds: float) -> bool:
        return self._event.wait(seconds)

    def _persist_received_signal(self) -> None:
        lifecycle = ACTIVE_LIFECYCLE.get()
        if (
            self._signal_received
            and not self._signal_recorded
            and lifecycle is not None
            and lifecycle.scope
        ):
            lifecycle.signal_stop()
            self._signal_recorded = True

    @property
    def requested(self) -> bool:
        lifecycle = ACTIVE_LIFECYCLE.get()
        if lifecycle is not None and lifecycle.scope:
            lifecycle.stop_requested()
        # Snapshot the event before checking received signals: returning a newly
        # signaled event can never outrun this durable drain. A later signal is
        # caught by the next observation or the context's terminal drain.
        requested = self._event.is_set()
        self._persist_received_signal()
        return requested


def handle_daemon_command(namespace: object) -> CliSuccess:
    options = daemon_options_from_namespace(namespace)
    progress_stream = (
        sys.stdout
        if options.monitor == "basic" and not bool(getattr(namespace, "json", False))
        else None
    )
    # Exact process birth/peer observation is currently macOS-only. Other
    # platforms retain foreground execution without advertising native control.
    lifecycle = (
        DaemonLifecycle(
            options.paths,
            str(getattr(namespace, "launch_correlation_id", None) or uuid4()),
        )
        if sys.platform == "darwin"
        else None
    )
    token = ACTIVE_LIFECYCLE.set(lifecycle)
    try:
        summary = run_daemon_loop(options, progress_stream=progress_stream)
    except CliCommandError:
        raise
    except (OSError, ValueError, ControlOperationError) as exc:
        raise CliCommandError(
            command=_COMMAND,
            code="daemon_lifecycle_refused",
            message="Daemon lifecycle could not establish compatible owner authority.",
            exit_code=ExitCode.DOMAIN_REFUSAL,
            details={"status": "unknown", "ready": False},
        ) from exc
    finally:
        ACTIVE_LIFECYCLE.reset(token)
    if _summary_is_success(summary):
        return success_result(
            command=_COMMAND,
            code="daemon_stopped",
            message="Daemon stopped.",
            data=summary.data(),
        )
    raise _summary_error(summary)


def handle_run_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "run"))
    if command == _COMMAND:
        return handle_daemon_command(namespace)
    if command == "run.budget-stop":
        return handle_budget_stop_command(namespace)
    raise CliCommandError(
        command=command,
        code="command_not_implemented",
        message="Command is not implemented.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def handle_budget_stop_command(namespace: object) -> CliSuccess:
    command = "run.budget-stop"
    budget_id_value = str(getattr(namespace, "budget_id", ""))
    if len(budget_id_value.encode("utf-8")) > _BUDGET_STOP_ID_MAX_BYTES:
        raise CliCommandError(
            command=command,
            code="invalid_budget_id",
            message=(
                f"--budget-id must be at most {_BUDGET_STOP_ID_MAX_BYTES} UTF-8 bytes."
            ),
            exit_code=ExitCode.CLI_USAGE,
            details={},
        )
    budget_id = require_nonblank(
        budget_id_value,
        option="--budget-id",
        command=command,
    )
    actor = require_nonblank(
        str(getattr(namespace, "actor_id", "local_operator")),
        option="--actor-id",
        command=command,
    )
    runtime = open_runtime_context(namespace, command=command)
    replayed = False
    try:
        try:
            epoch = runtime.store.load_daemon_budget_epoch(budget_id)
        except ValueError as exc:
            raise _budget_projection_refusal(command, budget_id, exc) from exc
        if epoch is None:
            raise _budget_stop_refusal(
                command,
                code="budget_not_found",
                details={"budget_id": budget_id},
            )
        if epoch.workspace_path != str(runtime.paths.workspace_path):
            raise _budget_stop_refusal(
                command,
                code="budget_workspace_mismatch",
                details={
                    "budget_id": budget_id,
                    "budget_workspace": epoch.workspace_path,
                    "workspace": str(runtime.paths.workspace_path),
                },
            )
        replayed = (
            epoch.status == "stopped" and epoch.terminal_reason == "operator_completed"
        )
        if epoch.status != "active" and not replayed:
            raise _budget_stop_refusal(
                command,
                code="budget_terminal_conflict",
                details={
                    "budget_id": budget_id,
                    "status": epoch.status,
                    "terminal_reason": epoch.terminal_reason,
                },
            )
        if replayed:
            try:
                state = runtime.store.load_runtime_state(runtime.cas_store)
                _validate_budget_stop_preconditions(
                    runtime,
                    state,
                    epoch,
                    command=command,
                )
            except CliCommandError:
                raise
            except ValueError as exc:
                raise _budget_projection_refusal(command, budget_id, exc) from exc
        else:

            def validate_budget_stop(
                checked_runtime: OpenRuntimeContext,
                checked_state: object,
                checked_epoch: DaemonBudgetEpochRecord,
            ) -> None:
                if checked_epoch.status != "active":
                    raise _budget_stop_refusal(
                        command,
                        code="budget_terminal_conflict",
                        details={
                            "budget_id": checked_epoch.budget_id,
                            "status": checked_epoch.status,
                            "terminal_reason": checked_epoch.terminal_reason,
                        },
                    )
                _validate_budget_stop_preconditions(
                    checked_runtime,
                    checked_state,
                    checked_epoch,
                    command=command,
                )

            try:
                terminalize_daemon_budget_with_suspension(
                    runtime,
                    budget_id=budget_id,
                    observed_at=max(int(time.time()), epoch.last_observed_at),
                    status="stopped",
                    reason="operator_completed",
                    command=command,
                    actor_id=actor,
                    validate=validate_budget_stop,
                )
            except CliCommandError:
                raise
            except ValueError as exc:
                raise _budget_projection_refusal(command, budget_id, exc) from exc
        final_epoch = runtime.store.load_daemon_budget_epoch(budget_id)
        if final_epoch is None:
            raise _budget_projection_refusal(
                command,
                budget_id,
                ValueError("daemon budget epoch disappeared after terminalization"),
            )
        final_state = runtime.store.load_runtime_state(runtime.cas_store)
        budget = _budget_stop_projection(runtime, final_state, final_epoch)
        suspension = final_state.dispatch_suspension
        if suspension is None:
            raise CliCommandError(
                command=command,
                code="budget_dispatch_suspension_missing",
                message="Budget stopped without a dispatch-suspension projection.",
                exit_code=ExitCode.PERSISTENCE_FAILURE,
                details={"budget_id": budget_id},
            )
    finally:
        runtime.close()
    return success_result(
        command=command,
        code="budget_stopped",
        message="Daemon budget stopped.",
        data={
            "budget": budget,
            "dispatch_suspension": json_ready(suspension),
            "replayed": replayed,
        },
    )


def _validate_budget_stop_preconditions(
    runtime: OpenRuntimeContext,
    state: object,
    epoch: DaemonBudgetEpochRecord,
    *,
    command: str,
) -> None:
    if epoch.status == "active":
        if getattr(state, "default_plan_ref", None) != epoch.selected_plan_ref:
            raise _budget_stop_refusal(
                command,
                code="budget_plan_mismatch",
                details={"budget_id": epoch.budget_id},
            )
        suspension = getattr(state, "dispatch_suspension", None)
        if (
            suspension is not None
            and suspension.status == "active"
            and suspension.selected_plan_ref != epoch.selected_plan_ref
        ):
            raise _budget_stop_refusal(
                command,
                code="budget_dispatch_suspension_mismatch",
                details={"budget_id": epoch.budget_id},
            )
    pending_ids = runtime.store.pending_budgeted_runner_start_session_ids(
        epoch.budget_id
    )
    if pending_ids:
        raise _budget_stop_refusal(
            command,
            code="budget_pending_reservation",
            details={
                "budget_id": epoch.budget_id,
                "session_ids": list(pending_ids[:100]),
                "session_count": len(pending_ids),
            },
        )
    session_count, session_ids = runtime.store.daemon_budget_session_ids(
        epoch.budget_id,
        limit=100,
    )
    if session_count != len(session_ids):
        raise _budget_stop_refusal(
            command,
            code="budget_session_projection_unbounded",
            details={
                "budget_id": epoch.budget_id,
                "session_count": session_count,
                "projection_limit": 100,
            },
        )
    for session_id in session_ids:
        session = getattr(state, "runner_sessions", {}).get(session_id)
        if session is None:
            raise _budget_stop_refusal(
                command,
                code="budget_session_missing",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )
        bound_budget_id = runtime.store.daemon_budget_id_for_session(session_id)
        if bound_budget_id != epoch.budget_id:
            raise _budget_stop_refusal(
                command,
                code="budget_session_identity_mismatch",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )
        run = getattr(state, "runs", {}).get(session.run_id)
        if (
            run is None
            or run.run_ref.run_id != session.run_id
            or run.run_ref.plan_ref != epoch.selected_plan_ref
        ):
            raise _budget_stop_refusal(
                command,
                code="budget_session_identity_mismatch",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )
        if session.state == "lost":
            raise _budget_stop_refusal(
                command,
                code="budget_session_lost",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )
        if session.state not in {"completed", "interrupted", "failed"}:
            raise _budget_stop_refusal(
                command,
                code="budget_session_not_terminal",
                details={
                    "budget_id": epoch.budget_id,
                    "session_id": session_id,
                    "state": session.state,
                },
            )
        if session.cleanup_disposition == "orphan_risk":
            raise _budget_stop_refusal(
                command,
                code="budget_session_orphan_risk",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )
        if session.cleanup_disposition not in {"complete", "not_required"}:
            raise _budget_stop_refusal(
                command,
                code="budget_session_cleanup_incomplete",
                details={
                    "budget_id": epoch.budget_id,
                    "session_id": session_id,
                    "cleanup_disposition": session.cleanup_disposition,
                },
            )
        completion = getattr(state, "runner_session_completions", {}).get(session_id)
        if completion is None:
            raise _budget_stop_refusal(
                command,
                code="budget_session_completion_missing",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )
        if (
            completion.session_id != session.session_id
            or completion.run_id != session.run_id
            or completion.dispatch_generation != session.dispatch_generation
            or completion.session_fencing_token != session.session_fencing_token
            or completion.terminal_state != session.state
            or completion.cleanup_disposition != session.cleanup_disposition
        ):
            raise _budget_stop_refusal(
                command,
                code="budget_session_identity_mismatch",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )
        try:
            usage = runtime.store.load_runner_session_usage(session_id)
        except ValueError as exc:
            raise _budget_projection_refusal(
                command,
                epoch.budget_id,
                exc,
                session_id=session_id,
            ) from exc
        if usage is None:
            raise _budget_stop_refusal(
                command,
                code="budget_session_usage_missing",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )
        if (
            usage.budget_id != epoch.budget_id
            or usage.run_id != session.run_id
            or usage.dispatch_generation != session.dispatch_generation
            or usage.session_fencing_token != session.session_fencing_token
        ):
            raise _budget_stop_refusal(
                command,
                code="budget_session_identity_mismatch",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )
        if not usage.final:
            raise _budget_stop_refusal(
                command,
                code="budget_session_usage_not_final",
                details={"budget_id": epoch.budget_id, "session_id": session_id},
            )


def _budget_stop_projection(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    epoch: DaemonBudgetEpochRecord,
) -> dict[str, object]:
    from millrace.adapters.cli.status import _daemon_budget_projection

    return _daemon_budget_projection(runtime, state, epoch)


def _budget_stop_refusal(
    command: str,
    *,
    code: str,
    details: dict[str, object],
) -> CliCommandError:
    return CliCommandError(
        command=command,
        code=code,
        message="Daemon budget stop was refused.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details=details,
    )


def _budget_projection_refusal(
    command: str,
    budget_id: str,
    exc: ValueError,
    *,
    session_id: str | None = None,
) -> CliCommandError:
    details: dict[str, object] = {
        "budget_id": budget_id,
        "reason": str(exc),
    }
    if session_id is not None:
        details["session_id"] = session_id
    return _budget_stop_refusal(
        command,
        code="budget_projection_corrupt",
        details=details,
    )


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
    budget_id = _optional_nonblank(namespace, "budget_id", "--budget-id")
    max_wall_seconds = _optional_positive_limit(
        namespace,
        "max_wall_seconds",
        "--max-wall-seconds",
    )
    max_invocations = _optional_positive_limit(
        namespace,
        "max_invocations",
        "--max-invocations",
    )
    max_total_tokens = _optional_positive_limit(
        namespace,
        "max_total_tokens",
        "--max-total-tokens",
    )
    if (
        any(
            value is not None
            for value in (
                max_wall_seconds,
                max_invocations,
                max_total_tokens,
            )
        )
        and budget_id is None
    ):
        raise CliCommandError(
            command=_COMMAND,
            code="budget_id_required",
            message="Any daemon budget limit requires --budget-id.",
            exit_code=ExitCode.CLI_USAGE,
            details={},
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
        budget_id=budget_id,
        max_wall_seconds=max_wall_seconds,
        max_invocations=max_invocations,
        max_total_tokens=max_total_tokens,
    )


def run_daemon_loop(
    options: DaemonRunOptions,
    *,
    progress_stream: TextIO | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
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
        lifecycle = ACTIVE_LIFECYCLE.get()
        if lifecycle is not None:
            lifecycle.initialize()
        budget = _prepare_budget_epoch(options, now=int(wall_clock()))
        result = _run_locked_loop(
            options,
            progress_stream=progress_stream,
            wall_clock=wall_clock,
            budget=budget,
        )
        return result
    finally:
        lifecycle = ACTIVE_LIFECYCLE.get()
        try:
            if lifecycle is not None and lifecycle.scope:
                lifecycle.finish(
                    result.stopped_reason if "result" in locals() else "daemon_failed"
                )
        finally:
            daemon_lock.release()


def open_runtime_context(namespace: object, *, command: str) -> OpenRuntimeContext:
    if isinstance(namespace, CliWorkspacePaths):
        namespace = SimpleNamespace(
            workspace=str(namespace.workspace_path),
            db=str(namespace.db_path),
            cas=str(namespace.cas_path),
        )
    runtime = _open_runtime_context(namespace, command=command)
    lifecycle = ACTIVE_LIFECYCLE.get()
    if lifecycle is not None and lifecycle.scope:
        runtime.store.daemon_scope = lifecycle.scope
    return runtime


def _run_locked_loop(
    options: DaemonRunOptions,
    *,
    progress_stream: TextIO | None,
    wall_clock: Callable[[], float],
    budget: DaemonBudgetEpochRecord | None,
) -> DaemonRunSummary:
    owners: dict[str, sessions._RetainedOwner] = {}
    token = sessions._RETAINED_OWNERS.set(owners)
    iterations = units_succeeded = units_refused = adapter_failures = 0
    idle_iterations = lifecycle_transitions_applied = 0
    started_runs: set[str] = set()
    last_result: dict[str, object] = {}
    last_handled_run_id: str | None = None
    diagnostics: list[dict[str, object]] = []
    stopped_reason: str | None = None
    budget_reason: str | None = None

    def wall_expired() -> bool:
        return (
            budget is not None
            and budget.wall_deadline is not None
            and int(wall_clock()) >= budget.wall_deadline
        )

    def record(result: BoundedExecutionUnitResult) -> None:
        nonlocal last_result, last_handled_run_id, units_succeeded
        nonlocal units_refused, adapter_failures, idle_iterations
        nonlocal lifecycle_transitions_applied
        last_result = _result_data(result)
        result_run_id = _result_run_id(result)
        if result_run_id is not None:
            last_handled_run_id = result_run_id
        if _result_started(result):
            started_runs.add(result.run_id or str(result.activation_id))
        if result.code == "observation_accepted" and result.accepted:
            units_succeeded += 1
        elif result.code in {"no_ready_work", "runner_session_waiting"}:
            idle_iterations += 1
        elif result.code == "lifecycle_transition_applied":
            lifecycle_transitions_applied += 1
        elif result.code in {"asset_material_refused", "observation_refused"}:
            units_refused += 1
        elif result.code in _RUNNER_FAILURE_REASONS:
            adapter_failures += 1
        diagnostics.extend(dict(item) for item in result.diagnostics)
        if options.monitor == "basic" and progress_stream is not None:
            _render_basic_progress(
                stream=progress_stream,
                iteration=iterations,
                result=result,
            )

    def service(*, drain: bool) -> None:
        nonlocal stopped_reason
        # A fresh context for each turn; no retained object owns its connection.
        for session_id, owner in tuple(owners.items()):
            runtime: OpenRuntimeContext | None = None
            try:
                runtime = open_runtime_context(options.paths, command=_COMMAND)
                result = sessions._step_retained_owner(
                    runtime,
                    owner,
                    stop_requested=drain,
                )
            except Exception as exc:
                if isinstance(exc, (StorageIntegrityError, ControlOperationError)) and (
                    str(exc).startswith("stale runtime state ")
                    or str(exc) == "stale_runtime_controls"
                ):
                    # Retain the exact consumed outcome and reload next turn.
                    continue
                diagnostics.append(_exception_diagnostic(exc))
                stopped_reason = stopped_reason or "session_reconciliation_required"
                if runtime is not None:
                    result = cancellation._emergency_cleanup_live_handle(
                        runtime,
                        run_ref=owner.run_ref,
                        session=owner.session,
                        handle=owner.handle,
                    )
                else:
                    # A failed store open cannot leave another accepted owner
                    # unvisited. No durable completion is claimed in this case.
                    cancellation._call_cancellation_operation(
                        "cooperative_cancel",
                        owner.handle.request_cancel,
                    )
                    cancellation._call_cancellation_operation(
                        "terminate",
                        owner.handle.terminate,
                    )
                    cancellation._call_cancellation_operation("kill", owner.handle.kill)
                    cleanup = cancellation._call_cleanup(owner.handle.cleanup)
                    result = sessions.SessionExecutionResult(
                        "runner_session_orphan_risk"
                        if cleanup.disposition == "orphan_risk"
                        else "session_reconciliation_required",
                    )
            finally:
                if runtime is not None:
                    runtime.close()
            if result is None:
                continue
            del owners[session_id]
            envelope = owner.request.dispatch_envelope
            bounded = BoundedExecutionUnitResult(
                code=result.code,
                accepted=result.accepted,
                activation_id=envelope.activation_id,
                run_id=owner.run_ref.run_id,
                claim_id=envelope.claim_id,
                fencing_token=envelope.fencing_token,
                adapter_error_kind=result.adapter_error_kind,
                observation_refusal_reason=result.observation_refusal_reason,
                transition_disposition=result.transition_disposition,
            )
            record(bounded)
            reason = _non_idle_stop_reason(bounded)
            if (
                reason == "runner_session_orphan_risk"
                or (
                    stopped_reason != "runner_session_orphan_risk"
                    and reason
                    in {
                        "session_reconciliation_required",
                        "runner_session_reconciliation_contradiction",
                    }
                )
                or (reason is not None and stopped_reason is None)
            ):
                stopped_reason = reason

    try:
        with _SignalStop() as stop:
            lifecycle = ACTIVE_LIFECYCLE.get()
            if lifecycle is not None:
                runtime = open_runtime_context(options.paths, command=_COMMAND)
                try:
                    readiness = classify_daemon_startup(
                        runtime,
                        local_config=options.local_config,
                        adapter_kind=options.adapter_kind,
                    )
                finally:
                    runtime.close()
                lifecycle.ready(readiness, stop._event)
                if readiness == "not_ready":
                    stopped_reason = "ready_state_refused"
            try:
                if stopped_reason is None:
                    _account_budgeted_starts(options)
                    startup = _reconcile_startup_sessions(
                        options,
                        daemon_stop_requested=lambda: stop.requested or wall_expired(),
                        max_timeout_seconds=_remaining_wall_seconds(budget, wall_clock),
                        driving_budget_clock=wall_clock,
                    )
                    if startup.code != "no_runner_session_reconciliation":
                        record(startup)
                        stopped_reason = _non_idle_stop_reason(startup)
                while stopped_reason is None:
                    if stop.requested:
                        stopped_reason = "signal"
                        break
                    if wall_expired():
                        budget_reason = "wall_time_exhausted"
                        stopped_reason = "budget_exhausted"
                        break
                    service(drain=False)
                    if stopped_reason is not None:
                        break
                    budget_reason = _budget_exhaustion_reason(options)
                    if budget_reason is not None and (
                        not owners
                        or budget_reason
                        not in {"invocation_limit_exhausted", "token_limit_exhausted"}
                    ):
                        stopped_reason = "budget_exhausted"
                        break
                    if (
                        options.max_ticks is not None
                        and iterations >= options.max_ticks
                    ):
                        # A foreground unit still runs to terminal/verified hold.
                        if not owners or all(owner.held for owner in owners.values()):
                            stopped_reason = "max_ticks"
                            break
                    elif budget_reason is None and all(
                        owner.held for owner in owners.values()
                    ):
                        result = _run_one_bounded_unit(
                            options,
                            daemon_stop_requested=lambda: (
                                stop.requested or wall_expired()
                            ),
                            max_timeout_seconds=_remaining_wall_seconds(
                                budget, wall_clock
                            ),
                            driving_budget_clock=wall_clock,
                        )
                        iterations += 1
                        record(result)
                        stopped_reason = _non_idle_stop_reason(result)
                        if stopped_reason is not None:
                            break
                        if not owners and result.code not in {
                            "no_ready_work",
                            "runner_session_waiting",
                        }:
                            continue
                    if (
                        not owners
                        and options.max_ticks is not None
                        and iterations >= options.max_ticks
                    ):
                        continue
                    wait_seconds = (
                        sessions._POLL_INTERVAL_SECONDS
                        if owners
                        else options.idle_sleep_seconds
                    )
                    if wait_seconds > 0 and stop.wait(wait_seconds):
                        _ = stop.requested
                        stopped_reason = "signal"
                        break
            finally:
                # Every normal exit and exception settles/cleans every accepted
                # owner before lifecycle.finish and lock release in the caller.
                while owners:
                    service(drain=True)
                    if owners:
                        time.sleep(cancellation._POLL_INTERVAL_SECONDS)
    finally:
        sessions._RETAINED_OWNERS.reset(token)
    if stopped_reason == "budget_exhausted" and budget_reason is not None:
        _finish_budget(options, observed_at=int(wall_clock()), reason=budget_reason)
    return _summary(
        options,
        iterations=iterations,
        units_started=len(started_runs),
        units_succeeded=units_succeeded,
        units_refused=units_refused,
        adapter_failures=adapter_failures,
        idle_iterations=idle_iterations,
        lifecycle_transitions_applied=lifecycle_transitions_applied,
        stopped_reason=stopped_reason or "max_ticks",
        last_result=last_result,
        last_handled_run_id=last_handled_run_id,
        diagnostics=tuple(diagnostics),
    )


def _reconcile_startup_sessions(
    options: DaemonRunOptions,
    *,
    daemon_stop_requested: Callable[[], bool],
    max_timeout_seconds: float | None = None,
    driving_budget_clock: Callable[[], float] | None = None,
) -> BoundedExecutionUnitResult:
    if options.activation_id is not None:
        return BoundedExecutionUnitResult(code="no_runner_session_reconciliation")
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        return reconcile_pending_runner_sessions(
            runtime,
            adapter_kind=options.adapter_kind,
            local_config=options.local_config,
            actor_id=options.actor_id,
            driving_budget_id=options.budget_id,
            driving_budget_clock=driving_budget_clock,
            on_accepted_start=(
                None
                if options.budget_id is None
                else lambda session: _account_budgeted_start(options, session)
            ),
            daemon_stop_requested=daemon_stop_requested,
            max_timeout_seconds=max_timeout_seconds,
        )
    finally:
        runtime.close()


def _run_one_bounded_unit(
    options: DaemonRunOptions,
    *,
    daemon_stop_requested: Callable[[], bool],
    max_timeout_seconds: float | None = None,
    driving_budget_clock: Callable[[], float] | None = None,
) -> BoundedExecutionUnitResult:
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        runtime.store.admit_daemon_unit()
        waiting_completion = None
        if options.activation_id is None:
            lifecycle_result = run_lifecycle_transition_once(runtime)
            if lifecycle_result.code != "no_ready_work":
                return lifecycle_result
            reconciliation_result = reconcile_pending_runner_completions(
                runtime,
                adapter_kind=options.adapter_kind,
                local_config=options.local_config,
                actor_id=options.actor_id,
                daemon_stop_requested=daemon_stop_requested,
                max_timeout_seconds=max_timeout_seconds,
            )
            if reconciliation_result.code == "runner_session_waiting":
                waiting_completion = reconciliation_result
            elif reconciliation_result.code != "no_runner_session_reconciliation":
                return reconciliation_result
        result = run_bounded_execution_unit(
            runtime,
            activation_id=options.activation_id,
            adapter_kind=options.adapter_kind,
            local_config=options.local_config,
            actor_id=options.actor_id,
            driving_budget_id=options.budget_id,
            driving_budget_clock=driving_budget_clock,
            on_accepted_start=(
                None
                if options.budget_id is None
                else lambda session: _account_budgeted_start(options, session)
            ),
            daemon_stop_requested=daemon_stop_requested,
            max_timeout_seconds=max_timeout_seconds,
        )
        if result.code == "no_ready_work" and waiting_completion is not None:
            return waiting_completion
        return result
    except ControlOperationError as exc:
        if str(exc) != "daemon_admission_stopped":
            raise
        return BoundedExecutionUnitResult(
            code="no_ready_work", diagnostics=({"reason": "daemon_stop_requested"},)
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


def _prepare_budget_epoch(
    options: DaemonRunOptions,
    *,
    now: int,
) -> DaemonBudgetEpochRecord | None:
    limits = (
        options.max_wall_seconds,
        options.max_invocations,
        options.max_total_tokens,
    )
    if all(value is None for value in limits):
        if options.budget_id is not None:
            raise CliCommandError(
                command=_COMMAND,
                code="budget_limit_required",
                message="--budget-id requires at least one daemon budget limit.",
                exit_code=ExitCode.CLI_USAGE,
                details={"budget_id": options.budget_id},
            )
        return None
    if options.max_total_tokens is not None:
        try:
            adapter = resolve_adapter(
                require_nonblank(
                    options.adapter_kind or "",
                    option="--adapter-kind",
                    command=_COMMAND,
                ),
                options.local_config or AdapterLocalConfig(),
            )
        except (AdapterResolverError, CliCommandError, TypeError, ValueError) as exc:
            raise CliCommandError(
                command=_COMMAND,
                code="runner_usage_mapping_unsupported",
                message=(
                    "--max-total-tokens requires a resolved reviewed usage mapping."
                ),
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={"adapter_kind": options.adapter_kind},
            ) from exc
        if not has_reviewed_token_usage_mapping(adapter):
            raise CliCommandError(
                command=_COMMAND,
                code="runner_usage_mapping_unsupported",
                message=(
                    "--max-total-tokens requires a resolved reviewed usage mapping."
                ),
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={"adapter_kind": options.adapter_kind},
            )
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        plan_ref = state.default_plan_ref
        if plan_ref is None:
            raise CliCommandError(
                command=_COMMAND,
                code="budget_plan_pin_refused",
                message="A daemon budget requires a selected default plan.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={},
            )
        budget_id = options.budget_id
        if budget_id is None:
            raise AssertionError("validated daemon budget is missing budget_id")
        try:
            existing = runtime.store.load_daemon_budget_epoch(budget_id)
        except ValueError as exc:
            raise CliCommandError(
                command=_COMMAND,
                code="daemon_budget_epoch_refused",
                message="Daemon budget epoch was refused.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={"budget_id": budget_id},
            ) from exc
        started_at = now if existing is None else existing.started_at
        try:
            epoch = DaemonBudgetEpochRecord(
                budget_id=budget_id,
                workspace_path=str(options.paths.workspace_path),
                selected_plan_ref=plan_ref,
                max_wall_seconds=options.max_wall_seconds,
                max_invocations=options.max_invocations,
                max_total_tokens=options.max_total_tokens,
                started_at=started_at,
                wall_deadline=(
                    None
                    if options.max_wall_seconds is None
                    else started_at + options.max_wall_seconds
                ),
                last_observed_at=now,
            )
        except ValueError as exc:
            raise CliCommandError(
                command=_COMMAND,
                code="daemon_budget_limit_out_of_range",
                message="Daemon budget limits exceed durable bounds.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={"budget_id": budget_id},
            ) from exc
        try:
            return runtime.store.create_or_resume_daemon_budget_epoch(epoch)
        except ValueError as exc:
            code = str(exc)
            if code not in {
                "daemon_budget_immutable_limits_changed",
                "daemon_budget_clock_discontinuity",
            }:
                code = "daemon_budget_epoch_refused"
            raise CliCommandError(
                command=_COMMAND,
                code=code,
                message="Daemon budget epoch was refused.",
                exit_code=ExitCode.DOMAIN_REFUSAL,
                details={"budget_id": budget_id},
            ) from exc
    finally:
        runtime.close()


def _remaining_wall_seconds(
    budget: DaemonBudgetEpochRecord | None,
    wall_clock: Callable[[], float],
) -> float | None:
    if budget is None or budget.wall_deadline is None:
        return None
    return max(0.001, float(budget.wall_deadline) - wall_clock())


def _account_budgeted_starts(options: DaemonRunOptions) -> None:
    if options.budget_id is None:
        return
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        epoch = runtime.store.load_daemon_budget_epoch(options.budget_id)
        if epoch is None:
            raise ValueError("daemon budget epoch is missing")
        for session_id in runtime.store.pending_budgeted_runner_start_session_ids(
            options.budget_id
        ):
            session = state.runner_sessions.get(session_id)
            if session is None:
                raise ValueError("runner_session_budget_identity_mismatch")
            run = state.runs.get(session.run_id)
            if run is None or run.run_ref.plan_ref != epoch.selected_plan_ref:
                raise ValueError("runner_session_budget_identity_mismatch")
            if session.start_intent_at is None:
                continue
            runtime.store.record_budgeted_runner_start(options.budget_id, session)
    finally:
        runtime.close()


def _account_budgeted_start(
    options: DaemonRunOptions,
    session: RunnerSessionRecord,
) -> None:
    if options.budget_id is None:
        return
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        epoch = runtime.store.load_daemon_budget_epoch(options.budget_id)
        run = state.runs.get(session.run_id)
        if (
            epoch is None
            or run is None
            or run.run_ref.plan_ref != epoch.selected_plan_ref
        ):
            raise ValueError("runner_session_budget_identity_mismatch")
        runtime.store.record_budgeted_runner_start(options.budget_id, session)
    finally:
        runtime.close()


def _reserve_budgeted_start(
    options: DaemonRunOptions,
    session: RunnerSessionRecord,
) -> None:
    if options.budget_id is None:
        return
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        epoch = runtime.store.load_daemon_budget_epoch(options.budget_id)
        run = state.runs.get(session.run_id)
        if (
            epoch is None
            or run is None
            or run.run_ref.plan_ref != epoch.selected_plan_ref
        ):
            raise ValueError("runner_session_budget_identity_mismatch")
        runtime.store.reserve_budgeted_runner_start(options.budget_id, session)
    finally:
        runtime.close()


def _budget_exhaustion_reason(options: DaemonRunOptions) -> str | None:
    if options.budget_id is None:
        return None
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        epoch = runtime.store.load_daemon_budget_epoch(options.budget_id)
    finally:
        runtime.close()
    if epoch is None:
        return "budget_epoch_missing"
    if epoch.status != "active":
        return epoch.terminal_reason or "budget_epoch_terminal"
    if (
        epoch.max_invocations is not None
        and epoch.accepted_start_count >= epoch.max_invocations
    ):
        return "invocation_limit_exhausted"
    if (
        epoch.max_total_tokens is not None
        and epoch.cumulative_total_tokens >= epoch.max_total_tokens
    ):
        return "token_limit_exhausted"
    return None


def _finish_budget(
    options: DaemonRunOptions,
    *,
    observed_at: int,
    reason: str,
) -> None:
    if options.budget_id is None:
        return
    runtime = open_runtime_context(options.paths, command=_COMMAND)
    try:
        terminalize_daemon_budget_with_suspension(
            runtime,
            budget_id=options.budget_id,
            observed_at=observed_at,
            status="exhausted",
            reason=reason,
            command=_COMMAND,
        )
    finally:
        runtime.close()


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


def _optional_positive_limit(
    namespace: object,
    attribute: str,
    option: str,
) -> int | None:
    value = getattr(namespace, attribute, None)
    if value is None:
        return None
    parsed = int(value)
    if parsed < 1 or parsed > DURABLE_INT64_MAX:
        raise CliCommandError(
            command=_COMMAND,
            code=f"invalid_{attribute}",
            message=f"{option} must be a positive integer.",
            exit_code=ExitCode.CLI_USAGE,
            details={attribute: parsed},
        )
    return parsed


def _non_idle_stop_reason(result: BoundedExecutionUnitResult) -> str | None:
    if result.code in {
        "lifecycle_transition_applied",
        "no_ready_work",
        "runner_session_waiting",
        "runner_session_retained",
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
        budget=_summary_budget(options),
    )


def _summary_runner_session(
    options: DaemonRunOptions,
    last_handled_run_id: str | None,
) -> dict[str, object] | None:
    from millrace.adapters.cli.status import (
        _hydration_totals_by_session,
        _hydration_totals_for_run,
        runner_session_projection,
    )

    if last_handled_run_id is None:
        return None
    try:
        runtime = open_runtime_context(options.paths, command=_COMMAND)
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            hydration_totals = _hydration_totals_by_session(runtime, state)
        finally:
            runtime.close()
    except (CliCommandError, OSError, SubstrateError):
        return None
    projection = runner_session_projection(
        state,
        last_handled_run_id,
        hydration_totals=_hydration_totals_for_run(
            state,
            last_handled_run_id,
            hydration_totals,
        ),
    )
    if projection is None:
        return None
    return projection


def _summary_budget(options: DaemonRunOptions) -> dict[str, object] | None:
    from millrace.adapters.cli.status import _daemon_budget_projections

    if options.budget_id is None:
        return None
    try:
        runtime = open_runtime_context(options.paths, command=_COMMAND)
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            projections = _daemon_budget_projections(runtime, state)
        finally:
            runtime.close()
    except (CliCommandError, OSError, SubstrateError, ValueError):
        return None
    return next(
        (
            projection
            for projection in projections
            if projection["budget_id"] == options.budget_id
        ),
        None,
    )


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
