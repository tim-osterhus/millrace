"""Selected-authority source value validation.

This module owns canonical value diagnostics for selected authority and
validated unselected catalogs. It must not add unselected data to selected plan
construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from unicodedata import normalize

from millrace.compiler.diagnostics import (
    compiler_error,
    non_nfc_authority_map_key_diagnostic,
)
from millrace.compiler.references import collect_id_index
from millrace.compiler.source import is_sequence, mapping, records
from millrace.contracts import Diagnostic

AUTHORITY_VALUE_FIELDS: Mapping[str, tuple[str, ...]] = {
    "graphs": ("presentation",),
    "partitions": ("presentation",),
    "queue_families": ("presentation",),
    "artifact_schemas": ("schema", "presentation"),
    "assets": ("presentation",),
    "stage_kinds": ("presentation",),
    "terminal_outcomes": ("presentation",),
    "terminal_actions": (
        "payload_projection",
        "presentation",
        "dynamic_target_selector",
    ),
    "effect_declarations": ("presentation",),
    "completion_behaviors": ("evidence_window_policy", "presentation"),
    "remediation_policies": ("presentation",),
    "fanout_declarations": ("target_payload_mapping",),
    "runner_bindings": ("presentation",),
}

WORKFLOW_TEXT_FIELDS: tuple[str, ...] = ("name",)

AUTHORITY_TEXT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "partitions": ("kind",),
    "assets": ("kind", "body"),
    "external_enqueue_routes": ("graph_node_id", "payload_schema_id"),
    "generated_work_routes": ("graph_node_id", "payload_schema_id"),
    "terminal_outcomes": ("marker",),
    "terminal_actions": ("kind",),
    "effect_declarations": (
        "terminal_action_id",
        "artifact_schema_id",
        "provider_ref",
        "capability_policy_ref",
        "target_ref_kind",
        "target_ref_schema",
    ),
    "fanout_declarations": (
        "source_action_id",
        "source_artifact_schema_id",
        "item_id_key",
        "target_route_id",
        "source_state_policy",
        "target_payload_schema_id",
        "duplicate_policy",
        "root_lineage_policy",
        "dependency_policy",
    ),
    "join_declarations": (
        "target_stage_kind_id",
        "correlation_key",
        "missing_policy",
    ),
    "concurrency_policies": ("partition_id",),
    "capabilities": ("kind", "support_status", "grant_status"),
    "intervention_options": (
        "policy_id",
        "kind",
        "legal_source_state",
        "target_selector",
        "payload_schema_id",
        "target_queue_family_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "target_runner_binding_id",
        "supersede_behavior",
        "attempt_effect",
        "actor_kind",
    ),
    "operator_waits": (
        "wait_scope",
        "source_work_item_behavior",
        "payload_schema_id",
        "target_queue_family_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "target_runner_binding_id",
        "actor_kind",
        "correlation_key",
        "idempotency",
        "timeout_policy",
        "expiry_policy",
        "cancellation_policy",
        "status_effect",
    ),
    "completion_behaviors": (
        "trigger",
        "readiness_rule",
        "request_kind",
        "target_selector",
        "target_stage_kind_id",
        "target_graph_node_id",
        "runner_binding_id",
        "request_queue_family_id",
        "pass_action_id",
        "gap_action_id",
        "blocked_action_id",
        "verdict_artifact_schema_id",
        "remediation_policy_id",
        "root_source_resolution",
        "evidence_window_policy",
        "rubric_policy",
        "blocked_work_policy",
    ),
    "remediation_policies": (
        "source_action_id",
        "target_queue_family_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "target_runner_binding_id",
        "payload_schema_id",
        "guidance_source",
        "dedupe_key",
        "duplicate_policy",
        "suppression_policy",
        "root_source_kind",
    ),
    "runner_bindings": ("adapter_kind",),
}

AUTHORITY_BOOL_FIELDS: Mapping[str, tuple[str, ...]] = {
    "queue_families": ("external_enqueue",),
    "completion_behaviors": ("skip_if_closed",),
    "operator_waits": ("unrelated_lineages_continue", "project_source_artifact"),
    "effect_declarations": ("real_side_effects_allowed",),
}

AUTHORITY_INT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "concurrency_policies": ("max_active_runs",),
    "runner_bindings": ("invocation_timeout_seconds",),
}

AUTHORITY_TEXT_SEQUENCE_FIELDS: Mapping[str, tuple[str, ...]] = {
    "graphs": ("node_ids",),
    "stage_kinds": (
        "input_queue_family_ids",
        "output_queue_family_ids",
        "artifact_schema_ids",
        "asset_ids",
        "declared_outcome_ids",
    ),
    "terminal_actions": ("asset_ids",),
    "fanout_declarations": ("item_source_path",),
    "join_declarations": ("required_artifact_schema_ids",),
    "concurrency_policies": ("coexist_partition_ids",),
    "intervention_options": ("audit_metadata_requirements",),
    "operator_waits": (
        "source_action_ids",
        "allowed_resolution_kinds",
        "audit_metadata_requirements",
    ),
    "effect_declarations": ("allowed_reconciliation_statuses",),
    "completion_behaviors": ("accepted_root_source_kinds",),
    "runner_bindings": ("stage_kind_ids", "required_capability_ids"),
}

AUTHORITY_OPTIONAL_TEXT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "terminal_actions": ("target_graph_node_id",),
    "capabilities": ("approval_policy_id",),
    "intervention_options": ("resume_target_selector", "close_behavior"),
}

RECOVERY_POLICY_TEXT_FIELDS: tuple[str, ...] = (
    "recovery_stage_kind_id",
    "recorded_source_selector",
    "attempt_scope",
    "threshold_behavior",
)

RECOVERY_POLICY_TEXT_SEQUENCE_FIELDS: tuple[str, ...] = (
    "source_recovery_action_ids",
    "return_action_ids",
    "quarantine_action_ids",
    "return_allowed_phases",
    "reset_trigger_action_ids",
)

RECOVERY_POLICY_INT_FIELDS: tuple[str, ...] = (
    "immediate_recovery_limit",
    "cooldown_starts_at_attempt",
    "quarantine_threshold_attempt",
    "default_cooldown_seconds",
)

UNSELECTED_CATALOG_COLLECTION = "unselected_catalog"

KNOWN_SOURCE_SECTIONS = frozenset(
    (
        "lineage_policy",
        "workflow",
        "graphs",
        "partitions",
        "queue_families",
        "external_enqueue_routes",
        "generated_work_routes",
        "artifact_schemas",
        "assets",
        "stage_kinds",
        "terminal_outcomes",
        "terminal_actions",
        "effect_declarations",
        "fanout_declarations",
        "join_declarations",
        "concurrency_policies",
        "recovery_policies",
        "wait_states",
        "counters",
        "completion_behaviors",
        "remediation_policies",
        "intervention_options",
        "operator_waits",
        "runner_bindings",
        "capabilities",
        UNSELECTED_CATALOG_COLLECTION,
    )
)


def validate_known_source_sections(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    for section in sorted(source):
        if section in KNOWN_SOURCE_SECTIONS:
            continue
        diagnostics.append(
            compiler_error(
                code="unknown_source_section",
                declaration_path=section,
                message="Workflow source contains an unknown top-level section.",
                context={"section": section},
                hint="Remove legacy or unsupported source sections before compile.",
            )
        )


def validate_selected_authority_values(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
    *,
    declaration_path_prefix: str = "",
) -> None:
    workflow = mapping(source.get("workflow"))
    for field_name in WORKFLOW_TEXT_FIELDS:
        if field_name in workflow:
            _validate_text_source_value(
                value=workflow[field_name],
                declaration_path=f"{declaration_path_prefix}workflow.{field_name}",
                diagnostics=diagnostics,
            )

    for collection_key, field_names in AUTHORITY_VALUE_FIELDS.items():
        for index, record in enumerate(records(source, collection_key)):
            for field_name in field_names:
                if field_name not in record:
                    continue
                _validate_canonical_source_value(
                    value=record[field_name],
                    declaration_path=(
                        f"{declaration_path_prefix}{collection_key}[{index}]."
                        f"{field_name}"
                    ),
                    diagnostics=diagnostics,
                )

    for collection_key, field_names in AUTHORITY_TEXT_FIELDS.items():
        for index, record in enumerate(records(source, collection_key)):
            for field_name in field_names:
                if field_name in record:
                    _validate_text_source_value(
                        value=record[field_name],
                        declaration_path=(
                            f"{declaration_path_prefix}{collection_key}[{index}]."
                            f"{field_name}"
                        ),
                        diagnostics=diagnostics,
                    )

    for collection_key, field_names in AUTHORITY_BOOL_FIELDS.items():
        for index, record in enumerate(records(source, collection_key)):
            for field_name in field_names:
                if field_name in record:
                    _validate_bool_source_value(
                        value=record[field_name],
                        declaration_path=(
                            f"{declaration_path_prefix}{collection_key}[{index}]."
                            f"{field_name}"
                        ),
                        diagnostics=diagnostics,
                    )

    for collection_key, field_names in AUTHORITY_INT_FIELDS.items():
        for index, record in enumerate(records(source, collection_key)):
            for field_name in field_names:
                if field_name in record:
                    _validate_int_source_value(
                        value=record[field_name],
                        declaration_path=(
                            f"{declaration_path_prefix}{collection_key}[{index}]."
                            f"{field_name}"
                        ),
                        diagnostics=diagnostics,
                    )

    for collection_key, field_names in AUTHORITY_TEXT_SEQUENCE_FIELDS.items():
        for index, record in enumerate(records(source, collection_key)):
            for field_name in field_names:
                if field_name in record:
                    _validate_text_sequence_source_value(
                        value=record[field_name],
                        declaration_path=(
                            f"{declaration_path_prefix}{collection_key}[{index}]."
                            f"{field_name}"
                        ),
                        diagnostics=diagnostics,
                    )

    for collection_key, field_names in AUTHORITY_OPTIONAL_TEXT_FIELDS.items():
        for index, record in enumerate(records(source, collection_key)):
            for field_name in field_names:
                if field_name in record:
                    _validate_optional_text_source_value(
                        value=record[field_name],
                        declaration_path=(
                            f"{declaration_path_prefix}{collection_key}[{index}]."
                            f"{field_name}"
                        ),
                        diagnostics=diagnostics,
                    )

    _validate_recovery_policy_source_values(
        source,
        diagnostics,
        declaration_path_prefix=declaration_path_prefix,
    )


def validate_unselected_catalog(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    if UNSELECTED_CATALOG_COLLECTION in source and not is_sequence(
        source[UNSELECTED_CATALOG_COLLECTION]
    ):
        diagnostics.append(
            unsupported_authority_value_diagnostic(
                declaration_path=UNSELECTED_CATALOG_COLLECTION,
                unsupported_type=type(source[UNSELECTED_CATALOG_COLLECTION]).__name__,
                value_kind="collection_shape",
            )
        )
        return

    catalog_records = records(source, UNSELECTED_CATALOG_COLLECTION)
    collect_id_index(
        records=catalog_records,
        collection_key=UNSELECTED_CATALOG_COLLECTION,
        namespace=UNSELECTED_CATALOG_COLLECTION,
        diagnostics=diagnostics,
    )
    for index, record in enumerate(catalog_records):
        _validate_canonical_source_value(
            value=record,
            declaration_path=f"{UNSELECTED_CATALOG_COLLECTION}[{index}]",
            diagnostics=diagnostics,
        )


def unsupported_authority_value_diagnostic(
    *,
    declaration_path: str,
    unsupported_type: str,
    value_kind: str,
) -> Diagnostic:
    return compiler_error(
        code="unsupported_authority_value",
        declaration_path=declaration_path,
        message="Authority data contains a non-canonical value.",
        context={
            "unsupported_type": unsupported_type,
            "value_kind": value_kind,
        },
        hint=(
            "Use only null, booleans, integers, strings, arrays, "
            "and string-keyed maps in authority data."
        ),
    )


def _validate_recovery_policy_source_values(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
    *,
    declaration_path_prefix: str = "",
) -> None:
    for index, record in enumerate(records(source, "recovery_policies")):
        referrer_path = f"{declaration_path_prefix}recovery_policies[{index}]"
        for field_name in RECOVERY_POLICY_TEXT_FIELDS:
            _validate_text_source_value(
                value=record.get(field_name),
                declaration_path=f"{referrer_path}.{field_name}",
                diagnostics=diagnostics,
            )
        for field_name in RECOVERY_POLICY_TEXT_SEQUENCE_FIELDS:
            _validate_text_sequence_source_value(
                value=record.get(field_name),
                declaration_path=f"{referrer_path}.{field_name}",
                diagnostics=diagnostics,
            )
        for field_name in RECOVERY_POLICY_INT_FIELDS:
            _validate_int_source_value(
                value=record.get(field_name),
                declaration_path=f"{referrer_path}.{field_name}",
                diagnostics=diagnostics,
            )


def _validate_text_source_value(
    *,
    value: object,
    declaration_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if isinstance(value, str):
        return
    diagnostics.append(
        unsupported_authority_value_diagnostic(
            declaration_path=declaration_path,
            unsupported_type=type(value).__name__,
            value_kind="value",
        )
    )


def _validate_optional_text_source_value(
    *,
    value: object,
    declaration_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if value is None or isinstance(value, str):
        return
    diagnostics.append(
        unsupported_authority_value_diagnostic(
            declaration_path=declaration_path,
            unsupported_type=type(value).__name__,
            value_kind="value",
        )
    )


def _validate_bool_source_value(
    *,
    value: object,
    declaration_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if isinstance(value, bool):
        return
    diagnostics.append(
        unsupported_authority_value_diagnostic(
            declaration_path=declaration_path,
            unsupported_type=type(value).__name__,
            value_kind="value",
        )
    )


def _validate_int_source_value(
    *,
    value: object,
    declaration_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if type(value) is int:
        return
    diagnostics.append(
        unsupported_authority_value_diagnostic(
            declaration_path=declaration_path,
            unsupported_type=type(value).__name__,
            value_kind="value",
        )
    )


def _validate_text_sequence_source_value(
    *,
    value: object,
    declaration_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if not is_sequence(value):
        diagnostics.append(
            unsupported_authority_value_diagnostic(
                declaration_path=declaration_path,
                unsupported_type=type(value).__name__,
                value_kind="value",
            )
        )
        return

    for index, item in enumerate(value):
        if isinstance(item, str) and item:
            continue
        if isinstance(item, str):
            diagnostics.append(
                unsupported_authority_value_diagnostic(
                    declaration_path=f"{declaration_path}[{index}]",
                    unsupported_type=type(item).__name__,
                    value_kind="empty_string",
                )
            )
            continue
        diagnostics.append(
            unsupported_authority_value_diagnostic(
                declaration_path=f"{declaration_path}[{index}]",
                unsupported_type=type(item).__name__,
                value_kind="value",
            )
        )


def _validate_canonical_source_value(
    *,
    value: object,
    declaration_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                diagnostics.append(
                    unsupported_authority_value_diagnostic(
                        declaration_path=f"{declaration_path}.<{type(key).__name__}>",
                        unsupported_type=type(key).__name__,
                        value_kind="map_key",
                    )
                )
                continue
            key_nfc = normalize("NFC", key)
            if key != key_nfc:
                diagnostics.append(
                    non_nfc_authority_map_key_diagnostic(
                        declaration_path=f"{declaration_path}.<non_nfc_key>",
                        map_key=key,
                        map_key_nfc=key_nfc,
                    )
                )
                continue
            _validate_canonical_source_value(
                value=nested_value,
                declaration_path=f"{declaration_path}.{key}",
                diagnostics=diagnostics,
            )
        return
    if isinstance(value, (list, tuple)):
        for index, nested_value in enumerate(value):
            _validate_canonical_source_value(
                value=nested_value,
                declaration_path=f"{declaration_path}[{index}]",
                diagnostics=diagnostics,
            )
        return

    diagnostics.append(
        unsupported_authority_value_diagnostic(
            declaration_path=declaration_path,
            unsupported_type=type(value).__name__,
            value_kind="value",
        )
    )

__all__ = (
    "UNSELECTED_CATALOG_COLLECTION",
    "unsupported_authority_value_diagnostic",
    "validate_known_source_sections",
    "validate_selected_authority_values",
    "validate_unselected_catalog",
)
