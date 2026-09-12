"""Serialized run holds and guards over replace-managed runtime snapshots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import replace
from typing import Any
from uuid import uuid4

from millrace.contracts.controls import (
    ControlRequest,
    canonical_json,
    digest_field,
    revision_field,
    uuid_field,
)
from millrace.contracts.state import RunExecutionControl, RuntimeState
from millrace.kernel.run_controls import (
    exact_run_target_refusal,
    run_control_projection,
    run_eligibility_refusal,
)
from millrace.substrate._sqlite_controls import (
    ControlDecision,
    _advance,
    _now,
    append_native_result,
    stored_operation_history,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import ControlOperationError


def _control_record(run_id: str, raw: str) -> RunExecutionControl:
    value = json.loads(raw)
    if canonical_json(value) != raw:
        raise ValueError
    if value["operation_key"] is not None:
        value["operation_key"] = tuple(value["operation_key"])
    control = RunExecutionControl(**value)
    if control.run_id != run_id or control.source_revision == 0:
        raise ValueError
    return control


def _event_target(
    connection: sqlite3.Connection, target: dict[str, Any], *, native: bool = False
) -> None:
    # Session rows are retained. Historical targets bind their own attempt,
    # never a later current session or explicit retry.
    run = connection.execute(
        "SELECT plan_authority_fingerprint, generation, fencing_token "
        "FROM runs WHERE run_id=?",
        (target["run_id"],),
    ).fetchone()
    if run != (
        target["plan_fingerprint"],
        target["run_generation"],
        target["run_fencing_token"],
    ):
        raise ValueError
    session = target["expected_session"]
    if session is None:
        if target["last_dispatch_generation"] != 0:
            raise ValueError
        return
    row = connection.execute(
        "SELECT run_id, dispatch_generation, session_fencing_token "
        "FROM runner_sessions WHERE session_id=?",
        (session["session_id"],),
    ).fetchone()
    if session["state"] not in (
        {"running", "lost"} if native else {"created"}
    ) or row != (
        target["run_id"],
        session["dispatch_generation"],
        session["session_fencing_token"],
    ):
        raise ValueError


def _applied_event(
    connection: sqlite3.Connection,
    control: RunExecutionControl,
    scope: tuple[str, str, str],
    snapshot_revision: int,
    deadline: float,
) -> tuple[int, dict[str, Any]]:
    key = control.operation_key
    if key is None or key[:3] != scope:
        raise ValueError
    history = stored_operation_history(
        connection,
        key,
        snapshot_revision=snapshot_revision,
        deadline=deadline,
    )
    receipt, result = history["receipt"], history["matching"]
    action = "runs.pause" if control.state == "paused" else "runs.resume"
    reason = "run_paused" if control.state == "paused" else "run_resumed"
    expected = {
        "action": action,
        "disposition": "applied",
        "accepted": True,
        "reason_code": reason,
        "profile": None,
        "pause_id": control.pause_id,
        "before_control_revision": control.control_revision - 1,
        "after_control_revision": control.control_revision,
        "before_execution_state": "runnable_unstarted"
        if control.state == "paused"
        else "paused",
        "after_execution_state": "paused"
        if control.state == "paused"
        else "runnable_unstarted",
    }
    if control.cause != reason or canonical_json(
        {field: receipt[field] for field in expected}
    ) != canonical_json(expected):
        raise ValueError
    if result is None or (
        result["result_revision"],
        result["result_id"],
        result["stage"],
        result["evidence"],
    ) != (1, "acceptance", "applied", {}):
        raise ValueError
    if (
        receipt["source_revision"] != control.source_revision + 1
        or result["source_revision"] != receipt["source_revision"] + 1
        or result["source_revision"] > snapshot_revision
    ):
        raise ValueError
    target = receipt["target"]
    if (
        target["run_id"] != control.run_id
        or target["expected_control_revision"] != control.control_revision - 1
    ):
        raise ValueError
    _event_target(connection, target)
    return result["source_revision"], target


def _aftermath_event(
    connection: sqlite3.Connection,
    control: RunExecutionControl,
    prior: RunExecutionControl | None,
    target: dict[str, Any] | None,
) -> None:
    if prior is None:
        if control.pause_id is not None or control.operation_key is not None:
            raise ValueError
    elif (control.pause_id, control.operation_key) != (
        prior.pause_id,
        prior.operation_key,
    ):
        raise ValueError
    if control.state == "unknown" and control.cause == "run_aftermath_unknown":
        evidence = connection.execute(
            "SELECT 1 FROM governance_events WHERE run_id=? AND input_kind IN "
            "('workflow.refuse_runner_session_signal', "
            "'workflow.record_runner_session_completion', "
            "'workflow.runner_result_observed') LIMIT 1",
            (control.run_id,),
        ).fetchone()
    elif control.state == "superseded" and control.cause == "run_work_closed":
        evidence = connection.execute(
            "SELECT 1 FROM closed_work_items c JOIN runs r "
            "ON r.work_item_id=c.work_item_id WHERE r.run_id=?",
            (control.run_id,),
        ).fetchone()
    elif (
        control.state == "superseded"
        and control.cause == "run_cancellation_in_progress"
    ):
        session = None if target is None else target["expected_session"]
        evidence = (
            None
            if session is None
            else connection.execute(
                "SELECT 1 FROM runner_session_cancellation_requests WHERE session_id=?",
                (session["session_id"],),
            ).fetchone()
        )
    else:
        raise ValueError
    if evidence is None:
        raise ValueError


def _control_transition(
    control: RunExecutionControl,
    prior: RunExecutionControl | None,
    used_pause_ids: set[str],
) -> None:
    if control.native is not None:
        if control.state == "pause_pending":
            if control.pause_id is None or control.pause_id in used_pause_ids:
                raise ValueError
            used_pause_ids.add(control.pause_id)
        return  # The separate native history validator below owns these transitions.
    prior_state = None if prior is None else prior.state
    permitted: dict[str, set[str | None]] = {
        "paused": {None, "resumed", "superseded"},
        "resumed": {"paused"},
        "unknown": {None, "paused", "resumed", "superseded"},
        "superseded": {"paused", "unknown"},
    }
    if prior_state not in permitted[control.state]:
        raise ValueError
    if control.state == "paused":
        if control.pause_id is None or control.pause_id in used_pause_ids:
            raise ValueError
        used_pause_ids.add(control.pause_id)
    elif control.state == "resumed":
        if prior is None or control.pause_id != prior.pause_id:
            raise ValueError
    elif control.state == "superseded" and control.pause_id is None:
        raise ValueError


def _history_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: str,
    deadline: float,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    total_bytes = 0
    cursor = connection.execute(
        f"SELECT {columns}, CASE WHEN length(CAST(record_json AS BLOB)) <= 16384 "
        f"THEN record_json ELSE NULL END FROM {table} ORDER BY {columns} LIMIT 10001"
    )
    for row in cursor:
        check_deadline(deadline)
        if len(rows) == 10000 or row[-1] is None:
            raise ControlOperationError("run_control_history_bound_exceeded")
        if not isinstance(row[-1], str):
            raise ValueError
        total_bytes += len(row[-1].encode("utf-8"))
        if total_bytes > 32 * 1024 * 1024:
            raise ControlOperationError("run_control_history_bound_exceeded")
        rows.append(row)
    return rows


def load_run_controls(connection: sqlite3.Connection) -> dict[str, RunExecutionControl]:
    deadline = time.monotonic() + 4
    try:
        identity = connection.execute(
            "SELECT workspace_id, instance_id, store_epoch, source_revision "
            "FROM control_identity WHERE id=1"
        ).fetchone()
        scope = tuple(uuid_field(value) for value in identity[:3])
        snapshot_revision = revision_field(identity[3])
        current = dict(
            _history_rows(connection, "run_execution_controls", "run_id", deadline)
        )
        controls: dict[str, RunExecutionControl] = {}
        ends: dict[str, int] = {}
        targets: dict[str, dict[str, Any]] = {}
        used_pause_ids: set[str] = set()
        used_revisions: set[int] = set()
        for run_id, revision, raw in _history_rows(
            connection, "run_control_events", "run_id, control_revision", deadline
        ):
            check_deadline(deadline)
            control = _control_record(run_id, raw)
            prior = controls.get(run_id)
            if revision_field(revision) != control.control_revision or revision != (
                1 if prior is None else prior.control_revision + 1
            ):
                raise ValueError
            if (
                not ends.get(run_id, 0) < control.source_revision <= snapshot_revision
                or control.source_revision in used_revisions
            ):
                raise ValueError
            used_revisions.add(control.source_revision)
            _control_transition(control, prior, used_pause_ids)
            if control.native is not None:
                ends[run_id], targets[run_id] = _native_event(
                    connection, control, prior, scope, snapshot_revision, deadline
                )
            elif control.state in {"paused", "resumed"}:
                previous_target = targets.get(run_id)
                ends[run_id], targets[run_id] = _applied_event(
                    connection,
                    control,
                    (scope[0], scope[1], scope[2]),
                    snapshot_revision,
                    deadline,
                )
                if control.state == "resumed" and previous_target != {
                    **targets[run_id],
                    "expected_control_revision": control.control_revision - 2,
                }:
                    raise ValueError
            else:
                _aftermath_event(connection, control, prior, targets.get(run_id))
                ends[run_id] = control.source_revision
            controls[run_id] = control
        if set(current) != set(controls):
            raise ValueError
        for run_id, raw in current.items():
            if _control_record(run_id, raw) != controls[run_id]:
                raise ValueError
        return controls
    except ControlOperationError as exc:
        if str(exc) == "control_storage_contradiction":
            raise ControlOperationError("run_control_storage_contradiction") from exc
        raise
    except (ValueError, TypeError, KeyError, IndexError, RecursionError) as exc:
        raise ControlOperationError("run_control_storage_contradiction") from exc


def write_run_control(
    connection: sqlite3.Connection, control: RunExecutionControl
) -> None:
    control = replace(control, source_revision=_advance(connection), recorded_at=_now())
    raw = canonical_json(
        {
            "run_id": control.run_id,
            "control_revision": control.control_revision,
            "pause_id": control.pause_id,
            "state": control.state,
            "cause": control.cause,
            "operation_key": None
            if control.operation_key is None
            else list(control.operation_key),
            "source_revision": control.source_revision,
            "recorded_at": control.recorded_at,
            **({"native": dict(control.native)} if control.native is not None else {}),
        }
    )
    connection.execute(
        "INSERT INTO run_execution_controls VALUES (?, ?) ON "
        "CONFLICT(run_id) DO UPDATE SET record_json=excluded.record_json",
        (control.run_id, raw),
    )
    connection.execute(
        "INSERT INTO run_control_events VALUES (?, ?, ?)",
        (control.run_id, control.control_revision, raw),
    )


def decide_run_control(
    connection: sqlite3.Connection,
    state: RuntimeState,
    request: ControlRequest,
    source_revision: int,
    supported_adapter_kinds: frozenset[str],
) -> ControlDecision:
    # All filesystem/CAS validation is staged. The source revision proves that
    # exact authority still holds at this transaction's admission boundary.
    current_revision = connection.execute(
        "SELECT source_revision FROM control_identity WHERE id=1"
    ).fetchone()[0]
    if source_revision != current_revision:
        raise ControlOperationError("run_authority_changed")
    # Replace-exempt control rows use explicit revision allocation. Revalidate
    # their retained evidence under this lock even if damaged by raw SQL.
    if load_run_controls(connection) != state.run_execution_controls:
        raise ControlOperationError("run_authority_changed")
    value = request.payload
    target = value["target"]
    if value["action"] not in {"runs.pause", "runs.resume"}:
        return ControlDecision("rejected_no_effect", "unsupported_control_action")
    run_id = target["run_id"]
    control = state.run_execution_controls.get(run_id)
    revision = 0 if control is None else control.control_revision
    before = (
        "runnable_unstarted"
        if control is None or control.state == "resumed"
        else control.state
    )
    pause_id = None if control is None else control.pause_id

    def refuse(reason: str) -> ControlDecision:
        return ControlDecision(
            "rejected_no_effect", reason, revision, revision, before, before, pause_id
        )

    refusal = exact_run_target_refusal(state, target)
    if refusal is not None:
        return refuse(refusal)
    run = state.runs[run_id]
    before = str(run_control_projection(state, run_id)["execution_state"])
    refusal = run_eligibility_refusal(state, run)
    if refusal is not None:
        return refuse(refusal)
    if (
        run.current_session_id is not None
        and connection.execute(
            "SELECT 1 FROM daemon_budget_sessions WHERE session_id=?",
            (run.current_session_id,),
        ).fetchone()
        is not None
    ):
        return refuse("run_pause_unsupported_state")
    admitted = state.admitted_plans[run.run_ref.plan_ref.authority_fingerprint]
    binding = next(
        item
        for item in admitted.selected_plan.runner_bindings
        if item.id == run.runner_binding_id
    )
    if binding.adapter_kind not in supported_adapter_kinds:
        return refuse("runner_pause_unsupported")
    if value["profile"] is not None:
        return refuse("run_native_profile_not_supported")
    if value["action"] == "runs.pause":
        if control is not None and control.state == "paused":
            return refuse("already_paused")
        pause_id = str(uuid4())
        next_state, cause, after = "paused", "run_paused", "paused"
    else:
        if control is None or control.state != "paused":
            return refuse("pause_not_active")
        if value["pause_id"] != control.pause_id:
            return refuse("pause_id_mismatch")
        next_state, cause, after = "resumed", "run_resumed", "runnable_unstarted"
    assert pause_id is not None
    write_run_control(
        connection,
        RunExecutionControl(
            run_id,
            revision + 1,
            pause_id,
            next_state,
            cause,
            request.key,
            0,
            _now(),
        ),
    )
    return ControlDecision(
        "applied", cause, revision, revision + 1, before, after, pause_id
    )


def guard_budget_reservation(connection: sqlite3.Connection, run_id: str) -> None:
    control = load_run_controls(connection).get(run_id)
    if control is not None and control.state in {
        "paused",
        "unknown",
        "pause_pending",
        "resume_pending",
        "recover_pending",
        "retired",
    }:
        raise ControlOperationError(
            "run_execution_held"
            if control.state == "paused"
            else "run_aftermath_unknown"
        )


def guard_runtime_write(connection: sqlite3.Connection, state: RuntimeState) -> None:
    durable = load_run_controls(connection)
    if durable != state.run_execution_controls:
        raise ControlOperationError("stale_runtime_controls")
    event_count = connection.execute(
        "SELECT COUNT(*) FROM governance_events"
    ).fetchone()[0]
    processed: set[str] = set()
    for event in state.governance_events[event_count:]:
        if (
            event.run_id is None
            or event.run_id in processed
            or (
                event.run_id in durable
                and durable[event.run_id].state in {"paused", "unknown"}
            )
            or event.input_kind
            not in {
                "workflow.refuse_runner_session_signal",
                "workflow.record_runner_session_completion",
                "workflow.runner_result_observed",
            }
        ):
            continue
        candidate_run = state.runs.get(event.run_id)
        candidate_session = state.runner_sessions.get(
            "" if candidate_run is None else candidate_run.current_session_id or ""
        )
        if (
            event.input_kind != "workflow.refuse_runner_session_signal"
            and candidate_session is not None
            and candidate_session.start_intent_at is not None
        ):
            continue
        prior = connection.execute(
            "SELECT s.state FROM runs r LEFT JOIN runner_sessions s ON "
            "s.session_id=r.current_session_id WHERE r.run_id=?",
            (event.run_id,),
        ).fetchone()
        if prior is not None and prior[0] in {None, "created"}:
            processed.add(event.run_id)
            old = durable.get(event.run_id)
            write_run_control(
                connection,
                RunExecutionControl(
                    event.run_id,
                    1 if old is None else old.control_revision + 1,
                    None if old is None else old.pause_id,
                    "unknown",
                    "run_aftermath_unknown",
                    None if old is None else old.operation_key,
                    0,
                    _now(),
                ),
            )
    for run_id, control in durable.items():
        if control.state not in {
            "paused",
            "unknown",
            "pause_pending",
            "resume_pending",
            "recover_pending",
            "retired",
        }:
            continue
        run = state.runs.get(run_id)
        if run is None:
            raise ControlOperationError("run_control_authority_mismatch")
        prior = connection.execute(
            "SELECT current_session_id, last_dispatch_generation FROM runs "
            "WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if prior != (run.current_session_id, run.last_dispatch_generation):
            raise ControlOperationError("run_execution_held")
        session = state.runner_sessions.get(run.current_session_id or "")
        prior_session = (
            None
            if session is None
            else connection.execute(
                "SELECT state, start_intent_at, context_manifest_digest "
                "FROM runner_sessions WHERE session_id=?",
                (session.session_id,),
            ).fetchone()
        )
        if session is not None and (
            prior_session is None
            or (
                session.start_intent_at != prior_session[1]
                or session.context_manifest_digest != prior_session[2]
                or (session.state in {"starting", "running"} and control.native is None)
            )
        ):
            raise ControlOperationError("run_execution_held")
        # Preserve contradictory signal evidence, but revoke successful paused
        # classification before committing it. Existing completion validation is
        # still authoritative and result application cannot pass the hold.
        unexpected = any(
            event.run_id == run_id
            and event.input_kind
            in {
                "workflow.refuse_runner_session_signal",
                "workflow.record_runner_session_completion",
                "workflow.runner_result_observed",
            }
            for event in state.governance_events[event_count:]
        )
        closed = run.work_item_id in state.closed_work_items
        cancellation = any(
            record.session_id == run.current_session_id
            for record in state.runner_session_cancellation_requests.values()
        )
        observed = any(
            record.run_id == run_id for record in state.runner_observations.values()
        )
        if observed:
            raise ControlOperationError("run_execution_held")
        if control.native is not None:
            recovery_loss = (
                control.state == "recover_pending"
                and session is not None
                and session.state == "lost"
                and not cancellation
                and not closed
            )
            if (unexpected or cancellation or closed) and not recovery_loss:
                native_update(
                    connection,
                    run_id,
                    str(control.native["snapshot"]["owner_id"]),
                    dict(control.native["snapshot"]),
                    reason="core_authority_preempted",
                )
            continue
        if unexpected and not cancellation:
            next_state, cause = "unknown", "run_aftermath_unknown"
        elif closed:
            next_state, cause = "superseded", "run_work_closed"
        elif cancellation:
            next_state, cause = "superseded", "run_cancellation_in_progress"
        else:
            continue
        if control.pause_id is None and next_state == "superseded":
            continue
        if control.state == "unknown" and next_state == "unknown":
            continue
        write_run_control(
            connection,
            replace(
                control,
                control_revision=control.control_revision + 1,
                state=next_state,
                cause=cause,
            ),
        )


def check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ControlOperationError("control_deadline_unknown")


class BoundedControlCAS(ContentAddressedByteStore):
    """Bound staged context size/work before acquiring the admission lock."""

    def __init__(self, store: ContentAddressedByteStore, deadline: float) -> None:
        self._delegate = store
        self._deadline = deadline
        self._bytes = 0

    def get_bytes(self, digest: str) -> bytes:
        check_deadline(self._deadline)
        size = self._delegate._object_path(digest).stat().st_size
        self._bytes += size
        if size > 16 * 1024 * 1024 or self._bytes > 64 * 1024 * 1024:
            raise ControlOperationError("run_control_context_bound_exceeded")
        payload = self._delegate.get_bytes(digest)
        check_deadline(self._deadline)
        return payload


def _native_snapshot_history(
    connection: sqlite3.Connection,
    control: RunExecutionControl,
    prior: RunExecutionControl | None,
    receipt: dict[str, Any],
    snapshot_revision: int,
    deadline: float,
) -> None:
    """Bind native identities within their namespace and their accepted history."""
    native = control.native
    assert native is not None and control.operation_key is not None
    snapshot = native["snapshot"]
    previous = None if prior is None else prior.native
    if previous is not None:
        # A new pause may target a later Core attempt. Resume, settlement and
        # recovery always retain their own original native invocation.
        same_attempt = native["attempt"] == previous["attempt"]
        if (
            same_attempt
            or receipt["action"] != "runs.pause"
            or native["kind"] != "intent"
        ):
            if not same_attempt or native["owner"] != previous["owner"]:
                raise ValueError
            old = previous["snapshot"]
            expected = set(old["operations"])
            current_key = canonical_json(list(control.operation_key))
            if current_key in snapshot["operations"] and current_key not in expected:
                if (
                    previous["kind"] != "intent"
                    or native["kind"] == "intent"
                    or receipt["action"] not in {"runs.pause", "runs.resume"}
                ):
                    raise ValueError
                if len(expected) == 2:
                    # Native admission alone evicts the oldest of two entries.
                    # Pause/resume may share a native sequence; receipt order
                    # preserves admission order independently of JSON key order.
                    revisions = {
                        key: stored_operation_history(
                            connection,
                            tuple(json.loads(key)),
                            snapshot_revision=snapshot_revision,
                            deadline=deadline,
                        )["receipt"]["source_revision"]
                        for key in expected
                    }
                    expected.remove(min(revisions, key=lambda key: revisions[key]))
                expected.add(current_key)
            if set(snapshot["operations"]) != expected:
                raise ValueError
            if any(
                snapshot[field] != old[field]
                for field in ("session_id", "invocation_id")
            ):
                raise ValueError
            if snapshot["sequence"] < old["sequence"]:
                raise ValueError
            for key, operation in old["operations"].items():
                retained = snapshot["operations"].get(key)
                if retained is not None and (
                    retained["digest"] != operation["digest"]
                    or (
                        operation["stage"] != "accepted_pending"
                        and retained != operation
                    )
                ):
                    raise ValueError
    for encoded, operation in snapshot["operations"].items():
        key = json.loads(encoded)
        if (
            type(key) is not list
            or len(key) != 5
            or any(type(part) is not str for part in key)
            or canonical_json(key) != encoded
            or key[:3] != list(control.operation_key[:3])
        ):
            raise ValueError
        history = stored_operation_history(
            connection,
            tuple(key),
            snapshot_revision=snapshot_revision,
            deadline=deadline,
        )
        accepted = history["receipt"]
        if (
            accepted["disposition"] != "accepted_pending"
            or accepted["source_revision"] >= control.source_revision
            or accepted["action"] not in {"runs.pause", "runs.resume"}
            or accepted["request_digest"] != operation["digest"]
            or accepted["target"]["run_id"] != control.run_id
            or accepted["target"]["expected_session"] != native["attempt"]
            or accepted["profile"] != native["profile"]
        ):
            raise ValueError
    action, kind = receipt["action"], native["kind"]
    encoded = canonical_json(list(control.operation_key))
    operation = snapshot["operations"].get(encoded)
    if kind == "intent":
        # This snapshot precedes signaling the newly accepted native operation.
        if (
            operation is not None
            or snapshot["pending"] is not None
            or not snapshot["eligible"]
            or snapshot["invalidation_pending"]
        ):
            raise ValueError
        if action == "runs.pause":
            prior_operations = (
                previous["snapshot"]["operations"]
                if previous is not None and native["attempt"] == previous["attempt"]
                else {}
            )
            if snapshot["operations"] != prior_operations:
                raise ValueError
            if snapshot["state"] != "running":
                raise ValueError
            if snapshot["pause_id"] != (
                None
                if prior is None
                or previous is None
                or native["attempt"] != previous["attempt"]
                else prior.pause_id
            ):
                raise ValueError
        else:
            if previous is None or snapshot["pause_id"] != control.pause_id:
                raise ValueError
            if snapshot["state"] != "held":
                raise ValueError
            if snapshot != previous["snapshot"]:
                raise ValueError
        return
    if action == "runs.recover":
        # Retirement retains the verified pause witness; there is no native
        # recovery operation or live owner signal to substitute into it.
        if previous is None or snapshot != previous["snapshot"]:
            raise ValueError
        return
    if (
        kind == "aftermath"
        and previous is not None
        and snapshot == previous["snapshot"]
    ):
        return  # Independent loss/preemption may retain the last known snapshot.
    if (
        kind == "aftermath"
        and control.state == "unknown"
        and previous is not None
        and previous["kind"] == "intent"
        and operation is None
        and snapshot["state"] == "closed"
        and not snapshot["eligible"]
        and snapshot["pending"] is None
        and snapshot["pause_id"] == previous["snapshot"]["pause_id"]
        and snapshot["operations"] == previous["snapshot"]["operations"]
    ):
        # Commit delivery can fail before native signaling. Preserve truthful
        # closed aftermath without inventing a native acceptance or a new hold.
        return
    if snapshot["pause_id"] != control.pause_id or snapshot["pending"] is not None:
        raise ValueError
    if operation is None or operation["stage"] != (
        "unknown" if control.state == "unknown" else "applied"
    ):
        # A fresh closed snapshot after an applied control retains that outcome;
        # its separate aftermath does not rewrite the native operation result.
        if not (
            kind == "aftermath"
            and operation is not None
            and operation["stage"] == "applied"
        ):
            raise ValueError
    if kind == "settlement" and control.state in {"paused", "resumed"}:
        if (
            operation is None
            or operation["sequence"] != snapshot["sequence"]
            or snapshot["active_effects"] != 0
            or not snapshot["eligible"]
            or snapshot["invalidation_pending"]
        ):
            raise ValueError
        if action != ("runs.pause" if control.state == "paused" else "runs.resume"):
            raise ValueError
        if snapshot["state"] != ("held" if control.state == "paused" else "running"):
            raise ValueError


def _native_event(
    connection: sqlite3.Connection,
    control: RunExecutionControl,
    prior: RunExecutionControl | None,
    scope: tuple[str, ...],
    snapshot_revision: int,
    deadline: float,
) -> tuple[int, dict[str, Any]]:
    key, native = control.operation_key, control.native
    if key is None or key[:3] != scope or native is None:
        raise ValueError
    history = stored_operation_history(
        connection, key, snapshot_revision=snapshot_revision, deadline=deadline
    )
    receipt, initial = history["receipt"], history["matching"]
    _validate_native_evidence(dict(native))
    _native_snapshot_history(
        connection, control, prior, receipt, snapshot_revision, deadline
    )
    target = receipt["target"]
    _event_target(connection, target, native=True)
    if native["attempt"] != target["expected_session"]:
        raise ValueError
    if receipt["profile"] != native["profile"] or target["run_id"] != control.run_id:
        raise ValueError
    if (
        native["digest"] != receipt["request_digest"]
        or control.pause_id != receipt["pause_id"]
    ):
        raise ValueError
    if (
        receipt["disposition"] != "accepted_pending"
        or initial is None
        or initial["stage"] != "pending"
    ):
        raise ValueError
    if native["kind"] == "intent":
        action = receipt["action"]
        expected = {
            "runs.pause": "pause_pending",
            "runs.resume": "resume_pending",
            "runs.recover": "recover_pending",
        }[action]
        if (
            control.state != expected
            or receipt["before_control_revision"] != control.control_revision - 1
            or receipt["after_control_revision"] != control.control_revision
            or target["expected_control_revision"] != control.control_revision - 1
            or receipt["after_execution_state"] != expected
        ):
            raise ValueError
        if action == "runs.recover" and (
            prior is None
            or prior.native is None
            or prior.state != "paused"
            or prior.pause_id != control.pause_id
            or prior.native["owner"] != native["owner"]
            or target["witness_digest"] != native_witness_digest(prior)
        ):
            raise ValueError
        if action == "runs.resume" and (
            prior is None
            or prior.state != "paused"
            or prior.pause_id != control.pause_id
        ):
            raise ValueError
        if (
            action == "runs.pause"
            and prior is not None
            and prior.state not in {"resumed", "superseded"}
        ):
            raise ValueError
        if (
            receipt["source_revision"] != control.source_revision + 1
            or initial["source_revision"] != control.source_revision + 2
        ):
            raise ValueError
        return initial["source_revision"], target
    if prior is None or prior.native is None or prior.pause_id != control.pause_id:
        raise ValueError
    if (
        native["owner"] != prior.native["owner"]
        or native["profile"] != prior.native["profile"]
        or native["digest"] != prior.native["digest"]
        or prior.operation_key != key
    ):
        raise ValueError
    row = connection.execute(
        "SELECT result_json FROM control_results WHERE workspace_id=? "
        "AND instance_id=? AND store_epoch=? AND caller_id=? "
        "AND operation_id=? AND result_id=?",
        (*key, f"native:{control.control_revision}"),
    ).fetchone()
    if row is None:
        raise ValueError
    result = json.loads(row[0])
    if result["source_revision"] != control.source_revision + 1 or result[
        "evidence"
    ] != dict(native):
        raise ValueError
    if native["kind"] == "settlement":
        if (
            prior.state not in {"pause_pending", "resume_pending", "recover_pending"}
            or prior.operation_key != key
        ):
            raise ValueError
        if control.state not in {"paused", "resumed", "retired", "unknown"}:
            raise ValueError
        if result["stage"] != (
            "applied" if control.state != "unknown" else "unknown_effects"
        ):
            raise ValueError
        if control.state == "retired" and (
            prior.state != "recover_pending"
            or native.get("cleanup") != "not_required"
            or native.get("owner_observation") != "not_live"
            or native.get("daemon_cleanup") != "unproved"
        ):
            raise ValueError
        if control.state == "paused" and (
            native["snapshot"]["state"] != "held"
            or native["snapshot"]["active_effects"] != 0
            or native["snapshot"].get("parked") is not True
            or not native["snapshot"]["eligible"]
            or native["snapshot"]["invalidation_pending"]
        ):
            raise ValueError
    elif native["kind"] == "aftermath":
        if control.state not in {"unknown", "unqualified"} or result["stage"] not in {
            "aftermath",
            "unknown_effects",
            "lost_continuation",
        }:
            raise ValueError
    else:
        raise ValueError
    return result["source_revision"], target


def native_intent(
    connection: sqlite3.Connection,
    state: RuntimeState,
    request: ControlRequest,
    revision: int,
    owner: dict[str, Any],
    snapshot: dict[str, Any],
) -> ControlDecision:
    if (
        connection.execute(
            "SELECT source_revision FROM control_identity WHERE id=1"
        ).fetchone()[0]
        != revision
        or load_run_controls(connection) != state.run_execution_controls
    ):
        raise ControlOperationError("run_authority_changed")
    value = request.payload
    target = value["target"]
    reason = exact_run_target_refusal(state, target)
    run = state.runs.get(target["run_id"])
    if reason is None and run is not None:
        reason = run_eligibility_refusal(state, run)
        if reason == "runner_pause_unsupported":
            # Replace only the active-state refusal with native eligibility.
            reason = None
    old = state.run_execution_controls.get(target["run_id"])
    current = 0 if old is None else old.control_revision
    if reason is not None or run is None:
        return ControlDecision(
            "rejected_no_effect", reason or "run_not_found", current, current
        )
    session = state.runner_sessions.get(run.current_session_id or "")
    if (
        session is None
        or session.state != "running"
        or snapshot["profile"] != value["profile"]
        or not snapshot["eligible"]
        or snapshot["invalidation_pending"]
    ):
        return ControlDecision(
            "rejected_no_effect", "native_profile_unqualified", current, current
        )
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    bindings = (
        []
        if admitted is None
        else [
            binding
            for binding in admitted.selected_plan.runner_bindings
            if binding.id == run.runner_binding_id
        ]
    )
    if (
        len(bindings) != 1
        or bindings[0].adapter_kind != snapshot["profile"]["selected_adapter"]
    ):
        return ControlDecision(
            "rejected_no_effect", "native_adapter_mismatch", current, current
        )
    pause_id = str(uuid4()) if value["action"] == "runs.pause" else value["pause_id"]
    native = {
        "kind": "intent",
        "owner": owner,
        "profile": value["profile"],
        "digest": request.digest,
        "snapshot": snapshot,
        "attempt": target["expected_session"],
    }
    next_state = (
        "pause_pending" if value["action"] == "runs.pause" else "resume_pending"
    )
    write_run_control(
        connection,
        RunExecutionControl(
            run.run_ref.run_id,
            current + 1,
            pause_id,
            next_state,
            "native_control_pending",
            request.key,
            0,
            _now(),
            native,
        ),
    )
    return ControlDecision(
        "accepted_pending",
        "native_control_pending",
        current,
        current + 1,
        "running" if old is None else old.state,
        next_state,
        pause_id,
    )


def native_update(
    connection: sqlite3.Connection,
    run_id: str,
    owner_id: str,
    snapshot: dict[str, Any],
    *,
    reason: str | None = None,
) -> None:
    old = load_run_controls(connection).get(run_id)
    if (
        old is None
        or old.native is None
        or old.native["snapshot"]["owner_id"] != owner_id
    ):
        raise ControlOperationError("native_owner_mismatch")
    key = old.operation_key
    assert key is not None
    pending = old.state in {"pause_pending", "resume_pending", "recover_pending"}
    native = dict(old.native)
    if reason is None and old.state in {"pause_pending", "resume_pending"}:
        session_row = connection.execute(
            "SELECT current_session_id FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        session_id = None if session_row is None else session_row[0]
        if (
            connection.execute(
                "SELECT 1 FROM runner_session_cancellation_requests WHERE session_id=?",
                (session_id,),
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM runner_session_completions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        ):
            reason = "core_authority_preempted"
    if reason is not None:
        if old.state in {"unknown", "retired"}:
            return
        native.update(kind="aftermath", aftermath_reason=reason)
        if not snapshot["eligible"]:
            native["snapshot"] = snapshot
        state = (
            "unqualified"
            if reason.startswith("unsupported_")
            and not pending
            and old.state == "resumed"
            else "unknown"
        )
        stage = "unknown_effects" if pending else "aftermath"
    else:
        if not pending:
            return
        operation = snapshot["operations"].get(canonical_json(list(key)))
        if operation is None or operation["stage"] == "accepted_pending":
            return
        state = (
            ("paused" if old.state == "pause_pending" else "resumed")
            if operation["stage"] == "applied"
            else "unknown"
        )
        stage = "applied" if state != "unknown" else "unknown_effects"
        native.update(kind="settlement", snapshot=snapshot)
    record = replace(
        old,
        control_revision=old.control_revision + 1,
        state=state,
        cause="native_control_" + state,
        native=native,
    )
    write_run_control(connection, record)
    append_native_result(
        connection,
        key,
        result_id=f"native:{record.control_revision}",
        stage=stage,
        evidence=native,
    )


def native_witness_digest(control: RunExecutionControl) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "source_revision": control.source_revision,
                "control_revision": control.control_revision,
                "pause_id": control.pause_id,
                "native": dict(control.native or {}),
            }
        ).encode()
    ).hexdigest()


def native_recovery_target_refusal(
    connection: sqlite3.Connection,
    request: ControlRequest,
    state: RuntimeState,
    revision: int,
) -> str | None:
    target = request.payload["target"]
    if (
        connection.execute(
            "SELECT source_revision FROM control_identity WHERE id=1"
        ).fetchone()[0]
        != revision
        or target["expected_source_revision"] != revision
        or load_run_controls(connection) != state.run_execution_controls
    ):
        return "native_recovery_source_changed"
    reason = exact_run_target_refusal(state, target)
    if reason is not None:
        return reason
    old = state.run_execution_controls.get(target["run_id"])
    if old is None or old.native is None:
        return "native_recovery_witness_unavailable"
    if (
        old.native["owner"]["owner_id"] != target["owner_id"]
        or native_witness_digest(old) != target["witness_digest"]
        or old.native["profile"] != request.payload["profile"]
        or old.pause_id != request.payload["pause_id"]
    ):
        return "native_recovery_witness_mismatch"
    return None


def native_recovery_observe_loss(
    connection: sqlite3.Connection,
    request: ControlRequest,
    state: RuntimeState,
    revision: int,
) -> ControlDecision:
    """Fence fresh loss observation under the exact accepted native control."""
    reason = native_recovery_target_refusal(connection, request, state, revision)
    if reason is not None:
        return ControlDecision("rejected_no_effect", reason)
    old = state.run_execution_controls[request.payload["target"]["run_id"]]
    assert old.native is not None and old.operation_key is not None
    if old.state not in {"pause_pending", "resume_pending", "unknown", "unqualified"}:
        return ControlDecision(
            "rejected_no_effect", "native_recovery_authority_changed"
        )
    native_update(
        connection,
        old.run_id,
        old.native["owner"]["owner_id"],
        dict(old.native["snapshot"]),
        reason="native_owner_lost_unknown_effects",
    )
    original = stored_operation_history(
        connection,
        old.operation_key,
        snapshot_revision=connection.execute(
            "SELECT source_revision FROM control_identity WHERE id=1"
        ).fetchone()[0],
        deadline=time.monotonic() + 1,
    )
    observation: dict[str, Any] = {
        "operation_key": list(old.operation_key),
        "result_id": "native-loss-observed:" + request.digest,
        "owner_id": old.native["owner"]["owner_id"],
        "session": request.payload["target"]["expected_session"],
        "recovery_request_key": list(request.key),
        "recovery_request_digest": request.digest,
        "authority": "original_accepted_control_aftermath",
        "control_action": original["receipt"]["action"],
        "native_retirement": "refused",
    }
    append_native_result(
        connection,
        old.operation_key,
        result_id=observation["result_id"],
        stage="aftermath",
        evidence=observation,
    )
    return ControlDecision(
        "rejected_no_effect",
        "native_recovery_witness_unavailable_loss_observed",
        old.control_revision,
        old.control_revision,
        old.state,
        old.state,
        old.pause_id,
        {"owner_loss_observation": observation},
    )


def native_recovery_intent(
    connection: sqlite3.Connection,
    request: ControlRequest,
    state: RuntimeState,
    revision: int,
) -> ControlDecision:
    target = request.payload["target"]
    old = state.run_execution_controls.get(target["run_id"])
    reason = native_recovery_target_refusal(connection, request, state, revision)
    if reason is None and (old is None or old.native is None or old.state != "paused"):
        reason = "native_recovery_witness_unavailable"
    if reason is not None:
        return ControlDecision("rejected_no_effect", reason)
    assert old is not None and old.native is not None
    assert old.operation_key is not None
    append_native_result(
        connection,
        old.operation_key,
        result_id="native-owner-loss:" + request.digest,
        stage="aftermath",
        evidence={
            "continuation": "lost",
            "owner_observation": "not_live",
            "recovery_operation": list(request.key),
        },
    )
    native = dict(old.native)
    native.update(
        kind="intent",
        digest=request.digest,
        recovery_witness=target["witness_digest"],
        owner_observation="not_live",
        cleanup="pending",
        daemon_cleanup="unproved",
    )
    write_run_control(
        connection,
        replace(
            old,
            control_revision=old.control_revision + 1,
            state="recover_pending",
            cause="native_retirement_pending",
            operation_key=request.key,
            native=native,
        ),
    )
    return ControlDecision(
        "accepted_pending",
        "native_retirement_pending",
        old.control_revision,
        old.control_revision + 1,
        "paused",
        "recover_pending",
        old.pause_id,
    )


def native_recovery_finish(
    connection: sqlite3.Connection, request: ControlRequest
) -> None:
    target = request.payload["target"]
    old = load_run_controls(connection).get(target["run_id"])
    if old is None or old.native is None or old.operation_key != request.key:
        raise ControlOperationError("native_recovery_authority_changed")
    if old.state == "retired":
        return
    if old.state != "recover_pending":
        raise ControlOperationError("native_recovery_authority_changed")
    session = target["expected_session"]["session_id"]
    row = connection.execute(
        "SELECT state FROM runner_sessions WHERE session_id=?", (session,)
    ).fetchone()
    if (
        row != ("lost",)
        or connection.execute(
            "SELECT 1 FROM runner_session_cancellation_requests WHERE session_id=?",
            (session,),
        ).fetchone()
    ):
        raise ControlOperationError("native_recovery_authority_changed")
    native = dict(old.native)
    native.update(
        kind="settlement",
        cleanup="not_required",
        continuation="irreversibly_retired",
        basis="held-owned-http-tasks-settled-no-process-entry-no-finalization-release",
    )
    record = replace(
        old,
        control_revision=old.control_revision + 1,
        state="retired",
        cause="native_continuation_retired",
        native=native,
    )
    write_run_control(connection, record)
    append_native_result(
        connection,
        request.key,
        result_id=f"native:{record.control_revision}",
        stage="applied",
        evidence=native,
    )


def _validate_native_evidence(native: dict[str, Any]) -> None:
    allowed = {
        "attempt",
        "kind",
        "owner",
        "profile",
        "digest",
        "snapshot",
        "aftermath_reason",
        "recovery_witness",
        "owner_observation",
        "cleanup",
        "daemon_cleanup",
        "continuation",
        "basis",
    }
    if set(native) - allowed or not {
        "attempt",
        "kind",
        "owner",
        "profile",
        "digest",
        "snapshot",
    } <= set(native):
        raise ValueError
    digest_field(native["digest"])
    owner = native["owner"]
    if set(owner) != {"owner_id", "process"}:
        raise ValueError
    uuid_field(owner["owner_id"])
    process = owner["process"]
    if (
        set(process) != {"status", "pid", "uid", "birth", "boot"}
        or process["status"] != "live"
    ):
        raise ValueError
    if revision_field(process["pid"]) == 0:
        raise ValueError
    revision_field(process["uid"])
    for field in ("birth", "boot"):
        if type(process[field]) is not list or len(process[field]) != 2:
            raise ValueError
        for part in process[field]:
            revision_field(part)
    snapshot = native["snapshot"]
    if set(snapshot) != {
        "owner_id",
        "profile",
        "eligible",
        "invalidation_pending",
        "state",
        "reason",
        "sequence",
        "active_effects",
        "parked",
        "effect_counts",
        "os_descendants",
        "descendant_boundary",
        "session_id",
        "invocation_id",
        "pause_id",
        "pending",
        "operations",
    }:
        raise ValueError
    if (
        snapshot["owner_id"] != owner["owner_id"]
        or snapshot["profile"] != native["profile"]
        or type(snapshot["eligible"]) is not bool
        or type(snapshot["invalidation_pending"]) is not bool
        or type(snapshot["parked"]) is not bool
    ):
        raise ValueError
    if snapshot["state"] not in {"running", "held", "pause_pending", "closed"}:
        raise ValueError
    if (
        type(native["profile"]["selected_adapter"]) is not str
        or not native["profile"]["selected_adapter"]
    ):
        raise ValueError
    expected_descendants: tuple[str, list[Any] | None] = (
        ("no-processes-before-unsupported-entry", [])
        if snapshot["eligible"]
        else ("unknown_after_unqualified_entry", None)
    )
    if (
        snapshot["descendant_boundary"],
        snapshot["os_descendants"],
    ) != expected_descendants:
        raise ValueError
    for field in ("sequence", "active_effects", "invocation_id"):
        revision_field(snapshot[field])
    uuid_field(snapshot["session_id"])
    if set(snapshot["effect_counts"]) - {"model", "tool"}:
        raise ValueError
    for count in snapshot["effect_counts"].values():
        revision_field(count)
    if snapshot["pause_id"] is not None:
        uuid_field(snapshot["pause_id"])
    if snapshot["invocation_id"] == 0:
        raise ValueError
    if type(snapshot["operations"]) is not dict or len(snapshot["operations"]) > 2:
        raise ValueError
    for operation in snapshot["operations"].values():
        if set(operation) not in (
            {"digest", "stage"},
            {"digest", "stage", "sequence"},
        ) or operation["stage"] not in {"accepted_pending", "applied", "unknown"}:
            raise ValueError
        digest_field(operation["digest"])
        if operation["stage"] == "accepted_pending":
            if set(operation) != {"digest", "stage"}:
                raise ValueError
        elif revision_field(operation["sequence"]) > snapshot["sequence"]:
            raise ValueError
    pending = snapshot["pending"]
    pending_keys = [
        key
        for key, operation in snapshot["operations"].items()
        if operation["stage"] == "accepted_pending"
    ]
    if pending_keys != ([] if pending is None else [pending]):
        raise ValueError
    if (snapshot["state"] == "pause_pending") != (pending is not None):
        raise ValueError
