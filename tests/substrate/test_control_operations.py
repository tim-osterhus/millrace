from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from millrace.contracts.controls import (
    CONTROL_MAX_BYTES,
    ControlRequest,
    InvalidControlRequest,
)
from millrace.kernel import empty_runtime_state
from millrace.substrate._sqlite_controls import ControlDecision
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import ControlOperationError, StoreIdentityMismatch
from millrace.substrate.sqlite import SQLiteRuntimeStore


def request_for(store: SQLiteRuntimeStore, **changes: object) -> ControlRequest:
    identity = store.control_identity()
    payload = {
        "contract_id": "millrace.core.controls",
        "contract_revision": 1,
        "action": "runs.pause",
        "caller_id": "caller",
        "actor_id": "operator",
        "operation_id": "operation",
        "reason": "secret reason",
        "correlation_id": "correlation",
        "target": {
            **{
                key: identity[key]
                for key in ("workspace_id", "instance_id", "store_epoch")
            },
            "run_id": "run-a",
            "plan_fingerprint": "sha256:" + "a" * 64,
            "run_generation": 0,
            "run_fencing_token": "fence-a",
            "expected_control_revision": 0,
            "expected_session": None,
            "last_dispatch_generation": 0,
        },
    }
    payload.update(changes)
    return ControlRequest.parse(json.dumps(payload))


def public_runtime_target(store: SQLiteRuntimeStore) -> dict:
    # Exact run/session references from CORE-I01F's installed public queue/daemon
    # N01/n01aa1 request; kept inline so tests do not require its disposable files.
    target = request_for(store).payload["target"]
    prefix = (
        "cli:run.bounded:cli:run.bounded:claim:cli:queue.enqueue:native-enqueue:"
        "sha256:044584f5894418e485fbb907bd8363a201273a61986d00e7f876079279bc5187"
        ":activation"
    )
    target.update(
        run_id=prefix + ":run",
        run_fencing_token=prefix + ":fence",
        expected_session={
            "session_id": "session-31c84e23c1514dc0a5e9479c4710e4d3",
            "session_fencing_token": "session-fence-8e8dc09ddde444d281c03fe2ad66dfdc",
            "dispatch_generation": 1,
            "state": "running",
        },
        last_dispatch_generation=1,
    )
    return target


@pytest.mark.parametrize("long_sessions", [False, True])
def test_runtime_references_survive_durable_history(
    tmp_path: Path, long_sessions: bool
) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    target = public_runtime_target(store)
    assert len(target["run_id"].encode("utf-8")) == 157
    assert len(target["run_fencing_token"].encode("utf-8")) == 159
    if long_sessions:
        # RunnerSessionRecord permits 4096 UTF-8 bytes per reference.
        target["expected_session"].update(
            session_id="s" * 4096, session_fencing_token="f" * 4096
        )
    value = request_for(store).payload
    value["target"] = target
    request = ControlRequest.parse(json.dumps(value, ensure_ascii=False))
    assert request.payload["target"] == target
    accepted = store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending")
    )
    assert accepted["receipt"]["target"] == target
    store.close()

    store = SQLiteRuntimeStore.open(path)
    store.append_operation_result(
        request, result_id="settled", stage="applied", evidence={}
    )
    store.close()
    store = SQLiteRuntimeStore.open(path)
    replay = store.execute_operation(request, lambda: pytest.fail("replayed effect"))
    assert replay["replayed"] is True
    assert replay["receipt"] == accepted["receipt"]
    assert [result["stage"] for result in replay["results"]] == ["pending", "applied"]
    for field in ("run_id", "run_fencing_token", "session_id", "session_fencing_token"):
        changed = request.payload
        reference = changed["target"]
        if field.startswith("session_"):
            reference = reference["expected_session"]
        reference[field] = reference[field][:-1] + "!"
        with pytest.raises(ControlOperationError, match="idempotency_conflict"):
            store.resolve_operation(
                ControlRequest.parse(json.dumps(changed, ensure_ascii=False))
            )
    store.close()


@pytest.mark.parametrize(
    "field", ["run_id", "run_fencing_token", "session_id", "session_fencing_token"]
)
@pytest.mark.parametrize("bad", [None, True, 1, [], {}, "", "  ", "x\n", "\ud800"])
def test_malformed_runtime_references_are_not_admitted(
    tmp_path: Path, field: str, bad: object
) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    value = request_for(store).payload
    value["target"] = public_runtime_target(store)
    reference = value["target"]
    if field.startswith("session_"):
        reference = reference["expected_session"]
    reference[field] = bad
    with pytest.raises(InvalidControlRequest):
        ControlRequest.parse(json.dumps(value))
    assert store.control_identity()["source_revision"] == 0
    store.close()


@pytest.mark.parametrize(
    "field,maximum",
    [
        ("operation_id", 128),
        ("actor_id", 128),
        ("caller_id", 128),
        ("correlation_id", 128),
        ("causation_id", 128),
        ("reason", 512),
    ],
)
def test_caller_fields_retain_utf8_byte_limits(
    tmp_path: Path, field: str, maximum: int
) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    for unit in ("x", "é"):
        value = request_for(store).payload
        value["target"] = public_runtime_target(store)
        value[field] = unit * (maximum // len(unit.encode("utf-8")))
        assert ControlRequest.parse(json.dumps(value)).payload[field] == value[field]
        value[field] += unit
        with pytest.raises(InvalidControlRequest):
            ControlRequest.parse(json.dumps(value))
    store.close()


def test_runtime_references_retain_aggregate_utf8_byte_bound(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    value = request_for(store).payload
    value["target"] = public_runtime_target(store)
    value["target"]["run_id"] = ""
    empty = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    available = CONTROL_MAX_BYTES - len(empty.encode("utf-8"))
    value["target"]["run_id"] = "é" * (available // 2) + "x" * (available % 2)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(raw.encode("utf-8")) == CONTROL_MAX_BYTES
    request = ControlRequest.parse(raw)
    assert request.payload["target"] == value["target"]
    # One byte beyond the aggregate cap refuses although each reference fits it.
    value["target"]["run_fencing_token"] += "x"
    with pytest.raises(InvalidControlRequest):
        ControlRequest.parse(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )
    # Equivalent escaped Unicode still obeys the raw transport byte bound.
    with pytest.raises(InvalidControlRequest):
        ControlRequest.parse(json.dumps(request.payload, ensure_ascii=True))
    assert store.control_identity()["source_revision"] == 0
    store.close()


def test_runtime_session_references_preserve_exact_unicode(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    value = request_for(store).payload
    value["target"] = public_runtime_target(store)
    session = value["target"]["expected_session"]
    session.update(session_id="é" * 2048, session_fencing_token="雪" * 1365 + "x")
    assert len(session["session_id"].encode("utf-8")) == 4096
    assert len(session["session_fencing_token"].encode("utf-8")) == 4096
    request = ControlRequest.parse(json.dumps(value, ensure_ascii=False))
    assert request.payload["target"] == value["target"]
    session["session_id"] = "e\u0301" + session["session_id"][1:]
    changed = ControlRequest.parse(json.dumps(value, ensure_ascii=False))
    assert changed.digest != request.digest
    assert changed.payload["target"] == value["target"]
    store.close()


def test_pending_restart_settlement_replay_and_aftermath(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    first = store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "native_pending")
    )
    assert first["receipt"]["accepted"] is True
    assert first["results"][0]["stage"] == "pending"
    assert "secret reason" not in json.dumps(first)
    store.close()
    store = SQLiteRuntimeStore.open(path)
    assert store.resolve_operation(request)["receipt"] == first["receipt"]
    applied = store.append_operation_result(
        request, result_id="settled", stage="applied", evidence={"owner_id": "owner"}
    )
    assert (
        store.append_operation_result(
            request,
            result_id="settled",
            stage="applied",
            evidence={"owner_id": "owner"},
        )
        == applied
    )
    with pytest.raises(ControlOperationError, match="already_settled"):
        store.append_operation_result(
            request, result_id="duplicate", stage="applied", evidence={}
        )
    store.append_operation_result(
        request,
        result_id="owner-death",
        stage="aftermath",
        evidence={"effect_status": "unknown_effects"},
    )
    replay = store.execute_operation(request, lambda: pytest.fail("replayed effect"))
    assert replay["replayed"] is True
    assert replay["receipt"] == first["receipt"]
    assert [result["stage"] for result in replay["results"]] == [
        "pending",
        "applied",
        "aftermath",
    ]
    assert (
        replay["observation"]["source_revision"] > first["receipt"]["source_revision"]
    )
    store.close()


@pytest.mark.parametrize("disposition", ["rejected_no_effect", "sealed_not_accepted"])
def test_rejected_and_sealed_keys_never_execute(
    tmp_path: Path, disposition: str
) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    request = request_for(store)
    first = store.execute_operation(
        request, lambda: ControlDecision(disposition, "refused")
    )
    assert (
        store.execute_operation(request, lambda: pytest.fail("effect"))["receipt"]
        == first["receipt"]
    )
    with pytest.raises(ControlOperationError, match="idempotency_conflict"):
        store.resolve_operation(request_for(store, reason="changed"))
    other = store.execute_operation(
        request_for(store, operation_id="fresh"),
        lambda: ControlDecision("accepted_pending", "pending"),
    )
    assert other["receipt"]["accepted"] is True
    store.close()


@pytest.mark.parametrize("winner", ["accept", "seal"])
def test_pending_seal_race_serializes_before_authority_read(
    tmp_path: Path, winner: str
) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    store.close()
    admitted = threading.Event()
    release = threading.Event()

    def first() -> dict:
        owner = SQLiteRuntimeStore.open(path)

        def decide() -> ControlDecision:
            admitted.set()
            assert release.wait(2)
            return ControlDecision(
                "accepted_pending" if winner == "accept" else "sealed_not_accepted",
                "test",
            )

        try:
            return owner.execute_operation(request, decide)
        finally:
            owner.close()

    def second() -> dict:
        owner = SQLiteRuntimeStore.open(path)
        try:
            return (
                owner.resolve_operation(request)
                if winner == "accept"
                else owner.execute_operation(
                    request, lambda: pytest.fail("sealed effect")
                )
            )
        finally:
            owner.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        assert admitted.wait(2)
        b = pool.submit(second)
        release.set()
        x, y = a.result(), b.result()
    assert x["receipt"] == y["receipt"]
    assert y["replayed"] is True


def test_precommit_failure_rolls_back_receipt_revision_and_runtime(
    tmp_path: Path,
) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    request = request_for(store)
    revision = store.control_identity()["source_revision"]

    def fail() -> ControlDecision:
        store._connection.execute(
            "INSERT INTO input_receipts VALUES ('i', 'd', 't', 1, NULL, 0)"
        )
        raise RuntimeError("injected before commit")

    with pytest.raises(RuntimeError, match="injected"):
        store.execute_operation(request, fail)
    assert store.control_identity()["source_revision"] == revision
    assert store._connection.execute(
        "SELECT COUNT(*) FROM input_receipts"
    ).fetchone() == (0,)
    assert store.show_operation(request)["nonacceptance_proven"] is False
    assert (
        store.resolve_operation(request)["receipt"]["disposition"]
        == "sealed_not_accepted"
    )
    store.close()


def test_receipt_insertion_failure_rolls_back_decision(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    store._connection.execute(
        "CREATE TEMP TRIGGER fail_receipt BEFORE INSERT ON control_operations "
        "BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    request = request_for(store)

    def decide() -> ControlDecision:
        store._connection.execute(
            "INSERT INTO input_receipts VALUES ('i', 'd', 't', 1, NULL, 0)"
        )
        return ControlDecision("accepted_pending", "pending")

    with pytest.raises(ControlOperationError, match="storage_unknown"):
        store.execute_operation(request, decide)
    assert store.control_identity()["source_revision"] == 0
    assert store._connection.execute(
        "SELECT COUNT(*) FROM input_receipts"
    ).fetchone() == (0,)
    assert store.show_operation(request)["receipt"] is None
    store.close()


def test_snapshot_preserves_operations_and_reads_do_not_mutate(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    cas = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    first = store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending")
    )
    store.persist_runtime_state(empty_runtime_state(), cas)
    assert store.show_operation(request)["receipt"] == first["receipt"]
    before = path.read_bytes()
    identity = store.control_identity()
    store.load_runtime_state(cas)
    store.show_operation(request)
    assert store.control_identity() == identity
    assert path.read_bytes() == before
    store.close()


@pytest.mark.parametrize("change", ["clone", "workspace", "cas", "epoch"])
def test_location_and_epoch_refuse_without_adoption(
    tmp_path: Path, change: str
) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    store.close()
    before = path.read_bytes()
    with pytest.raises(StoreIdentityMismatch):
        if change == "clone":
            clone = tmp_path / "clone.db"
            shutil.copyfile(path, clone)
            SQLiteRuntimeStore.open(clone)
        elif change == "epoch":
            store = SQLiteRuntimeStore.open(path)
            value = request.payload
            value["target"]["store_epoch"] = str(uuid4())
            try:
                store.resolve_operation(ControlRequest.parse(json.dumps(value)))
            finally:
                store.close()
        else:
            SQLiteRuntimeStore.open(path, **{f"{change}_path": tmp_path / "other"})
    assert path.read_bytes() == before


def test_identity_and_history_immutable_in_sqlite(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    store.resolve_operation(request_for(store))
    for statement in (
        "DELETE FROM control_operations",
        "UPDATE control_results SET stage='applied'",
        "DELETE FROM control_identity",
        "UPDATE control_identity SET store_epoch='other'",
        "UPDATE control_identity SET source_revision=0",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute(statement)
        store._connection.rollback()
    store.close()


def test_lock_contention_is_bounded_and_acceptance_stays_unknown(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    blocker = sqlite3.connect(path)
    blocker.execute("BEGIN IMMEDIATE")
    start = time.monotonic()
    with pytest.raises(ControlOperationError, match="storage_unknown"):
        store.resolve_operation(request)
    assert time.monotonic() - start < 2
    blocker.rollback()
    blocker.close()
    assert store.show_operation(request)["receipt"] is None
    store.close()


def test_unicode_canonical_semantics_and_supplied_digest(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    request = request_for(store, reason="café 雪")
    reordered = dict(reversed(list(request.payload.items())))
    assert (
        ControlRequest.parse(json.dumps(reordered, ensure_ascii=True, indent=4)).digest
        == request.digest
    )
    reordered["request_digest"] = request.digest
    assert ControlRequest.parse(json.dumps(reordered)) == request
    assert request_for(store, reason="cafe\u0301 雪").digest != request.digest
    store.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"unknown": 1},
        {"reason": None},
        {"reason": "é" * 257},
        {"caller_id": "x" * 129},
        {"contract_revision": True},
        {"contract_revision": 1.0},
        {"reason": "\ud800"},
        {"request_digest": "f" * 64},
        {"profile": {"unqualified": True}},
    ],
)
def test_invalid_requests_refused_before_admission(tmp_path: Path, bad: dict) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    value = request_for(store).payload
    value.update(bad)
    with pytest.raises(InvalidControlRequest):
        ControlRequest.parse(json.dumps(value))
    assert store.control_identity()["source_revision"] == 0
    store.close()


@pytest.mark.parametrize(
    "raw",
    ['{"a":1,"a":2}', '{"a":NaN}', '{"a":1.2}', "[" * 1100, " " * 16385],
    ids=["duplicate", "nonfinite", "float", "deep", "oversized"],
)
def test_malformed_duplicate_float_and_oversize_json(raw: str) -> None:
    with pytest.raises(InvalidControlRequest):
        ControlRequest.parse(raw)


@pytest.mark.parametrize("schema", [8, 9, 10, 12])
@pytest.mark.parametrize("method", ["open", "initialize"])
def test_old_and_future_store_refusal_is_nonmutating(
    tmp_path: Path, schema: int, method: str
) -> None:
    from millrace.substrate.errors import UnsupportedStoreSchemaVersion

    path = tmp_path / "store.db"
    SQLiteRuntimeStore.initialize(path).close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE store_metadata SET store_schema_version=?", (schema,)
        )
    before = path.read_bytes()
    with pytest.raises(UnsupportedStoreSchemaVersion):
        getattr(SQLiteRuntimeStore, method)(path)
    assert path.read_bytes() == before


def test_revisions_cover_real_runtime_budget_and_package_writes(tmp_path: Path) -> None:
    from tests.substrate.test_workflow_package_registry import (
        _archive_bytes,
        _import_archive,
    )

    from millrace.contracts.state import DaemonBudgetEpochRecord
    from millrace.testing import materialize_fake_runner_session_cas
    from substrate._runtime_store_support import worker_runtime_state

    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    cas = ContentAddressedByteStore(tmp_path / "cas")
    state = worker_runtime_state()
    assert state.default_plan_ref is not None
    state = materialize_fake_runner_session_cas(state=state, cas_store=cas)
    store.persist_runtime_state(state, cas)
    runtime_revision = store.control_identity()["source_revision"]
    assert runtime_revision > 0
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget",
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
    budget_revision = store.control_identity()["source_revision"]
    assert budget_revision > runtime_revision
    store.reserve_budgeted_runner_start(
        "budget", next(iter(state.runner_sessions.values()))
    )
    reservation_revision = store.control_identity()["source_revision"]
    assert reservation_revision > budget_revision
    _import_archive(
        store, cas, _archive_bytes(), actor_id="operator", source_uri="fixture:archive"
    )
    package_revision = store.control_identity()["source_revision"]
    assert package_revision > reservation_revision
    store.load_workflow_package_registry(cas)
    store.load_daemon_budget_epoch("budget")
    assert store.control_identity()["source_revision"] == package_revision
    store.close()


def test_large_retained_history_refuses_without_truncation(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    request = request_for(store)
    store.execute_operation(request, lambda: ControlDecision("applied", "applied"))
    for number in range(9):
        store.append_operation_result(
            request,
            result_id=f"after-{number}",
            stage="aftermath",
            evidence={"bounded": "x" * 15000},
        )
    with pytest.raises(ControlOperationError, match="too_large"):
        store.show_operation(request)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM control_results"
    ).fetchone() == (10,)
    store.close()


def test_corrupt_receipt_observation_is_unknown_and_nonmutating(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending")
    )
    # Deliberate corruption fixture; production mutations cannot rewrite history.
    store._connection.execute(
        "DROP TRIGGER control_immutable_control_operations_update"
    )
    store._connection.execute("UPDATE control_operations SET receipt_json='{}'")
    store._connection.commit()
    before = path.read_bytes()
    with pytest.raises(ControlOperationError, match="contradiction"):
        store.show_operation(request)
    assert path.read_bytes() == before
    store.close()


def test_aftermath_retention_continues_beyond_finite_read_limit(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    request = request_for(store)
    store.execute_operation(request, lambda: ControlDecision("applied", "applied"))
    for number in range(101):
        store.append_operation_result(
            request, result_id=f"after-{number}", stage="aftermath", evidence={}
        )
    with pytest.raises(ControlOperationError, match="too_large"):
        store.show_operation(request)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM control_results"
    ).fetchone() == (102,)
    store.close()


def test_show_overrides_long_busy_timeout_and_preserves_setting(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    store._connection.execute("PRAGMA busy_timeout=30000")
    blocker = sqlite3.connect(path)
    blocker.execute("BEGIN EXCLUSIVE")
    start = time.monotonic()
    with pytest.raises(ControlOperationError, match="storage_unknown"):
        store.show_operation(request)
    assert time.monotonic() - start < 2
    blocker.rollback()
    blocker.close()
    assert store._connection.execute("PRAGMA busy_timeout").fetchone() == (30000,)
    assert store.show_operation(request)["nonacceptance_proven"] is False
    store.close()


@pytest.mark.parametrize(
    "field,bad",
    [
        ("run_generation", True),
        ("expected_control_revision", -1),
        ("last_dispatch_generation", 1),
        ("workspace_id", "invalid"),
        ("plan_fingerprint", "sha256:" + "A" * 64),
    ],
)
def test_strict_target_identity_and_revisions(
    tmp_path: Path, field: str, bad: object
) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    payload = request_for(store).payload
    payload["target"][field] = bad
    with pytest.raises(InvalidControlRequest):
        ControlRequest.parse(json.dumps(payload))
    assert store.control_identity()["source_revision"] == 0
    store.close()


def test_unicode_result_wire_size_is_bounded(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    request = request_for(store)
    store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending")
    )
    store.append_operation_result(
        request, result_id="settled", stage="applied", evidence={"bounded": "雪" * 3000}
    )
    with pytest.raises(ControlOperationError, match="too_large"):
        store.show_operation(request)
    store.close()


def _corrupt_control_json(store: SQLiteRuntimeStore, table: str, change) -> None:
    """Corrupt an isolated fixture, then restore its exact immutability trigger."""
    column = "receipt_json" if table == "control_operations" else "result_json"
    trigger = f"control_immutable_{table}_update"
    sql = store._connection.execute(
        "SELECT sql FROM sqlite_master WHERE name=?", (trigger,)
    ).fetchone()[0]
    store._connection.execute(f"DROP TRIGGER {trigger}")
    rows = store._connection.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
    for rowid, encoded in rows:
        value = json.loads(encoded)
        change(value)
        store._connection.execute(
            f"UPDATE {table} SET {column}=? WHERE rowid=?", (json.dumps(value), rowid)
        )
    store._connection.execute(sql)
    store._connection.commit()


def _observe_or_settle(store: SQLiteRuntimeStore, request: ControlRequest, method: str):
    if method == "show":
        return store.show_operation(request)
    if method == "resolve":
        return store.resolve_operation(request)
    if method == "execute":
        return store.execute_operation(
            request, lambda: pytest.fail("replayed decision")
        )
    return store.append_operation_result(
        request, result_id="settlement", stage="applied", evidence={}
    )


@pytest.mark.parametrize("record", ["receipt", "result"])
@pytest.mark.parametrize(
    "method", ["show", "resolve", "execute", "settle", "result_replay"]
)
def test_history_revisions_must_fit_same_snapshot(
    tmp_path: Path, record: str, method: str
) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending")
    )
    if method == "result_replay":
        store.append_operation_result(
            request, result_id="settlement", stage="applied", evidence={}
        )
    if record == "receipt":
        _corrupt_control_json(
            store, "control_operations", lambda value: value.update(source_revision=999)
        )
    # Keep ordering coherent while isolating the invalid upper bound.
    _corrupt_control_json(
        store,
        "control_results",
        lambda value: value.update(source_revision=999 + value["result_revision"]),
    )
    store.close()
    store = SQLiteRuntimeStore.open(path)
    before = path.read_bytes()
    revision = store.control_identity()["source_revision"]
    with pytest.raises(ControlOperationError, match="control_storage_contradiction"):
        _observe_or_settle(store, request, method)
    assert store.control_identity()["source_revision"] == revision
    assert path.read_bytes() == before
    store.close()


@pytest.mark.parametrize(
    "original,retry",
    [
        ({"count": True}, {"count": 1}),
        ({"count": 1}, {"count": True}),
        ({"count": False}, {"count": 0}),
        ({"count": 0}, {"count": False}),
        ({"nested": [{"count": True}]}, {"nested": [{"count": 1}]}),
        ({"nested": [{"count": 1}]}, {"nested": [{"count": True}]}),
        ({"nested": {"count": [False]}}, {"nested": {"count": [0]}}),
        ({"nested": {"count": [0]}}, {"nested": {"count": [False]}}),
    ],
)
def test_result_replay_distinguishes_json_boolean_and_integer(
    tmp_path: Path, original: dict, retry: dict
) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending")
    )
    first = store.append_operation_result(
        request, result_id="settlement", stage="applied", evidence=original
    )
    before = path.read_bytes()
    with pytest.raises(ControlOperationError, match="operation_result_conflict"):
        store.append_operation_result(
            request, result_id="settlement", stage="applied", evidence=retry
        )
    assert path.read_bytes() == before
    assert store.show_operation(request)["results"][-1] == first
    assert store._connection.execute(
        "SELECT COUNT(*) FROM control_results"
    ).fetchone() == (2,)
    store.close()


def test_canonical_attribution_and_reordered_result_replay_remain_valid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(
        store,
        reason="café 雪",
        causation_id="cause",
        profile={
            "profile_id": "profile",
            "profile_digest": "b" * 64,
            "selected_adapter": "fixture",
            "native_state": "boundary",
            "effect_boundary": "none",
        },
    )
    first = store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending")
    )
    result = store.append_operation_result(
        request,
        result_id="settlement",
        stage="applied",
        evidence={"nested": {"a": True, "b": 1}, "other": None},
    )
    store.close()
    store = SQLiteRuntimeStore.open(path)
    before = path.read_bytes()
    assert (
        store.append_operation_result(
            request,
            result_id="settlement",
            stage="applied",
            evidence={"other": None, "nested": {"b": 1, "a": True}},
        )
        == result
    )
    replay = store.resolve_operation(request)
    assert replay["receipt"] == first["receipt"]
    assert replay["results"][-1] == result
    assert result["source_revision"] <= replay["observation"]["source_revision"]
    assert path.read_bytes() == before
    store.close()


@pytest.mark.parametrize(
    "field,bad",
    [
        ("actor_id", "wrong-actor"),
        ("caller_id", "wrong-caller"),
        ("correlation_id", "wrong-correlation"),
        ("causation_id", "wrong-cause"),
        ("profile", {"profile_id": "wrong-profile"}),
        ("reason_digest", "f" * 64),
        ("reason", "unredacted"),
        ("redaction_policy_id", "wrong-policy"),
        ("key", {}),
        ("action", "runs.resume"),
        ("accepted", False),
        ("disposition", "sealed_not_accepted"),
    ],
)
@pytest.mark.parametrize(
    "method", ["show", "resolve", "execute", "settle", "result_replay"]
)
def test_receipt_canonical_attribution_is_required_on_every_path(
    tmp_path: Path, field: str, bad: object, method: str
) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending")
    )
    if method == "result_replay":
        store.append_operation_result(
            request, result_id="settlement", stage="applied", evidence={}
        )
    _corrupt_control_json(
        store, "control_operations", lambda value: value.update({field: bad})
    )
    store.close()
    store = SQLiteRuntimeStore.open(path)
    before = path.read_bytes()
    with pytest.raises(ControlOperationError, match="control_storage_contradiction"):
        _observe_or_settle(store, request, method)
    assert path.read_bytes() == before
    store.close()


@pytest.mark.parametrize("method", ["show", "resolve", "execute", "settle"])
def test_contradictory_initial_stage_blocks_lookup_and_settlement(
    tmp_path: Path, method: str
) -> None:
    path = tmp_path / "store.db"
    store = SQLiteRuntimeStore.initialize(path)
    request = request_for(store)
    store.execute_operation(
        request, lambda: ControlDecision("accepted_pending", "pending")
    )
    _corrupt_control_json(
        store, "control_results", lambda value: value.update(stage="applied")
    )
    store.close()
    store = SQLiteRuntimeStore.open(path)
    before = path.read_bytes()
    with pytest.raises(ControlOperationError, match="control_storage_contradiction"):
        _observe_or_settle(store, request, method)
    assert path.read_bytes() == before
    store.close()


@pytest.mark.parametrize(
    "field,bad",
    [
        ("pause_id", "not-a-uuid"),
        ("pause_id", 1),
        ("reason_digest", "bad"),
        ("request_digest", "0" * 64),
        ("target", {}),
        ("caller_id", "other"),
    ],
)
def test_malformed_retained_receipt_refuses_without_mutation(tmp_path, field, bad):
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    try:
        request = request_for(store, action="runs.resume", pause_id=str(uuid4()))
        store.execute_operation(
            request, lambda: ControlDecision("rejected_no_effect", "pause_not_active")
        )
        _corrupt_control_json(
            store, "control_operations", lambda value: value.update({field: bad})
        )
        rows = tuple(store._connection.execute("SELECT * FROM control_operations"))
        results = tuple(store._connection.execute("SELECT * FROM control_results"))
        identity = store.control_identity()
        with pytest.raises(
            ControlOperationError, match="control_storage_contradiction"
        ):
            store.control_history_records(run_id="run-a")
        assert (
            tuple(store._connection.execute("SELECT * FROM control_operations")) == rows
        )
        assert (
            tuple(store._connection.execute("SELECT * FROM control_results")) == results
        )
        assert store.control_identity() == identity
    finally:
        store.close()


def test_accepted_resume_retained_receipt_requires_pause_authority(tmp_path):
    store = SQLiteRuntimeStore.initialize(tmp_path / "store.db")
    try:
        pause_id = str(uuid4())
        request = request_for(store, action="runs.resume", pause_id=pause_id)
        store.execute_operation(
            request, lambda: ControlDecision("applied", "resumed", pause_id=pause_id)
        )
        _corrupt_control_json(
            store, "control_operations", lambda value: value.update(pause_id=None)
        )
        rows = tuple(store._connection.execute("SELECT * FROM control_operations"))
        with pytest.raises(
            ControlOperationError, match="control_storage_contradiction"
        ):
            store.control_history_records(run_id="run-a")
        assert (
            tuple(store._connection.execute("SELECT * FROM control_operations")) == rows
        )
    finally:
        store.close()
