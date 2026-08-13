from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import (
    StorageIntegrityError,
    StoreSchemaUpgradeRequired,
)
from millrace.substrate.sqlite import SQLiteRuntimeStore
from substrate.test_runner_session_context_persistence import (
    _attach_state,
    _context_manifest,
    _persist_initial_state,
)
from substrate.test_runner_session_persistence import (
    _cas_backed_session_state,
    _session_state,
)


def _persist_candidate(tmp_path, state, cas_store):
    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
    finally:
        store.close()
    return db_path


def _persisted_runtime(tmp_path, *, completed: bool = False):
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(
        _session_state(), cas_store, completed=completed
    )
    db_path = _persist_candidate(tmp_path, state, cas_store)
    return db_path, cas_store, state


def _assert_load_refused(db_path, cas_store, message: str) -> None:
    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match=message):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("sql", "message"),
    (
        (
            """
            UPDATE runs
            SET current_session_id = 'missing-session'
            WHERE run_id = 'run-taskmaster'
            """,
            "current_session_id",
        ),
        (
            """
            UPDATE runner_sessions
            SET run_id = 'missing-run'
            WHERE session_id = 'session-1'
            """,
            "reference runs",
        ),
    ),
)
def test_runner_session_run_link_raw_corruption_is_read_only(
    tmp_path,
    sql: str,
    message: str,
) -> None:
    cas_root = tmp_path / "cas"
    db_path, cas_store, _state = _persisted_runtime(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql)
    database_before = db_path.read_bytes()
    cas_before = {
        path.relative_to(cas_root): path.read_bytes()
        for path in cas_root.rglob("*")
        if path.is_file()
    }

    _assert_load_refused(db_path, cas_store, message)

    assert db_path.read_bytes() == database_before
    assert {
        path.relative_to(cas_root): path.read_bytes()
        for path in cas_root.rglob("*")
        if path.is_file()
    } == cas_before


def test_runner_session_corrupt_cancellation_attempt_link_is_refused(
    tmp_path,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_session_cancellation_attempts
            SET request_id = 'missing-request'
            WHERE attempt_id = 'attempt-1'
            """
        )

    _assert_load_refused(db_path, cas_store, "cancellation attempt.*request")


@pytest.mark.parametrize(
    ("sql", "message"),
    (
        (
            """
            UPDATE runner_session_cancellation_requests
            SET request_order = 2
            WHERE request_id = 'cancel-1'
            """,
            "order",
        ),
        (
            """
            UPDATE runner_session_cancellation_requests
            SET primary_request = 0
            WHERE request_id = 'cancel-1'
            """,
            "primary",
        ),
        (
            """
            UPDATE runner_session_cancellation_attempts
            SET sequence = 2
            WHERE attempt_id = 'attempt-1'
            """,
            "order",
        ),
        (
            """
            UPDATE runner_session_cancellation_attempts
            SET session_id = 'missing-session'
            WHERE attempt_id = 'attempt-1'
            """,
            "session",
        ),
    ),
)
def test_runner_session_cancellation_link_raw_corruption_is_refused(
    tmp_path,
    sql: str,
    message: str,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql)
    database_before = db_path.read_bytes()
    cas_before = {
        path.relative_to(tmp_path / "cas"): path.read_bytes()
        for path in (tmp_path / "cas").rglob("*")
        if path.is_file()
    }

    _assert_load_refused(db_path, cas_store, message)
    assert db_path.read_bytes() == database_before
    assert {
        path.relative_to(tmp_path / "cas"): path.read_bytes()
        for path in (tmp_path / "cas").rglob("*")
        if path.is_file()
    } == cas_before


@pytest.mark.parametrize(
    ("column", "value", "message"),
    (
        ("run_id", "missing-run", "contradict session"),
        ("dispatch_generation", 2, "contradict session"),
        ("session_fencing_token", "changed-fence", "contradict session"),
        ("terminal_state", "failed", "contradict session"),
        ("cleanup_disposition", "not_required", "contradict session"),
        ("application_input_id", "wrong-input-id", "application_input_id"),
    ),
)
def test_runner_session_completion_link_raw_corruption_is_refused(
    tmp_path,
    column: str,
    value: str | int,
    message: str,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"""
            UPDATE runner_session_completions
            SET {column} = ?
            WHERE session_id = 'session-1'
            """,
            (value,),
        )
    database_before = db_path.read_bytes()
    cas_before = {
        path.relative_to(tmp_path / "cas"): path.read_bytes()
        for path in (tmp_path / "cas").rglob("*")
        if path.is_file()
    }

    _assert_load_refused(db_path, cas_store, message)
    assert db_path.read_bytes() == database_before
    assert {
        path.relative_to(tmp_path / "cas"): path.read_bytes()
        for path in (tmp_path / "cas").rglob("*")
        if path.is_file()
    } == cas_before


def test_runner_session_attempt_linked_to_secondary_request_is_refused(
    tmp_path,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO runner_session_cancellation_requests (
                schema_version,
                request_id,
                session_id,
                dispatch_generation,
                reason,
                source_kind,
                actor_id,
                requested_at,
                request_order,
                primary_request
            ) VALUES (1, 'cancel-2', 'session-1', 1, 'daemon_shutdown',
                      'daemon', 'daemon-1', 131, 2, 0)
            """
        )
        connection.execute(
            """
            UPDATE runner_session_cancellation_attempts
            SET request_id = 'cancel-2'
            WHERE attempt_id = 'attempt-1'
            """
        )

    _assert_load_refused(db_path, cas_store, "primary")


def test_runner_session_history_on_running_raw_corruption_is_refused(
    tmp_path,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    session = replace(
        state.runner_sessions["session-1"],
        state="cancellation_requested",
        ended_at=None,
        cleanup_disposition="pending",
    )
    state = replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_completions={},
    )
    db_path = _persist_candidate(tmp_path, state, cas_store)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_sessions
            SET state = 'running'
            WHERE session_id = 'session-1'
            """
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="cancellation history"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize(
    "active_state",
    ("cancellation_requested", "terminating"),
)
def test_active_cancellation_state_missing_primary_raw_corruption_is_refused(
    tmp_path,
    active_state: str,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(_session_state(), cas_store)
    session = replace(
        state.runner_sessions["session-1"],
        state=active_state,
        ended_at=None,
        cleanup_disposition="pending",
    )
    state = replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_completions={},
    )
    db_path = _persist_candidate(tmp_path, state, cas_store)

    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM runner_session_cancellation_attempts")
        connection.execute("DELETE FROM runner_session_cancellation_requests")

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="requires primary"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_interrupted_completion_missing_primary_link_raw_corruption_is_refused(
    tmp_path,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_session_completions
            SET primary_cancellation_request_id = NULL,
                cancel_requested_at = NULL
            WHERE session_id = 'session-1'
            """
        )

    _assert_load_refused(db_path, cas_store, "interrupted")


@pytest.mark.parametrize("terminal_state", ("completed", "failed", "lost"))
def test_terminal_completion_race_may_retain_unlinked_cancellation_history(
    tmp_path,
    terminal_state: str,
) -> None:
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, digests = _cas_backed_session_state(_session_state(), cas_store)
    cleanup = "orphan_risk" if terminal_state == "lost" else "complete"
    session = replace(
        state.runner_sessions["session-1"],
        state=terminal_state,
        cleanup_disposition=cleanup,
    )
    completion = replace(
        state.runner_session_completions["session-1"],
        completion_id=f"completion-{terminal_state}",
        terminal_state=terminal_state,
        exit_kind="success" if terminal_state == "completed" else "error",
        adapter_outcome_kind=("success" if terminal_state == "completed" else None),
        adapter_error_kind=(
            None if terminal_state == "completed" else "invocation_failed"
        ),
        runner_result_evidence_digest=(
            digests["completed_evidence"] if terminal_state == "completed" else None
        ),
        primary_cancellation_request_id=None,
        cleanup_disposition=cleanup,
        cancel_requested_at=None,
        application_input_id=(
            f"cli:run.session-completion:completion-{terminal_state}"
        ),
    )
    state = replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_completions={session.session_id: completion},
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        store.persist_runtime_state(state, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert loaded.runner_sessions["session-1"].state == terminal_state
    assert "cancel-1" in loaded.runner_session_cancellation_requests


def test_runner_session_reason_source_mismatch_raw_row_is_refused(
    tmp_path,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE runner_session_cancellation_requests
            SET source_kind = 'daemon'
            WHERE request_id = 'cancel-1'
            """
        )

    _assert_load_refused(db_path, cas_store, "reason and source")


@pytest.mark.parametrize(
    ("sql", "message"),
    (
        (
            """
            UPDATE runner_session_completions
            SET started_at = 121
            WHERE session_id = 'session-1'
            """,
            "started",
        ),
        (
            """
            UPDATE runner_session_completions
            SET primary_cancellation_request_id = NULL
            WHERE session_id = 'session-1'
            """,
            "cancellation",
        ),
    ),
)
def test_runner_session_completion_phase_raw_row_is_refused(
    tmp_path,
    sql: str,
    message: str,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql)

    _assert_load_refused(db_path, cas_store, message)


@pytest.mark.parametrize("started_at", (None, 105))
def test_starting_completed_start_evidence_raw_row_is_refused(
    tmp_path,
    started_at: int | None,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path, completed=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE runner_sessions
            SET started_at = ?
            WHERE session_id = 'session-1'
            """,
            (started_at,),
        )
        connection.execute(
            """
            UPDATE runner_session_completions
            SET started_at = ?
            WHERE session_id = 'session-1'
            """,
            (started_at,),
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="start|timestamp"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


def test_cancellation_request_before_session_phase_raw_row_is_refused(
    tmp_path,
) -> None:
    state = _session_state()
    session = replace(
        state.runner_sessions["session-1"],
        state="cancellation_requested",
        ended_at=None,
        durable_locator_digest=None,
        cleanup_disposition="pending",
    )
    state = replace(
        state,
        runner_sessions={session.session_id: session},
        runner_session_cancellation_attempts={},
        runner_session_completions={},
    )
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    db_path = _persist_candidate(tmp_path, state, cas_store)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE runner_session_cancellation_requests
            SET requested_at = 119
            WHERE request_id = 'cancel-1'
            """
        )

    store = SQLiteRuntimeStore.open(db_path)
    try:
        with pytest.raises(StorageIntegrityError, match="predates session phase"):
            store.load_runtime_state(cas_store)
    finally:
        store.close()


@pytest.mark.parametrize("record_kind", ("request", "attempt"))
def test_post_terminal_cancellation_history_raw_row_is_refused(
    tmp_path,
    record_kind: str,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path)
    with sqlite3.connect(db_path) as connection:
        if record_kind == "request":
            connection.execute("DELETE FROM runner_session_cancellation_attempts")
            connection.execute(
                """
                UPDATE runner_session_completions
                SET primary_cancellation_request_id = NULL,
                    cancel_requested_at = NULL
                WHERE session_id = 'session-1'
                """
            )
            connection.execute(
                """
                UPDATE runner_session_cancellation_requests
                SET requested_at = 151
                WHERE request_id = 'cancel-1'
                """
            )
        else:
            connection.execute(
                """
                UPDATE runner_session_cancellation_attempts
                SET completed_at = 151
                WHERE attempt_id = 'attempt-1'
                """
            )

    _assert_load_refused(db_path, cas_store, "ended_at")


def test_terminal_cancellation_history_allows_ended_at_equality(tmp_path) -> None:
    state = _session_state()
    request = replace(
        state.runner_session_cancellation_requests["cancel-1"],
        requested_at=150,
    )
    attempt = replace(
        state.runner_session_cancellation_attempts["attempt-1"],
        started_at=150,
        completed_at=150,
    )
    completion = replace(
        state.runner_session_completions["session-1"],
        cancel_requested_at=150,
    )
    state = replace(
        state,
        runner_session_cancellation_requests={request.request_id: request},
        runner_session_cancellation_attempts={attempt.attempt_id: attempt},
        runner_session_completions={completion.session_id: completion},
    )
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    state, _digests = _cas_backed_session_state(state, cas_store)
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    try:
        store.persist_runtime_state(state, cas_store)
        loaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert loaded == state


def test_runner_session_schema_rejects_oversized_text(tmp_path) -> None:
    db_path, _cas_store, _state = _persisted_runtime(tmp_path)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE runner_session_completions
                SET bounds_summary = ?
                WHERE session_id = 'session-1'
                """,
                ("x" * 4097,),
            )


def test_attached_context_missing_manifest_on_reload_is_refused_read_only(
    tmp_path,
) -> None:
    state, plan, plan_fingerprint, cas_store, db_path = _persist_initial_state(
        tmp_path
    )
    _manifest, digest = _context_manifest(
        state=state,
        plan=plan,
        plan_fingerprint=plan_fingerprint,
        cas_store=cas_store,
    )
    attached = _attach_state(state, digest=digest)
    store = SQLiteRuntimeStore.open(db_path)
    try:
        store.persist_runtime_state(attached, cas_store)
    finally:
        store.close()

    missing_digest = "sha256:" + "f" * 64
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runner_sessions SET context_manifest_digest = ? "
            "WHERE session_id = 'session-1'",
            (missing_digest,),
        )
    database_before = db_path.read_bytes()
    _assert_load_refused(db_path, cas_store, "context")
    assert db_path.read_bytes() == database_before


@pytest.mark.parametrize(
    ("sql", "message"),
    (
        (
            """
            UPDATE runner_session_completions
            SET truncation_metadata = ?
            WHERE session_id = 'session-1'
            """,
            "4096",
        ),
        (
            """
            UPDATE runs
            SET current_session_id = ?
            WHERE run_id = 'run-taskmaster'
            """,
            "4096",
        ),
    ),
)
def test_runner_session_oversized_raw_text_is_refused_on_reload(
    tmp_path,
    sql: str,
    message: str,
) -> None:
    db_path, cas_store, _state = _persisted_runtime(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql, ("x" * 4097,))

    _assert_load_refused(db_path, cas_store, message)


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
