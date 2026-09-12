from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from uuid import uuid4

import pytest

from millrace.adapters.cli.daemon_control import _target
from millrace.adapters.cli.daemon_process import process_identity
from millrace.contracts.controls import ControlRequest, canonical_json
from millrace.contracts.daemon_control import runtime_identity
from millrace.substrate.errors import ControlOperationError, StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.run_controls import epoch_for, runtime_with_run, start_intent

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="exact native process/lifecycle qualification requires macOS",
)

def registered(runtime):
    scope = runtime.store.register_daemon(
        {
            "daemon_id": str(uuid4()),
            "process_nonce": str(uuid4()),
            "process": process_identity(os.getpid()),
            "runtime": runtime_identity("0.22.3", 11),
            "launch_correlation_id": "fixture-launch",
        },
        runtime.store.control_identity()["source_revision"],
    )
    return runtime.store.update_daemon(scope, status="ready_idle")


def stop_request(runtime, scope, **overrides):
    target = _target(scope, runtime.store.control_identity()["source_revision"])
    target["plan_fingerprint"] = runtime.store.daemon_default_plan()
    payload = {
        "contract_id": "millrace.core.controls",
        "contract_revision": 1,
        "action": "daemon.stop",
        "operation_id": str(uuid4()),
        "caller_id": "fixture",
        "actor_id": "operator",
        "reason": "private stop reason",
        "correlation_id": "stop",
        "target": target,
    }
    payload.update(overrides)
    return ControlRequest.parse(canonical_json(payload))


def accept(runtime, scope, request):
    return runtime.store.accept_daemon_stop(
        scope, request, deadline=time.monotonic() + 1
    )


@pytest.mark.parametrize("budgeted", [False, True])
def test_committed_stop_blocks_unit_and_atomic_start_before_event_delivery(
    tmp_path, budgeted
):
    runtime, run = runtime_with_run(tmp_path, session=True)
    budget = epoch_for(runtime, run) if budgeted else None
    scope = registered(runtime)
    runtime.store.daemon_scope = scope
    request = stop_request(runtime, scope)
    other = SQLiteRuntimeStore.open(runtime.paths.db_path)
    result = other.accept_daemon_stop(scope, request, deadline=time.monotonic() + 1)
    assert result["receipt"]["disposition"] == "accepted_pending"
    # No event object or delivery exists: independent connection has committed.
    with pytest.raises(ControlOperationError, match="daemon_admission_stopped"):
        runtime.store.admit_daemon_unit()
    before = runtime.store.load_runtime_state(runtime.cas_store)
    with pytest.raises(ControlOperationError, match="daemon_admission_stopped"):
        start_intent(runtime, run, budget_id=budget.budget_id if budget else None)
    assert runtime.store.daemon_budget_id_for_session(run.current_session_id) is None
    assert runtime.store.load_runtime_state(runtime.cas_store) == before
    assert runtime.store.show_operation(request)["receipt"] == result["receipt"]
    # Existing state persistence/cleanup and a separately owned manual start survive.
    runtime.store.persist_runtime_state(before, runtime.cas_store)
    runtime.store.daemon_scope = None
    assert (
        start_intent(runtime, run, budget_id=budget.budget_id if budget else None)
        is not None
    )
    other.close()
    runtime.close()


def test_stop_replay_seal_and_changed_target_conflict(tmp_path):
    runtime, _ = runtime_with_run(tmp_path)
    scope = registered(runtime)
    sealed = stop_request(runtime, scope)
    runtime.store.resolve_operation(sealed)
    assert (
        accept(runtime, scope, sealed)["receipt"]["disposition"]
        == "sealed_not_accepted"
    )
    request = stop_request(runtime, scope)
    first = accept(runtime, scope, request)
    assert accept(runtime, scope, request)["receipt"] == first["receipt"]
    changed = request.payload
    changed["target"]["process_nonce"] = str(uuid4())
    with pytest.raises(ControlOperationError, match="idempotency_conflict"):
        accept(runtime, scope, ControlRequest.parse(canonical_json(changed)))
    assert runtime.store.daemon_records()[-1]["stop_key"] == list(request.key)
    runtime.close()


@pytest.mark.parametrize(
    "field",
    [
        "daemon_id",
        "daemon_generation",
        "process_nonce",
        "expected_source_revision",
        "plan_fingerprint",
        "expected_runtime",
    ],
)
def test_stale_exact_daemon_target_refuses_without_stop(tmp_path, field):
    runtime, _ = runtime_with_run(tmp_path)
    scope = registered(runtime)
    value = stop_request(runtime, scope).payload
    if field in {"daemon_id", "process_nonce"}:
        value["target"][field] = str(uuid4())
    elif field == "expected_runtime":
        value["target"][field]["runtime_version"] = "other"
    elif field == "plan_fingerprint":
        value["target"][field] = "sha256:" + "0" * 64
    else:
        value["target"][field] += 1
    result = accept(runtime, scope, ControlRequest.parse(canonical_json(value)))
    assert not result["receipt"]["accepted"]
    assert runtime.store.daemon_records()[-1]["stop_key"] is None
    runtime.close()


def test_default_plan_change_requires_fresh_target_same_incarnation(tmp_path):
    runtime, _ = runtime_with_run(tmp_path)
    scope = registered(runtime)
    stale = stop_request(runtime, scope)
    connection = runtime.store._connection
    connection.execute("DELETE FROM default_plan")
    connection.commit()
    assert not accept(runtime, scope, stale)["receipt"]["accepted"]
    fresh = stop_request(runtime, scope)
    assert fresh.payload["target"]["plan_fingerprint"] is None
    assert accept(runtime, scope, fresh)["receipt"]["accepted"]
    runtime.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "current_missing",
        "old_revision",
        "current_nonce",
        "missing_session",
        "wrong_session",
        "missing_final",
        "wrong_final",
        "receipt_link",
    ],
)
def test_retained_history_corruption_refuses_without_repair(tmp_path, corruption):
    runtime, run = runtime_with_run(tmp_path, session=True)
    scope = registered(runtime)
    session = runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
        run.current_session_id
    ]
    fence = {
        name: getattr(session, name)
        for name in (
            "session_id",
            "run_id",
            "dispatch_generation",
            "session_fencing_token",
        )
    }
    runtime.store.attach_daemon_sessions(scope, [fence])
    request = stop_request(runtime, scope)
    accept(runtime, scope, request)
    runtime.store.finish_daemon(
        scope,
        {"listener_teardown": "complete", "stopped_reason": "signal"},
        [{"fence": fence, "cleanup": "not_required"}],
        runtime.store.control_identity()["source_revision"],
    )
    assert runtime.store.daemon_records()[-1]["status"] == "shutdown_complete"
    connection = runtime.store._connection
    if corruption == "current_missing":
        with pytest.raises(sqlite3.IntegrityError, match="retained"):
            connection.execute("DELETE FROM daemon_records")
        connection.rollback()
    triggers = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    for name, _ in triggers:
        connection.execute(f'DROP TRIGGER "{name}"')
    if corruption == "current_missing":
        connection.execute("DELETE FROM daemon_records")
    elif corruption == "old_revision":
        connection.execute(
            "UPDATE daemon_events SET record_json=json_set(record_json, "
            "'$.revision', 55) WHERE revision=1"
        )
    elif corruption == "current_nonce":
        connection.execute(
            "UPDATE daemon_records SET record_json=json_set(record_json, "
            "'$.process_nonce', ?)",
            (str(uuid4()),),
        )
    elif corruption == "missing_session":
        connection.execute("DELETE FROM daemon_sessions")
    elif corruption == "wrong_session":
        connection.execute("UPDATE daemon_sessions SET session_id='other'")
    elif corruption == "missing_final":
        connection.execute("DELETE FROM daemon_session_results")
    elif corruption == "wrong_final":
        connection.execute(
            "UPDATE daemon_session_results SET record_json=json_set(record_json, "
            "'$.cleanup', 'complete')"
        )
    else:
        connection.execute(
            "UPDATE control_results SET result_json=json_set(result_json, "
            "'$.evidence.runtime_cleanup', 'complete') WHERE result_id='shutdown'"
        )
    for _, sql in triggers:
        connection.execute(sql)
    connection.commit()
    runtime.store.close()
    store = SQLiteRuntimeStore.open(runtime.paths.db_path)
    before = runtime.paths.db_path.read_bytes()
    with pytest.raises(ControlOperationError, match="unknown"):
        store.daemon_records()
    assert runtime.paths.db_path.read_bytes() == before
    store.close()


def test_more_than_100_sessions_have_complete_fenced_final_pages(tmp_path):
    runtime, _ = runtime_with_run(tmp_path)
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
    assert runtime.store.daemon_records()[-1]["session_count"] == 125
    request = stop_request(runtime, scope)
    accept(runtime, scope, request)
    # Missing session authority remains unknown for each retained fence.
    final = runtime.store.finish_daemon(
        scope,
        {"listener_teardown": "complete", "stopped_reason": "signal"},
        [{"fence": fence, "cleanup": "unknown"} for fence in fences],
        runtime.store.control_identity()["source_revision"],
    )
    assert final["final_summary"]["runtime_cleanup"] == "unknown"
    revision = runtime.store.control_identity()["source_revision"]
    pages = [
        runtime.store.daemon_session_page(scope["daemon_id"], after, revision)
        for after in (0, 50, 100)
    ]
    assert [len(page["runner_sessions"]) for page in pages] == [50, 50, 25]
    assert [page["next_after_session"] for page in pages] == [50, 100, None]
    assert [row["session_id"] for page in pages for row in page["runner_sessions"]] == [
        fence["session_id"] for fence in fences
    ]
    assert all(
        row["cleanup"] == "unknown" for page in pages for row in page["runner_sessions"]
    )
    assert all(len(json.dumps(page)) < 120 * 1024 for page in pages)
    assert len(canonical_json(final)) < 16384
    with pytest.raises(ControlOperationError, match="snapshot_changed"):
        runtime.store.daemon_session_page(scope["daemon_id"], 50, revision - 1)
    runtime.close()


def test_daemon_projection_holds_one_snapshot_during_between_read_stop(
    tmp_path, monkeypatch
):
    import millrace.substrate._sqlite_daemon as daemon_controls

    runtime, _ = runtime_with_run(tmp_path)
    scope = registered(runtime)
    request = stop_request(runtime, scope)
    revision = runtime.store.control_identity()["source_revision"]
    entered = threading.Event()
    completed = threading.Event()
    outcomes = []

    def stop_concurrently():
        store = SQLiteRuntimeStore.open(runtime.paths.db_path)
        try:
            entered.set()
            outcomes.append(
                store.accept_daemon_stop(scope, request, deadline=time.monotonic() + 1)
            )
        finally:
            store.close()
            completed.set()

    writer = threading.Thread(target=stop_concurrently)
    original = daemon_controls.session_page

    def page_between_reads(connection, daemon_id, after=0, **kwargs):
        writer.start()
        assert entered.wait(0.5)
        assert not completed.wait(0.05)
        return original(connection, daemon_id, after, **kwargs)

    monkeypatch.setattr(daemon_controls, "session_page", page_between_reads)
    try:
        snapshot = runtime.store.daemon_snapshot()
    finally:
        writer.join(2)
    assert not writer.is_alive()
    assert outcomes[0]["receipt"]["accepted"]
    assert snapshot["record"]["status"] == "ready_idle"
    assert snapshot["record"]["stop_key"] is None
    assert snapshot["identity"]["source_revision"] == revision
    assert snapshot["page"]["source_revision"] == revision
    assert snapshot["page"]["runner_sessions"] == []
    monkeypatch.setattr(daemon_controls, "session_page", original)
    later = runtime.store.daemon_snapshot()
    assert later["record"]["status"] == "stop_requested"
    assert later["record"]["stop_key"] == list(request.key)
    assert later["identity"]["source_revision"] == revision + 3
    assert later["page"]["source_revision"] == revision + 3
    runtime.close()


def test_synthetic_signal_stop_coalesces_under_admission_lock(tmp_path):
    runtime, _ = runtime_with_run(tmp_path)
    scope = registered(runtime)
    runtime.store.signal_daemon_stop(scope)
    first = runtime.store.daemon_snapshot()
    runtime.store.signal_daemon_stop(scope)
    assert runtime.store.daemon_snapshot() == first
    assert first["record"]["status"] == "stop_requested"
    assert first["record"]["stop_key"][3:] == [
        "core.local-signal",
        "signal:" + scope["daemon_id"],
    ]
    runtime.close()


def test_wide_unicode_session_pages_bound_wire_bytes_and_retain_all_rows(tmp_path):
    runtime, _ = runtime_with_run(tmp_path)
    scope = registered(runtime)
    fences = [
        {
            "session_id": "é" * 250 + str(n),
            "run_id": "é" * 250 + str(n),
            "session_fencing_token": "é" * 256,
            "dispatch_generation": 1,
        }
        for n in range(60)
    ]
    runtime.store.attach_daemon_sessions(scope, fences)
    assert len(runtime.store.daemon_owned_sessions(scope)) == 60
    seen = []
    after = 0
    revision = runtime.store.control_identity()["source_revision"]
    while True:
        page = runtime.store.daemon_session_page(scope["daemon_id"], after, revision)
        assert len(json.dumps(page).encode()) < 65 * 1024
        seen.extend(row["session_id"] for row in page["runner_sessions"])
        if page["next_after_session"] is None:
            break
        assert page["next_after_session"] > after
        after = page["next_after_session"]
    assert seen == [fence["session_id"] for fence in fences]
    runtime.close()


def replace_retained_identity_for_corruption(store, field, value):
    """Construct already-corrupt matching rows; restore all guards before reopen."""
    connection = store._connection
    triggers = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    for name, _ in triggers:
        connection.execute(f'DROP TRIGGER "{name}"')
    for table in ("daemon_records", "daemon_events"):
        for rowid, raw in connection.execute(
            f"SELECT rowid, record_json FROM {table}"
        ).fetchall():
            record = json.loads(raw)
            owner = record
            key = field
            if "." in field:
                key = field.split(".", 1)[1]
                owner = record["runtime"]
            if value == "__missing__":
                owner.pop(key)
            else:
                owner[key] = value
            connection.execute(
                f"UPDATE {table} SET record_json=? WHERE rowid=?",
                (
                    json.dumps(
                        record,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                    rowid,
                ),
            )
    for _, sql in triggers:
        connection.execute(sql)
    connection.commit()


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime", {}),
        ("runtime", []),
        ("runtime", None),
        ("runtime", "__missing__"),
        *[
            ("runtime." + key, "__missing__")
            for key in runtime_identity("historical", 11)
        ],
        ("runtime.extra", "unknown"),
        *[
            ("runtime." + key, value)
            for key in ("public_contract_id", "runtime_distribution", "runtime_version")
            for value in (True, " ", "x" * 129)
        ],
        *[
            ("runtime." + key, value)
            for key in ("public_contract_revision", "store_schema_version")
            for value in (True, -1, 1.5, 2**63)
        ],
        ("runtime.public_schema_digest", "invalid"),
        ("runtime.public_schema_digest", None),
        ("runtime.build_identity", True),
        ("runtime.build_identity", "sha256:" + "0" * 64),
        ("plan_fingerprint", "invalid"),
        ("plan_fingerprint", True),
        ("plan_fingerprint", "__missing__"),
        ("stop_plan_fingerprint", "invalid"),
    ],
)
def test_repair1_malformed_retained_identity_refuses_after_reopen_without_writes(
    tmp_path, field, value
):
    runtime, _ = runtime_with_run(tmp_path)
    registered(runtime)
    replace_retained_identity_for_corruption(runtime.store, field, value)
    runtime.close()
    store = SQLiteRuntimeStore.open(runtime.paths.db_path)
    try:
        before = runtime.paths.db_path.read_bytes()
        with pytest.raises(ControlOperationError, match="unknown"):
            store.daemon_snapshot()
        assert runtime.paths.db_path.read_bytes() == before
    finally:
        store.close()


def _progress_transition(runtime, run, *, input_id="capture-start"):
    from millrace.adapters.cli.context import transition_context
    from millrace.contracts.transition import AdvanceRunnerSession
    from millrace.kernel import apply, decide

    state = runtime.store.load_runtime_state(runtime.cas_store)
    session = state.runner_sessions[run.current_session_id]
    transition = AdvanceRunnerSession(
        input_id,
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        next_state="starting",
        occurred_at=101,
    )
    decision = decide(
        state,
        transition,
        transition_context(command="test", input_id_value=transition.input_id),
    )
    assert decision.accepted
    return apply(state, decision)


def test_progress_is_atomic_attributed_and_idle_reads_do_not_rewrite(
    tmp_path, monkeypatch
):
    from millrace.adapters.cli.daemon_control import _projection
    from millrace.substrate._sqlite_write import persist_runtime_state_rows

    runtime, run = runtime_with_run(tmp_path, session=True)
    scope = registered(runtime)
    runtime.store.daemon_scope = scope
    initial = runtime.store.daemon_snapshot()
    assert initial["record"]["runtime_progress"] is None
    candidate = _progress_transition(
        runtime, run, input_id="large-" + ('x\x00😀"\n' * 5000)
    )
    before = runtime.store.load_runtime_state(runtime.cas_store)

    def crash():
        raise RuntimeError("before commit")

    with pytest.raises(RuntimeError, match="before commit"):
        persist_runtime_state_rows(
            runtime.store._connection,
            candidate,
            runtime.cas_store,
            daemon_scope=scope,
            _before_sqlite_commit=crash,
        )
    assert runtime.store.daemon_snapshot() == initial
    assert runtime.store.load_runtime_state(runtime.cas_store) == before
    monkeypatch.setattr(
        "millrace.substrate._sqlite_daemon.time.time_ns", lambda: 987654321
    )
    runtime.store.persist_runtime_state(candidate, runtime.cas_store)
    snapshot = runtime.store.daemon_snapshot()
    record = snapshot["record"]
    progress = record["runtime_progress"]
    assert progress["captured_at_ns"] == 987654321
    assert progress["source_revision"] == record["source_revision"]
    assert progress["daemon_revision"] == record["revision"]
    assert progress["transition_order"] == len(candidate.transitions) - 1
    assert len(progress["transition_digest"]) == 64
    assert len(progress["run_fence_digest"]) == 64
    assert progress["session_snapshot"] == {
        "session_id": "session-a",
        "run_id": run.run_ref.run_id,
        "dispatch_generation": 1,
        "session_fencing_token": "session-fence-a",
    }
    assert len(canonical_json(record).encode()) < 16384
    for _ in range(3):
        runtime.store.persist_runtime_state(candidate, runtime.cas_store)
        assert runtime.store.daemon_snapshot()["record"] == record
        retained = runtime.store.daemon_snapshot()
        assert runtime.store.daemon_snapshot() == retained
        view = _projection(record, retained["identity"]["source_revision"])
        assert view["runtime_progress"]["availability"] == "available"
        assert view["runtime_progress"]["last_runtime_progress_at"] == 987654321
    runtime.close()


def test_progress_refuses_wrong_incarnation_and_final_shutdown(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    scope = registered(runtime)
    state = _progress_transition(runtime, run)
    runtime.store.daemon_scope = dict(scope, process_nonce=str(uuid4()))
    with pytest.raises(ControlOperationError, match="daemon_incarnation_mismatch"):
        runtime.store.persist_runtime_state(state, runtime.cas_store)
    assert runtime.store.daemon_records()[-1]["runtime_progress"] is None
    runtime.store.daemon_scope = scope
    runtime.store.finish_daemon(
        scope,
        {"listener_teardown": "complete", "stopped_reason": "signal"},
        [],
        runtime.store.control_identity()["source_revision"],
    )
    final = runtime.store.daemon_snapshot()
    with pytest.raises(ControlOperationError, match="daemon_already_shutdown"):
        runtime.store.persist_runtime_state(state, runtime.cas_store)
    assert runtime.store.daemon_snapshot() == final
    runtime.close()


def _rewrite_progress_fixture(runtime, mutate):
    connection = runtime.store._connection
    triggers = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    for name, _ in triggers:
        connection.execute(f'DROP TRIGGER "{name}"')
    for daemon_id, revision, raw in connection.execute(
        "SELECT daemon_id, revision, record_json FROM daemon_events"
    ).fetchall():
        record = json.loads(raw)
        mutate(record)
        connection.execute(
            "UPDATE daemon_events SET record_json=? WHERE daemon_id=? AND revision=?",
            (canonical_json(record), daemon_id, revision),
        )
    connection.execute(
        "UPDATE daemon_records SET record_json=(SELECT record_json FROM daemon_events "
        "WHERE daemon_events.daemon_id=daemon_records.daemon_id "
        "ORDER BY revision DESC LIMIT 1)"
    )
    for _, sql in triggers:
        connection.execute(sql)
    connection.commit()


@pytest.mark.parametrize(
    "corruption",
    [
        "transition_digest",
        "transition_order",
        "run_fence_digest",
        "daemon_revision",
        "source_revision",
        "captured_at_ns",
        "session_snapshot",
        "session_null",
        "erase",
    ],
)
def test_progress_corruption_read_is_nonmutating(tmp_path, corruption):
    runtime, run = runtime_with_run(tmp_path, session=True)
    scope = registered(runtime)
    runtime.store.daemon_scope = scope
    runtime.store.persist_runtime_state(
        _progress_transition(runtime, run), runtime.cas_store
    )

    def corrupt(record):
        progress = record.get("runtime_progress")
        if not progress:
            return
        if corruption == "erase":
            del record["runtime_progress"]
        elif corruption == "session_null":
            progress["session_snapshot"] = None
        elif corruption == "session_snapshot":
            progress[corruption]["session_fencing_token"] = "wrong"
        elif corruption.endswith("digest"):
            progress[corruption] = "0" * 64
        elif corruption == "captured_at_ns":
            progress[corruption] = -1
        else:
            progress[corruption] += 1

    _rewrite_progress_fixture(runtime, corrupt)
    before = runtime.paths.db_path.read_bytes()
    with pytest.raises(ControlOperationError, match="daemon_history_unknown"):
        runtime.store.daemon_snapshot()
    assert runtime.paths.db_path.read_bytes() == before
    runtime.close()


def test_legacy_progress_absence_is_not_repaired_or_invented(tmp_path):
    from millrace.adapters.cli.daemon_control import _projection

    runtime, _ = runtime_with_run(tmp_path)
    registered(runtime)
    _rewrite_progress_fixture(
        runtime, lambda record: record.pop("runtime_progress", None)
    )
    before = runtime.paths.db_path.read_bytes()
    snapshot = runtime.store.daemon_snapshot()
    view = _projection(snapshot["record"], snapshot["identity"]["source_revision"])
    assert view["runtime_progress"]["availability"] == "unavailable"
    assert view["runtime_progress"]["reason"] == "legacy_capture_absent"
    assert view["runtime_progress"]["last_runtime_progress_at"] is None
    assert runtime.paths.db_path.read_bytes() == before
    runtime.close()


def test_stop_and_refusals_are_not_progress_but_cleanup_is(tmp_path):
    from millrace.adapters.cli.context import transition_context
    from millrace.adapters.cli.session_coordinator import request_operator_cancellation
    from millrace.contracts.transition import ClaimWork
    from millrace.kernel import apply, decide

    runtime, run = runtime_with_run(tmp_path, session=True)
    scope = registered(runtime)
    runtime.store.daemon_scope = scope
    before = runtime.store.load_runtime_state(runtime.cas_store)
    runtime.store.persist_runtime_state(
        _progress_transition(runtime, run), runtime.cas_store
    )
    first = runtime.store.daemon_records()[-1]["runtime_progress"]
    with pytest.raises(StorageIntegrityError, match="stale"):
        runtime.store.persist_runtime_state(before, runtime.cas_store)
    assert runtime.store.daemon_records()[-1]["runtime_progress"] == first
    state = runtime.store.load_runtime_state(runtime.cas_store)
    refusal = ClaimWork("already-claimed", activation_id=run.activation_id)
    decision = decide(
        state,
        refusal,
        transition_context(command="test", input_id_value=refusal.input_id),
    )
    assert not decision.accepted
    runtime.store.persist_runtime_state(apply(state, decision), runtime.cas_store)
    assert runtime.store.daemon_records()[-1]["runtime_progress"] == first
    assert accept(runtime, scope, stop_request(runtime, scope))["receipt"]["accepted"]
    assert runtime.store.daemon_records()[-1]["runtime_progress"] == first
    result = request_operator_cancellation(
        runtime, run_id=run.run_ref.run_id, request_id="cleanup", actor_id="operator"
    )
    assert result.accepted
    progress = runtime.store.daemon_records()[-1]["runtime_progress"]
    assert progress["daemon_revision"] > first["daemon_revision"]
    assert progress["transition_order"] > first["transition_order"]
    assert progress["session_snapshot"] == first["session_snapshot"]
    runtime.close()


@pytest.mark.parametrize(
    "corruption", ["future_session", "claim_run_missing", "unrelated_run_invented"]
)
def test_progress_session_presence_uses_capture_history_not_current_session(
    tmp_path, corruption
):
    from millrace.adapters.cli.context import transition_context
    from millrace.contracts.ids import QueueFamilyId
    from millrace.contracts.state import RunnerSessionCompletionRecord
    from millrace.contracts.transition import (
        ClaimWork,
        CreateRunnerSession,
        EnqueueWork,
        RecordRunnerSessionCompletion,
    )
    from millrace.kernel import apply, decide

    runtime, _ = runtime_with_run(tmp_path)
    runtime.store.daemon_scope = registered(runtime)

    def advance(state, transition):
        decision = decide(
            state,
            transition,
            transition_context(command="test", input_id_value=transition.input_id),
        )
        assert decision.accepted
        return apply(state, decision)

    state = runtime.store.load_runtime_state(runtime.cas_store)
    enqueue = EnqueueWork(
        "historical-enqueue",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"prompt_id": "historical", "body": "Independent work"},
    )
    state = advance(state, enqueue)
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    sessionless = runtime.store.daemon_records()[-1]["runtime_progress"]
    assert sessionless["run_fence_digest"] is None
    assert sessionless["session_snapshot"] is None
    activation = next(
        x
        for x in state.activations.values()
        if x.created_by_input_id == enqueue.input_id
    )
    state = advance(
        state, ClaimWork("historical-claim", activation_id=activation.activation_id)
    )
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    run_only = runtime.store.daemon_records()[-1]["runtime_progress"]
    assert run_only["run_fence_digest"] is not None
    assert run_only["session_snapshot"] is None
    run = next(
        x for x in state.runs.values() if x.created_by_input_id == "historical-claim"
    )
    state = advance(
        state,
        CreateRunnerSession(
            "historical-create-1",
            run_ref=run.run_ref,
            session_id="history-s1",
            session_fencing_token="history-f1",
            created_at=100,
            explicit_retry_intent=False,
        ),
    )
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    first = runtime.store.daemon_records()[-1]["runtime_progress"]
    assert first["session_snapshot"]["session_id"] == "history-s1"
    completion = RunnerSessionCompletionRecord(
        completion_id="history-completion",
        session_id="history-s1",
        run_id=run.run_ref.run_id,
        dispatch_generation=1,
        session_fencing_token="history-f1",
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind=None,
        adapter_error_kind="invocation_failed",
        runner_result_evidence_digest=None,
        primary_cancellation_request_id=None,
        cleanup_disposition="not_required",
        started_at=None,
        cancel_requested_at=None,
        completed_at=150,
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest=runtime.cas_store.put_bytes(b"failure diagnostic"),
        application_input_id="cli:run.session-completion:history-completion",
    )
    state = advance(
        state,
        RecordRunnerSessionCompletion(
            "history-complete-1",
            run_ref=run.run_ref,
            expected_state="created",
            completion=completion,
        ),
    )
    state = advance(
        state,
        CreateRunnerSession(
            "historical-create-2",
            run_ref=run.run_ref,
            session_id="history-s2",
            session_fencing_token="history-f2",
            created_at=200,
            explicit_retry_intent=True,
        ),
    )
    # Completion plus retry are one transaction; only its final snapshot is captured.
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    current = runtime.store.daemon_snapshot()
    assert (
        current["record"]["runtime_progress"]["session_snapshot"]["session_id"]
        == "history-s2"
    )
    retained = [
        json.loads(row[0])["runtime_progress"]
        for row in runtime.store._connection.execute(
            "SELECT record_json FROM daemon_events ORDER BY revision"
        )
    ]
    assert sessionless in retained and run_only in retained and first in retained
    before = runtime.paths.db_path.read_bytes()
    assert runtime.store.daemon_snapshot() == current
    assert runtime.paths.db_path.read_bytes() == before

    def insert_future_session(record):
        if corruption == "unrelated_run_invented":
            if record.get("runtime_progress") == sessionless:
                record["runtime_progress"]["run_fence_digest"] = run_only[
                    "run_fence_digest"
                ]
        elif record.get("runtime_progress") == run_only:
            if corruption == "claim_run_missing":
                record["runtime_progress"]["run_fence_digest"] = None
            else:
                record["runtime_progress"]["session_snapshot"] = first[
                    "session_snapshot"
                ]

    _rewrite_progress_fixture(runtime, insert_future_session)
    corrupted = runtime.paths.db_path.read_bytes()
    with pytest.raises(ControlOperationError, match="daemon_history_unknown"):
        runtime.store.daemon_snapshot()
    assert runtime.paths.db_path.read_bytes() == corrupted
    runtime.close()
