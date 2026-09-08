from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from adapters.test_context_writeback import (
    _bound_fixture,
    _digest,
    _writeback_report,
    _writeback_success_start,
)
from cli.test_cli_bounded_execution_unit import (
    _codex_error_config,
    _codex_success_config,
    _load,
    _ready_state,
    _ready_state_with_selected_codex_authority,
    _reopen_runtime,
    _runtime,
)
from kernel.kernel_ping_scenarios import task_artifact_payload
from millrace.adapters.cli import (
    session_cancellation,
    session_completion,
    session_diagnostics,
)
from millrace.adapters.cli.run import (
    reconcile_pending_runner_sessions,
    run_bounded_execution_unit,
)
from millrace.adapters.runner_contract import (
    START_REFUSAL_DIAGNOSTIC_MAX_BYTES,
    AdapterAttribution,
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterSuccessResult,
    AdapterTokenUsage,
    DispatchEcho,
    RedactionPolicy,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    VerifiedLive,
    start_refusal_diagnostic_digest,
)
from millrace.contracts.context_checkout import decode_context_checkout_manifest
from millrace.contracts.runner import (
    runner_result_evidence_from_payload,
    runner_session_completion_diagnostic_from_payload,
)
from millrace.contracts.transition import (
    RunnerResultObserved,
)
from millrace.substrate.cas import storage_digest_for_bytes
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


def test_context_writeback_unruled_proposal_refusal_has_no_accepted_state(
    tmp_path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(
        tmp_path,
        unruled_required_root=True,
    )
    before = _load(runtime)
    proposed = "candidate\n"

    def start(request: AdapterInvocationRequest) -> object:
        return _writeback_success_start(
            request,
            artifact=_writeback_report(
                proposals=(
                    {
                        "path": "unruled/locked.txt",
                        "proposed_content": proposed,
                        "proposed_content_sha256": storage_digest_for_bytes(
                            proposed.encode("utf-8")
                        ),
                        "evidence_refs": ("runner:1",),
                        "classification": "protected_proposal",
                    },
                )
            ),
        )

    adapter = _RecordingAdapter(start)
    adapter.config = SimpleNamespace(
        cwd=runtime.paths.workspace_path,
        wrapper_protocol_version=4,
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id=state.runs[session.run_id].activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert result.code == "adapter_failure"
    assert result.adapter_error_kind == "context_mutation_refused"
    assert after.runner_observations == before.runner_observations
    completion = after.runner_session_completions[session.session_id]
    assert completion.terminal_state == "failed"
    assert completion.adapter_error_kind == "context_mutation_refused"
    assert after.artifacts == before.artifacts
    assert after.closed_work_items == before.closed_work_items


def test_context_writeback_adapter_error_requires_unchanged_roots(
    tmp_path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    before = _load(runtime)
    mutated = runtime.paths.workspace_path / "src" / "unreported.txt"

    def start(request: AdapterInvocationRequest) -> object:
        mutated.write_text("unreported\n", encoding="utf-8")
        return replace(
            _success_start(request),
            handle=_ImmediateHandle(_error_outcome(request)),
        )

    adapter = _RecordingAdapter(start)
    adapter.config = SimpleNamespace(
        cwd=runtime.paths.workspace_path,
        wrapper_protocol_version=4,
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id=state.runs[session.run_id].activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert result.code == "session_reconciliation_required"
    assert after.runner_observations == before.runner_observations
    assert after.runner_session_completions == before.runner_session_completions


def test_context_writeback_start_refusal_requires_unchanged_roots(
    tmp_path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    before = _load(runtime)
    mutated = runtime.paths.workspace_path / "src" / "unreported.txt"
    snapshots = []

    def refuse(request: AdapterInvocationRequest) -> StartRefusedBeforeExternalWork:
        mutated.write_text("unreported\n", encoding="utf-8")
        snapshots.append(_load(runtime))
        error = _error_outcome(request, dispatch_echo=_dispatch_echo(request))
        return StartRefusedBeforeExternalWork(
            _dispatch_echo(request),
            error,
            start_refusal_diagnostic_digest(error),
        )

    adapter = _RecordingAdapter(refuse)
    adapter.config = SimpleNamespace(
        cwd=runtime.paths.workspace_path,
        wrapper_protocol_version=4,
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id=state.runs[session.run_id].activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert result.code == "completion_refused"
    assert len(snapshots) == 1
    assert mutated.read_text(encoding="utf-8") == "unreported\n"
    assert after.runner_session_completions == before.runner_session_completions
    assert after.runner_observations == before.runner_observations
    assert after.artifacts == before.artifacts
    assert after.closed_work_items == before.closed_work_items
    before_signal = snapshots[0]
    assert len(after.receipts) == len(before_signal.receipts) + 1
    assert len(after.refusals) == len(before_signal.refusals) + 1
    assert len(after.governance_events) == len(before_signal.governance_events) + 1
    assert len(after.traces) == len(before_signal.traces) + 1
    assert after.runner_sessions == before_signal.runner_sessions
    assert after.runner_sessions[session.session_id].state == "starting"
    refusal = next(
        refusal
        for refusal in after.refusals
        if refusal.record_id
        not in {existing.record_id for existing in before_signal.refusals}
    )
    assert refusal.input_kind == "workflow.refuse_runner_session_signal"
    assert refusal.reason == "context_mutation_refused"


def test_context_writeback_direct_update_is_accepted(
    tmp_path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    path = runtime.paths.workspace_path / "src" / "new.txt"

    def start(request: AdapterInvocationRequest) -> object:
        path.write_text("new\n", encoding="utf-8")
        return _writeback_success_start(
            request,
            artifact=_writeback_report(
                changes=(
                    {
                        "path": "src/new.txt",
                        "change_kind": "create",
                        "after_sha256": _digest(path),
                        "evidence_refs": ("runner:1",),
                        "classification": "direct_write",
                    },
                )
            ),
        )

    adapter = _RecordingAdapter(start)
    adapter.config = SimpleNamespace(
        cwd=runtime.paths.workspace_path,
        wrapper_protocol_version=4,
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id=state.runs[session.run_id].activation_id,
        local_config=_config(adapter),
    )
    after = _load(runtime)

    assert result.code == "observation_accepted"
    assert len(after.runner_observations) == len(state.runner_observations) + 1


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


def test_persisted_completion_stamps_runner_observation_from_completion_time(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _ready_runtime(tmp_path)
    original_decide = session_completion.decide
    observed_at: list[int | None] = []

    def capture_observation(current, transition_input, context):
        if isinstance(transition_input, RunnerResultObserved):
            observed_at.append(transition_input.observed_at)
        return original_decide(current, transition_input, context)

    monkeypatch.setattr(session_completion, "decide", capture_observation)
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(_success_start)),
    )
    state = _load(runtime)
    completion = next(iter(state.runner_session_completions.values()))

    assert result.code == "observation_accepted"
    assert observed_at == [completion.completed_at // 1_000_000_000]


def test_v3_observation_requires_exact_completion_session_and_application_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _ = _ready_state_with_selected_codex_authority()
    runtime = _runtime(tmp_path, state)
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
    stored = runtime.cas_store.get_bytes(completion.diagnostic_digest)
    diagnostic = runner_session_completion_diagnostic_from_payload(
        json.loads(stored)
    )
    assert diagnostic.diagnostic == {
        "diagnostics": {"reason": "[REDACTED] is unavailable"},
        "error_kind": "selected_authority_refused",
    }
    assert b"secret" not in stored


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
    diagnostic = runner_session_completion_diagnostic_from_payload(
        json.loads(stored)
    )
    assert diagnostic.diagnostic["diagnostics"]["truncated"] is True


def test_signal_digest_distinguishes_oversized_signals_with_same_prefix() -> None:
    common_prefix = "x" * (16 * 1024)

    first = session_diagnostics._signal_digest(
        {"diagnostic": common_prefix + "first"}
    )
    second = session_diagnostics._signal_digest(
        {"diagnostic": common_prefix + "second"}
    )

    assert first != second


def test_codex_and_generic_fake_adapters_share_session_lifecycle(tmp_path) -> None:
    for index, (local_config, state_factory) in enumerate(
        (
            (
                _codex_success_config(),
                _ready_state_with_selected_codex_authority,
            ),
            (_config(_RecordingAdapter(_success_start)), _ready_state),
        )
    ):
        state, _ = state_factory()
        runtime = _runtime(tmp_path / str(index), state)
        result = run_bounded_execution_unit(runtime, local_config=local_config)
        after = _load(runtime)

        assert result.code == "observation_accepted"
        session = next(iter(after.runner_sessions.values()))
        assert session.state == "completed"
        completion = after.runner_session_completions[session.session_id]
        assert completion.application_input_id in after.receipts


def test_mutation_preserves_usage_but_refuses_application(
    tmp_path,
) -> None:
    """A selected-root mutation refuses application after usage handling."""
    from compiler.test_context_bindings import _source_with_context_binding
    from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
    from millrace.adapters.cli.context import contextual_input_id
    from millrace.adapters.cli.context_checkout import prepare_context_checkout
    from millrace.compiler import authority_fingerprint, compile_workflow
    from millrace.contracts.state import DaemonBudgetEpochRecord
    from millrace.contracts.transition import AttachRunnerSessionContext
    from millrace.testing import fake_runner_session_state

    source = _source_with_context_binding(write_enabled=False)
    binding = source["context_bindings"][0]
    binding["required_sources"] = [
        {
            "source_kind": "workspace_relative_root",
            "source_ref": "src",
            "max_files": 8,
            "max_bytes": 4096,
        }
    ]
    binding["discoverable_sources"] = []
    binding["write_rules"] = []
    binding["mutation_policy"] = "forbid_selected_roots"
    result = compile_workflow(source)
    assert result.plan is not None, result.diagnostics
    fingerprint = authority_fingerprint(result.plan)
    state = bootstrap_to_taskmaster_claim(result.plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    runtime = _runtime(tmp_path, state)
    workspace = runtime.paths.workspace_path
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "existing.txt").write_text(
        "before\n",
        encoding="utf-8",
    )
    state = _load(runtime)
    run = state.runs["run-taskmaster"]
    session = state.runner_sessions["test-session:run-taskmaster"]
    binding_decl = next(
        declaration
        for declaration in state.admitted_plans[
            fingerprint
        ].selected_plan.context_bindings
        if str(declaration.stage_kind_id) == str(run.stage_kind_id)
    )
    prepared = prepare_context_checkout(
        paths=runtime.paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=binding_decl,
        state=state,
        cas_store=runtime.cas_store,
    )
    attachment = AttachRunnerSessionContext(
        f"cli:run.session-context-attach:{session.session_id}",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        context_manifest_digest=prepared.manifest_digest,
        selected_binding_id=str(binding_decl.id),
    )
    persisted = session_completion._persist_transition(
        runtime,
        replace(attachment, input_id=contextual_input_id(attachment)),
    )
    assert persisted is not None
    state = _load(runtime)
    run = state.runs[session.run_id]
    session = state.runner_sessions[session.session_id]
    epoch = DaemonBudgetEpochRecord(
        budget_id="b1-mutation-refusal-usage",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=1,
        max_total_tokens=100,
        started_at=0,
        wall_deadline=None,
        last_observed_at=0,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    mutated_path = workspace / "src" / "mutated.txt"

    def start(request: AdapterInvocationRequest) -> object:
        mutated_path.write_text("mutated\n", encoding="utf-8")
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            marker="TASK_COMPLETE",
            observation_payload_candidate={"summary": "completed"},
            artifact_payload_candidate=_writeback_report(no_op_reason="No update."),
            token_usage=AdapterTokenUsage(
                input_tokens=7,
                output_tokens=5,
                total_tokens=12,
            ),
            attribution=AdapterAttribution(
                cached_input_tokens=11,
                reasoning_tokens=13,
                provider_event_count=17,
                provider_event_bytes=19,
                wrapper_input_bytes=23,
                retained_result_bytes=31,
                tool_call_event_count=37,
                runner_wall_milliseconds=47,
            ),
        )
        return replace(
            _success_start(request),
            handle=_ImmediateHandle(outcome),
        )

    adapter = _RecordingAdapter(start)
    adapter.config = SimpleNamespace(cwd=workspace, wrapper_protocol_version=4)
    before = _load(runtime)
    result2 = run_bounded_execution_unit(
        runtime,
        activation_id=state.runs[session.run_id].activation_id,
        local_config=_config(adapter),
        on_accepted_start=lambda started: runtime.store.record_budgeted_runner_start(
            epoch.budget_id,
            started,
        ),
    )
    reopened = _reopen_runtime(runtime)
    after = _load(reopened)
    assert result2.code == "adapter_failure"
    assert result2.adapter_error_kind == "context_mutation_refused"
    assert after.runner_observations == before.runner_observations
    completion = after.runner_session_completions[session.session_id]
    assert completion.terminal_state == "failed"
    assert completion.exit_kind == "error"
    assert completion.adapter_error_kind == "context_mutation_refused"
    assert completion.runner_result_evidence_digest is None
    stored = reopened.cas_store.get_bytes(completion.diagnostic_digest)
    diagnostic = runner_session_completion_diagnostic_from_payload(
        json.loads(stored)
    )
    assert diagnostic.diagnostic == {
        "context_writeback_refusal": "selected live context files changed"
    }
    usage = reopened.store.load_runner_session_usage(session.session_id)
    assert usage is not None
    assert usage.final is True
    assert (usage.budget_id, usage.run_id) == (epoch.budget_id, session.run_id)
    assert (usage.dispatch_generation, usage.session_fencing_token) == (
        session.dispatch_generation,
        session.session_fencing_token,
    )
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (7, 5, 12)
    attribution = reopened.store.load_runner_session_attribution_authenticated(
        session.session_id,
        session.dispatch_generation,
        session.session_fencing_token,
    )
    assert attribution is not None
    assert attribution.final is True
    for name, value in {
        "cached_input_tokens": 11, "reasoning_tokens": 13,
        "provider_event_count": 17, "provider_event_bytes": 19,
        "wrapper_input_bytes": 23, "retained_result_bytes": 31,
        "tool_call_event_count": 37, "runner_wall_milliseconds": 47,
    }.items():
        metric = attribution.metrics[name]
        assert (metric.value, metric.source, metric.availability) == (
            value,
            "adapter.direct",
            "observed",
        )
    manifest_digest = session.context_manifest_digest
    assert manifest_digest is not None
    manifest_bytes = reopened.cas_store.get_bytes(manifest_digest)
    manifest = decode_context_checkout_manifest(manifest_bytes)
    hydration_receipts = reopened.store.load_context_hydration_receipts_authenticated(
        session.session_id, session.dispatch_generation, session.session_fencing_token
    )
    context_values = {
        "manifest_bytes": len(manifest_bytes),
        "catalog_bytes": sum(item.byte_length for item in manifest.catalog),
        "catalog_file_count": len(manifest.catalog),
        "hydrated_bytes": sum(receipt.byte_length for receipt in hydration_receipts),
        "hydrated_file_count": len(hydration_receipts),
        "distinct_content_digest_count": len(
            {receipt.content_digest for receipt in hydration_receipts}
        ),
    }
    manifest_metric_names = {
        "manifest_bytes", "catalog_bytes", "catalog_file_count"
    }
    for name, value in context_values.items():
        metric = attribution.metrics[name]
        assert metric.value == value
        assert metric.source == (
            "runtime.context_manifest"
            if name in manifest_metric_names
            else "runtime.hydration_receipts"
        )
        assert metric.availability == (
            "observed" if name == "manifest_bytes" else "derived"
        )
    cleanup = reopened.store.load_context_cleanup_receipt_authenticated(
        session.session_id,
        session.dispatch_generation,
        manifest_digest,
        session.session_fencing_token,
    )
    assert cleanup is not None
    assert cleanup.adapter_cleanup_disposition == "not_required"
    assert cleanup.removed_path_classes == ("context_checkout",)
    assert cleanup.removed_file_count == len(manifest.files) + 1
    assert cleanup.removed_byte_count == (
        len(manifest_bytes) + sum(item.byte_length for item in manifest.files)
    )
    checkout = (
        reopened.paths.workspace_path / str(binding_decl.checkout_root)
        / session.session_id / str(session.dispatch_generation)
    )
    assert not checkout.exists()
    assert reopened.cas_store.get_bytes(manifest_digest) == manifest_bytes

    assert any(
        refusal.reason == "context_mutation_refused" for refusal in after.refusals
    )
    reopened.close()


def test_public_context_writeback_refusal_redacts_dynamic_exception() -> None:
    assert session_diagnostics._public_context_writeback_refusal(
        "live context root scan failed: /private/secret/path"
    ) == "live context root scan failed"


def test_unrelated_authority_refusal_does_not_gain_usage_writes(tmp_path) -> None:
    """An authority mismatch does not create a session completion or usage write."""
    result, before_signal, after, _ = _completion_signal_result(
        tmp_path,
        lambda request: _success_outcome(
            request,
            dispatch_echo=_mismatched_echo(request),
        ),
    )
    assert result.code == "session_reconciliation_required"
    assert after.runner_observations == before_signal.runner_observations
    assert len(after.runner_session_completions) == len(
        before_signal.runner_session_completions
    )


def test_authenticated_completion_persists_source_backed_attribution(
    tmp_path,
) -> None:
    runtime, state, session, binding = _bound_fixture(tmp_path)
    checkout = (
        runtime.paths.workspace_path
        / str(binding.checkout_root)
        / session.session_id
        / str(session.dispatch_generation)
    )
    assert checkout.is_dir()

    def start(request: AdapterInvocationRequest) -> object:
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            marker="WORK_COMPLETE",
            observation_payload_candidate={"summary": "ok"},
            artifact_payload_candidate=_writeback_report(
                no_op_reason="No update required."
            ),
            attribution=AdapterAttribution(
                cached_input_tokens=11,
                wrapper_input_bytes=23,
                retained_result_bytes=31,
                runner_wall_milliseconds=47,
            ),
        )
        return replace(
            _success_start(request),
            handle=_ImmediateHandle(outcome),
        )

    adapter = _RecordingAdapter(start)
    adapter.config = SimpleNamespace(
        cwd=runtime.paths.workspace_path,
        wrapper_protocol_version=4,
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id=state.runs[session.run_id].activation_id,
        local_config=_config(adapter),
    )

    assert result.code == "observation_accepted"
    record = runtime.store.load_runner_session_attribution_authenticated(
        session.session_id,
        session.dispatch_generation,
        session.session_fencing_token,
    )
    assert record is not None
    assert record.final is True
    assert record.metrics["cached_input_tokens"].payload() == {
        "value": 11,
        "source": "adapter.direct",
        "availability": "observed",
    }
    assert record.metrics["wrapper_input_bytes"].value == 23
    assert record.metrics["retained_result_bytes"].value == 31
    assert record.metrics["runner_wall_milliseconds"].value == 47
    assert record.metrics["reasoning_tokens"].value is None
    assert record.metrics["reasoning_tokens"].availability == "unavailable"

    manifest_digest = session.context_manifest_digest
    assert manifest_digest is not None
    manifest_bytes = runtime.cas_store.get_bytes(manifest_digest)
    manifest = decode_context_checkout_manifest(manifest_bytes)
    receipts = runtime.store.load_context_hydration_receipts_authenticated(
        session.session_id,
        session.dispatch_generation,
        session.session_fencing_token,
    )
    assert record.metrics["manifest_bytes"].value == len(manifest_bytes)
    assert record.metrics["catalog_bytes"].value == sum(
        item.byte_length for item in manifest.catalog
    )
    assert record.metrics["hydrated_bytes"].value == sum(
        receipt.byte_length for receipt in receipts
    )
    assert record.metrics["catalog_file_count"].value == len(manifest.catalog)
    assert record.metrics["hydrated_file_count"].value == len(receipts)
    assert record.metrics["distinct_content_digest_count"].value == len(
        {receipt.content_digest for receipt in receipts}
    )
    cleanup = runtime.store.load_context_cleanup_receipt_authenticated(
        session.session_id,
        session.dispatch_generation,
        manifest_digest,
        session.session_fencing_token,
    )
    assert cleanup is not None
    assert cleanup.adapter_cleanup_disposition == "not_required"
    assert cleanup.removed_path_classes == ("context_checkout",)
    assert cleanup.removed_file_count == len(manifest.files) + 1
    assert cleanup.removed_byte_count == len(manifest_bytes) + sum(
        item.byte_length for item in manifest.files
    )
    assert not checkout.exists()
    assert runtime.cas_store.get_bytes(manifest_digest) == manifest_bytes
    after = _load(runtime)
    assert after.runner_session_completions[session.session_id].terminal_state == (
        "completed"
    )
    assert after.runner_session_completions[
        session.session_id
    ].application_input_id in after.receipts


def test_authenticated_adapter_error_persists_final_attribution(tmp_path) -> None:
    runtime = _ready_runtime(tmp_path)

    def start(request: AdapterInvocationRequest) -> object:
        outcome = replace(
            _error_outcome(request, dispatch_echo=_dispatch_echo(request)),
            attribution=AdapterAttribution(provider_event_count=3),
        )
        return replace(
            _success_start(request),
            handle=_ImmediateHandle(outcome),
        )

    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    state = _load(runtime)
    session = next(iter(state.runner_sessions.values()))

    assert result.code == "adapter_failure"
    record = runtime.store.load_runner_session_attribution_authenticated(
        session.session_id,
        session.dispatch_generation,
        session.session_fencing_token,
    )
    assert record is not None
    assert record.final is True
    assert record.metrics["provider_event_count"].value == 3
    assert record.metrics["provider_event_count"].source == "adapter.direct"


def test_unrelated_authority_refusal_does_not_persist_attribution(tmp_path) -> None:
    runtime = _ready_runtime(tmp_path)

    def start(request: AdapterInvocationRequest) -> object:
        outcome = replace(
            _success_outcome(
                request,
                dispatch_echo=_mismatched_echo(request),
            ),
            attribution=AdapterAttribution(wrapper_input_bytes=99),
        )
        return replace(
            _success_start(request),
            handle=_ImmediateHandle(outcome),
        )

    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(_RecordingAdapter(start)),
    )
    state = _load(runtime)
    session = next(iter(state.runner_sessions.values()))

    assert result.code == "session_reconciliation_required"
    assert (
        runtime.store.load_runner_session_attribution(
            session.session_id,
            session.dispatch_generation,
        )
        is None
    )
    assert runtime.store.load_runner_session_usage(session.session_id) is None
