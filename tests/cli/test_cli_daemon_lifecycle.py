from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import cast

from tests.cli.test_cli_bounded_execution_unit import _load, _runtime
from tests.cli.test_cli_daemon_loop import _daemon_options

from millrace.adapters.runner_contract import AdapterLocalConfig
from support import generic_lifecycle


def test_daemon_applies_lifecycle_before_runner_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.adapters.cli import daemon

    state, _plan, _fingerprint = (
        generic_lifecycle.origin_closed_with_ready_activation_state()
    )
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
    assert after.fanout_records


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
