from __future__ import annotations

import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cli.test_cli_projections import inventory, invoke
from millrace.adapters.cli.history_projection import runner_history
from millrace.adapters.runner_contract import RedactionPolicy
from millrace.contracts.public_projections import decode_cursor, encode_cursor
from millrace.substrate.runner_session_events import (
    RunnerSessionEventStore,
    RunnerSessionEventWriter,
    runner_session_event_store_path,
)
from millrace.substrate.sqlite import ControlDecision
from support.run_controls import request_for, runtime_with_run


def events_for(runtime, run, count=3):
    state = runtime.store.load_runtime_state(runtime.cas_store)
    session = state.runner_sessions[run.current_session_id]
    store = RunnerSessionEventStore.initialize(
        runner_session_event_store_path(runtime.paths.db_path)
    )
    writer = RunnerSessionEventWriter(
        store,
        session_id=session.session_id,
        run_id=session.run_id,
        dispatch_generation=session.dispatch_generation,
        redaction_policy=RedactionPolicy(policy_id="test-redaction"),
    )
    for n in range(count):
        writer.record_progress(
            {"text": "private prompt /private/file token"},
            observed_at=n,
            replay_key=str(n),
        )
    store.close()


@pytest.mark.parametrize("session", [False, True])
def test_no_session_differs_from_missing_sidecar(tmp_path, session):
    runtime, run = runtime_with_run(tmp_path, session=session)
    before = inventory(tmp_path)
    code, data = invoke(runtime, "runs", "follow", run.run_ref.run_id)
    assert code == 0, data
    result = data["data"]
    assert result["history_status"] == (
        "history_unavailable" if session else "not_started"
    )
    assert (result["gap"] is not None) == session
    assert inventory(tmp_path) == before
    runtime.close()


def test_exact_cursor_gaps_ahead_corrupt_nonmutation_and_safe_telemetry(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    events_for(runtime, run)
    before = inventory(tmp_path)
    code, output = invoke(
        runtime, "--page-size", "1", "runs", "follow", run.run_ref.run_id
    )
    assert code == 0, output
    data = output["data"]
    assert data["history_status"] == "available" and data["has_more"]
    assert data["earliest_retained_sequence"] == 1
    assert data["next_after_sequence"] == 1
    assert "private prompt" not in json.dumps(data)
    assert inventory(tmp_path) == before
    token = decode_cursor(data["next_cursor"])
    for field, value in (
        ("session_fence", "wrong"),
        ("store_epoch", "wrong"),
        ("run_id", "wrong"),
        ("session_id", "wrong"),
        ("dispatch_generation", 99),
    ):
        bad = encode_cursor({**token, field: value})
        assert (
            invoke(runtime, "--cursor", bad, "runs", "follow", run.run_ref.run_id)[1][
                "code"
            ]
            == "cursor_scope_mismatch"
        )
    assert (
        invoke(
            runtime,
            "--cursor",
            encode_cursor({**token, "after_sequence": 99}),
            "runs",
            "follow",
            run.run_ref.run_id,
        )[1]["code"]
        == "cursor_ahead"
    )
    assert (
        invoke(runtime, "runs", "follow", run.run_ref.run_id, "--after-sequence", "-1")[
            1
        ]["code"]
        == "invalid_after_sequence"
    )
    path = runner_session_event_store_path(runtime.paths.db_path)
    import sqlite3

    db = sqlite3.connect(path)
    db.execute("DELETE FROM session_events WHERE sequence=2")
    db.commit()
    db.close()
    writer_store = RunnerSessionEventStore.open(path)
    writer_store._publish_snapshot()
    writer_store.close()
    code, gap = invoke(
        runtime, "--cursor", data["next_cursor"], "runs", "follow", run.run_ref.run_id
    )
    assert code == 0 and gap["data"]["history_status"] == "history_gap"
    assert gap["data"]["gap"]["resumes_at_sequence"] == 3
    path.write_bytes(b"private corrupt prompt")
    before = inventory(tmp_path)
    code, corrupt = invoke(runtime, "runs", "follow", run.run_ref.run_id)
    assert code == 0 and corrupt["data"]["history_status"] == "history_corrupt"
    assert inventory(tmp_path) == before
    runtime.close()


def test_retained_old_session_never_switches_to_new_session(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    events_for(runtime, run)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    old = state.runner_sessions[run.current_session_id]
    from millrace.adapters.cli.context import transition_context
    from millrace.contracts.state import RunnerSessionCompletionRecord
    from millrace.contracts.transition import (
        CreateRunnerSession,
        RecordRunnerSessionCompletion,
    )
    from millrace.kernel import apply, decide

    completion = RunnerSessionCompletionRecord(
        completion_id="old-failed",
        session_id=old.session_id,
        run_id=old.run_id,
        dispatch_generation=old.dispatch_generation,
        session_fencing_token=old.session_fencing_token,
        terminal_state="failed",
        exit_kind="adapter_error",
        adapter_outcome_kind=None,
        adapter_error_kind="fixture",
        runner_result_evidence_digest=None,
        primary_cancellation_request_id=None,
        cleanup_disposition="not_required",
        started_at=None,
        cancel_requested_at=None,
        completed_at=101,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="fixture",
        diagnostic_digest=runtime.cas_store.put_bytes(b"fixture failed completion"),
        application_input_id="cli:run.session-completion:old-failed",
    )
    for command in (
        RecordRunnerSessionCompletion(
            "old-record",
            run_ref=run.run_ref,
            expected_state="created",
            completion=completion,
        ),
        CreateRunnerSession(
            "explicit-retry",
            run_ref=run.run_ref,
            session_id="new-session",
            session_fencing_token="new-fence",
            created_at=102,
            explicit_retry_intent=True,
        ),
    ):
        decision = decide(
            state,
            command,
            transition_context(command="fixture", input_id_value=command.input_id),
        )
        assert decision.accepted
        state = apply(state, decision)
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    assert state.runs[run.run_ref.run_id].current_session_id == "new-session"
    code, projected = invoke(
        runtime,
        "--session-id",
        old.session_id,
        "--page-size",
        "1",
        "runs",
        "follow",
        run.run_ref.run_id,
    )
    assert code == 0, projected
    assert projected["data"]["scope"]["session_id"] == old.session_id
    code, continued = invoke(
        runtime,
        "--cursor",
        projected["data"]["next_cursor"],
        "runs",
        "follow",
        run.run_ref.run_id,
    )
    assert code == 0 and all(
        e["session_id"] == old.session_id for e in continued["data"]["events"]
    )
    ns = SimpleNamespace(
        run_id=run.run_ref.run_id, session_id=old.session_id, page_size=1
    )
    data = runner_history(
        runtime, state, runtime.store.control_identity(), ns, lambda *args: {}
    )
    assert data["scope"]["session_id"] == old.session_id
    ns.session_id = None
    ns.cursor = data["next_cursor"]
    second = runner_history(
        runtime, state, runtime.store.control_identity(), ns, lambda *args: {}
    )
    assert second["events"][0]["session_id"] == old.session_id
    assert second["next_after_sequence"] == 2
    runtime.close()


def test_durable_control_history_pending_settlement_and_separate_fresh_state(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    request = request_for(runtime, run)
    original = runtime.store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending_native_fixture")
    )
    code, first = invoke(
        runtime, "operations", "history", "--run-id", run.run_ref.run_id
    )
    assert code == 0, first
    rows = first["data"]["records"]
    assert [row["kind"] for row in rows] == ["receipt", "result"]
    assert rows[0]["record"] == original["receipt"]
    assert rows[1]["record"]["stage"] == "pending"
    runtime.store.append_operation_result(
        request,
        result_id="settled",
        stage="applied",
        evidence={"effect_status": "fixture_only"},
    )
    code, second = invoke(
        runtime, "operations", "history", "--run-id", run.run_ref.run_id
    )
    assert code == 0, second
    assert second["data"]["records"][:2] == rows
    assert second["data"]["records"][-1]["record"]["stage"] == "applied"
    assert second["data"]["fresh_state"]["execution_state"] == "runnable_unstarted"
    assert not second["data"]["nonacceptance_proven"]
    runtime.close()


def test_control_history_exceeds_old_aggregate_limit_and_remains_pageable(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    request = request_for(runtime, run)
    runtime.store.execute_operation(
        request, lambda: ControlDecision("applied", "fixture_applied")
    )
    for number in range(110):
        runtime.store.append_operation_result(
            request,
            result_id=f"aftermath-{number}",
            stage="aftermath",
            evidence={"status": "fixture"},
        )
    cursor = None
    rows = []
    while True:
        args = [] if cursor is None else ["--cursor", cursor]
        code, result = invoke(
            runtime, *args, "operations", "history", "--run-id", run.run_ref.run_id
        )
        assert code == 0, result
        rows.extend(result["data"]["records"])
        cursor = result["data"]["next_cursor"]
        if cursor is None:
            break
    assert len(rows) == 112 and len({row["id"] for row in rows}) == 112
    runtime.close()


def test_complete_daemon_history_keeps_all_fences_and_unknown_cleanup(tmp_path):
    from substrate.test_daemon_controls import accept, registered, stop_request

    runtime, _ = runtime_with_run(tmp_path)
    if sys.platform != "darwin":
        from millrace.substrate.errors import ControlOperationError

        with pytest.raises(ControlOperationError, match="daemon_history_unknown"):
            registered(runtime)
        runtime.close()
        return
    scope = registered(runtime)
    fences = [
        {
            "session_id": f"s-{n:04}",
            "run_id": f"r-{n:04}",
            "dispatch_generation": 1,
            "session_fencing_token": f"f-{n:04}",
        }
        for n in range(125)
    ]
    runtime.store.attach_daemon_sessions(scope, fences)
    accept(runtime, scope, stop_request(runtime, scope))
    runtime.store.finish_daemon(
        scope,
        {"listener_teardown": "complete", "stopped_reason": "signal"},
        [{"fence": f, "cleanup": "unknown"} for f in fences],
        runtime.store.control_identity()["source_revision"],
    )
    before = inventory(tmp_path)
    records, cursor = [], None
    while True:
        args = [] if cursor is None else ["--cursor", cursor]
        code, output = invoke(
            runtime,
            "--page-size",
            "50",
            "--daemon-id",
            scope["daemon_id"],
            *args,
            "daemon",
            "history",
        )
        assert code == 0, output
        records.extend(output["data"]["records"])
        cursor = output["data"]["next_cursor"]
        if cursor is None:
            break
    sessions = [r for r in records if r["kind"] == "daemon_session"]
    assert len(sessions) == 125
    assert {r["session_id"] for r in sessions} == {f["session_id"] for f in fences}
    assert all(r["cleanup"] == "unknown" for r in sessions)
    assert all(
        r["last_known_durable_progress"]["availability"] == "unavailable"
        for r in sessions
    )
    assert inventory(tmp_path) == before
    runtime.close()


def test_capture_provenance_does_not_certify_backing_health(tmp_path):
    from millrace.substrate.runner_session_events import public_snapshot_path

    runtime, run = runtime_with_run(tmp_path, session=True)
    events_for(runtime, run)
    path = runner_session_event_store_path(runtime.paths.db_path)
    snapshot = public_snapshot_path(path)
    code, output = invoke(runtime, "runs", "follow", run.run_ref.run_id)
    assert code == 0
    data = output["data"]
    assert data["capture"]["current_backing_health_proven"] is False
    assert data["backing_store"] == {
        "presence": "present",
        "validation": "header_only",
        "health": "unknown",
    }
    snapshot.write_bytes(b"corrupt capture")
    data = invoke(runtime, "runs", "follow", run.run_ref.run_id)[1]["data"]
    assert data["history_status"] == "history_corrupt" and data["capture"] is None
    assert data["backing_store"]["validation"] == "header_only"
    path.write_bytes(b"corrupt backing")
    data = invoke(runtime, "runs", "follow", run.run_ref.run_id)[1]["data"]
    assert data["history_status"] == "history_corrupt"
    assert data["backing_store"]["health"] == "corrupt"
    path.unlink()
    data = invoke(runtime, "runs", "follow", run.run_ref.run_id)[1]["data"]
    assert data["history_status"] == "history_unavailable"
    assert data["backing_store"]["presence"] == "missing"
    assert data["durable_final"]["session_id"] == run.current_session_id
    runtime.close()


def test_non_cli_completion_digest_join_and_refused_application_are_public(tmp_path):
    from millrace.adapters.cli.context import transition_context
    from millrace.adapters.cli.projections import _completion_input_id
    from millrace.contracts.runner import (
        RunnerResultEvidence,
        runner_result_evidence_bytes,
    )
    from millrace.contracts.state import RunnerSessionCompletionRecord
    from millrace.contracts.transition import (
        RecordRunnerSessionCompletion,
        RunnerResultObserved,
    )
    from millrace.kernel import apply, decide
    from support.run_controls import start_intent

    runtime, run = runtime_with_run(tmp_path, session=True)
    start_intent(runtime, run)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    session = state.runner_sessions[run.current_session_id]
    evidence = RunnerResultEvidence(
        run_id=run.run_ref.run_id,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        claim_id=run.run_ref.claim_id,
        generation=run.run_ref.generation,
        fencing_token=run.run_ref.fencing_token,
        stage_kind_id=str(run.stage_kind_id),
        graph_node_id=state.activations[run.activation_id].graph_node_id,
        runner_binding_id=str(run.runner_binding_id),
        marker="INVALID_MARKER",
        adapter_provenance=None,
        observation_payload={},
        artifact_payload={},
    )
    completion = RunnerSessionCompletionRecord(
        completion_id="other-producer-completion",
        session_id=session.session_id,
        run_id=run.run_ref.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        terminal_state="completed",
        exit_kind="success",
        adapter_outcome_kind="success",
        adapter_error_kind=None,
        runner_result_evidence_digest=runtime.cas_store.put_bytes(
            runner_result_evidence_bytes(evidence)
        ),
        primary_cancellation_request_id=None,
        cleanup_disposition="complete",
        started_at=102,
        cancel_requested_at=None,
        completed_at=103,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="fixture",
        diagnostic_digest=runtime.cas_store.put_bytes(b"private diagnostic"),
        application_input_id="cli:run.session-completion:other-producer-completion",
    )
    command = RecordRunnerSessionCompletion(
        "other-producer-record",
        run_ref=run.run_ref,
        expected_state="starting",
        completion=completion,
    )
    decision = decide(
        state,
        command,
        transition_context(command="fixture", input_id_value=command.input_id),
    )
    assert decision.accepted
    state = apply(state, decision)
    observation = RunnerResultObserved(
        completion.application_input_id,
        run_id=run.run_ref.run_id,
        payload=evidence.payload(),
        observed_at=104,
    )
    refused = decide(
        state,
        observation,
        transition_context(command="fixture", input_id_value=observation.input_id),
    )
    assert not refused.accepted
    state = apply(state, refused)
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    before = inventory(tmp_path)
    code, output = invoke(runtime, "runs", "show", run.run_ref.run_id)
    assert code == 0, output
    projected = output["data"]["records"][0]["runner_session"]
    assert projected["completion_input_id"] == command.input_id
    assert projected["completion_input_status"] == "verified_payload_digest_and_trace"
    assert (
        projected["application_persisted"]
        and projected["application_status"] == "refused"
    )
    assert projected["application_input_id"] != projected["completion_input_id"]
    assert projected["diagnostic_status"] == "corrupt"
    assert projected["completion_diagnostic_digest"] == completion.diagnostic_digest
    code, doctor = invoke(runtime, "doctor")
    assert code == 0, doctor
    diagnostics = [
        row for row in doctor["data"]["records"] if row["kind"] == "diagnostic"
    ]
    assert any(
        row["code"] == "runner_session_application_refused" for row in diagnostics
    )
    assert any(
        row["code"] == "runner_session_completion_diagnostic"
        and row["status"] == "corrupt"
        for row in diagnostics
    )
    assert "private diagnostic" not in json.dumps(doctor)
    assert inventory(tmp_path) == before
    assert _completion_input_id(replace(state, traces=()), completion) is None
    receipt = state.receipts[command.input_id]
    duplicate_id = "ambiguous-producer"
    duplicate = replace(
        receipt, receipt_ref=replace(receipt.receipt_ref, input_id=duplicate_id)
    )
    trace = next(t for t in state.traces if t.input_id == command.input_id)
    ambiguous = replace(
        state,
        receipts={**state.receipts, duplicate_id: duplicate},
        traces=(
            *state.traces,
            replace(trace, input_id=duplicate_id, record_id="ambiguous-trace"),
        ),
    )
    assert _completion_input_id(ambiguous, completion) is None
    runtime.close()


def test_ordinary_eviction_ends_continuation_without_skipping_or_mutating(tmp_path):
    import hashlib

    runtime, run = runtime_with_run(tmp_path, session=True)
    events_for(runtime, run)
    code, first = invoke(
        runtime, "--page-size", "1", "runs", "follow", run.run_ref.run_id
    )
    assert code == 0 and first["data"]["has_more"]
    token = first["data"]["next_cursor"]
    store = RunnerSessionEventStore.open(
        runner_session_event_store_path(runtime.paths.db_path)
    )
    for n in range(256):
        store.append(
            session_id="other",
            run_id="other-run",
            dispatch_generation=1,
            kind="session_started",
            observed_at=n,
            bounded_payload={},
            redaction_policy_id="fixture",
            truncation_metadata={},
            replay_key="sha256:" + hashlib.sha256(str(n).encode()).hexdigest(),
        )
    store.close()
    before = inventory(tmp_path)
    for _ in range(2):
        code, output = invoke(
            runtime, "--cursor", token, "runs", "follow", run.run_ref.run_id
        )
        assert code == 0, output
        page = output["data"]
        assert page["events"] == [] and page["history_status"] == "history_gap"
        assert page["gap"] is not None and page["restart_guidance"]
        assert page["next_after_sequence"] == 1 and page["last_sequence"] == 3
        assert not page["has_more"] and page["next_cursor"] is None
    assert inventory(tmp_path) == before
    runtime.close()


def test_persisted_cancellation_attempt_result_and_grace_are_not_native_effect_proof(
    tmp_path,
):
    from millrace.adapters.cli.context import transition_context
    from millrace.adapters.cli.session_coordinator import (
        cooperative_cancel_grace_seconds,
        terminate_grace_seconds,
    )
    from millrace.contracts.transition import (
        AdvanceRunnerSession,
        RecordRunnerSessionCancellationAttempt,
        RequestRunnerSessionCancellation,
    )
    from millrace.kernel import apply, decide

    runtime, run = runtime_with_run(tmp_path, session=True)
    state = runtime.store.load_runtime_state(runtime.cas_store)
    session = state.runner_sessions[run.current_session_id]
    scope = dict(
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
    )
    request = RequestRunnerSessionCancellation(
        "cancel-input",
        **scope,
        expected_state="created",
        request_id="cancel",
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id="operator",
        requested_at=101,
        request_order=1,
        primary=True,
    )
    attempt = RecordRunnerSessionCancellationAttempt(
        "attempt-input",
        **scope,
        expected_state="cancellation_requested",
        attempt_id="cooperative-attempt",
        request_id="cancel",
        sequence=1,
        operation="cooperative_cancel",
        result="timed_out",
        started_at=102,
        completed_at=103,
        bounded_diagnostic_digest=runtime.cas_store.put_bytes(b"private cancel detail"),
    )
    terminating = AdvanceRunnerSession(
        "terminating-input",
        **scope,
        expected_state="cancellation_requested",
        next_state="terminating",
        occurred_at=104,
    )
    terminated = RecordRunnerSessionCancellationAttempt(
        "terminate-input",
        **scope,
        expected_state="terminating",
        attempt_id="terminate-attempt",
        request_id="cancel",
        sequence=2,
        operation="terminate",
        result="succeeded",
        started_at=105,
        completed_at=106,
        bounded_diagnostic_digest=runtime.cas_store.put_bytes(
            b"private terminate detail"
        ),
    )
    for command in (request, attempt, terminating, terminated):
        decision = decide(
            state,
            command,
            transition_context(command="fixture", input_id_value=command.input_id),
        )
        assert decision.accepted, decision.refusal
        state = apply(state, decision)
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    before = inventory(tmp_path)
    code, output = invoke(runtime, "runs", "show", run.run_ref.run_id)
    assert code == 0, output
    row = output["data"]["records"][0]["runner_session"]
    assert row["primary_cancellation_request_id"] == "cancel"
    assert (
        row["cancellation_phase"] == row["cancellation_last_operation"] == "terminate"
    )
    assert row["cancellation_last_result"] == "succeeded"
    assert row["cooperative_cancel_grace_seconds"] == cooperative_cancel_grace_seconds
    assert row["terminate_grace_seconds"] == terminate_grace_seconds
    assert row["effect_certainty"]["availability"] == "unavailable"
    assert "private" not in json.dumps(output)
    assert inventory(tmp_path) == before
    runtime.close()
