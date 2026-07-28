from __future__ import annotations

import json
import os
import signal
import threading
import time

import pytest
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
                selected_adapter_kind=self._request.selected_adapter_kind,
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
                selected_adapter_kind=request.selected_adapter_kind,
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
                selected_adapter_kind=invocation.selected_adapter_kind,
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
                selected_adapter_kind=request.selected_adapter_kind,
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
                selected_adapter_kind=request.selected_adapter_kind,
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
                selected_adapter_kind=invocation.selected_adapter_kind,
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
    stopped_session = summary.data()["runner_session"]
    assert stopped_session["session_id"] == session.session_id
    assert stopped_session["adapter_kind"] == "codex"
    assert stopped_session["primary_cancellation_reason"] == "daemon_shutdown"
    assert stopped_session["cleanup_disposition"] == "complete"
    assert stopped_session["completion_persisted"] is True
    assert stopped_session["application_persisted"] is False
    assert stopped_session["application_status"] == "not_applicable"
    assert stopped_session["cancellation_last_operation"] == "transport_cleanup"
    assert stopped_session["cancellation_last_result"] == "succeeded"


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


def test_daemon_reconciles_restart_sessions_before_new_dispatch(tmp_path) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit

    class LocatedIndeterminateAdapter(_IndeterminateAdapter):
        def start_session(
            self,
            request: AdapterInvocationRequest,
        ) -> StartIndeterminate:
            return StartIndeterminate(
                DispatchEcho.from_dispatch_envelope(
                    request.dispatch_envelope,
                    correlation_id=request.correlation_id,
                    selected_adapter_kind=request.selected_adapter_kind,
                ),
                {"provider_request_id": "request-1"},
                _digest("e"),
            )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    config = AdapterLocalConfig(adapters={"codex": LocatedIndeterminateAdapter()})
    started = run_bounded_execution_unit(runtime, local_config=config)
    assert started.code == "session_reconciliation_required"
    paths = runtime.paths
    _close(runtime)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=config)
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.stopped_reason == "runner_session_orphan_risk"
    assert summary.iterations == 0
    session = after.runner_sessions[after.runs[started.run_id].current_session_id]
    assert (session.state, session.cleanup_disposition) == ("lost", "orphan_risk")
    assert len(after.runs) == 1
    assert after.runner_observations == {}
    show_code, show_stdout, show_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(paths.workspace_path),
            "runs",
            "show",
            started.run_id,
        ]
    )
    doctor_code, doctor_stdout, doctor_stderr = _invoke(
        ["--json", "--workspace", str(paths.workspace_path), "doctor"]
    )
    trace_code, trace_stdout, trace_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(paths.workspace_path),
            "trace",
            "show",
            started.run_id,
        ]
    )
    status_code, status_stdout, status_stderr = _invoke(
        ["--json", "--workspace", str(paths.workspace_path), "status"]
    )
    assert show_code == doctor_code == trace_code == status_code == 0, (
        show_stderr,
        doctor_stderr,
        trace_stderr,
        status_stderr,
    )
    assert show_stderr == doctor_stderr == trace_stderr == status_stderr == ""
    shown = json.loads(show_stdout)["data"]["run"]["runner_session"]
    assert shown["orphan_risk"] is True
    doctor = json.loads(doctor_stdout)["data"]["runner_session_diagnostics"]
    assert doctor["diagnostic_counts"] == {
        "runner_session_lost": 1,
        "runner_session_orphan_risk": 1,
        "runner_session_reconciliation_unsupported": 1,
    }
    assert {
        item["code"] for item in doctor["diagnostics"]
    } == {
        "runner_session_lost",
        "runner_session_orphan_risk",
        "runner_session_reconciliation_unsupported",
    }
    for diagnostic in doctor["diagnostics"]:
        assert diagnostic["session_id"] == session.session_id
        assert diagnostic["adapter_kind"] == "codex"
        assert diagnostic["state"] == "lost"
        assert diagnostic["cleanup_disposition"] == "orphan_risk"
        assert diagnostic["orphan_risk"] is True
        assert diagnostic["completion_persisted"] is True
        assert diagnostic["application_persisted"] is False
    traced = json.loads(trace_stdout)["data"]["runner_session"]
    assert traced["session_id"] == session.session_id
    assert traced["state"] == "lost"
    projected = json.loads(status_stdout)["data"]["runner_sessions"]
    assert projected == [traced]


@pytest.mark.parametrize("locator_damage", ("missing", "corrupt"))
def test_daemon_restart_refuses_missing_or_corrupt_session_locator_without_writes(
    tmp_path,
    locator_damage: str,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit

    class LocatedIndeterminateAdapter(_IndeterminateAdapter):
        def start_session(
            self,
            request: AdapterInvocationRequest,
        ) -> StartIndeterminate:
            return StartIndeterminate(
                DispatchEcho.from_dispatch_envelope(
                    request.dispatch_envelope,
                    correlation_id=request.correlation_id,
                    selected_adapter_kind=request.selected_adapter_kind,
                ),
                {"provider_request_id": "request-1"},
                _digest("e"),
            )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    config = AdapterLocalConfig(adapters={"codex": LocatedIndeterminateAdapter()})
    started = run_bounded_execution_unit(runtime, local_config=config)
    persisted = _load(runtime)
    session = persisted.runner_sessions[
        persisted.runs[started.run_id].current_session_id
    ]
    assert session.durable_locator_digest is not None
    paths = runtime.paths
    _close(runtime)
    locator_path = (
        paths.cas_path
        / "sha256"
        / session.durable_locator_digest.removeprefix("sha256:")
    )
    if locator_damage == "missing":
        locator_path.unlink()
    else:
        locator_path.write_bytes(b'{"adapter_locator":')
    database_before = paths.db_path.read_bytes()

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=config)
    )

    assert summary.stopped_reason == "state_open_failed"
    assert paths.db_path.read_bytes() == database_before


def test_runs_cancel_persists_fixed_operator_reason_and_replays(tmp_path) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        local_config=AdapterLocalConfig(adapters={"codex": _IndeterminateAdapter()}),
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
    cancellation = after.runner_session_cancellation_requests["operator-cancel-cli-1"]
    assert (cancellation.reason, cancellation.source_kind) == (
        "operator_cancel_work",
        "operator",
    )


def test_runner_session_public_projections_include_selected_authority_and_cancellation(
    tmp_path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        local_config=AdapterLocalConfig(adapters={"codex": _IndeterminateAdapter()}),
    )
    paths = runtime.paths
    _close(runtime)
    cancel_argv = [
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
        "operator-cancel-projection-1",
    ]

    human_code, human_stdout, human_stderr = _invoke(cancel_argv)
    assert human_code == 0
    assert human_stdout == "Runner session cancellation requested.\n"
    assert human_stderr == ""

    projected: list[dict[str, object]] = []
    for command in (
        ("runs", "list"),
        ("runs", "show", str(result.run_id)),
        ("trace", "show", str(result.run_id)),
        ("status",),
    ):
        code, stdout, stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(paths.workspace_path),
                "--db",
                str(paths.db_path),
                "--cas",
                str(paths.cas_path),
                *command,
            ]
        )
        assert code == 0, stderr
        assert stderr == ""
        data = json.loads(stdout)["data"]
        if command[:2] == ("runs", "list"):
            projected.append(data["runs"][0]["runner_session"])
        elif command[:2] == ("runs", "show"):
            projected.append(data["run"]["runner_session"])
        elif command[:2] == ("trace", "show"):
            projected.append(data["runner_session"])
        else:
            projected.append(data["runner_sessions"][0])

    for session in projected:
        assert session["adapter_kind"] == "codex"
        assert session["primary_cancellation_reason"] == "operator_cancel_work"
        assert session["cancellation_phase"] == "cancellation_requested"
        assert session["cleanup_disposition"] == "pending"
        assert session["completion_persisted"] is False
        assert session["application_persisted"] is False
        assert session["application_status"] == "not_completed"
        assert session["orphan_risk"] is False


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


def test_runs_cancel_human_refusal_uses_stable_public_code(tmp_path) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    paths = runtime.paths
    _close(runtime)

    code, stdout, stderr = _invoke(
        [
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
            "late-human-cancel-1",
        ]
    )

    assert code == 3
    assert stdout == ""
    assert stderr == (
        "runner_session_cancel_refused: "
        "Runner session cancellation was refused.\n"
    )


def test_runs_follow_projects_events_but_reconciles_final_from_durable_state(
    tmp_path,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.substrate.runner_session_events import (
        RunnerSessionEventStore,
        runner_session_event_store_path,
    )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    assert result.run_id is not None
    durable = _load(runtime)
    run = durable.runs[result.run_id]
    assert run.current_session_id is not None
    session = durable.runner_sessions[run.current_session_id]
    observations_before = durable.runner_observations
    event_path = runner_session_event_store_path(runtime.paths.db_path)
    store = RunnerSessionEventStore.open(event_path)
    store._connection.execute(  # noqa: SLF001 - simulate misleading sidecar data
        "UPDATE session_events SET payload_json = ? "
        "WHERE session_id = ? AND kind = 'session_terminal'",
        (
            '{"claimed_authority":true,"terminal_state":"lost"}',
            session.session_id,
        ),
    )
    store._connection.commit()  # noqa: SLF001
    store.close()
    paths = runtime.paths
    _close(runtime)

    code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(paths.workspace_path),
            "--db",
            str(paths.db_path),
            "--cas",
            str(paths.cas_path),
            "runs",
            "follow",
            result.run_id,
            "--after-sequence",
            "0",
            "--max-events",
            "1000",
        ]
    )

    assert code == 0, stderr
    payload = json.loads(stdout)["data"]
    assert len(payload["events"]) <= 100
    assert payload["durable_final"]["session_state"] == "completed"
    assert payload["durable_final"]["terminal_state"] == "completed"
    assert payload["durable_final"]["completion_persisted"] is True
    assert payload["durable_final"]["application_persisted"] is True
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        assert _load(reopened).runner_observations == observations_before
    finally:
        reopened.close()

    human_code, human_stdout, human_stderr = _invoke(
        [
            "--workspace",
            str(paths.workspace_path),
            "--db",
            str(paths.db_path),
            "--cas",
            str(paths.cas_path),
            "runs",
            "follow",
            result.run_id,
            "--after-sequence",
            "0",
        ]
    )
    assert human_code == 0
    assert human_stdout == "Runner session events projected.\n"
    assert human_stderr == ""
