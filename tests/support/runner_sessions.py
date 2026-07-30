from __future__ import annotations

import os
import signal
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import replace

from cli.test_cli_bounded_execution_unit import (
    _load,
    _ready_state,
    _runtime,
)
from kernel.kernel_ping_scenarios import task_artifact_payload
from millrace.adapters.cli import session_completion, session_coordinator
from millrace.adapters.cli.run import (
    run_bounded_execution_unit,
)
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterSuccessResult,
    DispatchEcho,
    RedactionPolicy,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    Unsupported,
    runner_cancellation_diagnostic_digest,
    start_refusal_diagnostic_digest,
)
from millrace.contracts import QueueFamilyId
from millrace.contracts.runner import (
    runner_session_locator_bytes,
)
from millrace.contracts.transition import (
    AdvanceRunnerSession,
    EnqueueWork,
)
from support.kernel_ping import apply_accepted_input, kernel_ping_context


def _codex_process_adapter(tmp_path, wrapper: str, adapter_type=None):
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig

    return (adapter_type or CodexAdapter)(
        CodexAdapterConfig(
            "codex-default",
            "offline_fake",
            (sys.executable, "-c", wrapper),
            tmp_path,
            {},
            5,
            16384,
            8192,
            512,
            RedactionPolicy(policy_id="redact-default"),
        )
    )


def _heartbeat_wrapper(ready, heartbeat, process_pid) -> str:
    return (
        "import os,pathlib,time\n"
        f"ready=pathlib.Path({str(ready)!r})\n"
        f"heartbeat=pathlib.Path({str(heartbeat)!r})\n"
        f"pathlib.Path({str(process_pid)!r}).write_text(str(os.getpid()))\n"
        "ready.write_text('ready')\n"
        "while True:\n heartbeat.write_text(str(time.time()))\n time.sleep(0.03)\n"
    )


def _assert_heartbeat_stopped(heartbeat) -> None:
    stable = heartbeat.read_text()
    time.sleep(0.15)
    assert heartbeat.read_text() == stable


def _kill_recorded_process(process_pid, *, group: bool = False) -> None:
    if not process_pid.exists():
        return
    pid = int(process_pid.read_text())
    try:
        (os.killpg if group else os.kill)(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _dispatch_echo(request: AdapterInvocationRequest) -> DispatchEcho:
    return DispatchEcho.from_dispatch_envelope(
        request.dispatch_envelope,
        correlation_id=request.correlation_id,
        selected_adapter_kind=request.selected_adapter_kind,
    )


def _started_session(request: AdapterInvocationRequest, handle) -> StartedSession:
    return StartedSession(
        _dispatch_echo(request), handle, f"fake:{request.session_id}", {}
    )


def _operation_result(
    operation: str,
    result: str,
    at: int = 0,
    *,
    diagnostic: dict | None = None,
) -> RunnerCancellationOperationResult:
    detail = diagnostic or {"operation": operation}
    return RunnerCancellationOperationResult(
        operation,
        result,
        at,
        at,
        detail,
        runner_cancellation_diagnostic_digest(detail),
    )


def _cleanup_result(disposition: str, at: int = 0) -> RunnerCleanupResult:
    diagnostic = {"cleanup": disposition}
    return RunnerCleanupResult(
        disposition,
        at,
        at,
        diagnostic,
        runner_cancellation_diagnostic_digest(diagnostic),
    )


class _ImmediateHandle:
    def __init__(
        self,
        outcome: AdapterInvocationOutcome,
        capture: Callable[[], None] | None = None,
        cleanup_disposition: str = "not_required",
        cleanup_at: int = 0,
        track_operations: bool = False,
    ) -> None:
        self._outcome: AdapterInvocationOutcome | None = outcome
        self._capture = capture
        self._cleanup = _cleanup_result(cleanup_disposition, cleanup_at)
        self.operations: list[str] = []
        self._track_operations = track_operations

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        if self._capture is not None:
            self._capture()
        outcome = self._outcome
        self._outcome = None
        return outcome

    def request_cancel(self) -> RunnerCancellationOperationResult:
        self._record("cooperative_cancel")
        return _operation_result("cooperative_cancel", "unsupported")

    def terminate(self) -> RunnerCancellationOperationResult:
        self._record("terminate")
        return _operation_result("terminate", "unsupported")

    def kill(self) -> RunnerCancellationOperationResult:
        self._record("kill")
        return _operation_result("kill", "unsupported")

    def cleanup(self) -> RunnerCleanupResult:
        self._record("transport_cleanup")
        return getattr(self, "_cleanup", _cleanup_result("not_required"))

    def _record(self, operation: str) -> None:
        if getattr(self, "_track_operations", False):
            self.operations.append(operation)


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
        ready_after: str | None = None,
        completion_race: bool = False,
        secondary_on_cancel: tuple[str, str] | None = None,
        secondary_on_terminate: tuple[str, str] | None = None,
        malformed_after: str | None = None,
        throw_after_request: bool = False,
        mislabeled_cooperative: bool = False,
    ) -> None:
        self._runtime = runtime
        self._request = request
        self._requested = False
        self.operations: list[str] = []
        self._cooperative_result = cooperative_result
        self._malformed_emitted = False
        self.ready_after = ready_after
        self.completion_race = completion_race
        self.secondary_on_cancel = secondary_on_cancel
        self.secondary_on_terminate = secondary_on_terminate
        self.malformed_after = malformed_after
        self.throw_after_request = throw_after_request
        self._mislabeled_cooperative = mislabeled_cooperative

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
        if self.throw_after_request:
            raise RuntimeError("poll transport failed")
        if self.malformed_after in self.operations and not self._malformed_emitted:
            self._malformed_emitted = True
            return object()
        if self._malformed_emitted or self.completion_race:
            return _success_outcome(self._request)
        if self.ready_after is not None and self.ready_after not in self.operations:
            return None
        return _error_outcome(
            self._request,
            error_kind="cancelled",
            dispatch_echo=_dispatch_echo(self._request),
        )

    def request_cancel(self) -> RunnerCancellationOperationResult:
        if self._mislabeled_cooperative:
            self._requested = True
            diagnostic = {"operation": "kill", "malicious": True}
            return _operation_result(
                "kill",
                "succeeded",
                100,
                diagnostic=diagnostic,
            )
        if self.secondary_on_cancel is not None:
            self._request_secondary(*self.secondary_on_cancel)
        self.operations.append("cooperative_cancel")
        return _operation_result(
            "cooperative_cancel",
            self._cooperative_result,
            100,
        )

    def terminate(self) -> RunnerCancellationOperationResult:
        if self.secondary_on_terminate is not None:
            self._request_secondary(*self.secondary_on_terminate)
        self.operations.append("terminate")
        result = "succeeded" if self.ready_after in {None, "terminate"} else "timed_out"
        return _operation_result("terminate", result, 101)

    def kill(self) -> RunnerCancellationOperationResult:
        self.operations.append("kill")
        return _operation_result("kill", "succeeded", 102)

    def cleanup(self) -> RunnerCleanupResult:
        self.operations.append("transport_cleanup")
        return _cleanup_result("complete", 103)

    def _request_secondary(self, request_id: str, actor_id: str) -> None:
        result = session_coordinator.request_operator_cancellation(
            self._runtime,
            run_id=self._request.dispatch_envelope.run_id,
            request_id=request_id,
            actor_id=actor_id,
        )
        assert result.accepted


def _CapturingImmediateHandle(
    outcome: AdapterInvocationOutcome,
    capture: Callable[[], None],
) -> _ImmediateHandle:
    return _ImmediateHandle(outcome, capture, track_operations=True)


def _CleanupTrackingHandle(outcome: AdapterInvocationOutcome) -> _ImmediateHandle:
    return _ImmediateHandle(
        outcome,
        cleanup_disposition="complete",
        cleanup_at=200,
        track_operations=True,
    )


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
        return Unsupported(_dispatch_echo(invocation))


def _success_start(request: AdapterInvocationRequest) -> StartedSession:
    echo = _dispatch_echo(request)
    outcome = AdapterSuccessResult.from_unredacted(
        adapter_id=request.adapter_id,
        dispatch_echo=echo,
        redaction_policy=request.redaction_policy,
        marker="TASK_COMPLETE",
        observation_payload_candidate={"summary": "ok"},
        artifact_payload_candidate=task_artifact_payload(),
    )
    return _started_session(request, _ImmediateHandle(outcome))


def _refused_start(request: AdapterInvocationRequest) -> StartRefusedBeforeExternalWork:
    echo = _dispatch_echo(request)
    error = _error_outcome(
        request,
        error_kind="selected_authority_refused",
        dispatch_echo=echo,
    )
    return StartRefusedBeforeExternalWork(
        echo,
        error,
        start_refusal_diagnostic_digest(error),
    )


def _config(adapter: _RecordingAdapter) -> AdapterLocalConfig:
    return AdapterLocalConfig(adapters={"codex": adapter})


def _ready_runtime(tmp_path):
    state, _fingerprint = _ready_state()
    return _runtime(tmp_path, state)


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
    unchanged = (
        "runner_session_completions",
        "runner_observations",
        "artifacts",
        "work_items",
        "activations",
        "activation_routes",
        "closed_work_items",
        "runs",
    )
    assert all(getattr(after, name) == getattr(before, name) for name in unchanged)
    refusal = next(
        refusal
        for refusal in after.refusals
        if refusal.record_id not in {existing.record_id for existing in before.refusals}
    )
    assert refusal.input_kind == "workflow.refuse_runner_session_signal"
    assert refusal.reason == reason


def _mismatched_echo(request: AdapterInvocationRequest) -> DispatchEcho:
    return replace(
        _dispatch_echo(request),
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
        handle = _CapturingImmediateHandle(
            outcome_factory(request),
            lambda: snapshots.append(_load(runtime)),
        )
        handles.append(handle)
        return _started_session(request, handle)

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
        dispatch_echo=dispatch_echo or _dispatch_echo(request),
        redaction_policy=request.redaction_policy,
        marker="TASK_COMPLETE",
        artifact_payload_candidate=task_artifact_payload(),
    )


def _error_outcome(
    request: AdapterInvocationRequest,
    *,
    dispatch_echo: DispatchEcho | None,
    error_kind: str = "invocation_failed",
) -> AdapterErrorResult:
    return AdapterErrorResult.from_unredacted(
        adapter_id=request.adapter_id,
        error_kind=error_kind,
        dispatch_echo=dispatch_echo,
        redaction_policy=request.redaction_policy,
    )


def _indeterminate_start(request: AdapterInvocationRequest) -> StartIndeterminate:
    return StartIndeterminate(
        _dispatch_echo(request),
        {"provider_request_id": "owned-request"},
        "sha256:" + "a" * 64,
    )


def _replace_running_locator_with_legacy_bare(runtime) -> str:
    current = _load(runtime)
    session = next(iter(current.runner_sessions.values()))
    assert session.start_intent_at is not None
    assert session.durable_locator_digest is not None
    persisted = session_completion._persist_transition(
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
        runner_session_locator_bytes({"provider_request_id": "legacy-running-request"})
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
