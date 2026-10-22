"""Exact request envelopes and durable ordered receipts, without execution policy."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

from .ownership import check_id
from .records import RouterError, fields, identifier, mapping, number, text

if TYPE_CHECKING:
    from .store import Store

OPERATIONS = frozenset(
    {
        "start_run",
        "recover_run",
        "pause_run",
        "cancel_run",
        "resume_run",
        "submit_revision",
        "accept_revision",
        "retry_invocation",
        "pause_plane",
        "resume_plane",
        "cancel_plane",
        "redirect_plane",
        "reconcile_invocation",
        "update_instance_config",
    }
)
STOPS = frozenset({"pause_run", "cancel_run", "pause_plane", "cancel_plane"})
PLANE_OPS = frozenset(
    {
        "pause_plane",
        "resume_plane",
        "cancel_plane",
        "redirect_plane",
        "retry_invocation",
        "reconcile_invocation",
    }
)
INSTANCE_OPS = frozenset({"start_run", "update_instance_config"})


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def envelope(
    operation: str,
    instance_id: str,
    run_id: str | None,
    request_id: str,
    expected_state_version: int | None,
    payload: dict[str, object],
    *,
    plane_id: str | None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "format": "millrace-router-request/v2",
        "operation": operation,
        "instance_id": instance_id,
        "run_id": run_id,
        "plane_id": plane_id,
        "request_id": request_id,
        "expected_state_version": expected_state_version,
        "payload": payload,
    }
    validate_envelope(result)
    return result


def validate_envelope(value: object, *, creation: bool = False) -> dict[str, object]:
    obj = fields(
        value,
        {
            "format",
            "operation",
            "instance_id",
            "run_id",
            "plane_id",
            "request_id",
            "expected_state_version",
            "payload",
        },
    )
    try:
        canonical(obj)
    except ValueError as exc:
        raise RouterError("nonfinite_json") from exc
    except TypeError as exc:
        raise RouterError("invalid_json") from exc
    check_id(cast(str, obj["request_id"]))
    if obj["format"] != "millrace-router-request/v2":
        raise RouterError("invalid_request")
    op = text(obj["operation"])
    if op == "create_instance" and creation:
        if obj["instance_id"] is not None or obj["run_id"] is not None:
            raise RouterError("invalid_target")
    else:
        if op not in OPERATIONS and op != "release_project_hold":
            raise RouterError("invalid_operation")
        check_id(cast(str, obj["instance_id"]))
        if op in INSTANCE_OPS:
            if obj["run_id"] is not None:
                raise RouterError("invalid_target")
        else:
            check_id(cast(str, obj["run_id"]))
    if op in PLANE_OPS:
        identifier(obj["plane_id"])
    elif obj["plane_id"] is not None:
        raise RouterError("invalid_target")
    expected = obj["expected_state_version"]
    if op in STOPS or op == "release_project_hold":
        if expected is not None:
            raise RouterError("invalid_version")
    elif type(expected) is not int or expected < (0 if creation else 1):
        raise RouterError("invalid_version")
    body = mapping(obj["payload"])
    if op == "start_run":
        item = fields(body, {"edit_mode"})
        if item["edit_mode"] not in {"locked", "live"}:
            raise RouterError("invalid_edit_mode")
    elif op in {
        "recover_run",
        "pause_run",
        "cancel_run",
        "resume_run",
        "pause_plane",
        "cancel_plane",
        "resume_plane",
        "submit_revision",
    }:
        fields(body, set())
    elif op == "accept_revision":
        item = fields(body, {"candidate_id"})
        if not isinstance(item["candidate_id"], str) or len(item["candidate_id"]) != 64:
            raise RouterError("invalid_revision")
    elif op in {"retry_invocation", "redirect_plane", "reconcile_invocation"}:
        key = "node_id" if op == "redirect_plane" else "invocation_id"
        item = fields(
            body,
            {key, "reason"} | ({"stopped"} if op == "reconcile_invocation" else set()),
        )
        text(item["reason"])
        if key == "node_id":
            identifier(item[key])
        else:
            check_id(cast(str, item[key]))
        if op == "reconcile_invocation" and item["stopped"] is not True:
            raise RouterError("writers_not_confirmed")
    elif op == "release_project_hold":
        item = fields(body, {"project_root", "hold_id", "reason", "stopped"})
        text(item["project_root"])
        check_id(cast(str, item["hold_id"]))
        text(item["reason"])
        if item["stopped"] is not True:
            raise RouterError("writers_not_confirmed")
    elif op == "update_instance_config":
        fields(body, {"settings"})
        fields(
            body["settings"],
            set(),
            {
                "path",
                "env_allowlist",
                "host_paths",
                "runtime_defaults",
                "operator_policy",
            },
        )
    elif op == "create_instance":
        fields(
            body,
            {"workflow_file", "project_root", "path", "env_allowlist", "host_paths"},
            {"runtime_defaults", "operator_policy"},
        )
    return obj


def receipt_base(request: dict[str, object], status: str) -> dict[str, object]:
    return {
        "request_id": request["request_id"],
        "instance_id": request["instance_id"],
        "run_id": request["run_id"],
        "plane_id": request["plane_id"],
        "operation": request["operation"],
        "request_digest": digest(request),
        "status": status,
    }


def lookup(store: Store, request: dict[str, object]) -> dict[str, object] | None:
    row = store.db.execute(
        "SELECT body,receipt FROM controls WHERE request_id=?", (request["request_id"],)
    ).fetchone()
    if row is None:
        return None
    if row["body"] != canonical(request):
        raise RouterError("request_id_reused")
    from .records import strict_json

    return mapping(strict_json(str(row["receipt"])))


def append(store: Store, request: dict[str, object]) -> dict[str, object]:
    old = lookup(store, request)
    if old is not None:
        return old
    queued = receipt_base(request, "queued")
    with store.transaction():
        store.db.execute(
            "INSERT INTO "
            "controls(request_id,run_id,plane_id,operation,expected_version,"
            "body,status,receipt) "
            "VALUES(?,?,?,?,?,?,'queued',?)",
            (
                request["request_id"],
                request["run_id"],
                request["plane_id"],
                request["operation"],
                request["expected_state_version"],
                canonical(request),
                canonical(queued),
            ),
        )
    return queued


def finish(
    store: Store,
    request: dict[str, object],
    *,
    code: str | None = None,
    retryable: bool = False,
    result: dict[str, object] | None = None,
) -> dict[str, object]:
    current = lookup(store, request)
    if current is None or current["status"] != "queued":
        raise RouterError("control_integrity")
    receipt = receipt_base(request, "refused" if code else "applied")
    receipt.update(result or {})
    receipt.update(code=code, retryable=retryable)
    if request["run_id"] is not None:
        try:
            receipt["state_version"] = store.run(cast(str, request["run_id"]))[
                "state_version"
            ]
        except RouterError:
            receipt["state_version"] = None
    else:
        receipt.setdefault("state_version", store.instance_settings()["state_version"])
    store.db.execute(
        "UPDATE controls SET status=?,receipt=? WHERE request_id=?",
        (receipt["status"], canonical(receipt), request["request_id"]),
    )
    store.db.execute(
        "INSERT INTO final_receipts VALUES(?,?)",
        (request["request_id"], canonical(receipt)),
    )
    return receipt


def validate_requests(store: Store) -> None:
    from .records import strict_json

    snapshot = not store.db.in_transaction
    try:
        if snapshot:
            store.db.execute("BEGIN")
        for row in store.db.execute("SELECT * FROM controls ORDER BY seq"):
            request = validate_envelope(strict_json(str(row["body"])))
            receipt = mapping(strict_json(str(row["receipt"])))
            if request["instance_id"] != store.config.instance_id or (
                request["request_id"],
                request["run_id"],
                request["plane_id"],
                request["operation"],
                request["expected_state_version"],
            ) != (
                row["request_id"],
                row["run_id"],
                row["plane_id"],
                row["operation"],
                row["expected_version"],
            ):
                raise RouterError("control_integrity")
            if canonical(request) != row["body"]:
                raise RouterError("control_integrity")
            for key, value in receipt_base(request, str(row["status"])).items():
                if (
                    key == "run_id"
                    and request["operation"] == "start_run"
                    and row["status"] == "applied"
                ):
                    created = store.run(str(receipt.get("run_id")))
                    if created.get("start_request_id") != request["request_id"]:
                        raise RouterError("control_integrity")
                    continue
                if receipt.get(key) != value:
                    raise RouterError("control_integrity")
            final = store.db.execute(
                "SELECT receipt FROM final_receipts WHERE request_id=?",
                (row["request_id"],),
            ).fetchone()
            if row["status"] == "queued":
                if final is not None or receipt != receipt_base(request, "queued"):
                    raise RouterError("control_integrity")
            elif row["status"] in {"applied", "refused"}:
                if (
                    final is None
                    or final[0] != row["receipt"]
                    or type(receipt.get("retryable")) is not bool
                ):
                    raise RouterError("control_integrity")
                if row["status"] == "refused" and not isinstance(
                    receipt.get("code"), str
                ):
                    raise RouterError("control_integrity")
                if row["status"] == "applied" and receipt.get("code") is not None:
                    raise RouterError("control_integrity")
                if receipt.get("state_version") is not None:
                    number(receipt["state_version"], integer=True)
            else:
                raise RouterError("control_integrity")
    except (RouterError, KeyError, TypeError, ValueError) as exc:
        raise RouterError("control_integrity") from exc
    finally:
        if snapshot and store.db.in_transaction:
            store.db.execute("ROLLBACK")


def validate_stop_history(
    store: Store, run_id: str, status_at: dict[int, tuple[str, object]]
) -> None:
    """Replay stop and reconciliation context at each receipt application."""
    from .records import strict_json

    for item in store.db.execute(
        "SELECT operation,receipt FROM controls WHERE run_id=? AND status='applied'",
        (run_id,),
    ):
        receipt = mapping(strict_json(str(item["receipt"])))
        prior = status_at[
            max(v for v in status_at if v <= cast(int, receipt["state_version"]))
        ]
        if item["operation"] == "reconcile_invocation" and prior[0] != "paused":
            raise RouterError("control_integrity")
        if (
            item["operation"] == "pause_run"
            and receipt.get("action") in {"paused", "already_paused"}
            and prior[0] != "paused"
        ):
            raise RouterError("control_integrity")
        if (
            item["operation"] != "cancel_run"
            or receipt.get("action") != "cancel_requested"
        ):
            continue
        if "active_invocation_id" not in receipt:
            raise RouterError("control_integrity")
        active_id = receipt["active_invocation_id"]
        if active_id is None:
            if prior != ("paused", "cancellation_unresolved"):
                raise RouterError("control_integrity")
            continue
        if not isinstance(active_id, str) or (
            prior != ("paused", "operator_pause") and prior[0] != "running"
        ):
            raise RouterError("control_integrity")
        invocation = store.db.execute(
            "SELECT body FROM invocations WHERE id=? AND run_id=?", (active_id, run_id)
        ).fetchone()
        version = cast(int, receipt["state_version"])
        if (
            invocation is None
            or cast(int, mapping(strict_json(str(invocation[0])))["version"]) >= version
            or store.db.execute(
                "SELECT 1 FROM transitions WHERE completion_id=? AND version<=? "
                "UNION ALL SELECT 1 FROM moves WHERE completion_id=? AND version<=?",
                (active_id, version, active_id, version),
            ).fetchone()
            is not None
        ):
            raise RouterError("control_integrity")
