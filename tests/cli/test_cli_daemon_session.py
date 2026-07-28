from __future__ import annotations

import json
import os
import signal
import threading
import time

from tests.cli.test_cli_daemon_loop import _close, _daemon_options, _invoke

from cli.test_cli_bounded_execution_unit import (
    _codex_success_config,
    _load,
    _ready_state,
    _runtime,
)
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationRequest,
    AdapterLocalConfig,
    DispatchEcho,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    StartedSession,
    StartIndeterminate,
    Unsupported,
    runner_cancellation_diagnostic_digest,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


class _SignalWaitHandle:
    def __init__(self, request: AdapterInvocationRequest) -> None:
        self._request = request
        self._cancelled = False

    def poll_completion(self):
        if not self._cancelled:
            return None
        return AdapterErrorResult.from_unredacted(
            adapter_id=self._request.adapter_id,
            error_kind="cancelled",
            dispatch_echo=DispatchEcho.from_dispatch_envelope(
                self._request.dispatch_envelope,
                correlation_id=self._request.correlation_id,
            ),
            redaction_policy=self._request.redaction_policy,
        )

    def request_cancel(self) -> RunnerCancellationOperationResult:
        self._cancelled = True
        now = time.time_ns()
        diagnostic = {"operation": "cooperative_cancel"}
        return RunnerCancellationOperationResult(
            "cooperative_cancel",
            "succeeded",
            now,
            now,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    def terminate(self) -> RunnerCancellationOperationResult:
        now = time.time_ns()
        diagnostic = {"operation": "terminate"}
        return RunnerCancellationOperationResult(
            "terminate",
            "unsupported",
            now,
            now,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    def kill(self) -> RunnerCancellationOperationResult:
        now = time.time_ns()
        diagnostic = {"operation": "kill"}
        return RunnerCancellationOperationResult(
            "kill",
            "unsupported",
            now,
            now,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    def cleanup(self) -> RunnerCleanupResult:
        now = time.time_ns()
        diagnostic = {"cleanup": "complete"}
        return RunnerCleanupResult(
            "complete",
            now,
            now,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


class _SignalWaitAdapter:
    adapter_kind = "codex"

    def __init__(self, active: threading.Event | None = None) -> None:
        self._active = active

    def start_session(self, request: AdapterInvocationRequest) -> StartedSession:
        started = StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
            ),
            _SignalWaitHandle(request),
            f"signal-wait:{request.session_id}",
            {},
        )
        if self._active is not None:
            self._active.set()
        return started

    def reconcile_session(self, request):
        invocation = request.invocation_request
        return Unsupported(
            DispatchEcho.from_dispatch_envelope(
                invocation.dispatch_envelope,
                correlation_id=invocation.correlation_id,
            )
        )


class _OrphanSignalWaitHandle(_SignalWaitHandle):
    def cleanup(self) -> RunnerCleanupResult:
        now = time.time_ns()
        diagnostic = {"cleanup": "orphan_risk"}
        return RunnerCleanupResult(
            "orphan_risk",
            now,
            now,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


class _OrphanSignalWaitAdapter(_SignalWaitAdapter):
    def start_session(self, request: AdapterInvocationRequest) -> StartedSession:
        started = StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
            ),
            _OrphanSignalWaitHandle(request),
            f"orphan-signal-wait:{request.session_id}",
            {},
        )
        if self._active is not None:
            self._active.set()
        return started


class _IndeterminateAdapter:
    adapter_kind = "codex"

    def start_session(
        self,
        request: AdapterInvocationRequest,
    ) -> StartIndeterminate:
        return StartIndeterminate(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
            ),
            None,
            _digest("e"),
        )

    def reconcile_session(self, request):
        invocation = request.invocation_request
        return Unsupported(
            DispatchEcho.from_dispatch_envelope(
                invocation.dispatch_envelope,
                correlation_id=invocation.correlation_id,
            )
        )


def _run_daemon_after_adapter_start(daemon, paths, adapter_type):
    active = threading.Event()
    stop_signal_worker = threading.Event()
    signal_gate = threading.Lock()
    signal_failures: list[str] = []

    def signal_active_session() -> None:
        deadline = time.monotonic() + 5
        while not active.wait(timeout=0.01):
            if stop_signal_worker.is_set():
                return
            if time.monotonic() >= deadline:
                signal_failures.append("adapter did not start before signal deadline")
                return
        with signal_gate:
            if stop_signal_worker.is_set():
                return
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except OSError as exc:
                signal_failures.append(str(exc))

    signal_worker = threading.Thread(
        target=signal_active_session,
        name="daemon-active-session-signal",
    )
    signal_worker.start()
    try:
        summary = daemon.run_daemon_loop(
            _daemon_options(
                paths,
                max_ticks=1,
                local_config=AdapterLocalConfig(
                    adapters={"codex": adapter_type(active)}
                ),
            )
        )
    finally:
        with signal_gate:
            stop_signal_worker.set()
        signal_worker.join(timeout=1)

    assert not signal_worker.is_alive()
    assert signal_failures == []
    assert active.is_set()
    return summary


def test_signal_during_active_session_requests_cancellation(tmp_path) -> None:
    from millrace.adapters.cli import daemon

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)
    summary = _run_daemon_after_adapter_start(daemon, paths, _SignalWaitAdapter)
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.stopped_reason == "signal"
    request = next(iter(after.runner_session_cancellation_requests.values()))
    assert (request.reason, request.source_kind) == ("daemon_shutdown", "daemon")
    session = after.runner_sessions[request.session_id]
    assert session.state == "interrupted"
    assert session.cleanup_disposition == "complete"


def test_signal_with_orphan_risk_is_not_reported_as_clean_stop(tmp_path) -> None:
    from millrace.adapters.cli import daemon

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)
    summary = _run_daemon_after_adapter_start(
        daemon,
        paths,
        _OrphanSignalWaitAdapter,
    )

    assert summary.stopped_reason == "runner_session_orphan_risk"
    assert daemon._summary_is_success(summary) is False


def test_runs_cancel_persists_fixed_operator_reason_and_replays(tmp_path) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        local_config=AdapterLocalConfig(
            adapters={"codex": _IndeterminateAdapter()}
        ),
    )
    paths = runtime.paths
    _close(runtime)
    argv = [
        "--json",
        "--workspace",
        str(paths.workspace_path),
        "--db",
        str(paths.db_path),
        "--cas",
        str(paths.cas_path),
        "runs",
        "cancel",
        str(result.run_id),
        "--input-id",
        "operator-cancel-cli-1",
    ]

    first_code, first_stdout, first_stderr = _invoke(argv)
    replay_code, replay_stdout, replay_stderr = _invoke(argv)

    assert (first_code, replay_code) == (0, 0)
    assert first_stderr == replay_stderr == ""
    assert json.loads(first_stdout)["code"] == "runner_session_cancel_requested"
    assert json.loads(replay_stdout)["code"] == "runner_session_cancel_requested"
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()
    cancellation = after.runner_session_cancellation_requests[
        "operator-cancel-cli-1"
    ]
    assert (cancellation.reason, cancellation.source_kind) == (
        "operator_cancel_work",
        "operator",
    )


def test_runs_cancel_refuses_terminal_session(tmp_path) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    paths = runtime.paths
    _close(runtime)
    before_runtime = daemon.open_runtime_context(paths, command="test")
    try:
        before = _load(before_runtime)
    finally:
        before_runtime.close()

    argv = [
        "--json",
        "--workspace",
        str(paths.workspace_path),
        "--db",
        str(paths.db_path),
        "--cas",
        str(paths.cas_path),
        "runs",
        "cancel",
        str(result.run_id),
        "--input-id",
        "late-cancel-1",
    ]
    code, stdout, stderr = _invoke(argv)
    replay_code, replay_stdout, replay_stderr = _invoke(argv)

    assert code != 0
    assert stdout == ""
    assert json.loads(stderr)["code"] == "runner_session_cancel_refused"
    assert replay_code != 0
    assert replay_stdout == ""
    assert json.loads(replay_stderr)["code"] == "runner_session_cancel_refused"
    after_runtime = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(after_runtime)
    finally:
        after_runtime.close()
    assert len(after.refusals) == len(before.refusals) + 1
    assert len(after.governance_events) == len(before.governance_events) + 1
    assert len(after.traces) == len(before.traces) + 1
    assert after.runner_sessions == before.runner_sessions
    assert after.runner_session_completions == before.runner_session_completions
