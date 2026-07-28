from __future__ import annotations

import io
import json
import os
import signal
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, cast

import pytest
from tests.cli.test_cli_bounded_execution_unit import (
    _codex_error_config,
    _codex_mismatch_config,
    _codex_success_config,
    _load,
    _observed_counts,
    _ready_state,
    _runtime,
    _state_with_runner_kind,
)

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
    activation_id: str | None = None,
    adapter_kind: str | None = None,
    local_config: AdapterLocalConfig | None = None,
    monitor: str = "none",
) -> object:
    from millrace.adapters.cli.daemon import DaemonRunOptions

    return DaemonRunOptions(
        paths=paths,
        idle_sleep_seconds=idle_sleep_seconds,
        max_ticks=max_ticks,
        activation_id=activation_id,
        adapter_kind=adapter_kind,
        local_config=local_config,
        monitor=monitor,
        actor_id="local_operator",
    )


def _close(runtime: object) -> None:
    runtime.close()


def test_daemon_run_repeats_cli_0004_bounded_unit_until_max_ticks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    _close(runtime)
    results = iter(
        (
            BoundedExecutionUnitResult(
                code="observation_accepted",
                accepted=True,
                activation_id="activation-a",
                run_id="run-a",
                claim_id="claim-a",
                fencing_token="fence-a",
            ),
            BoundedExecutionUnitResult(
                code="observation_accepted",
                accepted=True,
                activation_id="activation-b",
                run_id="run-b",
                claim_id="claim-b",
                fencing_token="fence-b",
            ),
            BoundedExecutionUnitResult(code="no_ready_work"),
        )
    )
    calls: list[object] = []

    def fake_bounded_unit(runtime_arg: object, **kwargs: object) -> object:
        calls.append((runtime_arg.paths.db_path, kwargs))
        return next(results)

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", fake_bounded_unit)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=3, local_config=AdapterLocalConfig()),
    )

    assert len(calls) == 3
    assert summary.iterations == 3
    assert summary.units_started == 2
    assert summary.units_succeeded == 2
    assert summary.idle_iterations == 1
    assert summary.stopped_reason == "max_ticks"


def test_daemon_max_ticks_bounds_execution_and_rejects_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    _close(runtime)
    calls = 0

    def fake_bounded_unit(_runtime_arg: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return BoundedExecutionUnitResult(code="no_ready_work")

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", fake_bounded_unit)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=2, local_config=AdapterLocalConfig()),
    )
    invalid_code, invalid_stdout, invalid_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(paths.workspace_path),
            "run",
            "daemon",
            "--max-ticks",
            "0",
        ]
    )

    assert calls == 2
    assert summary.iterations == 2
    assert summary.idle_iterations == 2
    assert invalid_code == 2
    assert invalid_stdout == ""
    assert _json(invalid_stderr)["code"] == "invalid_max_ticks"


def test_daemon_rejects_negative_idle_sleep_before_lock_or_state_open(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "run",
            "daemon",
            "--idle-sleep",
            "-0.01",
            "--max-ticks",
            "1",
        ]
    )

    assert exit_code == 2
    assert stdout == ""
    assert _json(stderr)["code"] == "invalid_idle_sleep"
    assert not (workspace / ".millrace" / "daemon.lock").exists()
    assert not (workspace / ".millrace" / "runtime.sqlite3").exists()


@pytest.mark.parametrize("max_ticks", (None, 2))
def test_daemon_activation_id_requires_single_tick_bound(
    tmp_path: Path,
    max_ticks: int | None,
) -> None:
    workspace = tmp_path / f"workspace-{max_ticks}"
    argv = [
        "--json",
        "--workspace",
        str(workspace),
        "run",
        "daemon",
        "--activation-id",
        "activation-taskmaster",
    ]
    if max_ticks is not None:
        argv.extend(["--max-ticks", str(max_ticks)])

    exit_code, stdout, stderr = _invoke(argv)

    assert exit_code == 2
    assert stdout == ""
    assert _json(stderr)["code"] == "invalid_activation_id"
    assert not (workspace / ".millrace" / "daemon.lock").exists()


def test_daemon_reloads_persisted_state_between_bounded_units(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)
    opened_db_paths: list[Path] = []
    real_open = daemon.open_runtime_context

    def tracking_open(namespace: object, *, command: str) -> object:
        context = real_open(namespace, command=command)
        opened_db_paths.append(context.paths.db_path)
        return context

    monkeypatch.setattr(daemon, "open_runtime_context", tracking_open)

    summary = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=3,
            local_config=_codex_success_config(marker="AUTO"),
        )
    )
    loop_opened_db_paths = tuple(opened_db_paths)
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert loop_opened_db_paths == (
        paths.db_path,
        paths.db_path,
        paths.db_path,
        paths.db_path,
    )
    assert summary.iterations == 3
    assert summary.units_succeeded == 2
    assert summary.idle_iterations == 1
    assert len(after.runs) == 2
    assert len(after.runner_observations) == 2
    assert after.closed_work_items


def test_daemon_store_or_cas_corruption_before_loop_releases_lock_without_mutation(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    before = _load(runtime)
    _close(runtime)
    for object_path in paths.cas_path.rglob("*"):
        if object_path.is_file():
            object_path.unlink()
            break

    summary = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=1,
            local_config=_codex_success_config(marker="AUTO"),
        )
    )
    assert summary.iterations == 0
    assert summary.stopped_reason == "state_open_failed"
    assert not (paths.workspace_path / ".millrace" / "daemon.lock").exists()

    repaired_runtime = _runtime(tmp_path / "control", before)
    try:
        assert _load(repaired_runtime) == before
    finally:
        repaired_runtime.close()


@pytest.mark.parametrize(
    "result_code",
    ("ready_state_refused", "ready_state_corrupt"),
)
def test_daemon_ready_state_refused_and_corrupt_are_not_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result_code: str,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)
    monkeypatch.setattr(
        daemon,
        "run_bounded_execution_unit",
        lambda *_args, **_kwargs: BoundedExecutionUnitResult(code=result_code),
    )

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=2, local_config=AdapterLocalConfig())
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.iterations == 1
    assert summary.idle_iterations == 0
    assert summary.stopped_reason == result_code
    assert after == state


@pytest.mark.parametrize(
    ("adapter_kind", "local_config"),
    (
        (None, AdapterLocalConfig()),
        ("codex", AdapterLocalConfig()),
        (
            "fake_local",
            AdapterLocalConfig(adapters={"fake_local": cast(Any, object())}),
        ),
    ),
)
def test_daemon_refuses_exact_fake_local_selected_authority_without_remap(
    tmp_path: Path,
    adapter_kind: str | None,
    local_config: AdapterLocalConfig,
) -> None:
    from millrace.adapters.cli import daemon

    state, _fingerprint = _state_with_runner_kind("fake_local")
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)

    summary = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=1,
            adapter_kind=adapter_kind,
            local_config=local_config,
        )
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.iterations == 1
    assert summary.units_started == 0
    assert summary.stopped_reason == "adapter_kind_refused"
    assert summary.last_result["code"] == "adapter_kind_refused"
    assert after == state


@pytest.mark.parametrize(
        ("local_config", "stopped_reason"),
        (
            (_codex_error_config(), "adapter_failure"),
            (_codex_mismatch_config(), "session_reconciliation_required"),
        ),
    )
def test_daemon_adapter_failures_stop_without_runtime_evidence(
    tmp_path: Path,
    local_config: AdapterLocalConfig,
    stopped_reason: str,
) -> None:
    from millrace.adapters.cli import daemon

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=2, local_config=local_config)
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.iterations == 1
    assert summary.adapter_failures == 1
    assert summary.stopped_reason == stopped_reason
    assert summary.last_result["activation_id"] == "activation-taskmaster"
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


def test_daemon_signal_during_failing_unit_preserves_failure_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)

    def fail_after_signal(_runtime_arg: object, **_kwargs: object) -> object:
        os.kill(os.getpid(), signal.SIGINT)
        return BoundedExecutionUnitResult(
            code="adapter_failure",
            activation_id="activation-taskmaster",
            run_id="run-taskmaster",
            claim_id="claim-taskmaster",
            fencing_token="fence-taskmaster",
        )

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", fail_after_signal)

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=3, local_config=AdapterLocalConfig())
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.iterations == 1
    assert summary.stopped_reason == "adapter_failure"
    assert summary.adapter_failures == 1
    assert after == state
    assert not (paths.workspace_path / ".millrace" / "daemon.lock").exists()


def test_daemon_preflight_unexpected_exception_is_not_state_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    _close(runtime)

    def broken_open(_namespace: object, *, command: str) -> object:
        raise RuntimeError(f"unexpected bug in {command}")

    monkeypatch.setattr(daemon, "open_runtime_context", broken_open)

    with pytest.raises(RuntimeError, match="unexpected bug"):
        daemon.run_daemon_loop(
            _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
        )

    assert not (paths.workspace_path / ".millrace" / "daemon.lock").exists()


def test_daemon_adapter_failure_after_claim_stops_and_next_no_arg_skips_active_run(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)

    failed = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=_codex_error_config())
    )
    skipped = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=1,
            local_config=_codex_success_config(marker="AUTO"),
        )
    )
    explicit = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=1,
            activation_id=cast(str, failed.last_result["activation_id"]),
            local_config=_codex_success_config(marker="AUTO"),
        )
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert failed.stopped_reason == "adapter_failure"
    assert skipped.stopped_reason == "max_ticks"
    assert skipped.idle_iterations == 1
    assert explicit.units_succeeded == 1
    assert explicit.last_result["run_id"] == failed.last_result["run_id"]
    assert len(after.runner_observations) == 1


def test_daemon_asset_material_refusal_counters_and_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli import run as run_module
    from millrace.operator.prompt_material import SelectedAssetMaterializationError

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)

    def refuse_material(**_kwargs: object) -> object:
        raise SelectedAssetMaterializationError("selected material refused")

    monkeypatch.setattr(run_module, "build_selected_asset_material", refuse_material)

    summary = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=1,
            local_config=_codex_success_config(marker="AUTO"),
        )
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.stopped_reason == "asset_material_refused"
    assert summary.units_started == 1
    assert summary.units_refused == 1
    assert summary.last_result["activation_id"] == "activation-taskmaster"
    assert summary.last_result["claim_id"] is not None
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


def test_daemon_observation_refusal_counters_and_ids(tmp_path: Path) -> None:
    from millrace.adapters.cli import daemon

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)

    summary = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=1,
            local_config=_codex_success_config(marker="UNDECLARED"),
        )
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.stopped_reason == "observation_refused"
    assert summary.units_started == 1
    assert summary.units_refused == 1
    assert summary.last_result["activation_id"] == "activation-taskmaster"
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


def test_daemon_non_success_from_cli_0004_never_creates_observation_or_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    _close(runtime)
    before_counts = _observed_counts(state)
    monkeypatch.setattr(
        daemon,
        "run_bounded_execution_unit",
        lambda *_args, **_kwargs: BoundedExecutionUnitResult(
            code="ready_state_refused",
            diagnostics=({"reason": "paused"},),
        ),
    )

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(reopened)
    finally:
        reopened.close()

    assert summary.stopped_reason == "ready_state_refused"
    assert _observed_counts(after) == before_counts


def test_daemon_is_only_public_runner_execution_surface(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    forbidden = (
        ["run", "once"],
        ["tick"],
        ["observe"],
        ["dispatch", "invoke"],
        ["run", "daemon", "status"],
        ["run", "daemon", "stop"],
    )

    for argv in forbidden:
        exit_code, stdout, stderr = _invoke(
            ["--json", "--workspace", str(workspace), *argv]
        )
        assert exit_code in {2, 3}
        assert stdout == ""
        assert _json(stderr)["code"] in {
            "argument_parse_error",
            "command_not_implemented",
        }


def test_daemon_monitor_basic_is_bounded_presentation_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    _close(runtime)
    results = iter(
        (
            BoundedExecutionUnitResult(
                code="observation_accepted",
                accepted=True,
                activation_id="activation-a",
                run_id="run-a",
                claim_id="claim-a",
            ),
            BoundedExecutionUnitResult(code="no_ready_work"),
        )
    )
    monkeypatch.setattr(
        daemon,
        "run_bounded_execution_unit",
        lambda *_args, **_kwargs: next(results),
    )
    stream = io.StringIO()

    summary = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=2,
            local_config=AdapterLocalConfig(),
            monitor="basic",
        ),
        progress_stream=stream,
    )

    output = stream.getvalue()
    assert summary.iterations == 2
    assert output.count("daemon tick") == 2
    assert "observation_accepted" in output
    assert "no_ready_work" in output
    assert "dashboard" not in output.lower()
    assert "tail" not in output.lower()
    assert not (paths.workspace_path / ".millrace" / "monitor.log").exists()


def test_daemon_does_not_start_watcher_mailbox_or_source_intake(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    _close(runtime)
    inbox = paths.workspace_path / "ideas-inbox"
    mailbox = paths.workspace_path / "mailbox"
    source_callback = paths.workspace_path / "provider-callback.json"
    inbox.mkdir()
    mailbox.mkdir()
    source_callback.write_text('{"new_work": true}', encoding="utf-8")

    summary = daemon.run_daemon_loop(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig())
    )

    assert summary.iterations == 1
    assert summary.idle_iterations == 1
    assert sorted(path.name for path in inbox.iterdir()) == []
    assert sorted(path.name for path in mailbox.iterdir()) == []
    assert source_callback.read_text(encoding="utf-8") == '{"new_work": true}'
