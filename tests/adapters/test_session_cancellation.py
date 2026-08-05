from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cli.test_cli_bounded_execution_unit import (
    _load,
    _ready_state_with_selected_codex_authority,
    _runtime,
)
from millrace.adapters.cli import (
    session_cancellation,
    session_completion,
    session_coordinator,
)
from millrace.adapters.cli import (
    status as session_status,
)
from millrace.adapters.cli.run import (
    run_bounded_execution_unit,
)
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterLocalConfig,
    DispatchEcho,
    RedactionPolicy,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    StartedSession,
    StartIndeterminate,
    runner_cancellation_diagnostic_digest,
)
from millrace.contracts.transition import (
    RequestRunnerSessionCancellation,
)
from support.runner_sessions import (
    _CancellingHandle,
    _CapturingImmediateHandle,
    _config,
    _dispatch_echo,
    _mismatched_echo,
    _ready_runtime,
    _RecordingAdapter,
    _SequenceHandle,
    _started_session,
    _success_outcome,
    _success_start,
)


def _ready_codex_runtime(tmp_path):
    state, _fingerprint = _ready_state_with_selected_codex_authority()
    return _runtime(tmp_path, state)


def test_pending_handle_is_polled_until_terminal_outcome(tmp_path) -> None:
    handle: _SequenceHandle | None = None

    def pending_then_success(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        started = _success_start(request)
        handle = _SequenceHandle([None, started.handle.poll_completion()])
        return replace(started, handle=handle)

    adapter = _RecordingAdapter(pending_then_success)
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))

    assert result.code == "observation_accepted"
    assert handle is not None
    assert handle.polls == 2


def test_prestart_cancellation_persists_typed_diagnostic_after_restart(
    tmp_path,
) -> None:
    from millrace.adapters.cli.context import OpenRuntimeContext
    from millrace.contracts.runner import RUNNER_SESSION_COMPLETION_DIAGNOSTIC_MAX_BYTES
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    runtime = _ready_codex_runtime(tmp_path)
    adapter = _RecordingAdapter(_success_start)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(adapter),
        daemon_stop_requested=lambda: True,
    )
    paths = runtime.paths
    runtime.close()

    reopened = OpenRuntimeContext(
        paths=paths,
        store=SQLiteRuntimeStore.open(paths.db_path),
        cas_store=ContentAddressedByteStore(paths.cas_path),
    )
    try:
        after = _load(reopened)
        session = next(iter(after.runner_sessions.values()))
        completion = after.runner_session_completions[session.session_id]
        diagnostic, diagnostic_status = session_status._load_completion_diagnostic(
            reopened,
            after,
            session.session_id,
            completion.diagnostic_digest,
        )
        raw = reopened.cas_store.get_bytes(completion.diagnostic_digest)
    finally:
        reopened.close()

    assert result.code == "adapter_failure"
    assert diagnostic_status == "available"
    assert diagnostic == {"external_start": False}
    assert len(raw) <= RUNNER_SESSION_COMPLETION_DIAGNOSTIC_MAX_BYTES
    assert adapter.requests == []


def test_durable_operator_cancellation_is_observed_and_cleaned_up(
    tmp_path,
) -> None:
    runtime = None
    handle: _CancellingHandle | None = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _CancellingHandle(runtime, request)
        return _started_session(request, handle)

    adapter = _RecordingAdapter(start)
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "cancelled"
    assert handle is not None
    assert handle.operations == ["cooperative_cancel", "transport_cleanup"]
    request = next(iter(after.runner_session_cancellation_requests.values()))
    assert (request.reason, request.source_kind, request.primary) == (
        "operator_cancel_work",
        "operator",
        True,
    )
    attempts = sorted(
        after.runner_session_cancellation_attempts.values(),
        key=lambda item: item.sequence,
    )
    assert [attempt.operation for attempt in attempts] == [
        "cooperative_cancel",
        "transport_cleanup",
    ]
    assert json.loads(
        runtime.cas_store.get_bytes(attempts[0].bounded_diagnostic_digest)
    ) == {"operation": "cooperative_cancel"}
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "interrupted"
    assert session.cleanup_disposition == "complete"
    completion = after.runner_session_completions[session.session_id]
    assert completion.primary_cancellation_request_id == request.request_id
    assert completion.cancel_requested_at == request.requested_at


@pytest.mark.parametrize(
    ("ready_after", "expected_operations"),
    (
        ("terminate", ["cooperative_cancel", "terminate", "transport_cleanup"]),
        (
            "kill",
            ["cooperative_cancel", "terminate", "kill", "transport_cleanup"],
        ),
    ),
)
def test_cancellation_escalates_in_order(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    ready_after: str,
    expected_operations: list[str],
) -> None:
    runtime = None
    handle: _CancellingHandle | None = None
    monotonic_value = 0.0

    def monotonic() -> float:
        nonlocal monotonic_value
        monotonic_value += 10.0
        return monotonic_value

    monkeypatch.setattr(session_cancellation, "_monotonic", monotonic)
    monkeypatch.setattr(session_cancellation, "_sleep", lambda _value: None)

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _CancellingHandle(
            runtime,
            request,
            cooperative_result="timed_out",
            ready_after=ready_after,
        )
        return _started_session(request, handle)

    adapter = _RecordingAdapter(start)
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))

    assert result.adapter_error_kind == "cancelled"
    assert handle is not None
    assert handle.operations == expected_operations


def test_normal_completion_can_win_after_durable_cancellation(tmp_path) -> None:
    runtime = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        return _started_session(
            request,
            _CancellingHandle(runtime, request, completion_race=True),
        )

    adapter = _RecordingAdapter(start)
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "observation_accepted"
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "completed"
    completion = after.runner_session_completions[session.session_id]
    primary = next(iter(after.runner_session_cancellation_requests.values()))
    assert completion.primary_cancellation_request_id == primary.request_id


def test_unsupported_cooperative_cancel_can_still_finish_cleanly(
    tmp_path,
) -> None:
    runtime = None
    handle: _CancellingHandle | None = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _CancellingHandle(
            runtime,
            request,
            cooperative_result="unsupported",
        )
        return _started_session(request, handle)

    adapter = _RecordingAdapter(start)
    runtime = _ready_runtime(tmp_path)

    run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    attempt = min(
        after.runner_session_cancellation_attempts.values(),
        key=lambda item: item.sequence,
    )
    assert (attempt.operation, attempt.result) == (
        "cooperative_cancel",
        "unsupported",
    )
    assert next(iter(after.runner_sessions.values())).cleanup_disposition == (
        "complete"
    )


def test_stale_prior_session_cancellation_cannot_cancel_retry(tmp_path) -> None:
    runtime = None

    def first_start(request: AdapterInvocationRequest) -> StartedSession:
        return _started_session(request, _CancellingHandle(runtime, request))

    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(first_start)),
    )
    second = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(_RecordingAdapter(_success_start)),
    )
    after = _load(runtime)

    assert second.code == "observation_accepted"
    sessions = sorted(
        after.runner_sessions.values(),
        key=lambda item: item.dispatch_generation,
    )
    assert [item.state for item in sessions] == ["interrupted", "completed"]
    cancellation = next(iter(after.runner_session_cancellation_requests.values()))
    assert cancellation.session_id == sessions[0].session_id


def test_racing_cancellation_requests_keep_first_request_primary(
    tmp_path,
) -> None:
    runtime = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        return _started_session(
            request,
            _CancellingHandle(
                runtime,
                request,
                secondary_on_cancel=("operator-cancel-2", "second-operator"),
            ),
        )

    runtime = _ready_runtime(tmp_path)
    run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    after = _load(runtime)

    requests = sorted(
        after.runner_session_cancellation_requests.values(),
        key=lambda item: item.request_order,
    )
    assert [(item.request_id, item.primary) for item in requests] == [
        ("operator-cancel-1", True),
        ("operator-cancel-2", False),
    ]
    completion = next(iter(after.runner_session_completions.values()))
    assert completion.primary_cancellation_request_id == "operator-cancel-1"
    assert {
        attempt.request_id
        for attempt in after.runner_session_cancellation_attempts.values()
    } == {"operator-cancel-1"}


def test_secondary_cancellation_is_preserved_while_terminating(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = None
    monotonic_value = 0.0

    def monotonic() -> float:
        nonlocal monotonic_value
        monotonic_value += 10
        return monotonic_value

    monkeypatch.setattr(session_cancellation, "_monotonic", monotonic)
    monkeypatch.setattr(session_cancellation, "_sleep", lambda _value: None)

    def start(request: AdapterInvocationRequest) -> StartedSession:
        return _started_session(
            request,
            _CancellingHandle(
                runtime,
                request,
                cooperative_result="timed_out",
                ready_after="terminate",
                secondary_on_terminate=(
                    "operator-cancel-terminating",
                    "second-operator",
                ),
            ),
        )

    runtime = _ready_runtime(tmp_path)
    run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    after = _load(runtime)
    requests = sorted(
        after.runner_session_cancellation_requests.values(),
        key=lambda item: item.request_order,
    )
    assert [(item.request_id, item.primary) for item in requests] == [
        ("operator-cancel-1", True),
        ("operator-cancel-terminating", False),
    ]


@pytest.mark.parametrize(
    "request_ids",
    (
        ("concurrent-same", "concurrent-same"),
        ("concurrent-first", "concurrent-second"),
    ),
)
def test_concurrent_cancellation_requests_retry_stale_snapshots(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    request_ids: tuple[str, str],
) -> None:
    from millrace.adapters.cli.context import OpenRuntimeContext
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    def indeterminate(request: AdapterInvocationRequest) -> StartIndeterminate:
        return StartIndeterminate(
            _dispatch_echo(request),
            None,
            "sha256:" + "a" * 64,
        )

    runtime = _ready_runtime(tmp_path)
    started = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(indeterminate)),
    )
    paths = runtime.paths
    runtime.close()

    barrier = threading.Barrier(2)
    local = threading.local()
    original = session_completion._persist_transition

    def synchronize_first_request(current_runtime, transition):
        if isinstance(transition, RequestRunnerSessionCancellation) and not getattr(
            local, "waited", False
        ):
            local.waited = True
            barrier.wait(timeout=5)
        return original(current_runtime, transition)

    monkeypatch.setattr(
        session_completion,
        "_persist_transition",
        synchronize_first_request,
    )
    results = []
    failures = []

    def request(request_id: str) -> None:
        worker = OpenRuntimeContext(
            paths=paths,
            store=SQLiteRuntimeStore.open(paths.db_path),
            cas_store=ContentAddressedByteStore(paths.cas_path),
        )
        try:
            results.append(
                session_coordinator.request_operator_cancellation(
                    worker,
                    run_id=started.run_id,
                    request_id=request_id,
                    actor_id="operator",
                )
            )
        except Exception as exc:
            failures.append(exc)
        finally:
            worker.close()

    threads = tuple(
        threading.Thread(target=request, args=(request_id,))
        for request_id in request_ids
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert all(result.accepted for result in results)
    reopened = OpenRuntimeContext(
        paths=paths,
        store=SQLiteRuntimeStore.open(paths.db_path),
        cas_store=ContentAddressedByteStore(paths.cas_path),
    )
    try:
        after = _load(reopened)
    finally:
        reopened.close()
    requests = sorted(
        after.runner_session_cancellation_requests.values(),
        key=lambda item: item.request_order,
    )
    assert [request.request_order for request in requests] == list(
        range(1, len(set(request_ids)) + 1)
    )
    assert sum(request.primary for request in requests) == 1


def test_orphan_risk_cleanup_always_completes_lost(tmp_path) -> None:
    runtime = None

    class OrphanHandle(_CancellingHandle):
        def cleanup(self) -> RunnerCleanupResult:
            self.operations.append("transport_cleanup")
            diagnostic = {"cleanup": "orphan_risk"}
            return RunnerCleanupResult(
                "orphan_risk",
                103,
                103,
                diagnostic,
                runner_cancellation_diagnostic_digest(diagnostic),
            )

    def start(request: AdapterInvocationRequest) -> StartedSession:
        return _started_session(request, OrphanHandle(runtime, request))

    runtime = _ready_runtime(tmp_path)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    after = _load(runtime)

    assert result.code == "runner_session_orphan_risk"
    session = next(iter(after.runner_sessions.values()))
    assert (session.state, session.cleanup_disposition) == ("lost", "orphan_risk")
    assert len(after.runner_session_completions) == 1


def test_mislabeled_operation_is_persisted_failed_under_expected_phase(
    tmp_path,
) -> None:
    runtime = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        return _started_session(
            request,
            _CancellingHandle(runtime, request, mislabeled_cooperative=True),
        )

    runtime = _ready_runtime(tmp_path)
    run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    after = _load(runtime)
    first = min(
        after.runner_session_cancellation_attempts.values(),
        key=lambda item: item.sequence,
    )
    assert (first.operation, first.result) == ("cooperative_cancel", "failed")


def test_attempt_diagnostic_is_redacted_and_oversize_is_summarized(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run

    runtime = None
    secret = "ATTEMPT_SECRET"

    class DiagnosticHandle(_CancellingHandle):
        def request_cancel(self) -> RunnerCancellationOperationResult:
            self._requested = True
            diagnostic = {"secret": secret, "blob": "x" * 20_000}
            return RunnerCancellationOperationResult(
                "cooperative_cancel",
                "succeeded",
                100,
                100,
                diagnostic,
                runner_cancellation_diagnostic_digest(diagnostic),
            )

    def start(request: AdapterInvocationRequest) -> StartedSession:
        return _started_session(request, DiagnosticHandle(runtime, request))

    original = run._session_invocation_request

    def secret_policy_request(*args: object, **kwargs: object):
        return replace(
            original(*args, **kwargs),
            redaction_policy=RedactionPolicy("test", (secret,)),
        )

    monkeypatch.setattr(run, "_session_invocation_request", secret_policy_request)
    runtime = _ready_runtime(tmp_path)
    run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    after = _load(runtime)
    first = min(
        after.runner_session_cancellation_attempts.values(),
        key=lambda item: item.sequence,
    )
    payload = runtime.cas_store.get_bytes(first.bounded_diagnostic_digest)
    assert secret.encode() not in payload
    assert json.loads(payload)["truncated"] is True


def test_attempt_diagnostic_redaction_failure_is_bounded_safe_content() -> None:
    class BrokenPolicy:
        def redact_authority_value(self, _value: object) -> object:
            raise RuntimeError("secret must not escape")

    from millrace.adapters.cli import session_diagnostics

    payload = session_diagnostics._bounded_session_diagnostic_bytes(
        {"secret": "must-not-persist"},
        redaction_policy=BrokenPolicy(),
    )
    assert json.loads(payload) == {"redaction_failed": True}
    assert b"must-not-persist" not in payload


def test_throwing_poll_during_cancellation_still_cleans_and_completes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = None
    monotonic_value = 0.0

    def monotonic() -> float:
        nonlocal monotonic_value
        monotonic_value += 10
        return monotonic_value

    monkeypatch.setattr(session_cancellation, "_monotonic", monotonic)
    monkeypatch.setattr(session_cancellation, "_sleep", lambda _value: None)

    def start(request: AdapterInvocationRequest) -> StartedSession:
        handle = _CancellingHandle(runtime, request, throw_after_request=True)
        return _started_session(request, handle)

    runtime = _ready_runtime(tmp_path)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    after = _load(runtime)
    session = next(iter(after.runner_sessions.values()))

    assert result.adapter_error_kind == "cancelled"
    assert (session.state, session.cleanup_disposition) == (
        "interrupted",
        "complete",
    )
    assert session.session_id in after.runner_session_completions


@pytest.mark.parametrize(
    "malformed_after",
    ("cooperative_cancel", "terminate", "kill"),
)
def test_malformed_poll_during_cancellation_is_refused_then_cleaned(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    malformed_after: str,
) -> None:
    runtime = None
    handle = None
    monotonic_value = 0.0

    def monotonic() -> float:
        nonlocal monotonic_value
        monotonic_value += 10
        return monotonic_value

    monkeypatch.setattr(session_cancellation, "_monotonic", monotonic)
    monkeypatch.setattr(session_cancellation, "_sleep", lambda _value: None)

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _CancellingHandle(
            runtime,
            request,
            cooperative_result="timed_out",
            ready_after="never",
            malformed_after=malformed_after,
        )
        return _started_session(request, handle)

    runtime = _ready_runtime(tmp_path)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    after = _load(runtime)

    assert result.adapter_error_kind == "cancelled"
    assert handle is not None
    assert handle.operations == [
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    attempts = sorted(
        after.runner_session_cancellation_attempts.values(),
        key=lambda item: item.sequence,
    )
    assert [attempt.operation for attempt in attempts] == handle.operations
    assert len(after.runner_session_completions) == 1
    completion = next(iter(after.runner_session_completions.values()))
    assert (
        completion.terminal_state,
        completion.exit_kind,
        completion.cleanup_disposition,
    ) == ("interrupted", "cancelled", "complete")
    refusals = [
        refusal
        for refusal in after.refusals
        if refusal.input_kind == "workflow.refuse_runner_session_signal"
    ]
    assert len(refusals) == 1
    assert refusals[0].reason == "runner_session_reconciliation_contradiction"
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


def test_forever_pending_handle_times_out_then_cleans_up(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle: _SequenceHandle | None = None

    def pending(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        started = _success_start(request)
        handle = _SequenceHandle([])
        return replace(started, handle=handle)

    monotonic_value = 0.0

    def advancing_monotonic() -> float:
        nonlocal monotonic_value
        monotonic_value += 10.0
        return monotonic_value

    monkeypatch.setattr(
        session_cancellation,
        "_monotonic",
        advancing_monotonic,
    )
    monkeypatch.setattr(
        session_coordinator,
        "_monotonic",
        advancing_monotonic,
    )
    monkeypatch.setattr(session_cancellation, "_sleep", lambda _value: None)
    monkeypatch.setattr(session_coordinator, "_sleep", lambda _value: None)
    adapter = _RecordingAdapter(pending)
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "cancelled"
    assert handle is not None
    assert handle.polls >= 1
    assert next(iter(after.runner_sessions.values())).state == "interrupted"
    cancellation = next(iter(after.runner_session_cancellation_requests.values()))
    assert (cancellation.reason, cancellation.source_kind) == (
        "runner_timeout",
        "runtime",
    )


def test_malformed_handle_outcome_is_cleaned_and_requires_reconciliation(
    tmp_path,
) -> None:
    handle = None

    class _MalformedOutcomeHandle(_CancellingHandle):
        def __init__(self, request: AdapterInvocationRequest) -> None:
            super().__init__(None, request)

        def poll_completion(self) -> object:
            return object()

    def malformed_outcome(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _MalformedOutcomeHandle(request)
        return replace(
            _success_start(request),
            handle=handle,
        )

    adapter = _RecordingAdapter(malformed_outcome)
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "session_reconciliation_required"
    assert handle is not None
    assert handle.operations == [
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    assert next(iter(after.runner_sessions.values())).state == "cancellation_requested"
    assert after.runner_session_completions == {}


@pytest.mark.parametrize("request_accepted", (True, False))
def test_poll_exception_without_primary_cancellation_cleans_handle(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    request_accepted: bool,
) -> None:
    handle = None

    class PollFailureHandle(_CancellingHandle):
        def poll_completion(self) -> AdapterInvocationOutcome | None:
            raise RuntimeError("poll failed")

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = PollFailureHandle(None, request)
        return replace(_success_start(request), handle=handle)

    request_result = lambda *_args, **_kwargs: SimpleNamespace(  # noqa: E731
        accepted=request_accepted
    )
    monkeypatch.setattr(
        session_cancellation,
        "_request_cancellation",
        request_result,
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
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    after = _load(runtime)
    assert next(iter(after.runner_sessions.values())).state == "running"
    assert after.runner_session_completions == {}


def test_terminal_completion_persistence_refusal_cleans_handle(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _CapturingImmediateHandle(
            _success_outcome(request),
            lambda: None,
        )
        return replace(_success_start(request), handle=handle)

    monkeypatch.setattr(
        session_completion,
        "_persist_completion_record",
        lambda *_args, **_kwargs: None,
    )
    runtime = _ready_runtime(tmp_path)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )

    assert result.code == "session_reconciliation_required"
    assert handle is not None
    assert handle.operations == ["transport_cleanup"]
    after = _load(runtime)
    assert next(iter(after.runner_sessions.values())).state == "running"
    assert after.runner_session_completions == {}
    assert after.runner_observations == {}


def test_cancel_cleanup_is_idempotently_repeated_when_completion_refuses(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = None
    handle = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _CancellingHandle(runtime, request)
        return replace(_success_start(request), handle=handle)

    refuse_completion = lambda *_args, **_kwargs: None  # noqa: E731
    monkeypatch.setattr(
        session_completion,
        "_persist_completion_record",
        refuse_completion,
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
        "transport_cleanup",
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    after = _load(runtime)
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "cancellation_requested"
    assert session.cleanup_disposition == "pending"
    assert after.runner_session_completions == {}
    assert after.runner_observations == {}


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
@pytest.mark.parametrize(
    "fault_kind",
    (
        "malformed_poll",
        "poll_exception_accepted",
        "poll_exception_refused",
        "dispatch_mismatch",
        "evidence_refusal",
        "policy_refusal",
        "completion_persistence_refusal",
    ),
)
def test_nonterminal_return_fault_cleans_live_subprocess_before_return(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    fault_kind: str,
) -> None:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig

    heartbeat = tmp_path / "malformed-live-heartbeat.txt"
    process_pid = tmp_path / "malformed-live.pid"
    wrapper = (
        "import os,pathlib,time\n"
        f"heartbeat=pathlib.Path({str(heartbeat)!r})\n"
        f"pathlib.Path({str(process_pid)!r}).write_text(str(os.getpid()))\n"
        "while True:\n"
        " heartbeat.write_text(str(time.time()))\n"
        " time.sleep(0.03)\n"
    )
    captured_handles = []

    class FaultingLiveHandle:
        def __init__(self, inner, request: AdapterInvocationRequest) -> None:
            self._inner = inner
            self._request = request

        def poll_completion(self) -> object:
            deadline = time.time() + 2
            while not heartbeat.exists() and time.time() < deadline:
                time.sleep(0.01)
            if fault_kind == "malformed_poll":
                return object()
            if fault_kind.startswith("poll_exception"):
                raise RuntimeError("poll failed")
            if fault_kind == "dispatch_mismatch":
                return _success_outcome(
                    self._request,
                    dispatch_echo=_mismatched_echo(self._request),
                )
            if fault_kind == "evidence_refusal":
                return replace(_success_outcome(self._request), marker=None)
            if fault_kind == "policy_refusal":
                return AdapterErrorResult(
                    adapter_id=self._request.adapter_id,
                    error_kind="invocation_failed",
                    redaction_policy_id="wrong-policy",
                    dispatch_echo=DispatchEcho.from_dispatch_envelope(
                        self._request.dispatch_envelope,
                        correlation_id=self._request.correlation_id,
                        selected_adapter_kind=self._request.selected_adapter_kind,
                    ),
                    diagnostics={},
                )
            self._inner.kill()
            outcome = None
            while outcome is None and time.time() < deadline:
                outcome = self._inner.poll_completion()
                if outcome is None:
                    time.sleep(0.01)
            assert outcome is not None
            return outcome

        def request_cancel(self):
            return self._inner.request_cancel()

        def terminate(self):
            return self._inner.terminate()

        def kill(self):
            return self._inner.kill()

        def cleanup(self):
            return self._inner.cleanup()

    class MalformedCodexAdapter(CodexAdapter):
        def start_session(self, request):
            started = super().start_session(request)
            if isinstance(started, StartedSession):
                captured_handles.append(started.handle)
                return replace(
                    started,
                    handle=FaultingLiveHandle(started.handle, request),
                )
            return started

    adapter = MalformedCodexAdapter(
        CodexAdapterConfig(
            adapter_id="codex-default",
            wrapper_mode="offline_fake",
            wrapper_argv=(sys.executable, "-c", wrapper),
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_input_bundle_bytes=16384,
            max_stdout_bytes=8192,
            max_stderr_diagnostic_bytes=512,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    )
    runtime = _ready_codex_runtime(tmp_path)
    if fault_kind.startswith("poll_exception"):
        request_result = lambda *_args, **_kwargs: SimpleNamespace(  # noqa: E731
            accepted=fault_kind.endswith("accepted")
        )
        monkeypatch.setattr(
            session_cancellation,
            "_request_cancellation",
            request_result,
        )
    if fault_kind == "completion_persistence_refusal":
        monkeypatch.setattr(
            session_completion,
            "_persist_completion_record",
            lambda *_args, **_kwargs: None,
        )
    try:
        result = run_bounded_execution_unit(
            runtime,
            local_config=AdapterLocalConfig(adapters={"codex": adapter}),
        )

        assert result.code == "session_reconciliation_required"
        assert heartbeat.exists()
        stable = heartbeat.read_text()
        time.sleep(0.15)
        assert heartbeat.read_text() == stable
        after = _load(runtime)
        session = next(iter(after.runner_sessions.values()))
        assert session.state == (
            "running"
            if fault_kind.startswith("poll_exception")
            or fault_kind == "completion_persistence_refusal"
            else "cancellation_requested"
        )
        assert after.runner_session_completions == {}
        assert after.runner_observations == {}
    finally:
        for live_handle in captured_handles:
            live_handle.kill()
            live_handle.cleanup()
        if process_pid.exists():
            try:
                os.killpg(int(process_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
