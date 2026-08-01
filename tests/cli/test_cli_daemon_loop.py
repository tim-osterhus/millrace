from __future__ import annotations

import io
import json
import os
import signal
import sqlite3
import threading
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
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


def test_daemon_retry_refusal_is_domain_refusal_in_json_and_human_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon

    runtime = _runtime(tmp_path)
    paths = runtime.paths
    _close(runtime)
    summary = daemon.DaemonRunSummary(
        iterations=1,
        units_started=1,
        units_succeeded=0,
        units_refused=1,
        adapter_failures=0,
        idle_iterations=0,
        lifecycle_transitions_applied=0,
        stopped_reason="runner_session_retry_refused",
        workspace=str(paths.workspace_path),
        db_path=str(paths.db_path),
        cas_path=str(paths.cas_path),
    )
    monkeypatch.setattr(
        daemon,
        "run_daemon_loop",
        lambda _options, *, progress_stream=None: summary,
    )

    json_exit, json_stdout, json_stderr = _invoke(
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
    human_exit, human_stdout, human_stderr = _invoke(
        [
            "--workspace",
            str(paths.workspace_path),
            "run",
            "daemon",
            "--max-ticks",
            "1",
        ]
    )

    assert json_exit == human_exit == 3
    assert json_stdout == human_stdout == ""
    error = _json(json_stderr)
    assert error["code"] == "runner_session_retry_refused"
    assert error["message"] == "Daemon stopped before successful completion."
    assert human_stderr == (
        "runner_session_retry_refused: "
        "Daemon stopped before successful completion.\n"
    )


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


@pytest.mark.parametrize(
    ("args", "code"),
    (
        (
            ["--max-wall-seconds", "0", "--budget-id", "budget-a"],
            "invalid_max_wall_seconds",
        ),
        (
            ["--max-wall-seconds", "-1", "--budget-id", "budget-a"],
            "invalid_max_wall_seconds",
        ),
        (
            ["--max-wall-seconds", str(2**63), "--budget-id", "budget-a"],
            "invalid_max_wall_seconds",
        ),
        (
            ["--max-invocations", "0", "--budget-id", "budget-a"],
            "invalid_max_invocations",
        ),
        (
            ["--max-invocations", "-1", "--budget-id", "budget-a"],
            "invalid_max_invocations",
        ),
        (
            ["--max-invocations", str(2**63), "--budget-id", "budget-a"],
            "invalid_max_invocations",
        ),
        (
            ["--max-total-tokens", "0", "--budget-id", "budget-a"],
            "invalid_max_total_tokens",
        ),
        (
            ["--max-total-tokens", "-1", "--budget-id", "budget-a"],
            "invalid_max_total_tokens",
        ),
        (
            ["--max-total-tokens", str(2**63), "--budget-id", "budget-a"],
            "invalid_max_total_tokens",
        ),
        (["--max-invocations", "1"], "budget_id_required"),
        (["--budget-id", "   ", "--max-invocations", "1"], "invalid_budget_id"),
    ),
)
def test_daemon_budget_options_refuse_invalid_limits_before_state_open(
    tmp_path: Path,
    args: list[str],
    code: str,
) -> None:
    workspace = tmp_path / "missing"
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "run",
            "daemon",
            *args,
        ]
    )

    assert exit_code == 2
    assert stdout == ""
    assert _json(stderr)["code"] == code
    assert not (workspace / ".millrace" / "daemon.lock").exists()
    assert not (workspace / ".millrace" / "runtime.sqlite3").exists()


@pytest.mark.parametrize(
    "attribute",
    ("max_wall_seconds", "max_invocations", "max_total_tokens"),
)
def test_daemon_budget_options_accept_durable_limit_upper_boundary(
    attribute: str,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.contracts.state import DURABLE_INT64_MAX

    values: dict[str, object] = {
        "idle_sleep": 0.0,
        "max_ticks": 1,
        "activation_id": None,
        "adapter_kind": None,
        "monitor": "none",
        "adapter_config_json": None,
        "actor_id": "local_operator",
        "budget_id": "budget-upper-boundary",
        "max_wall_seconds": None,
        "max_invocations": None,
        "max_total_tokens": None,
    }
    values[attribute] = str(DURABLE_INT64_MAX)

    options = daemon.daemon_options_from_namespace(SimpleNamespace(**values))

    assert getattr(options, attribute) == DURABLE_INT64_MAX


def test_daemon_budget_epoch_restart_preserves_deadline_and_totals(
    tmp_path: Path,
) -> None:
    from millrace.contracts.state import DaemonBudgetEpochRecord

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    first = DaemonBudgetEpochRecord(
        budget_id="budget-a",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=plan_ref,
        max_wall_seconds=60,
        max_invocations=2,
        max_total_tokens=None,
        started_at=100,
        wall_deadline=160,
        last_observed_at=100,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(first)
    resumed = runtime.store.create_or_resume_daemon_budget_epoch(
        DaemonBudgetEpochRecord(
            budget_id="budget-a",
            workspace_path=str(runtime.paths.workspace_path),
            selected_plan_ref=plan_ref,
            max_wall_seconds=60,
            max_invocations=2,
            max_total_tokens=None,
            started_at=100,
            wall_deadline=160,
            last_observed_at=120,
        )
    )

    assert resumed.started_at == 100
    assert resumed.wall_deadline == 160
    assert resumed.last_observed_at == 120
    assert resumed.accepted_start_count == 0
    with pytest.raises(ValueError, match="immutable_limits_changed"):
        runtime.store.create_or_resume_daemon_budget_epoch(
            DaemonBudgetEpochRecord(
                budget_id="budget-a",
                workspace_path=str(runtime.paths.workspace_path),
                selected_plan_ref=plan_ref,
                max_wall_seconds=61,
                max_invocations=2,
                max_total_tokens=None,
                started_at=100,
                wall_deadline=161,
                last_observed_at=121,
            )
        )
    runtime.close()


def test_terminal_daemon_budget_restart_preserves_terminal_observation(
    tmp_path: Path,
) -> None:
    from millrace.contracts.state import DaemonBudgetEpochRecord
    from millrace.operator.status import daemon_budget_projection

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    active = DaemonBudgetEpochRecord(
        budget_id="budget-terminal-restart",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=plan_ref,
        max_wall_seconds=10,
        max_invocations=None,
        max_total_tokens=None,
        started_at=100,
        wall_deadline=110,
        last_observed_at=100,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(active)
    terminal = runtime.store._stop_daemon_budget_epoch(
        active.budget_id,
        observed_at=112,
        status="stopped",
        reason="daemon_stopped",
    )

    resumed = runtime.store.create_or_resume_daemon_budget_epoch(
        replace(active, last_observed_at=200)
    )

    assert resumed == terminal
    projection = daemon_budget_projection(resumed)
    assert projection["status"] == "stopped"
    assert projection["terminal_reason"] == "daemon_stopped"
    assert projection["last_observed_at"] == 112
    assert projection["wall_cleanup_grace_overshoot"] == 2
    assert runtime.store.load_daemon_budget_epoch(active.budget_id) == terminal
    runtime.close()


def test_new_daemon_budget_epoch_refuses_terminal_state_without_mutation(
    tmp_path: Path,
) -> None:
    from millrace.contracts.state import DaemonBudgetEpochRecord

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    terminal = DaemonBudgetEpochRecord(
        budget_id="budget-new-terminal",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=None,
        started_at=100,
        wall_deadline=None,
        last_observed_at=100,
        status="stopped",
        terminal_reason="daemon_stopped",
    )
    before = runtime.paths.db_path.read_bytes()

    with pytest.raises(ValueError, match="new daemon budget epoch must be active"):
        runtime.store.create_or_resume_daemon_budget_epoch(terminal)

    assert runtime.store.load_daemon_budget_epoch(terminal.budget_id) is None
    assert runtime.paths.db_path.read_bytes() == before
    assert _load(runtime).dispatch_suspension is None
    runtime.close()


@pytest.mark.parametrize(
    "changes",
    (
        {"accepted_start_count": 1},
        {"cumulative_input_tokens": 1, "cumulative_total_tokens": 1},
    ),
)
def test_new_daemon_budget_epoch_refuses_nonzero_counters_without_mutation(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    from millrace.contracts.state import DaemonBudgetEpochRecord

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    candidate = replace(
        DaemonBudgetEpochRecord(
            budget_id="budget-new-nonzero",
            workspace_path=str(runtime.paths.workspace_path),
            selected_plan_ref=plan_ref,
            max_wall_seconds=None,
            max_invocations=1,
            max_total_tokens=None,
            started_at=100,
            wall_deadline=None,
            last_observed_at=100,
        ),
        **changes,
    )
    before = runtime.paths.db_path.read_bytes()

    with pytest.raises(
        ValueError,
        match="new daemon budget epoch counters must be zero",
    ):
        runtime.store.create_or_resume_daemon_budget_epoch(candidate)

    assert runtime.store.load_daemon_budget_epoch(candidate.budget_id) is None
    assert runtime.paths.db_path.read_bytes() == before
    runtime.close()


@pytest.mark.parametrize(
    ("last_observed_at", "refused"),
    (
        (199, True),
        (201, False),
        (200 + 86_400, False),
        (200 + 86_401, True),
    ),
)
def test_daemon_budget_epoch_restart_clock_boundaries_preserve_authority(
    tmp_path: Path,
    last_observed_at: int,
    refused: bool,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.state import (
        DaemonBudgetEpochRecord,
        RunnerSessionUsageRecord,
    )

    state, _fingerprint = _state_with_runner_kind("codex")
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
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-clock-boundaries",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=200_000,
        max_invocations=2,
        max_total_tokens=100,
        started_at=100,
        wall_deadline=200_100,
        last_observed_at=200,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_runner_session_usage(
        RunnerSessionUsageRecord(
            budget_id=epoch.budget_id,
            session_id=session.session_id,
            run_id=session.run_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            input_tokens=2,
            output_tokens=3,
            total_tokens=5,
            observed_at=200,
            final=True,
        )
    )
    epoch = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    assert epoch is not None
    candidate = replace(
        epoch,
        last_observed_at=last_observed_at,
        accepted_start_count=0,
        cumulative_input_tokens=0,
        cumulative_output_tokens=0,
        cumulative_total_tokens=0,
    )

    if refused:
        with pytest.raises(ValueError, match="daemon_budget_clock_discontinuity"):
            runtime.store.create_or_resume_daemon_budget_epoch(candidate)
    else:
        runtime.store.create_or_resume_daemon_budget_epoch(candidate)

    persisted = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    assert persisted is not None
    assert persisted.started_at == 100
    assert persisted.wall_deadline == 200_100
    assert persisted.accepted_start_count == 1
    assert persisted.cumulative_input_tokens == 2
    assert persisted.cumulative_output_tokens == 3
    assert persisted.cumulative_total_tokens == 5
    assert persisted.last_observed_at == (200 if refused else last_observed_at)
    runtime.close()


def test_daemon_budget_epoch_refuses_implausibly_forward_clock_restart(
    tmp_path: Path,
) -> None:
    from millrace.contracts.state import DaemonBudgetEpochRecord

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-forward-clock",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=None,
        started_at=100,
        wall_deadline=None,
        last_observed_at=100,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)

    with pytest.raises(ValueError, match="daemon_budget_clock_discontinuity"):
        runtime.store.create_or_resume_daemon_budget_epoch(
            replace(epoch, last_observed_at=100 + 86_401)
        )

    assert runtime.store.load_daemon_budget_epoch(epoch.budget_id) == epoch
    runtime.close()


def test_daemon_budget_epoch_maps_wall_deadline_overflow_to_stable_refusal(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.context import CliCommandError
    from millrace.contracts.state import DURABLE_INT64_MAX

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    options = replace(
        _daemon_options(runtime.paths, max_ticks=1),
        budget_id="budget-wall-overflow",
        max_wall_seconds=1,
    )

    with pytest.raises(CliCommandError) as exc_info:
        daemon._prepare_budget_epoch(options, now=DURABLE_INT64_MAX)

    assert exc_info.value.code == "daemon_budget_limit_out_of_range"
    assert runtime.store.load_daemon_budget_epoch(options.budget_id) is None
    runtime.close()


def test_daemon_budget_epoch_maps_corrupt_durable_load_to_stable_refusal(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.context import CliCommandError

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    options = replace(
        _daemon_options(runtime.paths, max_ticks=1),
        budget_id="budget-corrupt-restart",
        max_invocations=1,
    )
    daemon._prepare_budget_epoch(options, now=100)
    runtime.store._connection.execute("PRAGMA ignore_check_constraints = ON")
    runtime.store._connection.execute(
        "UPDATE daemon_budget_epochs SET accepted_start_count = ? WHERE budget_id = ?",
        (sqlite3.Binary(b"wrong-type"), options.budget_id),
    )
    runtime.store._connection.commit()
    runtime.store._connection.execute("PRAGMA ignore_check_constraints = OFF")

    with pytest.raises(CliCommandError) as exc_info:
        daemon._prepare_budget_epoch(options, now=101)

    assert exc_info.value.code == "daemon_budget_epoch_refused"
    runtime.close()


def test_budget_terminalization_rolls_back_when_dispatch_suspension_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    options = replace(
        _daemon_options(runtime.paths, max_ticks=1),
        budget_id="budget-atomic-stop",
        max_invocations=1,
    )
    daemon._prepare_budget_epoch(options, now=100)

    def fail_suspension(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected suspension persistence failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(SQLiteRuntimeStore, "persist_runtime_state", fail_suspension)
        with pytest.raises(
            RuntimeError,
            match="injected suspension persistence failure",
        ):
            daemon._finish_budget(
                options,
                observed_at=101,
                reason="invocation_limit_exhausted",
            )

    epoch = runtime.store.load_daemon_budget_epoch("budget-atomic-stop")
    assert epoch is not None
    assert epoch.status == "active"
    assert epoch.terminal_reason is None
    assert _load(runtime).dispatch_suspension is None

    daemon._finish_budget(
        options,
        observed_at=101,
        reason="invocation_limit_exhausted",
    )
    stopped = runtime.store.load_daemon_budget_epoch("budget-atomic-stop")
    assert stopped is not None
    assert (stopped.status, stopped.terminal_reason) == (
        "exhausted",
        "invocation_limit_exhausted",
    )
    assert _load(runtime).dispatch_suspension is not None
    runtime.close()


def test_invocation_ceiling_prevents_another_start_before_adapter_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.state import DaemonBudgetEpochRecord

    state, _fingerprint = _state_with_runner_kind("codex")
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
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-invocation-ceiling",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=None,
        started_at=100,
        wall_deadline=None,
        last_observed_at=100,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    paths = runtime.paths
    runtime.close()
    adapter_calls = 0

    def fail_if_started(*_args: object, **_kwargs: object) -> object:
        nonlocal adapter_calls
        adapter_calls += 1
        raise AssertionError("invocation-exhausted budget started another unit")

    monkeypatch.setattr(daemon, "_run_one_bounded_unit", fail_if_started)
    options = replace(
        _daemon_options(paths, max_ticks=1, local_config=AdapterLocalConfig()),
        budget_id=epoch.budget_id,
        max_invocations=1,
    )

    summary = daemon.run_daemon_loop(options, wall_clock=lambda: 100.0)
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        persisted = reopened.store.load_daemon_budget_epoch(epoch.budget_id)
        durable = _load(reopened)
    finally:
        reopened.close()

    assert adapter_calls == 0
    assert summary.iterations == 0
    assert summary.stopped_reason == "budget_exhausted"
    assert summary.budget is not None
    assert summary.budget["accepted_start_count"] == 1
    assert summary.budget["terminal_reason"] == "invocation_limit_exhausted"
    assert persisted is not None
    assert (persisted.status, persisted.terminal_reason) == (
        "exhausted",
        "invocation_limit_exhausted",
    )
    assert durable.dispatch_suspension is not None


def test_completed_token_ceiling_prevents_another_start_before_adapter_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import REVIEWED_TOKEN_USAGE_MAPPING
    from millrace.contracts.state import (
        DaemonBudgetEpochRecord,
        RunnerSessionUsageRecord,
    )

    state, _fingerprint = _state_with_runner_kind("codex")
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
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-token-ceiling",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=None,
        max_total_tokens=10,
        started_at=100,
        wall_deadline=None,
        last_observed_at=100,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_runner_session_usage(
        RunnerSessionUsageRecord(
            budget_id=epoch.budget_id,
            session_id=session.session_id,
            run_id=session.run_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            input_tokens=6,
            output_tokens=4,
            total_tokens=10,
            observed_at=100,
            final=True,
        )
    )
    paths = runtime.paths
    runtime.close()
    adapter_calls = 0

    class ReviewedAdapter:
        token_usage_mapping_capability = REVIEWED_TOKEN_USAGE_MAPPING

    def fail_if_started(*_args: object, **_kwargs: object) -> object:
        nonlocal adapter_calls
        adapter_calls += 1
        raise AssertionError("token-exhausted budget started another unit")

    monkeypatch.setattr(daemon, "resolve_adapter", lambda *_args: ReviewedAdapter())
    monkeypatch.setattr(daemon, "_run_one_bounded_unit", fail_if_started)
    options = replace(
        _daemon_options(
            paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=AdapterLocalConfig(),
        ),
        budget_id=epoch.budget_id,
        max_total_tokens=10,
    )

    summary = daemon.run_daemon_loop(options, wall_clock=lambda: 100.0)
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        persisted = reopened.store.load_daemon_budget_epoch(epoch.budget_id)
        durable = _load(reopened)
    finally:
        reopened.close()

    assert adapter_calls == 0
    assert summary.iterations == 0
    assert summary.stopped_reason == "budget_exhausted"
    assert summary.budget is not None
    assert summary.budget["cumulative_total_tokens"] == 10
    assert summary.budget["terminal_reason"] == "token_limit_exhausted"
    assert persisted is not None
    assert (persisted.status, persisted.terminal_reason) == (
        "exhausted",
        "token_limit_exhausted",
    )
    assert durable.dispatch_suspension is not None


def test_one_session_token_overshoot_is_truthful_and_blocks_another_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import REVIEWED_TOKEN_USAGE_MAPPING
    from millrace.contracts.state import (
        DaemonBudgetEpochRecord,
        RunnerSessionUsageRecord,
    )

    state, _fingerprint = _ready_state()
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
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-token-overshoot",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=2,
        max_total_tokens=10,
        started_at=100,
        wall_deadline=None,
        last_observed_at=100,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    usage = RunnerSessionUsageRecord(
        budget_id=epoch.budget_id,
        session_id=session.session_id,
        run_id=session.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        input_tokens=8,
        output_tokens=7,
        total_tokens=15,
        observed_at=110,
        final=True,
    )
    runtime.store.record_runner_session_usage(usage)
    paths = runtime.paths
    runtime.close()
    adapter_calls = 0

    class ReviewedAdapter:
        token_usage_mapping_capability = REVIEWED_TOKEN_USAGE_MAPPING

    def fail_if_started(*_args: object, **_kwargs: object) -> object:
        nonlocal adapter_calls
        adapter_calls += 1
        raise AssertionError("overshot token budget started another unit")

    monkeypatch.setattr(daemon, "resolve_adapter", lambda *_args: ReviewedAdapter())
    monkeypatch.setattr(daemon, "_run_one_bounded_unit", fail_if_started)
    options = replace(
        _daemon_options(
            paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=AdapterLocalConfig(),
        ),
        budget_id=epoch.budget_id,
        max_invocations=2,
        max_total_tokens=10,
    )

    summary = daemon.run_daemon_loop(options, wall_clock=lambda: 110.0)
    reopened = daemon.open_runtime_context(paths, command="test")
    try:
        persisted = reopened.store.load_daemon_budget_epoch(epoch.budget_id)
        persisted_usage = reopened.store.load_runner_session_usage(session.session_id)
        durable = _load(reopened)
    finally:
        reopened.close()

    assert adapter_calls == 0
    assert summary.stopped_reason == "budget_exhausted"
    assert summary.budget is not None
    assert summary.budget["cumulative_input_tokens"] == 8
    assert summary.budget["cumulative_output_tokens"] == 7
    assert summary.budget["cumulative_total_tokens"] == 15
    assert summary.budget["token_overshoot"] == 5
    assert summary.budget["terminal_reason"] == "token_limit_exhausted"
    assert persisted_usage == usage
    assert persisted is not None
    assert (persisted.status, persisted.terminal_reason) == (
        "exhausted",
        "token_limit_exhausted",
    )
    assert durable.dispatch_suspension is not None


def test_usage_refusal_rolls_back_when_dispatch_suspension_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon, session_persistence
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    options = replace(
        _daemon_options(runtime.paths, max_ticks=1),
        budget_id="budget-atomic-refusal",
        max_invocations=1,
    )
    epoch = daemon._prepare_budget_epoch(options, now=100)
    assert epoch is not None

    def fail_suspension(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected usage-refusal persistence failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(SQLiteRuntimeStore, "persist_runtime_state", fail_suspension)
        with pytest.raises(
            RuntimeError,
            match="injected usage-refusal persistence failure",
        ):
            session_persistence._refuse_governed_usage(runtime, epoch)

    active = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    assert active == epoch
    assert _load(runtime).dispatch_suspension is None
    runtime.close()


def test_governed_usage_does_not_swallow_terminalization_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import session_persistence
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.state import DaemonBudgetEpochRecord

    state, _fingerprint = _state_with_runner_kind("codex")
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
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-terminalization-refusal",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=None,
        max_total_tokens=10,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_budgeted_runner_start(epoch.budget_id, session)

    def refuse_terminalization(*_args: object, **_kwargs: object) -> None:
        raise ValueError("injected terminalization refusal")

    monkeypatch.setattr(
        session_persistence,
        "_refuse_governed_usage",
        refuse_terminalization,
    )

    with pytest.raises(ValueError, match="injected terminalization refusal"):
        session_persistence._persist_governed_runner_usage(
            runtime,
            session,
            None,
        )

    persisted = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    assert persisted is not None
    assert persisted.status == "active"
    assert _load(runtime).dispatch_suspension is None
    runtime.close()


def test_governed_usage_aggregate_overflow_refuses_and_suspends_atomically(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import session_persistence
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import AdapterTokenUsage
    from millrace.contracts.state import (
        DURABLE_INT64_MAX,
        DaemonBudgetEpochRecord,
        RunnerSessionUsageRecord,
    )
    from support.runner_sessions import _ready_state_with_two_activations

    state, _fingerprint = _ready_state_with_two_activations()
    runtime = _runtime(tmp_path, state)
    results = [
        run_bounded_execution_unit(
            runtime,
            local_config=_codex_success_config(),
        )
        for _ in range(2)
    ]
    assert all(result.run_id is not None for result in results)
    durable = _load(runtime)
    runs = [durable.runs[str(result.run_id)] for result in results]
    sessions = []
    for run in runs:
        assert run.current_session_id is not None
        sessions.append(durable.runner_sessions[run.current_session_id])
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-usage-aggregate-overflow",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=runs[0].run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=None,
        max_total_tokens=DURABLE_INT64_MAX,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    for session in sessions:
        runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
        runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_runner_session_usage(
        RunnerSessionUsageRecord(
            budget_id=epoch.budget_id,
            session_id=sessions[0].session_id,
            run_id=sessions[0].run_id,
            dispatch_generation=sessions[0].dispatch_generation,
            session_fencing_token=sessions[0].session_fencing_token,
            input_tokens=DURABLE_INT64_MAX - 1,
            output_tokens=0,
            total_tokens=DURABLE_INT64_MAX - 1,
            observed_at=1,
            final=True,
        )
    )

    accepted = session_persistence._persist_governed_runner_usage(
        runtime,
        sessions[1],
        cast(
            Any,
            SimpleNamespace(
                token_usage=AdapterTokenUsage(
                    input_tokens=2,
                    output_tokens=0,
                    total_tokens=2,
                )
            ),
        ),
    )

    persisted = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    assert accepted is False
    assert persisted is not None
    assert (
        persisted.cumulative_input_tokens,
        persisted.cumulative_output_tokens,
        persisted.cumulative_total_tokens,
    ) == (
        DURABLE_INT64_MAX - 1,
        0,
        DURABLE_INT64_MAX - 1,
    )
    assert (persisted.status, persisted.terminal_reason) == (
        "refused",
        "runner_usage_evidence_refused",
    )
    assert runtime.store.load_runner_session_usage(sessions[1].session_id) is None
    assert _load(runtime).dispatch_suspension is not None
    runtime.close()


def test_token_budget_refuses_same_name_adapter_without_reviewed_mapping(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.context import CliCommandError

    class SpoofedMillforgeAdapter:
        adapter_kind = "millforge"

        def start_session(self, request: object) -> object:
            return request

        def reconcile_session(self, request: object) -> object:
            return request

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    options = replace(
        _daemon_options(
            runtime.paths,
            max_ticks=1,
            adapter_kind="millforge",
            local_config=AdapterLocalConfig(
                adapters={"millforge": cast(Any, SpoofedMillforgeAdapter())},
            ),
        ),
        budget_id="budget-spoof",
        max_total_tokens=100,
    )

    with pytest.raises(CliCommandError) as exc_info:
        daemon._prepare_budget_epoch(options, now=100)

    assert exc_info.value.code == "runner_usage_mapping_unsupported"
    assert runtime.store.load_daemon_budget_epoch("budget-spoof") is None
    runtime.close()


@pytest.mark.parametrize("adapter_case", ("unresolved", "unsupported"))
def test_token_budget_adapter_admission_precedes_all_runtime_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_case: str,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.context import CliCommandError
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    class UnsupportedAdapter:
        adapter_kind = "millforge"

        def __init__(self) -> None:
            self.start_calls = 0
            self.reconcile_calls = 0

        def start_session(self, request: object) -> object:
            self.start_calls += 1
            return request

        def reconcile_session(self, request: object) -> object:
            self.reconcile_calls += 1
            return request

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()
    before = paths.db_path.read_bytes()
    adapter = UnsupportedAdapter()
    local_config = (
        AdapterLocalConfig()
        if adapter_case == "unresolved"
        else AdapterLocalConfig(adapters={"millforge": cast(Any, adapter)})
    )
    options = replace(
        _daemon_options(
            paths,
            max_ticks=1,
            adapter_kind="millforge",
            local_config=local_config,
        ),
        budget_id=f"budget-{adapter_case}",
        max_total_tokens=100,
    )
    monkeypatch.setattr(
        daemon,
        "_reconcile_startup_sessions",
        lambda *_args, **_kwargs: pytest.fail("startup reconciliation ran"),
    )
    monkeypatch.setattr(
        daemon,
        "_run_one_bounded_unit",
        lambda *_args, **_kwargs: pytest.fail("claim acceptance ran"),
    )

    with pytest.raises(CliCommandError) as exc_info:
        daemon.run_daemon_loop(options, wall_clock=lambda: 100.0)

    assert exc_info.value.code == "runner_usage_mapping_unsupported"
    assert paths.db_path.read_bytes() == before
    assert (adapter.start_calls, adapter.reconcile_calls) == (0, 0)
    reopened = SQLiteRuntimeStore.open(paths.db_path)
    assert reopened.load_daemon_budget_epoch(f"budget-{adapter_case}") is None
    assert reopened.load_runtime_state(runtime.cas_store) == state
    reopened.close()


def test_generic_budget_admission_does_not_branch_on_provider_or_workflow_names() -> (
    None
):
    import ast
    import inspect
    import textwrap

    from millrace.adapters.cli import daemon

    functions = (
        daemon._prepare_budget_epoch,
        daemon._account_budgeted_starts,
        daemon._budget_exhaustion_reason,
        daemon._finish_budget,
    )
    branch_literals: set[str] = set()
    sources: list[str] = []
    for function in functions:
        source = textwrap.dedent(inspect.getsource(function))
        sources.append(source)
        tree = ast.parse(source)
        branch_nodes = (
            node.test
            for node in ast.walk(tree)
            if isinstance(node, (ast.If, ast.IfExp))
        )
        for branch in branch_nodes:
            branch_literals.update(
                value.value
                for value in ast.walk(branch)
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            )

    forbidden_names = {
        "openai",
        "codex",
        "millforge",
        "planning",
        "execution",
        "learning",
        "builder",
        "checker",
        "planner",
        "arbiter",
    }
    assert branch_literals.isdisjoint(forbidden_names)
    admission_source = sources[0]
    assert "resolve_adapter(" in admission_source
    assert "has_reviewed_token_usage_mapping(" in admission_source


def test_budget_epoch_loader_refuses_wrongly_typed_durable_fields(
    tmp_path: Path,
) -> None:
    from millrace.contracts.state import DaemonBudgetEpochRecord

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-corrupt",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=None,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store._connection.execute(
        "UPDATE daemon_budget_epochs SET workspace_path = ? WHERE budget_id = ?",
        (sqlite3.Binary(b"wrong-type"), epoch.budget_id),
    )
    runtime.store._connection.commit()

    with pytest.raises(ValueError, match="invalid daemon budget epoch row"):
        runtime.store.load_daemon_budget_epoch(epoch.budget_id)

    runtime.close()


def test_daemon_charges_accepted_start_before_adapter_execution(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    state, _fingerprint = _state_with_runner_kind("codex")
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    delegate_config = _codex_success_config()
    delegate = delegate_config.adapters["codex"]
    observed_counts: list[int] = []

    class InspectingCodexAdapter:
        adapter_kind = "codex"

        def start_session(self, request: object) -> object:
            store = SQLiteRuntimeStore.open(paths.db_path)
            try:
                epoch = store.load_daemon_budget_epoch("budget-accepted-start")
                assert epoch is not None
                observed_counts.append(epoch.accepted_start_count)
            finally:
                store.close()
            return delegate.start_session(request)

        def reconcile_session(self, request: object) -> object:
            return delegate.reconcile_session(request)

    runtime.close()
    summary = daemon.run_daemon_loop(
        replace(
            _daemon_options(
                paths,
                max_ticks=1,
                adapter_kind="codex",
                local_config=AdapterLocalConfig(
                    adapters={"codex": cast(Any, InspectingCodexAdapter())},
                ),
            ),
            budget_id="budget-accepted-start",
            max_invocations=1,
        )
    )

    assert summary.units_started == 1
    assert observed_counts == [1]


def test_new_budget_epoch_does_not_charge_pre_epoch_start(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit

    state, _fingerprint = _state_with_runner_kind("codex")
    runtime = _runtime(tmp_path, state)
    prior = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    assert prior.run_id is not None
    options = replace(
        _daemon_options(
            runtime.paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=_codex_success_config(),
        ),
        budget_id="new-budget",
        max_invocations=1,
    )
    epoch = daemon._prepare_budget_epoch(options, now=2_000_000_000)
    assert epoch is not None

    daemon._account_budgeted_starts(options)

    recovered = runtime.store.load_daemon_budget_epoch("new-budget")
    assert recovered is not None
    assert recovered.accepted_start_count == 0
    runtime.close()


def test_budget_start_recovery_charges_only_a_durably_owned_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon

    state, _fingerprint = _state_with_runner_kind("codex")
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()
    options = replace(
        _daemon_options(
            paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=_codex_success_config(),
        ),
        budget_id="restart-budget",
        max_invocations=1,
    )

    def fail_after_start_intent(_options: object, _session: object) -> None:
        raise ValueError("injected_budget_accounting_failure")

    monkeypatch.setattr(daemon, "_account_budgeted_start", fail_after_start_intent)
    summary = daemon.run_daemon_loop(options)
    assert summary.last_result["code"] == "adapter_failure"

    runtime = daemon.open_runtime_context(paths, command="test.daemon-budget")
    durable = _load(runtime)
    session = next(iter(durable.runner_sessions.values()))
    assert session.start_intent_at is not None
    assert (
        runtime.store.daemon_budget_id_for_session(session.session_id)
        == "restart-budget"
    )
    runtime.close()

    daemon._account_budgeted_starts(options)
    daemon._account_budgeted_starts(options)

    runtime = daemon.open_runtime_context(paths, command="test.daemon-budget")
    recovered = runtime.store.load_daemon_budget_epoch("restart-budget")
    assert recovered is not None
    assert recovered.accepted_start_count == 1
    runtime.close()


def test_budget_reservation_limits_pending_ownership_before_start_intent(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts import QueueFamilyId
    from millrace.contracts.state import DaemonBudgetEpochRecord
    from millrace.contracts.transition import EnqueueWork
    from support.kernel_ping import apply_accepted_input, kernel_ping_context

    state, _fingerprint = _state_with_runner_kind("codex")
    state = apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-b",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-b", "body": "Build proof B"},
        ),
        kernel_ping_context("enqueue-b"),
    )
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    second = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    assert first.run_id is not None
    assert second.run_id is not None
    durable = _load(runtime)
    first_run = durable.runs[first.run_id]
    second_run = durable.runs[second.run_id]
    assert first_run.current_session_id is not None
    assert second_run.current_session_id is not None
    first_session = durable.runner_sessions[first_run.current_session_id]
    second_session = durable.runner_sessions[second_run.current_session_id]
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-pending-ownership",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=first_run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=None,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, first_session)

    with pytest.raises(ValueError, match="daemon_budget_invocations_exhausted"):
        runtime.store.reserve_budgeted_runner_start(epoch.budget_id, second_session)

    assert (
        runtime.store.reserve_budgeted_runner_start(
            epoch.budget_id,
            first_session,
        )
        == epoch
    )
    bindings = runtime.store._connection.execute(
        """
        SELECT session_id, accepted_at
        FROM daemon_budget_sessions
        WHERE budget_id = ?
        ORDER BY session_id
        """,
        (epoch.budget_id,),
    ).fetchall()
    assert bindings == [(first_session.session_id, None)]
    runtime.close()


def test_concurrent_budget_start_replay_is_atomic(tmp_path: Path) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.state import DaemonBudgetEpochRecord
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    state, _fingerprint = _state_with_runner_kind("codex")
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
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-concurrent-replay",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=None,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    read_barrier = threading.Barrier(2)
    start_barrier = threading.Barrier(2)
    results: list[int] = []
    failures: list[BaseException] = []

    def accept_start() -> None:
        store = SQLiteRuntimeStore.open(runtime.paths.db_path)
        original_load = store.load_daemon_budget_epoch
        first_load = True

        def synchronized_load(budget_id: str) -> DaemonBudgetEpochRecord | None:
            nonlocal first_load
            loaded = original_load(budget_id)
            if first_load:
                first_load = False
                if not store._connection.in_transaction:
                    assert read_barrier.wait(5) in (0, 1)
            return loaded

        store.load_daemon_budget_epoch = synchronized_load
        try:
            assert start_barrier.wait(5) in (0, 1)
            results.append(
                store.record_budgeted_runner_start(
                    epoch.budget_id, session
                ).accepted_start_count
            )
        except BaseException as exc:
            failures.append(exc)
        finally:
            store.close()

    first_thread = threading.Thread(target=accept_start)
    second_thread = threading.Thread(target=accept_start)
    first_thread.start()
    second_thread.start()
    first_thread.join(5)
    second_thread.join(5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert failures == []
    assert sorted(results) == [1, 1]
    persisted = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    assert persisted is not None
    assert persisted.accepted_start_count == 1
    assert runtime.store._connection.execute(
        """
        SELECT COUNT(*)
        FROM daemon_budget_sessions
        WHERE budget_id = ? AND accepted_at IS NOT NULL
        """,
        (epoch.budget_id,),
    ).fetchone() == (1,)
    runtime.close()


def test_concurrent_budget_start_limit_admission_is_atomic(tmp_path: Path) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts import QueueFamilyId
    from millrace.contracts.state import DaemonBudgetEpochRecord, RunnerSessionRecord
    from millrace.contracts.transition import EnqueueWork
    from millrace.substrate.sqlite import SQLiteRuntimeStore
    from support.kernel_ping import apply_accepted_input, kernel_ping_context

    state, _fingerprint = _state_with_runner_kind("codex")
    state = apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-b",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-b", "body": "Build proof B"},
        ),
        kernel_ping_context("enqueue-b"),
    )
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    second = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )
    assert first.run_id is not None
    assert second.run_id is not None
    durable = _load(runtime)
    first_run = durable.runs[first.run_id]
    second_run = durable.runs[second.run_id]
    assert first_run.current_session_id is not None
    assert second_run.current_session_id is not None
    sessions = (
        durable.runner_sessions[first_run.current_session_id],
        durable.runner_sessions[second_run.current_session_id],
    )
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-concurrent-limit",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=first_run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=None,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    for session in sessions:
        runtime.store._connection.execute(
            """
            INSERT INTO daemon_budget_sessions (
                session_id, schema_version, budget_id, run_id,
                dispatch_generation, session_fencing_token, accepted_at
            ) VALUES (?, 1, ?, ?, ?, ?, NULL)
            """,
            (
                session.session_id,
                epoch.budget_id,
                session.run_id,
                session.dispatch_generation,
                session.session_fencing_token,
            ),
        )
    runtime.store._connection.commit()
    read_barrier = threading.Barrier(2)
    start_barrier = threading.Barrier(2)
    accepted_counts: list[int] = []
    refusals: list[str] = []
    failures: list[BaseException] = []

    def accept_start(session: RunnerSessionRecord) -> None:
        store = SQLiteRuntimeStore.open(runtime.paths.db_path)
        original_load = store.load_daemon_budget_epoch
        first_load = True

        def synchronized_load(budget_id: str) -> DaemonBudgetEpochRecord | None:
            nonlocal first_load
            loaded = original_load(budget_id)
            if first_load:
                first_load = False
                if not store._connection.in_transaction:
                    assert read_barrier.wait(5) in (0, 1)
            return loaded

        store.load_daemon_budget_epoch = synchronized_load
        try:
            assert start_barrier.wait(5) in (0, 1)
            accepted_counts.append(
                store.record_budgeted_runner_start(
                    epoch.budget_id, session
                ).accepted_start_count
            )
        except ValueError as exc:
            refusals.append(str(exc))
        except BaseException as exc:
            failures.append(exc)
        finally:
            store.close()

    first_thread = threading.Thread(target=accept_start, args=(sessions[0],))
    second_thread = threading.Thread(target=accept_start, args=(sessions[1],))
    first_thread.start()
    second_thread.start()
    first_thread.join(5)
    second_thread.join(5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert failures == []
    assert accepted_counts == [1]
    assert refusals == ["daemon_budget_invocations_exhausted"]
    persisted = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    assert persisted is not None
    assert persisted.accepted_start_count == 1
    bindings = runtime.store._connection.execute(
        """
        SELECT accepted_at
        FROM daemon_budget_sessions
        WHERE budget_id = ?
        ORDER BY session_id
        """,
        (epoch.budget_id,),
    ).fetchall()
    assert sum(accepted_at is not None for (accepted_at,) in bindings) == 1
    runtime.close()


def test_active_session_stale_history_is_not_mapped_to_dispatch_suspended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.substrate.errors import StorageIntegrityError

    state, _fingerprint = _state_with_runner_kind("codex")
    runtime = _runtime(tmp_path, state)
    options = replace(
        _daemon_options(
            runtime.paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=_codex_error_config(),
        ),
        budget_id="budget-active-stale-history",
        max_invocations=2,
    )
    epoch = daemon._prepare_budget_epoch(options, now=100)
    assert epoch is not None
    active = run_bounded_execution_unit(
        runtime,
        local_config=_codex_error_config(),
    )
    assert active.code == "adapter_failure"
    assert active.activation_id is not None
    assert active.run_id is not None
    durable = _load(runtime)
    assert durable.runs[active.run_id].current_session_id is not None
    runtime.close()
    daemon._finish_budget(
        options,
        observed_at=101,
        reason="invocation_limit_exhausted",
    )

    def raise_stale_history(*_args: object, **_kwargs: object) -> object:
        raise StorageIntegrityError(
            "stale runtime state diverges from durable transition history"
        )

    monkeypatch.setattr(daemon, "run_bounded_execution_unit", raise_stale_history)
    with pytest.raises(
        StorageIntegrityError,
        match="stale runtime state diverges from durable transition history",
    ):
        daemon._run_one_bounded_unit(
            replace(options, activation_id=active.activation_id),
            daemon_stop_requested=lambda: False,
        )


def test_pending_budget_reservation_refuses_foreign_ownership_without_rewrite(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.state import DaemonBudgetEpochRecord

    foreign_plan_state, _foreign_fingerprint = _ready_state()
    state, _fingerprint = _state_with_runner_kind("codex")
    state = replace(
        state,
        admitted_plans={
            **foreign_plan_state.admitted_plans,
            **state.admitted_plans,
        },
    )
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
    owner = DaemonBudgetEpochRecord(
        budget_id="budget-owner",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=4,
        max_total_tokens=None,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    foreign_plan_ref = next(
        admitted.plan_ref for admitted in foreign_plan_state.admitted_plans.values()
    )
    candidates = (
        replace(owner, budget_id="budget-foreign-epoch"),
        replace(
            owner,
            budget_id="budget-foreign-plan",
            selected_plan_ref=foreign_plan_ref,
        ),
        replace(
            owner,
            budget_id="budget-foreign-daemon-path",
            workspace_path=str(tmp_path / "other-daemon"),
        ),
    )
    runtime.store.create_or_resume_daemon_budget_epoch(owner)
    for candidate in candidates:
        runtime.store.create_or_resume_daemon_budget_epoch(candidate)
    runtime.store.reserve_budgeted_runner_start(owner.budget_id, session)
    binding_before = runtime.store._connection.execute(
        "SELECT * FROM daemon_budget_sessions WHERE session_id = ?",
        (session.session_id,),
    ).fetchone()
    assert binding_before is not None
    assert binding_before[-1] is None
    epoch_before = runtime.store._connection.execute(
        "SELECT * FROM daemon_budget_epochs WHERE budget_id = ?",
        (owner.budget_id,),
    ).fetchone()
    assert epoch_before is not None

    with pytest.raises(ValueError, match="daemon_budget_immutable_limits_changed"):
        runtime.store.create_or_resume_daemon_budget_epoch(
            replace(
                owner,
                workspace_path=str(tmp_path / "other-daemon"),
            )
        )

    assert (
        runtime.store._connection.execute(
            "SELECT * FROM daemon_budget_epochs WHERE budget_id = ?",
            (owner.budget_id,),
        ).fetchone()
        == epoch_before
    )
    assert (
        runtime.store._connection.execute(
            "SELECT * FROM daemon_budget_sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()
        == binding_before
    )

    for candidate in candidates:
        with pytest.raises(
            ValueError,
            match="runner_session_budget_identity_mismatch",
        ):
            runtime.store.reserve_budgeted_runner_start(
                candidate.budget_id,
                session,
            )
        assert (
            runtime.store._connection.execute(
                "SELECT * FROM daemon_budget_sessions WHERE session_id = ?",
                (session.session_id,),
            ).fetchone()
            == binding_before
        )
        assert (
            runtime.store.pending_budgeted_runner_start_session_ids(candidate.budget_id)
            == ()
        )

    assert runtime.store.daemon_budget_id_for_session(session.session_id) == (
        owner.budget_id
    )
    assert (
        runtime.store.reserve_budgeted_runner_start(
            owner.budget_id,
            session,
        )
        == owner
    )
    runtime.close()


def test_budget_replay_retry_and_non_start_paths_charge_only_new_intents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import (
        BoundedExecutionUnitResult,
        reconcile_pending_runner_sessions,
        run_bounded_execution_unit,
    )

    state, _fingerprint = _state_with_runner_kind("codex")
    runtime = _runtime(tmp_path, state)
    options = replace(
        _daemon_options(
            runtime.paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=_codex_error_config(),
        ),
        budget_id="budget-retry-free-paths",
        max_invocations=2,
    )
    daemon._prepare_budget_epoch(options, now=100)
    callbacks = {
        "on_start_reserved": lambda session: daemon._reserve_budgeted_start(
            options,
            session,
        ),
        "on_accepted_start": lambda session: daemon._account_budgeted_start(
            options,
            session,
        ),
    }

    first = run_bounded_execution_unit(
        runtime,
        local_config=_codex_error_config(),
        **callbacks,
    )
    assert first.code == "adapter_failure"
    assert first.run_id is not None
    first_state = _load(runtime)
    first_session_id = first_state.runs[first.run_id].current_session_id
    assert first_session_id is not None
    first_session = first_state.runner_sessions[first_session_id]
    first_epoch = runtime.store.load_daemon_budget_epoch(options.budget_id)
    assert first_epoch is not None
    assert first_epoch.accepted_start_count == 1

    replay = runtime.store.record_budgeted_runner_start(
        options.budget_id,
        first_session,
    )
    assert replay.accepted_start_count == 1
    idle = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
        **callbacks,
    )
    assert idle.code == "no_ready_work"
    reconciled = reconcile_pending_runner_sessions(
        runtime,
        local_config=_codex_success_config(),
        **callbacks,
    )
    assert reconciled.code == "no_runner_session_reconciliation"
    assert daemon._summary_budget(options)["accepted_start_count"] == 1

    monkeypatch.setattr(
        daemon,
        "run_lifecycle_transition_once",
        lambda _runtime: BoundedExecutionUnitResult(
            code="lifecycle_transition_applied"
        ),
    )
    lifecycle = daemon._run_one_bounded_unit(
        options,
        daemon_stop_requested=lambda: False,
    )
    assert lifecycle.code == "lifecycle_transition_applied"
    assert (
        runtime.store.load_daemon_budget_epoch(options.budget_id).accepted_start_count
        == 1
    )

    retry = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=_codex_success_config(),
        **callbacks,
    )
    assert retry.code == "observation_accepted"
    retried_epoch = runtime.store.load_daemon_budget_epoch(options.budget_id)
    assert retried_epoch is not None
    assert retried_epoch.accepted_start_count == 2
    assert (
        len(
            runtime.store._connection.execute(
                """
            SELECT session_id
            FROM daemon_budget_sessions
            WHERE budget_id = ? AND accepted_at IS NOT NULL
            """,
                (options.budget_id,),
            ).fetchall()
        )
        == 2
    )
    runtime.close()


def test_failure_before_start_intent_leaves_pending_identity_uncharged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon, session_coordinator
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.transition import AdvanceRunnerSession

    state, _fingerprint = _state_with_runner_kind("codex")
    runtime = _runtime(tmp_path, state)
    options = replace(
        _daemon_options(runtime.paths, max_ticks=1),
        budget_id="budget-pre-intent-failure",
        max_invocations=1,
    )
    daemon._prepare_budget_epoch(options, now=100)
    real_persist_transition = session_coordinator.complete._persist_transition

    def refuse_start_intent(
        runtime_arg: object,
        transition_input: object,
    ) -> object:
        if isinstance(transition_input, AdvanceRunnerSession):
            return None
        return real_persist_transition(runtime_arg, transition_input)

    monkeypatch.setattr(
        session_coordinator.complete,
        "_persist_transition",
        refuse_start_intent,
    )
    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
        on_start_reserved=lambda session: daemon._reserve_budgeted_start(
            options,
            session,
        ),
        on_accepted_start=lambda session: daemon._account_budgeted_start(
            options,
            session,
        ),
    )

    assert result.code == "session_start_intent_refused"
    durable = _load(runtime)
    session = next(iter(durable.runner_sessions.values()))
    assert (session.state, session.start_intent_at) == ("created", None)
    epoch = runtime.store.load_daemon_budget_epoch(options.budget_id)
    assert epoch is not None
    assert epoch.accepted_start_count == 0
    binding = runtime.store._connection.execute(
        """
        SELECT budget_id, run_id, dispatch_generation,
               session_fencing_token, accepted_at
        FROM daemon_budget_sessions
        WHERE session_id = ?
        """,
        (session.session_id,),
    ).fetchone()
    assert binding == (
        options.budget_id,
        session.run_id,
        session.dispatch_generation,
        session.session_fencing_token,
        None,
    )
    monkeypatch.setattr(
        session_coordinator.complete,
        "_persist_transition",
        real_persist_transition,
    )
    replay = run_bounded_execution_unit(
        runtime,
        activation_id=durable.runs[session.run_id].activation_id,
        local_config=_codex_success_config(),
        on_start_reserved=lambda replayed_session: daemon._reserve_budgeted_start(
            options,
            replayed_session,
        ),
        on_accepted_start=lambda replayed_session: daemon._account_budgeted_start(
            options,
            replayed_session,
        ),
    )
    assert replay.code == "observation_accepted"
    resumed = runtime.store.load_daemon_budget_epoch(options.budget_id)
    assert resumed is not None
    assert resumed.accepted_start_count == 1
    runtime.close()


def test_post_intent_recovery_charges_before_reconciliation_or_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    state, _fingerprint = _state_with_runner_kind("codex")
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()
    delegate = _codex_success_config().adapters["codex"]
    reconcile_counts: list[int] = []
    start_calls = 0

    class RecoveryInspectingAdapter:
        adapter_kind = "codex"

        def start_session(self, request: object) -> object:
            nonlocal start_calls
            start_calls += 1
            return delegate.start_session(request)

        def reconcile_session(self, request: object) -> object:
            store = SQLiteRuntimeStore.open(paths.db_path)
            try:
                epoch = store.load_daemon_budget_epoch("budget-post-intent")
                assert epoch is not None
                reconcile_counts.append(epoch.accepted_start_count)
            finally:
                store.close()
            return delegate.reconcile_session(request)

    options = replace(
        _daemon_options(
            paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=AdapterLocalConfig(
                adapters={"codex": cast(Any, RecoveryInspectingAdapter())},
            ),
        ),
        budget_id="budget-post-intent",
        max_invocations=2,
    )

    def fail_after_intent(_options: object, _session: object) -> None:
        raise ValueError("injected_post_intent_failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(daemon, "_account_budgeted_start", fail_after_intent)
        failed = daemon.run_daemon_loop(options)
    assert failed.last_result["code"] == "adapter_failure"
    assert start_calls == 0

    recovered = daemon.run_daemon_loop(options)
    assert recovered.last_result["code"] in {
        "runner_session_orphan_risk",
        "session_reconciliation_required",
    }
    assert reconcile_counts == [1]
    assert start_calls == 0
    daemon._account_budgeted_starts(options)
    runtime = daemon.open_runtime_context(paths, command="test.daemon-budget")
    epoch = runtime.store.load_daemon_budget_epoch(options.budget_id)
    assert epoch is not None
    assert epoch.accepted_start_count == 1
    assert (
        len(
            runtime.store._connection.execute(
                """
            SELECT session_id
            FROM daemon_budget_sessions
            WHERE budget_id = ? AND accepted_at IS NOT NULL
            """,
                (options.budget_id,),
            ).fetchall()
        )
        == 1
    )
    runtime.close()


def test_one_invocation_budget_admits_only_one_of_two_ready_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.contracts import QueueFamilyId
    from millrace.contracts.transition import EnqueueWork
    from support.kernel_ping import apply_accepted_input, kernel_ping_context

    state, _fingerprint = _state_with_runner_kind("codex")
    state = apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-b",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-b", "body": "Build proof B"},
        ),
        kernel_ping_context("enqueue-b"),
    )
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()
    delegate = _codex_success_config().adapters["codex"]
    adapter_calls = 0
    real_start_session = delegate.start_session

    def count_start(request: object) -> object:
        nonlocal adapter_calls
        adapter_calls += 1
        return real_start_session(request)

    monkeypatch.setattr(delegate, "start_session", count_start)
    options = replace(
        _daemon_options(
            paths,
            max_ticks=2,
            adapter_kind="codex",
            local_config=AdapterLocalConfig(adapters={"codex": delegate}),
        ),
        budget_id="budget-two-claims",
        max_invocations=1,
    )
    summary = daemon.run_daemon_loop(options)

    runtime = daemon.open_runtime_context(paths, command="test.daemon-budget")
    durable = _load(runtime)
    epoch = runtime.store.load_daemon_budget_epoch(options.budget_id)
    bindings = runtime.store._connection.execute(
        """
        SELECT session_id, accepted_at
        FROM daemon_budget_sessions
        WHERE budget_id = ?
        """,
        (options.budget_id,),
    ).fetchall()
    assert summary.stopped_reason == "budget_exhausted"
    assert adapter_calls == 1
    assert epoch is not None
    assert (
        epoch.accepted_start_count,
        epoch.status,
        epoch.terminal_reason,
    ) == (1, "exhausted", "invocation_limit_exhausted")
    assert len(bindings) == 1
    assert bindings[0][1] is not None
    claimed = [
        activation.claimed_by_run_id for activation in durable.activations.values()
    ]
    assert sum(run_id is not None for run_id in claimed) == 1
    assert durable.dispatch_suspension is not None
    assert durable.dispatch_suspension.status == "active"
    runtime.close()


def test_one_invocation_budget_controlled_claim_race_admits_only_one_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli import run as run_module
    from millrace.contracts import QueueFamilyId
    from millrace.contracts.transition import EnqueueWork
    from millrace.substrate.sqlite import SQLiteRuntimeStore
    from support.kernel_ping import apply_accepted_input, kernel_ping_context

    state, _fingerprint = _state_with_runner_kind("codex")
    state = apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-b",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "prompt-b", "body": "Build proof B"},
        ),
        kernel_ping_context("enqueue-b"),
    )
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    delegate = _codex_success_config().adapters["codex"]
    adapter_calls = 0
    real_start_session = delegate.start_session

    def count_start(request: object) -> object:
        nonlocal adapter_calls
        adapter_calls += 1
        return real_start_session(request)

    monkeypatch.setattr(delegate, "start_session", count_start)
    options = replace(
        _daemon_options(
            paths,
            max_ticks=1,
            adapter_kind="codex",
            local_config=AdapterLocalConfig(adapters={"codex": delegate}),
        ),
        budget_id="budget-claim-race",
        max_invocations=1,
    )
    daemon._prepare_budget_epoch(options, now=100)
    runtime.close()
    winner = daemon._run_one_bounded_unit(
        options,
        daemon_stop_requested=lambda: False,
    )
    assert winner.code == "observation_accepted"
    assert winner.activation_id is not None
    assert winner.run_id is not None
    observer = daemon.open_runtime_context(paths, command="test.claim-race")

    claim_selected = threading.Event()
    release_claim = threading.Event()
    suspension_written = threading.Event()
    finish_terminalization = threading.Event()
    claim_results: list[object] = []
    failures: list[BaseException] = []
    real_claim = run_module._claim_activation
    real_stop_budget = SQLiteRuntimeStore._stop_daemon_budget_epoch

    def pause_selected_claim(
        runtime_arg: object,
        state_arg: object,
        *,
        activation_id: str,
    ) -> object:
        claim_selected.set()
        assert release_claim.wait(5)
        return real_claim(
            runtime_arg,
            state_arg,
            activation_id=activation_id,
        )

    def pause_after_suspension(
        store: SQLiteRuntimeStore,
        budget_id: str,
        *,
        observed_at: int,
        status: str,
        reason: str,
    ) -> object:
        suspension_written.set()
        assert finish_terminalization.wait(5)
        return real_stop_budget(
            store,
            budget_id,
            observed_at=observed_at,
            status=status,
            reason=reason,
        )

    monkeypatch.setattr(run_module, "_claim_activation", pause_selected_claim)
    monkeypatch.setattr(
        SQLiteRuntimeStore,
        "_stop_daemon_budget_epoch",
        pause_after_suspension,
    )

    def run_claim() -> None:
        try:
            claim_results.append(
                daemon._run_one_bounded_unit(
                    options,
                    daemon_stop_requested=lambda: False,
                )
            )
        except BaseException as exc:
            failures.append(exc)

    def terminalize() -> None:
        try:
            daemon._finish_budget(
                options,
                observed_at=101,
                reason="invocation_limit_exhausted",
            )
        except BaseException as exc:
            failures.append(exc)

    claim_thread = threading.Thread(target=run_claim)
    claim_thread.start()
    assert claim_selected.wait(5)
    terminal_thread = threading.Thread(target=terminalize)
    terminal_thread.start()
    assert suspension_written.wait(5)

    mid_epoch = observer.store.load_daemon_budget_epoch(options.budget_id)
    mid_state = _load(observer)
    assert mid_epoch is not None
    assert mid_epoch.status == "active"
    assert mid_state.dispatch_suspension is None

    finish_terminalization.set()
    terminal_thread.join(5)
    assert not terminal_thread.is_alive()
    committed_epoch = observer.store.load_daemon_budget_epoch(options.budget_id)
    committed_state = _load(observer)
    assert committed_epoch is not None
    assert committed_epoch.status == "exhausted"
    assert committed_state.dispatch_suspension is not None

    release_claim.set()
    claim_thread.join(5)
    assert not claim_thread.is_alive()
    assert failures == []
    assert len(claim_results) == 1
    claim_result = cast(Any, claim_results[0])
    assert claim_result.code == "no_ready_work"
    assert claim_result.diagnostics[0]["reason"] == "dispatch_suspended"

    final_state = _load(observer)
    final_epoch = observer.store.load_daemon_budget_epoch(options.budget_id)
    assert final_epoch == committed_epoch
    assert final_state.dispatch_suspension == committed_state.dispatch_suspension
    assert (
        final_epoch.accepted_start_count,
        final_epoch.status,
        final_epoch.terminal_reason,
    ) == (1, "exhausted", "invocation_limit_exhausted")
    winner_run = final_state.runs[winner.run_id]
    assert winner_run.current_session_id is not None
    winner_session = final_state.runner_sessions[winner_run.current_session_id]
    assert winner_session.start_intent_at is not None
    assert winner_run.run_ref.plan_ref == final_epoch.selected_plan_ref
    assert adapter_calls == 1
    assert (
        sum(
            session.start_intent_at is not None
            for session in final_state.runner_sessions.values()
        )
        == 1
    )
    bindings = observer.store._connection.execute(
        """
        SELECT session_id, run_id, dispatch_generation,
               session_fencing_token, accepted_at
        FROM daemon_budget_sessions
        WHERE budget_id = ?
        """,
        (options.budget_id,),
    ).fetchall()
    assert bindings == [
        (
            winner_session.session_id,
            winner.run_id,
            winner_session.dispatch_generation,
            winner_session.session_fencing_token,
            winner_session.start_intent_at,
        )
    ]
    assert (
        final_state.activations[winner.activation_id].claimed_by_run_id == winner.run_id
    )
    assert all(
        activation.claimed_by_run_id is None
        for activation_id, activation in final_state.activations.items()
        if activation_id != winner.activation_id
    )
    observer.close()


def test_budget_start_and_usage_accounting_is_replay_safe_fenced_and_rewrite_safe(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.state import (
        DaemonBudgetEpochRecord,
        RunnerSessionUsageRecord,
    )

    state, _fingerprint = _ready_state()
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
    assert session.start_intent_at is not None
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-usage",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=2,
        max_total_tokens=100,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)

    first = runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    replay = runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    assert first.accepted_start_count == replay.accepted_start_count == 1
    runtime.store.persist_runtime_state(durable, runtime.cas_store)
    durable = _load(runtime)
    run = durable.runs[result.run_id]
    assert run.current_session_id is not None
    session = durable.runner_sessions[run.current_session_id]
    assert (
        runtime.store.daemon_budget_id_for_session(session.session_id)
        == epoch.budget_id
    )

    usage = RunnerSessionUsageRecord(
        budget_id=epoch.budget_id,
        session_id=session.session_id,
        run_id=session.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        observed_at=session.start_intent_at,
        final=False,
    )
    first_usage = runtime.store.record_runner_session_usage(usage)
    replay_usage = runtime.store.record_runner_session_usage(usage)
    runtime.store.persist_runtime_state(durable, runtime.cas_store)
    assert runtime.store.load_daemon_budget_epoch(epoch.budget_id) == replay_usage
    assert (
        runtime.store.daemon_budget_id_for_session(session.session_id)
        == epoch.budget_id
    )
    final_evidence = replace(
        usage,
        input_tokens=12,
        output_tokens=6,
        total_tokens=18,
        final=True,
    )
    final_usage = runtime.store.record_runner_session_usage(final_evidence)
    assert first_usage.cumulative_total_tokens == 15
    assert replay_usage.cumulative_total_tokens == 15
    assert final_usage.cumulative_total_tokens == 18
    assert runtime.store.load_runner_session_usage(session.session_id) == final_evidence
    from millrace.adapters.cli.status import _budget_projection_by_session

    projection = _budget_projection_by_session(runtime, _load(runtime))[
        session.session_id
    ]
    assert projection["usage_evidence"] == {
        "status": "available",
        "input_tokens": 12,
        "output_tokens": 6,
        "total_tokens": 18,
        "observed_at": session.start_intent_at,
        "final": True,
    }
    with pytest.raises(ValueError, match="runner_usage_evidence_refused"):
        runtime.store.record_runner_session_usage(
            replace(
                usage,
                input_tokens=9,
                output_tokens=5,
                total_tokens=14,
            )
        )
    with pytest.raises(ValueError, match="runner_usage_evidence_refused"):
        runtime.store.record_runner_session_usage(
            replace(usage, session_fencing_token="wrong-fence")
        )
    runtime.close()


def test_concurrent_governed_usage_replay_is_atomic(tmp_path: Path) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.state import (
        DaemonBudgetEpochRecord,
        RunnerSessionUsageRecord,
    )
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    state, _fingerprint = _ready_state()
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
    assert session.start_intent_at is not None
    epoch = DaemonBudgetEpochRecord(
        budget_id="concurrent-governed-usage-replay",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=2,
        max_total_tokens=100,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    db_path = runtime.paths.db_path
    runtime.close()

    usage = RunnerSessionUsageRecord(
        budget_id=epoch.budget_id,
        session_id=session.session_id,
        run_id=session.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        observed_at=session.start_intent_at,
        final=True,
    )
    start_barrier = threading.Barrier(2)
    pre_transaction_read_barrier = threading.Barrier(2)
    results: list[tuple[int, int, int]] = []
    failures: list[BaseException] = []

    class CursorAfterPreTransactionPriorRead:
        def __init__(self, cursor: object, connection: object) -> None:
            self._cursor = cursor
            self._connection = connection

        def fetchone(self) -> object:
            row = self._cursor.fetchone()
            if not self._connection.in_transaction:
                pre_transaction_read_barrier.wait(timeout=5)
            return row

        def __getattr__(self, name: str) -> object:
            return getattr(self._cursor, name)

    class ConnectionSynchronizingPreTransactionUsageRead:
        def __init__(self, connection: object) -> None:
            self._connection = connection

        def execute(self, sql: str, parameters: object = ()) -> object:
            cursor = self._connection.execute(sql, parameters)
            if "FROM runner_session_usage WHERE session_id = ?" in " ".join(
                sql.split()
            ):
                return CursorAfterPreTransactionPriorRead(cursor, self._connection)
            return cursor

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

    def record() -> None:
        store = SQLiteRuntimeStore.open(db_path)
        store._connection = ConnectionSynchronizingPreTransactionUsageRead(  # type: ignore[assignment]
            store._connection
        )
        try:
            start_barrier.wait(timeout=5)
            recorded = store.record_runner_session_usage(usage)
            results.append(
                (
                    recorded.cumulative_input_tokens,
                    recorded.cumulative_output_tokens,
                    recorded.cumulative_total_tokens,
                )
            )
        except BaseException as exc:
            failures.append(exc)
        finally:
            store.close()

    threads = [threading.Thread(target=record) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()

    observer = SQLiteRuntimeStore.open(db_path)
    try:
        persisted_epoch = observer.load_daemon_budget_epoch(epoch.budget_id)
        persisted_usage = observer.load_runner_session_usage(session.session_id)
        binding = observer._connection.execute(
            """
            SELECT run_id, dispatch_generation, session_fencing_token
            FROM daemon_budget_sessions
            WHERE session_id = ? AND budget_id = ?
            """,
            (session.session_id, epoch.budget_id),
        ).fetchone()
    finally:
        observer.close()

    assert failures == []
    assert sorted(results) == [(10, 5, 15), (10, 5, 15)]
    assert persisted_epoch is not None
    assert (
        persisted_epoch.cumulative_input_tokens,
        persisted_epoch.cumulative_output_tokens,
        persisted_epoch.cumulative_total_tokens,
    ) == (10, 5, 15)
    assert persisted_usage == usage
    assert binding == (
        session.run_id,
        session.dispatch_generation,
        session.session_fencing_token,
    )


def test_budget_binding_refuses_raw_session_identity_corruption_without_mutation(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.contracts.state import DaemonBudgetEpochRecord

    state, _fingerprint = _ready_state()
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
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-corrupt-binding",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=None,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    connection = runtime.store._connection
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        "UPDATE daemon_budget_sessions SET run_id = ? WHERE session_id = ?",
        ("wrong-run", session.session_id),
    )
    connection.commit()
    connection.execute("PRAGMA foreign_keys = ON")
    before = runtime.paths.db_path.read_bytes()

    with pytest.raises(ValueError, match="runner_session_budget_identity_mismatch"):
        runtime.store.daemon_budget_id_for_session(session.session_id)

    assert runtime.paths.db_path.read_bytes() == before
    runtime.close()


def test_missing_governed_usage_on_lost_session_refuses_budget_and_suspends(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import run as run_module
    from millrace.adapters.cli import session_completion
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import (
        AdapterLocalConfig,
        DispatchEcho,
        StartIndeterminate,
    )
    from millrace.contracts.state import DaemonBudgetEpochRecord

    class IndeterminateAdapter:
        adapter_kind = "codex"

        def start_session(self, request: object) -> StartIndeterminate:
            assert isinstance(request, object)
            invocation = cast(Any, request)
            return StartIndeterminate(
                DispatchEcho.from_dispatch_envelope(
                    invocation.dispatch_envelope,
                    correlation_id=invocation.correlation_id,
                    selected_adapter_kind=invocation.selected_adapter_kind,
                ),
                {"provider_request_id": "missing-usage"},
                f"sha256:{'a' * 64}",
            )

        def reconcile_session(self, request: object) -> object:
            return request

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    config = AdapterLocalConfig(adapters={"codex": cast(Any, IndeterminateAdapter())})
    result = run_bounded_execution_unit(runtime, local_config=config)
    assert result.code == "session_reconciliation_required"
    assert result.run_id is not None
    durable = _load(runtime)
    run = durable.runs[result.run_id]
    assert run.current_session_id is not None
    session = durable.runner_sessions[run.current_session_id]
    assert session.start_intent_at is not None
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-missing-usage",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=1,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    active = run_module._active_run_for_run(durable, result.run_id)
    assert not isinstance(active, run_module.BoundedExecutionUnitResult)
    request = run_module._session_invocation_request(
        runtime,
        active=active,
        selected_kind="codex",
        effective_config=config,
        session=session,
    )

    outcome = session_completion._persist_orphan_risk(
        runtime,
        run_ref=run.run_ref,
        session=session,
        request=request,
        primary=None,
    )

    refused = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    assert outcome.code == "runner_usage_evidence_refused"
    assert refused is not None
    assert (refused.status, refused.terminal_reason) == (
        "refused",
        "runner_usage_evidence_refused",
    )
    assert _load(runtime).dispatch_suspension is not None
    runtime.close()


@pytest.mark.parametrize(
    "terminal_path",
    ("success", "failure", "cancellation", "timeout", "loss"),
)
@pytest.mark.parametrize(
    "usage_case",
    (
        "valid",
        "missing",
        "malformed",
        "decreasing",
        "final_conflicting",
        "runner_identity_mismatched",
        "fencing_token_mismatched",
    ),
)
def test_governed_usage_terminal_path_matrix(
    tmp_path: Path,
    terminal_path: str,
    usage_case: str,
) -> None:
    from millrace.adapters.cli import (
        session_cancellation,
        session_completion,
        session_coordinator,
        session_reconciliation,
    )
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import (
        AdapterErrorResult,
        AdapterTokenUsage,
    )
    from millrace.contracts.state import (
        DaemonBudgetEpochRecord,
        RunnerSessionUsageRecord,
    )
    from support.runner_sessions import (
        _config,
        _dispatch_echo,
        _indeterminate_start,
        _ready_state_with_two_activations,
        _RecordingAdapter,
        _success_outcome,
    )

    state, _fingerprint = _ready_state_with_two_activations()
    runtime = _runtime(tmp_path, state)
    adapter = _RecordingAdapter(_indeterminate_start)
    config = _config(adapter)
    started = run_bounded_execution_unit(runtime, local_config=config)
    assert started.code == "session_reconciliation_required"
    assert started.run_id is not None
    durable = _load(runtime)
    run = durable.runs[started.run_id]
    assert run.current_session_id is not None
    session = durable.runner_sessions[run.current_session_id]
    assert session.start_intent_at is not None
    request = adapter.requests[0]
    epoch = DaemonBudgetEpochRecord(
        budget_id=f"budget-{terminal_path}-{usage_case}",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=2,
        max_total_tokens=100,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    runtime.store.record_budgeted_runner_start(epoch.budget_id, session)

    prior: RunnerSessionUsageRecord | None = None
    if usage_case in {
        "valid",
        "decreasing",
        "final_conflicting",
        "runner_identity_mismatched",
        "fencing_token_mismatched",
    }:
        prior = RunnerSessionUsageRecord(
            budget_id=epoch.budget_id,
            session_id=session.session_id,
            run_id=session.run_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            input_tokens=10 if usage_case in {"decreasing", "final_conflicting"} else 3,
            output_tokens=5 if usage_case in {"decreasing", "final_conflicting"} else 2,
            total_tokens=15 if usage_case in {"decreasing", "final_conflicting"} else 5,
            observed_at=session.start_intent_at,
            final=usage_case == "final_conflicting",
        )
        runtime.store.record_runner_session_usage(prior)
    if usage_case in {"runner_identity_mismatched", "fencing_token_mismatched"}:
        column = (
            "run_id"
            if usage_case == "runner_identity_mismatched"
            else "session_fencing_token"
        )
        runtime.store._connection.execute("PRAGMA foreign_keys = OFF")
        runtime.store._connection.execute(
            f"UPDATE runner_session_usage SET {column} = ? WHERE session_id = ?",
            (
                "wrong-run"
                if usage_case == "runner_identity_mismatched"
                else "wrong-fence",
                session.session_id,
            ),
        )
        runtime.store._connection.commit()
        runtime.store._connection.execute("PRAGMA foreign_keys = ON")

    usage = (
        None
        if usage_case in {"missing", "malformed"}
        else AdapterTokenUsage(
            input_tokens=(
                9
                if usage_case == "decreasing"
                else 12
                if usage_case == "final_conflicting"
                else 10
            ),
            output_tokens=(
                4
                if usage_case == "decreasing"
                else 6
                if usage_case == "final_conflicting"
                else 5
            ),
            total_tokens=(
                13
                if usage_case == "decreasing"
                else 18
                if usage_case == "final_conflicting"
                else 15
            ),
        )
    )
    if usage_case == "malformed":
        outcome = AdapterErrorResult.from_unredacted(
            adapter_id=request.adapter_id,
            error_kind="result_parse_failed",
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
        )
    elif terminal_path == "success":
        outcome = replace(_success_outcome(request), token_usage=usage)
    else:
        outcome = AdapterErrorResult.from_unredacted(
            adapter_id=request.adapter_id,
            error_kind={
                "failure": "invocation_failed",
                "cancellation": "cancelled",
                "timeout": "timeout",
                "loss": "invocation_failed",
            }[terminal_path],
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            token_usage=usage,
        )

    if terminal_path == "loss":
        result = session_completion._persist_orphan_risk(
            runtime,
            run_ref=run.run_ref,
            session=session,
            request=request,
            primary=None,
            outcome=outcome,
        )
    else:
        running = session_reconciliation._advance_reconciled_starting_session(
            runtime,
            run_ref=run.run_ref,
            session=session,
            locator_digest=session.durable_locator_digest,
        )
        assert running is not None
        primary = None
        if terminal_path == "cancellation":
            cancellation = session_coordinator.request_operator_cancellation(
                runtime,
                run_id=started.run_id,
                request_id=f"cancel-{usage_case}",
                actor_id="test-operator",
            )
            assert cancellation.accepted
            running = _load(runtime).runner_sessions[session.session_id]
            primary = session_cancellation._primary_cancellation(
                _load(runtime),
                running,
            )
            assert primary is not None
        result = session_completion._persist_completion(
            runtime,
            run_ref=run.run_ref,
            session=running,
            request=request,
            outcome=outcome,
            cleanup=session_cancellation._terminal_cleanup_result(None, "complete"),
            primary=primary,
            adapter_error_terminal_state=(
                "interrupted" if terminal_path == "cancellation" else "failed"
            ),
        )

    persisted_epoch = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    assert persisted_epoch is not None
    if usage_case == "valid":
        assert result.code != "runner_usage_evidence_refused"
        assert (
            persisted_epoch.cumulative_input_tokens,
            persisted_epoch.cumulative_output_tokens,
            persisted_epoch.cumulative_total_tokens,
        ) == (10, 5, 15)
        final_usage = runtime.store.load_runner_session_usage(session.session_id)
        assert final_usage is not None
        assert final_usage.final
        replay = runtime.store.record_runner_session_usage(final_usage)
        assert replay.cumulative_total_tokens == 15
        assert _load(runtime).dispatch_suspension is None
    else:
        assert result.code == "runner_usage_evidence_refused"
        assert (persisted_epoch.status, persisted_epoch.terminal_reason) == (
            "refused",
            "runner_usage_evidence_refused",
        )
        assert _load(runtime).dispatch_suspension is not None
        post_refusal_adapter = _RecordingAdapter(
            lambda _request: pytest.fail("post-refusal adapter invocation")
        )
        before_post_refusal = _load(runtime)
        post_refusal = run_bounded_execution_unit(
            runtime,
            local_config=_config(post_refusal_adapter),
        )
        after_post_refusal = _load(runtime)
        assert post_refusal.code in {"dispatch_suspended", "ready_state_refused"}
        assert post_refusal_adapter.requests == []
        assert after_post_refusal.runner_sessions == before_post_refusal.runner_sessions
        assert after_post_refusal.runs == before_post_refusal.runs
    runtime.close()


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
    db_after_corruption = paths.db_path.read_bytes()
    cas_after_corruption = {
        path.relative_to(paths.cas_path): path.read_bytes()
        for path in paths.cas_path.rglob("*")
        if path.is_file()
    }

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
    assert paths.db_path.read_bytes() == db_after_corruption
    assert {
        path.relative_to(paths.cas_path): path.read_bytes()
        for path in paths.cas_path.rglob("*")
        if path.is_file()
    } == cas_after_corruption

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
        (_codex_mismatch_config(), "adapter_failure"),
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


def test_daemon_summary_keeps_handled_session_after_final_idle_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.run import BoundedExecutionUnitResult

    state, _fingerprint = _ready_state()
    runtime = _runtime(tmp_path, state)
    handled = daemon.run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(marker="AUTO"),
    )
    paths = runtime.paths
    _close(runtime)
    results = iter(
        (
            BoundedExecutionUnitResult(
                code="observation_accepted",
                accepted=True,
                run_id=handled.run_id,
            ),
            BoundedExecutionUnitResult(code="no_ready_work"),
        )
    )
    monkeypatch.setattr(
        daemon,
        "_run_one_bounded_unit",
        lambda *_args, **_kwargs: next(results),
    )

    summary = daemon.run_daemon_loop(
        _daemon_options(
            paths,
            max_ticks=2,
            local_config=_codex_success_config(marker="AUTO"),
        )
    )

    assert summary.last_result["code"] == "no_ready_work"
    assert summary.last_result["run_id"] is None
    assert summary.runner_session is not None
    assert summary.runner_session["run_id"] == handled.run_id
    assert summary.runner_session["application_status"] == "applied"


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
