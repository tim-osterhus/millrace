from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest
from tests.cli.test_cli_bounded_execution_unit import _load, _runtime
from tests.cli.test_cli_daemon_loop import _daemon_options

from millrace.adapters.runner_contract import AdapterLocalConfig
from support import generic_lifecycle


@pytest.mark.parametrize("ready_code", ["no_ready_work", "observation_accepted"])
def test_deferred_completion_does_not_starve_ready_work(
    tmp_path, monkeypatch, ready_code
):
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    runtime.close()
    calls = []
    monkeypatch.setattr(
        daemon,
        "run_lifecycle_transition_once",
        lambda *_: (
            calls.append("lifecycle")
            or BoundedExecutionUnitResult(code="no_ready_work")
        ),
    )
    monkeypatch.setattr(
        daemon,
        "reconcile_pending_runner_completions",
        lambda *_args, **_kwargs: (
            calls.append("completion")
            or BoundedExecutionUnitResult(code="runner_session_waiting")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        daemon,
        "run_bounded_execution_unit",
        lambda *_args, **_kwargs: (
            calls.append("dispatch") or BoundedExecutionUnitResult(code=ready_code)
        ),
    )
    result = daemon._run_one_bounded_unit(
        _daemon_options(paths, max_ticks=1),
        daemon_stop_requested=lambda: False,
    )
    assert calls == ["lifecycle", "completion", "dispatch"]
    assert result.code == (
        "runner_session_waiting" if ready_code == "no_ready_work" else ready_code
    )


@pytest.mark.parametrize("stop_on_wait", [False, True])
def test_completion_wait_honors_backoff_stop_and_max_ticks(
    tmp_path, monkeypatch, stop_on_wait
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    runtime.close()
    sleeps = []
    stop = SimpleNamespace(
        requested=False, wait=lambda seconds: sleeps.append(seconds) or stop_on_wait
    )
    monkeypatch.setattr(daemon, "_SignalStop", lambda: nullcontext(stop))
    monkeypatch.setattr(
        daemon,
        "_run_one_bounded_unit",
        lambda *_args, **_kwargs: BoundedExecutionUnitResult(
            code="runner_session_waiting"
        ),
    )
    options = _daemon_options(paths, max_ticks=2)
    from dataclasses import replace

    options = replace(options, idle_sleep_seconds=0.01)
    summary = daemon.run_daemon_loop(options)
    assert summary.stopped_reason == ("signal" if stop_on_wait else "max_ticks")
    assert summary.iterations == (1 if stop_on_wait else 2)
    assert summary.idle_iterations == summary.iterations
    assert summary.units_started == 0
    assert sleeps == [0.01]


def test_daemon_closure_lifecycle_fences_stale_state_without_runner_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.adapters.cli import daemon

    state, _plan, _fingerprint = generic_lifecycle.closure_origin_closed_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()

    def fail_runner(*_args, **_kwargs):
        raise AssertionError("runner dispatch must not run before lifecycle")

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", fail_runner)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.last_result["code"] == "lifecycle_transition_applied"
    assert summary.lifecycle_transitions_applied == 1
    assert after.closure_targets


def test_daemon_applies_lifecycle_then_ready_runner_on_following_tick(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    state, _plan, _fingerprint = (
        generic_lifecycle.origin_closed_with_ready_activation_state()
    )
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()
    calls: list[str | None] = []

    def fake_runner(_runtime, **kwargs):
        calls.append(cast(str | None, kwargs["activation_id"]))
        return BoundedExecutionUnitResult(
            code="observation_accepted",
            accepted=True,
            activation_id="activation-other-origin",
            run_id="run-other-origin",
        )

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", fake_runner)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=3, local_config=AdapterLocalConfig())
    )

    assert summary.iterations == 3
    assert summary.lifecycle_transitions_applied == 2
    assert summary.units_succeeded == 1
    assert calls == [None]


def test_explicit_activation_tick_bypasses_lifecycle_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    state, _plan, _fingerprint = (
        generic_lifecycle.origin_closed_with_ready_activation_state()
    )
    activation_id = "activation-other-origin"
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()
    calls: list[str | None] = []

    def fake_runner(_runtime, **kwargs):
        calls.append(cast(str | None, kwargs["activation_id"]))
        return BoundedExecutionUnitResult(
            code="observation_accepted",
            accepted=True,
            activation_id=activation_id,
            run_id="run-other-origin",
        )

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", fake_runner)

    summary = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=1,
            activation_id=activation_id,
            local_config=AdapterLocalConfig(),
        )
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert calls == [activation_id]
    assert summary.lifecycle_transitions_applied == 0
    assert summary.units_succeeded == 1
    assert after.fanout_records == {}


def test_daemon_lifecycle_corruption_stops_before_runner_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    runtime.close()

    monkeypatch.setattr(
        daemon,
        "run_lifecycle_transition_once",
        lambda *_args, **_kwargs: BoundedExecutionUnitResult(
            code="lifecycle_state_corrupt",
            diagnostics=({"reason_code": "fanout_partial_state"},),
        ),
    )

    def fail_runner(*_args, **_kwargs):
        raise AssertionError("corrupt lifecycle state must stop before runner")

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", fail_runner)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )

    assert summary.stopped_reason == "lifecycle_state_corrupt"
    assert summary.last_result["code"] == "lifecycle_state_corrupt"
    assert summary.lifecycle_transitions_applied == 0


def test_daemon_counts_lifecycle_ticks_separately_from_runner_units(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon

    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )

    assert summary.iterations == 1
    assert summary.lifecycle_transitions_applied == 1
    assert summary.units_started == 0
    assert summary.units_succeeded == 0
    assert summary.idle_iterations == 0
    assert summary.stopped_reason == "max_ticks"


def test_daemon_surfaces_projected_lifecycle_refusal(
    tmp_path: Path, monkeypatch
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    runtime.close()
    refusal = BoundedExecutionUnitResult(
        code="lifecycle_transition_refused",
        observation_refusal_reason="invalid_fanout_payload",
        transition_disposition="refused",
    )
    monkeypatch.setattr(
        daemon,
        "run_lifecycle_transition_once",
        lambda *_args, **_kwargs: refusal,
    )
    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )

    assert summary.stopped_reason == "lifecycle_transition_refused"
    assert summary.last_result["code"] == "lifecycle_transition_refused"
    assert summary.last_result["observation_refusal_reason"] == "invalid_fanout_payload"
    assert summary.lifecycle_transitions_applied == 0


def test_no_ready_work_requires_no_lifecycle_or_runner_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    runtime.close()
    monkeypatch.setattr(
        daemon,
        "run_bounded_execution_unit",
        lambda *_args, **_kwargs: BoundedExecutionUnitResult(code="no_ready_work"),
    )

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )

    assert summary.stopped_reason == "max_ticks"
    assert summary.idle_iterations == 1
    assert summary.lifecycle_transitions_applied == 0


def test_lifecycle_tick_never_invokes_runner_adapter(
    tmp_path: Path, monkeypatch
) -> None:
    from millrace.adapters.cli import daemon

    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()

    def fail_runner(*_args, **_kwargs):
        raise AssertionError("lifecycle tick must not invoke a runner")

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", fail_runner)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )

    assert summary.last_result["code"] == "lifecycle_transition_applied"
    assert summary.units_started == 0


def test_daemon_applies_due_cooldown_before_runner_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tests.substrate.test_persistence_integrity_refusals import (
        _generic_cooldown_runtime_state,
    )

    from millrace.adapters.cli import daemon

    state = _generic_cooldown_runtime_state()
    wait = next(iter(state.cooldown_waits.values()))
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()

    def fail_runner(*_args, **_kwargs):
        raise AssertionError("due cooldown lifecycle must run before a runner")

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", fail_runner)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    consumed = after.cooldown_waits[wait.wait_id]
    assert summary.last_result["code"] == "lifecycle_transition_applied"
    assert summary.lifecycle_transitions_applied == 1
    assert summary.units_started == 0
    assert consumed.consumed_input_id is not None
    assert consumed.resulting_recovery_activation_id in after.activations


def test_lifecycle_does_not_consume_cooldown_before_due_time(tmp_path: Path) -> None:
    from tests.substrate.test_persistence_integrity_refusals import (
        _generic_cooldown_runtime_state,
    )

    from millrace.adapters.cli.lifecycle import run_lifecycle_transition_once

    state = _generic_cooldown_runtime_state()
    wait = next(iter(state.cooldown_waits.values()))
    runtime = _runtime(tmp_path, state)
    try:
        result = run_lifecycle_transition_once(
            runtime,
            observed_at=wait.due_at - 1,
        )
        after = _load(runtime)
    finally:
        runtime.close()

    assert result.code == "no_ready_work"
    assert after.cooldown_waits[wait.wait_id].consumed_input_id is None


def test_signal_after_lifecycle_tick_preserves_applied_transition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.adapters.cli import daemon

    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()
    real_lifecycle = daemon.run_lifecycle_transition_once

    def signal_after_lifecycle(*args, **kwargs):
        result = real_lifecycle(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGINT)
        return result

    monkeypatch.setattr(
        daemon,
        "run_lifecycle_transition_once",
        signal_after_lifecycle,
    )

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=3, local_config=AdapterLocalConfig())
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.stopped_reason == "signal"
    assert summary.lifecycle_transitions_applied == 1
    assert after.fanout_records


def _wait_for_path(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 5.0
    while not path.exists():
        assert process.poll() is None, process.stderr.read()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


def _direct_signal_process(
    tmp_path: Path,
    *,
    mode: str,
    signum: signal.Signals,
    duplicate: bool = False,
) -> tuple[dict[str, object], object, float]:
    from millrace.adapters.cli import daemon

    state = None
    if mode != "idle":
        from tests.cli.test_cli_bounded_execution_unit import _ready_state

        state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()
    summary_path = tmp_path / "daemon-summary.json"
    marker_path = tmp_path / "runner-active"
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    python_path = os.pathsep.join((str(repo_root / "src"), str(repo_root / "tests")))
    if env.get("PYTHONPATH"):
        python_path = os.pathsep.join((python_path, env["PYTHONPATH"]))
    env["PYTHONPATH"] = python_path
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "support.direct_daemon_signal",
            "--workspace",
            str(paths.workspace_path),
            "--db",
            str(paths.db_path),
            "--cas",
            str(paths.cas_path),
            "--summary",
            str(summary_path),
            "--marker",
            str(marker_path),
            "--mode",
            mode,
        ],
        cwd=repo_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    lock_path = paths.workspace_path / ".millrace" / "daemon.lock"
    _wait_for_path(marker_path, process)
    _wait_for_path(lock_path, process)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["pid"] == process.pid
    assert process.poll() is None

    started = time.monotonic()
    os.kill(process.pid, signum)
    if duplicate:
        os.kill(process.pid, signum)
    stdout, stderr = process.communicate(timeout=10)
    elapsed = time.monotonic() - started

    assert stdout == ""
    assert stderr == ""
    assert process.returncode == (3 if mode == "orphan" else 0)
    assert summary_path.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()
    return summary, after, elapsed


@pytest.mark.parametrize("signum", (signal.SIGINT, signal.SIGTERM))
def test_direct_signal_stops_idle_daemon_process(
    tmp_path: Path,
    signum: signal.Signals,
) -> None:
    summary, after, elapsed = _direct_signal_process(
        tmp_path,
        mode="idle",
        signum=signum,
    )

    assert elapsed < 5.0
    assert summary["stopped_reason"] == "signal"
    assert summary["runner_session"] is None
    assert after.runner_sessions == {}


@pytest.mark.parametrize("signum", (signal.SIGINT, signal.SIGTERM))
def test_direct_signal_cleans_up_live_daemon_session(
    tmp_path: Path,
    signum: signal.Signals,
) -> None:
    summary, after, elapsed = _direct_signal_process(
        tmp_path,
        mode="cooperative",
        signum=signum,
    )

    assert elapsed < 5.0
    assert summary["stopped_reason"] == "signal"
    requests = tuple(after.runner_session_cancellation_requests.values())
    assert len(requests) == 1
    assert (requests[0].reason, requests[0].source_kind) == (
        "daemon_shutdown",
        "daemon",
    )
    session = after.runner_sessions[requests[0].session_id]
    assert (session.state, session.cleanup_disposition) == (
        "interrupted",
        "complete",
    )
    attempts = sorted(
        after.runner_session_cancellation_attempts.values(),
        key=lambda item: item.sequence,
    )
    assert [attempt.operation for attempt in attempts] == [
        "cooperative_cancel",
        "transport_cleanup",
    ]
    projected = summary["runner_session"]
    assert projected["session_id"] == session.session_id
    assert projected["primary_cancellation_reason"] == "daemon_shutdown"
    assert projected["cleanup_disposition"] == "complete"
    assert projected["orphan_risk"] is False


def test_duplicate_direct_signal_keeps_one_daemon_cancellation_request(
    tmp_path: Path,
) -> None:
    summary, after, _elapsed = _direct_signal_process(
        tmp_path,
        mode="terminate",
        signum=signal.SIGTERM,
        duplicate=True,
    )

    assert summary["stopped_reason"] == "signal"
    requests = tuple(after.runner_session_cancellation_requests.values())
    assert len(requests) == 1
    attempts = sorted(
        after.runner_session_cancellation_attempts.values(),
        key=lambda item: item.sequence,
    )
    assert [attempt.operation for attempt in attempts] == [
        "cooperative_cancel",
        "terminate",
        "transport_cleanup",
    ]


def test_direct_signal_completion_race_preserves_success(
    tmp_path: Path,
) -> None:
    summary, after, _elapsed = _direct_signal_process(
        tmp_path,
        mode="completion_race",
        signum=signal.SIGINT,
    )

    assert summary["stopped_reason"] == "signal"
    assert len(after.runner_session_cancellation_requests) == 1
    session = next(iter(after.runner_sessions.values()))
    completion = after.runner_session_completions[session.session_id]
    assert session.state == "completed"
    assert completion.terminal_state == "completed"
    assert summary["runner_session"]["state"] == "completed"
    assert summary["runner_session"]["application_persisted"] is True


def test_direct_signal_bounded_wait_expiry_reports_orphan_risk(
    tmp_path: Path,
) -> None:
    summary, after, elapsed = _direct_signal_process(
        tmp_path,
        mode="orphan",
        signum=signal.SIGTERM,
    )

    assert elapsed < 5.0
    assert summary["stopped_reason"] == "runner_session_orphan_risk"
    session = next(iter(after.runner_sessions.values()))
    assert (session.state, session.cleanup_disposition) == (
        "lost",
        "orphan_risk",
    )
    attempts = sorted(
        after.runner_session_cancellation_attempts.values(),
        key=lambda item: item.sequence,
    )
    assert [attempt.operation for attempt in attempts] == [
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    assert summary["runner_session"]["orphan_risk"] is True
