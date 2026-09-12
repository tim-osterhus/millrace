from __future__ import annotations

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from millrace.adapters.cli.context import transition_context
from millrace.adapters.cli.session_completion import _persist_transition
from millrace.contracts.controls import ControlRequest
from millrace.contracts.transition import AdvanceRunnerSession, CreateRunnerSession
from millrace.kernel import apply, decide
from millrace.substrate.errors import ControlOperationError, StoreIdentityMismatch
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.run_controls import (
    add_run,
    epoch_for,
    pause,
    request_for,
    runtime_with_run,
    start_intent,
)


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": ""},
        {"run_id": "run\ninvalid"},
        {"run_id": "\ud800"},
        {"run_id": "x" * (16 * 1024 + 1)},
        {"run_id": "é" * 8193},
        {"cause": "c" * 129},
        {"recorded_at": "t" * 129},
        {"control_revision": 0},
        {"source_revision": -1},
        {"pause_id": "not-a-uuid"},
        {"state": "not-a-state"},
    ],
)
def test_long_run_durable_control_retains_other_validation(tmp_path, change):
    from cli.test_cli_run_controls import _runtime_with_generated_long_run
    from millrace.contracts.controls import CONTROL_MAX_BYTES

    runtime, run = _runtime_with_generated_long_run(
        tmp_path, session=True, enqueue_id="native-enqueue"
    )
    receipt = pause(runtime, run)["receipt"]
    assert receipt["accepted"]
    control = runtime.store.load_runtime_state(
        runtime.cas_store
    ).run_execution_controls[run.run_ref.run_id]
    assert len(control.run_id.encode("utf-8")) == 157
    assert (
        replace(control, run_id="x" * CONTROL_MAX_BYTES).run_id
        == "x" * CONTROL_MAX_BYTES
    )
    with pytest.raises(ValueError):
        replace(control, **change)
    for index in (3, 4):
        key = list(control.operation_key)
        key[index] = "k" * 129
        with pytest.raises(ValueError, match="invalid_control_text"):
            replace(control, operation_key=tuple(key))
    assert runtime.store.show_operation(request_for(runtime, run))["receipt"] is None
    assert (
        runtime.store.load_runtime_state(runtime.cas_store).run_execution_controls[
            run.run_ref.run_id
        ]
        == control
    )
    runtime.close()


@pytest.mark.parametrize("session", [False, True])
def test_exact_pause_resume_reopen_replay_preserves_authority_and_unbound_budget(
    tmp_path, session
):
    runtime, run = runtime_with_run(tmp_path, session=session)
    epoch = epoch_for(runtime, run, max_invocations=1)
    runtime.store._stop_daemon_budget_epoch(
        epoch.budget_id,
        observed_at=101,
        status="exhausted",
        reason="invocation_limit_exhausted",
    )
    before = runtime.store.load_runtime_state(runtime.cas_store)
    budget = runtime.store.load_daemon_budget_epoch(epoch.budget_id)
    request = request_for(runtime, run)
    first = runtime.store.execute_run_control(
        request,
        runtime.cas_store,
        supported_adapter_kinds=frozenset({"codex", "millforge"}),
    )
    receipt = first["receipt"]
    assert (
        receipt["accepted"],
        receipt["before_control_revision"],
        receipt["after_control_revision"],
    ) == (True, 0, 1)
    assert "private reason" not in json.dumps(first)
    runtime.store.close()
    runtime = replace(runtime, store=SQLiteRuntimeStore.open(runtime.paths.db_path))
    held = runtime.store.load_runtime_state(runtime.cas_store)
    assert held.run_execution_controls[run.run_ref.run_id].state == "paused"
    assert replace(held, run_execution_controls={}) == before
    resume = request_for(
        runtime, run, action="runs.resume", pause_id=receipt["pause_id"]
    )
    released = runtime.store.execute_run_control(
        resume,
        runtime.cas_store,
        supported_adapter_kinds=frozenset({"codex", "millforge"}),
    )
    assert released["receipt"]["after_execution_state"] == "runnable_unstarted"
    assert runtime.store.load_daemon_budget_epoch(epoch.budget_id) == budget
    assert (
        runtime.store.daemon_budget_id_for_session(run.current_session_id or "absent")
        is None
    )
    assert (
        runtime.store.execute_run_control(
            request,
            runtime.cas_store,
            supported_adapter_kinds=frozenset({"codex", "millforge"}),
        )["receipt"]
        == receipt
    )
    assert runtime.store.execute_run_control(
        resume,
        runtime.cas_store,
        supported_adapter_kinds=frozenset({"codex", "millforge"}),
    )["replayed"]
    assert runtime.store.load_runtime_state(runtime.cas_store).runs == before.runs
    runtime.close()


def test_new_keys_never_nest_reuse_or_release_wrong_hold(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    first = pause(runtime, run)["receipt"]
    assert pause(runtime, run)["receipt"]["reason_code"] == "already_paused"
    wrong = request_for(runtime, run, action="runs.resume", pause_id=str(uuid4()))
    assert (
        runtime.store.execute_run_control(
            wrong,
            runtime.cas_store,
            supported_adapter_kinds=frozenset({"codex", "millforge"}),
        )["receipt"]["reason_code"]
        == "pause_id_mismatch"
    )
    resume = request_for(runtime, run, action="runs.resume", pause_id=first["pause_id"])
    runtime.store.execute_run_control(
        resume,
        runtime.cas_store,
        supported_adapter_kinds=frozenset({"codex", "millforge"}),
    )
    old = request_for(runtime, run, action="runs.resume", pause_id=first["pause_id"])
    assert (
        runtime.store.execute_run_control(
            old,
            runtime.cas_store,
            supported_adapter_kinds=frozenset({"codex", "millforge"}),
        )["receipt"]["reason_code"]
        == "pause_not_active"
    )
    assert pause(runtime, run)["receipt"]["pause_id"] != first["pause_id"]
    runtime.close()


@pytest.mark.parametrize(
    "field",
    [
        "workspace_id",
        "instance_id",
        "store_epoch",
        "run_id",
        "plan_fingerprint",
        "run_generation",
        "run_fencing_token",
        "expected_control_revision",
        "session_id",
        "dispatch_generation",
        "session_fencing_token",
        "state",
    ],
)
@pytest.mark.parametrize("action", ["runs.pause", "runs.resume"])
def test_all_exact_target_mismatches_refuse(tmp_path, field, action):
    runtime, run = runtime_with_run(tmp_path, session=True)
    first = pause(runtime, run)["receipt"] if action == "runs.resume" else None
    request = request_for(
        runtime,
        run,
        **({"action": action, "pause_id": first["pause_id"]} if first else {}),
    )
    payload = request.payload
    target = payload["target"]
    if field in {"workspace_id", "instance_id", "store_epoch"}:
        target[field] = str(uuid4())
    elif field in {
        "session_id",
        "session_fencing_token",
        "state",
        "dispatch_generation",
    }:
        target["expected_session"][field] = {
            "state": "running",
            "dispatch_generation": 2,
        }.get(field, "wrong")
        if field == "dispatch_generation":
            target["last_dispatch_generation"] = 2
    else:
        target[field] = {
            "plan_fingerprint": "sha256:" + "a" * 64,
            "run_generation": 99,
            "expected_control_revision": 99,
        }.get(field, "wrong")
    changed = ControlRequest.parse(json.dumps(payload))
    before = runtime.store.load_runtime_state(runtime.cas_store).run_execution_controls
    if field in {"workspace_id", "instance_id", "store_epoch"}:
        with pytest.raises(StoreIdentityMismatch):
            runtime.store.execute_run_control(
                changed,
                runtime.cas_store,
                supported_adapter_kinds=frozenset({"codex", "millforge"}),
            )
    else:
        assert not runtime.store.execute_run_control(
            changed,
            runtime.cas_store,
            supported_adapter_kinds=frozenset({"codex", "millforge"}),
        )["receipt"]["accepted"]
    assert (
        runtime.store.load_runtime_state(runtime.cas_store).run_execution_controls
        == before
    )
    runtime.close()


@pytest.mark.parametrize("field", ["reason", "actor_id", "action", "target"])
def test_same_key_semantic_conflict(tmp_path, field):
    runtime, run = runtime_with_run(tmp_path)
    request = request_for(runtime, run)
    runtime.store.execute_run_control(
        request,
        runtime.cas_store,
        supported_adapter_kinds=frozenset({"codex", "millforge"}),
    )
    payload = request.payload
    if field == "action":
        payload.update(action="runs.resume", pause_id=str(uuid4()))
    elif field == "target":
        payload["target"]["run_id"] = "another-run"
    else:
        payload[field] = "changed"
    with pytest.raises(ControlOperationError, match="idempotency_conflict"):
        runtime.store.execute_run_control(
            ControlRequest.parse(json.dumps(payload)),
            runtime.cas_store,
            supported_adapter_kinds=frozenset({"codex", "millforge"}),
        )
    runtime.close()


def test_stale_snapshot_cannot_attach_create_start_or_erase_hold(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    stale = runtime.store.load_runtime_state(runtime.cas_store)
    create = CreateRunnerSession(
        "create",
        run_ref=run.run_ref,
        session_id="new-session",
        session_fencing_token="new-fence",
        created_at=100,
        explicit_retry_intent=False,
    )
    next_state = apply(
        stale,
        decide(
            stale, create, transition_context(command="test", input_id_value="create")
        ),
    )
    pause(runtime, run)
    for candidate in (stale, next_state):
        with pytest.raises(ControlOperationError, match="stale_runtime_controls"):
            runtime.store.persist_runtime_state(candidate, runtime.cas_store)
    assert _persist_transition(runtime, create) is None
    assert not runtime.store.load_runtime_state(runtime.cas_store).runner_sessions
    runtime.close()


@pytest.mark.parametrize("ceiling", ["wall", "invocations", "tokens"])
def test_explicit_exhausted_epoch_refuses_atomic_start_without_binding(
    tmp_path, ceiling
):
    runtime, run = runtime_with_run(tmp_path, session=True)
    changes = {
        "wall": dict(max_wall_seconds=1, wall_deadline=101),
        "invocations": dict(max_invocations=1),
        "tokens": dict(max_total_tokens=2),
    }[ceiling]
    epoch_for(runtime, run, **changes)
    if ceiling != "wall":
        other = add_run(runtime, "budget-consumer")
        consumed = start_intent(runtime, other, budget_id="budget-a")
        other_session = consumed.runner_sessions[other.current_session_id]
        runtime.store.record_budgeted_runner_start("budget-a", other_session)
        if ceiling == "tokens":
            from millrace.contracts.state import RunnerSessionUsageRecord

            runtime.store.record_runner_session_usage(
                RunnerSessionUsageRecord(
                    budget_id="budget-a",
                    session_id=other_session.session_id,
                    run_id=other_session.run_id,
                    dispatch_generation=other_session.dispatch_generation,
                    session_fencing_token=other_session.session_fencing_token,
                    input_tokens=2,
                    output_tokens=0,
                    total_tokens=2,
                    observed_at=101,
                    final=True,
                )
            )
    held = pause(runtime, run)["receipt"]
    resume = request_for(runtime, run, action="runs.resume", pause_id=held["pause_id"])
    released = runtime.store.execute_run_control(
        resume, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
    )["receipt"]
    before = runtime.store.load_runtime_state(runtime.cas_store)
    session = before.runner_sessions[run.current_session_id]
    advance = AdvanceRunnerSession(
        "start",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        next_state="starting",
        occurred_at=101,
    )
    with pytest.raises(ValueError, match="daemon_budget_.*exhausted"):
        _persist_transition(
            runtime,
            advance,
            driving_budget_id="budget-a",
            driving_budget_clock=lambda: 101,
        )
    assert runtime.store.load_runtime_state(runtime.cas_store) == before
    assert runtime.store.daemon_budget_id_for_session(session.session_id) is None
    assert (
        runtime.store.execute_run_control(
            resume, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
        )["receipt"]
        == released
    )
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from support.runner_sessions import _config, _RecordingAdapter, _success_start

    adapter = _RecordingAdapter(_success_start)
    result = run_bounded_execution_unit(
        runtime,
        activation_id=run.activation_id,
        local_config=_config(adapter),
        driving_budget_id="budget-a",
        driving_budget_clock=lambda: 101,
    )
    assert result.code.startswith("daemon_budget_") and result.code.endswith(
        "exhausted"
    )
    assert not adapter.requests
    assert runtime.store.daemon_budget_id_for_session(session.session_id) is None
    runtime.close()


def test_intent_reservation_rollback_and_exactly_once_accounting_recovery(
    tmp_path, monkeypatch
):
    runtime, run = runtime_with_run(tmp_path, session=True)
    epoch_for(runtime, run)
    session = runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
        run.current_session_id
    ]
    advance = AdvanceRunnerSession(
        "start",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        next_state="starting",
        occurred_at=101,
    )
    original = runtime.store.reserve_budgeted_runner_start

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("before commit")

    monkeypatch.setattr(runtime.store, "reserve_budgeted_runner_start", crash)
    with pytest.raises(RuntimeError, match="before commit"):
        _persist_transition(
            runtime,
            advance,
            driving_budget_id="budget-a",
            driving_budget_clock=lambda: 101,
        )
    assert runtime.store.daemon_budget_id_for_session(session.session_id) is None
    assert (
        runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
            session.session_id
        ]
        == session
    )
    monkeypatch.setattr(runtime.store, "reserve_budgeted_runner_start", original)
    started = _persist_transition(
        runtime, advance, driving_budget_id="budget-a", driving_budget_clock=lambda: 101
    )
    assert started.runner_sessions[session.session_id].state == "starting"
    assert runtime.store.pending_budgeted_runner_start_session_ids("budget-a") == (
        session.session_id,
    )
    assert not pause(runtime, run)["receipt"]["accepted"]
    runtime.close()
    runtime = replace(runtime, store=SQLiteRuntimeStore.open(runtime.paths.db_path))
    for _ in range(2):
        runtime.store.record_budgeted_runner_start(
            "budget-a", started.runner_sessions[session.session_id]
        )
    assert runtime.store.load_daemon_budget_epoch("budget-a").accepted_start_count == 1
    runtime.close()


def test_pending_reservation_requires_explicit_driving_epoch_at_start(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    epoch = epoch_for(runtime, run)
    session = runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
        run.current_session_id
    ]
    runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    with pytest.raises(ValueError, match="runner_start_budget_required"):
        start_intent(runtime, run)
    assert (
        runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
            session.session_id
        ]
        == session
    )
    assert start_intent(runtime, run, budget_id=epoch.budget_id) is not None
    runtime.close()


def test_unknown_selected_adapter_capability_refuses_before_hold(tmp_path):
    runtime, run = runtime_with_run(tmp_path)
    request = request_for(runtime, run)
    result = runtime.store.execute_run_control(request, runtime.cas_store)
    assert result["receipt"]["reason_code"] == "runner_pause_unsupported"
    assert not result["receipt"]["accepted"]
    assert not runtime.store.load_runtime_state(
        runtime.cas_store
    ).run_execution_controls
    runtime.close()


def _repair1_corruption(connection, trigger, statement, parameters=()):
    """Build existing damaged storage, then restore the exact schema trigger."""
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,)
    ).fetchone()[0]
    connection.execute(f'DROP TRIGGER "{trigger}"')
    connection.execute(statement, parameters)
    connection.execute(sql)
    connection.commit()


def _repair1_durable_rows(connection):
    return tuple(
        (table, tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1")))
        for table in (
            "control_identity",
            "run_execution_controls",
            "run_control_events",
            "control_operations",
            "control_results",
            "runner_sessions",
            "daemon_budget_sessions",
        )
    )


def test_repair1_missing_current_refuses_load_and_start_without_repair(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    pause(runtime, run)
    session = runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
        run.current_session_id
    ]
    connection = runtime.store._connection
    _repair1_corruption(
        connection,
        "run_execution_control_retained",
        "DELETE FROM run_execution_controls WHERE run_id=?",
        (run.run_ref.run_id,),
    )
    before = _repair1_durable_rows(connection)
    runtime.store.close()
    runtime = replace(runtime, store=SQLiteRuntimeStore.open(runtime.paths.db_path))
    epoch = epoch_for(runtime, run)
    before = _repair1_durable_rows(runtime.store._connection)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        runtime.store.load_runtime_state(runtime.cas_store)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        start_intent(runtime, run)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    assert _repair1_durable_rows(runtime.store._connection) == before
    runtime.close()


@pytest.mark.parametrize(
    "damage",
    [
        "foreign_scope",
        "sql_revision",
        "revision_gap",
        "nonmonotonic_source",
        "future_source",
        "missing_receipt",
        "reuse_pause_receipt",
        "unknown_without_evidence",
        "superseded_without_evidence",
        "reused_pause_id",
        "sql_run_id",
    ],
)
def test_repair1_matching_current_and_event_cannot_forge_authority(tmp_path, damage):
    runtime, run = runtime_with_run(tmp_path, session=True)
    pause(runtime, run)
    session = runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
        run.current_session_id
    ]
    epoch = epoch_for(runtime, run)
    connection = runtime.store._connection
    value = json.loads(
        connection.execute("SELECT record_json FROM run_execution_controls").fetchone()[
            0
        ]
    )
    connection.execute("UPDATE control_identity SET source_revision=source_revision+10")
    maximum = connection.execute(
        "SELECT source_revision FROM control_identity"
    ).fetchone()[0]
    value.update(
        state="resumed",
        cause="run_resumed",
        control_revision=2,
        source_revision=maximum,
    )
    sql_revision, sql_run = 2, run.run_ref.run_id
    if damage == "foreign_scope":
        value["operation_key"][0] = str(uuid4())
    elif damage == "sql_revision":
        sql_revision = 300
    elif damage == "revision_gap":
        value["control_revision"] = sql_revision = 99
    elif damage == "nonmonotonic_source":
        value["source_revision"] = 1
    elif damage == "future_source":
        value["source_revision"] = maximum + 1
    elif damage == "missing_receipt":
        value["operation_key"][-1] = str(uuid4())
    elif damage == "unknown_without_evidence":
        value.update(state="unknown", cause="run_aftermath_unknown")
    elif damage == "superseded_without_evidence":
        value.update(state="superseded", cause="run_work_closed")
    elif damage == "reused_pause_id":
        value.update(state="paused", cause="run_paused")
    elif damage == "sql_run_id":
        sql_run = "other-run"
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    connection.execute("UPDATE run_execution_controls SET record_json=?", (raw,))
    connection.execute(
        "INSERT INTO run_control_events VALUES (?, ?, ?)", (sql_run, sql_revision, raw)
    )
    connection.commit()
    before = _repair1_durable_rows(connection)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        runtime.store.load_runtime_state(runtime.cas_store)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        start_intent(runtime, run)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
    assert _repair1_durable_rows(connection) == before
    runtime.close()


@pytest.mark.parametrize(
    "damage",
    [
        "old_event",
        "initial_result_missing",
        "initial_result_stage",
        "receipt_revision",
        "receipt_target",
        "receipt_scope",
    ],
)
def test_repair1_valid_latest_requires_complete_receipt_and_result_history(
    tmp_path, damage
):
    runtime, run = runtime_with_run(tmp_path, session=True)
    first = pause(runtime, run)["receipt"]
    request = request_for(
        runtime, run, action="runs.resume", pause_id=first["pause_id"]
    )
    assert runtime.store.execute_run_control(
        request, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
    )["receipt"]["accepted"]
    connection = runtime.store._connection
    if damage == "old_event":
        value = json.loads(
            connection.execute(
                "SELECT record_json FROM run_control_events WHERE control_revision=1"
            ).fetchone()[0]
        )
        value["cause"] = "forged_old_cause"
        _repair1_corruption(
            connection,
            "control_immutable_run_control_events_update",
            "UPDATE run_control_events SET record_json=? WHERE control_revision=1",
            (json.dumps(value, sort_keys=True, separators=(",", ":")),),
        )
    elif damage.startswith("initial_result"):
        if damage == "initial_result_missing":
            _repair1_corruption(
                connection,
                "control_immutable_control_results_delete",
                "DELETE FROM control_results WHERE operation_id=?",
                (request.key[-1],),
            )
        else:
            value = json.loads(
                connection.execute(
                    "SELECT result_json FROM control_results WHERE operation_id=?",
                    (request.key[-1],),
                ).fetchone()[0]
            )
            value["stage"] = "rejected_no_effect"
            _repair1_corruption(
                connection,
                "control_immutable_control_results_update",
                "UPDATE control_results SET stage=?, result_json=? "
                "WHERE operation_id=?",
                (
                    value["stage"],
                    json.dumps(value, sort_keys=True, separators=(",", ":")),
                    request.key[-1],
                ),
            )
    else:
        value = json.loads(
            connection.execute(
                "SELECT receipt_json FROM control_operations WHERE operation_id=?",
                (request.key[-1],),
            ).fetchone()[0]
        )
        if damage == "receipt_revision":
            value["after_control_revision"] = 300
        elif damage == "receipt_target":
            value["target"]["run_fencing_token"] = "another-fence"
        else:
            value["key"]["store_epoch"] = str(uuid4())
        _repair1_corruption(
            connection,
            "control_immutable_control_operations_update",
            "UPDATE control_operations SET receipt_json=? WHERE operation_id=?",
            (json.dumps(value, sort_keys=True, separators=(",", ":")), request.key[-1]),
        )
    before = _repair1_durable_rows(connection)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        runtime.store.load_runtime_state(runtime.cas_store)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        start_intent(runtime, run)
    assert _repair1_durable_rows(connection) == before
    runtime.close()


def test_repair1_repeated_cycles_keep_fresh_ids_and_historical_attempt_authority(
    tmp_path,
):
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from support.runner_sessions import _config, _RecordingAdapter, _refused_start

    runtime, run = runtime_with_run(tmp_path, session=True)
    ids = set()
    for _ in range(3):
        held = pause(runtime, run)["receipt"]
        assert held["pause_id"] not in ids
        ids.add(held["pause_id"])
        release = request_for(
            runtime, run, action="runs.resume", pause_id=held["pause_id"]
        )
        assert runtime.store.execute_run_control(
            release, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
        )["receipt"]["accepted"]
    result = run_bounded_execution_unit(
        runtime,
        activation_id=run.activation_id,
        local_config=_config(_RecordingAdapter(_refused_start)),
    )
    assert result.code == "adapter_failure"
    state = runtime.store.load_runtime_state(runtime.cas_store)
    prior = state.runner_sessions[run.current_session_id]
    retry = CreateRunnerSession(
        "later-retry",
        run_ref=run.run_ref,
        session_id="new-attempt",
        session_fencing_token="new-fence",
        created_at=prior.ended_at + 1,
        explicit_retry_intent=True,
    )
    assert _persist_transition(runtime, retry) is not None
    assert pause(runtime, run)["receipt"]["accepted"]
    runtime.close()
    runtime = replace(runtime, store=SQLiteRuntimeStore.open(runtime.paths.db_path))
    after = runtime.store.load_runtime_state(runtime.cas_store)
    assert after.run_execution_controls[run.run_ref.run_id].control_revision == 7
    assert after.runs[run.run_ref.run_id].current_session_id == "new-attempt"
    runtime.close()


@pytest.mark.parametrize("damage", ["oversized_item", "too_many_events"])
def test_repair1_unverifiable_history_bound_refuses_without_discarding_rows(
    tmp_path, damage
):
    runtime, run = runtime_with_run(tmp_path, session=True)
    pause(runtime, run)
    connection = runtime.store._connection
    if damage == "oversized_item":
        connection.execute(
            "UPDATE run_execution_controls SET record_json=?", (" " * 16385,)
        )
    else:
        connection.executemany(
            "INSERT INTO run_control_events VALUES (?, ?, ?)",
            ((f"extra-{index}", 1, "{}") for index in range(10001)),
        )
    connection.commit()
    before = _repair1_durable_rows(connection)
    with pytest.raises(
        ControlOperationError, match="run_control_history_bound_exceeded"
    ):
        runtime.store.load_runtime_state(runtime.cas_store)
    with pytest.raises(
        ControlOperationError, match="run_control_history_bound_exceeded"
    ):
        start_intent(runtime, run)
    assert _repair1_durable_rows(connection) == before
    runtime.close()


def test_repair1_event_source_must_link_exactly_to_its_receipt(tmp_path):
    runtime, run = runtime_with_run(tmp_path, session=True)
    held = pause(runtime, run)["receipt"]
    epoch_for(runtime, run)
    release = request_for(runtime, run, action="runs.resume", pause_id=held["pause_id"])
    runtime.store.execute_run_control(
        release, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
    )
    connection = runtime.store._connection
    value = json.loads(
        connection.execute("SELECT record_json FROM run_execution_controls").fetchone()[
            0
        ]
    )
    value["source_revision"] -= 1
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    _repair1_corruption(
        connection,
        "control_immutable_run_control_events_update",
        "UPDATE run_control_events SET record_json=? WHERE control_revision=2",
        (raw,),
    )
    connection.execute("UPDATE run_execution_controls SET record_json=?", (raw,))
    connection.commit()
    before = _repair1_durable_rows(connection)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        runtime.store.load_runtime_state(runtime.cas_store)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        start_intent(runtime, run)
    assert _repair1_durable_rows(connection) == before
    runtime.close()


def test_repair1_nontext_current_json_refuses_with_control_error(tmp_path):
    import sqlite3

    runtime, run = runtime_with_run(tmp_path, session=True)
    pause(runtime, run)
    connection = runtime.store._connection
    raw = connection.execute(
        "SELECT record_json FROM run_execution_controls"
    ).fetchone()[0]
    connection.execute(
        "UPDATE run_execution_controls SET record_json=?",
        (sqlite3.Binary(raw.encode()),),
    )
    connection.commit()
    before = _repair1_durable_rows(connection)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        runtime.store.load_runtime_state(runtime.cas_store)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        start_intent(runtime, run)
    assert _repair1_durable_rows(connection) == before
    runtime.close()


def test_repair1_admission_revalidates_control_history_after_staging(
    tmp_path, monkeypatch
):
    runtime, run = runtime_with_run(tmp_path, session=True)
    held = pause(runtime, run)["receipt"]
    request = request_for(runtime, run, action="runs.resume", pause_id=held["pause_id"])
    original = runtime.store.load_runtime_state
    connection = runtime.store._connection
    damaged = []

    def corrupt_after_load(cas, **kwargs):
        state = original(cas, **kwargs)
        value = json.loads(
            connection.execute(
                "SELECT record_json FROM run_execution_controls"
            ).fetchone()[0]
        )
        value.update(state="resumed", cause="run_resumed")
        connection.execute(
            "UPDATE run_execution_controls SET record_json=?",
            (json.dumps(value, sort_keys=True, separators=(",", ":")),),
        )
        connection.commit()
        damaged.append(_repair1_durable_rows(connection))
        return state

    monkeypatch.setattr(runtime.store, "load_runtime_state", corrupt_after_load)
    with pytest.raises(
        ControlOperationError, match="run_control_storage_contradiction"
    ):
        runtime.store.execute_run_control(
            request, runtime.cas_store, supported_adapter_kinds=frozenset({"codex"})
        )
    assert runtime.store.show_operation(request)["receipt"] is None
    assert _repair1_durable_rows(connection) == damaged[0]
    runtime.close()
