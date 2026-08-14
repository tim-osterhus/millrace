from __future__ import annotations

import json
import os
import signal
import sqlite3
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from adapters.test_context_writeback import (
    _bound_fixture,
    _writeback_report,
    _writeback_success_start,
)
from cli.test_cli_bounded_execution_unit import (
    _load,
    _ready_state_with_selected_codex_authority,
    _reopen_runtime,
    _runtime,
)
from millrace.adapters.cli import (
    session_cancellation,
    session_completion,
    session_coordinator,
    session_reconciliation,
)
from millrace.adapters.cli.run import (
    reconcile_pending_runner_sessions,
    run_bounded_execution_unit,
)
from millrace.adapters.runner_contract import (
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    CleanupPending,
    Contradiction,
    RedactionPolicy,
    RunnerCleanupResult,
    StartedSession,
    StartIndeterminate,
    Terminal,
    Unsupported,
    VerifiedLive,
)
from millrace.contracts.runner import (
    runner_session_locator_bytes,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    RunnerResultObserved,
)
from support.runner_sessions import (
    _CleanupTrackingHandle,
    _config,
    _dispatch_echo,
    _error_outcome,
    _ImmediateHandle,
    _indeterminate_start,
    _ready_runtime,
    _ready_state_with_two_activations,
    _RecordingAdapter,
    _replace_running_locator_with_legacy_bare,
    _SequenceHandle,
    _success_outcome,
    _success_start,
)


def _ready_codex_runtime(tmp_path):
    state, _ = _ready_state_with_selected_codex_authority()
    return _runtime(tmp_path, state)


def test_restart_reconciliation_refuses_mutated_completed_writeback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    before = _load(runtime)

    def start(request: AdapterInvocationRequest) -> object:
        return _writeback_success_start(
            request,
            artifact=_writeback_report(no_op_reason="No update."),
        )

    adapter = _RecordingAdapter(start)
    adapter.config = SimpleNamespace(
        cwd=runtime.paths.workspace_path,
        wrapper_protocol_version=4,
    )
    original_decide = session_completion.decide

    def crash_before_application(current, transition_input, context):
        if isinstance(transition_input, RunnerResultObserved):
            raise RuntimeError("application crash")
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_completion, "decide", crash_before_application)
    with pytest.raises(RuntimeError, match="application crash"):
        run_bounded_execution_unit(
            runtime,
            activation_id=state.runs[session.run_id].activation_id,
            local_config=_config(adapter),
        )

    persisted = _load(runtime)
    assert len(persisted.runner_session_completions) == (
        len(before.runner_session_completions) + 1
    )
    assert persisted.runner_observations == before.runner_observations
    mutated = runtime.paths.workspace_path / "src" / "unreported.txt"
    mutated.write_text("unreported\n", encoding="utf-8")

    monkeypatch.setattr(session_completion, "decide", original_decide)
    runtime = _reopen_runtime(runtime)
    replay = reconcile_pending_runner_sessions(
        runtime,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert replay.code == "completion_refused"
    assert after.runner_observations == before.runner_observations
    assert after.artifacts == persisted.artifacts
    assert after.closed_work_items == persisted.closed_work_items


@pytest.mark.parametrize(
    "active_activation_id",
    ("activation-1", "activation-taskmaster"),
)
def test_startup_reconciles_active_blocker_before_resuming_created_session(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    active_activation_id: str,
) -> None:
    created_activation_id = (
        "activation-taskmaster"
        if active_activation_id == "activation-1"
        else "activation-1"
    )
    created_starts: list[str] = []

    def start(request: AdapterInvocationRequest):
        if request.dispatch_envelope.activation_id == active_activation_id:
            return _indeterminate_start(request)
        created_starts.append(request.dispatch_envelope.activation_id)
        return _success_start(request)

    def reconcile(request):
        invocation = request.invocation_request
        return Contradiction(
            _dispatch_echo(invocation),
            "sha256:" + "c" * 64,
        )

    adapter = _RecordingAdapter(start, reconcile)
    state, _ = _ready_state_with_two_activations()
    runtime = _runtime(tmp_path, state)
    run_bounded_execution_unit(
        runtime,
        activation_id=active_activation_id,
        local_config=_config(adapter),
    )
    original = session_completion._persist_transition

    def crash_before_created_start(current_runtime, transition):
        if (
            getattr(transition, "expected_state", None) == "created"
            and getattr(transition, "next_state", None) == "starting"
        ):
            raise RuntimeError("crash before created start")
        return original(current_runtime, transition)

    monkeypatch.setattr(
        session_completion,
        "_persist_transition",
        crash_before_created_start,
    )
    with pytest.raises(RuntimeError, match="created start"):
        run_bounded_execution_unit(
            runtime,
            activation_id=created_activation_id,
            local_config=_config(adapter),
        )
    monkeypatch.setattr(session_completion, "_persist_transition", original)
    runtime = _reopen_runtime(runtime)

    result = reconcile_pending_runner_sessions(
        runtime,
        local_config=_config(adapter),
    )

    assert result.code == "runner_session_reconciliation_contradiction"
    assert created_starts == []
    assert len(adapter.reconcile_requests) == 1
    after = _load(runtime)
    created_session = next(
        session
        for session in after.runner_sessions.values()
        if after.runs[session.run_id].activation_id == created_activation_id
    )
    assert created_session.state == "created"


def test_startup_reports_contradiction_over_orphan_risk_after_classifying_all(
    tmp_path,
) -> None:
    def reconcile(request):
        invocation = request.invocation_request
        echo = _dispatch_echo(invocation)
        if invocation.dispatch_envelope.activation_id == "activation-1":
            return Unsupported(echo)
        return Contradiction(echo, "sha256:" + "c" * 64)

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state_with_two_activations()
    runtime = _runtime(tmp_path, state)
    for activation_id in ("activation-1", "activation-taskmaster"):
        run_bounded_execution_unit(
            runtime,
            activation_id=activation_id,
            local_config=_config(adapter),
        )
    runtime = _reopen_runtime(runtime)

    result = reconcile_pending_runner_sessions(
        runtime,
        local_config=_config(adapter),
    )

    assert result.code == "runner_session_reconciliation_contradiction"
    assert len(adapter.reconcile_requests) == 2


def test_restart_unsupported_marks_potentially_started_session_orphan_risk(
    tmp_path,
) -> None:
    adapter = _RecordingAdapter(_indeterminate_start)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    before = _load(runtime)

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert restarted.code == "runner_session_orphan_risk"
    assert len(adapter.requests) == 1
    assert len(adapter.reconcile_requests) == 1
    reconcile = adapter.reconcile_requests[0]
    invocation = reconcile.invocation_request
    session = next(iter(after.runner_sessions.values()))
    run = after.runs[session.run_id]
    assert invocation.dispatch_envelope.schema_version == 7
    assert invocation.dispatch_envelope.run_id == run.run_ref.run_id
    assert invocation.dispatch_envelope.claim_id == run.run_ref.claim_id
    assert invocation.dispatch_envelope.plan_fingerprint == (
        run.run_ref.plan_ref.authority_fingerprint
    )
    assert invocation.session_id == session.session_id
    assert invocation.dispatch_generation == session.dispatch_generation
    assert invocation.session_fencing_token == session.session_fencing_token
    assert (session.state, session.cleanup_disposition) == ("lost", "orphan_risk")
    assert run.run_ref.claim_id == before.runs[session.run_id].run_ref.claim_id
    assert run.current_session_id == session.session_id
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.quarantines == before.quarantines
    assert after.recovery_attempts == before.recovery_attempts
    assert after.runs.keys() == before.runs.keys()


def test_restart_verified_live_continues_observation_with_returned_handle(
    tmp_path,
) -> None:
    def reconcile(request):
        invocation = request.invocation_request
        echo = _dispatch_echo(invocation)
        return VerifiedLive(
            echo,
            _ImmediateHandle(_success_outcome(invocation)),
            "verified-owned-handle",
            request.durable_locator_metadata,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert restarted.code == "observation_accepted"
    assert len(adapter.requests) == 1
    assert len(adapter.reconcile_requests) == 1
    assert next(iter(after.runner_sessions.values())).state == "completed"
    assert len(after.runner_observations) == 1


def test_restart_verified_live_accepts_legacy_bare_locator_without_handle_proof(
    tmp_path,
) -> None:
    def reconcile(request):
        invocation = request.invocation_request
        assert request.durable_locator_metadata == {
            "provider_request_id": "legacy-request"
        }
        echo = _dispatch_echo(invocation)
        return VerifiedLive(
            echo,
            _ImmediateHandle(_success_outcome(invocation)),
            "verified-upgraded-handle",
            request.durable_locator_metadata,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    legacy_digest = runtime.cas_store.put_bytes(
        runner_session_locator_bytes({"provider_request_id": "legacy-request"})
    )
    with sqlite3.connect(runtime.paths.db_path) as connection:
        connection.execute(
            """
            UPDATE runner_sessions
            SET durable_locator_digest = ?
            WHERE session_id = ?
            """,
            (legacy_digest, session.session_id),
        )

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert restarted.code == "observation_accepted"
    assert len(adapter.reconcile_requests) == 1
    assert next(iter(after.runner_sessions.values())).state == "completed"


@pytest.mark.parametrize(
    ("cleanup_handle_id", "expected_code", "expected_operations"),
    (
        (
            "legacy-upgraded-handle",
            "adapter_failure",
            ["transport_cleanup"],
        ),
        (
            "foreign-cleanup-handle",
            "runner_session_reconciliation_contradiction",
            [],
        ),
    ),
)
def test_legacy_running_locator_upgrade_proves_subsequent_cleanup_handle(
    tmp_path,
    cleanup_handle_id: str,
    expected_code: str,
    expected_operations: list[str],
) -> None:
    cleanup_handle = None
    reconcile_count = 0

    class MalformedLiveHandle(_ImmediateHandle):
        def poll_completion(self) -> object:
            return object()

    def reconcile(request):
        nonlocal cleanup_handle, reconcile_count
        reconcile_count += 1
        invocation = request.invocation_request
        echo = _dispatch_echo(invocation)
        if reconcile_count == 1:
            return VerifiedLive(
                echo,
                MalformedLiveHandle(_success_outcome(invocation)),
                "legacy-upgraded-handle",
                {"provider_request_id": "legacy-running-request"},
            )
        cleanup_handle = _CleanupTrackingHandle(
            _error_outcome(invocation, dispatch_echo=echo, error_kind="cancelled")
        )
        return CleanupPending(echo, cleanup_handle, cleanup_handle_id)

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    run_bounded_execution_unit(runtime, local_config=_config(adapter))
    session_id = _replace_running_locator_with_legacy_bare(runtime)
    runtime = _reopen_runtime(runtime)

    first_reconcile = reconcile_pending_runner_sessions(
        runtime,
        local_config=_config(adapter),
    )
    after_first = _load(runtime)
    upgraded = json.loads(
        runtime.cas_store.get_bytes(
            after_first.runner_sessions[session_id].durable_locator_digest
        )
    )

    assert first_reconcile.code == "session_reconciliation_required"
    assert upgraded["record_kind"] == "runner_session_coordinator_locator"
    assert upgraded["schema_version"] == 1
    assert upgraded["handle_id_digest"] == (
        session_reconciliation._handle_id_digest("legacy-upgraded-handle")
    )

    runtime = _reopen_runtime(runtime)
    second_reconcile = reconcile_pending_runner_sessions(
        runtime,
        local_config=_config(adapter),
    )

    assert second_reconcile.code == expected_code
    assert cleanup_handle is not None
    assert cleanup_handle.operations == expected_operations


def test_legacy_running_locator_is_upgraded_before_live_completion_poll(
    tmp_path,
) -> None:
    inspected_locators: list[dict[str, object]] = []

    class InspectingHandle(_ImmediateHandle):
        def poll_completion(self) -> AdapterInvocationOutcome | None:
            current = _load(runtime)
            session = next(iter(current.runner_sessions.values()))
            inspected_locators.append(
                json.loads(runtime.cas_store.get_bytes(session.durable_locator_digest))
            )
            return super().poll_completion()

    def reconcile(request):
        invocation = request.invocation_request
        echo = _dispatch_echo(invocation)
        return VerifiedLive(
            echo,
            InspectingHandle(_success_outcome(invocation)),
            "legacy-completing-handle",
            {"provider_request_id": "legacy-running-request"},
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    run_bounded_execution_unit(runtime, local_config=_config(adapter))
    _replace_running_locator_with_legacy_bare(runtime)
    runtime = _reopen_runtime(runtime)

    result = reconcile_pending_runner_sessions(
        runtime,
        local_config=_config(adapter),
    )

    assert result.code == "observation_accepted"
    assert inspected_locators == [
        {
            "adapter_locator": {"provider_request_id": "legacy-running-request"},
            "handle_id_digest": session_reconciliation._handle_id_digest(
                "legacy-completing-handle"
            ),
            "record_kind": "runner_session_coordinator_locator",
            "schema_version": 1,
        }
    ]


def test_restart_legacy_bare_locator_cannot_authorize_cleanup(
    tmp_path,
) -> None:
    handle = None

    class CleanupTrackingHandle(_ImmediateHandle):
        def __init__(self) -> None:
            self.operations: list[str] = []

        def cleanup(self) -> RunnerCleanupResult:
            self.operations.append("transport_cleanup")
            return super().cleanup()

    def reconcile(request):
        nonlocal handle
        invocation = request.invocation_request
        echo = _dispatch_echo(invocation)
        handle = CleanupTrackingHandle()
        return CleanupPending(echo, handle, "unproven-legacy-handle")

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    legacy_digest = runtime.cas_store.put_bytes(
        runner_session_locator_bytes({"provider_request_id": "legacy-request"})
    )
    with sqlite3.connect(runtime.paths.db_path) as connection:
        connection.execute(
            """
            UPDATE runner_sessions
            SET durable_locator_digest = ?
            WHERE session_id = ?
            """,
            (legacy_digest, session.session_id),
        )

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )

    assert restarted.code == "runner_session_reconciliation_contradiction"
    assert handle is not None
    assert handle.operations == []
    assert _load(runtime).runner_session_completions == {}


@pytest.mark.parametrize(
    "invalid_locator",
    (
        {
            "record_kind": "runner_session_coordinator_locator",
            "schema_version": 999,
            "adapter_locator": {},
            "handle_id_digest": None,
        },
        {
            "record_kind": "runner_session_coordinator_locator",
            "schema_version": 1,
            "adapter_locator": {},
        },
        {
            "record_kind": "runner_session_coordinator_locator",
            "schema_version": True,
            "adapter_locator": {},
            "handle_id_digest": None,
        },
    ),
)
def test_restart_refuses_invalid_coordinator_locator_before_adapter(
    tmp_path,
    invalid_locator: dict[str, object],
) -> None:
    adapter = _RecordingAdapter(_indeterminate_start)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    invalid_digest = runtime.cas_store.put_bytes(
        runner_session_locator_bytes(invalid_locator)
    )
    with sqlite3.connect(runtime.paths.db_path) as connection:
        connection.execute(
            """
            UPDATE runner_sessions
            SET durable_locator_digest = ?
            WHERE session_id = ?
            """,
            (invalid_digest, session.session_id),
        )

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )

    assert restarted.code == "runner_session_reconciliation_contradiction"
    assert adapter.reconcile_requests == []
    assert _load(runtime).runner_session_completions == {}


def test_restart_verified_live_fault_cleans_owned_handle_before_return(
    tmp_path,
) -> None:
    handle = None

    class MalformedReconciledHandle(_ImmediateHandle):
        def __init__(self) -> None:
            self.operations: list[str] = []

        def poll_completion(self) -> object:
            return object()

        def cleanup(self) -> RunnerCleanupResult:
            self.operations.append("transport_cleanup")
            return super().cleanup()

    def reconcile(request):
        nonlocal handle
        invocation = request.invocation_request
        echo = _dispatch_echo(invocation)
        handle = MalformedReconciledHandle()
        return VerifiedLive(
            echo,
            handle,
            "verified-owned-handle",
            request.durable_locator_metadata,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert restarted.code == "session_reconciliation_required"
    assert handle is not None
    assert handle.operations == ["transport_cleanup"]
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "cancellation_requested"
    assert after.runner_session_completions == {}
    assert after.runner_observations == {}


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
def test_restart_verified_live_fault_cleans_real_subprocess_before_return(
    tmp_path,
) -> None:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig

    heartbeat = tmp_path / "restart-live-heartbeat.txt"
    process_pid = tmp_path / "restart-live.pid"
    wrapper = (
        "import os,pathlib,time\n"
        f"heartbeat=pathlib.Path({str(heartbeat)!r})\n"
        f"pathlib.Path({str(process_pid)!r}).write_text(str(os.getpid()))\n"
        "while True:\n"
        " heartbeat.write_text(str(time.time()))\n"
        " time.sleep(0.03)\n"
    )
    live_handles = []
    codex = CodexAdapter(
        CodexAdapterConfig(
            adapter_id="codex",
            wrapper_mode="offline_fake",
            wrapper_argv=(sys.executable, "-c", wrapper),
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_input_bundle_bytes=16384,
            max_stdout_bytes=8192,
            max_stderr_diagnostic_bytes=512,
            redaction_policy=RedactionPolicy(policy_id="cli-default"),
        )
    )

    class MalformedLiveHandle:
        def __init__(self, inner) -> None:
            self._inner = inner

        def poll_completion(self) -> object:
            deadline = time.time() + 2
            while not heartbeat.exists() and time.time() < deadline:
                time.sleep(0.01)
            return object()

        def request_cancel(self):
            return self._inner.request_cancel()

        def terminate(self):
            return self._inner.terminate()

        def kill(self):
            return self._inner.kill()

        def cleanup(self):
            return self._inner.cleanup()

    def reconcile(request):
        invocation = request.invocation_request
        started = codex.start_session(invocation)
        assert isinstance(started, StartedSession)
        live_handles.append(started.handle)
        return VerifiedLive(
            started.dispatch_echo,
            MalformedLiveHandle(started.handle),
            started.handle_id,
            started.durable_locator_metadata,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_codex_runtime(tmp_path)
    run_bounded_execution_unit(runtime, local_config=_config(adapter))
    runtime = _reopen_runtime(runtime)
    try:
        result = reconcile_pending_runner_sessions(
            runtime,
            local_config=_config(adapter),
        )

        assert result.code == "session_reconciliation_required"
        assert heartbeat.exists()
        stable = heartbeat.read_text()
        time.sleep(0.15)
        assert heartbeat.read_text() == stable
        after = _load(runtime)
        assert next(iter(after.runner_sessions.values())).state == (
            "cancellation_requested"
        )
        assert after.runner_session_completions == {}
        assert after.runner_observations == {}
    finally:
        for live_handle in live_handles:
            live_handle.kill()
            live_handle.cleanup()
        if process_pid.exists():
            try:
                os.killpg(int(process_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_restart_verified_live_application_crash_after_completion_cleans_once(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = None

    class CompletionTrackingHandle(_ImmediateHandle):
        def __init__(self, outcome: AdapterInvocationOutcome) -> None:
            super().__init__(outcome)
            self.operations: list[str] = []

        def cleanup(self) -> RunnerCleanupResult:
            self.operations.append("transport_cleanup")
            return super().cleanup()

    def reconcile(request):
        nonlocal handle
        invocation = request.invocation_request
        echo = _dispatch_echo(invocation)
        handle = CompletionTrackingHandle(_success_outcome(invocation))
        return VerifiedLive(
            echo,
            handle,
            "verified-owned-handle",
            request.durable_locator_metadata,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    runtime = _reopen_runtime(runtime)
    original_decide = session_completion.decide

    def crash_before_application(current, transition_input, context):
        if isinstance(transition_input, RunnerResultObserved):
            raise RuntimeError("application crash")
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_completion, "decide", crash_before_application)
    with pytest.raises(RuntimeError, match="application crash"):
        run_bounded_execution_unit(
            runtime,
            activation_id=first.activation_id,
            local_config=_config(adapter),
        )
    persisted = _load(runtime)

    assert handle is not None
    assert handle.operations == ["transport_cleanup"]
    assert len(persisted.runner_session_completions) == 1
    assert persisted.runner_observations == {}

    monkeypatch.setattr(session_completion, "decide", original_decide)
    runtime = _reopen_runtime(runtime)
    replay = reconcile_pending_runner_sessions(
        runtime,
        local_config=_config(adapter),
    )

    assert replay.code == "observation_accepted"
    assert handle.operations == ["transport_cleanup"]
    assert len(adapter.reconcile_requests) == 1


@pytest.mark.parametrize(
    "mismatch",
    (
        {"claim_id": "hostile-claim"},
        {"plan_fingerprint": "sha256:" + "f" * 64},
        {"runner_binding_id": "hostile-binding"},
        {"stage_kind_id": "hostile-stage"},
        {"graph_node_id": "hostile-node"},
        {"queue_family_id": "hostile-queue"},
        {"session_id": "hostile-session"},
        {"session_fencing_token": "hostile-fence"},
        {"run_id": "hostile-run"},
        {"generation": 99},
        {"fencing_token": "hostile-run-fence"},
        {"correlation_id": "hostile-correlation"},
    ),
)
def test_restart_refuses_verified_live_authority_mismatch(
    tmp_path,
    mismatch: dict[str, object],
) -> None:
    def reconcile(request):
        invocation = request.invocation_request
        echo = replace(
            _dispatch_echo(invocation),
            **mismatch,
        )
        return VerifiedLive(echo, _SequenceHandle([]), "hostile-handle", {})

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    before = _load(runtime)

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert restarted.code == "runner_session_reconciliation_contradiction"
    assert after.runner_sessions == before.runner_sessions
    assert after.runner_session_completions == before.runner_session_completions
    assert after.runner_observations == before.runner_observations
    assert after.runs == before.runs


def test_restart_terminal_outcome_uses_existing_completion_path(tmp_path) -> None:
    def reconcile(request):
        invocation = request.invocation_request
        echo = _dispatch_echo(invocation)
        return Terminal(echo, _success_outcome(invocation), "complete")

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert restarted.code == "observation_accepted"
    assert len(after.runner_session_completions) == 1
    assert len(after.runner_observations) == 1


def test_restart_adapter_contradiction_refuses_without_guessed_repair(
    tmp_path,
) -> None:
    def reconcile(request):
        invocation = request.invocation_request
        return Contradiction(
            _dispatch_echo(invocation),
            "sha256:" + "c" * 64,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    before = _load(runtime)

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert restarted.code == "runner_session_reconciliation_contradiction"
    assert after.runner_sessions == before.runner_sessions
    assert after.runner_session_completions == {}
    assert after.runner_observations == {}
    assert after.runs == before.runs


def test_orphan_risk_explicit_retry_preserves_claim_and_refuses_replacement(
    tmp_path,
) -> None:
    adapter = _RecordingAdapter(_indeterminate_start)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    orphaned = _load(runtime)
    run = orphaned.runs[first.run_id]

    retry = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(_RecordingAdapter(_success_start)),
    )
    after = _load(runtime)

    assert retry.code == "runner_session_orphan_risk"
    assert after.runs == orphaned.runs
    assert after.runner_sessions == orphaned.runner_sessions
    assert after.runner_session_completions == (orphaned.runner_session_completions)
    assert after.runs[first.run_id].run_ref.claim_id == run.run_ref.claim_id


def test_late_output_after_orphan_risk_remains_fenced(tmp_path) -> None:
    adapter = _RecordingAdapter(_indeterminate_start)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    request = adapter.requests[0]
    run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    orphaned = _load(runtime)
    session = orphaned.runner_sessions[request.session_id]

    late = session_completion._persist_completion(
        runtime,
        run_ref=orphaned.runs[session.run_id].run_ref,
        session=session,
        request=request,
        outcome=_success_outcome(request),
        cleanup=session_cancellation._terminal_cleanup_result(None, "complete"),
    )
    after = _load(runtime)

    assert late.code == "completion_refused"
    assert after.runner_sessions == orphaned.runner_sessions
    assert after.runner_session_completions == (orphaned.runner_session_completions)
    assert after.runner_observations == orphaned.runner_observations == {}
    assert after.artifacts == orphaned.artifacts == {}
    assert after.activation_routes == orphaned.activation_routes == ()


def test_restart_resumes_created_session_without_reconciliation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter(_success_start)
    runtime = _ready_runtime(tmp_path)
    original = session_completion._persist_transition
    crashed = False

    def crash_before_start_intent(current_runtime, transition):
        nonlocal crashed
        if (
            not crashed
            and getattr(transition, "input_kind", None)
            == "workflow.advance_runner_session"
        ):
            crashed = True
            raise RuntimeError("crash before durable start intent")
        return original(current_runtime, transition)

    monkeypatch.setattr(
        session_completion,
        "_persist_transition",
        crash_before_start_intent,
    )
    with pytest.raises(RuntimeError, match="start intent"):
        run_bounded_execution_unit(runtime, local_config=_config(adapter))
    created = _load(runtime)
    session = next(iter(created.runner_sessions.values()))
    assert session.state == "created"
    assert adapter.requests == []

    monkeypatch.setattr(session_completion, "_persist_transition", original)
    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=_config(adapter),
    )

    assert restarted.code == "observation_accepted"
    assert len(adapter.requests) == 1
    assert adapter.reconcile_requests == []


@pytest.mark.parametrize(
    (
        "returned_handle_id",
        "foreign_terminal_proof",
        "expected_code",
        "expected_operations",
    ),
    (
        (
            "verified-cleanup-handle",
            False,
            "adapter_failure",
            ["transport_cleanup"],
        ),
        (
            "foreign-cleanup-handle",
            False,
            "runner_session_reconciliation_contradiction",
            [],
        ),
        (
            "verified-cleanup-handle",
            True,
            "session_reconciliation_required",
            ["transport_cleanup"],
        ),
    ),
)
def test_restart_cleanup_pending_continues_only_verified_handle_cleanup(
    tmp_path,
    returned_handle_id: str,
    foreign_terminal_proof: bool,
    expected_code: str,
    expected_operations: list[str],
) -> None:
    handle = None

    def reconcile(request):
        nonlocal handle
        invocation = request.invocation_request
        echo = _dispatch_echo(invocation)
        terminal_echo = (
            replace(echo, session_fencing_token="foreign-session-fence")
            if foreign_terminal_proof
            else echo
        )
        handle = _CleanupTrackingHandle(
            _error_outcome(
                invocation,
                dispatch_echo=terminal_echo,
                error_kind="cancelled",
            )
        )
        return CleanupPending(echo, handle, returned_handle_id)

    def indeterminate_cleanup_pending_start(
        request: AdapterInvocationRequest,
    ) -> StartIndeterminate:
        return StartIndeterminate(
            _dispatch_echo(request),
            None,
            "sha256:" + "a" * 64,
        )

    adapter = _RecordingAdapter(indeterminate_cleanup_pending_start, reconcile)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    request = adapter.requests[0]
    assert session.start_intent_at is not None
    locator_digest = session_reconciliation._safe_coordinator_locator_digest(
        runtime,
        request,
        handle_id="verified-cleanup-handle",
        adapter_locator={"provider_request_id": "owned-request"},
    )
    assert locator_digest is not None
    persisted = session_completion._persist_transition(
        runtime,
        AdvanceRunnerSession(
            "test:prove-cleanup-handle",
            run_ref=current.runs[session.run_id].run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            expected_state="starting",
            next_state="starting",
            occurred_at=session.start_intent_at,
            durable_locator_digest=locator_digest,
        ),
    )
    assert persisted is not None
    cancellation = session_coordinator.request_operator_cancellation(
        runtime,
        run_id=first.run_id,
        request_id="cleanup-restart-cancel",
        actor_id="operator",
    )
    assert cancellation.accepted

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert restarted.code == expected_code
    assert handle is not None
    assert handle.operations == expected_operations
    session = next(iter(after.runner_sessions.values()))
    if returned_handle_id == "verified-cleanup-handle" and not foreign_terminal_proof:
        assert restarted.adapter_error_kind == "cancelled"
        assert (session.state, session.cleanup_disposition) == (
            "interrupted",
            "complete",
        )
    else:
        assert session.state == "cancellation_requested"
        assert after.runner_session_completions == {}
        assert after.runner_observations == {}


@pytest.mark.parametrize("restart_state", ("running", "terminating"))
def test_restart_reconciles_running_and_terminating_sessions(
    tmp_path,
    restart_state: str,
) -> None:
    adapter = _RecordingAdapter(_indeterminate_start)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    run = current.runs[session.run_id]
    if restart_state == "running":
        assert session.durable_locator_digest is not None
        persisted = session_completion._persist_transition(
            runtime,
            AdvanceRunnerSession(
                f"test:restart-running:{session.session_id}",
                run_ref=run.run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                expected_state="starting",
                next_state="running",
                occurred_at=session.start_intent_at,
                durable_locator_digest=session.durable_locator_digest,
            ),
        )
        assert persisted is not None
    else:
        cancellation = session_coordinator.request_operator_cancellation(
            runtime,
            run_id=first.run_id,
            request_id="terminating-restart-cancel",
            actor_id="operator",
        )
        assert cancellation.accepted
        current = _load(runtime)
        session = current.runner_sessions[session.session_id]
        primary = current.runner_session_cancellation_requests[
            "terminating-restart-cancel"
        ]
        persisted = session_completion._persist_transition(
            runtime,
            AdvanceRunnerSession(
                f"test:restart-terminating:{session.session_id}",
                run_ref=run.run_ref,
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                expected_state="cancellation_requested",
                next_state="terminating",
                occurred_at=primary.requested_at,
            ),
        )
        assert persisted is not None

    runtime = _reopen_runtime(runtime)
    restarted = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert restarted.code == "runner_session_orphan_risk"
    assert len(adapter.reconcile_requests) == 1
    session = next(iter(after.runner_sessions.values()))
    assert (session.state, session.cleanup_disposition) == ("lost", "orphan_risk")
