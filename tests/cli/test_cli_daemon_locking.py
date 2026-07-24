from __future__ import annotations

import io
import json
import os
import signal
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest
from tests.cli.test_cli_bounded_execution_unit import _load, _ready_state, _runtime

from millrace.adapters.runner_contract import AdapterLocalConfig


def _json(raw: str) -> dict[str, Any]:
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _daemon_options(
    paths: object,
    *,
    max_ticks: int | None,
    idle_sleep_seconds: float = 0.0,
    local_config: AdapterLocalConfig | None = None,
) -> object:
    from millrace.adapters.cli.daemon import DaemonRunOptions

    return DaemonRunOptions(
        paths=paths,
        idle_sleep_seconds=idle_sleep_seconds,
        max_ticks=max_ticks,
        activation_id=None,
        adapter_kind=None,
        local_config=local_config,
        monitor="none",
        actor_id="local_operator",
    )


def _close(runtime: object) -> None:
    runtime.close()


def test_daemon_lock_refuses_second_instance_without_state_mutation(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    before = _load(runtime)
    _close(runtime)
    lock_path = paths.workspace_path / ".millrace" / "daemon.lock"
    lock_path.write_text("diagnostic owner only", encoding="utf-8")

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(paths.workspace_path),
            "run",
            "daemon",
            "--max-ticks",
            "1",
        ]
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert exit_code == 3
    assert stdout == ""
    error = _json(stderr)
    assert error["command"] == "run.daemon"
    assert error["code"] == "daemon_already_running"
    assert "inspect/remove" in error["message"]
    assert after == before
    assert lock_path.exists()


def test_daemon_releases_lock_on_clean_exit(tmp_path: Path) -> None:
    from millrace.adapters.cli import daemon

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    _close(runtime)
    lock_path = paths.workspace_path / ".millrace" / "daemon.lock"

    first = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )
    second = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )

    assert first.stopped_reason == "max_ticks"
    assert second.stopped_reason == "max_ticks"
    assert not lock_path.exists()


def test_daemon_refuses_ambiguous_stale_lock_without_state_mutation(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    before = _load(runtime)
    _close(runtime)
    lock_path = paths.workspace_path / ".millrace" / "daemon.lock"
    lock_path.write_text('{"pid":999999,"note":"ambiguous"}', encoding="utf-8")

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.stopped_reason == "daemon_already_running"
    assert summary.iterations == 0
    assert after == before
    assert lock_path.exists()


def test_daemon_handles_sigint_after_current_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    _close(runtime)
    calls = 0

    def signal_after_unit(_runtime_arg: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        os.kill(os.getpid(), signal.SIGINT)
        return BoundedExecutionUnitResult(code="no_ready_work")

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", signal_after_unit)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=3, local_config=AdapterLocalConfig())
    )

    assert calls == 1
    assert summary.iterations == 1
    assert summary.stopped_reason == "signal"
    assert not (paths.workspace_path / ".millrace" / "daemon.lock").exists()


def test_daemon_handles_sigterm_during_idle_sleep_releases_lock_without_extra_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    _close(runtime)
    calls = 0

    def idle_unit(_runtime_arg: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return BoundedExecutionUnitResult(code="no_ready_work")

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", idle_unit)

    def send_sigterm() -> None:
        time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)

    thread = threading.Thread(target=send_sigterm)
    thread.start()
    try:
        summary = daemon.run_daemon_loop(
            _daemon_options(
                paths,
                max_ticks=5,
                idle_sleep_seconds=10.0,
                local_config=AdapterLocalConfig(),
            )
        )
    finally:
        thread.join(timeout=1)

    assert calls == 1
    assert summary.iterations == 1
    assert summary.stopped_reason == "signal"
    assert not (paths.workspace_path / ".millrace" / "daemon.lock").exists()
