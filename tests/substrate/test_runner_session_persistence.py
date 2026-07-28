from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.state import (
    RunnerSessionCancellationAttemptRecord,
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
    RuntimeState,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import (
    StorageIntegrityError,
    StoreSchemaUpgradeRequired,
)
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.workflows import kernel_ping


def _session_state() -> RuntimeState:
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    state = bootstrap_to_taskmaster_claim(
        result.plan,
        authority_fingerprint(result.plan),
    )
    run = state.runs["run-taskmaster"]
    session = RunnerSessionRecord(
        session_id="session-1",
        run_id="run-taskmaster",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        state="interrupted",
        created_at=100,
        start_intent_at=110,
        started_at=120,
        ended_at=150,
        durable_locator_digest="sha256:" + "a" * 64,
        cleanup_disposition="complete",
    )
    request = RunnerSessionCancellationRecord(
        request_id="cancel-1",
        session_id="session-1",
        dispatch_generation=1,
        reason="operator_cancel_work",
        source_kind="operator",
        actor_id="operator-1",
        requested_at=130,
        request_order=1,
        primary=True,
    )
    attempt = RunnerSessionCancellationAttemptRecord(
        attempt_id="attempt-1",
        session_id="session-1",
        request_id="cancel-1",
        sequence=1,
        operation="cooperative_cancel",
        result="succeeded",
        started_at=135,
        completed_at=140,
        bounded_diagnostic_digest="sha256:" + "b" * 64,
    )
    completion = RunnerSessionCompletionRecord(
        completion_id="completion-1",
        session_id="session-1",
        run_id="run-taskmaster",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        terminal_state="interrupted",
        exit_kind="cancelled",
        adapter_outcome_kind=None,
        adapter_error_kind=None,
        runner_result_evidence_digest=None,
        primary_cancellation_request_id="cancel-1",
        cleanup_disposition="complete",
        started_at=120,
        cancel_requested_at=130,
        completed_at=150,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest="sha256:" + "c" * 64,
        application_input_id="cli:run.session-completion:completion-1",
    )
    return replace(
        state,
        runs={
            **state.runs,
            run.run_ref.run_id: replace(
                run,
                current_session_id=session.session_id,
                last_dispatch_generation=1,
            ),
        },
        runner_sessions={session.session_id: session},
        runner_session_cancellation_requests={request.request_id: request},
        runner_session_cancellation_attempts={attempt.attempt_id: attempt},
        runner_session_completions={session.session_id: completion},
    )


def test_store_schema_seven_owns_runner_session_tables(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT store_schema_version FROM store_metadata WHERE id = 1"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runs)")
        }

    assert version == (7,)
    assert {
        "runner_sessions",
        "runner_session_cancellation_requests",
        "runner_session_cancellation_attempts",
        "runner_session_completions",
    }.issubset(tables)
    assert {"current_session_id", "last_dispatch_generation"}.issubset(run_columns)


def test_runner_session_records_round_trip(tmp_path) -> None:
    state = _session_state()
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, ContentAddressedByteStore(cas_root))
        loaded = store.load_runtime_state(ContentAddressedByteStore(cas_root))
    finally:
        store.close()

    assert loaded == state


def test_runner_session_generation_gap_is_refused(tmp_path) -> None:
    state = _session_state()
    session = RunnerSessionRecord(
        session_id="session-2",
        run_id="run-taskmaster",
        dispatch_generation=2,
        session_fencing_token="session-fence-2",
        state="created",
        created_at=200,
        start_intent_at=None,
        started_at=None,
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    run = replace(
        state.runs["run-taskmaster"],
        current_session_id=session.session_id,
        last_dispatch_generation=2,
    )
    corrupt = replace(
        state,
        runs={**state.runs, run.run_ref.run_id: run},
        runner_sessions={session.session_id: session},
        runner_session_cancellation_requests={},
        runner_session_cancellation_attempts={},
        runner_session_completions={},
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        with pytest.raises(StorageIntegrityError, match="generation"):
            store.persist_runtime_state(
                corrupt,
                ContentAddressedByteStore(tmp_path / "cas"),
            )
    finally:
        store.close()


def test_runner_session_corrupt_cancellation_attempt_link_is_refused(
    tmp_path,
) -> None:
    state = _session_state()
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, ContentAddressedByteStore(cas_root))
    finally:
        store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_session_cancellation_attempts
            SET request_id = 'missing-request'
            WHERE attempt_id = 'attempt-1'
            """
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(
            StorageIntegrityError,
            match="cancellation attempt request",
        ):
            store.load_runtime_state(ContentAddressedByteStore(cas_root))
    finally:
        store.close()


def test_v6_store_is_refused_unchanged_by_session_runtime(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()
    cas_root.mkdir()
    marker = cas_root / "marker"
    marker.write_bytes(b"unchanged-cas")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE store_metadata SET store_schema_version = 6 WHERE id = 1"
        )
    before_db = db_path.read_bytes()
    before_cas = marker.read_bytes()

    with pytest.raises(StoreSchemaUpgradeRequired):
        SQLiteRuntimeStore.open(db_path)
    with pytest.raises(StoreSchemaUpgradeRequired):
        SQLiteRuntimeStore.initialize(db_path)

    assert db_path.read_bytes() == before_db
    assert marker.read_bytes() == before_cas
