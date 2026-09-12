"""Durable control identity and serialized operation acceptance foundation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from millrace.contracts.controls import (
    ControlRequest,
    _validate_control_context,
    canonical_json,
    digest_field,
    revision_field,
    text_field,
    uuid_field,
)
from millrace.substrate.errors import ControlOperationError, StoreIdentityMismatch

CONTROL_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS daemon_sessions (daemon_id TEXT NOT NULL, "
    "sequence INTEGER NOT NULL, session_id TEXT NOT NULL, record_json TEXT NOT NULL, "
    "PRIMARY KEY(daemon_id, sequence), UNIQUE(daemon_id, session_id))",
    "CREATE TABLE IF NOT EXISTS daemon_session_results (daemon_id TEXT NOT NULL, "
    "sequence INTEGER NOT NULL, record_json TEXT NOT NULL, "
    "PRIMARY KEY(daemon_id, sequence))",
    "CREATE TABLE IF NOT EXISTS daemon_records (daemon_id TEXT PRIMARY KEY, "
    "record_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS daemon_events (daemon_id TEXT NOT NULL, revision "
    "INTEGER NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY (daemon_id, "
    "revision))",
    """CREATE TABLE IF NOT EXISTS run_execution_controls (
        run_id TEXT PRIMARY KEY, record_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS run_control_events (
        run_id TEXT NOT NULL, control_revision INTEGER NOT NULL,
        record_json TEXT NOT NULL, PRIMARY KEY (run_id, control_revision)
    )""",
    """CREATE TABLE IF NOT EXISTS control_identity (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        workspace_id TEXT NOT NULL, instance_id TEXT NOT NULL,
        store_epoch TEXT NOT NULL,
        workspace_path TEXT NOT NULL, db_path TEXT NOT NULL, cas_path TEXT NOT NULL,
        source_revision INTEGER NOT NULL CHECK (
            typeof(source_revision) = 'integer' AND source_revision >= 0
        )
    )""",
    """CREATE TABLE IF NOT EXISTS control_operations (
        workspace_id TEXT NOT NULL, instance_id TEXT NOT NULL,
        store_epoch TEXT NOT NULL,
        caller_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        request_digest TEXT NOT NULL, receipt_json TEXT NOT NULL,
        PRIMARY KEY (workspace_id, instance_id, store_epoch, caller_id, operation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS control_results (
        workspace_id TEXT NOT NULL, instance_id TEXT NOT NULL,
        store_epoch TEXT NOT NULL,
        caller_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        result_revision INTEGER NOT NULL CHECK (result_revision >= 1),
        result_id TEXT NOT NULL, stage TEXT NOT NULL, result_json TEXT NOT NULL,
        PRIMARY KEY (workspace_id, instance_id, store_epoch,
                     caller_id, operation_id, result_revision),
        UNIQUE (workspace_id, instance_id, store_epoch,
                caller_id, operation_id, result_id),
        FOREIGN KEY (workspace_id, instance_id, store_epoch, caller_id, operation_id)
            REFERENCES control_operations (workspace_id, instance_id, store_epoch,
                                           caller_id, operation_id)
    )""",
)
CONTROL_TABLE_COLUMNS = {
    "daemon_sessions": ("daemon_id", "sequence", "session_id", "record_json"),
    "daemon_session_results": ("daemon_id", "sequence", "record_json"),
    "daemon_records": ("daemon_id", "record_json"),
    "daemon_events": ("daemon_id", "revision", "record_json"),
    "run_execution_controls": ("run_id", "record_json"),
    "run_control_events": ("run_id", "control_revision", "record_json"),
    "control_identity": (
        "id",
        "workspace_id",
        "instance_id",
        "store_epoch",
        "workspace_path",
        "db_path",
        "cas_path",
        "source_revision",
    ),
    "control_operations": (
        "workspace_id",
        "instance_id",
        "store_epoch",
        "caller_id",
        "operation_id",
        "request_digest",
        "receipt_json",
    ),
    "control_results": (
        "workspace_id",
        "instance_id",
        "store_epoch",
        "caller_id",
        "operation_id",
        "result_revision",
        "result_id",
        "stage",
        "result_json",
    ),
}
_KEY_WHERE = (
    "workspace_id=? AND instance_id=? AND store_epoch=? "
    "AND caller_id=? AND operation_id=?"
)
_TERMINAL = {"applied", "rejected_no_effect", "lost_continuation", "unknown_effects"}


def control_triggers(tables: set[str]) -> dict[str, str]:
    statements = {}
    for table in sorted(tables - set(CONTROL_TABLE_COLUMNS) - {"store_metadata"}):
        for action in ("INSERT", "UPDATE", "DELETE"):
            name = f"control_revision_{table}_{action.lower()}"
            statements[name] = (
                f"CREATE TRIGGER {name} AFTER {action} ON {table} BEGIN "
                "UPDATE control_identity SET source_revision=source_revision+1 "
                "WHERE id=1; END"
            )
    for table in (
        "control_operations",
        "control_results",
        "run_control_events",
        "daemon_events",
        "daemon_sessions",
        "daemon_session_results",
    ):
        for action in ("UPDATE", "DELETE"):
            name = f"control_immutable_{table}_{action.lower()}"
            statements[name] = (
                f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN "
                "SELECT RAISE(ABORT, 'control_history_immutable'); END"
            )
    name = "daemon_record_retained"
    statements[name] = (
        f"CREATE TRIGGER {name} BEFORE DELETE ON daemon_records BEGIN "
        "SELECT RAISE(ABORT, 'daemon_record_retained'); END"
    )
    name = "run_execution_control_retained"
    statements[name] = (
        f"CREATE TRIGGER {name} BEFORE DELETE ON run_execution_controls BEGIN "
        "SELECT RAISE(ABORT, 'run_execution_control_retained'); END"
    )
    name = "control_identity_immutable"
    statements[name] = (
        f"CREATE TRIGGER {name} BEFORE UPDATE OF workspace_id, instance_id, "
        "store_epoch, "
        "workspace_path, db_path, cas_path ON control_identity BEGIN "
        "SELECT RAISE(ABORT, 'control_identity_immutable'); END"
    )
    name = "control_identity_retained"
    statements[name] = (
        f"CREATE TRIGGER {name} BEFORE DELETE ON control_identity BEGIN "
        "SELECT RAISE(ABORT, 'control_identity_immutable'); END"
    )
    name = "control_revision_monotonic"
    statements[name] = (
        f"CREATE TRIGGER {name} BEFORE UPDATE OF source_revision ON control_identity "
        "WHEN NEW.source_revision <= OLD.source_revision BEGIN "
        "SELECT RAISE(ABORT, 'control_revision_not_monotonic'); END"
    )
    return statements


def location_paths(
    path: str | Path, workspace_path: str | Path | None, cas_path: str | Path | None
) -> tuple[str, str, str]:
    db = Path(path).resolve()
    workspace = (
        Path(workspace_path).resolve()
        if workspace_path is not None
        else (db.parent.parent if db.parent.name == ".millrace" else db.parent)
    )
    cas = Path(cas_path).resolve() if cas_path is not None else db.parent / "cas"
    return str(workspace), str(db), str(cas.resolve())


def initialize_identity(
    connection: sqlite3.Connection, paths: tuple[str, str, str]
) -> None:
    connection.execute(
        "INSERT INTO control_identity VALUES (1, ?, ?, ?, ?, ?, ?, 0)",
        (str(uuid4()), str(uuid4()), str(uuid4()), *paths),
    )


def identity(
    connection: sqlite3.Connection, paths: tuple[str, str, str]
) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM control_identity WHERE id=1").fetchone()
    if row is None:
        raise ControlOperationError("control_storage_contradiction")
    try:
        for value in row[1:4]:
            uuid_field(value)
        revision_field(row[7])
    except ValueError as exc:
        raise ControlOperationError("control_storage_contradiction") from exc
    if tuple(row[4:7]) != paths:
        raise StoreIdentityMismatch("store_location_mismatch")
    return {
        "workspace_id": row[1],
        "instance_id": row[2],
        "store_epoch": row[3],
        "location_status": "registered",
        "source_revision": row[7],
    }


@contextmanager
def control_transaction(
    connection: sqlite3.Connection,
    *,
    deadline: float | None = None,
    translate_errors: bool = True,
) -> Iterator[None]:
    """Begin before authority reads. Caller owns no native/context I/O inside."""
    if connection.in_transaction:
        raise ControlOperationError("control_transaction_already_active")
    expires = (
        min(time.monotonic() + 4.0, deadline)
        if deadline is not None
        else time.monotonic() + 4.0
    )
    if time.monotonic() >= expires:
        raise ControlOperationError("control_deadline_unknown")
    previous_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    remaining_ms = int((expires - time.monotonic()) * 1000)
    connection.execute(f"PRAGMA busy_timeout={max(1, min(1000, remaining_ms))}")
    connection.set_progress_handler(lambda: int(time.monotonic() >= expires), 1000)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield
        if time.monotonic() >= expires:
            raise ControlOperationError("control_deadline_unknown")
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        if not translate_errors:
            raise
        raise ControlOperationError("control_storage_unknown") from exc
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.set_progress_handler(None, 0)
        connection.execute(f"PRAGMA busy_timeout={int(previous_timeout)}")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _scope(
    connection: sqlite3.Connection, paths: tuple[str, str, str], request: ControlRequest
) -> dict[str, Any]:
    observed = identity(connection, paths)
    if request.key[:3] != tuple(
        observed[key] for key in ("workspace_id", "instance_id", "store_epoch")
    ):
        raise StoreIdentityMismatch("store_identity_mismatch")
    return observed


def _advance(connection: sqlite3.Connection) -> int:
    connection.execute(
        "UPDATE control_identity SET source_revision=source_revision+1 WHERE id=1"
    )
    return int(
        connection.execute(
            "SELECT source_revision FROM control_identity WHERE id=1"
        ).fetchone()[0]
    )


@dataclass(frozen=True)
class _StoredOperation:
    """Validated retained identity; never a reconstructed submitted request."""

    payload: dict[str, Any]
    key: tuple[str, str, str, str, str]
    digest: str


def _validate_receipt(
    receipt: dict[str, Any],
    request: ControlRequest | _StoredOperation,
    snapshot_revision: int,
    *,
    stored_digest: str | None = None,
) -> None:
    value = request.payload
    expected = {
        "request_digest": request.digest if stored_digest is None else stored_digest,
        "target": value["target"],
        "action": value["action"],
        "key": dict(
            zip(
                (
                    "workspace_id",
                    "instance_id",
                    "store_epoch",
                    "caller_id",
                    "operation_id",
                ),
                request.key,
                strict=True,
            )
        ),
        "actor_id": value["actor_id"],
        "caller_id": value["caller_id"],
        "correlation_id": value["correlation_id"],
        "causation_id": value["causation_id"],
        "profile": value["profile"],
        "reason_digest": (
            hashlib.sha256(value["reason"].encode("utf-8")).hexdigest()
            if stored_digest is None
            else digest_field(receipt["reason_digest"])
        ),
        "reason": "[redacted]",
        "redaction_policy_id": "control-reason-omit-v1",
    }
    if canonical_json({key: receipt[key] for key in expected}) != canonical_json(
        expected
    ):
        raise ValueError
    if receipt["pause_id"] is not None:
        uuid_field(receipt["pause_id"])
    disposition = receipt["disposition"]
    if disposition not in {
        "accepted_pending",
        "applied",
        "rejected_no_effect",
        "sealed_not_accepted",
    } or receipt["accepted"] is not (disposition in {"accepted_pending", "applied"}):
        raise ValueError
    if (
        receipt["accepted"]
        and receipt["action"] in {"runs.resume", "runs.recover"}
        and receipt["pause_id"] is None
    ):
        raise ValueError
    if not 0 < revision_field(receipt["source_revision"]) <= snapshot_revision:
        raise ValueError


def _history(
    connection: sqlite3.Connection,
    request: ControlRequest | _StoredOperation,
    *,
    project: bool = True,
    result_id: str | None = None,
    _stored_digest: str | None = None,
    _snapshot_revision: int | None = None,
    _deadline: float | None = None,
) -> dict[str, Any] | None:
    """Validate the same durable history for projection, replay and settlement.

    Settlement streams retained rows instead of applying public page limits, so
    old aftermath remains appendable. Only the latest and requested result are
    retained in memory. The owning transaction and this loop bound the work.
    """
    deadline = (
        min(time.monotonic() + 4, _deadline)
        if _deadline is not None
        else time.monotonic() + 4
    )
    snapshot_revision = (
        _snapshot_revision
        if _snapshot_revision is not None
        else revision_field(
            connection.execute(
                "SELECT source_revision FROM control_identity WHERE id=1"
            ).fetchone()[0]
        )
    )
    row = connection.execute(
        "SELECT request_digest, CASE WHEN length(CAST(receipt_json AS BLOB)) <= 16384 "
        "THEN receipt_json ELSE NULL END FROM control_operations "
        f"WHERE {_KEY_WHERE}",
        request.key,
    ).fetchone()
    if row is None:
        return None
    if row[0] != (request.digest if _stored_digest is None else _stored_digest):
        raise ControlOperationError("operation_idempotency_conflict")
    try:
        receipt = json.loads(row[1])
        _validate_receipt(
            receipt, request, snapshot_revision, stored_digest=_stored_digest
        )
        disposition = receipt["disposition"]
        previous_revision = receipt["source_revision"]
        settled = disposition == "applied"
        wire_size = len(json.dumps(receipt, separators=(",", ":")).encode("utf-8"))
        if project and wire_size > 16384:
            raise ControlOperationError("control_history_item_too_large")
        results = []
        latest = None
        matching = None
        rows = connection.execute(
            "SELECT result_revision, stage, result_id, "
            "CASE WHEN length(CAST(result_json AS BLOB)) <= 16384 "
            f"THEN result_json ELSE NULL END FROM control_results WHERE {_KEY_WHERE} "
            "ORDER BY result_revision",
            request.key,
        )
        for number, stored in enumerate(rows, 1):
            if time.monotonic() >= deadline:
                raise ControlOperationError("control_deadline_unknown")
            if project and number > 100:
                raise ControlOperationError("control_history_item_too_large")
            result = json.loads(stored[3])
            revision = revision_field(result["source_revision"])
            if not previous_revision < revision <= snapshot_revision:
                raise ValueError
            previous_revision = revision
            if revision_field(result["result_revision"]) != number or stored[:3] != (
                number,
                result["stage"],
                result["result_id"],
            ):
                raise ValueError
            if number == 1:
                if result["stage"] != (
                    "pending" if disposition == "accepted_pending" else disposition
                ):
                    raise ValueError
            elif disposition not in {"accepted_pending", "applied"}:
                raise ValueError
            elif settled:
                if result["stage"] != "aftermath":
                    raise ValueError
            else:
                if result["stage"] not in _TERMINAL:
                    raise ValueError
                settled = True
            if project:
                item_size = len(
                    json.dumps(result, separators=(",", ":")).encode("utf-8")
                )
                wire_size += item_size
                if item_size > 16384 or wire_size > 120 * 1024:
                    raise ControlOperationError("control_history_item_too_large")
                results.append(result)
            latest = result
            if result["result_id"] == result_id:
                matching = result
        if latest is None:
            raise ValueError
        if project:
            return {"receipt": receipt, "results": results, "replayed": True}
        return {"receipt": receipt, "latest": latest, "matching": matching}
    except (KeyError, ValueError, TypeError, RecursionError) as exc:
        raise ControlOperationError("control_storage_contradiction") from exc


def stored_operation_history(
    connection: sqlite3.Connection,
    key: tuple[str, str, str, str, str],
    *,
    snapshot_revision: int,
    deadline: float,
) -> dict[str, Any]:
    """Validate retained public authority without reconstructing a redacted reason.

    The immutable SQL digest must equal the receipt digest. Shared context checks
    validate retained action/target/attribution; the submitted pause ID and private
    reason are unavailable by design. Shared receipt/result validation still
    governs both stored linkage and ordinary exact-request replay.
    """
    row = connection.execute(
        "SELECT request_digest, CASE WHEN length(CAST(receipt_json AS BLOB)) "
        "<= 16384 THEN receipt_json ELSE NULL END FROM control_operations "
        f"WHERE {_KEY_WHERE}",
        key,
    ).fetchone()
    try:
        if row is None:
            raise ValueError
        receipt = json.loads(row[1])
        if canonical_json(receipt) != row[1]:
            raise ValueError
        digest = digest_field(row[0])
        if receipt["request_digest"] != digest:
            raise ValueError
        # Validate only retained context. The submitted pause ID and private
        # reason cannot be recovered from the decision's receipt.
        value = {
            field: receipt[field]
            for field in (
                "action",
                "target",
                "caller_id",
                "actor_id",
                "correlation_id",
                "causation_id",
                "profile",
            )
        }
        value["operation_id"] = receipt["key"]["operation_id"]
        _validate_control_context(value)
        retained_key = (
            value["target"]["workspace_id"],
            value["target"]["instance_id"],
            value["target"]["store_epoch"],
            value["caller_id"],
            value["operation_id"],
        )
        if retained_key != key:
            raise ValueError
        request = _StoredOperation(value, retained_key, digest)
        history = _history(
            connection,
            request,
            project=False,
            result_id="acceptance",
            _stored_digest=digest,
            _snapshot_revision=snapshot_revision,
            _deadline=deadline,
        )
        if history is None:
            raise ValueError
        return history
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ControlOperationError("control_storage_contradiction") from exc


def _observation(observed: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_at": _now(),
        "source_revision": observed["source_revision"],
        "store_epoch": observed["store_epoch"],
        "consistency": "transaction_snapshot",
    }


def show_operation(
    connection: sqlite3.Connection, paths: tuple[str, str, str], request: ControlRequest
) -> dict[str, Any]:
    # A bounded read transaction is enough: absence is explicitly provisional.
    if connection.in_transaction:
        raise ControlOperationError("control_transaction_already_active")
    previous_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    connection.execute("PRAGMA busy_timeout=1000")
    connection.execute("BEGIN")
    deadline = time.monotonic() + 4
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        observed = _scope(connection, paths, request)
        result = _history(connection, request) or {
            "receipt": None,
            "results": [],
            "replayed": False,
            "status": "not_found_at_revision",
            "nonacceptance_proven": False,
        }
        result["observation"] = _observation(observed)
        connection.rollback()
        return result
    except sqlite3.Error as exc:
        connection.rollback()
        raise ControlOperationError("control_storage_unknown") from exc
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.set_progress_handler(None, 0)
        connection.execute(f"PRAGMA busy_timeout={int(previous_timeout)}")


@dataclass(frozen=True, slots=True)
class ControlDecision:
    disposition: str
    reason_code: str
    before_control_revision: int = 0
    after_control_revision: int = 0
    before_execution_state: str = "unknown"
    after_execution_state: str = "unknown"
    pause_id: str | None = None
    initial_evidence: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.disposition not in {
            "applied",
            "rejected_no_effect",
            "accepted_pending",
            "sealed_not_accepted",
        }:
            raise ValueError("invalid_control_disposition")
        for value in (
            self.reason_code,
            self.before_execution_state,
            self.after_execution_state,
        ):
            text_field(value)
        revision_field(self.before_control_revision)
        revision_field(self.after_control_revision)
        if self.pause_id is not None:
            uuid_field(self.pause_id)


def execute_operation(
    connection: sqlite3.Connection,
    paths: tuple[str, str, str],
    request: ControlRequest,
    decide: Callable[[], ControlDecision],
    *,
    deadline: float | None = None,
    _locked: bool = False,
) -> dict[str, Any]:
    """Trusted internal admission callback runs once, under begin-before-load lock.

    The callback must do bounded database work only and must not commit, wait,
    perform context/provider I/O, or expose this primitive as capability authority.
    `_locked` is restricted to trusted callers already holding this transaction.
    """
    if _locked and not connection.in_transaction:
        raise ControlOperationError("control_transaction_missing")
    transaction = (
        nullcontext() if _locked else control_transaction(connection, deadline=deadline)
    )
    with transaction:
        _scope(connection, paths, request)
        result = _history(connection, request)
        if result is None:
            decision = decide()
            if not connection.in_transaction:
                raise ControlOperationError("control_transaction_lost")
            value = request.payload
            accepted = decision.disposition in {"applied", "accepted_pending"}
            receipt = {
                "receipt_id": str(uuid4()),
                "key": dict(
                    zip(
                        (
                            "workspace_id",
                            "instance_id",
                            "store_epoch",
                            "caller_id",
                            "operation_id",
                        ),
                        request.key,
                        strict=True,
                    )
                ),
                "action": value["action"],
                "request_digest": request.digest,
                "target": value["target"],
                "actor_id": value["actor_id"],
                "caller_id": value["caller_id"],
                "reason": "[redacted]",
                "reason_digest": hashlib.sha256(
                    value["reason"].encode("utf-8")
                ).hexdigest(),
                "redaction_policy_id": "control-reason-omit-v1",
                "correlation_id": value["correlation_id"],
                "causation_id": value["causation_id"],
                "profile": value["profile"],
                "disposition": decision.disposition,
                "accepted": accepted,
                "reason_code": decision.reason_code,
                "accepted_at": _now() if accepted else None,
                "recorded_at": _now(),
                "before_control_revision": decision.before_control_revision,
                "after_control_revision": decision.after_control_revision,
                "before_execution_state": decision.before_execution_state,
                "after_execution_state": decision.after_execution_state,
                "pause_id": decision.pause_id,
                "source_revision": _advance(connection),
            }
            connection.execute(
                "INSERT INTO control_operations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*request.key, request.digest, canonical_json(receipt)),
            )
            stage = (
                "pending"
                if decision.disposition == "accepted_pending"
                else decision.disposition
            )
            _append(
                connection,
                request,
                result_id="acceptance",
                stage=stage,
                evidence=decision.initial_evidence or {},
            )
            result = _history(connection, request)
            assert result is not None
            result["replayed"] = False
        result["observation"] = _observation(identity(connection, paths))
    return result


def resolve_operation(
    connection: sqlite3.Connection, paths: tuple[str, str, str], request: ControlRequest
) -> dict[str, Any]:
    return execute_operation(
        connection,
        paths,
        request,
        lambda: ControlDecision("sealed_not_accepted", "operation_sealed"),
    )


def _append(
    connection: sqlite3.Connection,
    request: ControlRequest,
    *,
    result_id: str,
    stage: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    row = connection.execute(
        f"SELECT result_revision FROM control_results WHERE {_KEY_WHERE} "
        "ORDER BY result_revision DESC LIMIT 1",
        request.key,
    ).fetchone()
    prior = 0 if row is None else revision_field(row[0])
    result = {
        "result_id": result_id,
        "result_revision": prior + 1,
        "stage": stage,
        "recorded_at": _now(),
        "settled_at": _now() if stage in _TERMINAL else None,
        "source_revision": _advance(connection),
        "evidence": evidence,
    }
    connection.execute(
        "INSERT INTO control_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*request.key, prior + 1, result_id, stage, canonical_json(result)),
    )
    return result


def append_operation_result(
    connection: sqlite3.Connection,
    paths: tuple[str, str, str],
    request: ControlRequest,
    *,
    result_id: str,
    stage: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """One pending settlement; later identified aftermath appends without rewrite.

    Evidence must be pre-redacted public identity/digest/status data, never raw
    native diagnostics, credentials, paths, prompts or provider payloads.
    """
    text_field(result_id)
    canonical_json(evidence)
    if stage not in _TERMINAL | {"aftermath"}:
        raise ValueError("invalid_control_result_stage")
    with control_transaction(connection):
        _scope(connection, paths, request)
        history = _history(connection, request, project=False, result_id=result_id)
        if history is None or not history["receipt"]["accepted"]:
            raise ControlOperationError("operation_not_accepted")
        result = history["matching"]
        if result is not None:
            try:
                retained_evidence = canonical_json(result["evidence"])
            except (KeyError, ValueError, TypeError, RecursionError) as exc:
                raise ControlOperationError("control_storage_contradiction") from exc
            if result["stage"] != stage or retained_evidence != canonical_json(
                evidence
            ):
                raise ControlOperationError("operation_result_conflict")
            return cast(dict[str, Any], result)
        latest = history["latest"]
        if (
            stage != "aftermath"
            and (latest["result_revision"], latest["stage"]) != (1, "pending")
        ) or (
            stage == "aftermath" and latest["stage"] not in _TERMINAL | {"aftermath"}
        ):
            raise ControlOperationError("operation_already_settled")
        return _append(
            connection, request, result_id=result_id, stage=stage, evidence=evidence
        )


@contextmanager
def read_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Bounded rollback-only snapshot; never acquires the writer reservation."""
    if connection.in_transaction:
        raise ControlOperationError("control_transaction_already_active")
    deadline = time.monotonic() + 4
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        connection.execute("BEGIN")
        yield
    finally:
        connection.rollback()
        connection.set_progress_handler(None, 0)


def control_history_records(
    connection: sqlite3.Connection, *, snapshot_revision: int, run_id: str | None
) -> list[dict[str, Any]]:
    """Validate immutable operation chains and expose individual pageable records."""
    deadline = time.monotonic() + 4
    records: list[dict[str, Any]] = []
    rows = connection.execute(
        "SELECT workspace_id, instance_id, store_epoch, caller_id, operation_id "
        "FROM control_operations ORDER BY workspace_id,instance_id,store_epoch,"
        "caller_id,operation_id"
    )
    for key_row in rows:
        if time.monotonic() >= deadline:
            raise ControlOperationError("projection_deadline_exceeded")
        key = cast(tuple[str, str, str, str, str], tuple(key_row))
        history = stored_operation_history(
            connection, key, snapshot_revision=snapshot_revision, deadline=deadline
        )
        receipt = history["receipt"]
        if run_id is not None and receipt["target"].get("run_id") != run_id:
            continue
        record_key = dict(receipt["key"])
        records.append(
            {
                "kind": "receipt",
                "id": receipt["receipt_id"],
                "source_revision": receipt["source_revision"],
                "record": receipt,
                "key": record_key,
                "request_digest": receipt["request_digest"],
                "target": receipt["target"],
                "replayed": True,
            }
        )
        for result_row in connection.execute(
            f"SELECT result_json FROM control_results WHERE {_KEY_WHERE} "
            "ORDER BY result_revision",
            key,
        ):
            if time.monotonic() >= deadline:
                raise ControlOperationError("projection_deadline_exceeded")
            result = json.loads(result_row[0])
            records.append(
                {
                    "kind": "result",
                    "id": receipt["receipt_id"] + ":" + str(result["result_revision"]),
                    "source_revision": result["source_revision"],
                    "record": result,
                    "key": record_key,
                    "request_digest": receipt["request_digest"],
                    "target": receipt["target"],
                    "replayed": True,
                }
            )
    return records


def append_native_result(
    connection: sqlite3.Connection,
    key: tuple[str, str, str, str, str],
    *,
    result_id: str,
    stage: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Append under caller's transaction using validated immutable attribution."""
    if not connection.in_transaction:
        raise ControlOperationError("control_transaction_missing")
    revision = connection.execute(
        "SELECT source_revision FROM control_identity WHERE id=1"
    ).fetchone()[0]
    history = stored_operation_history(
        connection, key, snapshot_revision=revision, deadline=time.monotonic() + 1
    )
    if not history["receipt"]["accepted"]:
        raise ControlOperationError("operation_not_accepted")
    if stage not in _TERMINAL | {"aftermath"}:
        raise ControlOperationError("invalid_control_result_stage")
    text_field(result_id)
    canonical_json(evidence)
    latest = history["latest"]
    if (
        stage != "aftermath"
        and (latest["result_revision"], latest["stage"]) != (1, "pending")
    ) or (stage == "aftermath" and latest["stage"] not in _TERMINAL | {"aftermath"}):
        raise ControlOperationError("operation_already_settled")
    result = {
        "result_id": result_id,
        "result_revision": latest["result_revision"] + 1,
        "stage": stage,
        "recorded_at": _now(),
        "settled_at": _now() if stage in _TERMINAL else None,
        "source_revision": _advance(connection),
        "evidence": evidence,
    }
    connection.execute(
        "INSERT INTO control_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*key, result["result_revision"], result_id, stage, canonical_json(result)),
    )
    return result
