"""New relation DDL and connection-level rows; Store owns every transaction."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from .records import RouterError, mapping, strict_json

if TYPE_CHECKING:
    from .graph import Revision

SCHEMA = """
CREATE TABLE planes(run_id TEXT NOT NULL REFERENCES runs(id),
    plane_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(run_id,plane_id));
CREATE TABLE plane_events(run_id TEXT NOT NULL, version INTEGER NOT NULL,
    ordinal INTEGER NOT NULL, plane_id TEXT NOT NULL,
    kind TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY(run_id,version,ordinal),
    FOREIGN KEY(run_id,plane_id) REFERENCES planes(run_id,plane_id),
    FOREIGN KEY(run_id,version) REFERENCES run_versions(run_id,version));
CREATE TABLE run_gate_events(run_id TEXT NOT NULL, version INTEGER NOT NULL,
    ordinal INTEGER NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY(run_id,version,ordinal),
    FOREIGN KEY(run_id,version) REFERENCES run_versions(run_id,version));
CREATE TABLE seam_occurrences(run_id TEXT NOT NULL REFERENCES runs(id),
    seam_id TEXT NOT NULL, sequence INTEGER NOT NULL,
    producer_invocation_id TEXT NOT NULL REFERENCES invocations(id),
    completion_id TEXT NOT NULL REFERENCES completions(invocation_id),
    transition_id TEXT NOT NULL REFERENCES transitions(completion_id),
    version INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY(run_id,seam_id,sequence), UNIQUE(run_id,seam_id,producer_invocation_id),
    FOREIGN KEY(run_id,version) REFERENCES run_versions(run_id,version));
CREATE TABLE seam_selections(run_id TEXT NOT NULL, seam_id TEXT NOT NULL,
    sequence INTEGER NOT NULL, consumer_plane_id TEXT NOT NULL,
    consumer_node_id TEXT NOT NULL,
    version INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(run_id,seam_id,sequence),
    FOREIGN KEY(run_id,seam_id,sequence)
        REFERENCES seam_occurrences(run_id,seam_id,sequence),
    FOREIGN KEY(run_id,consumer_plane_id) REFERENCES planes(run_id,plane_id),
    FOREIGN KEY(run_id,version) REFERENCES run_versions(run_id,version));
CREATE TABLE seam_acknowledgements(run_id TEXT NOT NULL, seam_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    completion_id TEXT NOT NULL REFERENCES transitions(completion_id),
    version INTEGER NOT NULL, PRIMARY KEY(run_id,seam_id,sequence),
    FOREIGN KEY(run_id,seam_id,sequence)
        REFERENCES seam_selections(run_id,seam_id,sequence),
    FOREIGN KEY(run_id,version) REFERENCES run_versions(run_id,version));
"""


def initial_states(revision: Revision) -> dict[str, dict[str, object]]:
    """The startup event is the authority for every initial local cursor."""
    return {
        name: {
            "plane_id": name,
            "workspace": {"declared": plane.workspace, **revision.workspaces[name]},
            "cursor": plane.start,
            "status": "ready",
            "pause_reason": None,
            "resume_target": None,
            "active_invocation": None,
            "selected": {},
            "progress": {},
        }
        for name, plane in sorted(revision.graph.planes.items())
    }


def insert_initial(db: sqlite3.Connection, run_id: str, revision: Revision) -> None:
    if not db.in_transaction:
        raise RouterError("transaction_required")
    for ordinal, (name, body) in enumerate(initial_states(revision).items()):
        encoded = json.dumps(body, sort_keys=True)
        db.execute("INSERT INTO planes VALUES(?,?,?)", (run_id, name, encoded))
        db.execute(
            "INSERT INTO plane_events VALUES(?,1,?,?,'created',?)",
            (run_id, ordinal, name, encoded),
        )


def read_planes(db: sqlite3.Connection, run_id: str) -> dict[str, dict[str, object]]:
    return {
        str(row["plane_id"]): mapping(strict_json(str(row["body"])))
        for row in db.execute(
            "SELECT plane_id,body FROM planes WHERE run_id=? ORDER BY plane_id",
            (run_id,),
        )
    }


def validate_initial(db: sqlite3.Connection, run_id: str, revision: Revision) -> None:
    """Replay exact startup events; a locally valid forged cursor still refuses."""
    expected = initial_states(revision)
    if read_planes(db, run_id) != expected:
        raise RouterError("plane_integrity")
    rows = list(
        db.execute(
            "SELECT * FROM plane_events WHERE run_id=? ORDER BY version,ordinal",
            (run_id,),
        )
    )
    if len(rows) != len(expected):
        raise RouterError("plane_integrity")
    for ordinal, (row, (name, body)) in enumerate(
        zip(rows, expected.items(), strict=True)
    ):
        if (row["version"], row["ordinal"], row["plane_id"], row["kind"]) != (
            1,
            ordinal,
            name,
            "created",
        ) or mapping(strict_json(str(row["body"]))) != body:
            raise RouterError("plane_integrity")
    for table in (
        "run_gate_events",
        "seam_occurrences",
        "seam_selections",
        "seam_acknowledgements",
    ):
        if (
            db.execute(f"SELECT 1 FROM {table} WHERE run_id=?", (run_id,)).fetchone()
            is not None
        ):
            raise RouterError("plane_integrity")
