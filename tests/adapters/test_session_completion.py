from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cli.test_cli_bounded_execution_unit import (
    _codex_error_config,
    _codex_success_config,
    _load,
    _ready_state,
    _reopen_runtime,
    _runtime,
)
from kernel.kernel_ping_scenarios import task_artifact_payload
from millrace.adapters.cli import (
    session_cancellation,
    session_completion,
)
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
    DispatchEcho,
    RedactionPolicy,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    VerifiedLive,
    start_refusal_diagnostic_bytes,
    start_refusal_diagnostic_digest,
)
from millrace.contracts.runner import (
    runner_result_evidence_from_payload,
)
from millrace.contracts.transition import (
    RunnerResultObserved,
)
from support.runner_sessions import (
    _assert_single_refusal_audit,
    _CapturingImmediateHandle,
    _completion_signal_result,
    _config,
    _dispatch_echo,
    _error_outcome,
    _ImmediateHandle,
    _indeterminate_start,
    _mismatched_echo,
    _ready_runtime,
    _RecordingAdapter,
    _refused_start,
    _success_outcome,
    _success_start,
)


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
        valid_echo = _dispatch_echo(request)
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
    runtime = _ready_runtime(tmp_path)

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
    runtime = _ready_runtime(tmp_path)
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
    runtime = _ready_runtime(tmp_path)

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
    runtime = _ready_runtime(tmp_path)
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
        echo = _dispatch_echo(invocation)
        return VerifiedLive(
            echo,
            _ImmediateHandle(_success_outcome(invocation)),
            "verified-live-handle",
            adapter_locator,
        )

    adapter = _RecordingAdapter(_indeterminate_start, reconcile)
    runtime = _ready_runtime(tmp_path)
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
    assert not any(b"opaque-live-handle-123" in payload for payload in cas_payloads)
    assert after.runner_session_completions == {}
    assert after.runner_observations == {}


def test_indeterminate_start_retains_redacted_safe_locator(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import run

    secret = "locator-secret"

    def indeterminate(request: AdapterInvocationRequest) -> StartIndeterminate:
        echo = _dispatch_echo(request)
        return StartIndeterminate(
            echo,
            {"provider_request": secret},
            "sha256:" + "d" * 64,
        )

    adapter = _RecordingAdapter(indeterminate)
    runtime = _ready_runtime(tmp_path)
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
    runtime = _ready_runtime(tmp_path)
    original_decide = session_completion.decide

    def crash_before_application(current, transition_input, context):
        if isinstance(transition_input, RunnerResultObserved):
            raise RuntimeError("application crash")
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_completion, "decide", crash_before_application)
    with pytest.raises(RuntimeError, match="application crash"):
        run_bounded_execution_unit(runtime, local_config=_config(adapter))
    persisted = _load(runtime)
    assert handle is not None
    assert handle.operations == ["transport_cleanup"]
    assert len(persisted.runner_session_completions) == 1
    assert persisted.runner_observations == {}

    monkeypatch.setattr(session_completion, "decide", original_decide)
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
    runtime = _ready_runtime(tmp_path)
    original_decide = session_completion.decide

    def crash_before_application(current, transition_input, context):
        if isinstance(transition_input, RunnerResultObserved):
            raise RuntimeError("application crash")
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_completion, "decide", crash_before_application)
    with pytest.raises(RuntimeError, match="application crash"):
        run_bounded_execution_unit(runtime, local_config=_codex_success_config())
    monkeypatch.setattr(session_completion, "decide", original_decide)
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
        session_completion.transition_context(
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
        session_completion.transition_context(
            command="test",
            input_id_value=arbitrary_input.input_id,
        ),
    )
    exact = replace(stale, payload=evidence.payload())
    exact_decision = original_decide(
        persisted,
        exact,
        session_completion.transition_context(
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
    runtime = _ready_runtime(tmp_path)

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


def test_same_run_retry_creation_refusal_uses_public_retry_code(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter(_refused_start)
    runtime = _ready_runtime(tmp_path)
    first = run_bounded_execution_unit(
        runtime,
        local_config=_config(adapter),
    )
    monkeypatch.setattr(
        session_completion,
        "_persist_transition",
        lambda *_args, **_kwargs: None,
    )

    retry = run_bounded_execution_unit(
        runtime,
        activation_id=first.activation_id,
        local_config=_config(adapter),
    )

    assert first.code == "adapter_failure"
    assert retry.code == "runner_session_retry_refused"


def test_initial_session_creation_refusal_keeps_creation_code(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ready_runtime(tmp_path)
    monkeypatch.setattr(
        session_completion,
        "_persist_transition",
        lambda *_args, **_kwargs: None,
    )

    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(_success_start)),
    )

    assert result.code == "session_creation_refused"


def test_old_session_completion_after_same_run_retry_refuses(tmp_path) -> None:
    starts = 0

    def refuse_then_indeterminate(request: AdapterInvocationRequest) -> object:
        nonlocal starts
        starts += 1
        if starts == 1:
            return _refused_start(request)
        raise TimeoutError("second session may be live")

    adapter = _RecordingAdapter(refuse_then_indeterminate)
    runtime = _ready_runtime(tmp_path)
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

    result = session_completion._persist_completion(
        runtime,
        run_ref=before.runs[first_session.run_id].run_ref,
        session=first_session,
        request=first_request,
        outcome=late_outcome,
        cleanup=session_cancellation._terminal_cleanup_result(
            None,
            "not_required",
        ),
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
    runtime = _ready_runtime(tmp_path)

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
    runtime = _ready_runtime(tmp_path)
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
        echo = _dispatch_echo(request)
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

    runtime = _ready_runtime(tmp_path)
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

    runtime = _ready_runtime(tmp_path)
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
        echo = _dispatch_echo(request)
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

    runtime = _ready_runtime(tmp_path)
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

    first = session_completion._signal_digest({"diagnostic": common_prefix + "first"})
    second = session_completion._signal_digest({"diagnostic": common_prefix + "second"})

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
