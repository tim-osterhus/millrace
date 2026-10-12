from __future__ import annotations

import json
import sys
from dataclasses import fields, replace
from pathlib import Path
from typing import cast

import pytest
from tests.cli.test_cli_bounded_execution_unit import (
    _load,
    _reopen_runtime,
    _runtime,
)
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_parent_claimed_state,
)

from millrace.adapters.cli import session_completion
from millrace.adapters.cli.run import (
    reconcile_pending_runner_completions,
    reconcile_pending_runner_sessions,
    run_bounded_execution_unit,
)
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterSuccessResult,
)
from millrace.contracts.compiled_plan import (
    RunnerBindingWithRuntimeFailuresDeclaration,
    RunnerRuntimeFailureMapping,
    authority_fingerprint,
)
from millrace.contracts.ids import ActionId, StageKindId
from millrace.contracts.runner import (
    runner_session_completion_diagnostic_bytes,
    runner_session_completion_diagnostic_from_payload,
)
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_diagnostic_matches,
    runtime_failure_eligible,
)
from millrace.contracts.state import RunExecutionControl
from millrace.kernel.lookups import run_is_settled
from support import generic_admission, generic_fanout
from support.runner_sessions import (
    _dispatch_echo,
    _error_outcome,
    _ImmediateHandle,
    _RecordingAdapter,
    _started_session,
)


def _mapped_generic_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mapped: bool = True,
):
    plan, _fingerprint = generic_admission.compile_plan()
    if mapped:
        bindings = tuple(
            RunnerBindingWithRuntimeFailuresDeclaration(
                **{item.name: getattr(binding, item.name) for item in fields(binding)},
                runtime_failure_mappings=(
                    RunnerRuntimeFailureMapping(
                        StageKindId(generic_admission.PARENT_STAGE_ID),
                        "timeout",
                        ActionId(generic_admission.RECOVERY_SOURCE_ACTION_ID),
                    ),
                ),
            )
            for binding in plan.runner_bindings
        )
        plan = replace(plan, runner_bindings=bindings)
    fingerprint = authority_fingerprint(plan)
    monkeypatch.setattr(
        generic_admission,
        "compile_plan",
        lambda: (plan, fingerprint),
    )
    state, _selected_plan, _selected_fingerprint = _generic_parent_claimed_state()
    return state


def _timeout_config() -> tuple[AdapterLocalConfig, _RecordingAdapter]:
    def start(request: AdapterInvocationRequest):
        outcome = _error_outcome(
            request,
            dispatch_echo=_dispatch_echo(request),
            error_kind="timeout",
        )
        return _started_session(
            request,
            _ImmediateHandle(outcome, cleanup_disposition="complete"),
        )

    adapter = _RecordingAdapter(start)
    return AdapterLocalConfig(adapters={"codex": adapter}), adapter


def _recovery_success_config() -> tuple[AdapterLocalConfig, _RecordingAdapter]:
    def start(request: AdapterInvocationRequest):
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            marker="ADMISSION_RECOVERED",
            observation_payload_candidate={"summary": "recovered"},
            artifact_payload_candidate=generic_fanout.packet_payload(),
        )
        return _started_session(
            request,
            _ImmediateHandle(outcome, cleanup_disposition="complete"),
        )

    adapter = _RecordingAdapter(start)
    return AdapterLocalConfig(adapters={"codex": adapter}), adapter


def _persist_timeout_without_applying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    state = _mapped_generic_state(monkeypatch)
    runtime = _runtime(tmp_path, state)
    timeout_config, adapter = _timeout_config()
    original_apply = session_completion._apply_persisted_completion
    monkeypatch.setattr(
        session_completion,
        "_apply_persisted_completion",
        lambda _runtime, completion, **_kwargs: (
            session_completion.SessionExecutionResult(
                "adapter_failure",
                adapter_error_kind=completion.adapter_error_kind,
            )
        ),
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=timeout_config,
    )
    monkeypatch.setattr(
        session_completion,
        "_apply_persisted_completion",
        original_apply,
    )
    assert result.code == "adapter_failure"
    after = _load(runtime)
    session_id = after.runs[cast(str, result.run_id)].current_session_id
    assert session_id is not None
    completion = after.runner_session_completions[session_id]
    assert completion.terminal_state == "failed"
    assert completion.adapter_error_kind == "timeout"
    assert completion.runner_result_evidence_digest is None
    assert len(adapter.requests) == 1
    from millrace.adapters.cli.projections import session_projection
    from millrace.adapters.cli.status import rejected_result_projection

    session = after.runner_sessions[session_id]
    status = session_projection(runtime, after, session)
    assert status["application_input_id"] == completion.application_input_id
    assert status["application_status"] == "pending"
    rejected = rejected_result_projection(runtime, after, result.run_id)
    assert rejected is not None
    assert rejected["application_input_id"] == completion.application_input_id
    assert rejected["application_status"] == "pending"
    return runtime, result, completion


def test_clean_selected_timeout_applies_recovery_without_runner_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.status import (
        rejected_result_projection,
        runner_session_projection,
    )

    state = _mapped_generic_state(monkeypatch)
    runtime = _runtime(tmp_path, state)
    timeout_config, timeout_adapter = _timeout_config()
    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=timeout_config,
    )

    assert result.code == "runtime_failure_recovery_applied"
    assert result.accepted is True
    assert result.adapter_error_kind == "timeout"
    after = _load(runtime)
    run_id = cast(str, result.run_id)
    run = after.runs[run_id]
    session_id = run.current_session_id
    assert session_id is not None
    completion = after.runner_session_completions[session_id]
    assert completion.terminal_state == "failed"
    assert completion.adapter_error_kind == "timeout"
    assert completion.runner_result_evidence_digest is None
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert runtime_failure_applied(after, run_id)
    assert run_is_settled(after, run_id)
    projected = runner_session_projection(after, run_id)
    assert projected is not None
    assert projected["application_status"] == "applied"
    from millrace.adapters.cli.projections import session_projection

    public_projection = session_projection(
        runtime,
        after,
        after.runner_sessions[session_id],
    )
    assert public_projection["application_input_id"] == completion.application_input_id
    assert public_projection["application_status"] == "applied"
    assert rejected_result_projection(runtime, after, run_id) is None
    assert len(after.recovery_attempts) == 1
    assert len(timeout_adapter.requests) == 1

    # Reopening and explicitly revisiting the failed activation must replay the
    # authenticated runtime-failure receipt without starting another session.
    reopened = _reopen_runtime(runtime)
    replay = run_bounded_execution_unit(
        reopened,
        activation_id=run.activation_id,
        local_config=_recovery_success_config()[0],
    )
    assert replay.code == "runtime_failure_recovery_applied"
    assert replay.transition_disposition == "replayed"
    assert _load(reopened).runner_observations == {}
    paths = reopened.paths
    reopened.close()

    # The daemon's one allowed unit executes the selected recovery activation.
    from types import SimpleNamespace

    from tests.cli.test_cli_daemon_loop import _invoke

    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.daemon import DaemonRunOptions

    recovery_config, recovery_adapter = _recovery_success_config()
    options = DaemonRunOptions(
        paths=paths,
        idle_sleep_seconds=0,
        max_ticks=1,
        activation_id=None,
        adapter_kind=None,
        local_config=recovery_config,
        monitor="none",
        actor_id="local_operator",
    )
    monkeypatch.setattr(daemon, "daemon_options_from_namespace", lambda _ns: options)
    monkeypatch.setattr(
        daemon,
        "sys",
        SimpleNamespace(platform="linux", stdout=sys.stdout),
    )
    code, stdout, stderr = _invoke(
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
    assert code == 0, (stdout, stderr)
    response = json.loads(stdout)
    assert response["ok"] is True
    assert response["data"]["stopped_reason"] == "max_ticks"
    assert response["data"]["last_result"]["code"] == "observation_accepted"
    assert len(recovery_adapter.requests) == 1
    after_daemon = daemon.open_runtime_context(paths, command="test")
    try:
        completed = _load(after_daemon)
        assert len(completed.runner_observations) == 1
        assert len(completed.recovery_attempts) == 1
    finally:
        after_daemon.close()


def test_public_daemon_routes_timeout_then_runs_selected_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from tests.cli.test_cli_daemon_loop import _invoke

    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.daemon import DaemonRunOptions

    state = _mapped_generic_state(monkeypatch)
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()

    timeout_config, timeout_adapter = _timeout_config()
    options = DaemonRunOptions(
        paths=paths,
        idle_sleep_seconds=0,
        max_ticks=1,
        activation_id="activation-generic-parent",
        adapter_kind=None,
        local_config=timeout_config,
        monitor="none",
        actor_id="local_operator",
    )
    monkeypatch.setattr(daemon, "daemon_options_from_namespace", lambda _ns: options)
    # Exercise the public CLI command path without requiring the macOS-only
    # listener socket; run_daemon_loop and main still execute directly.
    monkeypatch.setattr(
        daemon,
        "sys",
        SimpleNamespace(platform="linux", stdout=sys.stdout),
    )
    code, stdout, stderr = _invoke(
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

    assert code == 0, (stdout, stderr)
    response = json.loads(stdout)
    assert response["ok"] is True
    first_daemon = response["data"]
    assert first_daemon["stopped_reason"] == "max_ticks"
    assert first_daemon["last_result"]["code"] == "runtime_failure_recovery_applied"
    assert first_daemon["last_result"]["accepted"] is True
    assert first_daemon["units_succeeded"] == 0
    assert first_daemon["adapter_failures"] == 0
    assert len(timeout_adapter.requests) == 1

    after_failure = daemon.open_runtime_context(paths, command="test")
    try:
        failed_state = _load(after_failure)
        failed_run_id = first_daemon["last_result"]["run_id"]
        failed_run = failed_state.runs[failed_run_id]
        failed_session_id = failed_run.current_session_id
        assert failed_session_id is not None
        failed_completion = failed_state.runner_session_completions[failed_session_id]
        assert failed_state.runner_sessions[failed_session_id].state == "failed"
        assert failed_completion.adapter_error_kind == "timeout"
        assert failed_completion.runner_result_evidence_digest is None
        assert failed_state.runner_observations == {}
        assert failed_state.artifacts == {}
        assert runtime_failure_applied(failed_state, failed_run_id)
        assert len(failed_state.recovery_attempts) == 1
    finally:
        after_failure.close()

    recovery_config, recovery_adapter = _recovery_success_config()
    options = replace(
        options,
        activation_id=None,
        local_config=recovery_config,
    )
    code, stdout, stderr = _invoke(
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

    assert code == 0, (stdout, stderr)
    response = json.loads(stdout)
    assert response["ok"] is True
    recovery_daemon = response["data"]
    assert recovery_daemon["stopped_reason"] == "max_ticks"
    assert recovery_daemon["last_result"]["code"] == "observation_accepted"
    assert recovery_daemon["last_result"]["accepted"] is True
    assert len(recovery_adapter.requests) == 1
    after_recovery = daemon.open_runtime_context(paths, command="test")
    try:
        completed = _load(after_recovery)
        assert len(completed.runner_observations) == 1
        observation = next(iter(completed.runner_observations.values()))
        assert observation.run_id == recovery_daemon["last_result"]["run_id"]
        assert observation.run_id != failed_run_id
        assert completed.runner_sessions[failed_session_id].state == "failed"
        assert (
            completed.runner_session_completions[failed_session_id]
            == failed_completion
        )
        assert len(completed.recovery_attempts) == 1
    finally:
        after_recovery.close()


@pytest.mark.parametrize(
    "reconcile",
    (reconcile_pending_runner_sessions, reconcile_pending_runner_completions),
    ids=("startup-sessions", "before-dispatch-completions"),
)
def test_pending_timeout_completion_is_applied_during_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reconcile,
) -> None:
    runtime, first, completion = _persist_timeout_without_applying(
        tmp_path,
        monkeypatch,
    )
    reopened = _reopen_runtime(runtime)

    success_config, success_adapter = _recovery_success_config()
    result = reconcile(
        reopened,
        local_config=success_config,
    )

    assert result.code == "runtime_failure_recovery_applied"
    after = _load(reopened)
    assert runtime_failure_applied(after, cast(str, first.run_id))
    assert after.runner_session_completions[completion.session_id] == completion
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert success_adapter.requests == []
    reopened.close()


def test_daemon_drain_defers_new_timeout_recovery_until_next_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, first, completion = _persist_timeout_without_applying(
        tmp_path,
        monkeypatch,
    )
    state = _load(runtime)

    deferred = session_completion._apply_persisted_completion(
        runtime,
        completion,
        daemon_stop_requested=lambda: True,
    )

    after_drain = _load(runtime)
    assert deferred.code == "runner_session_waiting"
    assert deferred.accepted is False
    assert completion.application_input_id not in after_drain.receipts
    assert after_drain.activation_routes == state.activation_routes
    applied = session_completion._apply_persisted_completion(
        runtime,
        completion,
        daemon_stop_requested=lambda: False,
    )
    assert applied.code == "runtime_failure_recovery_applied"
    assert runtime_failure_applied(_load(runtime), cast(str, first.run_id))
    runtime.close()


def test_mapped_timeout_respects_operator_run_hold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _first, completion = _persist_timeout_without_applying(
        tmp_path,
        monkeypatch,
    )
    state = _load(runtime)
    run = state.runs[completion.run_id]
    control = RunExecutionControl(
        run_id=run.run_ref.run_id,
        control_revision=1,
        pause_id="b035ee7a-f437-45c1-a9fc-9c26d5396acf",
        state="paused",
        cause="run_paused",
        operation_key=(
            "0105c6d4-05b4-4dc9-b87f-2933351c85f9",
            "69edb6a5-c463-4d7b-a50e-e12f1c52f420",
            "d07e109a-f212-4fd8-9893-e6deee7f237b",
            "operator",
            "pause-fixture",
        ),
        source_revision=0,
        recorded_at="2026-10-08T00:00:00Z",
    )
    held = replace(
        state,
        run_execution_controls={run.run_ref.run_id: control},
    )
    monkeypatch.setattr(
        runtime.store,
        "persist_runtime_state",
        lambda *_args: None,
    )

    result = session_completion._apply_persisted_runtime_failure(
        runtime,
        held,
        completion,
        daemon_stop_requested=None,
    )

    assert result.code == "observation_refused"
    assert result.observation_refusal_reason == "run_execution_held"
    assert completion.application_input_id not in held.receipts
    assert held.activation_routes == ()
    assert held.recovery_attempts == {}
    runtime.close()


def test_mapped_timeout_excludes_daemon_budget_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.cli.test_cli_daemon_session import _SignalWaitAdapter

    from millrace.adapters.cli import daemon
    from millrace.adapters.cli.daemon import DaemonRunOptions

    state = _mapped_generic_state(monkeypatch)
    runtime = _runtime(tmp_path, state)
    paths = runtime.paths
    runtime.close()
    clock_state = {"started": False}

    class BudgetWaitAdapter(_SignalWaitAdapter):
        def start_session(self, request):
            clock_state["started"] = True
            return super().start_session(request)

    def wall_clock() -> float:
        return 101.0 if clock_state["started"] else 100.0

    options = DaemonRunOptions(
        paths=paths,
        idle_sleep_seconds=0,
        max_ticks=1,
        activation_id="activation-generic-parent",
        adapter_kind=None,
        local_config=AdapterLocalConfig(
            adapters={"codex": BudgetWaitAdapter()}
        ),
        monitor="none",
        actor_id="local_operator",
        budget_id="budget-cancelled-mapped-timeout",
        max_wall_seconds=1,
        max_invocations=2,
    )

    summary = daemon.run_daemon_loop(options, wall_clock=wall_clock)
    after_runtime = daemon.open_runtime_context(paths, command="test")
    try:
        after = _load(after_runtime)
    finally:
        after_runtime.close()

    assert summary.stopped_reason == "budget_exhausted"
    assert summary.last_result["code"] == "adapter_failure"
    assert summary.units_succeeded == 0
    assert len(after.runner_session_cancellation_requests) == 1
    cancellation = next(iter(after.runner_session_cancellation_requests.values()))
    assert (cancellation.reason, cancellation.source_kind) == (
        "daemon_shutdown",
        "daemon",
    )
    session_id = cancellation.session_id
    completion = after.runner_session_completions[session_id]
    assert after.runner_sessions[session_id].state == "interrupted"
    assert completion.exit_kind == "cancelled"
    assert not runtime_failure_eligible(after, completion)
    assert completion.application_input_id not in after.receipts
    assert after.activation_routes == ()
    assert after.recovery_attempts == {}


def test_mapped_timeout_excludes_runtime_deadline_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.cli.test_cli_daemon_session import _SignalWaitAdapter

    from millrace.adapters.cli import session_coordinator

    state = _mapped_generic_state(monkeypatch)
    runtime = _runtime(tmp_path, state)
    monotonic_values = iter((0.0, 10_000.0))
    monkeypatch.setattr(
        session_coordinator,
        "_monotonic",
        lambda: next(monotonic_values),
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=AdapterLocalConfig(
            adapters={"codex": _SignalWaitAdapter()}
        ),
    )
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "cancelled"
    session_id = after.runs[cast(str, result.run_id)].current_session_id
    assert session_id is not None
    completion = after.runner_session_completions[session_id]
    cancellation = next(iter(after.runner_session_cancellation_requests.values()))
    assert (cancellation.reason, cancellation.source_kind) == (
        "runner_timeout",
        "runtime",
    )
    assert after.runner_sessions[session_id].state == "interrupted"
    assert not runtime_failure_eligible(after, completion)
    assert completion.application_input_id not in after.receipts
    assert after.activation_routes == ()
    assert after.runner_observations == {}
    assert after.recovery_attempts == {}
    runtime.close()


def test_missing_timeout_diagnostic_refuses_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, first, completion = _persist_timeout_without_applying(
        tmp_path,
        monkeypatch,
    )
    state = _load(runtime)
    runtime.cas_store._object_path(completion.diagnostic_digest).unlink()

    result = session_completion._apply_persisted_runtime_failure(
        runtime,
        state,
        completion,
        daemon_stop_requested=None,
    )

    assert result.code == "completion_refused"
    assert completion.application_input_id not in state.receipts
    assert state.activation_routes == ()
    assert not runtime_failure_applied(state, cast(str, first.run_id))
    runtime.close()


@pytest.mark.parametrize("already_applied", (False, True), ids=("pending", "replay"))
def test_foreign_timeout_diagnostic_envelope_refuses_recovery_and_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    already_applied: bool,
) -> None:
    runtime, first, completion = _persist_timeout_without_applying(
        tmp_path,
        monkeypatch,
    )
    state = _load(runtime)
    if already_applied:
        applied = session_completion._apply_persisted_runtime_failure(
            runtime,
            state,
            completion,
            daemon_stop_requested=None,
        )
        assert applied.code == "runtime_failure_recovery_applied"
        state = _load(runtime)

    original_payload = runtime.cas_store.get_bytes(completion.diagnostic_digest)
    original_diagnostic = runner_session_completion_diagnostic_from_payload(
        json.loads(original_payload)
    )
    foreign_diagnostic = replace(
        original_diagnostic,
        session_id="foreign-session",
    )
    foreign_digest = runtime.cas_store.put_bytes(
        runner_session_completion_diagnostic_bytes(foreign_diagnostic)
    )
    foreign_completion = replace(completion, diagnostic_digest=foreign_digest)
    completions = dict(state.runner_session_completions)
    completions[completion.session_id] = foreign_completion
    foreign_state = replace(state, runner_session_completions=completions)
    assert not runtime_failure_diagnostic_matches(
        foreign_state,
        foreign_completion,
        runtime.cas_store.get_bytes(foreign_digest),
    )

    result = session_completion._apply_persisted_runtime_failure(
        runtime,
        foreign_state,
        foreign_completion,
        daemon_stop_requested=None,
    )

    assert result.code == "completion_refused"
    if already_applied:
        assert runtime_failure_applied(state, cast(str, first.run_id))
        assert foreign_completion.application_input_id in state.receipts
        assert foreign_state.activation_routes == state.activation_routes
    else:
        assert foreign_completion.application_input_id not in foreign_state.receipts
        assert foreign_state.activation_routes == ()
    runtime.close()


def test_timeout_without_selected_mapping_keeps_adapter_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _mapped_generic_state(monkeypatch, mapped=False)
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=_timeout_config()[0],
    )

    after = _load(runtime)
    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "timeout"
    session_id = after.runs["run-generic-parent"].current_session_id
    assert session_id is not None
    completion = after.runner_session_completions[session_id]
    assert completion.application_input_id not in after.receipts
    from millrace.adapters.cli.status import (
        rejected_result_projection,
        runner_session_projection,
    )

    projected = runner_session_projection(after, "run-generic-parent")
    assert projected is not None
    assert projected["application_status"] == "not_applicable"
    rejected = rejected_result_projection(runtime, after, "run-generic-parent")
    assert rejected is not None
    assert rejected["application_status"] == "not_applicable"
    assert after.activation_routes == ()
    assert after.runner_observations == {}
    runtime.close()
