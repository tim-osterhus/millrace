from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cli.test_cli_bounded_execution_unit import (
    _codex_success_config,
    _codex_success_wrapper,
    _codex_timeout_config,
    _load,
)
from millrace.adapters.cli import (
    session_cancellation,
    session_completion,
    session_coordinator,
    session_reconciliation,
)
from millrace.adapters.cli.run import (
    run_bounded_execution_unit,
)
from millrace.adapters.runner_contract import (
    START_REFUSAL_DIAGNOSTIC_MAX_BYTES,
    AdapterErrorResult,
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterSuccessResult,
    RedactionPolicy,
    RunnerCleanupResult,
    StartedSession,
    StartRefusedBeforeExternalWork,
    runner_cancellation_diagnostic_digest,
    start_refusal_diagnostic_digest,
)
from millrace.contracts.transition import (
    RunnerResultObserved,
)
from millrace.operator.prompt_material import SelectedAssetMaterializationError
from support.runner_sessions import (
    _assert_heartbeat_stopped,
    _CancellingHandle,
    _CapturingImmediateHandle,
    _codex_process_adapter,
    _config,
    _dispatch_echo,
    _heartbeat_wrapper,
    _ImmediateHandle,
    _kill_recorded_process,
    _ready_runtime,
    _RecordingAdapter,
    _started_session,
    _success_start,
)


def test_request_factory_side_effect_occurs_after_start_intent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter(_success_start)
    runtime = _ready_runtime(tmp_path)

    def fail_materialization(**_kwargs: object) -> object:
        current = _load(runtime)
        assert next(iter(current.runner_sessions.values())).state == "starting"
        raise SelectedAssetMaterializationError("pre-start crash")

    monkeypatch.setattr(
        "millrace.adapters.cli.run.build_selected_asset_material",
        fail_materialization,
    )
    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "asset_material_refused"
    assert adapter.requests == []
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "starting"
    assert session.start_intent_at is not None


def test_daemon_stop_before_external_start_prevents_adapter_call(
    tmp_path,
) -> None:
    adapter = _RecordingAdapter(_success_start)
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(adapter),
        daemon_stop_requested=lambda: True,
    )
    after = _load(runtime)

    assert result.adapter_error_kind == "cancelled"
    assert adapter.requests == []
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "interrupted"
    assert session.start_intent_at is None
    assert session.cleanup_disposition == "not_required"


def test_cancellation_after_start_intent_prevents_external_start(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run

    adapter = _RecordingAdapter(_success_start)
    runtime = _ready_runtime(tmp_path)
    original = run._session_invocation_request

    def cancel_during_request(*args: object, **kwargs: object):
        request = original(*args, **kwargs)
        requested = session_coordinator.request_operator_cancellation(
            runtime,
            run_id=request.dispatch_envelope.run_id,
            request_id="cancel-after-intent",
            actor_id="operator",
        )
        assert requested.accepted
        return request

    monkeypatch.setattr(run, "_session_invocation_request", cancel_during_request)
    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.adapter_error_kind == "cancelled"
    assert adapter.requests == []
    assert len(after.runner_session_completions) == 1
    session = next(iter(after.runner_sessions.values()))
    assert (session.state, session.cleanup_disposition) == (
        "interrupted",
        "not_required",
    )


def test_operator_cancellation_after_authority_check_prevents_external_start(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter(_success_start)
    runtime = _ready_runtime(tmp_path)
    original = session_reconciliation._request_matches_current_authority
    workflow_snapshot = None

    def cancel_after_authority(*args: object, **kwargs: object) -> bool:
        nonlocal workflow_snapshot
        matches = original(*args, **kwargs)
        assert matches
        workflow_snapshot = _load(runtime)
        session = kwargs["session"]
        result = session_coordinator.request_operator_cancellation(
            runtime,
            run_id=session.run_id,
            request_id="cancel-after-authority",
            actor_id="operator",
        )
        assert result.accepted
        return matches

    monkeypatch.setattr(
        session_reconciliation,
        "_request_matches_current_authority",
        cancel_after_authority,
    )
    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.adapter_error_kind == "cancelled"
    assert adapter.requests == []
    assert len(after.runner_session_completions) == 1
    session = next(iter(after.runner_sessions.values()))
    assert (session.state, session.cleanup_disposition) == (
        "interrupted",
        "not_required",
    )
    assert workflow_snapshot is not None
    assert after.work_items == workflow_snapshot.work_items
    assert after.activations == workflow_snapshot.activations
    assert after.activation_routes == workflow_snapshot.activation_routes
    assert after.closed_work_items == workflow_snapshot.closed_work_items
    assert after.runner_observations == workflow_snapshot.runner_observations


def test_daemon_stop_after_authority_check_prevents_external_start(
    tmp_path,
) -> None:
    adapter = _RecordingAdapter(_success_start)
    runtime = _ready_runtime(tmp_path)
    callback_calls = 0

    def stop_at_final_gate() -> bool:
        nonlocal callback_calls
        callback_calls += 1
        return callback_calls == 2

    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(adapter),
        daemon_stop_requested=stop_at_final_gate,
    )
    after = _load(runtime)

    assert result.adapter_error_kind == "cancelled"
    assert adapter.requests == []
    assert len(after.runner_session_completions) == 1
    session = next(iter(after.runner_sessions.values()))
    assert (session.state, session.cleanup_disposition) == (
        "interrupted",
        "not_required",
    )
    cancellation = next(iter(after.runner_session_cancellation_requests.values()))
    assert (cancellation.reason, cancellation.source_kind, cancellation.actor_id) == (
        "daemon_shutdown",
        "daemon",
        "daemon",
    )
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


@pytest.mark.parametrize(
    "mutate",
    (
        lambda request: replace(
            request,
            correlation_id="arbitrary-correlation",
        ),
        lambda request: replace(
            request,
            cancellation_token="arbitrary-cancel-token",
        ),
        lambda request: replace(
            request,
            selected_adapter_kind="millforge",
        ),
        lambda request: replace(
            request,
            dispatch_envelope=replace(
                request.dispatch_envelope,
                work_item_payload={"foreign": True},
            ),
        ),
    ),
)
def test_request_identity_mismatch_is_audited_before_adapter_call(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[AdapterInvocationRequest], AdapterInvocationRequest],
) -> None:
    from millrace.adapters.cli import run

    adapter = _RecordingAdapter(_success_start)
    runtime = _ready_runtime(tmp_path)
    original = run._session_invocation_request

    def stale_request(*args: object, **kwargs: object) -> AdapterInvocationRequest:
        request = original(*args, **kwargs)
        return mutate(request)

    monkeypatch.setattr(run, "_session_invocation_request", stale_request)
    before = _load(runtime)
    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "session_reconciliation_required"
    assert adapter.requests == []
    assert len(after.refusals) == len(before.refusals) + 1
    assert next(iter(after.runner_sessions.values())).state == "starting"


def test_indeterminate_start_exception_stays_starting(tmp_path) -> None:
    def raise_after_start(_request: AdapterInvocationRequest) -> object:
        raise TimeoutError("external start may have happened")

    adapter = _RecordingAdapter(raise_after_start)
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "session_reconciliation_required"
    assert len(adapter.requests) == 1
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "starting"
    assert session.session_id not in after.runner_session_completions


def test_local_timeout_narrows_selected_deadline_and_requests_cancellation(
    tmp_path,
) -> None:
    runtime = _ready_runtime(tmp_path)

    started = time.monotonic()
    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_timeout_config(),
    )
    elapsed = time.monotonic() - started
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "cancelled"
    assert elapsed < 0.8
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "interrupted"
    cancellation = next(iter(after.runner_session_cancellation_requests.values()))
    assert (cancellation.reason, cancellation.source_kind) == (
        "runner_timeout",
        "runtime",
    )


@pytest.mark.parametrize(
    "timeout",
    (True, 0, -1, float("inf"), float("nan"), "5"),
)
def test_generic_local_timeout_must_be_finite_positive(tmp_path, timeout) -> None:
    adapter = _RecordingAdapter(_success_start)
    adapter.config = SimpleNamespace(timeout_seconds=timeout)
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))

    assert result.code == "adapter_failure"
    assert adapter.requests == []


def test_start_succeeds_but_running_write_fails_cannot_permit_another_start(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = None
    handle = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _CancellingHandle(runtime, request)
        return replace(_success_start(request), handle=handle)

    adapter = _RecordingAdapter(start)
    runtime = _ready_runtime(tmp_path)
    persist = runtime.store.persist_runtime_state

    def fail_running(candidate, cas_store) -> None:
        if any(
            session.state == "running" for session in candidate.runner_sessions.values()
        ):
            raise RuntimeError("simulated running write crash")
        persist(candidate, cas_store)

    monkeypatch.setattr(runtime.store, "persist_runtime_state", fail_running)
    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    monkeypatch.setattr(runtime.store, "persist_runtime_state", persist)

    after = _load(runtime)

    assert result.adapter_error_kind == "cancelled"
    assert len(adapter.requests) == 1
    assert handle is not None
    assert handle.operations == ["cooperative_cancel", "transport_cleanup"]
    assert next(iter(after.runner_sessions.values())).state == "interrupted"


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
def test_running_write_failure_cleans_real_subprocess(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.codex import CodexAdapter

    ready = tmp_path / "running-write-failure.ready"
    heartbeat = tmp_path / "running-write-failure.txt"
    process_pid = tmp_path / "running-write-failure.pid"
    wrapper = _heartbeat_wrapper(ready, heartbeat, process_pid)

    class ReadyCodexAdapter(CodexAdapter):
        def start_session(self, request):
            started = super().start_session(request)
            deadline = time.monotonic() + 2
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), (
                "subprocess did not reach running-write ready barrier"
            )
            return started

    adapter = _codex_process_adapter(tmp_path, wrapper, ReadyCodexAdapter)
    runtime = _ready_runtime(tmp_path)
    persist = runtime.store.persist_runtime_state

    def fail_running(candidate, cas_store) -> None:
        if any(
            session.state == "running" for session in candidate.runner_sessions.values()
        ):
            raise RuntimeError("simulated running write crash")
        persist(candidate, cas_store)

    monkeypatch.setattr(runtime.store, "persist_runtime_state", fail_running)
    try:
        result = run_bounded_execution_unit(
            runtime,
            local_config=AdapterLocalConfig(adapters={"codex": adapter}),
        )
        after = _load(runtime)

        assert result.adapter_error_kind == "cancelled"
        cancellation = next(iter(after.runner_session_cancellation_requests.values()))
        assert cancellation.reason == "runtime_failure"
        session = next(iter(after.runner_sessions.values()))
        assert (session.state, session.cleanup_disposition) == (
            "interrupted",
            "complete",
        )
        _assert_heartbeat_stopped(heartbeat)
    finally:
        _kill_recorded_process(process_pid)


def test_live_handle_fence_cleans_after_attempt_persistence_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = None
    handle = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _CancellingHandle(runtime, request)
        return replace(_success_start(request), handle=handle)

    def fail_attempt(*_args, **_kwargs):
        raise RuntimeError("simulated attempt write failure")

    monkeypatch.setattr(
        session_cancellation,
        "_persist_cancellation_operation",
        fail_attempt,
    )
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )

    assert result.code == "session_reconciliation_required"
    assert handle is not None
    assert handle.operations == [
        "cooperative_cancel",
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    after = _load(runtime)
    session = next(iter(after.runner_sessions.values()))
    assert session.state in {"cancellation_requested", "terminating"}
    assert after.runner_observations == {}


def test_live_handle_fence_reports_orphan_when_emergency_cleanup_raises(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = None
    handle = None

    class CleanupFailureHandle(_CancellingHandle):
        def cleanup(self) -> RunnerCleanupResult:
            self.operations.append("transport_cleanup")
            raise RuntimeError("cleanup failed")

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = CleanupFailureHandle(runtime, request)
        return replace(_success_start(request), handle=handle)

    monkeypatch.setattr(
        session_cancellation,
        "_persist_cancellation_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("attempt write failed")
        ),
    )
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )

    assert result.code == "runner_session_orphan_risk"
    assert handle is not None
    assert handle.operations[-4:] == [
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    assert _load(runtime).runner_observations == {}


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
def test_live_subprocess_fence_cleans_after_attempt_persistence_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.codex import CodexAdapter

    captured_handles = []

    class CapturingCodexAdapter(CodexAdapter):
        def start_session(self, request):
            started = super().start_session(request)
            if isinstance(started, StartedSession):
                captured_handles.append(started.handle)
            return started

    heartbeat = tmp_path / "live-fence-heartbeat.txt"
    ready = tmp_path / "live-fence.ready"
    process_pid = tmp_path / "live-fence.pid"
    wrapper = _heartbeat_wrapper(ready, heartbeat, process_pid)
    adapter = _codex_process_adapter(tmp_path, wrapper, CapturingCodexAdapter)

    def cancel_only_after_start() -> bool:
        if not captured_handles:
            return False
        deadline = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "subprocess did not reach live-fence ready barrier"
        return True

    def fail_attempt(*_args, **_kwargs):
        raise RuntimeError("simulated attempt write failure")

    monkeypatch.setattr(
        session_cancellation,
        "_persist_cancellation_operation",
        fail_attempt,
    )
    runtime = _ready_runtime(tmp_path)
    try:
        result = run_bounded_execution_unit(
            runtime,
            local_config=AdapterLocalConfig(adapters={"codex": adapter}),
            daemon_stop_requested=cancel_only_after_start,
        )

        assert result.code == "session_reconciliation_required"
        deadline = time.time() + 2
        while not heartbeat.exists() and time.time() < deadline:
            time.sleep(0.01)
        _assert_heartbeat_stopped(heartbeat)
        after = _load(runtime)
        session = next(iter(after.runner_sessions.values()))
        assert session.state in {"cancellation_requested", "terminating"}
        assert session.cleanup_disposition == "pending"
        assert after.runner_observations == {}
    finally:
        for live_handle in captured_handles:
            live_handle.kill()
            live_handle.cleanup()
        _kill_recorded_process(process_pid, group=True)


def test_raw_adapter_error_diagnostic_is_coordinator_redacted(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run

    secret = "RAW_ADAPTER_SECRET"
    original = run._session_invocation_request
    monkeypatch.setattr(
        run,
        "_session_invocation_request",
        lambda *args, **kwargs: replace(
            original(*args, **kwargs),
            redaction_policy=RedactionPolicy("redact-default", (secret,)),
        ),
    )

    def raw_error(request: AdapterInvocationRequest) -> StartedSession:
        outcome = AdapterErrorResult(
            adapter_id=request.adapter_id,
            error_kind="invocation_failed",
            redaction_policy_id=request.redaction_policy.policy_id,
            dispatch_echo=_dispatch_echo(request),
            diagnostics={"secret": secret, "blob": "x" * 20_000},
        )
        return replace(_success_start(request), handle=_ImmediateHandle(outcome))

    runtime = _ready_runtime(tmp_path)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(raw_error)),
    )

    assert result.adapter_error_kind == "invocation_failed"
    cas_payloads = tuple(
        path.read_bytes()
        for path in runtime.paths.cas_path.rglob("*")
        if path.is_file()
    )
    assert cas_payloads
    assert all(secret.encode() not in payload for payload in cas_payloads)
    completion = next(iter(_load(runtime).runner_session_completions.values()))
    diagnostic = runtime.cas_store.get_bytes(completion.diagnostic_digest)
    assert len(diagnostic) <= START_REFUSAL_DIAGNOSTIC_MAX_BYTES
    assert json.loads(diagnostic)["diagnostics"]["truncated"] is True


def test_adapter_error_redaction_failure_persists_only_safe_diagnostic(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "REDACTION_FAILURE_SECRET"

    class FailingRedactionHandle(_ImmediateHandle):
        def poll_completion(self):
            monkeypatch.setattr(
                RedactionPolicy,
                "redact_authority_value",
                lambda _self, _value: (_ for _ in ()).throw(
                    RuntimeError("redaction failed")
                ),
            )
            return super().poll_completion()

    def raw_error(request: AdapterInvocationRequest) -> StartedSession:
        outcome = AdapterErrorResult(
            adapter_id=request.adapter_id,
            error_kind="invocation_failed",
            redaction_policy_id=request.redaction_policy.policy_id,
            dispatch_echo=_dispatch_echo(request),
            diagnostics={"secret": secret},
        )
        return replace(
            _success_start(request),
            handle=FailingRedactionHandle(outcome),
        )

    runtime = _ready_runtime(tmp_path)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(raw_error)),
    )
    completion = next(iter(_load(runtime).runner_session_completions.values()))
    diagnostic = runtime.cas_store.get_bytes(completion.diagnostic_digest)

    assert result.adapter_error_kind == "invocation_failed"
    assert secret.encode() not in diagnostic
    assert json.loads(diagnostic)["diagnostics"]["redaction_failed"] is True


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
def test_coordinator_cleans_child_group_before_normal_completion(tmp_path) -> None:

    heartbeat = tmp_path / "coordinator-child.txt"
    ready = tmp_path / "coordinator-child.ready"
    child_pid = tmp_path / "coordinator-child.pid"
    child = (
        "import pathlib,time\n"
        f"ready=pathlib.Path({str(ready)!r})\n"
        f"path=pathlib.Path({str(heartbeat)!r})\n"
        "ready.write_text('ready')\n"
        "while True:\n"
        " path.write_text(str(time.time()))\n"
        " time.sleep(0.03)\n"
    )
    wrapper = (
        "import pathlib,subprocess,sys,time\n"
        f"child_process=subprocess.Popen([sys.executable,'-c',{child!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL)\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child_process.pid))\n"
        f"ready=pathlib.Path({str(ready)!r})\n"
        "deadline=time.monotonic()+2\n"
        "while not ready.exists() and time.monotonic()<deadline:\n"
        " time.sleep(0.01)\n"
        "assert ready.exists(), 'child did not reach normal-completion ready barrier'\n"
        + _codex_success_wrapper("TASK_COMPLETE")
    )
    adapter = _codex_process_adapter(tmp_path, wrapper)
    runtime = _ready_runtime(tmp_path)
    try:
        result = run_bounded_execution_unit(
            runtime,
            local_config=AdapterLocalConfig(adapters={"codex": adapter}),
        )
        after = _load(runtime)

        assert result.code == "observation_accepted"
        cancellation = next(iter(after.runner_session_cancellation_requests.values()))
        assert (cancellation.reason, cancellation.source_kind) == (
            "runtime_failure",
            "runtime",
        )
        completion = next(iter(after.runner_session_completions.values()))
        assert (completion.terminal_state, completion.cleanup_disposition) == (
            "completed",
            "complete",
        )
        _assert_heartbeat_stopped(heartbeat)
    finally:
        _kill_recorded_process(child_pid)


def test_raw_start_refusal_secret_is_not_persisted(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run

    secret = "RAW_START_REFUSAL_SECRET"
    original = run._session_invocation_request
    monkeypatch.setattr(
        run,
        "_session_invocation_request",
        lambda *args, **kwargs: replace(
            original(*args, **kwargs),
            redaction_policy=RedactionPolicy("redact-default", (secret,)),
        ),
    )

    def raw_refusal(
        request: AdapterInvocationRequest,
    ) -> StartRefusedBeforeExternalWork:
        echo = _dispatch_echo(request)
        error = AdapterErrorResult(
            adapter_id=request.adapter_id,
            error_kind="invocation_failed",
            redaction_policy_id=request.redaction_policy.policy_id,
            dispatch_echo=echo,
            diagnostics={"secret": secret},
        )
        return StartRefusedBeforeExternalWork(
            echo,
            error,
            start_refusal_diagnostic_digest(error),
        )

    runtime = _ready_runtime(tmp_path)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(raw_refusal)),
    )

    assert result.code == "session_reconciliation_required"
    assert all(
        secret.encode() not in path.read_bytes()
        for path in runtime.paths.cas_path.rglob("*")
        if path.is_file()
    )


def test_adapter_error_policy_mismatch_is_refused_without_diagnostic_cas(
    tmp_path,
) -> None:
    secret = "POLICY_MISMATCH_SECRET"
    handle = None

    def mismatched(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        outcome = AdapterErrorResult(
            adapter_id=request.adapter_id,
            error_kind="invocation_failed",
            redaction_policy_id="wrong-policy",
            dispatch_echo=_dispatch_echo(request),
            diagnostics={"secret": secret},
        )
        handle = _CapturingImmediateHandle(outcome, lambda: None)
        return replace(_success_start(request), handle=handle)

    runtime = _ready_runtime(tmp_path)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(mismatched)),
    )

    assert result.code == "session_reconciliation_required"
    assert handle is not None
    assert handle.operations == [
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    after = _load(runtime)
    assert next(iter(after.runner_sessions.values())).state == (
        "cancellation_requested"
    )
    assert after.runner_session_completions == {}
    assert all(
        secret.encode() not in path.read_bytes()
        for path in runtime.paths.cas_path.rglob("*")
        if path.is_file()
    )


def test_completion_persists_before_workflow_application(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ready_runtime(tmp_path)
    original_decide = session_completion.decide
    observed_completion = False

    def assert_completion_first(current, transition_input, context):
        nonlocal observed_completion
        if isinstance(transition_input, RunnerResultObserved):
            observed_completion = bool(current.runner_session_completions)
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_completion, "decide", assert_completion_first)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_success_config(),
    )

    assert result.code == "observation_accepted"
    assert observed_completion is True


def test_terminal_completion_requires_clean_handle_cleanup(tmp_path) -> None:
    cleaned = 0

    class CleanTerminalHandle(_ImmediateHandle):
        def cleanup(self) -> RunnerCleanupResult:
            nonlocal cleaned
            cleaned += 1
            return super().cleanup()

    def start(request: AdapterInvocationRequest) -> StartedSession:
        started = _success_start(request)
        return replace(
            started,
            handle=CleanTerminalHandle(started.handle.poll_completion()),
        )

    runtime = _ready_runtime(tmp_path)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    after = _load(runtime)

    assert result.code == "observation_accepted"
    assert cleaned == 1
    completion = next(iter(after.runner_session_completions.values()))
    assert completion.cleanup_disposition == "not_required"
    assert len(after.runner_observations) == 1


def test_terminal_completion_with_orphan_cleanup_has_no_runner_result_meaning(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run
    from millrace.substrate.runner_session_events import (
        RunnerSessionEventStore,
        runner_session_event_store_path,
    )

    secret = "ORPHAN_TERMINAL_SECRET"
    original_request = run._session_invocation_request
    monkeypatch.setattr(
        run,
        "_session_invocation_request",
        lambda *args, **kwargs: replace(
            original_request(*args, **kwargs),
            redaction_policy=RedactionPolicy(
                f"redact-{secret}",
                (secret,),
            ),
        ),
    )

    class OrphanTerminalHandle(_ImmediateHandle):
        def cleanup(self) -> RunnerCleanupResult:
            diagnostic = {"cleanup": "orphan_risk"}
            return RunnerCleanupResult(
                "orphan_risk",
                0,
                0,
                diagnostic,
                runner_cancellation_diagnostic_digest(diagnostic),
            )

    def start(request: AdapterInvocationRequest) -> StartedSession:
        started = _success_start(request)
        outcome = started.handle.poll_completion()
        assert isinstance(outcome, AdapterSuccessResult)
        return replace(
            started,
            handle=OrphanTerminalHandle(
                replace(
                    outcome,
                    redaction_policy_id=request.redaction_policy.policy_id,
                )
            ),
        )

    runtime = _ready_runtime(tmp_path)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    after = _load(runtime)

    assert result.code == "runner_session_orphan_risk"
    session = next(iter(after.runner_sessions.values()))
    completion = next(iter(after.runner_session_completions.values()))
    assert (session.state, session.cleanup_disposition) == ("lost", "orphan_risk")
    assert completion.runner_result_evidence_digest is None
    assert after.runner_observations == {}
    sidecar_path = runner_session_event_store_path(runtime.paths.db_path)
    event_store = RunnerSessionEventStore.open(sidecar_path)
    terminal = event_store.read(
        session.run_id,
        after_sequence=0,
        session_id=session.session_id,
    ).events[-1]
    event_store.close()
    assert terminal.redaction_policy_id == "redact-[REDACTED]"
    sidecar_bytes = b"".join(
        candidate.read_bytes()
        for candidate in (
            sidecar_path,
            sidecar_path.with_name(f"{sidecar_path.name}-wal"),
        )
        if candidate.is_file()
    )
    assert secret.encode() not in sidecar_bytes


def test_terminal_error_with_orphan_cleanup_blocks_same_run_retry(tmp_path) -> None:
    class OrphanErrorHandle(_ImmediateHandle):
        def cleanup(self) -> RunnerCleanupResult:
            diagnostic = {"cleanup": "orphan_risk"}
            return RunnerCleanupResult(
                "orphan_risk",
                0,
                0,
                diagnostic,
                runner_cancellation_diagnostic_digest(diagnostic),
            )

    def start(request: AdapterInvocationRequest) -> StartedSession:
        echo = _dispatch_echo(request)
        outcome = AdapterErrorResult.from_unredacted(
            adapter_id=request.adapter_id,
            error_kind="timeout",
            dispatch_echo=echo,
            redaction_policy=request.redaction_policy,
        )
        return _started_session(request, OrphanErrorHandle(outcome))

    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    orphaned = _load(runtime)
    retry = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(_RecordingAdapter(_success_start)),
    )
    after = _load(runtime)

    assert first.code == "runner_session_orphan_risk"
    assert retry.code == "runner_session_orphan_risk"
    completion = next(iter(after.runner_session_completions.values()))
    assert completion.runner_result_evidence_digest is None
    assert after.runner_observations == {}
    assert after.runner_sessions == orphaned.runner_sessions
    assert after.runs == orphaned.runs
