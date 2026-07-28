from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

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
    START_REFUSAL_DIAGNOSTIC_MAX_BYTES,
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
    start_refusal_diagnostic_bytes,
    start_refusal_diagnostic_digest,
)
from millrace.contracts.runner import runner_result_evidence_from_payload
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


class _SequenceHandle(_ImmediateHandle):
    def __init__(self, outcomes: list[AdapterInvocationOutcome | None]) -> None:
        self._outcomes = outcomes
        self.polls = 0

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        self.polls += 1
        return self._outcomes.pop(0) if self._outcomes else None


class _CapturingImmediateHandle(_ImmediateHandle):
    def __init__(
        self,
        outcome: AdapterInvocationOutcome,
        capture: Callable[[], None],
    ) -> None:
        super().__init__(outcome)
        self._capture = capture

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        self._capture()
        return super().poll_completion()


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
        start_refusal_diagnostic_digest(error),
    )


def _config(adapter: _RecordingAdapter) -> AdapterLocalConfig:
    return AdapterLocalConfig(adapters={"codex": adapter})


def _assert_single_refusal_audit(
    before,
    after,
    *,
    session_state: str,
    reason: str,
) -> None:
    assert len(after.receipts) == len(before.receipts) + 1
    assert len(after.refusals) == len(before.refusals) + 1
    assert len(after.governance_events) == len(before.governance_events) + 1
    assert len(after.traces) == len(before.traces) + 1
    assert after.runner_sessions == before.runner_sessions
    assert next(iter(after.runner_sessions.values())).state == session_state
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
        if refusal.record_id
        not in {existing.record_id for existing in before.refusals}
    )
    assert refusal.input_kind == "workflow.refuse_runner_session_signal"
    assert refusal.reason == reason


def _mismatched_echo(request: AdapterInvocationRequest) -> DispatchEcho:
    return replace(
        DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
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
    runtime = None

    def start(request: AdapterInvocationRequest) -> StartedSession:
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
        )
        return StartedSession(
            echo,
            _CapturingImmediateHandle(
                outcome_factory(request),
                lambda: snapshots.append(_load(runtime)),
            ),
            f"fake:{request.session_id}",
            {},
        )

    adapter = _RecordingAdapter(start)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    assert len(snapshots) == 1
    return result, snapshots[0], _load(runtime)


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


def test_forever_pending_handle_is_owned_until_selected_deadline(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle: _SequenceHandle | None = None

    def pending(request: AdapterInvocationRequest) -> StartedSession:
        nonlocal handle
        started = _success_start(request)
        handle = _SequenceHandle([])
        return replace(started, handle=handle)

    monotonic_values = iter((10.0, 10.0, 1_000_000_000.0))
    monkeypatch.setattr(
        session_coordinator,
        "_monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(session_coordinator, "_sleep", lambda _value: None)
    adapter = _RecordingAdapter(pending)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "session_reconciliation_required"
    assert handle is not None
    assert handle.polls >= 1
    assert next(iter(after.runner_sessions.values())).state == "running"


def test_malformed_handle_outcome_remains_running_and_requires_reconciliation(
    tmp_path,
) -> None:
    class _MalformedOutcomeHandle(_ImmediateHandle):
        def __init__(self) -> None:
            pass

        def poll_completion(self) -> object:
            return object()

    def malformed_outcome(request: AdapterInvocationRequest) -> StartedSession:
        return replace(
            _success_start(request),
            handle=_MalformedOutcomeHandle(),
        )

    adapter = _RecordingAdapter(malformed_outcome)
    state, _ = _ready_state()
    runtime = _runtime(tmp_path, state)

    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = _load(runtime)

    assert result.code == "session_reconciliation_required"
    assert next(iter(after.runner_sessions.values())).state == "running"
    assert after.runner_session_completions == {}


def test_adapter_error_without_dispatch_echo_is_durably_audited(tmp_path) -> None:
    result, before_signal, after = _completion_signal_result(
        tmp_path,
        lambda request: _error_outcome(request, dispatch_echo=None),
    )

    assert result.code == "session_reconciliation_required"
    _assert_single_refusal_audit(
        before_signal,
        after,
        session_state="running",
        reason="runner_session_reconciliation_contradiction",
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

    result, before_signal, after = _completion_signal_result(
        tmp_path,
        mismatched_completion,
    )

    assert result.code == "session_reconciliation_required"
    _assert_single_refusal_audit(
        before_signal,
        after,
        session_state="running",
        reason="runner_session_authority_mismatch",
    )


def test_evidence_conversion_refusal_is_durably_audited(tmp_path) -> None:
    result, before_signal, after = _completion_signal_result(
        tmp_path,
        lambda request: replace(_success_outcome(request), marker=None),
    )

    assert result.code == "adapter_conversion_refused"
    _assert_single_refusal_audit(
        before_signal,
        after,
        session_state="running",
        reason="runner_session_reconciliation_contradiction",
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
        json.loads(runtime.cas_store.get_bytes(completion.runner_result_evidence_digest))
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


def test_prestart_refusal_cas_contains_real_redacted_diagnostic(tmp_path) -> None:
    captured_error: AdapterErrorResult | None = None

    def refuse(request: AdapterInvocationRequest) -> StartRefusedBeforeExternalWork:
        nonlocal captured_error
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
        )
        captured_error = AdapterErrorResult.from_unredacted(
            adapter_id=request.adapter_id,
            error_kind="selected_authority_refused",
            dispatch_echo=echo,
            redaction_policy=RedactionPolicy("test", ("secret",)),
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
    assert b"secret" not in runtime.cas_store.get_bytes(
        completion.diagnostic_digest
    )


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
        )
        error = AdapterErrorResult(
            adapter_id=request.adapter_id,
            error_kind="selected_authority_refused",
            redaction_policy_id=request.redaction_policy.policy_id,
            dispatch_echo=echo,
            diagnostics={
                "message": "x" * (START_REFUSAL_DIAGNOSTIC_MAX_BYTES * 2)
            },
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

    first = session_coordinator._signal_digest(
        {"diagnostic": common_prefix + "first"}
    )
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
