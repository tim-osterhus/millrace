"""SQLite runtime store schema and metadata validation."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import TypeAlias

from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.state import DURABLE_INT64_MAX, RUNNER_SESSION_TEXT_MAX_BYTES
from millrace.substrate.errors import (
    StoreNotInitialized,
    StoreSchemaUpgradeRequired,
    UnsupportedStoreSchemaVersion,
)
from millrace.substrate.records import (
    SQLITE_STORE_CREATED_BY,
    SQLITE_STORE_INITIALIZATION_MARKER,
    SQLITE_STORE_KIND,
    SQLITE_STORE_SCHEMA_VERSION,
    WORKFLOW_PACKAGE_COMMAND_OPERATION_IDS,
    WORKFLOW_PACKAGE_MUTATION_COMMAND_OPERATION_IDS,
)

StoreSchemaMetadata: TypeAlias = Mapping[str, str | int]
_PLAN_FORMAT_VERSION = SelectedCompiledPlan.schema_version
_QUEUE_CLOSURE_IDS_JSON_MAX_BYTES = 1024 * 1024
_PACKAGE_COMMAND_OPERATIONS_SQL = ", ".join(
    f"'{operation_id}'" for operation_id in WORKFLOW_PACKAGE_COMMAND_OPERATION_IDS
)
_PACKAGE_MUTATION_COMMAND_OPERATIONS_SQL = ", ".join(
    f"'{operation_id}'"
    for operation_id in WORKFLOW_PACKAGE_MUTATION_COMMAND_OPERATION_IDS
)

_METADATA_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS store_metadata (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    store_kind TEXT NOT NULL,
    store_schema_version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    initialization_marker TEXT NOT NULL
)
"""

_RUNTIME_TABLE_SQL = (
    f"""
    CREATE TABLE IF NOT EXISTS admitted_plan_pins (
        authority_fingerprint TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        selected_plan_digest TEXT NOT NULL,
        admitted_at_order INTEGER NOT NULL CHECK (admitted_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS default_plan (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        plan_id TEXT NOT NULL,
        authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        selected_plan_digest TEXT NOT NULL,
        set_at_order INTEGER NOT NULL CHECK (set_at_order >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS input_receipts (
        input_id TEXT PRIMARY KEY,
        input_payload_digest TEXT NOT NULL,
        transition_id TEXT NOT NULL,
        accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
        refusal_reason TEXT,
        received_at_order INTEGER NOT NULL CHECK (received_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS work_items (
        work_item_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        payload_digest TEXT NOT NULL,
        queue_family_id TEXT NOT NULL,
        lineage_id TEXT,
        created_by_input_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0),
        UNIQUE (work_item_id, generation)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS activations (
        activation_id TEXT PRIMARY KEY,
        work_item_id TEXT NOT NULL,
        lineage_id TEXT,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        queue_family_id TEXT NOT NULL,
        graph_node_id TEXT NOT NULL,
        stage_kind_id TEXT NOT NULL,
        runner_binding_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        created_by_input_id TEXT NOT NULL,
        claimed_by_run_id TEXT,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        activation_id TEXT NOT NULL,
        work_item_id TEXT NOT NULL,
        claim_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        fencing_token TEXT NOT NULL,
        stage_kind_id TEXT NOT NULL,
        runner_binding_id TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        current_session_id TEXT CHECK (
            current_session_id IS NULL
            OR length(CAST(current_session_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        last_dispatch_generation INTEGER NOT NULL CHECK (
            last_dispatch_generation >= 0
        ),
        started_at_order INTEGER NOT NULL CHECK (started_at_order >= 0),
        UNIQUE (activation_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS runner_sessions (
        schema_version INTEGER NOT NULL CHECK (schema_version = 2),
        session_id TEXT PRIMARY KEY CHECK (
            length(CAST(session_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        run_id TEXT NOT NULL CHECK (
            length(CAST(run_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        dispatch_generation INTEGER NOT NULL CHECK (dispatch_generation >= 1),
        session_fencing_token TEXT NOT NULL CHECK (
            length(CAST(session_fencing_token AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        state TEXT NOT NULL CHECK (
            state IN (
                'created',
                'starting',
                'running',
                'cancellation_requested',
                'terminating',
                'completed',
                'interrupted',
                'failed',
                'lost'
            )
        ),
        created_at INTEGER NOT NULL CHECK (created_at >= 0),
        start_intent_at INTEGER CHECK (
            start_intent_at IS NULL OR start_intent_at >= 0
        ),
        started_at INTEGER CHECK (started_at IS NULL OR started_at >= 0),
        ended_at INTEGER CHECK (ended_at IS NULL OR ended_at >= 0),
        durable_locator_digest TEXT CHECK (
            durable_locator_digest IS NULL
            OR (
                length(CAST(durable_locator_digest AS BLOB)) = 71
                AND substr(durable_locator_digest, 1, 7) = 'sha256:'
                AND substr(durable_locator_digest, 8)
                    NOT GLOB '*[^0-9a-f]*'
            )
        ),
        cleanup_disposition TEXT NOT NULL CHECK (
            cleanup_disposition IN (
                'pending',
                'not_required',
                'complete',
                'orphan_risk'
            )
        ),
        context_manifest_digest TEXT CHECK (
            context_manifest_digest IS NULL
            OR (
                length(CAST(context_manifest_digest AS BLOB)) = 71
                AND substr(context_manifest_digest, 1, 7) = 'sha256:'
                AND substr(context_manifest_digest, 8)
                    NOT GLOB '*[^0-9a-f]*'
            )
        ),
        UNIQUE (run_id, dispatch_generation)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS runner_session_cancellation_requests (
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        request_id TEXT PRIMARY KEY CHECK (
            length(CAST(request_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        session_id TEXT NOT NULL CHECK (
            length(CAST(session_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        dispatch_generation INTEGER NOT NULL CHECK (dispatch_generation >= 1),
        reason TEXT NOT NULL CHECK (
            reason IN (
                'operator_cancel_work',
                'daemon_shutdown',
                'runner_timeout',
                'runtime_failure'
            )
        ),
        source_kind TEXT NOT NULL CHECK (
            source_kind IN ('operator', 'daemon', 'runtime')
        ),
        actor_id TEXT NOT NULL CHECK (
            length(CAST(actor_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        requested_at INTEGER NOT NULL CHECK (requested_at >= 0),
        request_order INTEGER NOT NULL CHECK (request_order >= 1),
        primary_request INTEGER NOT NULL CHECK (primary_request IN (0, 1)),
        CHECK (
            (reason = 'operator_cancel_work' AND source_kind = 'operator')
            OR (reason = 'daemon_shutdown' AND source_kind = 'daemon')
            OR (
                reason IN ('runner_timeout', 'runtime_failure')
                AND source_kind = 'runtime'
            )
        ),
        UNIQUE (session_id, request_order)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS runner_session_cancellation_attempts (
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        attempt_id TEXT PRIMARY KEY CHECK (
            length(CAST(attempt_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        session_id TEXT NOT NULL CHECK (
            length(CAST(session_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        request_id TEXT NOT NULL CHECK (
            length(CAST(request_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        operation TEXT NOT NULL CHECK (
            operation IN (
                'cooperative_cancel',
                'terminate',
                'kill',
                'transport_cleanup'
            )
        ),
        result TEXT NOT NULL CHECK (
            result IN ('succeeded', 'failed', 'timed_out', 'unsupported')
        ),
        started_at INTEGER NOT NULL CHECK (started_at >= 0),
        completed_at INTEGER NOT NULL CHECK (completed_at >= 0),
        bounded_diagnostic_digest TEXT NOT NULL CHECK (
            length(CAST(bounded_diagnostic_digest AS BLOB)) = 71
            AND substr(bounded_diagnostic_digest, 1, 7) = 'sha256:'
            AND substr(bounded_diagnostic_digest, 8)
                NOT GLOB '*[^0-9a-f]*'
        ),
        UNIQUE (session_id, sequence)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS runner_session_completions (
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        completion_id TEXT PRIMARY KEY CHECK (
            length(CAST(completion_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        session_id TEXT NOT NULL UNIQUE CHECK (
            length(CAST(session_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        run_id TEXT NOT NULL CHECK (
            length(CAST(run_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        dispatch_generation INTEGER NOT NULL CHECK (dispatch_generation >= 1),
        session_fencing_token TEXT NOT NULL CHECK (
            length(CAST(session_fencing_token AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        terminal_state TEXT NOT NULL CHECK (
            terminal_state IN ('completed', 'interrupted', 'failed', 'lost')
        ),
        exit_kind TEXT NOT NULL CHECK (
            length(CAST(exit_kind AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        adapter_outcome_kind TEXT CHECK (
            adapter_outcome_kind IS NULL
            OR length(CAST(adapter_outcome_kind AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        adapter_error_kind TEXT CHECK (
            adapter_error_kind IS NULL
            OR length(CAST(adapter_error_kind AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        runner_result_evidence_digest TEXT CHECK (
            runner_result_evidence_digest IS NULL
            OR (
                length(CAST(runner_result_evidence_digest AS BLOB)) = 71
                AND substr(runner_result_evidence_digest, 1, 7) = 'sha256:'
                AND substr(runner_result_evidence_digest, 8)
                    NOT GLOB '*[^0-9a-f]*'
            )
        ),
        primary_cancellation_request_id TEXT CHECK (
            primary_cancellation_request_id IS NULL
            OR length(CAST(primary_cancellation_request_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        cleanup_disposition TEXT NOT NULL CHECK (
            cleanup_disposition IN ('not_required', 'complete', 'orphan_risk')
        ),
        started_at INTEGER CHECK (started_at IS NULL OR started_at >= 0),
        cancel_requested_at INTEGER CHECK (
            cancel_requested_at IS NULL OR cancel_requested_at >= 0
        ),
        completed_at INTEGER NOT NULL CHECK (completed_at >= 0),
        bounds_summary TEXT NOT NULL CHECK (
            length(CAST(bounds_summary AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        truncation_metadata TEXT NOT NULL CHECK (
            length(CAST(truncation_metadata AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        redaction_policy_id TEXT NOT NULL CHECK (
            length(CAST(redaction_policy_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        diagnostic_digest TEXT NOT NULL CHECK (
            length(CAST(diagnostic_digest AS BLOB)) = 71
            AND substr(diagnostic_digest, 1, 7) = 'sha256:'
            AND substr(diagnostic_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        application_input_id TEXT NOT NULL UNIQUE CHECK (
            length(CAST(application_input_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        CHECK (
            (primary_cancellation_request_id IS NULL)
            = (cancel_requested_at IS NULL)
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runner_observations (
        observation_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        observed_at INTEGER CHECK (observed_at IS NULL OR observed_at >= 0),
        observed_at_order INTEGER NOT NULL CHECK (observed_at_order >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        work_item_id TEXT NOT NULL,
        artifact_schema_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        source_action_id TEXT NOT NULL,
        source_stage_kind_id TEXT NOT NULL,
        source_graph_node_id TEXT NOT NULL,
        artifact_payload_digest TEXT NOT NULL,
        transition_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS effect_proposals (
        effect_id TEXT PRIMARY KEY,
        dedupe_key TEXT NOT NULL UNIQUE,
        effect_declaration_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        selected_plan_fingerprint TEXT NOT NULL,
        terminal_action_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        artifact_schema_id TEXT NOT NULL,
        artifact_payload_digest TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        source_action_id TEXT NOT NULL,
        source_input_id TEXT NOT NULL,
        source_work_item_id TEXT NOT NULL,
        source_activation_id TEXT NOT NULL,
        source_graph_node_id TEXT NOT NULL,
        source_stage_kind_id TEXT NOT NULL,
        source_runner_binding_id TEXT NOT NULL,
        source_queue_family_id TEXT NOT NULL,
        lineage_id TEXT,
        provider_ref TEXT NOT NULL,
        capability_policy_ref TEXT NOT NULL,
        target_ref_kind TEXT NOT NULL,
        target_ref_schema TEXT NOT NULL,
        target_skill_id TEXT,
        target_path_ref TEXT,
        status TEXT NOT NULL CHECK (status = 'pending'),
        created_input_id TEXT NOT NULL,
        created_transition_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS effect_reconciliations (
        reconciliation_id TEXT PRIMARY KEY,
        effect_id TEXT NOT NULL UNIQUE,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        selected_plan_fingerprint TEXT NOT NULL,
        provider_ref TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('applied', 'no_op', 'refused')),
        fake_local_result_digest TEXT NOT NULL,
        created_input_id TEXT NOT NULL,
        created_transition_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS activation_routes (
        record_id TEXT PRIMARY KEY,
        action_id TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        source_work_item_id TEXT NOT NULL,
        target_work_item_id TEXT NOT NULL,
        target_activation_id TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS fanout_records (
        record_id TEXT PRIMARY KEY,
        fanout_id TEXT NOT NULL,
        source_artifact_id TEXT NOT NULL,
        source_artifact_digest TEXT NOT NULL,
        source_work_item_id TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        source_action_id TEXT NOT NULL,
        target_work_item_id TEXT NOT NULL,
        target_activation_id TEXT NOT NULL,
        target_queue_family_id TEXT NOT NULL,
        target_stage_kind_id TEXT NOT NULL,
        target_graph_node_id TEXT NOT NULL,
        item_key TEXT NOT NULL,
        lineage_id TEXT,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        created_by_input_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS work_dependencies (
        dependency_id TEXT PRIMARY KEY,
        dependent_work_item_id TEXT NOT NULL,
        dependency_work_item_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        lineage_id TEXT,
        fanout_record_id TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS closure_targets (
        closure_target_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        completion_behavior_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        root_source_kind TEXT NOT NULL,
        root_source_id TEXT NOT NULL,
        closure_root_work_item_id TEXT,
        request_kind TEXT NOT NULL,
        target_graph_node_id TEXT NOT NULL,
        evidence_window_digest TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
        opened_by_input_id TEXT NOT NULL,
        closed_by_record_id TEXT,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0),
        CHECK (
            (
                status = 'open'
                AND closed_by_record_id IS NULL
            )
            OR (
                status = 'closed'
                AND closed_by_record_id IS NOT NULL
            )
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS closure_evaluations (
        record_id TEXT PRIMARY KEY,
        closure_target_id TEXT NOT NULL,
        completion_behavior_id TEXT NOT NULL,
        request_kind TEXT NOT NULL,
        target_work_item_id TEXT NOT NULL,
        target_activation_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        lineage_id TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS closure_terminal_records (
        record_id TEXT PRIMARY KEY,
        closure_target_id TEXT NOT NULL,
        completion_behavior_id TEXT NOT NULL,
        terminal_kind TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        source_action_id TEXT NOT NULL,
        source_artifact_id TEXT,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        lineage_id TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS remediation_work_records (
        record_id TEXT PRIMARY KEY,
        remediation_policy_id TEXT NOT NULL,
        closure_target_id TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        source_action_id TEXT NOT NULL,
        source_artifact_id TEXT,
        target_work_item_id TEXT NOT NULL,
        target_activation_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        lineage_id TEXT NOT NULL,
        dedupe_key TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS closure_blocked_records (
        record_id TEXT PRIMARY KEY,
        closure_target_id TEXT NOT NULL,
        completion_behavior_id TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        source_action_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        lineage_id TEXT NOT NULL,
        operator_required INTEGER NOT NULL CHECK (operator_required IN (0, 1)),
        created_by_input_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS closed_work_items (
        record_id TEXT PRIMARY KEY,
        work_item_id TEXT NOT NULL,
        source_run_id TEXT,
        action_id TEXT,
        operator_intervention_record_id TEXT,
        close_kind TEXT NOT NULL CHECK (
            close_kind IN (
                'terminal_action',
                'operator_intervention',
                'queue_cancellation'
            )
        ),
        created_by_input_id TEXT NOT NULL,
        closed_at_order INTEGER NOT NULL CHECK (closed_at_order >= 0),
        CHECK (
            (
                close_kind = 'terminal_action'
                AND source_run_id IS NOT NULL
                AND action_id IS NOT NULL
                AND operator_intervention_record_id IS NULL
            )
            OR (
                close_kind = 'operator_intervention'
                AND source_run_id IS NULL
                AND action_id IS NULL
                AND operator_intervention_record_id IS NOT NULL
            )
            OR (
                close_kind = 'queue_cancellation'
                AND source_run_id IS NULL
                AND action_id IS NULL
                AND operator_intervention_record_id IS NULL
            )
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS operator_interventions (
        record_id TEXT PRIMARY KEY,
        created_by_input_id TEXT NOT NULL,
        input_payload_digest TEXT NOT NULL,
        option_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (
            kind IN ('resume_lineage', 'close_lineage', 'revise_lineage')
        ),
        result TEXT NOT NULL CHECK (result IN ('resumed', 'closed', 'revised')),
        policy_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        quarantine_id TEXT NOT NULL,
        recovery_attempt_record_id TEXT NOT NULL,
        recovery_attempt_count INTEGER NOT NULL CHECK (recovery_attempt_count > 0),
        attempt_effect TEXT NOT NULL CHECK (attempt_effect = 'resolve_attempt'),
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        actor_kind TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        reason TEXT NOT NULL CHECK (reason != ''),
        target_work_item_id TEXT,
        target_activation_id TEXT,
        closed_work_item_ids_json TEXT NOT NULL,
        closed_activation_ids_json TEXT NOT NULL,
        closed_run_ids_json TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        payload_reference TEXT,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0),
        CHECK (
            (
                kind = 'resume_lineage'
                AND result = 'resumed'
                AND target_activation_id IS NOT NULL
                AND closed_work_item_ids_json = '[]'
                AND closed_activation_ids_json = '[]'
                AND closed_run_ids_json = '[]'
            )
            OR (
                kind = 'close_lineage'
                AND result = 'closed'
                AND target_activation_id IS NULL
                AND closed_work_item_ids_json != '[]'
            )
            OR (
                kind = 'revise_lineage'
                AND result = 'revised'
                AND target_work_item_id IS NOT NULL
                AND target_activation_id IS NOT NULL
                AND closed_work_item_ids_json = '[]'
                AND closed_activation_ids_json = '[]'
                AND closed_run_ids_json = '[]'
            )
        ),
        CHECK (
            (
                kind IN ('resume_lineage', 'close_lineage')
                AND payload_reference IS NULL
            )
            OR kind = 'revise_lineage'
                AND payload_reference IS NOT NULL
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS operator_waits (
        wait_id TEXT PRIMARY KEY,
        operator_wait_id TEXT NOT NULL,
        source_action_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        source_work_item_id TEXT NOT NULL,
        source_activation_id TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        source_stage_kind_id TEXT NOT NULL,
        source_graph_node_id TEXT NOT NULL,
        source_queue_family_id TEXT NOT NULL,
        source_runner_binding_id TEXT NOT NULL,
        source_artifact_id TEXT,
        status TEXT NOT NULL CHECK (status IN ('active', 'resolved', 'superseded')),
        created_input_id TEXT NOT NULL,
        created_input_payload_digest TEXT NOT NULL,
        resolved_input_id TEXT,
        resolved_input_payload_digest TEXT,
        actor_id TEXT,
        actor_kind TEXT,
        resolution_kind TEXT,
        target_work_item_id TEXT,
        target_activation_id TEXT,
        closed_work_item_ids_json TEXT,
        payload_digest TEXT,
        payload_reference TEXT,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0),
        CHECK (
            (
                status = 'active'
                AND resolved_input_id IS NULL
                AND resolved_input_payload_digest IS NULL
                AND actor_id IS NULL
                AND actor_kind IS NULL
                AND resolution_kind IS NULL
                AND target_work_item_id IS NULL
                AND target_activation_id IS NULL
                AND closed_work_item_ids_json IS NULL
                AND payload_digest IS NULL
                AND payload_reference IS NULL
            )
            OR (
                status IN ('resolved', 'superseded')
                AND resolved_input_id IS NOT NULL
                AND resolved_input_payload_digest IS NOT NULL
                AND actor_id IS NOT NULL
                AND actor_kind IS NOT NULL
                AND resolution_kind IS NOT NULL
                AND closed_work_item_ids_json IS NOT NULL
                AND payload_digest IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pause_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        record_id TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        work_item_id TEXT NOT NULL,
        action_id TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        paused_at_order INTEGER NOT NULL CHECK (paused_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS dispatch_suspension (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        suspension_id TEXT NOT NULL CHECK (
            length(CAST(suspension_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        generation INTEGER NOT NULL CHECK (generation > 0),
        dispatch_generation INTEGER NOT NULL CHECK (dispatch_generation >= 0),
        actor_id TEXT NOT NULL CHECK (
            length(CAST(actor_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        reason TEXT NOT NULL CHECK (
            length(CAST(reason AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        suspended_by_input_id TEXT NOT NULL CHECK (
            length(CAST(suspended_by_input_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        status TEXT NOT NULL CHECK (status IN ('active', 'resumed')),
        resumed_by_input_id TEXT CHECK (
            resumed_by_input_id IS NULL
            OR length(CAST(resumed_by_input_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        resume_actor_id TEXT CHECK (
            resume_actor_id IS NULL
            OR length(CAST(resume_actor_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        resume_reason TEXT CHECK (
            resume_reason IS NULL
            OR length(CAST(resume_reason AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        CHECK (
            (
                status = 'active'
                AND resumed_by_input_id IS NULL
                AND resume_actor_id IS NULL
                AND resume_reason IS NULL
            )
            OR (
                status = 'resumed'
                AND resumed_by_input_id IS NOT NULL
                AND resume_actor_id IS NOT NULL
                AND resume_reason IS NOT NULL
            )
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS daemon_budget_epochs (
        budget_id TEXT PRIMARY KEY CHECK (
            length(CAST(budget_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        workspace_path TEXT NOT NULL CHECK (
            length(CAST(workspace_path AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        max_wall_seconds INTEGER CHECK (
            max_wall_seconds IS NULL
            OR (
                typeof(max_wall_seconds) = 'integer'
                AND max_wall_seconds BETWEEN 1 AND {DURABLE_INT64_MAX}
            )
        ),
        max_invocations INTEGER CHECK (
            max_invocations IS NULL
            OR (
                typeof(max_invocations) = 'integer'
                AND max_invocations BETWEEN 1 AND {DURABLE_INT64_MAX}
            )
        ),
        max_total_tokens INTEGER CHECK (
            max_total_tokens IS NULL
            OR (
                typeof(max_total_tokens) = 'integer'
                AND max_total_tokens BETWEEN 1 AND {DURABLE_INT64_MAX}
            )
        ),
        started_at INTEGER NOT NULL CHECK (
            typeof(started_at) = 'integer'
            AND started_at BETWEEN 0 AND {DURABLE_INT64_MAX}
        ),
        wall_deadline INTEGER CHECK (
            wall_deadline IS NULL
            OR (
                typeof(wall_deadline) = 'integer'
                AND wall_deadline BETWEEN 0 AND {DURABLE_INT64_MAX}
            )
        ),
        last_observed_at INTEGER NOT NULL CHECK (
            typeof(last_observed_at) = 'integer'
            AND last_observed_at BETWEEN started_at AND {DURABLE_INT64_MAX}
        ),
        accepted_start_count INTEGER NOT NULL CHECK (
            typeof(accepted_start_count) = 'integer'
            AND accepted_start_count BETWEEN 0 AND {DURABLE_INT64_MAX}
        ),
        cumulative_input_tokens INTEGER NOT NULL CHECK (
            typeof(cumulative_input_tokens) = 'integer'
            AND cumulative_input_tokens BETWEEN 0 AND {DURABLE_INT64_MAX}
        ),
        cumulative_output_tokens INTEGER NOT NULL CHECK (
            typeof(cumulative_output_tokens) = 'integer'
            AND cumulative_output_tokens BETWEEN 0 AND {DURABLE_INT64_MAX}
        ),
        cumulative_total_tokens INTEGER NOT NULL CHECK (
            typeof(cumulative_total_tokens) = 'integer'
            AND cumulative_total_tokens BETWEEN 0 AND {DURABLE_INT64_MAX}
            AND
            cumulative_total_tokens
                = cumulative_input_tokens + cumulative_output_tokens
        ),
        status TEXT NOT NULL CHECK (
            status IN ('active', 'exhausted', 'refused', 'stopped')
        ),
        terminal_reason TEXT CHECK (
            terminal_reason IS NULL
            OR length(CAST(terminal_reason AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        CHECK (
            max_wall_seconds IS NOT NULL
            OR max_invocations IS NOT NULL
            OR max_total_tokens IS NOT NULL
        ),
        CHECK (
            (max_wall_seconds IS NULL AND wall_deadline IS NULL)
            OR wall_deadline = started_at + max_wall_seconds
        ),
        CHECK (
            (status = 'active' AND terminal_reason IS NULL)
            OR (status != 'active' AND terminal_reason IS NOT NULL)
        ),
        FOREIGN KEY (
            plan_authority_fingerprint
        ) REFERENCES admitted_plan_pins(authority_fingerprint)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS runner_session_usage (
        session_id TEXT PRIMARY KEY CHECK (
            length(CAST(session_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        budget_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        dispatch_generation INTEGER NOT NULL CHECK (
            typeof(dispatch_generation) = 'integer'
            AND dispatch_generation BETWEEN 1 AND {DURABLE_INT64_MAX}
        ),
        session_fencing_token TEXT NOT NULL CHECK (
            length(CAST(session_fencing_token AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        input_tokens INTEGER NOT NULL CHECK (
            typeof(input_tokens) = 'integer'
            AND input_tokens BETWEEN 0 AND {DURABLE_INT64_MAX}
        ),
        output_tokens INTEGER NOT NULL CHECK (
            typeof(output_tokens) = 'integer'
            AND output_tokens BETWEEN 0 AND {DURABLE_INT64_MAX}
        ),
        total_tokens INTEGER NOT NULL CHECK (
            typeof(total_tokens) = 'integer'
            AND total_tokens BETWEEN 0 AND {DURABLE_INT64_MAX}
            AND
            total_tokens = input_tokens + output_tokens
        ),
        observed_at INTEGER NOT NULL CHECK (
            typeof(observed_at) = 'integer'
            AND observed_at BETWEEN 0 AND {DURABLE_INT64_MAX}
        ),
        final INTEGER NOT NULL CHECK (final IN (0, 1)),
        FOREIGN KEY (budget_id) REFERENCES daemon_budget_epochs(budget_id),
        FOREIGN KEY (session_id) REFERENCES runner_sessions(session_id),
        FOREIGN KEY (run_id) REFERENCES runs(run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS daemon_budget_sessions (
        session_id TEXT PRIMARY KEY CHECK (
            length(CAST(session_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        budget_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        dispatch_generation INTEGER NOT NULL CHECK (
            typeof(dispatch_generation) = 'integer'
            AND dispatch_generation BETWEEN 1 AND {DURABLE_INT64_MAX}
        ),
        session_fencing_token TEXT NOT NULL CHECK (
            length(CAST(session_fencing_token AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        accepted_at INTEGER CHECK (
            accepted_at IS NULL
            OR (
                typeof(accepted_at) = 'integer'
                AND accepted_at BETWEEN 0 AND {DURABLE_INT64_MAX}
            )
        ),
        FOREIGN KEY (budget_id) REFERENCES daemon_budget_epochs(budget_id),
        FOREIGN KEY (session_id) REFERENCES runner_sessions(session_id),
        FOREIGN KEY (run_id) REFERENCES runs(run_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS queue_closures (
        closure_id TEXT PRIMARY KEY CHECK (
            length(CAST(closure_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        target_kind TEXT NOT NULL CHECK (
            target_kind IN ('work_item', 'lineage')
        ),
        target_id TEXT NOT NULL CHECK (
            length(CAST(target_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        actor_id TEXT NOT NULL CHECK (
            length(CAST(actor_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        reason TEXT NOT NULL CHECK (
            length(CAST(reason AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        created_by_input_id TEXT NOT NULL CHECK (
            length(CAST(created_by_input_id AS BLOB))
                BETWEEN 1 AND {RUNNER_SESSION_TEXT_MAX_BYTES}
        ),
        closed_work_item_ids_json TEXT NOT NULL CHECK (
            length(CAST(closed_work_item_ids_json AS BLOB))
                BETWEEN 3 AND {_QUEUE_CLOSURE_IDS_JSON_MAX_BYTES}
        ),
        closed_activation_ids_json TEXT NOT NULL CHECK (
            length(CAST(closed_activation_ids_json AS BLOB))
                BETWEEN 2 AND {_QUEUE_CLOSURE_IDS_JSON_MAX_BYTES}
        ),
        closed_run_ids_json TEXT NOT NULL CHECK (
            length(CAST(closed_run_ids_json AS BLOB))
                BETWEEN 2 AND {_QUEUE_CLOSURE_IDS_JSON_MAX_BYTES}
        ),
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quarantine_records (
        record_id TEXT PRIMARY KEY,
        work_item_id TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        action_id TEXT NOT NULL,
        created_by_input_id TEXT NOT NULL,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS lineage_quarantines (
        quarantine_id TEXT PRIMARY KEY,
        policy_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        recovery_attempt_record_id TEXT NOT NULL,
        original_source_run_id TEXT NOT NULL,
        original_source_work_item_id TEXT NOT NULL,
        original_source_activation_id TEXT NOT NULL,
        emitting_recovery_activation_id TEXT NOT NULL,
        emitting_recovery_run_id TEXT NOT NULL,
        action_id TEXT NOT NULL,
        attempt_count INTEGER NOT NULL CHECK (attempt_count > 0),
        created_input_id TEXT NOT NULL,
        actor_kind TEXT NOT NULL CHECK (actor_kind IN ('runtime')),
        status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
        superseded_input_id TEXT,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0),
        CHECK (
            (
                status = 'active'
                AND superseded_input_id IS NULL
            )
            OR (
                status = 'superseded'
                AND superseded_input_id IS NOT NULL
            )
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS recovery_attempts (
        record_id TEXT PRIMARY KEY,
        policy_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        attempt_count INTEGER NOT NULL CHECK (attempt_count > 0),
        phase TEXT NOT NULL CHECK (
            phase IN (
                'active_recovery',
                'pending_cooldown',
                'quarantine_eligible',
                'resolved'
            )
        ),
        source_run_id TEXT NOT NULL,
        source_work_item_id TEXT NOT NULL,
        source_activation_id TEXT NOT NULL,
        source_graph_node_id TEXT NOT NULL,
        source_stage_kind_id TEXT NOT NULL,
        source_runner_binding_id TEXT NOT NULL,
        source_queue_family_id TEXT NOT NULL,
        recovery_action_id TEXT NOT NULL,
        latest_recovery_activation_id TEXT,
        latest_recovery_run_id TEXT,
        latest_return_action_id TEXT,
        created_by_input_id TEXT NOT NULL,
        updated_by_input_id TEXT NOT NULL,
        updated_at_order INTEGER NOT NULL CHECK (updated_at_order >= 0)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS cooldown_waits (
        wait_id TEXT PRIMARY KEY,
        policy_id TEXT NOT NULL,
        lineage_id TEXT NOT NULL,
        recovery_attempt_record_id TEXT NOT NULL,
        attempt_count INTEGER NOT NULL CHECK (attempt_count > 0),
        source_run_id TEXT NOT NULL,
        source_work_item_id TEXT NOT NULL,
        source_activation_id TEXT NOT NULL,
        recovery_action_id TEXT NOT NULL,
        target_stage_kind_id TEXT NOT NULL,
        target_graph_node_id TEXT NOT NULL,
        target_runner_binding_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        created_input_id TEXT NOT NULL,
        created_at INTEGER NOT NULL CHECK (created_at >= 0),
        due_at INTEGER NOT NULL CHECK (due_at >= created_at),
        consumed_input_id TEXT,
        consumed_at INTEGER CHECK (consumed_at IS NULL OR consumed_at >= due_at),
        resulting_recovery_activation_id TEXT,
        updated_at_order INTEGER NOT NULL CHECK (updated_at_order >= 0),
        CHECK (
            (
                consumed_input_id IS NULL
                AND consumed_at IS NULL
                AND resulting_recovery_activation_id IS NULL
            )
            OR (
                consumed_input_id IS NOT NULL
                AND consumed_at IS NOT NULL
                AND resulting_recovery_activation_id IS NOT NULL
            )
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS counters (
        record_id TEXT PRIMARY KEY,
        counter_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        plan_authority_fingerprint TEXT NOT NULL,
        plan_format_version INTEGER NOT NULL CHECK (
            plan_format_version = {_PLAN_FORMAT_VERSION}
        ),
        lineage_id TEXT NOT NULL,
        value INTEGER NOT NULL CHECK (value > 0),
        updated_by_input_id TEXT NOT NULL,
        updated_at_order INTEGER NOT NULL CHECK (updated_at_order >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_package_registry (
        record_id TEXT PRIMARY KEY,
        package_id TEXT NOT NULL,
        package_version TEXT NOT NULL,
        package_generation INTEGER NOT NULL,
        package_format_version TEXT NOT NULL,
        manifest_digest TEXT NOT NULL,
        manifest_cas_digest TEXT NOT NULL,
        package_digest TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        source_digest TEXT NOT NULL,
        source_provenance_digest TEXT NOT NULL,
        status TEXT NOT NULL,
        status_generation INTEGER NOT NULL,
        latest_audit_id TEXT NOT NULL,
        import_record_digest TEXT NOT NULL,
        is_current INTEGER NOT NULL CHECK (is_current IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_package_manifests (
        record_id TEXT PRIMARY KEY,
        package_id TEXT NOT NULL,
        package_version TEXT NOT NULL,
        package_generation INTEGER NOT NULL,
        manifest_digest TEXT NOT NULL,
        manifest_cas_digest TEXT NOT NULL,
        byte_length INTEGER NOT NULL CHECK (byte_length >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_package_assets (
        record_id TEXT PRIMARY KEY,
        package_id TEXT NOT NULL,
        package_version TEXT NOT NULL,
        package_generation INTEGER NOT NULL,
        asset_id TEXT NOT NULL,
        package_path TEXT NOT NULL,
        content_digest TEXT NOT NULL,
        byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
        cas_digest TEXT NOT NULL,
        selected_authority_participation TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_package_dependencies (
        record_id TEXT PRIMARY KEY,
        package_id TEXT NOT NULL,
        package_version TEXT NOT NULL,
        package_generation INTEGER NOT NULL,
        dependency_package_id TEXT NOT NULL,
        version_constraint TEXT NOT NULL,
        manifest_digest TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_package_sources (
        record_id TEXT PRIMARY KEY,
        package_id TEXT NOT NULL,
        package_version TEXT NOT NULL,
        package_generation INTEGER NOT NULL,
        source_kind TEXT NOT NULL,
        source_digest TEXT NOT NULL,
        source_provenance_json TEXT NOT NULL,
        source_provenance_digest TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_package_status_history (
        audit_id TEXT PRIMARY KEY,
        package_id TEXT NOT NULL,
        package_version TEXT NOT NULL,
        package_generation INTEGER NOT NULL,
        status_generation INTEGER NOT NULL,
        status TEXT NOT NULL,
        previous_status TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_package_audit_events (
        audit_id TEXT PRIMARY KEY,
        actor_id TEXT NOT NULL,
        actor_kind TEXT NOT NULL,
        created_at TEXT NOT NULL,
        operation TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        old_generation INTEGER,
        new_generation INTEGER NOT NULL,
        diagnostics_summary TEXT NOT NULL,
        package_digest TEXT NOT NULL,
        import_record_digest TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS workflow_package_command_audit_events (
        command_audit_id TEXT PRIMARY KEY CHECK (command_audit_id != ''),
        command_id TEXT NOT NULL UNIQUE CHECK (command_id != ''),
        operation_id TEXT NOT NULL CHECK (
            operation_id IN ({_PACKAGE_COMMAND_OPERATIONS_SQL})
        ),
        actor_id TEXT NOT NULL CHECK (actor_id != ''),
        actor_kind TEXT NOT NULL CHECK (actor_kind != ''),
        created_at TEXT NOT NULL CHECK (created_at != ''),
        outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed')),
        package_id TEXT,
        package_version TEXT,
        package_generation INTEGER CHECK (
            package_generation IS NULL OR package_generation >= 1
        ),
        status TEXT CHECK (
            status IS NULL OR status IN ('imported', 'enabled', 'disabled', 'removed')
        ),
        diagnostics_summary TEXT NOT NULL CHECK (diagnostics_summary != ''),
        error_code TEXT,
        registry_audit_id TEXT,
        package_digest TEXT,
        import_record_digest TEXT,
        CHECK (outcome = 'failed' OR error_code IS NULL),
        CHECK (outcome = 'succeeded' OR error_code IS NOT NULL),
        CHECK (registry_audit_id IS NULL OR outcome = 'succeeded'),
        CHECK (
            operation_id NOT IN ({_PACKAGE_MUTATION_COMMAND_OPERATIONS_SQL})
            OR outcome != 'succeeded'
            OR (
                registry_audit_id IS NOT NULL
                AND package_id IS NOT NULL
                AND package_version IS NOT NULL
                AND package_generation IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transitions (
        transition_order INTEGER PRIMARY KEY CHECK (transition_order >= 0),
        record_id TEXT NOT NULL UNIQUE,
        input_id TEXT NOT NULL,
        input_kind TEXT NOT NULL,
        input_family TEXT NOT NULL,
        accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS governance_events (
        record_id TEXT PRIMARY KEY,
        transition_order INTEGER NOT NULL CHECK (transition_order >= 0),
        input_id TEXT NOT NULL,
        input_kind TEXT NOT NULL,
        input_family TEXT NOT NULL,
        disposition TEXT NOT NULL,
        plan_fingerprint TEXT,
        work_item_id TEXT,
        run_id TEXT,
        action_id TEXT,
        authority_source TEXT,
        refusal_reason TEXT,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS traces (
        record_id TEXT PRIMARY KEY,
        transition_order INTEGER NOT NULL CHECK (transition_order >= 0),
        input_id TEXT NOT NULL,
        input_kind TEXT NOT NULL,
        input_family TEXT NOT NULL,
        disposition TEXT NOT NULL,
        plan_fingerprint TEXT,
        work_item_id TEXT,
        run_id TEXT,
        action_id TEXT,
        authority_source TEXT,
        refusal_reason TEXT,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS refusals (
        record_id TEXT PRIMARY KEY,
        transition_order INTEGER NOT NULL CHECK (transition_order >= 0),
        input_id TEXT NOT NULL,
        input_kind TEXT NOT NULL,
        input_family TEXT NOT NULL,
        reason TEXT NOT NULL,
        detail TEXT,
        created_at_order INTEGER NOT NULL CHECK (created_at_order >= 0)
    )
    """,
)

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


def configure_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")


def table_names(connection: sqlite3.Connection) -> frozenset[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return frozenset(str(row[0]) for row in rows)


def validate_schema_shape(connection: sqlite3.Connection) -> None:
    expected_names = frozenset(EXPECTED_TABLE_COLUMNS)
    if table_names(connection) != expected_names:
        raise StoreNotInitialized("SQLite store schema is not supported")
    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        if _table_columns(connection, table_name) != expected_columns:
            raise StoreNotInitialized("SQLite store schema is not supported")
        if _table_sql(connection, table_name) != _expected_table_sql(table_name):
            raise StoreNotInitialized("SQLite store schema is not supported")


def initialize_schema(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(_METADATA_TABLE_SQL)
        for statement in _RUNTIME_TABLE_SQL:
            connection.execute(statement)
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
            ON CONFLICT(id) DO NOTHING
            """,
            (
                SQLITE_STORE_KIND,
                SQLITE_STORE_SCHEMA_VERSION,
                SQLITE_STORE_CREATED_BY,
                SQLITE_STORE_INITIALIZATION_MARKER,
            ),
        )


def read_metadata(connection: sqlite3.Connection) -> dict[str, str | int]:
    try:
        row = connection.execute(
            """
            SELECT
                store_kind,
                store_schema_version,
                created_by,
                initialization_marker
            FROM store_metadata
            WHERE id = 1
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise StoreNotInitialized(
            "SQLite store is missing initialization marker"
        ) from exc

    if row is None:
        raise StoreNotInitialized("SQLite store is missing initialization marker")

    return {
        "store_kind": row[0],
        "store_schema_version": row[1],
        "created_by": row[2],
        "initialization_marker": row[3],
    }


def validate_metadata(metadata: StoreSchemaMetadata) -> None:
    if metadata["store_kind"] != SQLITE_STORE_KIND:
        raise StoreNotInitialized("SQLite store kind is not supported")
    if metadata["created_by"] != SQLITE_STORE_CREATED_BY:
        raise StoreNotInitialized("SQLite store creator is not supported")
    if metadata["initialization_marker"] != SQLITE_STORE_INITIALIZATION_MARKER:
        raise StoreNotInitialized("SQLite store is missing initialization marker")

    schema_version = metadata["store_schema_version"]
    if schema_version in {6, 7}:
        raise StoreSchemaUpgradeRequired(schema_version)
    if schema_version != SQLITE_STORE_SCHEMA_VERSION:
        raise UnsupportedStoreSchemaVersion(
            "unsupported SQLite store schema version: "
            f"{schema_version}; expected {SQLITE_STORE_SCHEMA_VERSION}"
        )


def _table_columns(connection: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return tuple(str(row[1]) for row in rows)


def _table_sql(connection: sqlite3.Connection, table_name: str) -> str:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise StoreNotInitialized("SQLite store schema is not supported")
    return _normalize_create_table_sql(row[0])


def _expected_table_sql(table_name: str) -> str:
    for statement in (_METADATA_TABLE_SQL, *_RUNTIME_TABLE_SQL):
        if _create_table_name(statement) == table_name:
            return _normalize_create_table_sql(statement)
    raise StoreNotInitialized("SQLite store schema is not supported")


def _create_table_name(statement: str) -> str:
    tokens = statement.strip().split()
    table_index = tokens.index("TABLE")
    name_index = table_index + 1
    if tokens[name_index : name_index + 3] == ["IF", "NOT", "EXISTS"]:
        name_index += 3
    return tokens[name_index]


def _normalize_create_table_sql(statement: str) -> str:
    return " ".join(
        statement.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE").split()
    )
