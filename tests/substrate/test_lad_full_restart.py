from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from millrace.substrate.errors import StorageIntegrityError
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import lad_learning


def test_restart_refuses_full_lad_selected_plan_drift(tmp_path: Path) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.closed_source_learning_effect_state(
        plan,
        fingerprint,
        reconciliation_status="applied",
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    wrong_fingerprint = f"sha256:{'f' * 64}"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE admitted_plan_pins SET authority_fingerprint = ?",
            (wrong_fingerprint,),
        )
        connection.execute(
            "UPDATE default_plan SET authority_fingerprint = ?",
            (wrong_fingerprint,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="selected plan authority fingerprint mismatch",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_preserves_full_lad_learning_closure_effect_and_intervention(
    tmp_path: Path,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    effect_state = lad_learning.closure_librarian_effect_state(
        plan,
        fingerprint,
        reconciliation_status="applied",
    )
    wait_state, wait = lad_learning.closed_source_learning_blocked_wait_state(
        plan,
        fingerprint,
    )

    effect_path = tmp_path / "effect"
    wait_path = tmp_path / "wait"
    effect_path.mkdir()
    wait_path.mkdir()

    loaded_effect = persist_and_load_runtime_state(effect_path, effect_state)
    loaded_wait = persist_and_load_runtime_state(wait_path, wait_state)

    assert loaded_effect == effect_state
    assert loaded_wait == wait_state
    assert loaded_effect.closure_targets["closure-target-learning"].status == "open"
    assert next(iter(loaded_effect.effect_reconciliations.values())).status == "applied"
    assert loaded_wait.operator_waits[wait.wait_id].status == "active"


@pytest.mark.parametrize("state_kind", lad_learning.FULL_LAD_CLOSURE_ROOT_STATE_KINDS)
@pytest.mark.parametrize(
    ("drift", "match"),
    (
        (
            "plan_fingerprint",
            "closure_targets.plan_authority_fingerprint",
        ),
        (
            "root_inventory_queue_family",
            "activations.queue_family_id must match work_items.queue_family_id",
        ),
        (
            "root_work_item_plan_ref",
            "work_items.plan_authority_fingerprint must reference admitted_plan_pins",
        ),
        (
            "root_source_kind",
            "closure_targets manual root source must not reference runtime inventory",
        ),
        (
            "root_source_id",
            "closure_targets root source must exist in runtime inventory",
        ),
        (
            "closure_root_work_item_id",
            "closure_targets.closure_root_work_item_id must match runtime inventory",
        ),
        (
            "lineage_id",
            "closure_targets.lineage_id must match runtime inventory root lineage",
        ),
        (
            "closed_state",
            "closure_targets.closed_by_record_id",
        ),
    ),
)
def test_restart_refuses_full_lad_closure_root_authority_drift(
    tmp_path: Path,
    state_kind: str,
    drift: str,
    match: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.full_lad_closure_root_state(
        plan,
        fingerprint,
        state_kind=state_kind,
    )
    legal_root = tmp_path / "legal"
    legal_root.mkdir()
    assert persist_and_load_runtime_state(legal_root, state) == state
    corrupt_root = tmp_path / "corrupt"
    corrupt_root.mkdir()
    db_path, cas_root = runtime_store_paths(corrupt_root)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        if drift == "plan_fingerprint":
            connection.execute(
                """
                UPDATE closure_targets
                SET plan_authority_fingerprint = ?
                WHERE closure_target_id = ?
                """,
                (f"sha256:{'f' * 64}", "closure-target-learning"),
            )
        elif drift == "root_inventory_queue_family":
            connection.execute(
                """
                UPDATE work_items
                SET queue_family_id = ?
                WHERE work_item_id = ?
                """,
                ("stage_result", "root-spec-closure"),
            )
        elif drift == "root_work_item_plan_ref":
            connection.execute(
                """
                UPDATE work_items
                SET plan_authority_fingerprint = ?
                WHERE work_item_id = ?
                """,
                (f"sha256:{'e' * 64}", "root-spec-closure"),
            )
        elif drift == "root_source_kind":
            connection.execute(
                """
                UPDATE closure_targets
                SET root_source_kind = ?
                WHERE closure_target_id = ?
                """,
                ("manual", "closure-target-learning"),
            )
        elif drift == "root_source_id":
            connection.execute(
                """
                UPDATE closure_targets
                SET root_source_id = ?
                WHERE closure_target_id = ?
                """,
                ("wrong-source", "closure-target-learning"),
            )
        elif drift == "closure_root_work_item_id":
            connection.execute(
                """
                UPDATE closure_targets
                SET closure_root_work_item_id = ?
                WHERE closure_target_id = ?
                """,
                ("wrong-root-work-item", "closure-target-learning"),
            )
        elif drift == "lineage_id":
            connection.execute(
                """
                UPDATE closure_targets
                SET lineage_id = ?
                WHERE closure_target_id = ?
                """,
                ("wrong-lineage", "closure-target-learning"),
            )
        elif drift == "closed_state":
            connection.execute(
                """
                UPDATE closure_targets
                SET status = ?, closed_by_record_id = ?
                WHERE closure_target_id = ?
                """,
                (
                    "closed",
                    "missing-closure-terminal-record",
                    "closure-target-learning",
                ),
            )
        else:  # pragma: no cover - parameter table guard
            raise AssertionError(f"unhandled closure-root drift case: {drift}")

    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)
