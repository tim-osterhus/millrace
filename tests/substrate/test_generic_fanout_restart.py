from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.state import FanoutRecord, RuntimeState
from millrace.contracts.transition import FanoutFromArtifact
from millrace.kernel import apply
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import (
    dumps_cas_object,
    encode_payload,
    encode_selected_compiled_plan,
)
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import decide_with_fake_runner_completion as decide
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import generic_fanout

_OBSERVATION_EVIDENCE_AUTHORITY_ERROR = (
    "runner_observations accepted-input authority invalid: evidence_authority"
)


def _fanned_state():
    plan, fingerprint = generic_fanout.compile_fanout()
    state = generic_fanout.parent_closed_state(plan, fingerprint)
    return apply(
        state,
        decide(
            state,
            FanoutFromArtifact(
                "fanout-parent-packet",
                fanout_id="fanout.packet.children",
                source_artifact_id="transition-observe-parent-done:artifact",
            ),
            generic_fanout.context("fanout-parent-packet"),
        ),
    )


def _first_fanout(state: RuntimeState) -> FanoutRecord:
    return next(iter(state.fanout_records.values()))


def _persist_fanned_state(tmp_path: Path) -> tuple[RuntimeState, Path, Path]:
    state = _fanned_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    return state, db_path, cas_root


def _expect_restart_refuses(
    tmp_path: Path,
    mutate: Callable[[sqlite3.Connection, RuntimeState, Path], None],
    *,
    match: str,
) -> None:
    state, db_path, cas_root = _persist_fanned_state(tmp_path)
    with sqlite3.connect(db_path) as connection:
        mutate(connection, state, cas_root)

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


def _put_payload(cas_root: Path, payload: dict[str, object]) -> str:
    return ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_payload(payload))
    )


def _replace_fingerprint_references(
    connection: sqlite3.Connection,
    *,
    old_fingerprint: str,
    new_fingerprint: str,
) -> None:
    for table_name, column_name in (
        ("admitted_plan_pins", "authority_fingerprint"),
        ("default_plan", "authority_fingerprint"),
        ("work_items", "plan_authority_fingerprint"),
        ("activations", "plan_authority_fingerprint"),
        ("runs", "plan_authority_fingerprint"),
        ("fanout_records", "plan_authority_fingerprint"),
        ("work_dependencies", "plan_authority_fingerprint"),
        ("governance_events", "plan_fingerprint"),
        ("traces", "plan_fingerprint"),
    ):
        connection.execute(
            f"UPDATE {table_name} SET {column_name} = ? WHERE {column_name} = ?",
            (new_fingerprint, old_fingerprint),
        )


def test_fanout_and_dependency_state_survives_restart(tmp_path: Path) -> None:
    state = _fanned_state()
    db_path, cas_root = runtime_store_paths(tmp_path)

    persist_runtime_state(db_path, cas_root, state)
    loaded = load_runtime_state(db_path, cas_root)

    assert loaded.fanout_records == state.fanout_records
    assert loaded.work_dependencies == state.work_dependencies
    assert loaded.work_items == state.work_items
    assert loaded.activations == state.activations


def test_restart_refuses_generated_activation_runner_binding_drift(
    tmp_path: Path,
) -> None:
    def mutate(
        connection: sqlite3.Connection,
        state: RuntimeState,
        _cas_root: Path,
    ) -> None:
        fanout = _first_fanout(state)
        connection.execute(
            "UPDATE activations SET runner_binding_id = ? WHERE activation_id = ?",
            ("fanout.runner.drift", fanout.target_activation_id),
        )

    _expect_restart_refuses(
        tmp_path,
        mutate,
        match="fanout target runner binding",
    )


def test_restart_refuses_generated_target_payload_schema_drift(
    tmp_path: Path,
) -> None:
    def mutate(
        connection: sqlite3.Connection,
        state: RuntimeState,
        cas_root: Path,
    ) -> None:
        fanout = _first_fanout(state)
        wrong_payload_digest = _put_payload(
            cas_root,
            {
                "artifact_kind": generic_fanout.PACKET_SCHEMA_ID,
                "items": [{"item_id": "still-valid-cas", "body": "not child"}],
            },
        )
        connection.execute(
            "UPDATE work_items SET payload_digest = ? WHERE work_item_id = ?",
            (wrong_payload_digest, fanout.target_work_item_id),
        )

    _expect_restart_refuses(
        tmp_path,
        mutate,
        match="fanout target work item payload",
    )


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        (
            "fanout_id",
            "fanout.packet.children.drift",
            "selected fanout_declarations",
        ),
        (
            "source_artifact_digest",
            "sha256:0000000000000000000000000000000000000000000000000000000000000000",
            "source artifact digest",
        ),
        ("source_action_id", "fanout.parent.close.drift", "source fields"),
        ("source_run_id", "run-parent-drift", "source_run_id"),
    ),
)
def test_restart_refuses_fanout_record_source_authority_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    def mutate(
        connection: sqlite3.Connection,
        _state: RuntimeState,
        _cas_root: Path,
    ) -> None:
        connection.execute(f"UPDATE fanout_records SET {column} = ?", (value,))

    _expect_restart_refuses(tmp_path, mutate, match=match)


def test_restart_refuses_fanout_source_work_item_drift(tmp_path: Path) -> None:
    def mutate(
        connection: sqlite3.Connection,
        state: RuntimeState,
        _cas_root: Path,
    ) -> None:
        fanout = _first_fanout(state)
        connection.execute(
            "UPDATE fanout_records SET source_work_item_id = ?",
            (fanout.target_work_item_id,),
        )

    _expect_restart_refuses(tmp_path, mutate, match="source fields")


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        ("target_queue_family_id", "parent", "target queue family"),
        ("target_stage_kind_id", "parent_stage", "target activation context"),
        ("target_graph_node_id", "fanout.parent.start", "target activation context"),
    ),
)
def test_restart_refuses_fanout_target_route_authority_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    def mutate(
        connection: sqlite3.Connection,
        _state: RuntimeState,
        _cas_root: Path,
    ) -> None:
        connection.execute(f"UPDATE fanout_records SET {column} = ?", (value,))

    _expect_restart_refuses(tmp_path, mutate, match=match)


def test_restart_refuses_dependency_fanout_id_drift(tmp_path: Path) -> None:
    def mutate(
        connection: sqlite3.Connection,
        _state: RuntimeState,
        _cas_root: Path,
    ) -> None:
        connection.execute(
            "UPDATE work_dependencies SET fanout_record_id = ?",
            ("fanout-record-drift",),
        )

    _expect_restart_refuses(
        tmp_path,
        mutate,
        match="fanout_record_id",
    )


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        ("plan_id", "drifted-plan", "PlanRef"),
        ("dependency_work_item_id", None, "dependency work items"),
        ("dependent_work_item_id", None, "dependency work items"),
    ),
)
def test_restart_refuses_dependency_plan_source_or_dependent_drift(
    tmp_path: Path,
    column: str,
    value: str | None,
    match: str,
) -> None:
    def mutate(
        connection: sqlite3.Connection,
        state: RuntimeState,
        _cas_root: Path,
    ) -> None:
        fanout = _first_fanout(state)
        drifted_value = (
            value
            if value is not None
            else (
                fanout.target_work_item_id
                if column == "dependency_work_item_id"
                else fanout.source_work_item_id
            )
        )
        connection.execute(
            f"UPDATE work_dependencies SET {column} = ?",
            (drifted_value,),
        )

    _expect_restart_refuses(tmp_path, mutate, match=match)


def test_restart_refuses_source_close_removal(tmp_path: Path) -> None:
    def mutate(
        connection: sqlite3.Connection,
        state: RuntimeState,
        _cas_root: Path,
    ) -> None:
        fanout = _first_fanout(state)
        connection.execute(
            "DELETE FROM closed_work_items WHERE work_item_id = ?",
            (fanout.source_work_item_id,),
        )

    _expect_restart_refuses(
        tmp_path,
        mutate,
        match="source work item must be closed",
    )


def test_restart_refuses_source_close_wrong_action(tmp_path: Path) -> None:
    def mutate(
        connection: sqlite3.Connection,
        state: RuntimeState,
        _cas_root: Path,
    ) -> None:
        fanout = _first_fanout(state)
        connection.execute(
            "UPDATE closed_work_items SET action_id = ? WHERE work_item_id = ?",
            ("fanout.parent.close.drift", fanout.source_work_item_id),
        )

    _expect_restart_refuses(
        tmp_path,
        mutate,
        match="source close must match fanout record",
    )


def test_restart_refuses_selected_fanout_source_action_kind_drift(
    tmp_path: Path,
) -> None:
    state = _fanned_state()
    old_fingerprint = next(iter(state.admitted_plans))
    selected_plan = state.admitted_plans[old_fingerprint].selected_plan
    tampered_plan = generic_fanout.plan_with_valid_route_source_action(selected_plan)
    new_fingerprint = authority_fingerprint(tampered_plan)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    tampered_plan_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_selected_compiled_plan(tampered_plan))
    )
    with sqlite3.connect(db_path) as connection:
        _replace_fingerprint_references(
            connection,
            old_fingerprint=old_fingerprint,
            new_fingerprint=new_fingerprint,
        )
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (tampered_plan_digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (tampered_plan_digest,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match=_OBSERVATION_EVIDENCE_AUTHORITY_ERROR,
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_fanout_record_with_corrupt_target_work(
    tmp_path: Path,
) -> None:
    state = _fanned_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE fanout_records SET target_work_item_id = ?",
            ("missing-work",),
        )

    with pytest.raises(StorageIntegrityError, match="fanout target work item"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_dependency_with_foreign_lineage(tmp_path: Path) -> None:
    state = _fanned_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE work_dependencies SET lineage_id = ?",
            ("foreign-lineage",),
        )

    with pytest.raises(StorageIntegrityError, match="dependency lineage"):
        load_runtime_state(db_path, cas_root)
