from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tests.cli.test_cli_bounded_execution_unit import _load, _runtime
from tests.cli.test_cli_daemon_session import _SignalWaitAdapter
from tests.cli.test_cli_runtime_failure_recovery import (
    _mapped_generic_state,
    _timeout_config,
)

from millrace.adapters.cli import (
    session_completion,
    session_coordinator,
    session_runtime_failures,
)
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterSuccessResult,
    StartRefusedBeforeExternalWork,
    start_refusal_diagnostic_digest,
)
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_eligible,
)
from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
from millrace.substrate.errors import StorageIntegrityError
from support import generic_fanout
from support.runner_sessions import (
    _dispatch_echo,
    _error_outcome,
    _ImmediateHandle,
    _RecordingAdapter,
    _started_session,
)


@pytest.mark.parametrize(
    "failure_kind",
    [
        "cancelled",
        "missing_opt_in_config",
        "unsupported_adapter_kind",
        "input_too_large",
        "redaction_refused",
        "selected_authority_refused",
    ],
)
def test_safety_configuration_and_cancel_errors_do_not_take_recovery_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    runtime = _runtime(tmp_path, _mapped_generic_state(monkeypatch))
    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=_timeout_config(failure_kind)[0],
    )
    after = _load(runtime)
    completion = after.runner_session_completions[
        cast(str, after.runs[cast(str, result.run_id)].current_session_id)
    ]
    assert not result.accepted
    assert result.adapter_error_kind == failure_kind
    assert not runtime_failure_eligible(after, completion)
    assert not runtime_failure_applied(after, completion.run_id)
    assert after.activation_routes == ()
    assert after.recovery_attempts == {}
    runtime.close()


def test_authenticated_invocation_refused_before_external_work_can_recover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(
        tmp_path, _mapped_generic_state(monkeypatch, failure_kind="invocation_failed")
    )

    def start(request: AdapterInvocationRequest):
        echo = _dispatch_echo(request)
        error = _error_outcome(
            request, dispatch_echo=echo, error_kind="invocation_failed"
        )
        return StartRefusedBeforeExternalWork(
            echo, error, start_refusal_diagnostic_digest(error)
        )

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=AdapterLocalConfig(adapters={"codex": _RecordingAdapter(start)}),
    )
    assert result.code == "runtime_failure_recovery_applied"
    after = _load(runtime)
    completion = next(iter(after.runner_session_completions.values()))
    assert completion.started_at is None
    assert completion.cleanup_disposition == "not_required"
    assert completion.adapter_error_kind == "invocation_failed"
    assert runtime_failure_applied(after, completion.run_id)
    runtime.close()


def test_campaign_wall_deadline_is_shutdown_not_stage_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path, _mapped_generic_state(monkeypatch))
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(session_coordinator, "_monotonic", lambda: next(clock))
    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        max_timeout_seconds=1,
        local_config=AdapterLocalConfig(adapters={"codex": _SignalWaitAdapter()}),
    )
    after = _load(runtime)
    cancellation = next(iter(after.runner_session_cancellation_requests.values()))
    assert cancellation.reason == "daemon_shutdown"
    assert cancellation.source_kind == "daemon"
    assert result.code == "adapter_failure"
    assert after.activation_routes == ()
    assert after.recovery_attempts == {}
    runtime.close()


def _pending_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime = _runtime(
        tmp_path, _mapped_generic_state(monkeypatch, failure_kind="result_rejected")
    )

    def start(request: AdapterInvocationRequest):
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            marker="UNKNOWN_RESULT",
            artifact_payload_candidate=None,
        )
        return _started_session(
            request, _ImmediateHandle(outcome, cleanup_disposition="complete")
        )

    apply_failure = session_runtime_failures.apply_persisted_runtime_failure
    monkeypatch.setattr(
        session_runtime_failures,
        "apply_persisted_runtime_failure",
        lambda *_args, **_kwargs: session_completion.SessionExecutionResult(
            "adapter_failure"
        ),
    )
    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=AdapterLocalConfig(adapters={"codex": _RecordingAdapter(start)}),
    )
    monkeypatch.setattr(
        session_runtime_failures, "apply_persisted_runtime_failure", apply_failure
    )
    assert result.code == "observation_refused"
    state = _load(runtime)
    completion = next(iter(state.runner_session_completions.values()))
    assert runtime_failure_eligible(state, completion)
    return runtime, state, completion


@pytest.mark.parametrize(
    "part",
    [
        "receipt",
        "transition",
        "refusal",
        "trace",
        "governance",
        "current_session",
        "dispatch_generation",
        "session_fence",
        "cleanup",
        "authority_refusal",
    ],
)
def test_rejected_result_requires_exact_current_refusal_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    part: str,
) -> None:
    runtime, state, completion = _pending_rejected(tmp_path, monkeypatch)
    input_id = completion.application_input_id
    if part == "receipt":
        bad = replace(
            state, receipts={k: v for k, v in state.receipts.items() if k != input_id}
        )
    elif part == "transition":
        bad = replace(
            state,
            transitions=tuple(t for t in state.transitions if t.input_id != input_id),
        )
    elif part == "refusal":
        bad = replace(
            state, refusals=tuple(r for r in state.refusals if r.input_id != input_id)
        )
    elif part == "trace":
        bad = replace(
            state, traces=tuple(t for t in state.traces if t.input_id != input_id)
        )
    elif part == "governance":
        bad = replace(
            state,
            governance_events=tuple(
                e for e in state.governance_events if e.input_id != input_id
            ),
        )
    elif part == "current_session":
        run = state.runs[completion.run_id]
        bad = replace(
            state,
            runs={
                **state.runs,
                completion.run_id: replace(run, current_session_id="foreign"),
            },
        )
    elif part in {"dispatch_generation", "session_fence", "cleanup"}:
        mutation = {
            "dispatch_generation": {
                "dispatch_generation": completion.dispatch_generation + 1
            },
            "session_fence": {"session_fencing_token": "foreign"},
            "cleanup": {"cleanup_disposition": "orphan_risk", "terminal_state": "lost"},
        }[part]
        completion = replace(completion)
        for field_name, value in mutation.items():
            object.__setattr__(completion, field_name, value)
        bad = replace(
            state, runner_session_completions={completion.session_id: completion}
        )
    else:
        reason = "invalid_observation_authority"
        bad = replace(
            state,
            receipts={
                **state.receipts,
                input_id: replace(state.receipts[input_id], refusal_reason=reason),
            },
            refusals=tuple(
                replace(r, reason=reason) if r.input_id == input_id else r
                for r in state.refusals
            ),
            traces=tuple(
                replace(t, refusal_reason=reason) if t.input_id == input_id else t
                for t in state.traces
            ),
            governance_events=tuple(
                replace(e, refusal_reason=reason) if e.input_id == input_id else e
                for e in state.governance_events
            ),
        )
    assert not runtime_failure_eligible(bad, completion)
    result = session_completion._apply_persisted_runtime_failure(
        runtime, bad, completion, daemon_stop_requested=None
    )
    assert not result.accepted
    assert bad.activation_routes == ()
    assert bad.recovery_attempts == {}
    runtime.close()


def test_rejected_result_refuses_coherent_receipt_with_wrong_evidence_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, state, completion = _pending_rejected(tmp_path, monkeypatch)
    original = state.receipts[completion.application_input_id]
    foreign_receipt = replace(
        original,
        receipt_ref=replace(
            original.receipt_ref, input_payload_digest="sha256:" + "f" * 64
        ),
    )
    bad = replace(
        state,
        receipts={**state.receipts, completion.application_input_id: foreign_receipt},
    )
    assert runtime_failure_eligible(
        bad, completion
    )  # Structural proof still requires CAS authentication.
    result = session_completion._apply_persisted_runtime_failure(
        runtime, bad, completion, daemon_stop_requested=None
    )
    assert result.code == "completion_refused"
    assert bad.activation_routes == ()
    runtime.close()


@pytest.mark.parametrize(
    "part", ["attempts", "cleanup", "operation", "receipt", "digest"]
)
def test_deadline_recovery_refuses_missing_or_forged_cleanup_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    part: str,
) -> None:
    runtime = _runtime(tmp_path, _mapped_generic_state(monkeypatch))
    clock = iter((0.0, 10_000.0))
    monkeypatch.setattr(session_coordinator, "_monotonic", lambda: next(clock))
    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=AdapterLocalConfig(adapters={"codex": _SignalWaitAdapter()}),
    )
    assert result.code == "runtime_failure_recovery_applied"
    state = _load(runtime)
    completion = next(iter(state.runner_session_completions.values()))
    attempts = dict(state.runner_session_cancellation_attempts)
    cleanup = next(a for a in attempts.values() if a.operation == "transport_cleanup")
    receipts = dict(state.receipts)
    if part == "attempts":
        attempts = {}
    elif part == "cleanup":
        attempts.pop(cleanup.attempt_id)
    elif part == "operation":
        attempts[cleanup.attempt_id] = replace(cleanup, operation="kill")
    elif part == "digest":
        attempts[cleanup.attempt_id] = replace(
            cleanup, bounded_diagnostic_digest="sha256:" + "e" * 64
        )
    else:
        receipts.pop(
            f"cli:run.session-cancel-attempt:{cleanup.session_id}:{cleanup.sequence}"
        )
    bad = replace(
        state, runner_session_cancellation_attempts=attempts, receipts=receipts
    )
    assert not runtime_failure_eligible(bad, completion)
    assert not runtime_failure_applied(bad, completion.run_id)
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(bad)
    runtime.close()


@pytest.mark.parametrize(
    "field",
    [
        "policy_id",
        "attempt_count",
        "source_stage_kind_id",
        "source_graph_node_id",
        "source_runner_binding_id",
        "failure_kind",
        "refusal_reason",
        "source_session_id",
        "diagnostic_digest",
        "omitted_context",
    ],
)
def test_recovery_context_requires_the_complete_native_current_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    from millrace.adapters.cli.recovery_context import (
        materialize_recovery_failure_context,
    )
    from millrace.operator.dispatch import (
        DispatchProjectionError,
        build_dispatch_envelope_for_run,
    )

    runtime = _runtime(
        tmp_path, _mapped_generic_state(monkeypatch, failure_kind="result_parse_failed")
    )
    first = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=_timeout_config("result_parse_failed")[0],
    )
    assert first.code == "runtime_failure_recovery_applied"

    def start(request: AdapterInvocationRequest):
        state = _load(runtime)
        native = build_dispatch_envelope_for_run(
            state=state, run_id=request.dispatch_envelope.run_id
        )
        governance = dict(native.governance_context)
        source = dict(governance["runtime_failure_source"])
        if field == "omitted_context":
            governance.pop("runtime_failure_source")
        else:
            source[field] = 999 if field == "attempt_count" else "foreign"
            governance["runtime_failure_source"] = source
        foreign = replace(native, governance_context=governance)
        with pytest.raises(DispatchProjectionError):
            materialize_recovery_failure_context(runtime, state, foreign)
        assert (
            materialize_recovery_failure_context(runtime, state, native)
            == request.dispatch_envelope
        )
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            marker="ADMISSION_RECOVERED",
            artifact_payload_candidate=generic_fanout.packet_payload(),
        )
        return _started_session(
            request, _ImmediateHandle(outcome, cleanup_disposition="complete")
        )

    result = run_bounded_execution_unit(
        runtime,
        local_config=AdapterLocalConfig(adapters={"codex": _RecordingAdapter(start)}),
    )
    assert result.code == "observation_accepted"
    runtime.close()


def test_rejected_result_reconciles_before_new_dispatch_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.cli.test_cli_bounded_execution_unit import _reopen_runtime
    from tests.cli.test_cli_runtime_failure_recovery import _recovery_success_config

    from millrace.adapters.cli.run import reconcile_pending_runner_completions

    runtime, _state, completion = _pending_rejected(tmp_path, monkeypatch)
    reopened = _reopen_runtime(runtime)
    config, adapter = _recovery_success_config()
    result = reconcile_pending_runner_completions(reopened, local_config=config)
    assert result.code == "runtime_failure_recovery_applied"
    after = _load(reopened)
    assert runtime_failure_applied(after, completion.run_id)
    assert after.runner_session_completions[completion.session_id] == completion
    assert after.receipts[completion.application_input_id].accepted is False
    assert adapter.requests == []
    assert after.runner_observations == {}
    reopened.close()


def test_drain_arriving_during_deadline_cleanup_defers_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.cli.test_cli_daemon_session import _SignalWaitHandle

    from millrace.adapters.runner_contract import StartedSession

    runtime = _runtime(tmp_path, _mapped_generic_state(monkeypatch))
    stop = {"requested": False}
    clock = iter((0.0, 10_000.0))
    monkeypatch.setattr(session_coordinator, "_monotonic", lambda: next(clock))

    class Handle(_SignalWaitHandle):
        def cleanup(self):
            result = super().cleanup()
            stop["requested"] = True
            return result

    def start(request: AdapterInvocationRequest):
        return StartedSession(
            _dispatch_echo(request), Handle(request), "deadline-drain", {}
        )

    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=AdapterLocalConfig(adapters={"codex": _RecordingAdapter(start)}),
        daemon_stop_requested=lambda: stop["requested"],
    )
    assert result.code == "runner_session_waiting"
    state = _load(runtime)
    completion = next(iter(state.runner_session_completions.values()))
    assert runtime_failure_eligible(state, completion)
    assert state.activation_routes == ()
    assert state.recovery_attempts == {}
    assert completion.application_input_id not in state.receipts
    applied = session_completion._apply_persisted_runtime_failure(
        runtime,
        state,
        completion,
        daemon_stop_requested=lambda: False,
    )
    assert applied.code == "runtime_failure_recovery_applied"
    assert runtime_failure_applied(_load(runtime), completion.run_id)
    runtime.close()
