from __future__ import annotations

import json
import os
import signal
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cli.test_cli_bounded_execution_unit import (
    _codex_error_config,
    _codex_success_config,
    _codex_success_wrapper,
    _codex_timeout_config,
    _load,
    _ready_state,
    _reopen_runtime,
    _runtime,
)
from kernel.kernel_ping_scenarios import task_artifact_payload
from millrace.adapters.cli import session_coordinator
from millrace.adapters.cli.run import (
    reconcile_pending_runner_sessions,
    run_bounded_execution_unit,
)
from millrace.adapters.runner_contract import (
    START_REFUSAL_DIAGNOSTIC_MAX_BYTES,
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterSuccessResult,
    CleanupPending,
    Contradiction,
    DispatchEcho,
    RedactionPolicy,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    Terminal,
    Unsupported,
    VerifiedLive,
    runner_cancellation_diagnostic_digest,
    start_refusal_diagnostic_bytes,
    start_refusal_diagnostic_digest,
)
from millrace.contracts import QueueFamilyId
from millrace.contracts.runner import (
    runner_result_evidence_from_payload,
    runner_session_locator_bytes,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    EnqueueWork,
    RequestRunnerSessionCancellation,
    RunnerResultObserved,
)
from millrace.operator.prompt_material import SelectedAssetMaterializationError
from support.kernel_ping import apply_accepted_input, kernel_ping_context


class _ImmediateHandle:
    def __init__(self, outcome: AdapterInvocationOutcome) -> None:
        self._outcome: AdapterInvocationOutcome | None = outcome

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        outcome = self._outcome
        self._outcome = None
        return outcome

    def request_cancel(self) -> RunnerCancellationOperationResult:
        return self._unsupported("cooperative_cancel")

    def terminate(self) -> RunnerCancellationOperationResult:
        return self._unsupported("terminate")

    def kill(self) -> RunnerCancellationOperationResult:
        return self._unsupported("kill")

    def cleanup(self) -> RunnerCleanupResult:
        diagnostic = {"cleanup": "not_required"}
        return RunnerCleanupResult(
            "not_required",
            0,
            0,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    @staticmethod
    def _unsupported(operation: str) -> RunnerCancellationOperationResult:
        diagnostic = {"operation": operation}
        return RunnerCancellationOperationResult(
            operation,
            "unsupported",
            0,
            0,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


class _SequenceHandle(_ImmediateHandle):
    def __init__(self, outcomes: list[AdapterInvocationOutcome | None]) -> None:
        self._outcomes = outcomes
        self.polls = 0

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        self.polls += 1
        return self._outcomes.pop(0) if self._outcomes else None


class _CancellingHandle(_ImmediateHandle):
    def __init__(
        self,
        runtime,
        request: AdapterInvocationRequest,
        *,
        cooperative_result: str = "succeeded",
    ) -> None:
        self._runtime = runtime
        self._request = request
        self._requested = False
        self.operations: list[str] = []
        self._cooperative_result = cooperative_result

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        if not self._requested:
            self._requested = True
            result = session_coordinator.request_operator_cancellation(
                self._runtime,
                run_id=self._request.dispatch_envelope.run_id,
                request_id="operator-cancel-1",
                actor_id="operator",
            )
            assert result.code == "runner_session_cancel_requested"
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
        self.operations.append("cooperative_cancel")
        diagnostic = {"operation": "cooperative_cancel"}
        return RunnerCancellationOperationResult(
            "cooperative_cancel",
            self._cooperative_result,
            100,
            100,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    def terminate(self) -> RunnerCancellationOperationResult:
        self.operations.append("terminate")
        diagnostic = {"operation": "terminate"}
        return RunnerCancellationOperationResult(
            "terminate",
            "succeeded",
            101,
            101,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    def kill(self) -> RunnerCancellationOperationResult:
        self.operations.append("kill")
        diagnostic = {"operation": "kill"}
        return RunnerCancellationOperationResult(
            "kill",
            "succeeded",
            102,
            102,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    def cleanup(self) -> RunnerCleanupResult:
        self.operations.append("transport_cleanup")
        diagnostic = {"cleanup": "complete"}
        return RunnerCleanupResult(
            "complete",
            103,
            103,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


class _EscalatingCancellingHandle(_CancellingHandle):
    def __init__(
        self,
        runtime,
        request: AdapterInvocationRequest,
        *,
        ready_after: str,
    ) -> None:
        super().__init__(runtime, request, cooperative_result="timed_out")
        self._ready_after = ready_after

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        if not self._requested:
            return super().poll_completion()
        if self._ready_after not in self.operations:
            return None
        return super().poll_completion()

    def terminate(self) -> RunnerCancellationOperationResult:
        self.operations.append("terminate")
        diagnostic = {"operation": "terminate"}
        return RunnerCancellationOperationResult(
            "terminate",
            "succeeded" if self._ready_after == "terminate" else "timed_out",
            101,
            101,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


class _CompletionRaceHandle(_CancellingHandle):
    def poll_completion(self) -> AdapterInvocationOutcome | None:
        if not self._requested:
            super().poll_completion()
            return None
        return _success_outcome(self._request)


class _SecondaryRaceHandle(_CancellingHandle):
    def request_cancel(self) -> RunnerCancellationOperationResult:
        secondary = session_coordinator.request_operator_cancellation(
            self._runtime,
            run_id=self._request.dispatch_envelope.run_id,
            request_id="operator-cancel-2",
            actor_id="second-operator",
        )
        assert secondary.accepted
        return super().request_cancel()


class _TerminatingSecondaryHandle(_EscalatingCancellingHandle):
    def terminate(self) -> RunnerCancellationOperationResult:
        secondary = session_coordinator.request_operator_cancellation(
            self._runtime,
            run_id=self._request.dispatch_envelope.run_id,
            request_id="operator-cancel-terminating",
            actor_id="second-operator",
        )
        assert secondary.accepted
        return super().terminate()


class _MislabeledOperationHandle(_CancellingHandle):
    def request_cancel(self) -> RunnerCancellationOperationResult:
        self._requested = True
        diagnostic = {"operation": "kill", "malicious": True}
        return RunnerCancellationOperationResult(
            "kill",
            "succeeded",
            100,
            100,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


class _ThrowingCancellationPollHandle(_CancellingHandle):
    def poll_completion(self) -> AdapterInvocationOutcome | None:
        if not self._requested:
            return super().poll_completion()
        raise RuntimeError("poll transport failed")


class _MalformedCancellationPollHandle(_CancellingHandle):
    def __init__(
        self,
        runtime,
        request: AdapterInvocationRequest,
        *,
        malformed_after: str,
    ) -> None:
        super().__init__(runtime, request, cooperative_result="timed_out")
        self._malformed_after = malformed_after
        self._malformed_emitted = False

    def poll_completion(self) -> object:
        if not self._requested:
            return super().poll_completion()
        if self._malformed_after in self.operations and not self._malformed_emitted:
            self._malformed_emitted = True
            return object()
        if self._malformed_emitted:
            return _success_outcome(self._request)
        return None

    def terminate(self) -> RunnerCancellationOperationResult:
        result = super().terminate()
        return replace(result, result="timed_out")


class _CapturingImmediateHandle(_ImmediateHandle):
    def __init__(
        self,
        outcome: AdapterInvocationOutcome,
        capture: Callable[[], None],
    ) -> None:
        super().__init__(outcome)
        self._capture = capture
        self.operations: list[str] = []

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        self._capture()
        return super().poll_completion()

    def request_cancel(self) -> RunnerCancellationOperationResult:
        self.operations.append("cooperative_cancel")
        return super().request_cancel()

    def terminate(self) -> RunnerCancellationOperationResult:
        self.operations.append("terminate")
        return super().terminate()

    def kill(self) -> RunnerCancellationOperationResult:
        self.operations.append("kill")
        return super().kill()

    def cleanup(self) -> RunnerCleanupResult:
        self.operations.append("transport_cleanup")
        return super().cleanup()


class _RecordingAdapter:
    adapter_kind = "codex"

    def __init__(
        self,
        start: Callable[[AdapterInvocationRequest], object],
        reconcile: Callable[[object], object] | None = None,
    ) -> None:
        self._start = start
        self._reconcile = reconcile
        self.requests: list[AdapterInvocationRequest] = []
        self.reconcile_requests: list[object] = []

    def start_session(self, request: AdapterInvocationRequest) -> object:
        self.requests.append(request)
        return self._start(request)

    def reconcile_session(self, request: object) -> object:
        self.reconcile_requests.append(request)
        if self._reconcile is not None:
            return self._reconcile(request)
        invocation = request.invocation_request
        return Unsupported(
            DispatchEcho.from_dispatch_envelope(
                invocation.dispatch_envelope,
                correlation_id=invocation.correlation_id,
                selected_adapter_kind=invocation.selected_adapter_kind,
            )
        )


def _success_start(request: AdapterInvocationRequest) -> StartedSession:
    echo = DispatchEcho.from_dispatch_envelope(
        request.dispatch_envelope,
        correlation_id=request.correlation_id,
        selected_adapter_kind=request.selected_adapter_kind,
    )
    outcome = AdapterSuccessResult.from_unredacted(
        adapter_id=request.adapter_id,
        dispatch_echo=echo,
        redaction_policy=request.redaction_policy,
        marker="TASK_COMPLETE",
        observation_payload_candidate={"summary": "ok"},
        artifact_payload_candidate=task_artifact_payload(),
    )
    return StartedSession(
        echo,
        _ImmediateHandle(outcome),
        f"fake:{request.session_id}",
        {},
    )


def _refused_start(
    request: AdapterInvocationRequest,
) -> StartRefusedBeforeExternalWork:
    echo = DispatchEcho.from_dispatch_envelope(
        request.dispatch_envelope,
        correlation_id=request.correlation_id,
        selected_adapter_kind=request.selected_adapter_kind,
    )
    error = AdapterErrorResult.from_unredacted(
        adapter_id=request.adapter_id,
        error_kind="selected_authority_refused",
        dispatch_echo=echo,
        redaction_policy=request.redaction_policy,
    )
    return StartRefusedBeforeExternalWork(
        echo,
        error,
        start_refusal_diagnostic_digest(error),
    )


def _config(adapter: _RecordingAdapter) -> AdapterLocalConfig:
    return AdapterLocalConfig(adapters={"codex": adapter})


def _ready_state_with_two_activations():
    state, fingerprint = _ready_state()
    second = EnqueueWork(
        "enqueue-second",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"prompt_id": "prompt-2", "body": "Build the second proof"},
    )
    return (
        apply_accepted_input(
            state,
            second,
            kernel_ping_context(second.input_id),
        ),
        fingerprint,
    )


def _assert_single_refusal_audit(
    before,
    after,
    *,
    session_state: str,
    reason: str,
    emergency_cancellation: bool = False,
) -> None:
    assert len(after.receipts) == len(before.receipts) + (
        2 if emergency_cancellation else 1
    )
    assert len(after.refusals) == len(before.refusals) + 1
    transition_count = 2 if emergency_cancellation else 1
    assert len(after.governance_events) == (
        len(before.governance_events) + transition_count
    )
    assert len(after.traces) == len(before.traces) + transition_count
    assert next(iter(after.runner_sessions.values())).state == session_state
    if not emergency_cancellation:
        assert after.runner_sessions == before.runner_sessions
    assert after.runner_session_completions == before.runner_session_completions
    assert after.runner_observations == before.runner_observations
    assert after.artifacts == before.artifacts
    assert after.work_items == before.work_items
    assert after.activations == before.activations
    assert after.activation_routes == before.activation_routes
    assert after.closed_work_items == before.closed_work_items
    assert after.runs == before.runs
    refusal = next(
        refusal
        for refusal in after.refusals
        if refusal.record_id not in {existing.record_id for existing in before.refusals}
    )
    assert refusal.input_kind == "workflow.refuse_runner_session_signal"
    assert refusal.reason == reason


def _mismatched_echo(request: AdapterInvocationRequest) -> DispatchEcho:
    return replace(
        DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        ),
        correlation_id="stale-correlation",
    )


def _completion_signal_result(
    tmp_path,
    outcome_factory: Callable[
        [AdapterInvocationRequest],
        AdapterInvocationOutcome,
    ],
):
    snapshots = []
    handles = []
    runtime = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        handle = _CapturingImmediateHandle(
            outcome_factory(request),
            lambda: snapshots.append(_load(runtime)),
        )
        handles.append(handle)
        return StartedSession(
            echo,
            handle,
            f"fake:{request.session_id}",
            {},
        )

    adapter = _RecordingAdapter(start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    assert len(snapshots) == 1
    assert len(handles) == 1
    return result, snapshots[0], _load(runtime), handles[0]


def _success_outcome(
    request: AdapterInvocationRequest,
    *,
    dispatch_echo: DispatchEcho | None = None,
) -> AdapterSuccessResult:
    return AdapterSuccessResult.from_unredacted(
        adapter_id=request.adapter_id,
        dispatch_echo=dispatch_echo
        or DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        ),
        redaction_policy=request.redaction_policy,
        marker="TASK_COMPLETE",
        artifact_payload_candidate=task_artifact_payload(),
    )


def _error_outcome(
    request: AdapterInvocationRequest,
    *,
    dispatch_echo: DispatchEcho | None,
) -> AdapterErrorResult:
    return AdapterErrorResult.from_unredacted(
        adapter_id=request.adapter_id,
        error_kind="invocation_failed",
        dispatch_echo=dispatch_echo,
        redaction_policy=request.redaction_policy,
    )


def test_request_factory_side_effect_occurs_after_start_intent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter(_success_start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    original = session_coordinator._request_matches_current_authority
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
        session_coordinator,
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig

    heartbeat = tmp_path / "running-write-failure.txt"
    process_pid = tmp_path / "running-write-failure.pid"
    wrapper = (
        "import os,pathlib,time\n"
        f"heartbeat=pathlib.Path({str(heartbeat)!r})\n"
        f"pathlib.Path({str(process_pid)!r}).write_text(str(os.getpid()))\n"
        "while True:\n"
        " heartbeat.write_text(str(time.time()))\n"
        " time.sleep(0.03)\n"
    )
    adapter = CodexAdapter(
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        stable = heartbeat.read_text()
        time.sleep(0.15)
        assert heartbeat.read_text() == stable
    finally:
        if process_pid.exists():
            try:
                os.kill(int(process_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


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
        session_coordinator,
        "_persist_cancellation_operation",
        fail_attempt,
    )
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
        session_coordinator,
        "_persist_cancellation_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("attempt write failed")
        ),
    )
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig

    captured_handles = []

    class CapturingCodexAdapter(CodexAdapter):
        def start_session(self, request):
            started = super().start_session(request)
            if isinstance(started, StartedSession):
                captured_handles.append(started.handle)
            return started

    heartbeat = tmp_path / "live-fence-heartbeat.txt"
    process_pid = tmp_path / "live-fence.pid"
    wrapper = (
        "import os,pathlib,time\n"
        f"heartbeat=pathlib.Path({str(heartbeat)!r})\n"
        f"pathlib.Path({str(process_pid)!r}).write_text(str(os.getpid()))\n"
        "while True:\n"
        " heartbeat.write_text(str(time.time()))\n"
        " time.sleep(0.03)\n"
    )
    adapter = CapturingCodexAdapter(
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
    callback_calls = 0

    def cancel_only_after_start() -> bool:
        nonlocal callback_calls
        callback_calls += 1
        return callback_calls >= 3

    def fail_attempt(*_args, **_kwargs):
        raise RuntimeError("simulated attempt write failure")

    monkeypatch.setattr(
        session_coordinator,
        "_persist_cancellation_operation",
        fail_attempt,
    )
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        stable = heartbeat.read_text()
        time.sleep(0.15)
        assert heartbeat.read_text() == stable
        after = _load(runtime)
        session = next(iter(after.runner_sessions.values()))
        assert session.state in {"cancellation_requested", "terminating"}
        assert session.cleanup_disposition == "pending"
        assert after.runner_observations == {}
    finally:
        for live_handle in captured_handles:
            live_handle.kill()
            live_handle.cleanup()
        if process_pid.exists():
            pid = int(process_pid.read_text())
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


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
            dispatch_echo=DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            diagnostics={"secret": secret, "blob": "x" * 20_000},
        )
        return replace(_success_start(request), handle=_ImmediateHandle(outcome))

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
            dispatch_echo=DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            diagnostics={"secret": secret},
        )
        return replace(
            _success_start(request),
            handle=FailingRedactionHandle(outcome),
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig

    heartbeat = tmp_path / "coordinator-child.txt"
    child_pid = tmp_path / "coordinator-child.pid"
    child = (
        "import pathlib,time\n"
        f"path=pathlib.Path({str(heartbeat)!r})\n"
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
        "time.sleep(0.1)\n" + _codex_success_wrapper("TASK_COMPLETE")
    )
    adapter = CodexAdapter(
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        stable = heartbeat.read_text()
        time.sleep(0.15)
        assert heartbeat.read_text() == stable
    finally:
        if child_pid.exists():
            try:
                os.kill(int(child_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


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
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
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

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
            dispatch_echo=DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            diagnostics={"secret": secret},
        )
        handle = _CapturingImmediateHandle(outcome, lambda: None)
        return replace(_success_start(request), handle=handle)

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    original_decide = session_coordinator.decide
    observed_completion = False

    def assert_completion_first(current, transition_input, context):
        nonlocal observed_completion
        if isinstance(transition_input, RunnerResultObserved):
            observed_completion = bool(current.runner_session_completions)
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_coordinator, "decide", assert_completion_first)
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

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
) -> None:
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
        return replace(
            started,
            handle=OrphanTerminalHandle(started.handle.poll_completion()),
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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


def test_pending_handle_is_polled_until_terminal_outcome(tmp_path) -> None:
    handle: _SequenceHandle | None = None

    def pending_then_success(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        started = _success_start(request)
        handle = _SequenceHandle([None, started.handle.poll_completion()])
        return replace(started, handle=handle)

    adapter = _RecordingAdapter(pending_then_success)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))

    assert result.code == "observation_accepted"
    assert handle is not None
    assert handle.polls == 2


def test_durable_operator_cancellation_is_observed_and_cleaned_up(
    tmp_path,
) -> None:
    runtime = None
    handle: _CancellingHandle | None = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _CancellingHandle(runtime, request)
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            handle,
            f"fake:{request.session_id}",
            {},
        )

    adapter = _RecordingAdapter(start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
    handle: _EscalatingCancellingHandle | None = None
    monotonic_value = 0.0

    def monotonic() -> float:
        nonlocal monotonic_value
        monotonic_value += 10.0
        return monotonic_value

    monkeypatch.setattr(session_coordinator, "_monotonic", monotonic)
    monkeypatch.setattr(session_coordinator, "_sleep", lambda _value: None)

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _EscalatingCancellingHandle(
            runtime,
            request,
            ready_after=ready_after,
        )
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            handle,
            f"fake:{request.session_id}",
            {},
        )

    adapter = _RecordingAdapter(start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))

    assert result.adapter_error_kind == "cancelled"
    assert handle is not None
    assert handle.operations == expected_operations


def test_normal_completion_can_win_after_durable_cancellation(tmp_path) -> None:
    runtime = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            _CompletionRaceHandle(runtime, request),
            f"fake:{request.session_id}",
            {},
        )

    adapter = _RecordingAdapter(start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            handle,
            f"fake:{request.session_id}",
            {},
        )

    adapter = _RecordingAdapter(start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            _CancellingHandle(runtime, request),
            f"fake:{request.session_id}",
            {},
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            _SecondaryRaceHandle(runtime, request),
            f"fake:{request.session_id}",
            {},
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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

    monkeypatch.setattr(session_coordinator, "_monotonic", monotonic)
    monkeypatch.setattr(session_coordinator, "_sleep", lambda _value: None)

    def start(request: AdapterInvocationRequest) -> StartedSession:
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            _TerminatingSecondaryHandle(
                runtime,
                request,
                ready_after="terminate",
            ),
            f"fake:{request.session_id}",
            {},
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            None,
            "sha256:" + "a" * 64,
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    started = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(indeterminate)),
    )
    paths = runtime.paths
    runtime.close()

    barrier = threading.Barrier(2)
    local = threading.local()
    original = session_coordinator._persist_transition

    def synchronize_first_request(current_runtime, transition):
        if isinstance(transition, RequestRunnerSessionCancellation) and not getattr(
            local, "waited", False
        ):
            local.waited = True
            barrier.wait(timeout=5)
        return original(current_runtime, transition)

    monkeypatch.setattr(
        session_coordinator,
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
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            OrphanHandle(runtime, request),
            f"fake:{request.session_id}",
            {},
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            _MislabeledOperationHandle(runtime, request),
            f"fake:{request.session_id}",
            {},
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            DiagnosticHandle(runtime, request),
            f"fake:{request.session_id}",
            {},
        )

    original = run._session_invocation_request

    def secret_policy_request(*args: object, **kwargs: object):
        return replace(
            original(*args, **kwargs),
            redaction_policy=RedactionPolicy("test", (secret,)),
        )

    monkeypatch.setattr(run, "_session_invocation_request", secret_policy_request)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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

    payload = session_coordinator._bounded_session_diagnostic_bytes(
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

    monkeypatch.setattr(session_coordinator, "_monotonic", monotonic)
    monkeypatch.setattr(session_coordinator, "_sleep", lambda _value: None)

    def start(request: AdapterInvocationRequest) -> StartedSession:
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            _ThrowingCancellationPollHandle(runtime, request),
            f"fake:{request.session_id}",
            {},
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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

    monkeypatch.setattr(session_coordinator, "_monotonic", monotonic)
    monkeypatch.setattr(session_coordinator, "_sleep", lambda _value: None)

    def start(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        handle = _MalformedCancellationPollHandle(
            runtime,
            request,
            malformed_after=malformed_after,
        )
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            handle,
            f"fake:{request.session_id}",
            {},
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        session_coordinator,
        "_monotonic",
        advancing_monotonic,
    )
    monkeypatch.setattr(session_coordinator, "_sleep", lambda _value: None)
    adapter = _RecordingAdapter(pending)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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

    monkeypatch.setattr(
        session_coordinator,
        "_request_cancellation",
        lambda *_args, **_kwargs: SimpleNamespace(accepted=request_accepted),
    )
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
        session_coordinator,
        "_persist_completion_record",
        lambda *_args, **_kwargs: None,
    )
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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

    monkeypatch.setattr(
        session_coordinator,
        "_persist_completion_record",
        lambda *_args, **_kwargs: None,
    )
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    if fault_kind.startswith("poll_exception"):
        monkeypatch.setattr(
            session_coordinator,
            "_request_cancellation",
            lambda *_args, **_kwargs: SimpleNamespace(
                accepted=fault_kind.endswith("accepted")
            ),
        )
    if fault_kind == "completion_persistence_refusal":
        monkeypatch.setattr(
            session_coordinator,
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


def test_adapter_error_without_dispatch_echo_is_durably_audited(tmp_path) -> None:
    result, before_signal, after, handle = _completion_signal_result(
        tmp_path,
        lambda request: _error_outcome(request, dispatch_echo=None),
    )

    assert result.code == "session_reconciliation_required"
    assert handle.operations == [
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    _assert_single_refusal_audit(
        before_signal,
        after,
        session_state="cancellation_requested",
        reason="runner_session_reconciliation_contradiction",
        emergency_cancellation=True,
    )


@pytest.mark.parametrize("outcome_kind", ("success", "error"))
def test_completion_dispatch_echo_mismatch_is_durably_audited(
    tmp_path,
    outcome_kind: str,
) -> None:
    def mismatched_completion(
        request: AdapterInvocationRequest,
    ) -> AdapterInvocationOutcome:
        echo = _mismatched_echo(request)
        if outcome_kind == "success":
            return _success_outcome(request, dispatch_echo=echo)
        return _error_outcome(
            request,
            dispatch_echo=echo,
        )

    result, before_signal, after, handle = _completion_signal_result(
        tmp_path,
        mismatched_completion,
    )

    assert result.code == "session_reconciliation_required"
    assert handle.operations == [
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    _assert_single_refusal_audit(
        before_signal,
        after,
        session_state="cancellation_requested",
        reason="runner_session_authority_mismatch",
        emergency_cancellation=True,
    )


def test_evidence_conversion_refusal_is_durably_audited(tmp_path) -> None:
    result, before_signal, after, handle = _completion_signal_result(
        tmp_path,
        lambda request: replace(_success_outcome(request), marker=None),
    )

    assert result.code == "session_reconciliation_required"
    assert handle.operations == [
        "cooperative_cancel",
        "terminate",
        "kill",
        "transport_cleanup",
    ]
    _assert_single_refusal_audit(
        before_signal,
        after,
        session_state="cancellation_requested",
        reason="runner_session_reconciliation_contradiction",
        emergency_cancellation=True,
    )


@pytest.mark.parametrize("malformation", ("missing_error_echo", "mismatched_echo"))
def test_start_refusal_echo_malformation_is_durably_audited(
    tmp_path,
    malformation: str,
) -> None:
    snapshots = []
    runtime = None

    def malformed_refusal(
        request: AdapterInvocationRequest,
    ) -> StartRefusedBeforeExternalWork:
        valid_echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        error = AdapterErrorResult.from_unredacted(
            adapter_id=request.adapter_id,
            error_kind="selected_authority_refused",
            dispatch_echo=(
                None
                if malformation == "missing_error_echo"
                else _mismatched_echo(request)
            ),
            redaction_policy=request.redaction_policy,
        )
        snapshots.append(_load(runtime))
        return StartRefusedBeforeExternalWork(
            (
                valid_echo
                if malformation == "missing_error_echo"
                else _mismatched_echo(request)
            ),
            error,
            start_refusal_diagnostic_digest(error),
        )

    adapter = _RecordingAdapter(malformed_refusal)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "session_reconciliation_required"
    assert len(snapshots) == 1
    _assert_single_refusal_audit(
        snapshots[0],
        after,
        session_state="starting",
        reason=(
            "runner_session_reconciliation_contradiction"
            if malformation == "missing_error_echo"
            else "runner_session_authority_mismatch"
        ),
    )


def test_locator_is_redacted_before_bounded_cas_persistence(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run

    secret = "locator-secret"

    def start_with_secret(request: AdapterInvocationRequest) -> StartedSession:
        return replace(
            _success_start(request),
            handle_id=secret,
            durable_locator_metadata={"provider_request": secret},
        )

    adapter = _RecordingAdapter(start_with_secret)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    config = AdapterLocalConfig(
        adapters={"codex": adapter},
    )
    monkeypatch.setattr(
        run,
        "_redaction_policy_for_adapter",
        lambda *_args: RedactionPolicy("redact-default", (secret,)),
    )
    result = run_bounded_execution_unit(runtime, local_config=config)
    after = _load(runtime)
    session = next(iter(after.runner_sessions.values()))
    assert result.code == "observation_accepted"
    assert session.durable_locator_digest is not None
    locator = runtime.cas_store.get_bytes(session.durable_locator_digest)
    assert secret.encode() not in locator
    assert b"[REDACTED]" in locator
    assert b"handle_id_digest" in locator
    decoded = json.loads(locator)
    assert set(decoded) == {
        "record_kind",
        "schema_version",
        "adapter_locator",
        "handle_id_digest",
    }
    assert decoded["record_kind"] == "runner_session_coordinator_locator"
    assert decoded["schema_version"] == 1


def test_oversized_locator_remains_starting_without_adapter_completion(
    tmp_path,
) -> None:
    adapter = _RecordingAdapter(
        lambda request: replace(
            _success_start(request),
            durable_locator_metadata={"oversized": "x" * 20000},
        )
    )
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)
    session = next(iter(after.runner_sessions.values()))

    assert result.code == "session_reconciliation_required"
    assert session.state == "starting"
    assert session.durable_locator_digest is None


@pytest.mark.parametrize(
    "adapter_locator",
    (
        {"handle_id": "opaque-handle-secret"},
        {"nested": {"handle_id": "opaque-handle-secret"}},
    ),
)
def test_adapter_locator_cannot_persist_raw_handle_identity(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_locator: dict[str, object],
) -> None:
    from millrace.adapters.cli import run

    adapter = _RecordingAdapter(
        lambda request: replace(
            _success_start(request),
            durable_locator_metadata=adapter_locator,
        )
    )
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    monkeypatch.setattr(
        run,
        "_redaction_policy_for_adapter",
        lambda *_args: RedactionPolicy("redact-handle-key", ("handle_id",)),
    )

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)
    session = next(iter(after.runner_sessions.values()))
    cas_payloads = [
        path.read_bytes()
        for path in runtime.paths.cas_path.rglob("*")
        if path.is_file()
    ]

    assert result.code == "session_reconciliation_required"
    assert session.state == "starting"
    assert session.durable_locator_digest is None
    assert not any(b"opaque-handle-secret" in payload for payload in cas_payloads)
    assert after.runner_session_completions == {}
    assert after.runner_observations == {}


@pytest.mark.parametrize(
    "adapter_locator",
    (
        {"handle_id": "opaque-live-handle-123"},
        {"nested": {"handle_id": "opaque-live-handle-123"}},
    ),
)
def test_reconciled_locator_cannot_redact_away_raw_handle_identity(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_locator: dict[str, object],
) -> None:
    from millrace.adapters.cli import run

    def reconcile(request):
        invocation = request.invocation_request
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        return VerifiedLive(
            echo,
            _ImmediateHandle(_success_outcome(invocation)),
            "verified-live-handle",
            adapter_locator,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    monkeypatch.setattr(
        run,
        "_redaction_policy_for_adapter",
        lambda *_args: RedactionPolicy("redact-handle-key", ("handle_id",)),
    )
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    runtime = _reopen_runtime(runtime)

    result = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)
    cas_payloads = [
        path.read_bytes()
        for path in runtime.paths.cas_path.rglob("*")
        if path.is_file()
    ]

    assert result.code == "runner_session_reconciliation_contradiction"
    assert not any(
        b"opaque-live-handle-123" in payload for payload in cas_payloads
    )
    assert after.runner_session_completions == {}
    assert after.runner_observations == {}


def test_indeterminate_start_retains_redacted_safe_locator(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run

    secret = "locator-secret"

    def indeterminate(request: AdapterInvocationRequest) -> StartIndeterminate:
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        return StartIndeterminate(
            echo,
            {"provider_request": secret},
            "sha256:" + "d" * 64,
        )

    adapter = _RecordingAdapter(indeterminate)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    monkeypatch.setattr(
        run,
        "_redaction_policy_for_adapter",
        lambda *_args: RedactionPolicy("redact-default", (secret,)),
    )
    before = _load(runtime)
    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)
    session = next(iter(after.runner_sessions.values()))

    assert result.code == "session_reconciliation_required"
    assert session.state == "starting"
    assert session.durable_locator_digest is not None
    assert after.refusals == before.refusals
    assert not any(
        event.disposition == "refused"
        for event in after.governance_events[len(before.governance_events) :]
    )
    locator = runtime.cas_store.get_bytes(session.durable_locator_digest)
    assert secret.encode() not in locator


def test_crash_after_completion_persistence_replays_without_adapter_invocation(
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

    adapter = _RecordingAdapter(start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    original_decide = session_coordinator.decide

    def crash_before_application(current, transition_input, context):
        if isinstance(transition_input, RunnerResultObserved):
            raise RuntimeError("application crash")
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_coordinator, "decide", crash_before_application)
    with pytest.raises(RuntimeError, match="application crash"):
        run_bounded_execution_unit(runtime, local_config=_config(adapter))
    persisted = _load(runtime)
    assert handle is not None
    assert handle.operations == ["transport_cleanup"]
    assert len(persisted.runner_session_completions) == 1
    assert persisted.runner_observations == {}

    monkeypatch.setattr(session_coordinator, "decide", original_decide)
    runtime = _reopen_runtime(runtime)
    replay = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert replay.code == "observation_accepted"
    assert len(adapter.requests) == 1
    assert len(after.runner_observations) == 1


def test_v3_observation_requires_exact_completion_session_and_application_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    original_decide = session_coordinator.decide

    def crash_before_application(current, transition_input, context):
        if isinstance(transition_input, RunnerResultObserved):
            raise RuntimeError("application crash")
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_coordinator, "decide", crash_before_application)
    with pytest.raises(RuntimeError, match="application crash"):
        run_bounded_execution_unit(runtime, local_config=_codex_success_config())
    monkeypatch.setattr(session_coordinator, "decide", original_decide)
    persisted = _load(runtime)
    completion = next(iter(persisted.runner_session_completions.values()))
    evidence = runner_result_evidence_from_payload(
        json.loads(
            runtime.cas_store.get_bytes(completion.runner_result_evidence_digest)
        )
    )

    stale_payload = dict(evidence.payload())
    stale_payload["session_id"] = "session-foreign"
    stale = RunnerResultObserved(
        completion.application_input_id,
        run_id=completion.run_id,
        payload=stale_payload,
        observed_at=None,
    )
    stale_decision = original_decide(
        persisted,
        stale,
        session_coordinator.transition_context(
            command="test",
            input_id_value=stale.input_id,
        ),
    )
    arbitrary_input = replace(
        stale,
        input_id="arbitrary-input",
        payload=evidence.payload(),
    )
    arbitrary_decision = original_decide(
        persisted,
        arbitrary_input,
        session_coordinator.transition_context(
            command="test",
            input_id_value=arbitrary_input.input_id,
        ),
    )
    exact = replace(stale, payload=evidence.payload())
    exact_decision = original_decide(
        persisted,
        exact,
        session_coordinator.transition_context(
            command="test",
            input_id_value=exact.input_id,
        ),
    )

    assert stale_decision.accepted is False
    assert arbitrary_decision.accepted is False
    assert exact_decision.accepted is True


def test_same_run_retry_changes_correlation_and_cancellation_ids(tmp_path) -> None:
    starts = 0

    def refuse_then_succeed(request: AdapterInvocationRequest) -> object:
        nonlocal starts
        starts += 1
        return _refused_start(request) if starts == 1 else _success_start(request)

    adapter = _RecordingAdapter(refuse_then_succeed)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    second = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=_config(adapter),
    )

    assert first.code == "adapter_failure"
    assert second.code == "observation_accepted"
    assert len(adapter.requests) == 2
    first_request, second_request = adapter.requests
    assert (
        first_request.dispatch_envelope.run_id
        == second_request.dispatch_envelope.run_id
    )
    assert first_request.session_id != second_request.session_id
    assert first_request.dispatch_generation + 1 == second_request.dispatch_generation
    assert first_request.correlation_id != second_request.correlation_id
    assert first_request.cancellation_token != second_request.cancellation_token


def test_old_session_completion_after_same_run_retry_refuses(tmp_path) -> None:
    starts = 0

    def refuse_then_indeterminate(request: AdapterInvocationRequest) -> object:
        nonlocal starts
        starts += 1
        if starts == 1:
            return _refused_start(request)
        raise TimeoutError("second session may be live")

    adapter = _RecordingAdapter(refuse_then_indeterminate)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    run_bounded_execution_unit(runtime, local_config=_config(adapter))
    run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=_config(adapter),
    )
    before = _load(runtime)
    first_request = adapter.requests[0]
    first_session = before.runner_sessions[first_request.session_id]
    echo = DispatchEcho.from_dispatch_envelope(
        first_request.dispatch_envelope,
        correlation_id=first_request.correlation_id,
        selected_adapter_kind=first_request.selected_adapter_kind,
    )
    late_outcome = AdapterSuccessResult.from_unredacted(
        adapter_id=first_request.adapter_id,
        dispatch_echo=echo,
        redaction_policy=first_request.redaction_policy,
        marker="TASK_COMPLETE",
        observation_payload_candidate={"summary": "late"},
        artifact_payload_candidate=task_artifact_payload(),
    )

    result = session_coordinator._persist_completion(
        runtime,
        run_ref=before.runs[first_session.run_id].run_ref,
        session=first_session,
        request=first_request,
        outcome=late_outcome,
        cleanup_disposition="not_required",
    )
    after = _load(runtime)

    assert result.code == "completion_refused"
    assert after.runner_observations == before.runner_observations == {}
    assert after.runner_session_completions == before.runner_session_completions
    assert after.runner_sessions == before.runner_sessions
    assert after.runs == before.runs
    assert len(after.receipts) == len(before.receipts) + 1
    assert len(after.refusals) == len(before.refusals) + 1


def test_adapter_error_persists_terminal_session_without_workflow_progress(
    tmp_path,
) -> None:
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_error_config(),
    )
    after = _load(runtime)

    assert result.code == "adapter_failure"
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "failed"
    assert session.session_id in after.runner_session_completions
    assert after.runner_observations == {}
    assert after.artifacts == {}
    assert after.activation_routes == ()


@pytest.mark.parametrize("terminal_state", ("failed", "interrupted"))
def test_terminal_session_reopen_never_reinvokes_adapter(
    tmp_path,
    terminal_state: str,
) -> None:
    adapter = _RecordingAdapter(
        _refused_start if terminal_state == "failed" else _success_start
    )
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    run_bounded_execution_unit(
        runtime,
        local_config=_config(adapter),
        daemon_stop_requested=(
            (lambda: True) if terminal_state == "interrupted" else None
        ),
    )
    before = _load(runtime)
    requests_before_reopen = len(adapter.requests)

    runtime = _reopen_runtime(runtime)
    replay = reconcile_pending_runner_sessions(
        runtime,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert replay.code == "no_runner_session_reconciliation"
    assert len(adapter.requests) == requests_before_reopen
    assert next(iter(after.runner_sessions.values())).state == terminal_state
    assert after.runner_sessions == before.runner_sessions
    assert after.runner_session_completions == before.runner_session_completions
    assert after.runner_observations == before.runner_observations == {}
    assert after.runs == before.runs


def test_prestart_refusal_cas_contains_real_redacted_diagnostic(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run

    captured_error: AdapterErrorResult | None = None
    original = run._session_invocation_request
    monkeypatch.setattr(
        run,
        "_session_invocation_request",
        lambda *args, **kwargs: replace(
            original(*args, **kwargs),
            redaction_policy=RedactionPolicy("test", ("secret",)),
        ),
    )

    def refuse(request: AdapterInvocationRequest) -> StartRefusedBeforeExternalWork:
        nonlocal captured_error
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        captured_error = AdapterErrorResult.from_unredacted(
            adapter_id=request.adapter_id,
            error_kind="selected_authority_refused",
            dispatch_echo=echo,
            redaction_policy=request.redaction_policy,
            diagnostics={"reason": "secret is unavailable"},
        )
        return StartRefusedBeforeExternalWork(
            echo,
            captured_error,
            start_refusal_diagnostic_digest(captured_error),
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(refuse)),
    )
    after = _load(runtime)
    completion = next(iter(after.runner_session_completions.values()))

    assert result.code == "adapter_failure"
    assert captured_error is not None
    assert runtime.cas_store.get_bytes(completion.diagnostic_digest) == (
        start_refusal_diagnostic_bytes(captured_error)
    )
    assert b"secret" not in runtime.cas_store.get_bytes(completion.diagnostic_digest)


def test_tampered_prestart_refusal_digest_is_durably_refused(tmp_path) -> None:
    snapshot = None
    runtime = None

    def tampered(request: AdapterInvocationRequest) -> StartRefusedBeforeExternalWork:
        nonlocal snapshot
        outcome = _refused_start(request)
        object.__setattr__(outcome, "diagnostic_digest", "sha256:" + "f" * 64)
        snapshot = _load(runtime)
        return outcome

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(tampered)),
    )
    after = _load(runtime)

    assert result.code == "session_reconciliation_required"
    assert snapshot is not None
    _assert_single_refusal_audit(
        snapshot,
        after,
        session_state="starting",
        reason="runner_session_reconciliation_contradiction",
    )
    assert after.runner_session_completions == {}


def test_oversized_prestart_diagnostic_keeps_proven_refusal_terminal(
    tmp_path,
) -> None:
    def oversized(
        request: AdapterInvocationRequest,
    ) -> StartRefusedBeforeExternalWork:
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        error = AdapterErrorResult(
            adapter_id=request.adapter_id,
            error_kind="selected_authority_refused",
            redaction_policy_id=request.redaction_policy.policy_id,
            dispatch_echo=echo,
            diagnostics={"message": "x" * (START_REFUSAL_DIAGNOSTIC_MAX_BYTES * 2)},
        )
        return StartRefusedBeforeExternalWork(
            echo,
            error,
            start_refusal_diagnostic_digest(error),
        )

    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(oversized)),
    )
    after = _load(runtime)
    session = next(iter(after.runner_sessions.values()))
    completion = after.runner_session_completions[session.session_id]
    stored = runtime.cas_store.get_bytes(completion.diagnostic_digest)

    assert result.code == "adapter_failure"
    assert session.state == "failed"
    assert completion.cleanup_disposition == "not_required"
    assert len(stored) <= START_REFUSAL_DIAGNOSTIC_MAX_BYTES
    assert json.loads(stored)["diagnostics"]["truncated"] is True


def test_signal_digest_distinguishes_oversized_signals_with_same_prefix() -> None:
    common_prefix = "x" * (16 * 1024)

    first = session_coordinator._signal_digest({"diagnostic": common_prefix + "first"})
    second = session_coordinator._signal_digest(
        {"diagnostic": common_prefix + "second"}
    )

    assert first != second


def test_codex_and_generic_fake_adapters_share_session_lifecycle(tmp_path) -> None:
    for index, local_config in enumerate(
        (_codex_success_config(), _config(_RecordingAdapter(_success_start)))
    ):
        state, _ = _ready_state()
        runtime = _runtime(tmp_path / str(index), state)
        result = run_bounded_execution_unit(runtime, local_config=local_config)
        after = _load(runtime)

        assert result.code == "observation_accepted"
        session = next(iter(after.runner_sessions.values()))
        assert session.state == "completed"
        completion = after.runner_session_completions[session.session_id]
        assert completion.application_input_id in after.receipts


def _indeterminate_start(request: AdapterInvocationRequest) -> StartIndeterminate:
    return StartIndeterminate(
        DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        ),
        {"provider_request_id": "owned-request"},
        "sha256:" + "a" * 64,
    )


def _replace_running_locator_with_legacy_bare(runtime) -> str:
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    assert session.start_intent_at is not None
    assert session.durable_locator_digest is not None
    persisted = session_coordinator._persist_transition(
        runtime,
        AdvanceRunnerSession(
            f"test:legacy-running:{session.session_id}",
            run_ref=current.runs[session.run_id].run_ref,
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
    legacy_digest = runtime.cas_store.put_bytes(
        runner_session_locator_bytes(
            {"provider_request_id": "legacy-running-request"}
        )
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
    return session.session_id


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
            DispatchEcho.from_dispatch_envelope(
                invocation.dispatch_envelope,
                correlation_id=invocation.correlation_id,
                selected_adapter_kind=invocation.selected_adapter_kind,
            ),
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
    original = session_coordinator._persist_transition

    def crash_before_created_start(current_runtime, transition):
        if (
            getattr(transition, "expected_state", None) == "created"
            and getattr(transition, "next_state", None) == "starting"
        ):
            raise RuntimeError("crash before created start")
        return original(current_runtime, transition)

    monkeypatch.setattr(
        session_coordinator,
        "_persist_transition",
        crash_before_created_start,
    )
    with pytest.raises(RuntimeError, match="created start"):
        run_bounded_execution_unit(
            runtime,
            activation_id=created_activation_id,
            local_config=_config(adapter),
        )
    monkeypatch.setattr(session_coordinator, "_persist_transition", original)
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
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    assert invocation.dispatch_envelope.schema_version == 5
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
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        return VerifiedLive(
            echo,
            _ImmediateHandle(_success_outcome(invocation)),
            "verified-owned-handle",
            request.durable_locator_metadata,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        return VerifiedLive(
            echo,
            _ImmediateHandle(_success_outcome(invocation)),
            "verified-upgraded-handle",
            request.durable_locator_metadata,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    legacy_digest = runtime.cas_store.put_bytes(
        runner_session_locator_bytes(
            {"provider_request_id": "legacy-request"}
        )
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

    class CleanupHandle(_ImmediateHandle):
        def __init__(self, outcome: AdapterInvocationOutcome) -> None:
            super().__init__(outcome)
            self.operations: list[str] = []

        def cleanup(self) -> RunnerCleanupResult:
            self.operations.append("transport_cleanup")
            diagnostic = {"cleanup": "complete"}
            return RunnerCleanupResult(
                "complete",
                200,
                200,
                diagnostic,
                runner_cancellation_diagnostic_digest(diagnostic),
            )

    def reconcile(request):
        nonlocal cleanup_handle, reconcile_count
        reconcile_count += 1
        invocation = request.invocation_request
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        if reconcile_count == 1:
            return VerifiedLive(
                echo,
                MalformedLiveHandle(_success_outcome(invocation)),
                "legacy-upgraded-handle",
                {"provider_request_id": "legacy-running-request"},
            )
        cleanup_handle = CleanupHandle(
            AdapterErrorResult.from_unredacted(
                adapter_id=invocation.adapter_id,
                error_kind="cancelled",
                dispatch_echo=echo,
                redaction_policy=invocation.redaction_policy,
            )
        )
        return CleanupPending(echo, cleanup_handle, cleanup_handle_id)

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        session_coordinator._handle_id_digest("legacy-upgraded-handle")
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
                json.loads(
                    runtime.cas_store.get_bytes(
                        session.durable_locator_digest
                    )
                )
            )
            return super().poll_completion()

    def reconcile(request):
        invocation = request.invocation_request
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        return VerifiedLive(
            echo,
            InspectingHandle(_success_outcome(invocation)),
            "legacy-completing-handle",
            {"provider_request_id": "legacy-running-request"},
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
            "adapter_locator": {
                "provider_request_id": "legacy-running-request"
            },
            "handle_id_digest": session_coordinator._handle_id_digest(
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
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        handle = CleanupTrackingHandle()
        return CleanupPending(echo, handle, "unproven-legacy-handle")

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    legacy_digest = runtime.cas_store.put_bytes(
        runner_session_locator_bytes(
            {"provider_request_id": "legacy-request"}
        )
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        handle = MalformedReconciledHandle()
        return VerifiedLive(
            echo,
            handle,
            "verified-owned-handle",
            request.durable_locator_metadata,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        handle = CompletionTrackingHandle(_success_outcome(invocation))
        return VerifiedLive(
            echo,
            handle,
            "verified-owned-handle",
            request.durable_locator_metadata,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    runtime = _reopen_runtime(runtime)
    original_decide = session_coordinator.decide

    def crash_before_application(current, transition_input, context):
        if isinstance(transition_input, RunnerResultObserved):
            raise RuntimeError("application crash")
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_coordinator, "decide", crash_before_application)
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

    monkeypatch.setattr(session_coordinator, "decide", original_decide)
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
            DispatchEcho.from_dispatch_envelope(
                invocation.dispatch_envelope,
                correlation_id=invocation.correlation_id,
                selected_adapter_kind=invocation.selected_adapter_kind,
            ),
            **mismatch,
        )
        return VerifiedLive(echo, _SequenceHandle([]), "hostile-handle", {})

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        return Terminal(echo, _success_outcome(invocation), "complete")

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
            DispatchEcho.from_dispatch_envelope(
                invocation.dispatch_envelope,
                correlation_id=invocation.correlation_id,
                selected_adapter_kind=invocation.selected_adapter_kind,
            ),
            "sha256:" + "c" * 64,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    request = adapter.requests[0]
    run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )
    orphaned = _load(runtime)
    session = orphaned.runner_sessions[request.session_id]

    late = session_coordinator._persist_completion(
        runtime,
        run_ref=orphaned.runs[session.run_id].run_ref,
        session=session,
        request=request,
        outcome=_success_outcome(request),
        cleanup_disposition="complete",
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    original = session_coordinator._persist_transition
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
        session_coordinator,
        "_persist_transition",
        crash_before_start_intent,
    )
    with pytest.raises(RuntimeError, match="start intent"):
        run_bounded_execution_unit(runtime, local_config=_config(adapter))
    created = _load(runtime)
    session = next(iter(created.runner_sessions.values()))
    assert session.state == "created"
    assert adapter.requests == []

    monkeypatch.setattr(session_coordinator, "_persist_transition", original)
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

    class CleanupHandle(_ImmediateHandle):
        def __init__(self, outcome: AdapterInvocationOutcome) -> None:
            super().__init__(outcome)
            self.operations: list[str] = []

        def cleanup(self) -> RunnerCleanupResult:
            self.operations.append("transport_cleanup")
            diagnostic = {"cleanup": "complete"}
            return RunnerCleanupResult(
                "complete",
                200,
                200,
                diagnostic,
                runner_cancellation_diagnostic_digest(diagnostic),
            )

    def reconcile(request):
        nonlocal handle
        invocation = request.invocation_request
        echo = DispatchEcho.from_dispatch_envelope(
            invocation.dispatch_envelope,
            correlation_id=invocation.correlation_id,
            selected_adapter_kind=invocation.selected_adapter_kind,
        )
        terminal_echo = (
            replace(echo, session_fencing_token="foreign-session-fence")
            if foreign_terminal_proof
            else echo
        )
        handle = CleanupHandle(
            AdapterErrorResult.from_unredacted(
                adapter_id=invocation.adapter_id,
                error_kind="cancelled",
                dispatch_echo=terminal_echo,
                redaction_policy=invocation.redaction_policy,
            )
        )
        return CleanupPending(echo, handle, returned_handle_id)

    def indeterminate_cleanup_pending_start(
        request: AdapterInvocationRequest,
    ) -> StartIndeterminate:
        return StartIndeterminate(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            None,
            "sha256:" + "a" * 64,
        )

    adapter = _RecordingAdapter(indeterminate_cleanup_pending_start, reconcile)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    request = adapter.requests[0]
    assert session.start_intent_at is not None
    locator_digest = session_coordinator._safe_coordinator_locator_digest(
        runtime,
        request,
        handle_id="verified-cleanup-handle",
        adapter_locator={"provider_request_id": "owned-request"},
    )
    assert locator_digest is not None
    persisted = session_coordinator._persist_transition(
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
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    run = current.runs[session.run_id]
    if restart_state == "running":
        assert session.durable_locator_digest is not None
        persisted = session_coordinator._persist_transition(
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
        persisted = session_coordinator._persist_transition(
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
