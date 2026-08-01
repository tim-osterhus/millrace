from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace.contracts import QueueFamilyId
from millrace.contracts.state import (
    DURABLE_INT64_MAX,
    DaemonBudgetEpochRecord,
    RunnerSessionRecord,
    RunnerSessionUsageRecord,
    RuntimeState,
    TraceRecord,
)
from millrace.contracts.transition import (
    AdmitPlan,
    CancelQueuedLineage,
    CancelQueuedWork,
    EnqueueWork,
    InitializeWorkspace,
    SelectDefaultPlan,
)
from millrace.kernel import StateConcurrencyError, apply, empty_runtime_state
from millrace.substrate._sqlite_load import load_runtime_state_rows
from millrace.substrate._sqlite_write import persist_runtime_state_rows
from millrace.substrate.cas import (
    ContentAddressedByteStore,
    storage_digest_for_bytes,
)
from millrace.substrate.codecs import dumps_cas_object, encode_payload
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    deterministic_context,
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
    "dispatch_suspension",
    "queue_closures",
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


def test_queue_closure_commit_failure_rolls_back_audit_and_all_closes(
    tmp_path: Path,
) -> None:
    plan, fingerprint = kernel_ping_support.compile_kernel_ping()
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("queue-atomic-init"),
        AdmitPlan(
            "queue-atomic-admit",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(
            "queue-atomic-select",
            authority_fingerprint=fingerprint,
        ),
        EnqueueWork(
            "queue-atomic-enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "atomic", "body": "remain all or nothing"},
        ),
    ):
        state = apply(
            state,
            decide(
                state,
                transition_input,
                deterministic_context(
                    transition_id=f"transition-{transition_input.input_id}",
                    work_item_id="queue-atomic-work",
                    activation_id="queue-atomic-activation",
                ),
            ),
        )
    closure = apply(
        state,
        decide(
            state,
            CancelQueuedWork(
                "queue-atomic-close",
                work_item_id="queue-atomic-work",
                plan_fingerprint=fingerprint,
                actor_id="queue-operator",
                reason="atomic persistence proof",
            ),
            deterministic_context(
                transition_id="transition-queue-atomic-close"
            ),
        ),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    cas_store = ContentAddressedByteStore(cas_root)
    durable_before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    }

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            persist_runtime_state_rows(
                connection,
                closure,
                cas_store,
                _before_sqlite_commit=lambda: (_ for _ in ()).throw(
                    sqlite3.OperationalError("injected commit failure")
                ),
            )

    reloaded = load_runtime_state(db_path, cas_root)
    assert reloaded == state
    assert reloaded.queue_closures == {}
    assert "queue-atomic-work" not in reloaded.closed_work_items
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    } == durable_before


def test_queue_lineage_apply_refusal_leaves_durable_bytes_unchanged(
    tmp_path: Path,
) -> None:
    plan, fingerprint = kernel_ping_support.compile_kernel_ping()
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("queue-lineage-atomic-init"),
        AdmitPlan(
            "queue-lineage-atomic-admit",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(
            "queue-lineage-atomic-select",
            authority_fingerprint=fingerprint,
        ),
        EnqueueWork(
            "queue-lineage-atomic-enqueue",
            queue_family_id=QueueFamilyId("prompt"),
            payload={"prompt_id": "lineage-atomic", "body": "stay whole"},
        ),
    ):
        state = apply(
            state,
            decide(
                state,
                transition_input,
                deterministic_context(
                    transition_id=f"transition-{transition_input.input_id}",
                    work_item_id="queue-lineage-atomic-work",
                    activation_id="queue-lineage-atomic-activation",
                ),
            ),
        )
    root = state.work_items["queue-lineage-atomic-work"]
    root_activation = state.activations["queue-lineage-atomic-activation"]
    child = replace(
        root,
        ref=replace(root.ref, work_item_id="queue-lineage-atomic-child"),
        lineage_id="queue-lineage-atomic",
        created_by_input_id="queue-lineage-atomic-child",
    )
    child_activation = replace(
        root_activation,
        activation_id="queue-lineage-atomic-child-activation",
        work_item_id=child.ref.work_item_id,
        lineage_id="queue-lineage-atomic",
        created_by_input_id="queue-lineage-atomic-child",
    )
    state = replace(
        state,
        work_items={
            root.ref.work_item_id: replace(root, lineage_id="queue-lineage-atomic"),
            child.ref.work_item_id: child,
        },
        activations={
            root_activation.activation_id: replace(
                root_activation,
                lineage_id="queue-lineage-atomic",
            ),
            child_activation.activation_id: child_activation,
        },
    )
    decision = decide(
        state,
        CancelQueuedLineage(
            "queue-lineage-atomic-close",
            lineage_id="queue-lineage-atomic",
            plan_fingerprint=fingerprint,
            actor_id="queue-operator",
            reason="preflight every lineage member",
        ),
        deterministic_context(transition_id="transition-queue-lineage-atomic-close"),
    )
    assert decision.accepted is True

    late_work = replace(
        child,
        ref=replace(child.ref, work_item_id="queue-lineage-atomic-late"),
        created_by_input_id="queue-lineage-atomic-late",
    )
    late_activation = replace(
        child_activation,
        activation_id="queue-lineage-atomic-late-activation",
        work_item_id=late_work.ref.work_item_id,
        created_by_input_id="queue-lineage-atomic-late",
    )
    changed_membership = replace(
        state,
        work_items={**state.work_items, late_work.ref.work_item_id: late_work},
        activations={
            **state.activations,
            late_activation.activation_id: late_activation,
        },
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    durable_before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    }

    with pytest.raises(StateConcurrencyError, match="lineage membership changed"):
        apply(changed_membership, decision)

    assert load_runtime_state(db_path, cas_root) == state
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    } == durable_before


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


def test_deferred_budget_reference_commit_failure_preserves_parent_state(
    tmp_path: Path,
) -> None:
    _db_path, cas_root, _state, store, epoch, session = (
        _persist_budget_atomicity_fixture(tmp_path)
    )
    before = _budget_atomicity_snapshot(store, cas_root, epoch.budget_id)
    connection = store._connection
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA defer_foreign_keys = ON")
        connection.execute(
            "DELETE FROM runner_sessions WHERE session_id = ?",
            (session.session_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint"):
            connection.execute("COMMIT")

        assert connection.in_transaction is True
        connection.execute("ROLLBACK")
        assert _budget_atomicity_snapshot(store, cas_root, epoch.budget_id) == before
    finally:
        store.close()


def test_budget_terminal_write_failure_rolls_back_parent_runtime_state(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context import (
        terminalize_daemon_budget_with_suspension,
    )

    _db_path, cas_root, _state, store, epoch, _session = (
        _persist_budget_atomicity_fixture(tmp_path)
    )
    connection = store._connection
    connection.execute(
        """
        CREATE TRIGGER fail_budget_terminal_write
        BEFORE UPDATE OF status ON daemon_budget_epochs
        BEGIN
            SELECT RAISE(ABORT, 'injected budget terminal write failure');
        END
        """
    )
    connection.commit()
    before = _budget_atomicity_snapshot(store, cas_root, epoch.budget_id)
    runtime = SimpleNamespace(
        store=store,
        cas_store=ContentAddressedByteStore(cas_root),
    )
    try:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="injected budget terminal write failure",
        ):
            terminalize_daemon_budget_with_suspension(
                runtime,
                budget_id=epoch.budget_id,
                observed_at=11,
                status="exhausted",
                reason="invocation_limit_exhausted",
                command="daemon",
            )

        assert connection.in_transaction is False
        assert _budget_atomicity_snapshot(store, cas_root, epoch.budget_id) == before
    finally:
        store.close()


def test_budget_terminal_runtime_row_failure_rolls_back_complete_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli.context import (
        terminalize_daemon_budget_with_suspension,
    )
    from millrace.substrate import sqlite as sqlite_module

    _db_path, cas_root, _state, store, epoch, _session = (
        _persist_budget_atomicity_fixture(tmp_path)
    )
    connection = store._connection
    before = _budget_atomicity_snapshot(store, cas_root, epoch.budget_id)
    persist_runtime_state_rows = sqlite_module.persist_runtime_state_rows
    runtime = SimpleNamespace(
        store=store,
        cas_store=ContentAddressedByteStore(cas_root),
    )

    def fail_after_runtime_row_replacement(
        candidate_connection: sqlite3.Connection,
        state: RuntimeState,
        cas_store: ContentAddressedByteStore,
    ) -> None:
        def fail_before_outer_commit() -> None:
            assert candidate_connection.in_transaction
            assert candidate_connection.execute(
                "SELECT 1 FROM dispatch_suspension"
            ).fetchone() is not None
            raise sqlite3.OperationalError(
                "injected runtime row failure after replacement"
            )

        persist_runtime_state_rows(
            candidate_connection,
            state,
            cas_store,
            _before_sqlite_commit=fail_before_outer_commit,
        )

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(
                sqlite_module,
                "persist_runtime_state_rows",
                fail_after_runtime_row_replacement,
            )
            with pytest.raises(
                sqlite3.OperationalError,
                match="injected runtime row failure after replacement",
            ):
                terminalize_daemon_budget_with_suspension(
                    runtime,
                    budget_id=epoch.budget_id,
                    observed_at=11,
                    status="exhausted",
                    reason="invocation_limit_exhausted",
                    command="daemon",
                )

        assert connection.in_transaction is False
        assert _budget_atomicity_snapshot(store, cas_root, epoch.budget_id) == before
    finally:
        store.close()


@pytest.mark.parametrize(
    "operation",
    ("accepted_start", "usage"),
)
def test_budget_counter_write_failure_rolls_back_binding_and_usage(
    tmp_path: Path,
    operation: str,
) -> None:
    _db_path, cas_root, _state, store, epoch, session = (
        _persist_budget_atomicity_fixture(tmp_path)
    )
    connection = store._connection
    if operation == "usage":
        store.record_budgeted_runner_start(epoch.budget_id, session)
    if operation == "accepted_start":
        trigger_column = "accepted_start_count"
    else:
        trigger_column = "cumulative_total_tokens"
    connection.execute(
        f"""
        CREATE TRIGGER fail_budget_counter_write
        BEFORE UPDATE OF {trigger_column} ON daemon_budget_epochs
        BEGIN
            SELECT RAISE(ABORT, 'injected budget counter write failure');
        END
        """
    )
    connection.commit()
    before = _budget_atomicity_snapshot(store, cas_root, epoch.budget_id)
    try:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="injected budget counter write failure",
        ):
            if operation == "accepted_start":
                store.record_budgeted_runner_start(epoch.budget_id, session)
            else:
                store.record_runner_session_usage(
                    RunnerSessionUsageRecord(
                        budget_id=epoch.budget_id,
                        session_id=session.session_id,
                        run_id=session.run_id,
                        dispatch_generation=session.dispatch_generation,
                        session_fencing_token=session.session_fencing_token,
                        input_tokens=2,
                        output_tokens=3,
                        total_tokens=5,
                        observed_at=11,
                        final=False,
                    )
                )

        assert connection.in_transaction is False
        assert _budget_atomicity_snapshot(store, cas_root, epoch.budget_id) == before
    finally:
        store.close()


def test_budget_accepted_start_aggregate_drift_refuses_without_partial_binding(
    tmp_path: Path,
) -> None:
    _db_path, cas_root, _state, store, epoch, session = (
        _persist_budget_atomicity_fixture(tmp_path)
    )
    connection = store._connection
    store.record_budgeted_runner_start(epoch.budget_id, session)
    connection.execute(
        """
        UPDATE daemon_budget_epochs
        SET max_invocations = NULL, accepted_start_count = ?
        WHERE budget_id = ?
        """,
        (DURABLE_INT64_MAX, epoch.budget_id),
    )
    connection.commit()
    before = _budget_atomicity_snapshot(store, cas_root, epoch.budget_id)
    try:
        with pytest.raises(
            ValueError,
            match="invalid daemon budget aggregate authority",
        ):
            store.record_budgeted_runner_start(epoch.budget_id, session)

        assert connection.in_transaction is False
        assert _budget_atomicity_snapshot(store, cas_root, epoch.budget_id) == before
    finally:
        store.close()


def test_budget_usage_aggregate_drift_refuses_without_partial_usage(
    tmp_path: Path,
) -> None:
    _db_path, cas_root, _state, store, epoch, session = (
        _persist_budget_atomicity_fixture(tmp_path)
    )
    connection = store._connection
    store.record_budgeted_runner_start(epoch.budget_id, session)
    connection.execute(
        """
        UPDATE daemon_budget_epochs
        SET cumulative_input_tokens = ?,
            cumulative_output_tokens = 0,
            cumulative_total_tokens = ?
        WHERE budget_id = ?
        """,
        (DURABLE_INT64_MAX - 1, DURABLE_INT64_MAX - 1, epoch.budget_id),
    )
    connection.commit()
    before = _budget_atomicity_snapshot(store, cas_root, epoch.budget_id)
    try:
        with pytest.raises(
            ValueError,
            match="invalid daemon budget aggregate authority",
        ):
            store.record_runner_session_usage(
                RunnerSessionUsageRecord(
                    budget_id=epoch.budget_id,
                    session_id=session.session_id,
                    run_id=session.run_id,
                    dispatch_generation=session.dispatch_generation,
                    session_fencing_token=session.session_fencing_token,
                    input_tokens=2,
                    output_tokens=0,
                    total_tokens=2,
                    observed_at=11,
                    final=True,
                )
            )

        assert connection.in_transaction is False
        assert _budget_atomicity_snapshot(store, cas_root, epoch.budget_id) == before
    finally:
        store.close()


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


def _persist_budget_atomicity_fixture(
    tmp_path: Path,
) -> tuple[
    Path,
    Path,
    RuntimeState,
    SQLiteRuntimeStore,
    DaemonBudgetEpochRecord,
    RunnerSessionRecord,
]:
    state = worker_runtime_state()
    assert state.default_plan_ref is not None
    session = next(iter(state.runner_sessions.values()))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    store = SQLiteRuntimeStore.open(db_path)
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-atomicity",
        workspace_path=str(tmp_path),
        selected_plan_ref=state.default_plan_ref,
        max_wall_seconds=None,
        max_invocations=2,
        max_total_tokens=100,
        started_at=10,
        wall_deadline=None,
        last_observed_at=10,
    )
    store.create_or_resume_daemon_budget_epoch(epoch)
    store.reserve_budgeted_runner_start(epoch.budget_id, session)
    return db_path, cas_root, state, store, epoch, session


def _budget_atomicity_snapshot(
    store: SQLiteRuntimeStore,
    cas_root: Path,
    budget_id: str,
) -> tuple[object, ...]:
    connection = store._connection
    return (
        store.load_runtime_state(ContentAddressedByteStore(cas_root)),
        frozenset(
            str(row[0])
            for query in (
                "SELECT selected_plan_digest FROM admitted_plan_pins",
                "SELECT payload_digest FROM work_items",
                "SELECT payload_digest FROM runner_observations",
                "SELECT payload_digest FROM artifacts",
            )
            for row in connection.execute(query).fetchall()
        ),
        connection.execute(
            "SELECT * FROM daemon_budget_epochs WHERE budget_id = ?",
            (budget_id,),
        ).fetchall(),
        connection.execute(
            "SELECT * FROM daemon_budget_sessions WHERE budget_id = ?",
            (budget_id,),
        ).fetchall(),
        connection.execute(
            "SELECT * FROM runner_session_usage WHERE budget_id = ?",
            (budget_id,),
        ).fetchall(),
        connection.execute("SELECT * FROM dispatch_suspension").fetchall(),
    )
