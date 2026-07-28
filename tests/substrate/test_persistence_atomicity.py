from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts.state import RuntimeState, TraceRecord
from millrace.substrate._sqlite_load import load_runtime_state_rows
from millrace.substrate._sqlite_write import persist_runtime_state_rows
from millrace.substrate.cas import (
    ContentAddressedByteStore,
    storage_digest_for_bytes,
)
from millrace.substrate.codecs import dumps_cas_object, encode_payload
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import (
    fake_runner_completion_input_id,
    materialize_fake_runner_session_cas,
)
from substrate._runtime_store_support import (
    initialize_runtime_store,
    load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
    taskmaster_runtime_state,
    worker_runtime_state,
)
from support import kernel_ping as kernel_ping_support

_RUNTIME_TABLES = (
    "admitted_plan_pins",
    "default_plan",
    "input_receipts",
    "work_items",
    "activations",
    "runs",
    "runner_observations",
    "artifacts",
    "activation_routes",
    "closed_work_items",
    "pause_state",
    "quarantine_records",
    "lineage_quarantines",
    "recovery_attempts",
    "operator_interventions",
    "operator_waits",
    "cooldown_waits",
    "counters",
    "transitions",
    "governance_events",
    "traces",
    "refusals",
)


def test_sqlite_commit_failure_after_cas_prewrites_leaves_no_referenced_state(
    tmp_path: Path,
) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    state = taskmaster_runtime_state()
    initialize_runtime_store(db_path)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            persist_runtime_state_rows(
                connection,
                state,
                ContentAddressedByteStore(cas_root),
                _before_sqlite_commit=_raise_injected_commit_failure,
            )

    assert _stored_cas_files(cas_root) != ()
    assert _runtime_row_counts(db_path) == dict.fromkeys(_RUNTIME_TABLES, 0)
    assert load_runtime_state(db_path, cas_root) == RuntimeState()


def test_failed_persist_does_not_advance_loaded_runtime_state(
    tmp_path: Path,
) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    initial_state = taskmaster_runtime_state()
    advanced_state = worker_runtime_state()
    assert advanced_state != initial_state
    persist_runtime_state(db_path, cas_root, initial_state)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            cas_store = ContentAddressedByteStore(cas_root)
            persist_runtime_state_rows(
                connection,
                materialize_fake_runner_session_cas(
                    state=advanced_state,
                    cas_store=cas_store,
                ),
                cas_store,
                _before_sqlite_commit=_raise_injected_commit_failure,
            )

    loaded = load_runtime_state(db_path, cas_root)
    assert loaded == initial_state
    assert loaded != advanced_state


def test_stale_runtime_state_cannot_rewind_durable_history(tmp_path: Path) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    stale_state = taskmaster_runtime_state()
    advanced_state = worker_runtime_state()
    persist_runtime_state(db_path, cas_root, advanced_state)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(StorageIntegrityError, match="stale runtime state"):
            persist_runtime_state_rows(
                connection,
                stale_state,
                ContentAddressedByteStore(cas_root),
            )

    assert load_runtime_state(db_path, cas_root) == advanced_state


def test_divergent_runtime_state_cannot_replace_durable_history(
    tmp_path: Path,
) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    durable_state = taskmaster_runtime_state()
    first_transition = durable_state.transitions[0]
    divergent_transition = replace(
        first_transition,
        record_id=f"{first_transition.record_id}:diverged",
    )
    divergent_state = replace(
        durable_state,
        transitions=(divergent_transition, *durable_state.transitions[1:]),
    )
    persist_runtime_state(db_path, cas_root, durable_state)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(StorageIntegrityError, match="stale runtime state"):
            persist_runtime_state_rows(
                connection,
                divergent_state,
                ContentAddressedByteStore(cas_root),
            )

    assert load_runtime_state(db_path, cas_root) == durable_state


def test_same_history_stale_runtime_state_cannot_erase_durable_rows(
    tmp_path: Path,
) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    durable_state = worker_runtime_state()
    stale_state = replace(durable_state, artifacts={})
    assert durable_state.transitions == stale_state.transitions
    assert durable_state.artifacts != stale_state.artifacts
    persist_runtime_state(db_path, cas_root, durable_state)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(
            StorageIntegrityError,
            match=(
                "stale runtime state would rewrite durable state without new "
                "transition"
            ),
        ):
            persist_runtime_state_rows(
                connection,
                stale_state,
                ContentAddressedByteStore(cas_root),
            )

    assert load_runtime_state(db_path, cas_root) == durable_state


@pytest.mark.parametrize("table_name", ("governance_events", "traces"))
def test_persist_refuses_duplicate_candidate_audit_rows_before_rewrite(
    tmp_path: Path,
    table_name: str,
) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    durable_state = worker_runtime_state()
    if table_name == "governance_events":
        base_governance = durable_state.governance_events[0]
        duplicate_record = replace(
            base_governance,
            record_id="duplicate-governance-row",
        )
        corrupt_state = replace(
            durable_state,
            governance_events=(duplicate_record, *durable_state.governance_events),
        )
    else:
        base_trace = durable_state.traces[0]
        duplicate_trace = replace(base_trace, record_id="duplicate-trace-row")
        corrupt_state = replace(
            durable_state,
            traces=(duplicate_trace, *durable_state.traces),
        )
    persist_runtime_state(db_path, cas_root, durable_state)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(
            StorageIntegrityError,
            match=f"{table_name}.transition_order must be unique",
        ):
            persist_runtime_state_rows(
                connection,
                corrupt_state,
                ContentAddressedByteStore(cas_root),
            )

    assert load_runtime_state(db_path, cas_root) == durable_state


def test_persist_refuses_candidate_trace_governance_drift_before_rewrite(
    tmp_path: Path,
) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    durable_state = worker_runtime_state()
    first_trace = durable_state.traces[0]
    corrupt_trace = TraceRecord(
        record_id=first_trace.record_id,
        input_id=first_trace.input_id,
        input_kind=first_trace.input_kind,
        input_family=first_trace.input_family,
        disposition=first_trace.disposition,
        plan_fingerprint=first_trace.plan_fingerprint,
        work_item_id=first_trace.work_item_id,
        run_id=first_trace.run_id,
        action_id=first_trace.action_id,
        authority_source="trace-drift",
        refusal_reason=first_trace.refusal_reason,
    )
    corrupt_state = replace(
        durable_state,
        traces=(corrupt_trace, *durable_state.traces[1:]),
    )
    persist_runtime_state(db_path, cas_root, durable_state)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(
            StorageIntegrityError,
            match="traces must match governance_events for transition_order",
        ):
            persist_runtime_state_rows(
                connection,
                corrupt_state,
                ContentAddressedByteStore(cas_root),
            )

    assert load_runtime_state(db_path, cas_root) == durable_state


def test_corrupt_runner_observation_persist_preserves_prior_sqlite_authority(
    tmp_path: Path,
) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    durable_state = worker_runtime_state()
    run = durable_state.runs["run-worker"]
    admitted = durable_state.admitted_plans[
        run.run_ref.plan_ref.authority_fingerprint
    ]
    legal_advanced = kernel_ping_support.apply_accepted_input(
        durable_state,
        kernel_ping_support.runner_observation(
            state=durable_state,
            plan=admitted.selected_plan,
            fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.close_worker_success",
            input_id="observe-worker",
            artifact_payload={},
        ),
        kernel_ping_support.kernel_ping_context("observe-worker"),
    )
    observation = next(
        candidate
        for candidate in legal_advanced.runner_observations.values()
        if candidate.created_by_input_id
        == fake_runner_completion_input_id("observe-worker")
    )
    corrupt_payload = {**observation.payload, "marker": "CORRUPT_MARKER"}
    corrupt_state = replace(
        legal_advanced,
        runner_observations={
            **legal_advanced.runner_observations,
            observation.observation_id: replace(
                observation,
                payload=corrupt_payload,
            ),
        },
    )
    corrupt_object = dumps_cas_object(encode_payload(corrupt_payload))
    corrupt_digest = storage_digest_for_bytes(corrupt_object)
    persist_runtime_state(db_path, cas_root, durable_state)
    cas_store = ContentAddressedByteStore(cas_root)
    materialize_fake_runner_session_cas(
        state=legal_advanced,
        cas_store=cas_store,
    )
    references_before = _authoritative_cas_references(db_path)

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(
            StorageIntegrityError,
            match=(
                "runner_observations accepted-input authority invalid: "
                "receipt_authority"
            ),
        ):
            persist_runtime_state_rows(
                connection,
                corrupt_state,
                cas_store,
            )

    assert load_runtime_state(db_path, cas_root) == durable_state
    assert _authoritative_cas_references(db_path) == references_before
    assert corrupt_digest not in references_before
    assert ContentAddressedByteStore(cas_root).get_bytes(corrupt_digest) == (
        corrupt_object
    )


def test_load_uses_one_sqlite_snapshot_when_store_advances_mid_load(
    tmp_path: Path,
) -> None:
    db_path, cas_root = runtime_store_paths(tmp_path)
    initial_state = taskmaster_runtime_state()
    advanced_state = worker_runtime_state()
    persist_runtime_state(db_path, cas_root, initial_state)

    with sqlite3.connect(db_path) as setup_connection:
        setup_connection.execute("PRAGMA journal_mode = WAL")

    def advance_store() -> None:
        with sqlite3.connect(db_path) as writer_connection:
            writer_connection.execute("PRAGMA journal_mode = WAL")
            cas_store = ContentAddressedByteStore(cas_root)
            persist_runtime_state_rows(
                writer_connection,
                materialize_fake_runner_session_cas(
                    state=advanced_state,
                    cas_store=cas_store,
                ),
                cas_store,
            )

    with sqlite3.connect(db_path) as reader_connection:
        reader_connection.execute("PRAGMA journal_mode = WAL")
        loaded = load_runtime_state_rows(
            reader_connection,
            ContentAddressedByteStore(cas_root),
            _after_admitted_plans=advance_store,
        )

    assert loaded == initial_state
    assert load_runtime_state(db_path, cas_root) == advanced_state


def _raise_injected_commit_failure() -> None:
    raise sqlite3.OperationalError("injected commit failure after CAS prewrites")


def _stored_cas_files(cas_root: Path) -> tuple[Path, ...]:
    return tuple(path for path in cas_root.rglob("*") if path.is_file())


def _runtime_row_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as connection:
        return {
            table_name: int(
                connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            )
            for table_name in _RUNTIME_TABLES
        }


def _authoritative_cas_references(db_path: Path) -> frozenset[str]:
    queries = (
        "SELECT selected_plan_digest FROM admitted_plan_pins",
        "SELECT payload_digest FROM work_items",
        "SELECT payload_digest FROM runner_observations",
        "SELECT payload_digest FROM artifacts",
        "SELECT evidence_window_digest FROM closure_targets",
    )
    with sqlite3.connect(db_path) as connection:
        return frozenset(
            str(row[0])
            for query in queries
            for row in connection.execute(query).fetchall()
        )
