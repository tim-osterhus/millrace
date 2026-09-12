"""Retained daemon incarnation history and exact daemon-scoped admission fences."""

from __future__ import annotations

import codecs
import hashlib
import json
import sqlite3
import time
from typing import Any

import millrace.substrate._sqlite_controls as controls
from millrace.contracts.controls import (
    ControlRequest,
    canonical_json,
    daemon_runtime_identity_field,
    digest_field,
    revision_field,
    text_field,
    uuid_field,
)
from millrace.contracts.daemon_control import session_chain
from millrace.contracts.state import RuntimeState
from millrace.substrate.errors import ControlOperationError

_IDENTITY = (
    "daemon_id",
    "daemon_generation",
    "process_nonce",
    "process",
    "runtime",
    "workspace_id",
    "instance_id",
    "store_epoch",
    "plan_fingerprint",
    "launch_correlation_id",
)
_FENCE = ("session_id", "run_id", "dispatch_generation", "session_fencing_token")
_EMPTY_DIGEST = "0" * 64


def _error() -> ControlOperationError:
    return ControlOperationError("daemon_history_unknown")


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ControlOperationError("daemon_history_deadline_unknown")


def _decode(raw: Any) -> dict[str, Any]:
    value = json.loads(raw)
    if canonical_json(value) != raw:
        raise _error()
    return dict(value)


def plan_fingerprint(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT authority_fingerprint FROM default_plan WHERE id=1"
    ).fetchone()
    return None if row is None else str(row[0])


def _session_history(
    connection: sqlite3.Connection, deadline: float, snapshot: int
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]]]:
    sessions: dict[str, list[dict[str, Any]]] = {}
    digests: dict[str, list[str]] = {}
    for daemon_id, sequence, session_id, raw in connection.execute(
        "SELECT daemon_id, sequence, session_id, CASE WHEN "
        "length(CAST(record_json AS BLOB))<=16384 THEN record_json END "
        "FROM daemon_sessions ORDER BY daemon_id, sequence"
    ):
        _check(deadline)
        value = _decode(raw)
        retained = sessions.setdefault(daemon_id, [])
        chain = digests.setdefault(daemon_id, [_EMPTY_DIGEST])
        if (
            sequence != len(retained) + 1
            or value["sequence"] != sequence
            or value["daemon_id"] != daemon_id
            or value["fence"]["session_id"] != session_id
            or not 0 < revision_field(value["source_revision"]) <= snapshot
        ):
            raise _error()
        fence = value["fence"]
        if set(fence) != set(_FENCE):
            raise _error()
        for key in ("session_id", "run_id", "session_fencing_token"):
            text_field(fence[key], 512)
        if revision_field(fence["dispatch_generation"]) < 1:
            raise _error()
        if value["role"] not in {"owned", "startup_reconciliation"}:
            raise _error()
        chain.append(session_chain(chain[-1], {"fence": fence, "role": value["role"]}))
        if value["digest"] != chain[-1]:
            raise _error()
        if retained and value["source_revision"] <= retained[-1]["source_revision"]:
            raise _error()
        retained.append(value)
    return sessions, digests


def _final_history(
    connection: sqlite3.Connection,
    records: dict[str, dict[str, Any]],
    sessions: dict[str, list[dict[str, Any]]],
    deadline: float,
    snapshot: int,
) -> None:
    results: dict[str, list[dict[str, Any]]] = {}
    for daemon_id, sequence, raw in connection.execute(
        "SELECT daemon_id, sequence, CASE WHEN length(CAST(record_json AS BLOB))"
        "<=16384 THEN record_json END FROM daemon_session_results ORDER BY "
        "daemon_id, sequence"
    ):
        _check(deadline)
        result = _decode(raw)
        rows = results.setdefault(daemon_id, [])
        owned = sessions.get(daemon_id, [])
        if (
            sequence != len(rows) + 1
            or sequence > len(owned)
            or result["fence"] != owned[sequence - 1]["fence"]
            or result["cleanup"]
            not in {"complete", "not_required", "unknown", "orphan_risk"}
            or not owned[sequence - 1]["source_revision"]
            < result["source_revision"]
            <= snapshot
        ):
            raise _error()
        if rows and result["source_revision"] <= rows[-1]["source_revision"]:
            raise _error()
        rows.append(result)
    for daemon_id, record in records.items():
        summary = record["final_summary"]
        rows = results.get(daemon_id, [])
        if record["status"] != "shutdown_complete":
            if summary is not None or rows:
                raise _error()
            continue
        if (
            summary is None
            or summary["session_count"] != record["session_count"]
            or len(rows) != record["session_count"]
        ):
            raise _error()
        digest = _EMPTY_DIGEST
        for result in rows:
            digest = session_chain(
                digest, {"fence": result["fence"], "cleanup": result["cleanup"]}
            )
            if result["source_revision"] >= record["source_revision"]:
                raise _error()
        if summary["session_results_digest"] != digest:
            raise _error()
        cleanup = _cleanup_status(
            [row["cleanup"] for row in rows],
            summary["listener_teardown"],
            summary["stopped_reason"],
        )
        if cleanup != summary["runtime_cleanup"]:
            raise _error()
    if set(results) - set(records):
        raise _error()


def _validate_progress(
    connection: sqlite3.Connection,
    item: dict[str, Any],
    previous: dict[str, Any] | None,
    deadline: float,
) -> None:
    """Validate capture linkage; absent legacy evidence is never backfilled."""
    progress = item.get("runtime_progress")
    if previous is None:
        if progress is not None:
            raise _error()
        return
    prior = previous.get("runtime_progress")
    if "runtime_progress" in previous and "runtime_progress" not in item:
        raise _error()
    if (
        "runtime_progress" not in previous
        and "runtime_progress" in item
        and progress is None
    ):
        raise _error()
    if progress == prior:
        return
    if progress is None or item["status"] == "shutdown_complete":
        raise _error()
    if set(progress) != {
        "captured_at_ns",
        "source_revision",
        "daemon_revision",
        "transition_order",
        "transition_digest",
        "run_fence_digest",
        "session_snapshot",
    }:
        raise _error()
    if (
        revision_field(progress["captured_at_ns"]) < 1
        or progress["source_revision"] != item["source_revision"]
        or progress["daemon_revision"] != item["revision"]
        or (
            prior is not None
            and progress["transition_order"] <= prior["transition_order"]
        )
    ):
        raise _error()
    order = revision_field(progress["transition_order"])
    transition_from = "FROM transitions t WHERE t.transition_order=?"
    row = connection.execute(
        "SELECT substr(input_kind,1,128), accepted FROM transitions "
        "WHERE transition_order=? AND length(input_kind)<=128",
        (order,),
    ).fetchone()
    if row is None or row[1] != 1 or not _progress_kind(row[0]):
        raise _error()
    if (
        _linked_progress_digest(
            connection,
            ("t.record_id", "t.input_id", "t.input_kind"),
            transition_from,
            order,
            deadline,
        )
        != progress["transition_digest"]
    ):
        raise _error()
    # Claim governance describes the previously unclaimed activation and has no
    # run_id. The resulting immutable run's creator input supplies that linkage.
    run_join = (
        "FROM transitions t JOIN governance_events g "
        "ON g.record_id=t.record_id||':governance' LEFT JOIN runs r "
        "ON r.run_id=g.run_id OR (g.run_id IS NULL "
        "AND t.input_kind='workflow.claim_work' AND r.created_by_input_id=t.input_id) "
    )
    run_from = run_join + "WHERE t.transition_order=?"
    event = connection.execute(
        "SELECT r.run_id IS NULL, g.run_id IS NOT NULL "
        "OR t.input_kind='workflow.claim_work' " + run_from + " LIMIT 2",
        (order,),
    ).fetchall()
    if len(event) != 1 or (event[0][0] and event[0][1]):
        raise _error()
    session = progress["session_snapshot"]
    if event[0][0]:
        if session is not None or progress["run_fence_digest"] is not None:
            raise _error()
        return
    if (
        _linked_progress_digest(
            connection,
            (
                "r.run_id",
                "r.generation",
                "r.fencing_token",
                "r.plan_authority_fingerprint",
            ),
            run_from,
            order,
            deadline,
        )
        != progress["run_fence_digest"]
    ):
        raise _error()
    # Registration history is immutable and source-ordered. Its latest dispatch
    # for this run at capture time establishes presence as well as the fence;
    # consulting today's run.current_session_id would rewrite historical meaning.
    applicable = connection.execute(
        "SELECT CASE WHEN length(CAST(s.record_json AS BLOB))<=16384 "
        "THEN s.record_json END " + run_join + "JOIN daemon_sessions s ON "
        "json_extract(s.record_json,'$.fence.run_id')=r.run_id "
        "WHERE t.transition_order=? AND s.daemon_id=? "
        "AND json_extract(s.record_json,'$.source_revision')<? "
        "ORDER BY json_extract(s.record_json,'$.fence.dispatch_generation') DESC "
        "LIMIT 2",
        (order, item["daemon_id"], progress["source_revision"]),
    ).fetchall()
    expected = None if not applicable else _decode(applicable[0][0])["fence"]
    if session != expected:
        raise _error()
    if len(applicable) == 2 and (
        _decode(applicable[1][0])["fence"]["dispatch_generation"]
        == expected["dispatch_generation"]
    ):
        raise _error()
    if session is not None:
        if set(session) != set(_FENCE):
            raise _error()
        for key in ("session_id", "run_id", "session_fencing_token"):
            text_field(session[key], 512)
        if revision_field(session["dispatch_generation"]) < 1:
            raise _error()
        row = connection.execute(
            "SELECT 1 " + run_join + "JOIN runner_sessions s ON s.run_id=r.run_id "
            "WHERE t.transition_order=? AND s.session_id=? AND s.run_id=? "
            "AND s.dispatch_generation=? AND s.session_fencing_token=?",
            (
                order,
                session["session_id"],
                session["run_id"],
                session["dispatch_generation"],
                session["session_fencing_token"],
            ),
        ).fetchone()
        if row is None:
            raise _error()


def _linked_progress_digest(
    connection: sqlite3.Connection,
    columns: tuple[str, ...],
    source: str,
    order: int,
    deadline: float,
) -> str:
    """Hash canonical scalar arrays in bounded text chunks, even for long IDs.

    SQL fragments are fixed private call-site constants. Checking between chunks
    bounds Python work as well as SQLite's enclosing read progress handler.
    """
    digest = hashlib.sha256(b"[")
    for index, column in enumerate(columns):
        _check(deadline)
        if index:
            digest.update(b",")
        metadata = connection.execute(
            f"SELECT typeof({column}), length(CAST({column} AS BLOB)) " + source,
            (order,),
        ).fetchone()
        if metadata is None or metadata[0] not in {"text", "integer"}:
            raise _error()
        if metadata[0] == "integer":
            value = connection.execute(
                f"SELECT {column} " + source, (order,)
            ).fetchone()[0]
            digest.update(str(revision_field(value)).encode())
            continue
        digest.update(b'"')
        decoder = codecs.getincrementaldecoder("utf-8")()
        for offset in range(1, metadata[1] + 1, 4096):
            _check(deadline)
            chunk = connection.execute(
                f"SELECT substr(CAST({column} AS BLOB),?,4096) " + source,
                (offset, order),
            ).fetchone()[0]
            text = decoder.decode(chunk, final=offset + 4096 > metadata[1])
            digest.update(json.dumps(text, ensure_ascii=False)[1:-1].encode())
        digest.update(b'"')
    digest.update(b"]")
    return digest.hexdigest()


def _progress_digest(value: Any) -> str:
    # Runtime IDs are not control-request payloads and have no control wire cap.
    encoded = json.dumps(
        list(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _progress_kind(kind: str) -> bool:
    # Refused signals can be accepted audit records, but are not workflow progress.
    return (
        kind.startswith("workflow.") and kind != "workflow.refuse_runner_session_signal"
    )


def _capture_progress(
    connection: sqlite3.Connection, state: RuntimeState, record: dict[str, Any]
) -> None:
    start = connection.execute("SELECT count(*) FROM transitions").fetchone()[0]
    for order in range(len(state.transitions) - 1, start - 1, -1):
        transition = state.transitions[order]
        if not transition.accepted or not _progress_kind(transition.input_kind):
            continue
        event = next(
            event
            for event in state.governance_events
            if event.record_id == transition.record_id + ":governance"
        )
        run = None if event.run_id is None else state.runs[event.run_id]
        if run is None and transition.input_kind == "workflow.claim_work":
            run = next(
                run
                for run in state.runs.values()
                if run.created_by_input_id == transition.input_id
            )
        session = (
            None
            if run is None
            else state.runner_sessions.get(run.current_session_id or "")
        )
        if session is not None:
            record = _attach_sessions(connection, record, [session_fence(session)])
        progress = {
            "captured_at_ns": time.time_ns(),
            "source_revision": connection.execute(
                "SELECT source_revision FROM control_identity WHERE id=1"
            ).fetchone()[0]
            + 1,
            "daemon_revision": record["revision"] + 1,
            "transition_order": order,
            "transition_digest": _progress_digest(
                (
                    transition.record_id,
                    transition.input_id,
                    transition.input_kind,
                )
            ),
            "run_fence_digest": None
            if run is None
            else _progress_digest(
                (
                    run.run_ref.run_id,
                    run.run_ref.generation,
                    run.run_ref.fencing_token,
                    str(run.run_ref.plan_ref.authority_fingerprint),
                )
            ),
            "session_snapshot": None if session is None else session_fence(session),
        }
        write_record(connection, dict(record, runtime_progress=progress))
        return


def load_records(
    connection: sqlite3.Connection, *, deadline: float | None = None
) -> list[dict[str, Any]]:
    """Stream full immutable linkage under a deadline, with bounded individual rows."""
    expires = deadline if deadline is not None else time.monotonic() + 1
    snapshot = connection.execute(
        "SELECT source_revision FROM control_identity WHERE id=1"
    ).fetchone()[0]
    scope = connection.execute(
        "SELECT workspace_id, instance_id, store_epoch FROM control_identity WHERE id=1"
    ).fetchone()
    latest: dict[str, dict[str, Any]] = {}
    previous_source = 0
    try:
        sessions, digests = _session_history(connection, expires, snapshot)
        for daemon_id, revision, raw in connection.execute(
            "SELECT daemon_id, revision, CASE WHEN length(CAST(record_json AS BLOB)) "
            "<=16384 THEN record_json END FROM daemon_events "
            "ORDER BY json_extract(record_json, '$.source_revision')"
        ):
            _check(expires)
            item = _decode(raw)
            if item["daemon_id"] != uuid_field(daemon_id):
                raise _error()
            if (
                tuple(
                    item[key] for key in ("workspace_id", "instance_id", "store_epoch")
                )
                != scope
            ):
                raise _error()
            daemon_runtime_identity_field(item["runtime"])
            for plan_key in ("plan_fingerprint", "stop_plan_fingerprint"):
                if item[plan_key] is not None:
                    digest_field(item[plan_key], prefixed=True)
            uuid_field(item["process_nonce"])
            text_field(item["launch_correlation_id"])
            if revision_field(item["daemon_generation"]) < 1:
                raise _error()
            source = revision_field(item["source_revision"])
            if not previous_source < source <= snapshot:
                raise _error()
            previous_source = source
            previous = latest.get(daemon_id)
            if item["revision"] != revision or revision != (
                1 if previous is None else previous["revision"] + 1
            ):
                raise _error()
            if previous is None:
                if (
                    item["status"] != "initializing"
                    or item["stop_key"] is not None
                    or item["session_count"] != 0
                ):
                    raise _error()
            else:
                if (
                    any(item[key] != previous[key] for key in _IDENTITY)
                    or previous["status"] == "shutdown_complete"
                ):
                    raise _error()
                if previous["stop_key"] is not None and (
                    item["stop_key"],
                    item["stop_plan_fingerprint"],
                ) != (previous["stop_key"], previous["stop_plan_fingerprint"]):
                    raise _error()
                if item["session_count"] < previous["session_count"]:
                    raise _error()
            count = revision_field(item["session_count"])
            chain = digests.get(daemon_id, [_EMPTY_DIGEST])
            if count >= len(chain) or item["session_digest"] != chain[count]:
                raise _error()
            if count and sessions[daemon_id][count - 1]["source_revision"] >= source:
                raise _error()
            if item["status"] not in {
                "initializing",
                "ready_idle",
                "ready_active",
                "not_ready",
                "stop_requested",
                "shutdown_complete",
            }:
                raise _error()
            process = item["process"]
            if (
                process["status"] != "live"
                or type(process["pid"]) is not int
                or process["pid"] <= 0
                or type(process["uid"]) is not int
                or len(process["birth"]) != 2
                or len(process["boot"]) != 2
                or any(
                    type(v) is not int or v < 0
                    for pair in (process["birth"], process["boot"])
                    for v in pair
                )
            ):
                raise _error()
            key = item["stop_key"]
            if key is not None:
                history = controls.stored_operation_history(
                    connection, tuple(key), snapshot_revision=snapshot, deadline=expires
                )
                receipt = history["receipt"]
                target = receipt["target"]
                if (
                    receipt["action"] != "daemon.stop"
                    or receipt["disposition"] != "accepted_pending"
                    or any(
                        target[name] != item[name]
                        for name in ("daemon_id", "daemon_generation", "process_nonce")
                    )
                    or target["plan_fingerprint"] != item["stop_plan_fingerprint"]
                    or target["expected_runtime"] != item["runtime"]
                ):
                    raise _error()
                if previous is not None and previous["stop_key"] is None:
                    if (
                        item["status"] != "stop_requested"
                        or target["expected_source_revision"] != source - 1
                        or receipt["source_revision"] != source + 1
                        or history["matching"]["source_revision"] != source + 2
                    ):
                        raise _error()
                if item["status"] not in {"stop_requested", "shutdown_complete"}:
                    raise _error()
                if item["status"] == "shutdown_complete" and (
                    history["latest"]["evidence"] != item["final_summary"]
                    or history["latest"]["source_revision"] != source + 1
                    or history["latest"]["stage"]
                    != (
                        "applied"
                        if item["final_summary"]["runtime_cleanup"]
                        in {"complete", "not_required"}
                        else "unknown_effects"
                    )
                ):
                    raise _error()
            _validate_progress(connection, item, previous, expires)
            latest[daemon_id] = item
        current = set()
        for daemon_id, raw in connection.execute(
            "SELECT daemon_id, CASE WHEN length(CAST(record_json AS BLOB)) <= 16384 "
            "THEN record_json END FROM daemon_records"
        ):
            _check(expires)
            if daemon_id not in latest or canonical_json(latest[daemon_id]) != raw:
                raise _error()
            current.add(daemon_id)
        if current != set(latest) or set(sessions) - current:
            raise _error()
        for daemon_id, item in latest.items():
            if item["session_count"] != len(sessions.get(daemon_id, [])):
                raise _error()
        _final_history(connection, latest, sessions, expires, snapshot)
        for row in connection.execute(
            "SELECT workspace_id, instance_id, store_epoch, caller_id, operation_id, "
            "receipt_json FROM control_operations WHERE json_extract(receipt_json, "
            "'$.action')='daemon.stop' AND json_extract(receipt_json, '$.accepted')=1"
        ):
            _check(expires)
            receipt = _decode(row[5])
            found = latest.get(receipt["target"]["daemon_id"])
            if found is None or found["stop_key"] != list(row[:5]):
                raise _error()
        records = sorted(latest.values(), key=lambda item: item["daemon_generation"])
        if [item["daemon_generation"] for item in records] != list(
            range(1, len(records) + 1)
        ):
            raise _error()
        return records
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        RecursionError,
    ) as exc:
        raise _error() from exc


def write_record(
    connection: sqlite3.Connection, record: dict[str, Any]
) -> dict[str, Any]:
    record = dict(
        record,
        revision=record["revision"] + 1,
        source_revision=controls._advance(connection),
    )
    raw = canonical_json(record)
    connection.execute(
        "INSERT INTO daemon_events VALUES (?, ?, ?)",
        (record["daemon_id"], record["revision"], raw),
    )
    connection.execute(
        "INSERT INTO daemon_records VALUES (?, ?) ON CONFLICT(daemon_id) "
        "DO UPDATE SET record_json=excluded.record_json",
        (record["daemon_id"], raw),
    )
    return record


def exact_record(
    connection: sqlite3.Connection, scope: dict[str, Any]
) -> dict[str, Any]:
    records = load_records(connection)
    if not records or any(records[-1][key] != scope[key] for key in _IDENTITY):
        raise ControlOperationError("daemon_incarnation_mismatch")
    return records[-1]


def register(
    connection: sqlite3.Connection,
    paths: tuple[str, str, str],
    record: dict[str, Any],
    previous_revision: int,
) -> dict[str, Any]:
    with controls.control_transaction(connection, deadline=time.monotonic() + 1):
        observed = controls.identity(connection, paths)
        records = load_records(connection)
        if observed["source_revision"] != previous_revision:
            raise ControlOperationError("daemon_runtime_changed")
        if records and records[-1]["status"] != "shutdown_complete":
            raise ControlOperationError("daemon_aftermath_unknown")
        record = dict(
            record,
            **{
                key: observed[key]
                for key in ("workspace_id", "instance_id", "store_epoch")
            },
            daemon_generation=len(records) + 1,
            plan_fingerprint=plan_fingerprint(connection),
            revision=0,
            status="initializing",
            stop_key=None,
            stop_plan_fingerprint=None,
            session_count=0,
            session_digest=_EMPTY_DIGEST,
            final_summary=None,
            runtime_progress=None,
        )
        return write_record(connection, record)


def update(
    connection: sqlite3.Connection, scope: dict[str, Any], **changes: Any
) -> dict[str, Any]:
    with controls.control_transaction(connection, deadline=time.monotonic() + 1):
        record = exact_record(connection, scope)
        if record["stop_key"] is not None and changes.get("status") in {
            "ready_idle",
            "ready_active",
            "not_ready",
        }:
            return record
        if all(record.get(key) == value for key, value in changes.items()):
            return record
        return write_record(connection, dict(record, **changes))


def accept_stop(
    connection: sqlite3.Connection,
    paths: tuple[str, str, str],
    scope: dict[str, Any],
    request: ControlRequest,
    *,
    deadline: float,
    _locked: bool = False,
) -> dict[str, Any]:
    def decide() -> controls.ControlDecision:
        record = exact_record(connection, scope)
        target = request.payload["target"]
        observed = controls.identity(connection, paths)
        if (
            request.payload["action"] != "daemon.stop"
            or request.payload["profile"] is not None
            or any(
                target[key] != record[key]
                for key in ("daemon_id", "daemon_generation", "process_nonce")
            )
            or target["expected_runtime"] != record["runtime"]
            or target["expected_source_revision"] != observed["source_revision"]
            or target["plan_fingerprint"] != plan_fingerprint(connection)
        ):
            return controls.ControlDecision(
                "rejected_no_effect", "daemon_target_mismatch"
            )
        if record["status"] == "shutdown_complete" or record["stop_key"] is not None:
            return controls.ControlDecision(
                "rejected_no_effect", "daemon_stop_already_requested"
            )
        write_record(
            connection,
            dict(
                record,
                status="stop_requested",
                stop_key=list(request.key),
                stop_plan_fingerprint=target["plan_fingerprint"],
            ),
        )
        return controls.ControlDecision("accepted_pending", "daemon_stop_requested")

    return controls.execute_operation(
        connection, paths, request, decide, deadline=deadline, _locked=_locked
    )


def accept_signal_stop(
    connection: sqlite3.Connection, paths: tuple[str, str, str], scope: dict[str, Any]
) -> None:
    """Coalesce local signals with fresh targets in the same admission transaction."""
    deadline = time.monotonic() + 1
    with controls.control_transaction(connection, deadline=deadline):
        record = exact_record(connection, scope)
        if record["stop_key"] is not None:
            return
        target = {
            key: record[key]
            for key in (
                "workspace_id",
                "instance_id",
                "store_epoch",
                "daemon_id",
                "daemon_generation",
                "process_nonce",
            )
        }
        target.update(
            expected_source_revision=controls.identity(connection, paths)[
                "source_revision"
            ],
            expected_runtime=record["runtime"],
            plan_fingerprint=plan_fingerprint(connection),
        )
        request = ControlRequest.parse(
            canonical_json(
                {
                    "contract_id": "millrace.core.controls",
                    "contract_revision": 1,
                    "action": "daemon.stop",
                    "operation_id": "signal:" + record["daemon_id"],
                    "caller_id": "core.local-signal",
                    "actor_id": "local_operator",
                    "reason": "operator signal",
                    "correlation_id": record["launch_correlation_id"],
                    "target": target,
                }
            )
        )
        accept_stop(connection, paths, scope, request, deadline=deadline, _locked=True)


def session_fence(session: Any) -> dict[str, Any]:
    return {key: getattr(session, key) for key in _FENCE}


def _attach_sessions(
    connection: sqlite3.Connection,
    record: dict[str, Any],
    fences: list[dict[str, Any]],
    role: str = "owned",
) -> dict[str, Any]:
    count, digest = record["session_count"], record["session_digest"]
    for fence in fences:
        old = connection.execute(
            "SELECT record_json FROM daemon_sessions WHERE daemon_id=? AND "
            "session_id=?",
            (record["daemon_id"], fence["session_id"]),
        ).fetchone()
        if old is not None:
            if json.loads(old[0])["fence"] != fence:
                raise _error()
            continue
        count += 1
        digest = session_chain(digest, {"fence": fence, "role": role})
        value = {
            "daemon_id": record["daemon_id"],
            "sequence": count,
            "fence": fence,
            "role": role,
            "digest": digest,
            "source_revision": controls._advance(connection),
        }
        connection.execute(
            "INSERT INTO daemon_sessions VALUES (?, ?, ?, ?)",
            (record["daemon_id"], count, fence["session_id"], canonical_json(value)),
        )
    if count != record["session_count"]:
        return write_record(
            connection, dict(record, session_count=count, session_digest=digest)
        )
    return record


def attach_sessions(
    connection: sqlite3.Connection,
    scope: dict[str, Any],
    fences: list[dict[str, Any]],
    role: str = "owned",
) -> dict[str, Any]:
    with controls.control_transaction(connection, deadline=time.monotonic() + 1):
        return _attach_sessions(
            connection, exact_record(connection, scope), fences, role
        )


def session_page(
    connection: sqlite3.Connection,
    daemon_id: str,
    after: int = 0,
    limit: int = 50,
    *,
    wire_limit: int | None = 60 * 1024,
) -> list[dict[str, Any]]:
    page: list[dict[str, Any]] = []
    size = 2
    for sequence, raw, final in connection.execute(
        "SELECT s.sequence, s.record_json, r.record_json FROM daemon_sessions s "
        "LEFT JOIN daemon_session_results r ON s.daemon_id=r.daemon_id AND "
        "s.sequence=r.sequence "
        "WHERE s.daemon_id=? AND s.sequence>? ORDER BY s.sequence LIMIT ?",
        (daemon_id, after, limit),
    ):
        item = {
            "sequence": sequence,
            "role": json.loads(raw)["role"],
            **json.loads(raw)["fence"],
            "cleanup": "pending" if final is None else json.loads(final)["cleanup"],
        }
        item_size = len(json.dumps(item, separators=(",", ":")).encode()) + 1
        if wire_limit is not None and size + item_size > wire_limit:
            if not page:
                raise ControlOperationError("daemon_session_page_oversized")
            break
        page.append(item)
        size += item_size
    return page


def admit_unit(connection: sqlite3.Connection, scope: dict[str, Any]) -> None:
    with controls.control_transaction(connection, deadline=time.monotonic() + 1):
        record = exact_record(connection, scope)
        if record["stop_key"] is not None or record["status"] not in {
            "ready_idle",
            "ready_active",
        }:
            raise ControlOperationError("daemon_admission_stopped")


def guard_runtime_write(
    connection: sqlite3.Connection, state: RuntimeState, scope: dict[str, Any] | None
) -> None:
    if scope is None:
        return
    record = exact_record(connection, scope)
    if record["status"] == "shutdown_complete":
        raise ControlOperationError("daemon_already_shutdown")
    old_runs = {row[0] for row in connection.execute("SELECT run_id FROM runs")}
    old_sessions = {
        row[0]: row[1]
        for row in connection.execute("SELECT session_id, state FROM runner_sessions")
    }
    new_runs = set(state.runs) - old_runs
    new_sessions = set(state.runner_sessions) - set(old_sessions)
    starts = {
        sid
        for sid, session in state.runner_sessions.items()
        if session.state == "starting" and old_sessions.get(sid) != "starting"
    }
    if record["stop_key"] is not None or record["status"] not in {
        "ready_idle",
        "ready_active",
    }:
        if new_runs or new_sessions or starts:
            raise ControlOperationError("daemon_admission_stopped")
    record = _attach_sessions(
        connection,
        record,
        [
            session_fence(state.runner_sessions[sid])
            for sid in sorted(new_sessions | starts)
        ],
    )

    _capture_progress(connection, state, record)


def _cleanup_status(dispositions: list[str], listener: str, reason: str) -> str:
    if "orphan_risk" in dispositions:
        return "orphan_risk"
    if (
        listener != "complete"
        or reason == "daemon_failed"
        or any(value not in {"complete", "not_required"} for value in dispositions)
    ):
        return "unknown"
    return "complete" if "complete" in dispositions else "not_required"


def finish(
    connection: sqlite3.Connection,
    scope: dict[str, Any],
    summary: dict[str, Any],
    results: list[dict[str, Any]],
    expected_revision: int,
) -> dict[str, Any]:
    with controls.control_transaction(connection, deadline=time.monotonic() + 1):
        record = exact_record(connection, scope)
        if (
            connection.execute(
                "SELECT source_revision FROM control_identity WHERE id=1"
            ).fetchone()[0]
            != expected_revision
        ):
            raise ControlOperationError("daemon_cleanup_snapshot_changed")
        if len(results) != record["session_count"]:
            raise _error()
        digest = _EMPTY_DIGEST
        for sequence, result in enumerate(results, 1):
            digest = session_chain(digest, result)
            value = dict(result, source_revision=controls._advance(connection))
            connection.execute(
                "INSERT INTO daemon_session_results VALUES (?, ?, ?)",
                (record["daemon_id"], sequence, canonical_json(value)),
            )
        summary = dict(
            summary,
            session_count=len(results),
            session_results_digest=digest,
            runtime_cleanup=_cleanup_status(
                [item["cleanup"] for item in results],
                summary["listener_teardown"],
                summary["stopped_reason"],
            ),
        )
        record = write_record(
            connection, dict(record, status="shutdown_complete", final_summary=summary)
        )
        if record["stop_key"] is not None:
            history = controls.stored_operation_history(
                connection,
                tuple(record["stop_key"]),
                snapshot_revision=record["source_revision"],
                deadline=time.monotonic() + 1,
            )
            receipt = history["receipt"]
            request = ControlRequest.parse(
                canonical_json(
                    {
                        "contract_id": "millrace.core.controls",
                        "contract_revision": 1,
                        "action": "daemon.stop",
                        "operation_id": receipt["key"]["operation_id"],
                        "actor_id": receipt["actor_id"],
                        "caller_id": receipt["caller_id"],
                        "reason": "retained private reason",
                        "correlation_id": receipt["correlation_id"],
                        "causation_id": receipt["causation_id"],
                        "target": receipt["target"],
                    }
                )
            )
            controls._append(
                connection,
                request,
                result_id="shutdown",
                stage="applied"
                if summary["runtime_cleanup"] in {"complete", "not_required"}
                else "unknown_effects",
                evidence=summary,
            )
        return record
