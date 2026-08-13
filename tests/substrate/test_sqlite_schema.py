from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from millrace.contracts.compiled_plan import SelectedCompiledPlan

SUBSTRATE_ROOT = Path(__file__).resolve().parents[2] / "src/millrace/substrate"

EXPECTED_TABLE_COLUMNS = {
    "store_metadata": (
        "id",
        "store_kind",
        "store_schema_version",
        "created_by",
        "initialization_marker",
    ),
    "admitted_plan_pins": (
        "authority_fingerprint",
        "plan_id",
        "plan_format_version",
        "selected_plan_digest",
        "admitted_at_order",
    ),
    "default_plan": (
        "id",
        "plan_id",
        "authority_fingerprint",
        "plan_format_version",
        "selected_plan_digest",
        "set_at_order",
    ),
    "input_receipts": (
        "input_id",
        "input_payload_digest",
        "transition_id",
        "accepted",
        "refusal_reason",
        "received_at_order",
    ),
    "work_items": (
        "work_item_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "generation",
        "payload_digest",
        "queue_family_id",
        "lineage_id",
        "created_by_input_id",
        "created_at_order",
    ),
    "activations": (
        "activation_id",
        "work_item_id",
        "lineage_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "queue_family_id",
        "graph_node_id",
        "stage_kind_id",
        "runner_binding_id",
        "generation",
        "created_by_input_id",
        "claimed_by_run_id",
        "created_at_order",
    ),
    "runs": (
        "run_id",
        "activation_id",
        "work_item_id",
        "claim_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "generation",
        "fencing_token",
        "stage_kind_id",
        "runner_binding_id",
        "created_by_input_id",
        "current_session_id",
        "last_dispatch_generation",
        "started_at_order",
    ),
    "runner_sessions": (
        "schema_version",
        "session_id",
        "run_id",
        "dispatch_generation",
        "session_fencing_token",
        "state",
        "created_at",
        "start_intent_at",
        "started_at",
        "ended_at",
        "durable_locator_digest",
        "cleanup_disposition",
        "context_manifest_digest",
    ),
    "runner_session_cancellation_requests": (
        "schema_version",
        "request_id",
        "session_id",
        "dispatch_generation",
        "reason",
        "source_kind",
        "actor_id",
        "requested_at",
        "request_order",
        "primary_request",
    ),
    "runner_session_cancellation_attempts": (
        "schema_version",
        "attempt_id",
        "session_id",
        "request_id",
        "sequence",
        "operation",
        "result",
        "started_at",
        "completed_at",
        "bounded_diagnostic_digest",
    ),
    "runner_session_completions": (
        "schema_version",
        "completion_id",
        "session_id",
        "run_id",
        "dispatch_generation",
        "session_fencing_token",
        "terminal_state",
        "exit_kind",
        "adapter_outcome_kind",
        "adapter_error_kind",
        "runner_result_evidence_digest",
        "primary_cancellation_request_id",
        "cleanup_disposition",
        "started_at",
        "cancel_requested_at",
        "completed_at",
        "bounds_summary",
        "truncation_metadata",
        "redaction_policy_id",
        "diagnostic_digest",
        "application_input_id",
    ),
    "runner_observations": (
        "observation_id",
        "run_id",
        "payload_digest",
        "created_by_input_id",
        "observed_at",
        "observed_at_order",
    ),
    "artifacts": (
        "artifact_id",
        "work_item_id",
        "artifact_schema_id",
        "payload_digest",
        "created_by_input_id",
        "source_run_id",
        "source_action_id",
        "source_stage_kind_id",
        "source_graph_node_id",
        "artifact_payload_digest",
        "transition_id",
        "created_at_order",
    ),
    "effect_proposals": (
        "effect_id",
        "dedupe_key",
        "effect_declaration_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "selected_plan_fingerprint",
        "terminal_action_id",
        "artifact_id",
        "artifact_schema_id",
        "artifact_payload_digest",
        "source_run_id",
        "source_action_id",
        "source_input_id",
        "source_work_item_id",
        "source_activation_id",
        "source_graph_node_id",
        "source_stage_kind_id",
        "source_runner_binding_id",
        "source_queue_family_id",
        "lineage_id",
        "provider_ref",
        "capability_policy_ref",
        "target_ref_kind",
        "target_ref_schema",
        "target_skill_id",
        "target_path_ref",
        "status",
        "created_input_id",
        "created_transition_id",
        "created_at_order",
    ),
    "effect_reconciliations": (
        "reconciliation_id",
        "effect_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "selected_plan_fingerprint",
        "provider_ref",
        "status",
        "fake_local_result_digest",
        "created_input_id",
        "created_transition_id",
        "created_at_order",
    ),
    "activation_routes": (
        "record_id",
        "action_id",
        "source_run_id",
        "source_work_item_id",
        "target_work_item_id",
        "target_activation_id",
        "created_by_input_id",
        "created_at_order",
    ),
    "fanout_records": (
        "record_id",
        "fanout_id",
        "source_artifact_id",
        "source_artifact_digest",
        "source_work_item_id",
        "source_run_id",
        "source_action_id",
        "target_work_item_id",
        "target_activation_id",
        "target_queue_family_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "item_key",
        "lineage_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "created_by_input_id",
        "created_at_order",
    ),
    "work_dependencies": (
        "dependency_id",
        "dependent_work_item_id",
        "dependency_work_item_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "lineage_id",
        "fanout_record_id",
        "created_by_input_id",
        "created_at_order",
    ),
    "closure_targets": (
        "closure_target_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "completion_behavior_id",
        "lineage_id",
        "root_source_kind",
        "root_source_id",
        "closure_root_work_item_id",
        "request_kind",
        "target_graph_node_id",
        "evidence_window_digest",
        "status",
        "opened_by_input_id",
        "closed_by_record_id",
        "created_at_order",
    ),
    "closure_evaluations": (
        "record_id",
        "closure_target_id",
        "completion_behavior_id",
        "request_kind",
        "target_work_item_id",
        "target_activation_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "lineage_id",
        "created_by_input_id",
        "created_at_order",
    ),
    "closure_terminal_records": (
        "record_id",
        "closure_target_id",
        "completion_behavior_id",
        "terminal_kind",
        "source_run_id",
        "source_action_id",
        "source_artifact_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "lineage_id",
        "created_by_input_id",
        "created_at_order",
    ),
    "remediation_work_records": (
        "record_id",
        "remediation_policy_id",
        "closure_target_id",
        "source_run_id",
        "source_action_id",
        "source_artifact_id",
        "target_work_item_id",
        "target_activation_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "lineage_id",
        "dedupe_key",
        "created_by_input_id",
        "created_at_order",
    ),
    "closure_blocked_records": (
        "record_id",
        "closure_target_id",
        "completion_behavior_id",
        "source_run_id",
        "source_action_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "lineage_id",
        "operator_required",
        "created_by_input_id",
        "created_at_order",
    ),
    "closed_work_items": (
        "record_id",
        "work_item_id",
        "source_run_id",
        "action_id",
        "operator_intervention_record_id",
        "close_kind",
        "created_by_input_id",
        "closed_at_order",
    ),
    "operator_interventions": (
        "record_id",
        "created_by_input_id",
        "input_payload_digest",
        "option_id",
        "kind",
        "result",
        "policy_id",
        "lineage_id",
        "quarantine_id",
        "recovery_attempt_record_id",
        "recovery_attempt_count",
        "attempt_effect",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "actor_kind",
        "actor_id",
        "reason",
        "target_work_item_id",
        "target_activation_id",
        "closed_work_item_ids_json",
        "closed_activation_ids_json",
        "closed_run_ids_json",
        "payload_digest",
        "payload_reference",
        "created_at_order",
    ),
    "operator_waits": (
        "wait_id",
        "operator_wait_id",
        "source_action_id",
        "lineage_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "source_work_item_id",
        "source_activation_id",
        "source_run_id",
        "source_stage_kind_id",
        "source_graph_node_id",
        "source_queue_family_id",
        "source_runner_binding_id",
        "source_artifact_id",
        "status",
        "created_input_id",
        "created_input_payload_digest",
        "resolved_input_id",
        "resolved_input_payload_digest",
        "actor_id",
        "actor_kind",
        "resolution_kind",
        "target_work_item_id",
        "target_activation_id",
        "closed_work_item_ids_json",
        "payload_digest",
        "payload_reference",
        "created_at_order",
    ),
    "pause_state": (
        "id",
        "record_id",
        "source_run_id",
        "work_item_id",
        "action_id",
        "created_by_input_id",
        "paused_at_order",
    ),
    "dispatch_suspension": (
        "id",
        "schema_version",
        "suspension_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "generation",
        "dispatch_generation",
        "actor_id",
        "reason",
        "suspended_by_input_id",
        "status",
        "resumed_by_input_id",
        "resume_actor_id",
        "resume_reason",
    ),
    "daemon_budget_epochs": (
        "budget_id",
        "schema_version",
        "workspace_path",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "max_wall_seconds",
        "max_invocations",
        "max_total_tokens",
        "started_at",
        "wall_deadline",
        "last_observed_at",
        "accepted_start_count",
        "cumulative_input_tokens",
        "cumulative_output_tokens",
        "cumulative_total_tokens",
        "status",
        "terminal_reason",
    ),
    "runner_session_usage": (
        "session_id",
        "schema_version",
        "budget_id",
        "run_id",
        "dispatch_generation",
        "session_fencing_token",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "observed_at",
        "final",
    ),
    "daemon_budget_sessions": (
        "session_id",
        "schema_version",
        "budget_id",
        "run_id",
        "dispatch_generation",
        "session_fencing_token",
        "accepted_at",
    ),
    "queue_closures": (
        "closure_id",
        "schema_version",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "target_kind",
        "target_id",
        "actor_id",
        "reason",
        "created_by_input_id",
        "closed_work_item_ids_json",
        "closed_activation_ids_json",
        "closed_run_ids_json",
        "created_at_order",
    ),
    "quarantine_records": (
        "record_id",
        "work_item_id",
        "source_run_id",
        "action_id",
        "created_by_input_id",
        "created_at_order",
    ),
    "lineage_quarantines": (
        "quarantine_id",
        "policy_id",
        "lineage_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "recovery_attempt_record_id",
        "original_source_run_id",
        "original_source_work_item_id",
        "original_source_activation_id",
        "emitting_recovery_activation_id",
        "emitting_recovery_run_id",
        "action_id",
        "attempt_count",
        "created_input_id",
        "actor_kind",
        "status",
        "superseded_input_id",
        "created_at_order",
    ),
    "recovery_attempts": (
        "record_id",
        "policy_id",
        "lineage_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "attempt_count",
        "phase",
        "source_run_id",
        "source_work_item_id",
        "source_activation_id",
        "source_graph_node_id",
        "source_stage_kind_id",
        "source_runner_binding_id",
        "source_queue_family_id",
        "recovery_action_id",
        "latest_recovery_activation_id",
        "latest_recovery_run_id",
        "latest_return_action_id",
        "created_by_input_id",
        "updated_by_input_id",
        "updated_at_order",
    ),
    "cooldown_waits": (
        "wait_id",
        "policy_id",
        "lineage_id",
        "recovery_attempt_record_id",
        "attempt_count",
        "source_run_id",
        "source_work_item_id",
        "source_activation_id",
        "recovery_action_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "target_runner_binding_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "created_input_id",
        "created_at",
        "due_at",
        "consumed_input_id",
        "consumed_at",
        "resulting_recovery_activation_id",
        "updated_at_order",
    ),
    "counters": (
        "record_id",
        "counter_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "lineage_id",
        "value",
        "updated_by_input_id",
        "updated_at_order",
    ),
    "workflow_package_registry": (
        "record_id",
        "package_id",
        "package_version",
        "package_generation",
        "package_format_version",
        "manifest_digest",
        "manifest_cas_digest",
        "package_digest",
        "source_kind",
        "source_digest",
        "source_provenance_digest",
        "status",
        "status_generation",
        "latest_audit_id",
        "import_record_digest",
        "is_current",
    ),
    "workflow_package_manifests": (
        "record_id",
        "package_id",
        "package_version",
        "package_generation",
        "manifest_digest",
        "manifest_cas_digest",
        "byte_length",
    ),
    "workflow_package_assets": (
        "record_id",
        "package_id",
        "package_version",
        "package_generation",
        "asset_id",
        "package_path",
        "content_digest",
        "byte_length",
        "cas_digest",
        "selected_authority_participation",
    ),
    "workflow_package_dependencies": (
        "record_id",
        "package_id",
        "package_version",
        "package_generation",
        "dependency_package_id",
        "version_constraint",
        "manifest_digest",
    ),
    "workflow_package_sources": (
        "record_id",
        "package_id",
        "package_version",
        "package_generation",
        "source_kind",
        "source_digest",
        "source_provenance_json",
        "source_provenance_digest",
    ),
    "workflow_package_status_history": (
        "audit_id",
        "package_id",
        "package_version",
        "package_generation",
        "status_generation",
        "status",
        "previous_status",
    ),
    "workflow_package_audit_events": (
        "audit_id",
        "actor_id",
        "actor_kind",
        "created_at",
        "operation",
        "source_kind",
        "old_generation",
        "new_generation",
        "diagnostics_summary",
        "package_digest",
        "import_record_digest",
    ),
    "workflow_package_command_audit_events": (
        "command_audit_id",
        "command_id",
        "operation_id",
        "actor_id",
        "actor_kind",
        "created_at",
        "outcome",
        "package_id",
        "package_version",
        "package_generation",
        "status",
        "diagnostics_summary",
        "error_code",
        "registry_audit_id",
        "package_digest",
        "import_record_digest",
    ),
    "transitions": (
        "transition_order",
        "record_id",
        "input_id",
        "input_kind",
        "input_family",
        "accepted",
        "created_at",
    ),
    "governance_events": (
        "record_id",
        "transition_order",
        "input_id",
        "input_kind",
        "input_family",
        "disposition",
        "plan_fingerprint",
        "work_item_id",
        "run_id",
        "action_id",
        "authority_source",
        "refusal_reason",
        "created_at_order",
    ),
    "traces": (
        "record_id",
        "transition_order",
        "input_id",
        "input_kind",
        "input_family",
        "disposition",
        "plan_fingerprint",
        "work_item_id",
        "run_id",
        "action_id",
        "authority_source",
        "refusal_reason",
        "created_at_order",
    ),
    "refusals": (
        "record_id",
        "transition_order",
        "input_id",
        "input_kind",
        "input_family",
        "reason",
        "detail",
        "created_at_order",
    ),
}


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _table_columns(db_path: Path, table_name: str) -> tuple[str, ...]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return tuple(str(row[1]) for row in rows)


def _table_info(db_path: Path, table_name: str) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(db_path) as connection:
        return tuple(connection.execute(f"PRAGMA table_info({table_name})").fetchall())


def _sqlite_master_snapshot(db_path: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(db_path) as connection:
        return tuple(
            connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        )


def _metadata_rows(db_path: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(db_path) as connection:
        metadata_table = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'store_metadata'
            """
        ).fetchone()
        if metadata_table is None:
            return ()
        return tuple(
            connection.execute(
                """
                SELECT
                    id,
                    store_kind,
                    store_schema_version,
                    created_by,
                    initialization_marker
                FROM store_metadata
                ORDER BY id
                """
            ).fetchall()
        )


def _store_snapshot(db_path: Path) -> tuple[object, ...]:
    return (_sqlite_master_snapshot(db_path), _metadata_rows(db_path))


def _create_metadata_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE store_metadata (
            id INTEGER PRIMARY KEY,
            store_kind TEXT NOT NULL,
            store_schema_version INTEGER NOT NULL,
            created_by TEXT NOT NULL,
            initialization_marker TEXT
        )
        """
    )


def _insert_store_metadata(
    connection: sqlite3.Connection,
    *,
    store_schema_version: int,
    created_by: str | None = None,
) -> None:
    from millrace.substrate.records import (
        SQLITE_STORE_CREATED_BY,
        SQLITE_STORE_INITIALIZATION_MARKER,
        SQLITE_STORE_KIND,
    )

    connection.execute(
        """
        INSERT INTO store_metadata (
            id,
            store_kind,
            store_schema_version,
            created_by,
            initialization_marker
        )
        VALUES (1, ?, ?, ?, ?)
        """,
        (
            SQLITE_STORE_KIND,
            store_schema_version,
            created_by or SQLITE_STORE_CREATED_BY,
            SQLITE_STORE_INITIALIZATION_MARKER,
        ),
    )


def _create_marked_store_metadata(
    db_path: Path,
    *,
    store_schema_version: int,
    created_by: str | None = None,
) -> None:
    with sqlite3.connect(db_path) as connection:
        _create_metadata_table(connection)
        _insert_store_metadata(
            connection,
            store_schema_version=store_schema_version,
            created_by=created_by,
        )


def _replace_runner_observations_with_legacy_shape(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "ALTER TABLE runner_observations RENAME TO runner_observations_new"
        )
        connection.execute(
            """
            CREATE TABLE runner_observations (
                observation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                created_by_input_id TEXT NOT NULL,
                observed_at_order INTEGER NOT NULL CHECK (observed_at_order >= 0)
            )
            """
        )
        connection.execute("DROP TABLE runner_observations_new")


def _sqlite_module_sources() -> dict[str, str]:
    module_paths = (
        SUBSTRATE_ROOT / "sqlite.py",
        *sorted(SUBSTRATE_ROOT.glob("_sqlite*.py")),
    )
    return {
        module_path.name: module_path.read_text(encoding="utf-8")
        for module_path in module_paths
    }


def test_initialize_fresh_store_creates_schema_metadata(tmp_path: Path) -> None:
    from millrace.substrate.records import (
        SQLITE_STORE_CREATED_BY,
        SQLITE_STORE_INITIALIZATION_MARKER,
        SQLITE_STORE_KIND,
        SQLITE_STORE_SCHEMA_VERSION,
    )
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    assert SQLITE_STORE_CREATED_BY == "millrace-ai"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        assert store.schema_metadata() == {
            "store_kind": SQLITE_STORE_KIND,
            "store_schema_version": SQLITE_STORE_SCHEMA_VERSION,
            "created_by": SQLITE_STORE_CREATED_BY,
            "initialization_marker": SQLITE_STORE_INITIALIZATION_MARKER,
        }
    finally:
        store.close()

    reopened = SQLiteRuntimeStore.open(db_path)
    try:
        assert reopened.schema_metadata()["initialization_marker"] == (
            SQLITE_STORE_INITIALIZATION_MARKER
        )
    finally:
        reopened.close()


@pytest.mark.parametrize("operation", ("open", "initialize"))
def test_store_refuses_temporary_creator_without_mutation(
    tmp_path: Path,
    operation: str,
) -> None:
    from millrace.substrate.errors import StoreNotInitialized
    from millrace.substrate.records import SQLITE_STORE_SCHEMA_VERSION
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    _create_marked_store_metadata(
        db_path,
        store_schema_version=SQLITE_STORE_SCHEMA_VERSION,
        created_by="millrace" + "-rewrite",
    )
    before = _store_snapshot(db_path)

    with pytest.raises(StoreNotInitialized, match="creator is not supported"):
        getattr(SQLiteRuntimeStore, operation)(db_path)

    assert _store_snapshot(db_path) == before


def test_open_refuses_missing_initialization_marker(tmp_path: Path) -> None:
    from millrace.substrate.errors import StoreNotInitialized
    from millrace.substrate.records import (
        SQLITE_STORE_CREATED_BY,
        SQLITE_STORE_KIND,
        SQLITE_STORE_SCHEMA_VERSION,
    )
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(db_path) as connection:
        _create_metadata_table(connection)
        connection.execute(
            """
            INSERT INTO store_metadata (
                id,
                store_kind,
                store_schema_version,
                created_by,
                initialization_marker
            )
            VALUES (1, ?, ?, ?, NULL)
            """,
            (SQLITE_STORE_KIND, SQLITE_STORE_SCHEMA_VERSION, SQLITE_STORE_CREATED_BY),
        )

    with pytest.raises(StoreNotInitialized, match="initialization marker"):
        SQLiteRuntimeStore.open(db_path)


def test_open_refuses_store_schema_version_5_without_mutation(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import UnsupportedStoreSchemaVersion
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    _create_marked_store_metadata(db_path, store_schema_version=5)

    before = _store_snapshot(db_path)
    with pytest.raises(UnsupportedStoreSchemaVersion, match="5"):
        SQLiteRuntimeStore.open(db_path)
    assert _store_snapshot(db_path) == before


def test_open_refuses_unknown_store_schema_version_without_mutation(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import UnsupportedStoreSchemaVersion
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    _create_marked_store_metadata(db_path, store_schema_version=10)

    before = _store_snapshot(db_path)
    with pytest.raises(UnsupportedStoreSchemaVersion, match="10"):
        SQLiteRuntimeStore.open(db_path)
    assert _store_snapshot(db_path) == before


def test_sqlite_schema_includes_package_registry_asset_source_and_audit_tables(
    tmp_path: Path,
) -> None:
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    assert {
        "workflow_package_registry",
        "workflow_package_manifests",
        "workflow_package_assets",
        "workflow_package_sources",
        "workflow_package_status_history",
        "workflow_package_audit_events",
    }.issubset(_table_names(db_path))


def test_sqlite_schema_version_bumps_for_package_registry_tables() -> None:
    from millrace.substrate.records import SQLITE_STORE_SCHEMA_VERSION

    assert SQLITE_STORE_SCHEMA_VERSION >= 4


def test_workflow_package_command_audit_schema_bumps_sqlite_store_version() -> None:
    from millrace.substrate.records import SQLITE_STORE_SCHEMA_VERSION

    assert SQLITE_STORE_SCHEMA_VERSION >= 5


def test_runner_session_schema_uses_store_version_9() -> None:
    from millrace.substrate.records import SQLITE_STORE_SCHEMA_VERSION

    assert SQLITE_STORE_SCHEMA_VERSION == 9


@pytest.mark.parametrize("operation", ("open", "initialize"))
def test_schema_8_open_and_initialize_refuse_without_byte_mutation(
    tmp_path: Path,
    operation: str,
) -> None:
    from millrace.substrate.errors import UnsupportedStoreSchemaVersion
    from millrace.substrate.records import SQLITE_STORE_SCHEMA_VERSION
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE store_metadata SET store_schema_version = 8"
        )
    before = db_path.read_bytes()

    with pytest.raises(
        UnsupportedStoreSchemaVersion,
        match=rf"8; expected {SQLITE_STORE_SCHEMA_VERSION}",
    ):
        getattr(SQLiteRuntimeStore, operation)(db_path)

    assert db_path.read_bytes() == before


def test_daemon_budget_store_schema_remains_version_9() -> None:
    from millrace.substrate.records import SQLITE_STORE_SCHEMA_VERSION

    assert SQLITE_STORE_SCHEMA_VERSION == 9


def test_open_refuses_package_registry_table_shape_drift(tmp_path: Path) -> None:
    from millrace.substrate.errors import StoreNotInitialized
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("ALTER TABLE workflow_package_registry RENAME TO drifted")

    with pytest.raises(StoreNotInitialized, match="schema is not supported"):
        SQLiteRuntimeStore.open(db_path)


def test_workflow_package_command_audit_table_shape_and_checks(
    tmp_path: Path,
) -> None:
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    assert _table_columns(db_path, "workflow_package_command_audit_events") == (
        "command_audit_id",
        "command_id",
        "operation_id",
        "actor_id",
        "actor_kind",
        "created_at",
        "outcome",
        "package_id",
        "package_version",
        "package_generation",
        "status",
        "diagnostics_summary",
        "error_code",
        "registry_audit_id",
        "package_digest",
        "import_record_digest",
    )
    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO workflow_package_command_audit_events VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    "audit-invalid-operation",
                    "cmd-invalid-operation",
                    "package.unknown",
                    "operator:local",
                    "local_operator",
                    "1970-01-01T00:00:00Z",
                    "failed",
                    None,
                    None,
                    None,
                    None,
                    "error:unsupported_package_operation",
                    "unsupported_package_operation",
                    None,
                    None,
                    None,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO workflow_package_command_audit_events VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    "audit-split-brain",
                    "cmd-split-brain",
                    "package.import_archive",
                    "operator:local",
                    "local_operator",
                    "1970-01-01T00:00:00Z",
                    "succeeded",
                    "pkg.example.operator",
                    "1.0.0",
                    1,
                    "imported",
                    "diagnostics:0",
                    None,
                    None,
                    "sha256:" + ("a" * 64),
                    "sha256:" + ("b" * 64),
                ),
            )


def test_workflow_package_command_audit_schema_drift_is_refused(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StoreNotInitialized
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            ALTER TABLE workflow_package_command_audit_events
            RENAME TO workflow_package_command_audit_events_old
            """
        )
        connection.execute(
            """
            CREATE TABLE workflow_package_command_audit_events (
                command_audit_id TEXT PRIMARY KEY,
                command_id TEXT NOT NULL UNIQUE,
                operation_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_kind TEXT NOT NULL,
                created_at TEXT NOT NULL,
                outcome TEXT NOT NULL,
                package_id TEXT,
                package_version TEXT,
                package_generation INTEGER,
                status TEXT,
                diagnostics_summary TEXT,
                error_code TEXT,
                registry_audit_id TEXT,
                package_digest TEXT,
                import_record_digest TEXT
            )
            """
        )
        connection.execute("DROP TABLE workflow_package_command_audit_events_old")

    with pytest.raises(StoreNotInitialized, match="schema"):
        SQLiteRuntimeStore.open(db_path)


def test_initialize_refuses_store_schema_version_5_without_mutation(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import UnsupportedStoreSchemaVersion
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    _create_marked_store_metadata(db_path, store_schema_version=5)

    before = _store_snapshot(db_path)
    with pytest.raises(UnsupportedStoreSchemaVersion, match="5"):
        SQLiteRuntimeStore.initialize(db_path)
    assert _store_snapshot(db_path) == before


def test_initialize_refuses_store_schema_version_10_without_mutation(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import UnsupportedStoreSchemaVersion
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    _create_marked_store_metadata(db_path, store_schema_version=10)

    before = _store_snapshot(db_path)
    with pytest.raises(UnsupportedStoreSchemaVersion, match="10"):
        SQLiteRuntimeStore.initialize(db_path)
    assert _store_snapshot(db_path) == before


def test_open_refuses_v6_store_with_legacy_runner_observation_shape_without_mutation(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StoreSchemaUpgradeRequired
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE store_metadata SET store_schema_version = 6")
    _replace_runner_observations_with_legacy_shape(db_path)

    before = _store_snapshot(db_path)
    with pytest.raises(StoreSchemaUpgradeRequired, match="upgrade"):
        SQLiteRuntimeStore.open(db_path)
    assert _store_snapshot(db_path) == before


def test_initialize_refuses_existing_unmarked_sqlite_database_without_mutating_it(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StoreNotInitialized
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "external.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE external_records (id TEXT PRIMARY KEY)")

    with pytest.raises(StoreNotInitialized, match="initialization marker"):
        SQLiteRuntimeStore.initialize(db_path)

    assert _table_names(db_path) == {"external_records"}


def test_initialize_refuses_marked_store_with_extra_table_without_mutating_it(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StoreNotInitialized
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE external_records (id TEXT PRIMARY KEY)")

    with pytest.raises(StoreNotInitialized, match="schema"):
        SQLiteRuntimeStore.initialize(db_path)

    assert _table_names(db_path) == {*EXPECTED_TABLE_COLUMNS, "external_records"}


def test_open_refuses_marked_store_with_drifted_schema(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StoreNotInitialized
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE external_records (id TEXT PRIMARY KEY)")

    with pytest.raises(StoreNotInitialized, match="schema"):
        SQLiteRuntimeStore.open(db_path)


def test_initialize_existing_empty_sqlite_database_creates_schema(
    tmp_path: Path,
) -> None:
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "empty.sqlite3"
    sqlite3.connect(db_path).close()

    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    assert _table_names(db_path) == set(EXPECTED_TABLE_COLUMNS)


def test_reinitialize_existing_store_is_idempotent_without_dropping_state(
    tmp_path: Path,
) -> None:
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO admitted_plan_pins (
                authority_fingerprint,
                plan_id,
                plan_format_version,
                selected_plan_digest,
                admitted_at_order
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "authority:fingerprint-a",
                "plan-a",
                SelectedCompiledPlan.schema_version,
                f"sha256:{'a' * 64}",
                7,
            ),
        )

    reopened = SQLiteRuntimeStore.initialize(db_path)
    reopened.close()

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                authority_fingerprint,
                plan_id,
                plan_format_version,
                selected_plan_digest,
                admitted_at_order
            FROM admitted_plan_pins
            """
        ).fetchall()

    assert rows == [
        (
            "authority:fingerprint-a",
            "plan-a",
            SelectedCompiledPlan.schema_version,
            f"sha256:{'a' * 64}",
            7,
        )
    ]


def test_schema_creates_expected_runtime_tables_without_old_workspace_tables(
    tmp_path: Path,
) -> None:
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    tables = _table_names(db_path)

    assert tables == set(EXPECTED_TABLE_COLUMNS)
    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        assert _table_columns(db_path, table_name) == expected_columns


def test_runner_observation_schema_separates_observed_at_from_order(
    tmp_path: Path,
) -> None:
    from millrace.contracts.state import DURABLE_INT64_MAX
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    table_info = {
        str(row[1]): {"type": str(row[2]), "notnull": int(row[3])}
        for row in _table_info(db_path, "runner_observations")
    }

    assert _table_columns(db_path, "runner_observations") == (
        EXPECTED_TABLE_COLUMNS["runner_observations"]
    )
    assert table_info["observed_at"] == {"type": "INTEGER", "notnull": 0}
    assert table_info["observed_at_order"] == {"type": "INTEGER", "notnull": 1}

    insert_sql = """
        INSERT INTO runner_observations (
            observation_id,
            run_id,
            payload_digest,
            created_by_input_id,
            observed_at,
            observed_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            insert_sql,
            ("observation-null-time", "run-a", "digest-a", "input-a", None, 0),
        )
        connection.execute(
            insert_sql,
            ("observation-zero-time", "run-b", "digest-b", "input-b", 0, 1),
        )
        connection.execute(
            insert_sql,
            (
                "observation-max-time",
                "run-c",
                "digest-c",
                "input-c",
                DURABLE_INT64_MAX,
                2,
            ),
        )
        assert connection.execute(
            """
            SELECT observed_at, observed_at_order
            FROM runner_observations
            ORDER BY observed_at_order
            """
        ).fetchall() == [(None, 0), (0, 1), (DURABLE_INT64_MAX, 2)]

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                insert_sql,
                (
                    "observation-negative-time",
                    "run-d",
                    "digest-d",
                    "input-d",
                    -1,
                    3,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                insert_sql,
                (
                    "observation-missing-order",
                    "run-e",
                    "digest-e",
                    "input-e",
                    456,
                    None,
                ),
            )


def test_sqlite_schema_includes_learning_effect_proposal_and_reconciliation_rows(
    tmp_path: Path,
) -> None:
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    assert _table_columns(db_path, "effect_proposals") == (
        "effect_id",
        "dedupe_key",
        "effect_declaration_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "selected_plan_fingerprint",
        "terminal_action_id",
        "artifact_id",
        "artifact_schema_id",
        "artifact_payload_digest",
        "source_run_id",
        "source_action_id",
        "source_input_id",
        "source_work_item_id",
        "source_activation_id",
        "source_graph_node_id",
        "source_stage_kind_id",
        "source_runner_binding_id",
        "source_queue_family_id",
        "lineage_id",
        "provider_ref",
        "capability_policy_ref",
        "target_ref_kind",
        "target_ref_schema",
        "target_skill_id",
        "target_path_ref",
        "status",
        "created_input_id",
        "created_transition_id",
        "created_at_order",
    )
    assert _table_columns(db_path, "effect_reconciliations") == (
        "reconciliation_id",
        "effect_id",
        "plan_id",
        "plan_authority_fingerprint",
        "plan_format_version",
        "selected_plan_fingerprint",
        "provider_ref",
        "status",
        "fake_local_result_digest",
        "created_input_id",
        "created_transition_id",
        "created_at_order",
    )


def test_sqlite_runtime_rows_use_store_schema_except_versioned_records(
    tmp_path: Path,
) -> None:
    from millrace.substrate.records import SQLITE_STORE_SCHEMA_VERSION
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        assert store.schema_metadata()["store_schema_version"] == (
            SQLITE_STORE_SCHEMA_VERSION
        )
    finally:
        store.close()

    runtime_tables = set(EXPECTED_TABLE_COLUMNS) - {"store_metadata"}
    versioned_record_tables = {
        "runner_sessions",
        "runner_session_cancellation_requests",
        "runner_session_cancellation_attempts",
        "runner_session_completions",
        "dispatch_suspension",
        "daemon_budget_epochs",
        "runner_session_usage",
        "daemon_budget_sessions",
        "queue_closures",
    }
    for table_name in runtime_tables:
        columns = _table_columns(db_path, table_name)
        assert "record_kind" not in columns
        assert ("schema_version" in columns) == (
            table_name in versioned_record_tables
        )


def test_open_refuses_schema_with_same_columns_but_missing_constraints(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StoreNotInitialized
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute("ALTER TABLE input_receipts RENAME TO input_receipts_old")
        connection.execute(
            """
            CREATE TABLE input_receipts (
                input_id TEXT PRIMARY KEY,
                input_payload_digest TEXT NOT NULL,
                transition_id TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                refusal_reason TEXT,
                received_at_order INTEGER NOT NULL
            )
            """
        )
        connection.execute("DROP TABLE input_receipts_old")

    with pytest.raises(StoreNotInitialized, match="schema"):
        SQLiteRuntimeStore.open(db_path)


def test_store_uses_standard_sqlite_without_third_party_dependency() -> None:
    sources = _sqlite_module_sources()
    assert "import sqlite3" in sources["sqlite.py"]
    forbidden_tokens = (
        "alembic",
        "sqlalchemy",
        "peewee",
        "typer",
        "click",
        "argparse",
        "fastapi",
        "flask",
        "subprocess",
        "asyncio",
    )

    assert [
        (module_name, token)
        for module_name, source in sources.items()
        for token in forbidden_tokens
        if token in source
    ] == []


def test_sqlite_split_modules_do_not_use_reflective_serializers() -> None:
    sources = _sqlite_module_sources()
    forbidden_tokens = (
        "pic" + "kle",
        "dataclasses." + "as" + "dict",
        "as" + "dict(",
        "__di" + "ct__",
        "__cla" + "ss__",
    )

    assert [
        (module_name, token)
        for module_name, source in sources.items()
        for token in forbidden_tokens
        if token in source
    ] == []
