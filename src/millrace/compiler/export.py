"""Compiled-plan export envelope emission and integrity verification."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from unicodedata import normalize

from millrace.contracts.compiled_plan import (
    CanonicalAuthorityError,
    CompletionBehaviorDeclaration,
    RunnerBindingDeclaration,
    RunnerComponentPin,
    RunnerTerminalResultMapping,
    SelectedCompiledPlan,
    SelectedWorkflowPackageAssetPin,
    SelectedWorkflowPackageDependencyPin,
    SelectedWorkflowPackagePin,
    authority_fingerprint,
    canonical_authority_bytes,
    context_binding_authority_refusal,
)
from millrace.contracts.schema import validate_closure_verdict_schema_declaration

COMPILED_PLAN_EXPORT_RECORD_KIND = "compiled_plan_export"
COMPILED_PLAN_EXPORT_SCHEMA_VERSION = 1
CANONICALIZATION_ALGORITHM = "millrace-canonical-json-v1"
EXPORT_HASH_ALGORITHM = "sha256"
EXPORT_AUTHORITY_FINGERPRINT_DOMAIN = "millrace-authority-v1"
COMPILER_ID = "millrace-ai"
COMPILER_PROTOCOL_VERSION = 1
_SHA256_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

_EXPORT_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "compiler_id",
        "compiler_protocol_version",
        "plan_format_version",
        "workflow_id",
        "workflow_version",
        "canonicalization_algorithm",
        "hash_algorithm",
        "authority_fingerprint_domain",
        "authority_fingerprint",
        "selected_authority",
    }
)

_SELECTED_AUTHORITY_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "workflow",
        "compatibility_profile",
        "workflow_package_pin",
        "required_extensions",
        "graphs",
        "partitions",
        "queue_families",
        "external_enqueue_routes",
        "generated_work_routes",
        "fanout_declarations",
        "join_declarations",
        "concurrency_policies",
        "artifact_schemas",
        "assets",
        "stage_kinds",
        "terminal_outcomes",
        "terminal_actions",
        "effect_declarations",
        "recovery_policies",
        "completion_behaviors",
        "remediation_policies",
        "wait_states",
        "counters",
        "lineage_policy",
        "runner_bindings",
        "intervention_options",
        "operator_waits",
        "capabilities",
        "context_bindings",
    }
)


class CompiledPlanExportError(ValueError):
    """Raised when a compiled-plan export envelope cannot be verified."""


@dataclass(frozen=True, slots=True)
class VerifiedCompiledPlanExport:
    """Integrity-checked export envelope metadata and selected authority."""

    authority_fingerprint: str
    workflow_id: str
    workflow_version: str
    plan_format_version: int
    selected_authority: Mapping[str, object]


def compiled_plan_export_record(plan: SelectedCompiledPlan) -> Mapping[str, object]:
    """Return the deterministic JSON-compatible export envelope for a plan."""

    selected_authority = _selected_authority_value(plan)
    return {
        "record_kind": COMPILED_PLAN_EXPORT_RECORD_KIND,
        "schema_version": COMPILED_PLAN_EXPORT_SCHEMA_VERSION,
        "compiler_id": COMPILER_ID,
        "compiler_protocol_version": COMPILER_PROTOCOL_VERSION,
        "plan_format_version": SelectedCompiledPlan.schema_version,
        "workflow_id": str(plan.workflow.workflow_id),
        "workflow_version": str(plan.workflow.workflow_version),
        "canonicalization_algorithm": CANONICALIZATION_ALGORITHM,
        "hash_algorithm": EXPORT_HASH_ALGORITHM,
        "authority_fingerprint_domain": EXPORT_AUTHORITY_FINGERPRINT_DOMAIN,
        "authority_fingerprint": authority_fingerprint(selected_authority),
        "selected_authority": selected_authority,
    }


def compiled_plan_export_bytes(plan: SelectedCompiledPlan) -> bytes:
    """Return canonical UTF-8 JSON bytes for a compiled-plan export envelope."""

    return json.dumps(
        compiled_plan_export_record(plan),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def verify_compiled_plan_export_bytes(
    export_bytes: bytes,
) -> VerifiedCompiledPlanExport:
    """Verify canonical compiled-plan export JSON bytes without admission."""

    try:
        export_text = export_bytes.decode("utf-8")
        parsed = json.loads(
            export_text,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except CompiledPlanExportError:
        raise
    except UnicodeDecodeError as exc:
        raise CompiledPlanExportError("invalid UTF-8 export bytes") from exc
    except ValueError as exc:
        raise CompiledPlanExportError("invalid JSON") from exc

    if not isinstance(parsed, Mapping):
        raise CompiledPlanExportError("export root must be an object")
    return verify_compiled_plan_export_record(parsed)


def verify_compiled_plan_export_record(
    record: Mapping[str, object],
) -> VerifiedCompiledPlanExport:
    """Verify export envelope/nested shape; CAS decode and admission own closure."""

    if not isinstance(record, Mapping):
        raise CompiledPlanExportError("export root must be an object")
    _validate_object_keys(record, label="export")
    _require_exact_keys(record, _EXPORT_KEYS, label="export")

    _require_exact_value(record, "record_kind", COMPILED_PLAN_EXPORT_RECORD_KIND)
    _require_exact_value(record, "schema_version", COMPILED_PLAN_EXPORT_SCHEMA_VERSION)
    _require_exact_value(record, "compiler_id", COMPILER_ID)
    _require_exact_value(
        record,
        "compiler_protocol_version",
        COMPILER_PROTOCOL_VERSION,
    )
    _require_exact_value(
        record,
        "plan_format_version",
        SelectedCompiledPlan.schema_version,
    )
    _require_exact_value(
        record,
        "canonicalization_algorithm",
        CANONICALIZATION_ALGORITHM,
    )
    _require_exact_value(record, "hash_algorithm", EXPORT_HASH_ALGORITHM)
    _require_exact_value(
        record,
        "authority_fingerprint_domain",
        EXPORT_AUTHORITY_FINGERPRINT_DOMAIN,
    )

    selected_authority = _require_mapping(record, "selected_authority")
    selected_authority_keys = _SELECTED_AUTHORITY_KEYS
    if "context_bindings" not in selected_authority:
        selected_authority_keys -= frozenset({"context_bindings"})
    _require_exact_keys(
        selected_authority,
        selected_authority_keys,
        label="selected_authority",
    )
    _require_exact_value(
        selected_authority,
        "record_kind",
        SelectedCompiledPlan.record_kind,
        label="selected_authority",
    )
    _require_exact_value(
        selected_authority,
        "schema_version",
        SelectedCompiledPlan.schema_version,
        label="selected_authority",
    )

    workflow = _require_mapping(
        selected_authority,
        "workflow",
        label="selected_authority",
    )
    workflow_id = _require_string(record, "workflow_id")
    workflow_version = _require_string(record, "workflow_version")
    _require_matching_value(workflow, "workflow_id", workflow_id)
    _require_matching_value(workflow, "workflow_version", workflow_version)
    _validate_workflow_package_pin(
        selected_authority["workflow_package_pin"],
        workflow_id=workflow_id,
        workflow_version=workflow_version,
    )
    _validate_runner_bindings(
        _require_sequence(selected_authority, "runner_bindings")
    )
    _validate_completion_behaviors(
        _require_sequence(selected_authority, "completion_behaviors"),
        artifact_schemas=_require_sequence(selected_authority, "artifact_schemas"),
        stage_kinds=_require_sequence(selected_authority, "stage_kinds"),
        terminal_actions=_require_sequence(
            selected_authority,
            "terminal_actions",
        ),
        runner_bindings=_require_sequence(selected_authority, "runner_bindings"),
    )
    context_refusal = context_binding_authority_refusal(selected_authority)
    if context_refusal is not None:
        raise CompiledPlanExportError(
            f"selected context binding authority is invalid: {context_refusal}"
        )

    expected_fingerprint = _require_string(record, "authority_fingerprint")
    try:
        selected_authority_bytes = canonical_authority_bytes(selected_authority)
        actual_fingerprint = authority_fingerprint(selected_authority)
    except (CanonicalAuthorityError, UnicodeEncodeError) as exc:
        raise CompiledPlanExportError("selected_authority is not canonical") from exc
    if actual_fingerprint != expected_fingerprint:
        raise CompiledPlanExportError("authority fingerprint mismatch")

    verified_selected_authority = json.loads(selected_authority_bytes.decode("utf-8"))
    if not isinstance(verified_selected_authority, Mapping):
        raise CompiledPlanExportError("selected_authority must be an object")

    return VerifiedCompiledPlanExport(
        authority_fingerprint=expected_fingerprint,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        plan_format_version=SelectedCompiledPlan.schema_version,
        selected_authority=verified_selected_authority,
    )


def _selected_authority_value(plan: SelectedCompiledPlan) -> object:
    selected_authority = json.loads(canonical_authority_bytes(plan).decode("utf-8"))
    return selected_authority


def _strict_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    seen_normalized: set[str] = set()
    for key, value in pairs:
        normalized_key = normalize("NFC", key)
        if normalized_key in seen_normalized:
            raise CompiledPlanExportError(
                f"duplicate JSON object key: {normalized_key}"
            )
        if key != normalized_key:
            raise CompiledPlanExportError(f"non-NFC JSON object key: {normalized_key}")
        seen_normalized.add(normalized_key)
        result[key] = value
    return result


def _reject_json_constant(constant: str) -> object:
    raise CompiledPlanExportError(f"invalid JSON constant: {constant}")


def _validate_object_keys(value: object, *, label: str) -> None:
    if value is None or isinstance(value, (str, bool, float)):
        return
    if type(value) is int:
        return
    if isinstance(value, list):
        for item in value:
            _validate_object_keys(item, label=label)
        return
    if isinstance(value, tuple):
        for item in value:
            _validate_object_keys(item, label=label)
        return
    if isinstance(value, Mapping):
        seen_normalized: set[str] = set()
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise CompiledPlanExportError(f"{label} key must be a string")
            normalized_key = normalize("NFC", key)
            if normalized_key in seen_normalized:
                raise CompiledPlanExportError(
                    f"duplicate {label} key: {normalized_key}"
                )
            if key != normalized_key:
                raise CompiledPlanExportError(f"non-NFC {label} key: {normalized_key}")
            seen_normalized.add(normalized_key)
            _validate_object_keys(nested_value, label=label)
        return
    raise CompiledPlanExportError(
        f"unsupported {label} value type: {type(value).__name__}"
    )


def _require_exact_keys(
    record: Mapping[str, object],
    expected_keys: frozenset[str],
    *,
    label: str,
) -> None:
    record_keys = set(record)
    missing_keys = sorted(expected_keys - record_keys)
    extra_keys = sorted(record_keys - expected_keys)
    if missing_keys:
        raise CompiledPlanExportError(f"missing {label} key: {missing_keys[0]}")
    if extra_keys:
        raise CompiledPlanExportError(f"extra {label} key: {extra_keys[0]}")


def _require_exact_value(
    record: Mapping[str, object],
    key: str,
    expected_value: object,
    *,
    label: str = "",
) -> None:
    value = record[key]
    if type(value) is type(expected_value) and value == expected_value:
        return
    prefix = f"{label}." if label else ""
    raise CompiledPlanExportError(f"unsupported {prefix}{key}")


def _require_mapping(
    record: Mapping[str, object],
    key: str,
    *,
    label: str = "",
) -> Mapping[str, object]:
    value = record[key]
    if isinstance(value, Mapping):
        return value
    prefix = f"{label}." if label else ""
    raise CompiledPlanExportError(f"{prefix}{key} must be an object")


def _require_string(record: Mapping[str, object], key: str) -> str:
    value = record[key]
    if isinstance(value, str):
        return value
    raise CompiledPlanExportError(f"{key} must be a string")


def _require_matching_value(
    record: Mapping[str, object],
    key: str,
    expected_value: object,
) -> None:
    if record.get(key) == expected_value:
        return
    raise CompiledPlanExportError(f"{key} mismatch")


def _validate_workflow_package_pin(
    value: object,
    *,
    workflow_id: str,
    workflow_version: str,
) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise CompiledPlanExportError("workflow_package_pin must be an object or null")
    _require_exact_keys(
        value,
        frozenset(
            {
                "record_kind",
                "schema_version",
                "package_id",
                "package_version",
                "package_format_version",
                "workflow_id",
                "workflow_version",
                "entrypoint",
                "selected_asset_pins",
                "selected_dependency_pins",
            }
        ),
        label="workflow_package_pin",
    )
    _require_exact_value(
        value,
        "record_kind",
        SelectedWorkflowPackagePin.record_kind,
        label="workflow_package_pin",
    )
    _require_exact_value(
        value,
        "schema_version",
        SelectedWorkflowPackagePin.schema_version,
        label="workflow_package_pin",
    )
    if value.get("workflow_id") != workflow_id or value.get(
        "workflow_version"
    ) != workflow_version:
        raise CompiledPlanExportError("workflow_package_pin workflow mismatch")
    for field in (
        "package_id",
        "package_version",
        "package_format_version",
        "workflow_id",
        "workflow_version",
        "entrypoint",
    ):
        _require_non_empty_string(value, field, label="workflow_package_pin")
    _validate_asset_pins(_require_sequence(value, "selected_asset_pins"))
    _validate_dependency_pins(_require_sequence(value, "selected_dependency_pins"))


def _validate_asset_pins(values: Sequence[object]) -> None:
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, Mapping):
            raise CompiledPlanExportError("selected_asset_pins must contain objects")
        _require_exact_keys(
            item,
            frozenset(
                {
                    "record_kind",
                    "schema_version",
                    "asset_id",
                    "content_digest",
                }
            ),
            label="selected_asset_pins",
        )
        _require_exact_value(
            item,
            "record_kind",
            SelectedWorkflowPackageAssetPin.record_kind,
            label="selected_asset_pins",
        )
        _require_exact_value(
            item,
            "schema_version",
            SelectedWorkflowPackageAssetPin.schema_version,
            label="selected_asset_pins",
        )
        asset_id = _require_non_empty_string(
            item,
            "asset_id",
            label="selected_asset_pins",
        )
        digest = _require_non_empty_string(
            item,
            "content_digest",
            label="selected_asset_pins",
        )
        if _SHA256_DIGEST_RE.fullmatch(digest) is None:
            raise CompiledPlanExportError(
                "selected asset content_digest must be a sha256 digest"
            )
        if asset_id in seen:
            raise CompiledPlanExportError("duplicate selected asset pin")
        seen.add(asset_id)


def _validate_dependency_pins(values: Sequence[object]) -> None:
    seen: set[tuple[str, str, str]] = set()
    for item in values:
        if not isinstance(item, Mapping):
            raise CompiledPlanExportError(
                "selected_dependency_pins must contain objects"
            )
        _require_exact_keys(
            item,
            frozenset(
                {
                    "record_kind",
                    "schema_version",
                    "package_id",
                    "package_version",
                    "package_format_version",
                }
            ),
            label="selected_dependency_pins",
        )
        _require_exact_value(
            item,
            "record_kind",
            SelectedWorkflowPackageDependencyPin.record_kind,
            label="selected_dependency_pins",
        )
        _require_exact_value(
            item,
            "schema_version",
            SelectedWorkflowPackageDependencyPin.schema_version,
            label="selected_dependency_pins",
        )
        key = (
            _require_non_empty_string(
                item,
                "package_id",
                label="selected_dependency_pins",
            ),
            _require_non_empty_string(
                item,
                "package_version",
                label="selected_dependency_pins",
            ),
            _require_non_empty_string(
                item,
                "package_format_version",
                label="selected_dependency_pins",
            ),
        )
        if key in seen:
            raise CompiledPlanExportError("duplicate selected dependency pin")
        seen.add(key)


def _validate_runner_bindings(values: Sequence[object]) -> None:
    for item in values:
        if not isinstance(item, Mapping):
            raise CompiledPlanExportError("runner_bindings must contain objects")
        _require_exact_keys(
            item,
            frozenset(
                {
                    "record_kind",
                    "schema_version",
                    "id",
                    "adapter_kind",
                    "stage_kind_ids",
                    "invocation_timeout_seconds",
                    "required_capability_ids",
                    "component_pin",
                    "terminal_result_mappings",
                }
            ),
            label="runner binding",
        )
        _require_exact_value(
            item,
            "record_kind",
            RunnerBindingDeclaration.record_kind,
            label="runner binding",
        )
        _require_exact_value(
            item,
            "schema_version",
            RunnerBindingDeclaration.schema_version,
            label="runner binding",
        )
        component_pin = item["component_pin"]
        mappings = _require_sequence(item, "terminal_result_mappings")
        legal_result_ids: frozenset[str] = frozenset()
        if component_pin is None:
            if mappings:
                raise CompiledPlanExportError(
                    "runner terminal result mappings require a component pin"
                )
        elif isinstance(component_pin, Mapping):
            legal_result_ids = _validate_runner_component_pin(component_pin)
        else:
            raise CompiledPlanExportError(
                "runner component pin must be an object or null"
            )
        _validate_runner_terminal_result_mappings(
            mappings,
            legal_result_ids=legal_result_ids,
        )


def _validate_runner_component_pin(
    pin: Mapping[str, object],
) -> frozenset[str]:
    _require_exact_keys(
        pin,
        frozenset(
            {
                "record_kind",
                "schema_version",
                "component_kind",
                "component_id",
                "component_version",
                "provider_distribution",
                "provider_version",
                "descriptor_media_type",
                "descriptor_sha256",
                "required_capability_ids",
                "legal_terminal_result_ids",
                "max_work_item_payload_bytes",
            }
        ),
        label="runner component pin",
    )
    _require_exact_value(
        pin,
        "record_kind",
        RunnerComponentPin.record_kind,
        label="runner component pin",
    )
    _require_exact_value(
        pin,
        "schema_version",
        RunnerComponentPin.schema_version,
        label="runner component pin",
    )
    for field_name in (
        "component_kind",
        "component_id",
        "component_version",
        "provider_distribution",
        "provider_version",
        "descriptor_media_type",
    ):
        value = _require_non_empty_string(
            pin,
            field_name,
            label="runner component pin",
        )
        if not value.strip():
            raise CompiledPlanExportError(
                f"runner component pin.{field_name} must be a nonblank string"
            )
    digest = _require_non_empty_string(
        pin,
        "descriptor_sha256",
        label="runner component pin",
    )
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise CompiledPlanExportError(
            "runner component descriptor_sha256 must be lowercase sha256 hex"
        )
    capacity = pin["max_work_item_payload_bytes"]
    if capacity is not None and (
        type(capacity) is not int or capacity <= 0
    ):
        raise CompiledPlanExportError(
            "runner component max_work_item_payload_bytes must be a positive "
            "integer or null"
        )
    _validate_canonical_string_sequence(
        _require_sequence(pin, "required_capability_ids"),
        label="runner component required_capability_ids",
    )
    return frozenset(
        _validate_canonical_string_sequence(
            _require_sequence(pin, "legal_terminal_result_ids"),
            label="runner component legal_terminal_result_ids",
        )
    )


def _validate_completion_behaviors(
    values: Sequence[object],
    *,
    artifact_schemas: Sequence[object],
    stage_kinds: Sequence[object],
    terminal_actions: Sequence[object],
    runner_bindings: Sequence[object],
) -> None:
    expected_keys = frozenset(
        {
            "record_kind",
            "schema_version",
            "id",
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
            "evidence_artifact_schema_ids",
            "evidence_item_limit",
            "request_payload_byte_limit",
            "remediation_policy_id",
            "accepted_root_source_kinds",
            "root_source_resolution",
            "evidence_window_policy",
            "rubric_policy",
            "blocked_work_policy",
            "skip_if_closed",
        }
    )
    schemas_by_id = {
        str(schema["id"]): schema
        for schema in artifact_schemas
        if isinstance(schema, Mapping) and isinstance(schema.get("id"), str)
    }
    stages_by_id = {
        str(stage["id"]): stage
        for stage in stage_kinds
        if isinstance(stage, Mapping) and isinstance(stage.get("id"), str)
    }
    actions_by_id = {
        str(action["id"]): action
        for action in terminal_actions
        if isinstance(action, Mapping) and isinstance(action.get("id"), str)
    }
    runners_by_id = {
        str(runner["id"]): runner
        for runner in runner_bindings
        if isinstance(runner, Mapping) and isinstance(runner.get("id"), str)
    }
    for item in values:
        if not isinstance(item, Mapping):
            raise CompiledPlanExportError(
                "completion_behaviors must contain objects"
            )
        _require_exact_keys(item, expected_keys, label="completion behavior")
        _require_exact_value(
            item,
            "record_kind",
            CompletionBehaviorDeclaration.record_kind,
            label="completion behavior",
        )
        _require_exact_value(
            item,
            "schema_version",
            CompletionBehaviorDeclaration.schema_version,
            label="completion behavior",
        )
        schemas = _require_sequence(item, "evidence_artifact_schema_ids")
        if not schemas:
            raise CompiledPlanExportError(
                "completion behavior evidence_artifact_schema_ids must be non-empty"
            )
        _validate_canonical_string_sequence(
            schemas,
            label="completion behavior evidence_artifact_schema_ids",
            require_sorted=False,
        )
        evidence_schema_ids = {str(schema) for schema in schemas}
        if not evidence_schema_ids.issubset(schemas_by_id):
            raise CompiledPlanExportError(
                "completion behavior references an unknown evidence artifact schema"
            )
        verdict_schema_id = _require_string(item, "verdict_artifact_schema_id")
        verdict_schema = schemas_by_id.get(verdict_schema_id)
        if verdict_schema is None:
            raise CompiledPlanExportError(
                "completion behavior references an unknown verdict artifact schema"
            )
        target_stage_id = _require_string(item, "target_stage_kind_id")
        target_stage = stages_by_id.get(target_stage_id)
        if target_stage is None or verdict_schema_id not in _string_values(
            target_stage.get("artifact_schema_ids")
        ):
            raise CompiledPlanExportError(
                "completion behavior verdict schema is unavailable to target stage"
            )
        if item["rubric_policy"] == "reuse_or_create" and not (
            _closure_verdict_schema_supported(verdict_schema)
        ):
            raise CompiledPlanExportError(
                "completion behavior verdict artifact schema lacks closure fields"
            )
        action_contracts = (
            ("pass_action_id", {"close", "complete_work_item"}),
            ("gap_action_id", {"closure_gap"}),
            ("blocked_action_id", {"close", "block_work_item"}),
        )
        for action_field, expected_kinds in action_contracts:
            action = actions_by_id.get(_require_string(item, action_field))
            if (
                action is None
                or action.get("stage_kind_id") != target_stage_id
                or action.get("action_kind") not in expected_kinds
            ):
                raise CompiledPlanExportError(
                    "completion behavior terminal action contract must match "
                    "target stage and action kind"
                )
            if action.get("artifact_schema_id") != verdict_schema_id:
                raise CompiledPlanExportError(
                    "completion behavior terminal action schema must equal "
                    "verdict schema"
                )
        evidence_limit = item["evidence_item_limit"]
        if (
            type(evidence_limit) is not int
            or evidence_limit < 1
            or evidence_limit > 256
        ):
            raise CompiledPlanExportError(
                "completion behavior evidence_item_limit is outside 1..256"
            )
        request_limit = item["request_payload_byte_limit"]
        if type(request_limit) is not int or request_limit <= 0:
            raise CompiledPlanExportError(
                "completion behavior request_payload_byte_limit must be positive"
            )
        runner = runners_by_id.get(_require_string(item, "runner_binding_id"))
        if runner is None:
            raise CompiledPlanExportError(
                "completion behavior runner binding is missing"
            )
        component_pin = runner.get("component_pin")
        if not isinstance(component_pin, Mapping):
            raise CompiledPlanExportError(
                "completion behavior runner component pin is missing"
            )
        capacity = component_pin.get("max_work_item_payload_bytes")
        if type(capacity) is not int or capacity <= 0:
            raise CompiledPlanExportError(
                "completion behavior runner payload capacity must be positive"
            )
        if request_limit > capacity:
            raise CompiledPlanExportError(
                "completion behavior request payload limit exceeds runner capacity"
            )


def _closure_verdict_schema_supported(schema: Mapping[str, object]) -> bool:
    raw_schema = schema.get("schema")
    return validate_closure_verdict_schema_declaration(raw_schema).accepted


def _string_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _validate_runner_terminal_result_mappings(
    values: Sequence[object],
    *,
    legal_result_ids: frozenset[str],
) -> None:
    seen: set[tuple[str, str]] = set()
    seen_outcomes: set[tuple[str, str]] = set()
    sort_keys: list[tuple[bytes, bytes, bytes]] = []
    for item in values:
        if not isinstance(item, Mapping):
            raise CompiledPlanExportError(
                "runner terminal result mappings must contain objects"
            )
        _require_exact_keys(
            item,
            frozenset(
                {
                    "record_kind",
                    "schema_version",
                    "stage_kind_id",
                    "runner_result_id",
                    "outcome_id",
                }
            ),
            label="runner terminal result mapping",
        )
        _require_exact_value(
            item,
            "record_kind",
            RunnerTerminalResultMapping.record_kind,
            label="runner terminal result mapping",
        )
        _require_exact_value(
            item,
            "schema_version",
            RunnerTerminalResultMapping.schema_version,
            label="runner terminal result mapping",
        )
        stage_kind_id = _require_non_empty_string(
            item,
            "stage_kind_id",
            label="runner terminal result mapping",
        )
        runner_result_id = _require_non_empty_string(
            item,
            "runner_result_id",
            label="runner terminal result mapping",
        )
        outcome_id = _require_non_empty_string(
            item,
            "outcome_id",
            label="runner terminal result mapping",
        )
        key = (stage_kind_id, runner_result_id)
        if key in seen:
            raise CompiledPlanExportError(
                "duplicate runner terminal result mapping"
            )
        seen.add(key)
        outcome_key = (stage_kind_id, outcome_id)
        if outcome_key in seen_outcomes:
            raise CompiledPlanExportError(
                "duplicate runner terminal outcome mapping"
            )
        seen_outcomes.add(outcome_key)
        if runner_result_id not in legal_result_ids:
            raise CompiledPlanExportError(
                "runner terminal result mapping uses an unknown result"
            )
        sort_keys.append(
            (
                stage_kind_id.encode("utf-8"),
                runner_result_id.encode("utf-8"),
                outcome_id.encode("utf-8"),
            )
        )
    if sort_keys != sorted(sort_keys):
        raise CompiledPlanExportError(
            "runner terminal result mappings are not canonical"
        )


def _validate_canonical_string_sequence(
    values: Sequence[object],
    *,
    label: str,
    require_sorted: bool = True,
) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise CompiledPlanExportError(f"{label} must contain nonblank strings")
    rendered = tuple(str(value) for value in values)
    if len(set(rendered)) != len(rendered):
        raise CompiledPlanExportError(f"{label} must contain unique values")
    if require_sorted and rendered != tuple(
        sorted(rendered, key=lambda value: value.encode("utf-8"))
    ):
        raise CompiledPlanExportError(f"{label} is not canonical")
    return rendered


def _require_sequence(
    record: Mapping[str, object],
    key: str,
) -> Sequence[object]:
    value = record[key]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    raise CompiledPlanExportError(f"{key} must be an array")


def _require_non_empty_string(
    record: Mapping[str, object],
    key: str,
    *,
    label: str,
) -> str:
    value = record[key]
    if isinstance(value, str) and value:
        return value
    raise CompiledPlanExportError(f"{label}.{key} must be a non-empty string")


__all__ = (
    "CANONICALIZATION_ALGORITHM",
    "COMPILED_PLAN_EXPORT_RECORD_KIND",
    "COMPILED_PLAN_EXPORT_SCHEMA_VERSION",
    "CompiledPlanExportError",
    "COMPILER_ID",
    "COMPILER_PROTOCOL_VERSION",
    "EXPORT_AUTHORITY_FINGERPRINT_DOMAIN",
    "EXPORT_HASH_ALGORITHM",
    "VerifiedCompiledPlanExport",
    "compiled_plan_export_bytes",
    "compiled_plan_export_record",
    "verify_compiled_plan_export_bytes",
    "verify_compiled_plan_export_record",
)
