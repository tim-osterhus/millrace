"""Transactional execution records; history validation lives in store_validation."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from .control_requests import canonical
from .execution import (
    AdapterResolver,
    CompletionFacts,
    LaunchPlan,
    SeamInput,
    _adapter,
    _object,
    _result,
)
from .graph import (
    Candidate,
    Revision,
    build_launch,
    capture_candidate,
    decode_revision,
    resolve_nodes,
    resolve_settings,
)
from .ownership import (
    Config,
    atomic_json,
    check_id,
    claim_hold,
    file_lock,
    new_id,
    release_hold,
)
from .plane_store import SCHEMA as PLANE_SCHEMA
from .records import (
    TERMINAL,
    CommandResult,
    RouterError,
    mapping,
    parse_result,
    recorded_result,
    select_action,
    strict_json,
)

FORMAT = "millrace-router-store/v7"
__all__ = [
    "Store",
    "Config",
    "FORMAT",
    "atomic_json",
    "check_id",
    "claim_hold",
    "file_lock",
    "new_id",
    "release_hold",
]

_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY,
    value TEXT NOT NULL);
CREATE TABLE revisions(id TEXT PRIMARY KEY,
    body TEXT NOT NULL);
CREATE TABLE runs(id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES revisions(id),
    hold_id TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL);
CREATE TABLE invocations(id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    revision_id TEXT NOT NULL REFERENCES revisions(id),
    plane_id TEXT NOT NULL,
    node TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    body TEXT NOT NULL,
    UNIQUE(run_id,
    attempt));
CREATE TABLE completions(invocation_id TEXT PRIMARY KEY REFERENCES invocations(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    revision_id TEXT NOT NULL REFERENCES revisions(id),
    plane_id TEXT NOT NULL,
    node TEXT NOT NULL,
    transport TEXT NOT NULL,
    stdout BLOB NOT NULL,
    stderr BLOB NOT NULL,
    writer TEXT NOT NULL,
    diagnostic TEXT NOT NULL,
    exit_code INTEGER,
    classification TEXT,
    outcome TEXT);
CREATE TABLE transitions(
    completion_id TEXT PRIMARY KEY REFERENCES completions(invocation_id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    revision_id TEXT NOT NULL REFERENCES revisions(id),
    plane_id TEXT NOT NULL,
    node TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    retry_delta INTEGER NOT NULL,
    version INTEGER NOT NULL);
CREATE TABLE retries(run_id TEXT NOT NULL REFERENCES runs(id),
    plane_id TEXT NOT NULL,
    node TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY(run_id,
    plane_id,
    node));
CREATE TABLE run_versions(run_id TEXT NOT NULL REFERENCES runs(id),
    version INTEGER NOT NULL,
    PRIMARY KEY(run_id,
    version));
CREATE TABLE candidates(run_id TEXT NOT NULL REFERENCES runs(id),
    seq INTEGER NOT NULL,
    body TEXT NOT NULL,
    version INTEGER NOT NULL,
    request_id TEXT,
    PRIMARY KEY(run_id,
    seq));
CREATE TABLE activations(run_id TEXT NOT NULL REFERENCES runs(id),
    revision_id TEXT NOT NULL REFERENCES revisions(id),
    version INTEGER NOT NULL,
    candidate_seq INTEGER,
    request_id TEXT,
    PRIMARY KEY(run_id,
    version));
CREATE TABLE states(run_id TEXT NOT NULL REFERENCES runs(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    run_gate TEXT,
    request_id TEXT,
    PRIMARY KEY(run_id,
    version));
CREATE TABLE moves(run_id TEXT NOT NULL REFERENCES runs(id),
    version INTEGER NOT NULL,
    plane_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    source_invocation TEXT REFERENCES invocations(id),
    completion_id TEXT UNIQUE REFERENCES completions(invocation_id),
    revision_id TEXT NOT NULL REFERENCES revisions(id),
    reason TEXT NOT NULL,
    request_id TEXT NOT NULL,
    PRIMARY KEY(run_id,
    version));
CREATE TABLE writer_facts(invocation_id TEXT PRIMARY KEY REFERENCES invocations(id),
    version INTEGER NOT NULL,
    writer TEXT NOT NULL,
    reason TEXT NOT NULL,
    request_id TEXT NOT NULL);
CREATE TABLE controls(seq INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    run_id TEXT,
    plane_id TEXT,
    operation TEXT NOT NULL,
    expected_version INTEGER,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    receipt TEXT NOT NULL);
CREATE TABLE final_receipts(request_id TEXT PRIMARY KEY REFERENCES controls(request_id),
    receipt TEXT NOT NULL);
"""


_SCHEMA += PLANE_SCHEMA


class Store:
    """A single SQLite connection, with one version increment per execution commit."""

    def __init__(
        self,
        config: Config,
        *,
        create: bool = False,
        readonly: bool = False,
        creation: dict[str, object] | None = None,
        adapter_resolver: AdapterResolver | None = None,
    ) -> None:
        self.adapter_resolver = adapter_resolver
        self.config = config
        self._changed: set[str] = set()
        if not create and not config.store_path.is_file():
            raise RouterError("unknown_store")
        try:
            self.db = sqlite3.connect(
                config.store_path.as_uri()
                + ("?mode=ro" if readonly else "?mode=rwc" if create else "?mode=rw"),
                uri=True,
                isolation_level=None,
                timeout=5,
            )
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            if create:
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA synchronous=FULL")
                self.db.executescript(_SCHEMA)
                with self.transaction():
                    self.db.executemany(
                        "INSERT INTO meta VALUES(?,?)",
                        [
                            ("format", FORMAT),
                            ("identity", json.dumps(config.identity(), sort_keys=True)),
                            ("settings", json.dumps(config.settings(), sort_keys=True)),
                            ("creation", json.dumps(creation, sort_keys=True)),
                        ],
                    )
            if readonly:
                self.db.execute("BEGIN")
            self.validate()
        except (sqlite3.Error, ValueError) as exc:
            if hasattr(self, "db"):
                self.db.close()
            raise RouterError("store_integrity") from exc
        except RouterError:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self.db.in_transaction:
            yield
            return
        self._changed = set()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        finally:
            self._changed = set()

    def instance_settings(self) -> dict[str, object]:
        return mapping(
            strict_json(
                str(
                    self.db.execute(
                        "SELECT value FROM meta WHERE key='settings'"
                    ).fetchone()[0]
                )
            )
        )

    def bump_instance(self) -> int:
        value = self.instance_settings()
        value["state_version"] = cast(int, value["state_version"]) + 1
        self.db.execute(
            "UPDATE meta SET value=? WHERE key='settings'",
            (json.dumps(value, sort_keys=True),),
        )
        return cast(int, value["state_version"])

    def run(self, run_id: str) -> dict[str, object]:
        row = self.db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RouterError("unknown_run")
        return mapping(strict_json(str(row[0])))

    def revision(self, revision_id: str) -> Revision:
        row = self.db.execute(
            "SELECT body FROM revisions WHERE id=?", (revision_id,)
        ).fetchone()
        if row is None:
            raise RouterError("revision_integrity")
        return decode_revision(str(row[0]), revision_id)

    def update_run(self, run: dict[str, object]) -> None:
        run_id = cast(str, run["id"])
        if run_id not in self._changed:
            run["state_version"] = cast(int, run["state_version"]) + 1
            self._changed.add(run_id)
            self.db.execute(
                "INSERT INTO run_versions VALUES(?,?)", (run_id, run["state_version"])
            )
        self.db.execute(
            "UPDATE runs SET body=?,revision_id=? WHERE id=?",
            (json.dumps(run), run["revision_id"], run_id),
        )

    def create_run(self, revision: Revision, mode: str = "locked") -> dict[str, object]:
        resolve_nodes(
            revision,
            settings_context=self.config.settings(),
            adapter_resolver=self.adapter_resolver,
            project_root=self.config.project_root,
        )
        for row in self.db.execute("SELECT body FROM runs"):
            if mapping(strict_json(str(row[0])))["status"] not in TERMINAL:
                raise RouterError("run_nonterminal")
        if mode not in {"locked", "live"}:
            raise RouterError("invalid_edit_mode")
        now = time.time()
        run: dict[str, object] = {
            "id": new_id(),
            "instance_id": self.config.instance_id,
            "revision_id": revision.revision_id,
            "startup_revision_id": revision.revision_id,
            "hold_id": new_id(),
            "status": "ready",
            "mode": mode,
            "run_gate": None,
            "last_admitted_plane": None,
            "pending_seq": None,
            "pause_reason": None,
            "count": 0,
            "state_version": 1,
            "cap": revision.graph.max_invocations,
            "max_elapsed": revision.graph.max_elapsed,
            "startup_cap": revision.graph.max_invocations,
            "startup_elapsed": revision.graph.max_elapsed,
            "started_at": now,
            "startup_deadline": now + revision.graph.max_elapsed,
            "deadline": now + revision.graph.max_elapsed,
            "host_settings": self.config.settings(),
            "cancel_requested": False,
        }
        with self.transaction():
            self.db.execute(
                "INSERT OR IGNORE INTO revisions VALUES(?,?)",
                (revision.revision_id, revision.encoded()),
            )
            self.db.execute(
                "INSERT INTO runs VALUES(?,?,?,?)",
                (run["id"], revision.revision_id, run["hold_id"], json.dumps(run)),
            )
            self.db.execute("INSERT INTO run_versions VALUES(?,1)", (run["id"],))
            self.db.execute(
                "INSERT INTO activations VALUES(?,?,1,NULL,NULL)",
                (run["id"], revision.revision_id),
            )
            self.db.execute(
                "INSERT INTO states VALUES(?,1,'ready',NULL,NULL,NULL)",
                (run["id"],),
            )
            from .plane_store import insert_initial

            insert_initial(self.db, str(run["id"]), revision)
            self.bump_instance()
        return run

    def plane_states(self, run_id: str) -> dict[str, dict[str, object]]:
        from .plane_store import read_planes

        self.run(run_id)
        return read_planes(self.db, run_id)

    def compatibility_facts(
        self, run_id: str
    ) -> tuple[Revision, dict[str, dict[str, object]], tuple[dict[str, object], ...]]:
        """Extract producing-classified facts from the owner's existing snapshot."""
        run = self.run(run_id)
        startup = self.revision(str(run["startup_revision_id"]))
        results: list[dict[str, object]] = []
        for row in self.db.execute(
            "SELECT i.id,i.plane_id,i.node,i.revision_id,c.stdout,c.transport,"
            "c.classification,c.outcome FROM invocations i "
            "LEFT JOIN completions c ON c.invocation_id=i.id "
            "LEFT JOIN transitions t ON t.completion_id=i.id "
            "LEFT JOIN moves m ON m.completion_id=i.id "
            "WHERE i.run_id=? AND t.completion_id IS NULL "
            "AND m.completion_id IS NULL ORDER BY i.attempt",
            (run_id,),
        ):
            classification, outcome = row["classification"], row["outcome"]
            if row["transport"] is not None and classification is None:
                producing = self.revision(str(row["revision_id"]))
                node = producing.graph.planes[str(row["plane_id"])].nodes[
                    str(row["node"])
                ]
                classification, outcome = (
                    parse_result(
                        bytes(row["stdout"]),
                        node,
                        self.config.project_root,
                        resolve_refs=False,
                    )
                    if row["transport"] == "completed"
                    else (str(row["transport"]), None)
                )
            results.append(
                {
                    "plane_id": row["plane_id"],
                    "node_id": row["node"],
                    "classification": classification,
                    "outcome": outcome,
                }
            )
        return startup, self.plane_states(run_id), tuple(results)

    def set_state(
        self,
        run_id: str,
        status: str,
        reason: str | None = None,
        *,
        request_id: str | None = None,
    ) -> None:
        with self.transaction():
            run = self.run(run_id)
            previous = self.db.execute(
                "SELECT run_gate FROM states WHERE run_id=? "
                "ORDER BY version DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            gate_value = (
                canonical(run["run_gate"]) if run["run_gate"] is not None else None
            )
            if (
                (run["status"], run["pause_reason"]) == (status, reason)
                and previous is not None
                and previous[0] == gate_value
            ):
                return
            run.update(status=status, pause_reason=reason)
            self.update_run(run)
            same_commit = self.db.execute(
                "SELECT request_id FROM states WHERE run_id=? AND version=?",
                (run_id, run["state_version"]),
            ).fetchone()
            if request_id is None and same_commit is not None:
                request_id = same_commit[0]
            self.db.execute(
                "INSERT OR REPLACE INTO states VALUES(?,?,?,?,?,?)",
                (
                    run_id,
                    run["state_version"],
                    status,
                    reason,
                    canonical(run["run_gate"]) if run["run_gate"] is not None else None,
                    request_id,
                ),
            )

    def save_plane(
        self, run_id: str, plane: dict[str, object], kind: str, cause: dict[str, object]
    ) -> None:
        with self.transaction():
            run = self.run(run_id)
            self.update_run(run)
            ordinal = self.db.execute(
                "SELECT COALESCE(MAX(ordinal),-1)+1 FROM plane_events "
                "WHERE run_id=? AND version=?",
                (run_id, run["state_version"]),
            ).fetchone()[0]
            self.db.execute(
                "UPDATE planes SET body=? WHERE run_id=? AND plane_id=?",
                (canonical(plane), run_id, plane["plane_id"]),
            )
            self.db.execute(
                "INSERT INTO plane_events VALUES(?,?,?,?,?,?)",
                (
                    run_id,
                    run["state_version"],
                    ordinal,
                    plane["plane_id"],
                    kind,
                    canonical({"state": plane, "cause": cause}),
                ),
            )

    def set_gate(
        self, run_id: str, reason: str, *, request_id: str | None = None
    ) -> None:
        with self.transaction():
            run = self.run(run_id)
            gate = {"reason": reason, "request_id": request_id}
            if run["run_gate"] is not None and reason != "writer_unknown":
                return
            if run["run_gate"] == gate:
                return
            run["run_gate"] = gate
            self.update_run(run)
            ordinal = self.db.execute(
                "SELECT COALESCE(MAX(ordinal),-1)+1 FROM run_gate_events "
                "WHERE run_id=? AND version=?",
                (run_id, run["state_version"]),
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO run_gate_events VALUES(?,?,?,'set',?)",
                (run_id, run["state_version"], ordinal, canonical(gate)),
            )
            self.set_state(run_id, "paused", reason, request_id=request_id)

    def clear_gate(self, run_id: str, request_id: str) -> None:
        with self.transaction():
            run = self.run(run_id)
            if run["run_gate"] is None:
                raise RouterError("run_not_paused")
            run["run_gate"] = None
            self.update_run(run)
            ordinal = self.db.execute(
                "SELECT COALESCE(MAX(ordinal),-1)+1 FROM run_gate_events "
                "WHERE run_id=? AND version=?",
                (run_id, run["state_version"]),
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO run_gate_events VALUES(?,?,?,'clear',?)",
                (
                    run_id,
                    run["state_version"],
                    ordinal,
                    canonical({"request_id": request_id}),
                ),
            )
            self.recompute(run_id, request_id=request_id)

    def recompute(self, run_id: str, *, request_id: str | None = None) -> None:
        run = self.run(run_id)
        planes = self.plane_states(run_id)
        if run["cancel_requested"] and self.quiescent(run_id):
            for plane in planes.values():
                if plane["status"] not in TERMINAL:
                    plane.update(
                        status="cancelled",
                        pause_reason="operator_cancel",
                        active_invocation=None,
                    )
                    self.save_plane(run_id, plane, "cancelled", {"run_cancel": True})
            self.set_state(
                run_id, "cancelled", "operator_cancel", request_id=request_id
            )
        elif all(plane["status"] in TERMINAL for plane in planes.values()):
            status = next(
                (
                    value
                    for value in ("failed", "cancelled", "succeeded")
                    if any(plane["status"] == value for plane in planes.values())
                ),
                "succeeded",
            )
            self.set_state(run_id, status, request_id=request_id)
        elif run["run_gate"] is not None:
            self.set_state(
                run_id,
                "paused",
                str(mapping(run["run_gate"])["reason"]),
                request_id=request_id,
            )
        elif any(plane["status"] in {"ready", "running"} for plane in planes.values()):
            self.set_state(run_id, "running", request_id=request_id)
        else:
            self.set_state(
                run_id, "paused", "no_runnable_planes", request_id=request_id
            )

    def inflight(self, run_id: str, plane_id: str | None = None) -> list[str]:
        return [
            str(row[0])
            for row in self.db.execute(
                "SELECT id FROM invocations WHERE run_id=? "
                "AND status IN ('reserved','active') "
                + ("AND plane_id=? " if plane_id is not None else "")
                + "ORDER BY id",
                (run_id, plane_id) if plane_id is not None else (run_id,),
            )
        ]

    def eligible(self, run_id: str, plane_id: str) -> bool:
        run = self.run(run_id)
        plane = self.plane_states(run_id)[plane_id]
        if (
            plane["status"] not in {"ready", "waiting"}
            or plane["active_invocation"] is not None
        ):
            return False
        revision = self.revision(str(run["revision_id"]))
        node = revision.graph.planes[plane_id].nodes[str(plane["cursor"])]
        planes = self.plane_states(run_id)
        for dependency in node.requires_terminal:
            status = planes[dependency.plane_id]["status"]
            if status != dependency.status:
                reason = (
                    "dependency_failed" if status in TERMINAL else "terminal_dependency"
                )
                state = "paused" if status in TERMINAL else "waiting"
                if (plane["status"], plane["pause_reason"]) != (state, reason):
                    plane.update(status=state, pause_reason=reason)
                    self.save_plane(run_id, plane, "waiting", {"reason": reason})
                return False
        for seam_id, seam in revision.graph.seams.items():
            if (seam.consumer_plane, seam.consumer_node) != (plane_id, plane["cursor"]):
                continue
            selected = mapping(plane["selected"])
            if seam_id not in selected:
                occurrence = self.db.execute(
                    "SELECT o.sequence,o.body FROM seam_occurrences o "
                    "LEFT JOIN seam_acknowledgements a USING(run_id,seam_id,sequence) "
                    "WHERE o.run_id=? AND o.seam_id=? AND a.sequence IS NULL "
                    "ORDER BY o.sequence LIMIT 1",
                    (run_id, seam_id),
                ).fetchone()
                if occurrence is None:
                    exhausted = planes[seam.producer_plane]["status"] in TERMINAL
                    status, reason = (
                        ("paused", "seam_exhausted")
                        if exhausted
                        else ("waiting", "seam_input")
                    )
                    if (plane["status"], plane["pause_reason"]) != (status, reason):
                        plane.update(status=status, pause_reason=reason)
                        self.save_plane(run_id, plane, "waiting", {"reason": reason})
                    return False
                with self.transaction():
                    selected[seam_id] = occurrence["sequence"]
                    plane["selected"] = selected
                    plane.update(status="ready", pause_reason=None)
                    self.save_plane(
                        run_id,
                        plane,
                        "selected",
                        {"seam_id": seam_id, "sequence": occurrence["sequence"]},
                    )
                    self.db.execute(
                        "INSERT INTO seam_selections VALUES(?,?,?,?,?,?,?)",
                        (
                            run_id,
                            seam_id,
                            occurrence["sequence"],
                            plane_id,
                            plane["cursor"],
                            self.run(run_id)["state_version"],
                            occurrence["body"],
                        ),
                    )
        if plane["status"] == "waiting":
            plane.update(status="ready", pause_reason=None)
            self.save_plane(run_id, plane, "ready", {})
        return True

    def seam_input(
        self, run_id: str, plane_id: str, node_id: str, invocation_id: str
    ) -> SeamInput | None:
        from .seams import build_input

        plane = self.plane_states(run_id)[plane_id]
        revision = self.revision(str(self.run(run_id)["revision_id"]))
        for seam_id, sequence in mapping(plane["selected"]).items():
            seam = revision.graph.seams[seam_id]
            if seam.consumer_node == node_id:
                row = self.db.execute(
                    "SELECT body FROM seam_selections WHERE run_id=? "
                    "AND seam_id=? AND sequence=?",
                    (run_id, seam_id, sequence),
                ).fetchone()
                if row is None:
                    raise RouterError("seam_integrity")
                return build_input(
                    self.config.state_dir
                    / "invocations"
                    / invocation_id
                    / "seam-input.json",
                    mapping(strict_json(str(row[0]))),
                )
        return None

    def reserve(
        self, run_id: str, revision: Revision, plane_id: str
    ) -> dict[str, object]:
        run = self.run(run_id)
        plane = self.plane_states(run_id)[plane_id]
        node_id = str(plane["cursor"])
        if plane["active_invocation"] is not None or plane["status"] != "ready":
            raise RouterError("plane_not_ready")
        if node_id not in revision.graph.planes[plane_id].nodes:
            raise RouterError("incompatible_revision")
        node = revision.graph.planes[plane_id].nodes[node_id]
        now = time.time()
        if run["status"] != "running":
            raise RouterError("run_not_running")
        if cast(int, run["count"]) >= cast(int, run["cap"]) or now >= cast(
            float, run["deadline"]
        ):
            raise RouterError("limit_exhausted")
        invocation_id = new_id()
        launch_settings = resolve_settings(
            revision,
            plane_id,
            node_id,
            settings_context=mapping(run["host_settings"]),
            adapter_resolver=self.adapter_resolver,
            project_root=self.config.project_root,
        )
        effective = mapping(mapping(launch_settings["common"])["effective"])
        controls = self.config.state_dir / "invocations" / invocation_id / "controls"
        plan, launch = build_launch(
            revision,
            plane_id,
            node_id,
            mapping(run["host_settings"]),
            controls,
            adapter_resolver=self.adapter_resolver,
            project_root=self.config.project_root,
            invocation_id=invocation_id,
            seam_input=self.seam_input(run_id, plane_id, node_id, invocation_id),
        )
        if canonical(plan.settings) != canonical(launch_settings["adapter"]):
            raise RouterError("invalid_launch_plan")
        invocation: dict[str, object] = {
            "id": invocation_id,
            "run_id": run_id,
            "revision_id": revision.revision_id,
            "plane_id": plane_id,
            "node": node_id,
            "attempt": cast(int, run["count"]) + 1,
            "created_at": now,
            "deadline": min(
                cast(float, run["deadline"]),
                now + cast(float, effective["timeout_seconds"]),
            ),
            "run_deadline": run["deadline"],
            "argv": list(plan.argv),
            "cwd": str(Path(str(revision.workspaces[plane_id]["path"])) / node.cwd),
            "pid": None,
            "started_version": None,
            "completed_version": None,
            "parsed_version": None,
            "env_names": mapping(run["host_settings"])["env_allowlist"],
            "result_contract": "json-v1",
            "ref_admission": None,
            "launch_settings": launch_settings,
            "launch": launch,
            "origin": None,
            "uncertainty_diagnostic": None,
            "adapter_evidence": None,
            "version": cast(int, run["state_version"]) + 1,
        }
        with self.transaction():
            self.db.execute(
                "INSERT INTO invocations VALUES(?,?,?,?,?,?,?,?)",
                (
                    invocation_id,
                    run_id,
                    revision.revision_id,
                    plane_id,
                    node_id,
                    invocation["attempt"],
                    "reserved",
                    json.dumps(invocation),
                ),
            )
            run["count"] = invocation["attempt"]
            run["last_admitted_plane"] = plane_id
            self.update_run(run)
            plane.update(active_invocation=invocation_id, status="running")
            self.save_plane(run_id, plane, "reserved", {"invocation_id": invocation_id})
        return invocation

    def launch(self, body: dict[str, object]) -> LaunchPlan:
        revision = self.revision(str(body["revision_id"]))
        host = mapping(self.run(str(body["run_id"]))["host_settings"])
        seam = mapping(body["launch"]).get("seam_input")
        frozen_input = None
        if seam is not None:
            item = mapping(seam)
            frozen_input = SeamInput(
                Path(str(item["path"])),
                base64.b64decode(str(item["content"]), validate=True),
                str(item["sha256"]),
                str(item["seam_id"]),
                cast(int, item["sequence"]),
                str(item["producer_invocation_id"]),
            )
        plan, projection = build_launch(
            revision,
            str(body["plane_id"]),
            str(body["node"]),
            host,
            self.config.state_dir / "invocations" / str(body["id"]) / "controls",
            adapter_resolver=self.adapter_resolver,
            project_root=self.config.project_root,
            invocation_id=str(body["id"]),
            seam_input=frozen_input,
        )
        if canonical(projection) != canonical(body["launch"]):
            raise RouterError("invocation_integrity")
        return plan

    def active(self, invocation_id: str, pid: int) -> None:
        if type(pid) is not int or pid <= 0:
            raise RouterError("invalid_process_identity")
        row = self.db.execute(
            "SELECT body FROM invocations WHERE id=?", (invocation_id,)
        ).fetchone()
        assert row is not None
        body = mapping(strict_json(cast(str, row[0])))
        body["pid"] = pid
        with self.transaction():
            run = self.run(str(body["run_id"]))
            self.update_run(run)
            body["started_version"] = run["state_version"]
            self.db.execute(
                "UPDATE invocations SET body=?,status='active' WHERE id=?",
                (json.dumps(body), invocation_id),
            )
            self.update_run(self.run(cast(str, body["run_id"])))

    def complete(
        self, invocation_id: str, result: CommandResult, *, origin: str = "adapter"
    ) -> None:
        invocation = self.db.execute(
            "SELECT * FROM invocations WHERE id=?", (invocation_id,)
        ).fetchone()
        assert invocation is not None
        old = self.db.execute(
            "SELECT * FROM completions WHERE invocation_id=?", (invocation_id,)
        ).fetchone()
        invocation_body = mapping(strict_json(str(invocation["body"])))
        if old is not None:
            try:
                identical = (
                    canonical(invocation_body.get("adapter_evidence"))
                    == canonical(result.adapter_evidence)
                    and invocation_body.get("origin") == origin
                    and old["diagnostic"] == result.diagnostic
                )
            except (TypeError, ValueError) as exc:
                raise RouterError("conflicting_completion") from exc
            if not identical:
                raise RouterError("conflicting_completion")
            if (
                old["stdout"],
                old["stderr"],
                old["transport"],
                old["writer"],
                old["exit_code"],
            ) != (
                result.stdout,
                result.stderr,
                result.classification,
                result.writer_state,
                result.exit_code,
            ):
                raise RouterError("conflicting_completion")
            return
        facts = CompletionFacts(
            invocation_id,
            cast(int | None, invocation_body["pid"]),
            result.stdout,
            result.stderr,
            result.classification,
            result.writer_state,
            result.diagnostic,
            result.exit_code,
            result.adapter_evidence,
            origin,
        )
        _result(facts)
        plan = self.launch(invocation_body)
        if origin == "adapter":
            _adapter(
                plan.adapter_id, plan.adapter_contract, self.adapter_resolver
            ).validate_completion(plan, facts)
        invocation_body["origin"] = origin
        invocation_body["adapter_evidence"] = (
            _object(result.adapter_evidence)
            if result.adapter_evidence is not None
            else None
        )
        with self.transaction():
            run = self.run(str(invocation["run_id"]))
            self.update_run(run)
            if invocation_body["completed_version"] is None:
                invocation_body["completed_version"] = run["state_version"]
            self.db.execute(
                "UPDATE invocations SET body=? WHERE id=?",
                (json.dumps(invocation_body), invocation_id),
            )
            self.db.execute(
                "INSERT INTO completions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    invocation_id,
                    invocation["run_id"],
                    invocation["revision_id"],
                    invocation["plane_id"],
                    invocation["node"],
                    result.classification,
                    result.stdout,
                    result.stderr,
                    result.writer_state,
                    result.diagnostic,
                    result.exit_code,
                    None,
                    None,
                ),
            )
            self.db.execute(
                "UPDATE invocations SET status='completed' WHERE id=?", (invocation_id,)
            )
            self.update_run(self.run(cast(str, invocation["run_id"])))

    def parsed(self, invocation_id: str) -> tuple[str, str | None]:
        row = self.db.execute(
            "SELECT * FROM completions WHERE invocation_id=?", (invocation_id,)
        ).fetchone()
        assert row is not None
        node = (
            self.revision(cast(str, row["revision_id"]))
            .graph.planes[str(row["plane_id"])]
            .nodes[cast(str, row["node"])]
        )
        workspace = Path(
            str(
                self.revision(str(row["revision_id"])).workspaces[str(row["plane_id"])][
                    "path"
                ]
            )
        )
        invocation = self.db.execute(
            "SELECT body FROM invocations WHERE id=?", (invocation_id,)
        ).fetchone()
        assert invocation is not None
        body = mapping(strict_json(cast(str, invocation[0])))
        if "ref_admission" not in body:
            raise RouterError("completion_integrity")
        if row["classification"] is None:
            if row["outcome"] is not None or body["ref_admission"] is not None:
                raise RouterError("completion_integrity")
            pure = (
                parse_result(
                    cast(bytes, row["stdout"]),
                    node,
                    workspace,
                    resolve_refs=False,
                )
                if row["transport"] == "completed"
                else (cast(str, row["transport"]), None)
            )
            admission = "not_applicable"
            if pure[0] in {"semantic", "unknown"}:
                resolved = parse_result(cast(bytes, row["stdout"]), node, workspace)
                admission = "accepted" if resolved == pure else "rejected"
            expected = recorded_result(
                cast(bytes, row["stdout"]),
                node,
                workspace,
                cast(str, row["transport"]),
                admission,
            )
            body["ref_admission"] = admission
            with self.transaction():
                run = self.run(str(row["run_id"]))
                self.update_run(run)
                body["parsed_version"] = run["state_version"]
                self.db.execute(
                    "UPDATE invocations SET body=? WHERE id=?",
                    (json.dumps(body), invocation_id),
                )
                self.db.execute(
                    "UPDATE completions SET classification=?,outcome=? "
                    "WHERE invocation_id=?",
                    (*expected, invocation_id),
                )
                self.update_run(self.run(cast(str, row["run_id"])))
        else:
            expected = recorded_result(
                cast(bytes, row["stdout"]),
                node,
                workspace,
                cast(str, row["transport"]),
                body["ref_admission"],
            )
            if (row["classification"], row["outcome"]) != expected:
                raise RouterError("completion_integrity")
        return expected

    def pending_completion(
        self, run_id: str, plane_id: str | None = None
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self.db.execute(
                "SELECT c.* FROM completions c JOIN invocations i ON "
                "i.id=c.invocation_id "
                "LEFT JOIN transitions t ON t.completion_id=c.invocation_id "
                "LEFT JOIN moves m ON m.completion_id=c.invocation_id "
                "WHERE c.run_id=? AND t.completion_id IS NULL AND m.completion_id "
                "IS NULL "
                + ("AND c.plane_id=? " if plane_id is not None else "")
                + "ORDER BY c.plane_id,i.attempt LIMIT 1",
                (run_id, plane_id) if plane_id is not None else (run_id,),
            ).fetchone(),
        )

    def writer(self, invocation_id: str) -> str:
        fact = self.db.execute(
            "SELECT writer FROM writer_facts WHERE invocation_id=?", (invocation_id,)
        ).fetchone()
        if fact is not None:
            return str(fact[0])
        row = self.db.execute(
            "SELECT c.writer,i.status FROM invocations i LEFT JOIN completions c "
            "ON c.invocation_id=i.id WHERE i.id=?",
            (invocation_id,),
        ).fetchone()
        if row is None:
            raise RouterError("unknown_invocation")
        return (
            str(row[0])
            if row[0] is not None
            else "unknown"
            if row[1] == "uncertain"
            else "running"
        )

    def quiescent(self, run_id: str) -> bool:
        return all(
            self.writer(str(row[0])) == "stopped"
            for row in self.db.execute(
                "SELECT id FROM invocations WHERE run_id=?", (run_id,)
            )
        )

    def route_pending(self, run_id: str) -> None:
        if self.run(run_id)["run_gate"] is not None:
            return
        for plane_id, plane in self.plane_states(run_id).items():
            if plane["status"] in {*TERMINAL, "paused"}:
                continue
            completion = self.pending_completion(run_id, plane_id)
            if completion is not None:
                self.route(str(completion["invocation_id"]))

    def route(self, invocation_id: str) -> None:
        from .seams import acknowledges

        if self.db.execute(
            "SELECT 1 FROM transitions WHERE completion_id=? UNION ALL "
            "SELECT 1 FROM moves WHERE completion_id=?",
            (invocation_id, invocation_id),
        ).fetchone():
            return
        row = self.db.execute(
            "SELECT * FROM completions WHERE invocation_id=?", (invocation_id,)
        ).fetchone()
        assert row is not None
        run_id, plane_id, node_id = (
            str(row["run_id"]),
            str(row["plane_id"]),
            str(row["node"]),
        )
        run = self.run(run_id)
        revision = self.revision(str(run["revision_id"]))
        nodes = revision.graph.planes[plane_id].nodes
        classification, outcome = self.parsed(invocation_id)
        if node_id not in nodes or (
            classification == "semantic" and outcome not in nodes[node_id].routes
        ):
            raise RouterError("incompatible_revision")
        if self.writer(invocation_id) != "stopped":
            raise RouterError("writer_unknown")
        counter = self.db.execute(
            "SELECT count FROM retries WHERE run_id=? AND plane_id=? AND node=?",
            (run_id, plane_id, node_id),
        ).fetchone()
        count = int(counter[0]) if counter else 0
        selected, delta = select_action(nodes[node_id], classification, outcome, count)
        if selected.kind == "retry" and time.time() >= cast(float, run["deadline"]):
            self.set_gate(run_id, "limit_exhausted")
            return
        target = (
            node_id
            if selected.kind == "retry"
            else selected.target
            if selected.kind != "finish"
            else None
        )
        with self.transaction():
            plane = self.plane_states(run_id)[plane_id]
            plane.update(
                cursor=target,
                active_invocation=None,
                pause_reason=None,
                resume_target=None,
                status="ready",
            )
            if selected.kind == "finish":
                plane.update(status=selected.target)
            elif selected.kind == "pause":
                plane.update(
                    status="paused",
                    pause_reason=selected.reason,
                    resume_target=selected.target,
                )
            run = self.run(run_id)
            self.update_run(run)
            version = run["state_version"]
            self.db.execute(
                "INSERT INTO transitions VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    invocation_id,
                    run_id,
                    revision.revision_id,
                    plane_id,
                    node_id,
                    canonical(selected.data()),
                    target,
                    delta,
                    version,
                ),
            )
            if delta:
                self.db.execute(
                    "INSERT INTO retries VALUES(?,?,?,?) "
                    "ON CONFLICT(run_id,plane_id,node) "
                    "DO UPDATE SET count=excluded.count",
                    (run_id, plane_id, node_id, count + delta),
                )
            if classification == "semantic":
                result = mapping(strict_json(bytes(row["stdout"])))
                producer = self.revision(str(row["revision_id"]))
                for seam_id, seam in revision.graph.seams.items():
                    if (seam.producer_plane, seam.producer_node, seam.outcome) != (
                        plane_id,
                        node_id,
                        outcome,
                    ):
                        continue
                    sequence = self.db.execute(
                        "SELECT COALESCE(MAX(sequence),0)+1 FROM seam_occurrences "
                        "WHERE run_id=? AND seam_id=?",
                        (run_id, seam_id),
                    ).fetchone()[0]
                    envelope = {
                        "format": "millrace-seam-input/v1",
                        "run_id": run_id,
                        "consumer_plane_id": seam.consumer_plane,
                        "consumer_node_id": seam.consumer_node,
                        "seam_id": seam_id,
                        "sequence": sequence,
                        "producer_plane_id": plane_id,
                        "producer_node_id": node_id,
                        "producer_invocation_id": invocation_id,
                        "producing_revision": producer.revision_id,
                        "completion_id": invocation_id,
                        "producer_workspace": producer.workspaces[plane_id]["path"],
                        "result": result,
                    }
                    self.db.execute(
                        "INSERT INTO seam_occurrences VALUES(?,?,?,?,?,?,?,?)",
                        (
                            run_id,
                            seam_id,
                            sequence,
                            invocation_id,
                            invocation_id,
                            invocation_id,
                            version,
                            canonical(envelope),
                        ),
                    )
            if acknowledges(selected, node_id):
                pinned = mapping(plane["selected"])
                progress = mapping(plane["progress"])
                for seam_id, sequence in list(pinned.items()):
                    if revision.graph.seams[seam_id].consumer_node != node_id:
                        continue
                    self.db.execute(
                        "INSERT INTO seam_acknowledgements VALUES(?,?,?,?,?)",
                        (run_id, seam_id, sequence, invocation_id, version),
                    )
                    progress[seam_id] = sequence
                    del pinned[seam_id]
                plane.update(selected=pinned, progress=progress)
            self.save_plane(run_id, plane, "routed", {"invocation_id": invocation_id})
            self.recompute(run_id)

    def uncertain(self, invocation_id: str, *, diagnostic: str = "interrupted") -> None:
        row = self.db.execute(
            "SELECT run_id,status FROM invocations WHERE id=?", (invocation_id,)
        ).fetchone()
        assert row is not None
        if row["status"] == "uncertain":
            return
        with self.transaction():
            body = mapping(
                strict_json(
                    str(
                        self.db.execute(
                            "SELECT body FROM invocations WHERE id=?", (invocation_id,)
                        ).fetchone()[0]
                    )
                )
            )
            run = self.run(str(row["run_id"]))
            self.update_run(run)
            body["uncertainty_diagnostic"] = diagnostic
            body["completed_version"] = run["state_version"]
            self.db.execute(
                "UPDATE invocations SET status='uncertain',body=? WHERE id=?",
                (json.dumps(body), invocation_id),
            )
            self.set_gate(str(row["run_id"]), "writer_unknown")

    def draft(self, run_id: str) -> Candidate:
        run = self.run(run_id)
        host = mapping(run["host_settings"])
        candidate = capture_candidate(
            self.config.workflow_file,
            self.config.project_root,
            search_path=tuple(cast(list[str], host["path"])),
            host_paths=cast(dict[str, str], host["host_paths"]),
            baseline=self.revision(cast(str, run["revision_id"])),
        )
        if candidate.revision is not None:
            try:
                resolve_nodes(
                    candidate.revision,
                    settings_context=host,
                    adapter_resolver=self.adapter_resolver,
                    project_root=self.config.project_root,
                )
            except RouterError as exc:
                value = candidate.data()
                encoded = json.dumps(
                    {"files": value["files"], "diagnostic": exc.code}, sort_keys=True
                )
                return Candidate(
                    hashlib.sha256(encoded.encode()).hexdigest(),
                    None,
                    candidate.files,
                    exc.code,
                )
        return candidate

    def observe(
        self, run_id: str, candidate: Candidate, *, request_id: str | None = None
    ) -> bool:
        run = self.run(run_id)
        last = self.db.execute(
            "SELECT seq,body FROM candidates WHERE run_id=? ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        old = mapping(strict_json(str(last["body"]))) if last else None
        if old is not None and old["candidate_id"] == candidate.candidate_id:
            return False
        if old is None and candidate.candidate_id == run["revision_id"]:
            return False
        restored = candidate.candidate_id == run["revision_id"]
        seq = int(last["seq"]) + 1 if last else 1
        value = candidate.data()
        value.update(
            seq=seq,
            supersedes=int(last["seq"]) if last else None,
            withdrawn=restored,
            changed_paths=candidate.changed(
                self.revision(cast(str, run["revision_id"]))
            ),
        )
        with self.transaction():
            if candidate.revision is not None:
                self.db.execute(
                    "INSERT OR IGNORE INTO revisions VALUES(?,?)",
                    (candidate.revision.revision_id, candidate.revision.encoded()),
                )
            run["pending_seq"] = None if restored else seq
            self.update_run(run)
            self.db.execute(
                "INSERT INTO candidates VALUES(?,?,?,?,?)",
                (
                    run_id,
                    seq,
                    json.dumps(value, sort_keys=True),
                    run["state_version"],
                    request_id,
                ),
            )
        return True

    def pending_candidate(self, run_id: str) -> dict[str, object] | None:
        run = self.run(run_id)
        if run["pending_seq"] is None:
            return None
        row = self.db.execute(
            "SELECT body FROM candidates WHERE run_id=? AND seq=?",
            (run_id, run["pending_seq"]),
        ).fetchone()
        if row is None:
            raise RouterError("revision_integrity")
        return mapping(strict_json(str(row[0])))

    def activate(self, run_id: str, *, request_id: str | None = None) -> None:
        run = self.run(run_id)
        candidate = self.pending_candidate(run_id)
        if candidate is None:
            return
        if candidate["diagnostic"] is not None or candidate["revision_id"] is None:
            raise RouterError("invalid_revision")
        revision = self.revision(cast(str, candidate["revision_id"]))
        from .planes import check_run_compatibility

        startup, planes, unrouted = self.compatibility_facts(run_id)
        check_run_compatibility(startup, revision, planes, unrouted)
        with self.transaction():
            pending_seq = run["pending_seq"]
            run.update(
                revision_id=revision.revision_id,
                pending_seq=None,
                cap=min(cast(int, run["startup_cap"]), revision.graph.max_invocations),
                max_elapsed=min(
                    cast(float, run["startup_elapsed"]), revision.graph.max_elapsed
                ),
                deadline=min(
                    cast(float, run["startup_deadline"]),
                    cast(float, run["started_at"]) + revision.graph.max_elapsed,
                ),
            )
            self.update_run(run)
            self.db.execute(
                "INSERT INTO activations VALUES(?,?,?,?,?)",
                (
                    run_id,
                    revision.revision_id,
                    run["state_version"],
                    pending_seq,
                    request_id,
                ),
            )

    def move(
        self,
        run_id: str,
        plane_id: str,
        target: str,
        kind: str,
        reason: str,
        request_id: str,
        *,
        source_invocation: str | None = None,
    ) -> None:
        run = self.run(run_id)
        revision = self.revision(str(run["revision_id"]))
        plane = self.plane_states(run_id)[plane_id]
        if target not in revision.graph.planes[plane_id].nodes:
            raise RouterError("missing_target")
        if plane["status"] != "paused" or self.inflight(run_id, plane_id):
            raise RouterError("plane_not_paused")
        pinned = mapping(plane["selected"])
        if pinned and kind == "redirect":
            raise RouterError("seam_input_pinned")
        if source_invocation is not None:
            source = self.db.execute(
                "SELECT * FROM invocations WHERE id=? AND run_id=? AND plane_id=?",
                (source_invocation, run_id, plane_id),
            ).fetchone()
            if (
                source is None
                or source["node"] != target
                or self.writer(source_invocation) != "stopped"
            ):
                raise RouterError("invalid_target")
            body = mapping(strict_json(str(source["body"])))
            seam = mapping(body["launch"]).get("seam_input")
            if seam is not None:
                item = mapping(seam)
                if self.db.execute(
                    "SELECT 1 FROM seam_acknowledgements WHERE run_id=? AND seam_id=? "
                    "AND sequence=?",
                    (run_id, item["seam_id"], item["sequence"]),
                ).fetchone():
                    raise RouterError("occurrence_already_acknowledged")
                latest = self.db.execute(
                    "SELECT id FROM invocations WHERE run_id=? AND plane_id=? "
                    "ORDER BY attempt DESC LIMIT 1",
                    (run_id, plane_id),
                ).fetchone()
                if (
                    latest is None
                    or latest[0] != source_invocation
                    or pinned.get(str(item["seam_id"])) != item["sequence"]
                    or plane["cursor"] != target
                ):
                    raise RouterError("seam_input_pinned")
            elif pinned:
                raise RouterError("seam_input_pinned")
        completion = self.pending_completion(run_id, plane_id)
        with self.transaction():
            plane.update(
                status="ready",
                pause_reason=None,
                cursor=target,
                resume_target=None,
                active_invocation=None,
            )
            self.save_plane(run_id, plane, "move", {"request_id": request_id})
            version = self.run(run_id)["state_version"]
            self.db.execute(
                "INSERT INTO moves VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    version,
                    plane_id,
                    kind,
                    target,
                    source_invocation,
                    completion["invocation_id"] if completion else None,
                    revision.revision_id,
                    reason,
                    request_id,
                ),
            )
            if kind == "retry":
                self.db.execute(
                    "INSERT INTO retries VALUES(?,?,?,1) "
                    "ON CONFLICT(run_id,plane_id,node) "
                    "DO UPDATE SET count=count+1",
                    (run_id, plane_id, target),
                )
            self.recompute(run_id)

    def reconcile(
        self, run_id: str, invocation_id: str, reason: str, request_id: str
    ) -> None:
        row = self.db.execute(
            "SELECT * FROM invocations WHERE id=? AND run_id=?", (invocation_id, run_id)
        ).fetchone()
        if row is None:
            raise RouterError("invalid_target")
        if row["status"] != "uncertain" and not (
            row["status"] == "completed"
            and self.writer(invocation_id) in {"may_live", "unknown"}
        ):
            raise RouterError("invocation_not_reconcilable")
        with self.transaction():
            if (
                self.db.execute(
                    "SELECT 1 FROM completions WHERE invocation_id=?", (invocation_id,)
                ).fetchone()
                is None
            ):
                self.complete(
                    invocation_id,
                    CommandResult(b"", b"", "interrupted", "unknown", "interrupted"),
                    origin="core_interruption",
                )
            run = self.run(run_id)
            self.update_run(run)
            self.db.execute(
                "INSERT INTO writer_facts VALUES(?,?,'stopped',?,?)",
                (invocation_id, run["state_version"], reason, request_id),
            )

    def inspection(self, run_id: str) -> dict[str, object]:
        result = self.run(run_id)
        result["planes"] = self.plane_states(run_id)
        result["inflight_invocations"] = self.inflight(run_id)
        result["quiescent"] = self.quiescent(run_id)
        result["invocations"] = [
            {
                **mapping(strict_json(str(row["body"]))),
                "status": row["status"],
                "writer_state": self.writer(str(row["id"])),
            }
            for row in self.db.execute(
                "SELECT * FROM invocations WHERE run_id=? ORDER BY attempt", (run_id,)
            )
        ]
        result["completions"] = [
            {
                **{
                    key: row[key]
                    for key in row.keys()
                    if key not in {"stdout", "stderr"}
                },
                "effective_writer": self.writer(str(row["invocation_id"])),
                "stdout_base64": base64.b64encode(cast(bytes, row["stdout"])).decode(),
                "stdout": cast(bytes, row["stdout"]).decode("utf-8", "replace"),
                "stderr": cast(bytes, row["stderr"]).decode("utf-8", "replace"),
            }
            for row in self.db.execute(
                "SELECT * FROM completions WHERE run_id=?", (run_id,)
            )
        ]
        for key, table in (
            ("transitions", "transitions"),
            ("operator_moves", "moves"),
            ("activations", "activations"),
            ("state_changes", "states"),
        ):
            order = (
                "version,plane_id" if table in {"transitions", "moves"} else "version"
            )
            result[key] = [
                dict(row)
                for row in self.db.execute(
                    f"SELECT * FROM {table} WHERE run_id=? ORDER BY {order}", (run_id,)
                )
            ]
        result["candidates"] = [
            mapping(strict_json(str(row["body"])))
            for row in self.db.execute(
                "SELECT * FROM candidates WHERE run_id=? ORDER BY seq", (run_id,)
            )
        ]
        result["pending_revision"] = self.pending_candidate(run_id)
        result["active_revision"] = result["revision_id"]
        try:
            result["draft_revision"] = self.draft(run_id).data()
        except RouterError as exc:
            result["draft_revision"] = {"diagnostic": exc.code}
        result["selected_bindings"] = self.revision(
            cast(str, result["revision_id"])
        ).graph.bindings
        result["writer_facts"] = [
            dict(row)
            for row in self.db.execute(
                "SELECT f.* FROM writer_facts f JOIN invocations i ON "
                "i.id=f.invocation_id WHERE i.run_id=?",
                (run_id,),
            )
        ]
        result["controls"] = [
            mapping(strict_json(str(row[0])))
            for row in self.db.execute(
                "SELECT receipt FROM controls WHERE run_id=? ORDER BY seq", (run_id,)
            )
        ]
        result["retry_counts"] = {
            plane_id: {
                str(row[0]): row[1]
                for row in self.db.execute(
                    "SELECT node,count FROM retries WHERE run_id=? AND plane_id=?",
                    (run_id, plane_id),
                )
            }
            for plane_id in self.plane_states(run_id)
        }
        for table, ordering in (
            ("plane_events", "version,ordinal"),
            ("run_gate_events", "version,ordinal"),
            ("seam_occurrences", "seam_id,sequence"),
            ("seam_selections", "seam_id,sequence"),
            ("seam_acknowledgements", "seam_id,sequence"),
        ):
            result[table] = [
                dict(row)
                for row in self.db.execute(
                    f"SELECT * FROM {table} WHERE run_id=? ORDER BY {ordering}",
                    (run_id,),
                )
            ]
        return result

    def validate(self) -> None:
        from .store_validation import validate_store

        validate_store(self)
