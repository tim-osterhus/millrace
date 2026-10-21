"""Accepted observation outputs cannot disappear from current or sealed evidence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts.transition import EnqueueWork
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from substrate._runtime_store_support import load_runtime_state, persist_runtime_state
from support import closure_overflow as co
from support import generic_lifecycle as gl


def _bytes(paths: list[Path]) -> dict[str, str]:
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


@pytest.mark.parametrize("evaluated", [False, True], ids=["before-capture", "sealed"])
@pytest.mark.parametrize("request_limit", [8192, 65536], ids=["overflow", "inline"])
def test_deleted_accepted_evidence_refuses_load_without_db_or_cas_writes(
    tmp_path: Path, evaluated: bool, request_limit: int
) -> None:
    factory = co.evaluated_state if evaluated else co.opened_state
    fixture = factory(co.overflow_source(request_limit=request_limit))
    artifact = next(
        item
        for item in fixture.state.artifacts.values()
        if str(item.schema_id) == gl.BETA_REPORT_SCHEMA_ID
    )
    db_path, cas_root = tmp_path / "runtime.sqlite3", tmp_path / "cas"
    persist_runtime_state(db_path, cas_root, fixture.state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "DELETE FROM artifacts WHERE artifact_id = ?", (artifact.artifact_id,)
        )
    # Snapshot after the deliberate corruption; load must not attempt repair.
    before = _bytes(sorted(tmp_path.rglob("*")))
    with pytest.raises(StorageIntegrityError, match="artifact"):
        load_runtime_state(db_path, cas_root)
    assert _bytes(sorted(tmp_path.rglob("*"))) == before


@pytest.mark.parametrize("has_evidence_payload", [False, True])
def test_schema_less_accepted_action_has_no_required_artifact_output(
    tmp_path: Path, has_evidence_payload: bool
) -> None:
    source = co.overflow_source()
    action = next(
        action
        for action in source["terminal_actions"]
        if action["id"] == "lifecycle.beta.complete"
    )
    action.pop("artifact_schema_id")
    plan, fingerprint = gl.compile_lifecycle(source)
    state, _plan, _fingerprint = gl.admitted_state(plan=plan, fingerprint=fingerprint)
    state = gl.enqueue_origin(state)
    state = gl.claim_activation(
        state, activation_id="activation-origin", suffix="origin"
    )
    # The origin must still produce its declared source artifact to fan out.
    state = gl.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-origin",
        input_id="observe-origin",
        marker="SOURCE_READY",
        artifact_payload=state.work_items["work-origin"].payload,
    )
    state = gl.apply_fanout(state, gl.FANOUT_BETA_ID, input_id="fanout-beta")
    activation = next(
        item
        for item in state.activations.values()
        if str(item.stage_kind_id) == "beta_stage"
    )
    state = gl.claim_activation(
        state, activation_id=activation.activation_id, suffix="no-artifact"
    )
    state = gl.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-no-artifact",
        input_id="observe-no-artifact",
        marker="BETA_READY",
        artifact_payload=gl.report_payload("beta") if has_evidence_payload else None,
    )
    assert all(
        item.created_by_input_id != "observe-no-artifact"
        for item in state.artifacts.values()
    )
    db_path, cas_root = tmp_path / "runtime.sqlite3", tmp_path / "cas"
    persist_runtime_state(db_path, cas_root, state)
    loaded = load_runtime_state(db_path, cas_root)
    assert loaded.runner_observations == state.runner_observations


@pytest.mark.parametrize("request_limit", [8192, 65536], ids=["overflow", "inline"])
def test_candidate_persistence_refuses_missing_accepted_output_before_writes(
    tmp_path: Path, request_limit: int
) -> None:
    fixture = co.evaluated_state(co.overflow_source(request_limit=request_limit))
    db_path, cas_root = tmp_path / "runtime.sqlite3", tmp_path / "cas"
    persist_runtime_state(db_path, cas_root, fixture.state)
    store = SQLiteRuntimeStore.open(db_path)
    cas_store = ContentAddressedByteStore(cas_root)
    state = store.load_runtime_state(cas_store)
    # This advances the durable frontier and introduces a genuinely new payload;
    # missing accepted outputs must be refused before even an orphan CAS write.
    payload = {**gl.source_payload(), "body": "unique advancing candidate body"}
    state = gl.apply_accepted_input(
        state,
        EnqueueWork(
            "candidate-new-history",
            queue_family_id=fixture.plan.external_enqueue_routes[0].queue_family_id,
            payload=payload,
        ),
        gl.context(
            "candidate-new-history",
            work_item_id="work-candidate-new-history",
            activation_id="activation-candidate-new-history",
        ),
    )
    artifact = next(
        item
        for item in state.artifacts.values()
        if str(item.schema_id) == gl.BETA_REPORT_SCHEMA_ID
    )
    corrupted = replace(
        state,
        artifacts={
            key: value
            for key, value in state.artifacts.items()
            if key != artifact.artifact_id
        },
    )
    before = _bytes(sorted(tmp_path.rglob("*")))
    try:
        with pytest.raises(StorageIntegrityError, match="artifact"):
            store.persist_runtime_state(corrupted, cas_store)
        assert _bytes(sorted(tmp_path.rglob("*"))) == before
    finally:
        store.close()


@pytest.mark.parametrize("evaluated", [False, True], ids=["before-capture", "sealed"])
@pytest.mark.parametrize("request_limit", [8192, 65536], ids=["overflow", "inline"])
def test_raw_copied_artifact_alias_refuses_load_without_db_or_cas_writes(
    tmp_path: Path, evaluated: bool, request_limit: int
) -> None:
    factory = co.evaluated_state if evaluated else co.opened_state
    fixture = factory(co.overflow_source(request_limit=request_limit))
    artifact = next(
        item
        for item in fixture.state.artifacts.values()
        if str(item.schema_id) == gl.BETA_REPORT_SCHEMA_ID
    )
    db_path, cas_root = tmp_path / "runtime.sqlite3", tmp_path / "cas"
    persist_runtime_state(db_path, cas_root, fixture.state)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact.artifact_id,)
        )
        columns = [column[0] for column in cursor.description]
        values = list(cursor.fetchone())
        values[columns.index("artifact_id")] = "stateful-shadow"
        connection.execute(
            "INSERT INTO artifacts ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            values,
        )
    # The alias shares authentic CAS bytes; only its emitted identity is false.
    before = _bytes(sorted(tmp_path.rglob("*")))
    with pytest.raises(StorageIntegrityError, match="artifact_identity"):
        load_runtime_state(db_path, cas_root)
    assert _bytes(sorted(tmp_path.rglob("*"))) == before


@pytest.mark.parametrize("evaluated", [False, True], ids=["before-capture", "sealed"])
@pytest.mark.parametrize("request_limit", [8192, 65536], ids=["overflow", "inline"])
def test_advancing_candidate_refuses_copied_artifact_alias_before_writes(
    tmp_path: Path, evaluated: bool, request_limit: int
) -> None:
    factory = co.evaluated_state if evaluated else co.opened_state
    fixture = factory(co.overflow_source(request_limit=request_limit))
    db_path, cas_root = tmp_path / "runtime.sqlite3", tmp_path / "cas"
    persist_runtime_state(db_path, cas_root, fixture.state)
    store = SQLiteRuntimeStore.open(db_path)
    cas_store = ContentAddressedByteStore(cas_root)
    try:
        original = store.load_runtime_state(cas_store)
        advanced = gl.apply_accepted_input(
            original,
            EnqueueWork(
                "alias-candidate-new-history",
                queue_family_id=fixture.plan.external_enqueue_routes[0].queue_family_id,
                payload={**gl.source_payload(), "body": "unique alias candidate body"},
            ),
            gl.context(
                "alias-candidate-new-history",
                work_item_id="work-alias-candidate",
                activation_id="activation-alias-candidate",
            ),
        )
        artifact = next(
            item
            for item in advanced.artifacts.values()
            if str(item.schema_id) == gl.BETA_REPORT_SCHEMA_ID
        )
        alias = replace(artifact, artifact_id="stateful-shadow")
        corrupted = replace(
            advanced, artifacts={**advanced.artifacts, alias.artifact_id: alias}
        )
        before = _bytes(sorted(tmp_path.rglob("*")))
        with pytest.raises(StorageIntegrityError, match="artifact_identity"):
            store.persist_runtime_state(corrupted, cas_store)
        assert _bytes(sorted(tmp_path.rglob("*"))) == before
        assert store.load_runtime_state(cas_store) == original
    finally:
        store.close()
