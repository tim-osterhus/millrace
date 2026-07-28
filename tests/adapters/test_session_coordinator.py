from __future__ import annotations

from collections.abc import Callable

import pytest

from cli.test_cli_bounded_execution_unit import (
    _codex_error_config,
    _codex_success_config,
    _codex_timeout_config,
    _load,
    _ready_state,
    _runtime,
)
from kernel.kernel_ping_scenarios import task_artifact_payload
from millrace.adapters.cli import session_coordinator
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterSuccessResult,
    DispatchEcho,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    StartedSession,
    StartRefusedBeforeExternalWork,
    Unsupported,
)
from millrace.contracts.transition import RunnerResultObserved
from millrace.operator.prompt_material import SelectedAssetMaterializationError


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
        return RunnerCleanupResult("not_required", 0, 0, "sha256:" + "a" * 64)

    @staticmethod
    def _unsupported(operation: str) -> RunnerCancellationOperationResult:
        return RunnerCancellationOperationResult(
            operation,
            "unsupported",
            0,
            0,
            "sha256:" + "a" * 64,
        )


class _RecordingAdapter:
    adapter_kind = "codex"

    def __init__(
        self,
        start: Callable[[AdapterInvocationRequest], object],
    ) -> None:
        self._start = start
        self.requests: list[AdapterInvocationRequest] = []

    def start_session(self, request: AdapterInvocationRequest) -> object:
        self.requests.append(request)
        return self._start(request)

    def reconcile_session(self, request: object) -> object:
        invocation = request.invocation_request
        return Unsupported(
            DispatchEcho.from_dispatch_envelope(
                invocation.dispatch_envelope,
                correlation_id=invocation.correlation_id,
            )
        )


def _success_start(request: AdapterInvocationRequest) -> StartedSession:
    echo = DispatchEcho.from_dispatch_envelope(
        request.dispatch_envelope,
        correlation_id=request.correlation_id,
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
        "sha256:" + "b" * 64,
    )


def _config(adapter: _RecordingAdapter) -> AdapterLocalConfig:
    return AdapterLocalConfig(adapters={"codex": adapter})


def test_crash_before_start_intent_proves_no_adapter_call(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter(_success_start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    def fail_materialization(**_kwargs: object) -> object:
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
    assert session.state == "created"
    assert session.start_intent_at is None


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


def test_timeout_after_external_side_effect_stays_reconcile_required(
    tmp_path,
) -> None:
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(
        runtime,
        local_config=_codex_timeout_config(),
    )
    after = _load(runtime)

    assert result.code == "session_reconciliation_required"
    session = next(iter(after.runner_sessions.values()))
    assert session.state == "starting"
    assert session.session_id not in after.runner_session_completions


def test_start_succeeds_but_running_write_fails_cannot_permit_another_start(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter(_success_start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    persist = runtime.store.persist_runtime_state

    def fail_running(candidate, cas_store) -> None:
        if any(
            session.state == "running"
            for session in candidate.runner_sessions.values()
        ):
            raise RuntimeError("simulated running write crash")
        persist(candidate, cas_store)

    monkeypatch.setattr(runtime.store, "persist_runtime_state", fail_running)
    with pytest.raises(RuntimeError, match="running write crash"):
        run_bounded_execution_unit(runtime, local_config=_config(adapter))
    monkeypatch.setattr(runtime.store, "persist_runtime_state", persist)

    retry = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert retry.code == "session_reconciliation_required"
    assert len(adapter.requests) == 1
    assert next(iter(after.runner_sessions.values())).state == "starting"


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


def test_crash_after_completion_persistence_replays_without_adapter_invocation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter(_success_start)
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
    assert len(persisted.runner_session_completions) == 1
    assert persisted.runner_observations == {}

    monkeypatch.setattr(session_coordinator, "decide", original_decide)
    replay = run_bounded_execution_unit(
        runtime,
        activation_id="activation-taskmaster",
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert replay.code == "observation_accepted"
    assert len(adapter.requests) == 1
    assert len(after.runner_observations) == 1


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
