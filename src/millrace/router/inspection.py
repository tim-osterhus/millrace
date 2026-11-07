"""Bounded public read-only inspection of one Core state home.

`millrace-router-inspection/v1` projects validated instance state for operator
clients. Each read first accounts the selected instance store inside an explicit
read-only snapshot, before anything is decoded, then runs Core's normal
whole-instance integrity validation in that same snapshot and returns an
allowlisted projection. Nothing is created, captured, locked or mutated, and no
adapter readiness or provider probe runs. The budgets below bound this reader's
work only; they are not workflow, runtime or history capacity limits.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from .execution import AdapterResolver
from .ownership import canonical_path, check_id
from .records import Action, Graph, Node, RouterError, mapping, strict_json
from .store import Store

INSPECTION_CONTRACT = "millrace-router-inspection/v1"

__all__ = [
    "INSPECTION_CONTRACT",
    "list_instances_page",
    "inspect_instance_page",
    "inspect_run_page",
    "inspect_revision_graph",
    "validate_revision_summary",
]

# Fixed reader budgets. Module constants so tests can lower them.
PAGE_LIMIT = 50
INVENTORY_ENTRIES = 4096
MAX_PLANES = 128
MAX_NODES = 512
MAX_GRAPH_ITEMS = 4096
MAX_LIST_ENTRIES = 256
MAX_SEAM_ENTRIES = 4096
RESULT_BYTES = 1_048_576
INSTANCE_ROWS = 100_000
INSTANCE_BYTES = 32 * 1_048_576
INPUT_BYTES = 4 * 1_048_576
PAGE_ROWS = 200_000
PAGE_BYTES = 64 * 1_048_576

_SECTIONS = frozenset({"summary", "invocations", "transitions", "controls"})
_CURSOR_TEXT = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CURSOR = re.compile(r"([a-z])([0-9a-f]{32})_([A-Za-z0-9_-]+)\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_COMMON = ("binding", "timeout_seconds")
_POLICY = ("binding_pin", "timeout_ceiling_seconds")

_EVENTS = (
    "SELECT * FROM ("
    "SELECT 'transition' AS event_kind, t.completion_id AS event_id, "
    "t.version AS version, t.plane_id AS plane_id, t.completion_id AS completion_id, "
    "t.node AS node_id, c.revision_id AS producing_revision_id, "
    "t.revision_id AS routing_revision_id, c.outcome AS outcome, t.action AS action, "
    "t.target AS target, t.retry_delta AS retry_delta, NULL AS move_kind, "
    "NULL AS source_invocation_id, NULL AS request_id "
    "FROM transitions t JOIN completions c ON c.invocation_id=t.completion_id "
    "WHERE t.run_id=? "
    "UNION ALL "
    "SELECT 'move', m.request_id, m.version, m.plane_id, m.completion_id, NULL, "
    "NULL, m.revision_id, NULL, NULL, m.target, NULL, m.kind, "
    "m.source_invocation, m.request_id FROM moves m WHERE m.run_id=?) "
)
_EVENT_ORDER = "ORDER BY version DESC, plane_id ASC, event_kind ASC, event_id ASC "


class _Preflight:
    """Account one snapshot's stored rows and TEXT/BLOB octets before decoding.

    Octets are SQLite's stored UTF-8 bytes (`length(CAST(value AS BLOB))`), not
    characters. Scans stop as soon as a limit is exceeded and parse no JSON.
    """

    def __init__(self) -> None:
        self.rows = 0
        self.octets = 0

    def file(self, size: int) -> None:
        self.octets += size
        if self.octets > INSTANCE_BYTES:
            raise RouterError("inspection_too_large")

    def __call__(self, connection: sqlite3.Connection) -> None:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND substr(name,1,7)<>'sqlite_' ORDER BY name"
            )
        ]
        for table in tables:
            name = _quote(table)
            columns = [
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({name})")
            ]
            size = " + ".join(_octets(column) for column in columns) or "0"
            body = _octets("body") if table == "revisions" else "0"
            for row in connection.execute(f"SELECT {size}, {body} FROM {name}"):
                self.rows += 1
                self.octets += int(row[0])
                if (
                    self.rows > INSTANCE_ROWS
                    or self.octets > INSTANCE_BYTES
                    or int(row[1]) > INPUT_BYTES
                ):
                    raise RouterError("inspection_too_large")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _octets(column: str) -> str:
    name = _quote(column)
    return (
        f"(CASE WHEN typeof({name}) IN ('text','blob') "
        f"THEN length(CAST({name} AS BLOB)) ELSE 0 END)"
    )


def _open(
    instance_id: str,
    state_home: Path,
    adapter_resolver: AdapterResolver | None,
    work: _Preflight | None = None,
) -> Store:
    """Bounded load snapshot, then the validated Store snapshot.

    `work` receives the larger of the two snapshots' accounted work, also on
    refusal, so inventory pages can charge actual verification work.
    """
    # Imported here: `control` re-exports this module's functions.
    from .control import _load_instance

    loader, reader = _Preflight(), _Preflight()
    try:
        config = _load_instance(
            instance_id,
            state_home,
            adapter_resolver=adapter_resolver,
            read_budget=loader,
        )
        return Store(
            config,
            readonly=True,
            adapter_resolver=adapter_resolver,
            read_budget=reader,
        )
    finally:
        if work is not None:
            work.rows = max(loader.rows, reader.rows)
            work.octets = max(loader.octets, reader.octets)


def _limit(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= PAGE_LIMIT
    ):
        raise RouterError("invalid_request")
    return value


def _cursor_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _CURSOR_TEXT.fullmatch(value) is None:
        raise RouterError("invalid_request")
    return value


def _binding(**values: object) -> str:
    raw = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cursor_key(cursor: str, prefix: str, binding: str) -> str:
    match = _CURSOR.fullmatch(cursor)
    if match is None or match.group(1) != prefix:
        raise RouterError("invalid_request")
    if match.group(2) != binding:
        raise RouterError("inspection_changed")
    return match.group(3)


def _decimal(key: str) -> int:
    if not key.isdecimal():
        raise RouterError("invalid_request")
    return int(key)


def _result(
    kind: str,
    instance_id: str | None,
    run_id: str | None,
    state_version: object,
    items: list[dict[str, object]],
    next_cursor: str | None,
    data: dict[str, object],
) -> dict[str, object]:
    result: dict[str, object] = {
        "format": INSPECTION_CONTRACT,
        "kind": kind,
        "instance_id": instance_id,
        "run_id": run_id,
        "state_version": state_version,
        "items": items,
        "next_cursor": next_cursor,
        "data": data,
    }
    encoded = json.dumps(
        result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    if len(encoded.encode()) > RESULT_BYTES:
        raise RouterError("inspection_too_large")
    return result


def _pick(value: object, keys: Iterable[str]) -> dict[str, object]:
    source = mapping(value)
    return {key: source[key] for key in keys if key in source}


def _code(value: object, field: str, unavailable_reason: dict[str, str]) -> str | None:
    """Keep code-shaped diagnostics; record why free text is withheld."""
    if value is None or value == "":
        return None
    if isinstance(value, str) and _CODE.fullmatch(value) is not None:
        return value
    unavailable_reason[field] = "not_a_code"
    return None


def list_instances_page(
    *,
    state_home: Path,
    limit: int = 50,
    cursor: str | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    """Page configured instances; each item is a separate bounded observation."""
    size = _limit(limit)
    text = _cursor_text(cursor)
    home = canonical_path(state_home)
    binding = _binding(kind="instances", state_home=str(home))
    after: str | None = None
    if text is not None:
        after = _cursor_key(text, "i", binding)
        if _ID.fullmatch(after) is None:
            raise RouterError("invalid_request")
    folder = home / "instances"
    names: list[str] = []
    if folder.exists():
        for entry in folder.iterdir():
            if entry.name == ".DS_Store":
                try:
                    mode = entry.lstat().st_mode
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise RouterError("instance_integrity") from exc
                if stat.S_ISREG(mode):
                    continue
            names.append(entry.name)
            if len(names) > INVENTORY_ENTRIES:
                raise RouterError("inspection_too_large")
    names.sort()
    for name in names:
        check_id(name)
    pending = [name for name in names if after is None or name > after]
    items: list[dict[str, object]] = []
    rows, octets = PAGE_ROWS, PAGE_BYTES
    last: str | None = None
    for name in pending:
        if len(items) == size or (
            items and (rows < INSTANCE_ROWS or octets < INSTANCE_BYTES)
        ):
            break
        work = _Preflight()
        try:
            store = _open(name, state_home, adapter_resolver, work)
        except RouterError as exc:
            items.append(_instance_item(name, None, exc.code))
        else:
            try:
                items.append(_instance_item(name, store, None))
            finally:
                store.close()
        rows -= work.rows
        octets -= work.octets
        last = name
    following = last is not None and pending[-1] != last
    next_cursor = f"i{binding}_{last}" if following else None
    return _result("instances", None, None, None, items, next_cursor, {})


def _instance_item(
    instance_id: str, store: Store | None, code: str | None
) -> dict[str, object]:
    if store is None:
        return {
            "instance_id": instance_id,
            "settings_version": None,
            "project_root": None,
            "workflow_file": None,
            "store_format": None,
            "error_code": code,
        }
    return {
        "instance_id": instance_id,
        "settings_version": store.instance_settings()["state_version"],
        "project_root": str(store.config.project_root),
        "workflow_file": str(store.config.workflow_file),
        "store_format": store._format,
        "error_code": None,
    }


def inspect_instance_page(
    instance_id: str,
    *,
    state_home: Path,
    limit: int = 50,
    cursor: str | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    """Instance identity, common settings and runs newest first."""
    size = _limit(limit)
    text = _cursor_text(cursor)
    store = _open(instance_id, state_home, adapter_resolver)
    try:
        settings = store.instance_settings()
        version = settings["state_version"]
        binding = _binding(
            kind="instance",
            instance_id=instance_id,
            run_id=None,
            section="runs",
            state_version=version,
        )
        clause = ""
        params: tuple[object, ...] = ()
        if text is not None:
            key = _cursor_key(text, "c", binding)
            if _ID.fullmatch(key) is None:
                raise RouterError("invalid_request")
            row = store.db.execute(
                "SELECT rowid FROM runs WHERE id=?", (key,)
            ).fetchone()
            if row is None:
                raise RouterError("inspection_changed")
            clause, params = "WHERE rowid<? ", (row[0],)
        rows = list(
            store.db.execute(
                "SELECT id,body FROM runs " + clause + "ORDER BY rowid DESC LIMIT ?",
                (*params, size + 1),
            )
        )
        items = [_run_item(mapping(strict_json(str(row[1])))) for row in rows[:size]]
        next_cursor = f"c{binding}_{rows[size - 1][0]}" if len(rows) > size else None
        data: dict[str, object] = {
            "instance_id": instance_id,
            "project_root": str(store.config.project_root),
            "workflow_file": str(store.config.workflow_file),
            "settings_version": version,
            "runtime_defaults": _pick(settings["runtime_defaults"], _COMMON),
            "operator_policy": _pick(settings["operator_policy"], _POLICY),
        }
        return _result("instance", instance_id, None, version, items, next_cursor, data)
    finally:
        store.close()


def _run_item(run: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": run["id"],
        "status": run["status"],
        "mode": run["mode"],
        "state_version": run["state_version"],
        "active_revision_id": run["revision_id"],
        "pause_reason": run.get("pause_reason"),
    }


def inspect_run_page(
    instance_id: str,
    run_id: str | None,
    *,
    state_home: Path,
    section: str = "summary",
    limit: int = 50,
    cursor: str | None = None,
    request_id: str | None = None,
    invocation_id: str | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    """One run section: summary, or newest-first history with stable-key cursors.

    `request_id` selects zero/one exact receipt (controls only, no cursor; the run
    may be null). `invocation_id` selects zero/one exact invocation of this run
    (invocations only, no cursor).
    """
    if not isinstance(section, str) or section not in _SECTIONS:
        raise RouterError("invalid_request")
    size = _limit(limit)
    text = _cursor_text(cursor)
    if request_id is not None and (section != "controls" or text is not None):
        raise RouterError("invalid_request")
    if invocation_id is not None and (
        section != "invocations" or text is not None or run_id is None
    ):
        raise RouterError("invalid_request")
    if run_id is None and request_id is None:
        raise RouterError("invalid_request")
    if section == "summary" and text is not None:
        raise RouterError("invalid_request")
    for value in (run_id, request_id, invocation_id):
        if value is not None:
            check_id(value)
    store = _open(instance_id, state_home, adapter_resolver)
    try:
        if run_id is None:
            return _receipt(store, instance_id, None, cast(str, request_id))
        run = store.run(run_id)
        version = run["state_version"]
        if section == "summary":
            data = _summary(store, run)
            return _result("run-summary", instance_id, run_id, version, [], None, data)
        if request_id is not None:
            return _receipt(store, instance_id, run, request_id)
        binding = _binding(
            kind=section,
            instance_id=instance_id,
            run_id=run_id,
            section=section,
            state_version=version,
        )
        key = None if text is None else _cursor_key(text, "c", binding)
        if section == "invocations":
            items, last = _invocations(store, run_id, size, key, invocation_id)
        elif section == "transitions":
            items, last = _events(store, run_id, size, key)
        else:
            items, last = _controls(store, run_id, size, key)
        next_cursor = None if last is None else f"c{binding}_{last}"
        return _result(section, instance_id, run_id, version, items, next_cursor, {})
    finally:
        store.close()


def _invocations(
    store: Store, run_id: str, size: int, key: str | None, invocation_id: str | None
) -> tuple[list[dict[str, object]], str | None]:
    if invocation_id is not None:
        rows = store.db.execute(
            "SELECT * FROM invocations WHERE id=? AND run_id=?", (invocation_id, run_id)
        ).fetchall()
        return [_invocation(store, row) for row in rows], None
    clause = ""
    params: tuple[object, ...] = ()
    if key is not None:
        attempt = _decimal(key)
        if (
            store.db.execute(
                "SELECT 1 FROM invocations WHERE run_id=? AND attempt=?",
                (run_id, attempt),
            ).fetchone()
            is None
        ):
            raise RouterError("inspection_changed")
        clause, params = "AND attempt<? ", (attempt,)
    rows = store.db.execute(
        "SELECT * FROM invocations WHERE run_id=? "
        + clause
        + "ORDER BY attempt DESC LIMIT ?",
        (run_id, *params, size + 1),
    ).fetchall()
    items = [_invocation(store, row) for row in rows[:size]]
    return items, str(rows[size - 1]["attempt"]) if len(rows) > size else None


def _invocation(store: Store, row: sqlite3.Row) -> dict[str, object]:
    body = mapping(strict_json(str(row["body"])))
    invocation_id = str(row["id"])
    completion = store.db.execute(
        "SELECT transport,exit_code,classification,outcome,diagnostic,writer "
        "FROM completions WHERE invocation_id=?",
        (invocation_id,),
    ).fetchone()
    fact = store.db.execute(
        "SELECT request_id,version,writer FROM writer_facts WHERE invocation_id=?",
        (invocation_id,),
    ).fetchone()
    launch = body.get("launch_settings")
    unavailable_reason: dict[str, str] = {}
    item: dict[str, object] = {
        "invocation_id": invocation_id,
        "attempt": row["attempt"],
        "plane_id": row["plane_id"],
        "node_id": row["node"],
        "status": row["status"],
        "producing_revision_id": row["revision_id"],
        "created_at": body.get("created_at"),
        "deadline": body.get("deadline"),
        "run_deadline": body.get("run_deadline"),
        "started_version": body.get("started_version"),
        "completed_version": body.get("completed_version"),
        "parsed_version": body.get("parsed_version"),
        "result_contract": body.get("result_contract"),
        "uncertainty_diagnostic": _code(
            body.get("uncertainty_diagnostic"),
            "uncertainty_diagnostic",
            unavailable_reason,
        ),
        "writer": {
            "original": None if completion is None else completion["writer"],
            "effective": store.writer(invocation_id),
        },
        "completion": None
        if completion is None
        else {
            "transport": completion["transport"],
            "exit_code": completion["exit_code"],
            "classification": completion["classification"],
            "outcome": completion["outcome"],
            "diagnostic": _code(
                completion["diagnostic"], "completion.diagnostic", unavailable_reason
            ),
        },
        "reconciliation": None
        if fact is None
        else {
            "request_id": fact["request_id"],
            "version": fact["version"],
            "writer": fact["writer"],
        },
        "settings": mapping(launch).get("common") if isinstance(launch, dict) else None,
    }
    item["unavailable_reason"] = unavailable_reason
    return item


def _events(
    store: Store, run_id: str, size: int, key: str | None
) -> tuple[list[dict[str, object]], str | None]:
    clause = ""
    params: tuple[object, ...] = ()
    if key is not None:
        parts = key.split("_", 2)
        if (
            len(parts) != 3
            or parts[1] not in {"t", "m"}
            or _ID.fullmatch(parts[2]) is None
        ):
            raise RouterError("invalid_request")
        version, kind, event_id = _decimal(parts[0]), parts[1], parts[2]
        table, column = (
            ("transitions", "completion_id") if kind == "t" else ("moves", "request_id")
        )
        found = store.db.execute(
            f"SELECT plane_id FROM {table} WHERE run_id=? AND {column}=? AND version=?",
            (run_id, event_id, version),
        ).fetchone()
        if found is None:
            raise RouterError("inspection_changed")
        plane, event_kind = str(found[0]), "transition" if kind == "t" else "move"
        clause = (
            "WHERE version<? OR (version=? AND (plane_id>? OR (plane_id=? AND "
            "(event_kind>? OR (event_kind=? AND event_id>?))))) "
        )
        params = (version, version, plane, plane, event_kind, event_kind, event_id)
    rows = store.db.execute(
        _EVENTS + clause + _EVENT_ORDER + "LIMIT ?",
        (run_id, run_id, *params, size + 1),
    ).fetchall()
    items = [_event(row) for row in rows[:size]]
    if len(rows) <= size:
        return items, None
    tail = rows[size - 1]
    mark = "t" if tail["event_kind"] == "transition" else "m"
    return items, f"{tail['version']}_{mark}_{tail['event_id']}"


def _event(row: sqlite3.Row) -> dict[str, object]:
    if row["event_kind"] == "transition":
        return {
            "event_kind": "transition",
            "event_id": row["event_id"],
            "completion_id": row["completion_id"],
            "plane_id": row["plane_id"],
            "node_id": row["node_id"],
            "producing_revision_id": row["producing_revision_id"],
            "routing_revision_id": row["routing_revision_id"],
            "outcome": row["outcome"],
            "action": row["action"],
            "target": row["target"],
            "retry_delta": row["retry_delta"],
            "version": row["version"],
        }
    return {
        "event_kind": "move",
        "event_id": row["event_id"],
        "move_kind": row["move_kind"],
        "completion_id": row["completion_id"],
        "source_invocation_id": row["source_invocation_id"],
        "plane_id": row["plane_id"],
        "target": row["target"],
        "routing_revision_id": row["routing_revision_id"],
        "request_id": row["request_id"],
        "version": row["version"],
    }


def _controls(
    store: Store, run_id: str, size: int, key: str | None
) -> tuple[list[dict[str, object]], str | None]:
    clause = ""
    params: tuple[object, ...] = ()
    if key is not None:
        seq = _decimal(key)
        if (
            store.db.execute(
                "SELECT 1 FROM controls WHERE run_id=? AND seq=?", (run_id, seq)
            ).fetchone()
            is None
        ):
            raise RouterError("inspection_changed")
        clause, params = "AND seq<? ", (seq,)
    rows = store.db.execute(
        "SELECT seq,receipt FROM controls WHERE run_id=? "
        + clause
        + "ORDER BY seq DESC LIMIT ?",
        (run_id, *params, size + 1),
    ).fetchall()
    items = [mapping(strict_json(str(row["receipt"]))) for row in rows[:size]]
    return items, str(rows[size - 1]["seq"]) if len(rows) > size else None


def _receipt(
    store: Store,
    instance_id: str,
    run: dict[str, object] | None,
    request_id: str,
) -> dict[str, object]:
    row = store.db.execute(
        "SELECT run_id,receipt FROM controls WHERE request_id=?", (request_id,)
    ).fetchone()
    run_id = None if run is None else str(run["id"])
    version = (
        store.instance_settings()["state_version"]
        if run is None
        else run["state_version"]
    )
    items: list[dict[str, object]] = []
    if row is not None:
        if run is not None and row["run_id"] != run_id:
            # An instance-targeted Start belongs to the run it recorded creating.
            if row["run_id"] is not None or run.get("start_request_id") != request_id:
                raise RouterError("request_target_mismatch")
        items.append(mapping(strict_json(str(row["receipt"]))))
    return _result("controls", instance_id, run_id, version, items, None, {})


def _summary(store: Store, run: dict[str, object]) -> dict[str, object]:
    run_id = str(run["id"])
    revision = store.revision(str(run["revision_id"]))
    planes = store.plane_states(run_id)
    if len(planes) > MAX_PLANES:
        raise RouterError("inspection_too_large")
    pending: dict[str, list[str]] = {}
    for row in store.db.execute(
        "SELECT c.plane_id,c.invocation_id FROM completions c JOIN invocations i "
        "ON i.id=c.invocation_id "
        "LEFT JOIN transitions t ON t.completion_id=c.invocation_id "
        "LEFT JOIN moves m ON m.completion_id=c.invocation_id "
        "WHERE c.run_id=? AND t.completion_id IS NULL AND m.completion_id IS NULL "
        "ORDER BY c.plane_id,i.attempt",
        (run_id,),
    ):
        pending.setdefault(str(row[0]), []).append(str(row[1]))
    latest: dict[str, dict[str, object]] = {}
    for row in store.db.execute(_EVENTS + _EVENT_ORDER, (run_id, run_id)):
        latest.setdefault(str(row["plane_id"]), _event(row))
    selected = progress = 0
    projected: dict[str, object] = {}
    for plane_id, plane in planes.items():
        chosen = mapping(plane.get("selected", {}))
        progressed = mapping(plane.get("progress", {}))
        selected += len(chosen)
        progress += len(progressed)
        cursor = plane.get("cursor")
        declared = revision.graph.planes.get(plane_id)
        node = (
            declared.nodes.get(cursor)
            if declared is not None and isinstance(cursor, str)
            else None
        )
        projected[plane_id] = {
            "status": plane.get("status"),
            "cursor": cursor,
            "active_invocation": plane.get("active_invocation"),
            "pause_reason": plane.get("pause_reason"),
            "resume_target": plane.get("resume_target"),
            "dependencies": []
            if node is None
            else [
                {"plane_id": item.plane_id, "status": item.status}
                for item in node.requires_terminal
            ],
            "selected": chosen,
            "progress": progressed,
            "pending_completion_ids": pending.get(plane_id, []),
            "last_event": latest.get(plane_id),
        }
    if selected > MAX_SEAM_ENTRIES or progress > MAX_SEAM_ENTRIES:
        raise RouterError("inspection_too_large")
    seams: dict[str, dict[str, object]] = {
        seam_id: _seam() for seam_id in sorted(revision.graph.seams)
    }
    for table, field in (
        ("seam_occurrences", "latest_occurrence_sequence"),
        ("seam_selections", "latest_selection_sequence"),
        ("seam_acknowledgements", "latest_acknowledgement_sequence"),
    ):
        for row in store.db.execute(
            f"SELECT seam_id,MAX(sequence) FROM {table} WHERE run_id=? "
            "GROUP BY seam_id",
            (run_id,),
        ):
            seams.setdefault(str(row[0]), _seam())[field] = row[1]
    if len(seams) > MAX_SEAM_ENTRIES:
        raise RouterError("inspection_too_large")
    recorded = {
        str(row[0]): int(row[1])
        for row in store.db.execute(
            "SELECT writer,COUNT(*) FROM completions WHERE run_id=? GROUP BY writer",
            (run_id,),
        )
    }
    effective: dict[str, int] = {}
    for row in store.db.execute(
        "SELECT id FROM invocations WHERE run_id=? ORDER BY attempt", (run_id,)
    ):
        writer = store.writer(str(row[0]))
        effective[writer] = effective.get(writer, 0) + 1
    return {
        "run_id": run_id,
        "status": run["status"],
        "mode": run["mode"],
        "state_version": run["state_version"],
        "run_gate": run.get("run_gate"),
        "pause_reason": run.get("pause_reason"),
        "cancel_requested": run.get("cancel_requested"),
        "invocation_count": run.get("count"),
        "limits": {
            "max_invocations": run.get("cap"),
            "max_elapsed_seconds": run.get("max_elapsed"),
            "startup_max_invocations": run.get("startup_cap"),
            "startup_max_elapsed_seconds": run.get("startup_elapsed"),
            "started_at": run.get("started_at"),
            "deadline": run.get("deadline"),
            "startup_deadline": run.get("startup_deadline"),
        },
        "startup_revision_id": run.get("startup_revision_id"),
        "active_revision_id": run["revision_id"],
        "pending_candidate": _candidate(store, run_id),
        "quiescent": store.quiescent(run_id),
        "inflight_invocations": store.inflight(run_id),
        "writer_counts": {"recorded": recorded, "effective": effective},
        "planes": projected,
        "seams": seams,
    }


def _seam() -> dict[str, object]:
    return {
        "latest_occurrence_sequence": None,
        "latest_selection_sequence": None,
        "latest_acknowledgement_sequence": None,
    }


def _candidate(store: Store, run_id: str) -> dict[str, object] | None:
    body = store.pending_candidate(run_id)
    if body is None:
        return None
    row = store.db.execute(
        "SELECT version,request_id FROM candidates WHERE run_id=? AND seq=?",
        (run_id, body.get("seq")),
    ).fetchone()
    changed = body.get("changed_paths")
    if isinstance(changed, list) and len(changed) > MAX_LIST_ENTRIES:
        raise RouterError("inspection_too_large")
    return {
        "seq": body.get("seq"),
        "candidate_id": body.get("candidate_id"),
        "revision_id": body.get("revision_id"),
        "diagnostic": body.get("diagnostic"),
        "changed_paths": changed,
        "withdrawn": body.get("withdrawn"),
        "supersedes": body.get("supersedes"),
        "version": None if row is None else row["version"],
        "request_id": None if row is None else row["request_id"],
    }


def inspect_revision_graph(
    instance_id: str,
    run_id: str,
    revision_id: str,
    *,
    state_home: Path,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    """The captured graph of a revision belonging to this run's history."""
    check_id(run_id)
    if not isinstance(revision_id, str) or _REVISION.fullmatch(revision_id) is None:
        raise RouterError("invalid_request")
    store = _open(instance_id, state_home, adapter_resolver)
    try:
        run = store.run(run_id)
        members = {str(run["revision_id"]), str(run.get("startup_revision_id"))}
        for table in ("activations", "invocations"):
            members.update(
                str(row[0])
                for row in store.db.execute(
                    f"SELECT revision_id FROM {table} WHERE run_id=?", (run_id,)
                )
            )
        candidate = store.pending_candidate(run_id)
        if candidate is not None and isinstance(candidate.get("revision_id"), str):
            members.add(str(candidate["revision_id"]))
        if revision_id not in members:
            raise RouterError("unknown_revision")
        data = _graph(revision_id, store.revision(revision_id).graph)
        return _result(
            "graph", instance_id, run_id, run["state_version"], [], None, data
        )
    finally:
        store.close()


def _graph(revision_id: str, graph: Graph) -> dict[str, object]:
    nodes = [node for plane in graph.planes.values() for node in plane.nodes.values()]
    routes = len(graph.seams) + sum(
        len(node.routes) + 2 + len(node.requires_terminal) for node in nodes
    )
    if (
        len(graph.planes) > MAX_PLANES
        or len(nodes) > MAX_NODES
        or routes > MAX_GRAPH_ITEMS
    ):
        raise RouterError("inspection_too_large")
    return {
        "revision_id": revision_id,
        "graph_format": graph.format,
        "limits": {
            "max_invocations": graph.max_invocations,
            "max_elapsed_seconds": graph.max_elapsed,
            "max_parallel_invocations": graph.max_parallel,
        },
        "bindings": sorted(graph.bindings),
        "defaults": _pick(graph.defaults, _COMMON),
        "planes": {
            plane_id: {
                "start": plane.start,
                "defaults": _pick(plane.defaults, _COMMON),
                "nodes": {
                    node_id: _node(node)
                    for node_id, node in sorted(plane.nodes.items())
                },
            }
            for plane_id, plane in sorted(graph.planes.items())
        },
        "seams": {
            seam_id: {
                "producer_plane": seam.producer_plane,
                "producer_node": seam.producer_node,
                "outcome": seam.outcome,
                "consumer_plane": seam.consumer_plane,
                "consumer_node": seam.consumer_node,
            }
            for seam_id, seam in sorted(graph.seams.items())
        },
    }


def _node(node: Node) -> dict[str, object]:
    return {
        "binding": node.binding,
        "timeout_seconds": node.timeout,
        "outcomes": list(node.outcomes),
        "routes": {
            outcome: _action(action) for outcome, action in sorted(node.routes.items())
        },
        "on_error": _action(node.on_error),
        "on_unknown": _action(node.on_unknown),
        "requires_terminal": [
            {"plane_id": item.plane_id, "status": item.status}
            for item in node.requires_terminal
        ],
    }


def _action(action: Action) -> dict[str, object]:
    return {
        "kind": action.kind,
        "target": action.target,
        "reason": action.reason,
        "limit": action.limit,
        "exhausted": None if action.exhausted is None else _action(action.exhausted),
    }


def validate_revision_summary(
    instance_id: str,
    *,
    state_home: Path,
    run_id: str | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    """Core validation of the configured workflow, as a bounded summary.

    Uses Core's public validation semantics with bounded store snapshots and a
    4 MiB current-input limit. It exposes no caller-selected path, captures no
    candidate row and publishes nothing.
    """
    from .control import _validate_revision

    if run_id is not None:
        check_id(run_id)
    full = _validate_revision(
        instance_id,
        state_home=state_home,
        run_id=run_id,
        adapter_resolver=adapter_resolver,
        load_budget=_Preflight(),
        store_budget=_Preflight(),
        read_limit=INPUT_BYTES,
        changed_paths=True,
        safe_details=True,
    )
    changed = full.get("changed_paths")
    lists = [
        cast(list[object], full.get(name, []))
        for name in ("diagnostics", "diagnostic_details", "warnings")
    ]
    if isinstance(changed, list):
        lists.append(cast(list[object], changed))
    resolved_nodes = mapping(full["resolved_nodes"])
    count = sum(len(mapping(nodes)) for nodes in resolved_nodes.values())
    if (
        any(len(entries) > MAX_LIST_ENTRIES for entries in lists)
        or len(resolved_nodes) > MAX_PLANES
        or count > MAX_NODES
    ):
        raise RouterError("inspection_too_large")
    draft = full.get("draft_revision")
    candidate: dict[str, object] | None = None
    if isinstance(draft, dict):
        source = mapping(draft)
        candidate = {
            "candidate_id": source.get("candidate_id"),
            "revision_id": source.get("revision_id"),
            "diagnostic": source.get("diagnostic"),
            "changed_paths": changed,
        }
    context = mapping(full["validation_context"])
    data: dict[str, object] = {
        "valid": full["valid"],
        "revision_id": full["revision_id"],
        "candidate": candidate,
        "diagnostics": lists[0],
        "diagnostic_details": lists[1],
        "warnings": lists[2],
        "validation_context": context,
        "resolved_nodes": {
            plane_id: {
                node_id: {
                    "format": mapping(settings)["format"],
                    "common": mapping(settings)["common"],
                }
                for node_id, settings in mapping(nodes).items()
            }
            for plane_id, nodes in resolved_nodes.items()
        },
    }
    version = (
        context.get("settings_version")
        if run_id is None
        else context.get("run_state_version")
    )
    return _result("validation", instance_id, run_id, version, [], None, data)
