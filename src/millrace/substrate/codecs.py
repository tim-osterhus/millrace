"""Explicit JSON codecs for durable CAS object envelopes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import cast

from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    AssetDeclaration,
    AuthorityValue,
    CapabilityDeclaration,
    CompletionBehaviorDeclaration,
    ConcurrencyPolicyDeclaration,
    CounterDeclaration,
    EffectDeclaration,
    ExternalEnqueueRouteDeclaration,
    FanoutDeclaration,
    GeneratedWorkRouteDeclaration,
    GraphDeclaration,
    InterventionOptionDeclaration,
    JoinDeclaration,
    OperatorWaitDeclaration,
    PartitionDeclaration,
    QueueFamilyDeclaration,
    RecoveryPolicyDeclaration,
    RemediationPolicyDeclaration,
    RunnerBindingDeclaration,
    RunnerComponentPin,
    RunnerTerminalResultMapping,
    SelectedCompiledPlan,
    SelectedWorkflowPackageAssetPin,
    SelectedWorkflowPackageDependencyPin,
    SelectedWorkflowPackagePin,
    StageKindDeclaration,
    TerminalActionDeclaration,
    TerminalOutcomeDeclaration,
    WaitStateDeclaration,
    WorkflowIdentity,
    freeze_authority_mapping,
    runner_component_authority_refusal,
)
from millrace.contracts.ids import (
    ActionId,
    ArtifactSchemaId,
    AssetId,
    CapabilityId,
    CompletionBehaviorId,
    CounterId,
    EffectDeclarationId,
    FanoutId,
    GraphId,
    InterventionOptionId,
    OperatorWaitId,
    OutcomeId,
    PartitionId,
    QueueFamilyId,
    RecoveryPolicyId,
    RemediationPolicyId,
    RunnerBindingId,
    StageKindId,
    WaitStateId,
    WorkflowId,
    WorkflowVersion,
)
from millrace.substrate.errors import (
    CasObjectKindMismatch,
    InvalidCasObject,
    UnsupportedCodec,
    UnsupportedRecordKind,
    UnsupportedSchemaVersion,
)
from millrace.substrate.records import (
    ARTIFACT_PAYLOAD_OBJECT_KIND,
    CAS_OBJECT_KINDS,
    CAS_OBJECT_RECORD_KIND,
    CAS_OBJECT_SCHEMA_VERSION,
    CODEC_ID,
    PAYLOAD_OBJECT_KIND,
    SELECTED_COMPILED_PLAN_OBJECT_KIND,
    CasObjectEnvelope,
    JsonValue,
    freeze_json_mapping,
    freeze_json_value,
)

Record = Mapping[str, JsonValue]
_SHA256_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

_CAS_OBJECT_KEYS = frozenset(
    {"record_kind", "schema_version", "object_kind", "codec", "payload"}
)
_SELECTED_COMPILED_PLAN_KEYS = frozenset(
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
        "lineage_policy",
        "runner_bindings",
        "intervention_options",
        "operator_waits",
        "capabilities",
    }
)
_WORKFLOW_IDENTITY_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "workflow_id",
        "workflow_version",
        "workflow_name",
    }
)
_GRAPH_KEYS = frozenset(
    {"record_kind", "schema_version", "id", "node_ids", "presentation"}
)
_PARTITION_KEYS = frozenset(
    {"record_kind", "schema_version", "id", "partition_kind", "presentation"}
)
_QUEUE_FAMILY_KEYS = frozenset(
    {"record_kind", "schema_version", "id", "external_enqueue", "presentation"}
)
_EXTERNAL_ENQUEUE_ROUTE_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "queue_family_id",
        "graph_node_id",
        "stage_kind_id",
        "runner_binding_id",
        "payload_schema_id",
    }
)
_GENERATED_WORK_ROUTE_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "queue_family_id",
        "graph_node_id",
        "stage_kind_id",
        "runner_binding_id",
        "payload_schema_id",
    }
)
_ARTIFACT_SCHEMA_KEYS = frozenset(
    {"record_kind", "schema_version", "id", "schema", "presentation"}
)
_ASSET_KEYS = frozenset(
    {"record_kind", "schema_version", "id", "asset_kind", "body", "presentation"}
)
_STAGE_KIND_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "partition_id",
        "runner_binding_id",
        "input_queue_family_ids",
        "output_queue_family_ids",
        "artifact_schema_ids",
        "asset_ids",
        "declared_outcome_ids",
        "presentation",
    }
)
_TERMINAL_OUTCOME_KEYS = frozenset(
    {"record_kind", "schema_version", "id", "stage_kind_id", "marker", "presentation"}
)
_TERMINAL_ACTION_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "stage_kind_id",
        "outcome_id",
        "action_kind",
        "target_stage_kind_id",
        "target_graph_node_id",
        "emitted_queue_family_id",
        "artifact_schema_id",
        "runner_binding_id",
        "asset_ids",
        "payload_projection",
        "presentation",
        "dynamic_target_selector",
    }
)
_EFFECT_DECLARATION_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "effect_declaration_id",
        "terminal_action_id",
        "artifact_schema_id",
        "provider_ref",
        "capability_policy_ref",
        "target_ref_kind",
        "target_ref_schema",
        "allowed_reconciliation_statuses",
        "real_side_effects_allowed",
    }
)
_FANOUT_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "source_action_id",
        "source_artifact_schema_id",
        "item_source_path",
        "item_id_key",
        "target_route_id",
        "target_queue_family_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "target_runner_binding_id",
        "target_payload_schema_id",
        "target_payload_mapping",
        "source_state_policy",
        "duplicate_policy",
        "root_lineage_policy",
        "dependency_policy",
    }
)
_JOIN_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "target_stage_kind_id",
        "correlation_key",
        "required_artifact_schema_ids",
        "missing_policy",
    }
)
_CONCURRENCY_POLICY_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "partition_id",
        "max_active_runs",
        "coexist_partition_ids",
    }
)
_RECOVERY_POLICY_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "source_recovery_action_ids",
        "return_action_ids",
        "quarantine_action_ids",
        "recovery_stage_kind_id",
        "recorded_source_selector",
        "attempt_scope",
        "immediate_recovery_limit",
        "cooldown_starts_at_attempt",
        "quarantine_threshold_attempt",
        "threshold_behavior",
        "return_allowed_phases",
        "reset_trigger_action_ids",
        "default_cooldown_seconds",
        "cooldown_wait_state_id",
    }
)
_WAIT_STATE_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "wait_kind",
        "policy_id",
        "starts_at_attempt",
        "duration_seconds",
    }
)
_COUNTER_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "counter_kind",
        "scope",
        "stage_kind_id",
        "increment_action_id",
        "threshold_action_id",
        "threshold_count",
    }
)
_COMPLETION_BEHAVIOR_KEYS = frozenset(
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
        "remediation_policy_id",
        "accepted_root_source_kinds",
        "root_source_resolution",
        "evidence_window_policy",
        "rubric_policy",
        "blocked_work_policy",
        "skip_if_closed",
        "evidence_artifact_schema_ids",
        "evidence_item_limit",
        "request_payload_byte_limit",
        "presentation",
    }
)
_REMEDIATION_POLICY_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
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
        "presentation",
    }
)
_INTERVENTION_OPTION_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "policy_id",
        "kind",
        "legal_source_state",
        "target_selector",
        "resume_target_selector",
        "close_behavior",
        "payload_schema_id",
        "target_queue_family_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "target_runner_binding_id",
        "supersede_behavior",
        "attempt_effect",
        "actor_kind",
        "audit_metadata_requirements",
    }
)
_OPERATOR_WAIT_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "source_action_ids",
        "wait_scope",
        "source_work_item_behavior",
        "project_source_artifact",
        "unrelated_lineages_continue",
        "allowed_resolution_kinds",
        "payload_schema_id",
        "target_queue_family_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "target_runner_binding_id",
        "actor_kind",
        "audit_metadata_requirements",
        "correlation_key",
        "idempotency",
        "timeout_policy",
        "expiry_policy",
        "cancellation_policy",
        "status_effect",
    }
)
_RUNNER_BINDING_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "adapter_kind",
        "stage_kind_ids",
        "invocation_timeout_seconds",
        "presentation",
        "required_capability_ids",
        "component_pin",
        "terminal_result_mappings",
    }
)
_RUNNER_COMPONENT_PIN_KEYS = frozenset(
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
)
_RUNNER_TERMINAL_RESULT_MAPPING_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "stage_kind_id",
        "runner_result_id",
        "outcome_id",
    }
)
_CAPABILITY_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "capability_kind",
        "support_status",
        "grant_status",
        "approval_policy_id",
    }
)
_WORKFLOW_PACKAGE_PIN_KEYS = frozenset(
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
)
_WORKFLOW_PACKAGE_ASSET_PIN_KEYS = frozenset(
    {"record_kind", "schema_version", "asset_id", "content_digest"}
)
_WORKFLOW_PACKAGE_DEPENDENCY_PIN_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "package_id",
        "package_version",
        "package_format_version",
    }
)


def encode_selected_compiled_plan(plan: SelectedCompiledPlan) -> CasObjectEnvelope:
    return CasObjectEnvelope(
        object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
        payload=_encode_selected_compiled_plan(plan),
    )


def decode_selected_compiled_plan(envelope: CasObjectEnvelope) -> SelectedCompiledPlan:
    _ensure_envelope_object_kind(
        envelope,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
    )
    return _decode_selected_compiled_plan(envelope.payload)


def encode_payload(
    payload: Mapping[str, object],
    *,
    object_kind: str = PAYLOAD_OBJECT_KIND,
) -> CasObjectEnvelope:
    if object_kind not in (PAYLOAD_OBJECT_KIND, ARTIFACT_PAYLOAD_OBJECT_KIND):
        raise CasObjectKindMismatch(f"payload codec cannot encode {object_kind}")
    frozen = freeze_authority_mapping(payload)
    return CasObjectEnvelope(
        object_kind=object_kind,
        payload=freeze_json_mapping(cast(Mapping[object, object], frozen)),
    )


def decode_payload(
    envelope: CasObjectEnvelope,
    *,
    expected_object_kind: str = PAYLOAD_OBJECT_KIND,
) -> Mapping[str, AuthorityValue]:
    _ensure_payload_object_kind(expected_object_kind)
    _ensure_envelope_object_kind(
        envelope,
        expected_object_kind=expected_object_kind,
    )
    return envelope.payload


def dumps_cas_object(envelope: CasObjectEnvelope) -> bytes:
    if envelope.codec != CODEC_ID:
        raise UnsupportedCodec(f"unsupported CAS object codec: {envelope.codec}")
    record = {
        "record_kind": CAS_OBJECT_RECORD_KIND,
        "schema_version": CAS_OBJECT_SCHEMA_VERSION,
        "object_kind": envelope.object_kind,
        "codec": envelope.codec,
        "payload": _json_ready_mapping(envelope.payload),
    }
    return json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def loads_cas_object(
    object_bytes: bytes,
    *,
    expected_object_kind: str,
) -> CasObjectEnvelope:
    parsed = _parse_json_object(object_bytes)
    record_kind = _expect_string(parsed, "record_kind")
    if record_kind != CAS_OBJECT_RECORD_KIND:
        raise UnsupportedRecordKind(f"unsupported CAS record kind: {record_kind}")

    schema_version = _expect_int(parsed, "schema_version")
    if schema_version != CAS_OBJECT_SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(
            f"unsupported CAS object schema version: {schema_version}"
        )
    _ensure_exact_keys(parsed, _CAS_OBJECT_KEYS)

    object_kind = _expect_string(parsed, "object_kind")
    if object_kind not in CAS_OBJECT_KINDS or object_kind != expected_object_kind:
        raise CasObjectKindMismatch(
            f"expected CAS object kind {expected_object_kind}, got {object_kind}"
        )

    codec = _expect_string(parsed, "codec")
    if codec != CODEC_ID:
        raise UnsupportedCodec(f"unsupported CAS object codec: {codec}")

    return CasObjectEnvelope(
        object_kind=object_kind,
        codec=codec,
        payload=_expect_record(parsed, "payload"),
    )


def _encode_selected_compiled_plan(
    plan: SelectedCompiledPlan,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": SelectedCompiledPlan.record_kind,
        "schema_version": SelectedCompiledPlan.schema_version,
        "workflow": _encode_workflow_identity(plan.workflow),
        "compatibility_profile": plan.compatibility_profile,
        "workflow_package_pin": _encode_workflow_package_pin(
            plan.workflow_package_pin
        ),
        "required_extensions": tuple(plan.required_extensions),
        "graphs": tuple(_encode_graph(item) for item in plan.graphs),
        "partitions": tuple(_encode_partition(item) for item in plan.partitions),
        "queue_families": tuple(
            _encode_queue_family(item) for item in plan.queue_families
        ),
        "external_enqueue_routes": tuple(
            _encode_external_enqueue_route(item)
            for item in plan.external_enqueue_routes
        ),
        "generated_work_routes": tuple(
            _encode_generated_work_route(item)
            for item in plan.generated_work_routes
        ),
        "artifact_schemas": tuple(
            _encode_artifact_schema(item) for item in plan.artifact_schemas
        ),
        "assets": tuple(_encode_asset(item) for item in plan.assets),
        "stage_kinds": tuple(_encode_stage_kind(item) for item in plan.stage_kinds),
        "terminal_outcomes": tuple(
            _encode_terminal_outcome(item) for item in plan.terminal_outcomes
        ),
        "terminal_actions": tuple(
            _encode_terminal_action(item) for item in plan.terminal_actions
        ),
        "effect_declarations": tuple(
            _encode_effect_declaration(item) for item in plan.effect_declarations
        ),
        "fanout_declarations": tuple(
            _encode_fanout(item) for item in plan.fanout_declarations
        ),
        "join_declarations": tuple(
            _encode_join(item) for item in plan.join_declarations
        ),
        "concurrency_policies": tuple(
            _encode_concurrency_policy(item)
            for item in plan.concurrency_policies
        ),
        "recovery_policies": tuple(
            _encode_recovery_policy(item) for item in plan.recovery_policies
        ),
        "wait_states": tuple(_encode_wait_state(item) for item in plan.wait_states),
        "counters": tuple(_encode_counter(item) for item in plan.counters),
        "completion_behaviors": tuple(
            _encode_completion_behavior(item)
            for item in plan.completion_behaviors
        ),
        "remediation_policies": tuple(
            _encode_remediation_policy(item)
            for item in plan.remediation_policies
        ),
        "lineage_policy": plan.lineage_policy,
        "runner_bindings": tuple(
            _encode_runner_binding(item) for item in plan.runner_bindings
        ),
        "intervention_options": tuple(
            _encode_intervention_option(item) for item in plan.intervention_options
        ),
        "operator_waits": tuple(
            _encode_operator_wait(item) for item in plan.operator_waits
        ),
        "capabilities": tuple(
            _encode_capability(item) for item in plan.capabilities
        ),
    }


def _decode_selected_compiled_plan(record: Record) -> SelectedCompiledPlan:
    _ensure_record_header(
        record,
        SelectedCompiledPlan.record_kind,
        SelectedCompiledPlan.schema_version,
        _SELECTED_COMPILED_PLAN_KEYS,
    )
    if _required_value(record, "compatibility_profile") is not None:
        raise InvalidCasObject(
            "selected compiled plan compatibility_profile must be null"
        )
    selected_plan = SelectedCompiledPlan(
        workflow=_decode_workflow_identity(_expect_record(record, "workflow")),
        compatibility_profile=None,
        workflow_package_pin=_decode_workflow_package_pin(
            _required_value(record, "workflow_package_pin")
        ),
        required_extensions=_expect_string_tuple(record, "required_extensions"),
        graphs=tuple(
            _decode_graph(item) for item in _expect_record_tuple(record, "graphs")
        ),
        partitions=tuple(
            _decode_partition(item)
            for item in _expect_record_tuple(record, "partitions")
        ),
        queue_families=tuple(
            _decode_queue_family(item)
            for item in _expect_record_tuple(record, "queue_families")
        ),
        external_enqueue_routes=tuple(
            _decode_external_enqueue_route(item)
            for item in _expect_record_tuple(record, "external_enqueue_routes")
        ),
        generated_work_routes=tuple(
            _decode_generated_work_route(item)
            for item in _expect_record_tuple(record, "generated_work_routes")
        ),
        artifact_schemas=tuple(
            _decode_artifact_schema(item)
            for item in _expect_record_tuple(record, "artifact_schemas")
        ),
        assets=tuple(
            _decode_asset(item) for item in _expect_record_tuple(record, "assets")
        ),
        stage_kinds=tuple(
            _decode_stage_kind(item)
            for item in _expect_record_tuple(record, "stage_kinds")
        ),
        terminal_outcomes=tuple(
            _decode_terminal_outcome(item)
            for item in _expect_record_tuple(record, "terminal_outcomes")
        ),
        terminal_actions=tuple(
            _decode_terminal_action(item)
            for item in _expect_record_tuple(record, "terminal_actions")
        ),
        effect_declarations=tuple(
            _decode_effect_declaration(item)
            for item in _expect_record_tuple(record, "effect_declarations")
        ),
        fanout_declarations=tuple(
            _decode_fanout(item)
            for item in _expect_record_tuple(record, "fanout_declarations")
        ),
        join_declarations=tuple(
            _decode_join(item)
            for item in _expect_record_tuple(record, "join_declarations")
        ),
        concurrency_policies=tuple(
            _decode_concurrency_policy(item)
            for item in _expect_record_tuple(record, "concurrency_policies")
        ),
        recovery_policies=tuple(
            _decode_recovery_policy(item)
            for item in _expect_record_tuple(record, "recovery_policies")
        ),
        wait_states=tuple(
            _decode_wait_state(item)
            for item in _expect_record_tuple(record, "wait_states")
        ),
        counters=tuple(
            _decode_counter(item) for item in _expect_record_tuple(record, "counters")
        ),
        completion_behaviors=tuple(
            _decode_completion_behavior(item)
            for item in _expect_record_tuple(record, "completion_behaviors")
        ),
        remediation_policies=tuple(
            _decode_remediation_policy(item)
            for item in _expect_record_tuple(record, "remediation_policies")
        ),
        lineage_policy=_expect_string(record, "lineage_policy"),
        runner_bindings=tuple(
            _decode_runner_binding(item)
            for item in _expect_record_tuple(record, "runner_bindings")
        ),
        intervention_options=tuple(
            _decode_intervention_option(item)
            for item in _expect_record_tuple(record, "intervention_options")
        ),
        operator_waits=tuple(
            _decode_operator_wait(item)
            for item in _expect_record_tuple(record, "operator_waits")
        ),
        capabilities=tuple(
            _decode_capability(item)
            for item in _expect_record_tuple(record, "capabilities")
        ),
    )
    component_refusal = runner_component_authority_refusal(selected_plan)
    if component_refusal is not None:
        raise InvalidCasObject(
            f"selected runner component authority is invalid: {component_refusal}"
        )
    return selected_plan


def _encode_workflow_identity(workflow: WorkflowIdentity) -> Mapping[str, JsonValue]:
    return {
        "record_kind": WorkflowIdentity.record_kind,
        "schema_version": WorkflowIdentity.schema_version,
        "workflow_id": str(workflow.workflow_id),
        "workflow_version": str(workflow.workflow_version),
        "workflow_name": workflow.workflow_name,
    }


def _decode_workflow_identity(record: Record) -> WorkflowIdentity:
    _ensure_record_header(
        record,
        WorkflowIdentity.record_kind,
        WorkflowIdentity.schema_version,
        _WORKFLOW_IDENTITY_KEYS,
    )
    return WorkflowIdentity(
        workflow_id=WorkflowId(_expect_string(record, "workflow_id")),
        workflow_version=WorkflowVersion(_expect_string(record, "workflow_version")),
        workflow_name=_expect_string(record, "workflow_name"),
    )


def _encode_workflow_package_pin(
    pin: SelectedWorkflowPackagePin | None,
) -> Mapping[str, JsonValue] | None:
    if pin is None:
        return None
    return {
        "record_kind": SelectedWorkflowPackagePin.record_kind,
        "schema_version": SelectedWorkflowPackagePin.schema_version,
        "package_id": pin.package_id,
        "package_version": pin.package_version,
        "package_format_version": pin.package_format_version,
        "workflow_id": pin.workflow_id,
        "workflow_version": pin.workflow_version,
        "entrypoint": pin.entrypoint,
        "selected_asset_pins": tuple(
            _encode_workflow_package_asset_pin(item)
            for item in pin.selected_asset_pins
        ),
        "selected_dependency_pins": tuple(
            _encode_workflow_package_dependency_pin(item)
            for item in pin.selected_dependency_pins
        ),
    }


def _decode_workflow_package_pin(value: JsonValue) -> SelectedWorkflowPackagePin | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise InvalidCasObject(
            "CAS object field must be an object or null: workflow_package_pin"
        )
    record = value
    _ensure_record_header(
        record,
        SelectedWorkflowPackagePin.record_kind,
        SelectedWorkflowPackagePin.schema_version,
        _WORKFLOW_PACKAGE_PIN_KEYS,
    )
    return SelectedWorkflowPackagePin(
        package_id=_expect_non_empty_string(record, "package_id"),
        package_version=_expect_non_empty_string(record, "package_version"),
        package_format_version=_expect_non_empty_string(
            record,
            "package_format_version",
        ),
        workflow_id=_expect_non_empty_string(record, "workflow_id"),
        workflow_version=_expect_non_empty_string(record, "workflow_version"),
        entrypoint=_expect_non_empty_string(record, "entrypoint"),
        selected_asset_pins=tuple(
            _decode_workflow_package_asset_pin(item)
            for item in _expect_record_tuple(record, "selected_asset_pins")
        ),
        selected_dependency_pins=tuple(
            _decode_workflow_package_dependency_pin(item)
            for item in _expect_record_tuple(record, "selected_dependency_pins")
        ),
    )


def _encode_workflow_package_asset_pin(
    pin: SelectedWorkflowPackageAssetPin,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": SelectedWorkflowPackageAssetPin.record_kind,
        "schema_version": SelectedWorkflowPackageAssetPin.schema_version,
        "asset_id": pin.asset_id,
        "content_digest": pin.content_digest,
    }


def _decode_workflow_package_asset_pin(
    record: Record,
) -> SelectedWorkflowPackageAssetPin:
    _ensure_record_header(
        record,
        SelectedWorkflowPackageAssetPin.record_kind,
        SelectedWorkflowPackageAssetPin.schema_version,
        _WORKFLOW_PACKAGE_ASSET_PIN_KEYS,
    )
    return SelectedWorkflowPackageAssetPin(
        asset_id=_expect_non_empty_string(record, "asset_id"),
        content_digest=_expect_sha256_digest(record, "content_digest"),
    )


def _encode_workflow_package_dependency_pin(
    pin: SelectedWorkflowPackageDependencyPin,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": SelectedWorkflowPackageDependencyPin.record_kind,
        "schema_version": SelectedWorkflowPackageDependencyPin.schema_version,
        "package_id": pin.package_id,
        "package_version": pin.package_version,
        "package_format_version": pin.package_format_version,
    }


def _decode_workflow_package_dependency_pin(
    record: Record,
) -> SelectedWorkflowPackageDependencyPin:
    _ensure_record_header(
        record,
        SelectedWorkflowPackageDependencyPin.record_kind,
        SelectedWorkflowPackageDependencyPin.schema_version,
        _WORKFLOW_PACKAGE_DEPENDENCY_PIN_KEYS,
    )
    return SelectedWorkflowPackageDependencyPin(
        package_id=_expect_non_empty_string(record, "package_id"),
        package_version=_expect_non_empty_string(record, "package_version"),
        package_format_version=_expect_non_empty_string(
            record,
            "package_format_version",
        ),
    )


def _encode_graph(graph: GraphDeclaration) -> Mapping[str, JsonValue]:
    return {
        "record_kind": GraphDeclaration.record_kind,
        "schema_version": GraphDeclaration.schema_version,
        "id": str(graph.id),
        "node_ids": tuple(graph.node_ids),
        "presentation": _encode_authority_mapping(graph.presentation),
    }


def _decode_graph(record: Record) -> GraphDeclaration:
    _ensure_record_header(
        record,
        GraphDeclaration.record_kind,
        GraphDeclaration.schema_version,
        _GRAPH_KEYS,
    )
    return GraphDeclaration(
        id=GraphId(_expect_string(record, "id")),
        node_ids=_expect_string_tuple(record, "node_ids"),
        presentation=_expect_authority_mapping(record, "presentation"),
    )


def _encode_partition(partition: PartitionDeclaration) -> Mapping[str, JsonValue]:
    return {
        "record_kind": PartitionDeclaration.record_kind,
        "schema_version": PartitionDeclaration.schema_version,
        "id": str(partition.id),
        "partition_kind": partition.partition_kind,
        "presentation": _encode_authority_mapping(partition.presentation),
    }


def _decode_partition(record: Record) -> PartitionDeclaration:
    _ensure_record_header(
        record,
        PartitionDeclaration.record_kind,
        PartitionDeclaration.schema_version,
        _PARTITION_KEYS,
    )
    return PartitionDeclaration(
        id=PartitionId(_expect_string(record, "id")),
        partition_kind=_expect_string(record, "partition_kind"),
        presentation=_expect_authority_mapping(record, "presentation"),
    )


def _encode_queue_family(
    queue_family: QueueFamilyDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": QueueFamilyDeclaration.record_kind,
        "schema_version": QueueFamilyDeclaration.schema_version,
        "id": str(queue_family.id),
        "external_enqueue": queue_family.external_enqueue,
        "presentation": _encode_authority_mapping(queue_family.presentation),
    }


def _decode_queue_family(record: Record) -> QueueFamilyDeclaration:
    _ensure_record_header(
        record,
        QueueFamilyDeclaration.record_kind,
        QueueFamilyDeclaration.schema_version,
        _QUEUE_FAMILY_KEYS,
    )
    return QueueFamilyDeclaration(
        id=QueueFamilyId(_expect_string(record, "id")),
        external_enqueue=_expect_bool(record, "external_enqueue"),
        presentation=_expect_authority_mapping(record, "presentation"),
    )


def _encode_external_enqueue_route(
    route: ExternalEnqueueRouteDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": ExternalEnqueueRouteDeclaration.record_kind,
        "schema_version": ExternalEnqueueRouteDeclaration.schema_version,
        "id": route.id,
        "queue_family_id": str(route.queue_family_id),
        "graph_node_id": route.graph_node_id,
        "stage_kind_id": str(route.stage_kind_id),
        "runner_binding_id": str(route.runner_binding_id),
        "payload_schema_id": _optional_id(route.payload_schema_id),
    }


def _decode_external_enqueue_route(record: Record) -> ExternalEnqueueRouteDeclaration:
    _ensure_record_header(
        record,
        ExternalEnqueueRouteDeclaration.record_kind,
        ExternalEnqueueRouteDeclaration.schema_version,
        _EXTERNAL_ENQUEUE_ROUTE_KEYS,
    )
    return ExternalEnqueueRouteDeclaration(
        id=_expect_string(record, "id"),
        queue_family_id=QueueFamilyId(_expect_string(record, "queue_family_id")),
        graph_node_id=_expect_string(record, "graph_node_id"),
        stage_kind_id=StageKindId(_expect_string(record, "stage_kind_id")),
        runner_binding_id=RunnerBindingId(_expect_string(record, "runner_binding_id")),
        payload_schema_id=_optional_artifact_schema_id(record, "payload_schema_id"),
    )


def _encode_generated_work_route(
    route: GeneratedWorkRouteDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": GeneratedWorkRouteDeclaration.record_kind,
        "schema_version": GeneratedWorkRouteDeclaration.schema_version,
        "id": route.id,
        "queue_family_id": str(route.queue_family_id),
        "graph_node_id": route.graph_node_id,
        "stage_kind_id": str(route.stage_kind_id),
        "runner_binding_id": str(route.runner_binding_id),
        "payload_schema_id": _optional_id(route.payload_schema_id),
    }


def _decode_generated_work_route(record: Record) -> GeneratedWorkRouteDeclaration:
    _ensure_record_header(
        record,
        GeneratedWorkRouteDeclaration.record_kind,
        GeneratedWorkRouteDeclaration.schema_version,
        _GENERATED_WORK_ROUTE_KEYS,
    )
    return GeneratedWorkRouteDeclaration(
        id=_expect_string(record, "id"),
        queue_family_id=QueueFamilyId(_expect_string(record, "queue_family_id")),
        graph_node_id=_expect_string(record, "graph_node_id"),
        stage_kind_id=StageKindId(_expect_string(record, "stage_kind_id")),
        runner_binding_id=RunnerBindingId(_expect_string(record, "runner_binding_id")),
        payload_schema_id=_optional_artifact_schema_id(record, "payload_schema_id"),
    )


def _encode_artifact_schema(
    artifact_schema: ArtifactSchemaDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": ArtifactSchemaDeclaration.record_kind,
        "schema_version": ArtifactSchemaDeclaration.schema_version,
        "id": str(artifact_schema.id),
        "schema": _encode_authority_mapping(artifact_schema.schema),
        "presentation": _encode_authority_mapping(artifact_schema.presentation),
    }


def _decode_artifact_schema(record: Record) -> ArtifactSchemaDeclaration:
    _ensure_record_header(
        record,
        ArtifactSchemaDeclaration.record_kind,
        ArtifactSchemaDeclaration.schema_version,
        _ARTIFACT_SCHEMA_KEYS,
    )
    return ArtifactSchemaDeclaration(
        id=ArtifactSchemaId(_expect_string(record, "id")),
        schema=_expect_authority_mapping(record, "schema"),
        presentation=_expect_authority_mapping(record, "presentation"),
    )


def _encode_asset(asset: AssetDeclaration) -> Mapping[str, JsonValue]:
    return {
        "record_kind": AssetDeclaration.record_kind,
        "schema_version": AssetDeclaration.schema_version,
        "id": str(asset.id),
        "asset_kind": asset.asset_kind,
        "body": asset.body,
        "presentation": _encode_authority_mapping(asset.presentation),
    }


def _decode_asset(record: Record) -> AssetDeclaration:
    _ensure_record_header(
        record,
        AssetDeclaration.record_kind,
        AssetDeclaration.schema_version,
        _ASSET_KEYS,
    )
    return AssetDeclaration(
        id=AssetId(_expect_string(record, "id")),
        asset_kind=_expect_string(record, "asset_kind"),
        body=_expect_string(record, "body"),
        presentation=_expect_authority_mapping(record, "presentation"),
    )


def _encode_stage_kind(stage_kind: StageKindDeclaration) -> Mapping[str, JsonValue]:
    return {
        "record_kind": StageKindDeclaration.record_kind,
        "schema_version": StageKindDeclaration.schema_version,
        "id": str(stage_kind.id),
        "partition_id": _optional_id(stage_kind.partition_id),
        "runner_binding_id": str(stage_kind.runner_binding_id),
        "input_queue_family_ids": tuple(
            str(item) for item in stage_kind.input_queue_family_ids
        ),
        "output_queue_family_ids": tuple(
            str(item) for item in stage_kind.output_queue_family_ids
        ),
        "artifact_schema_ids": tuple(
            str(item) for item in stage_kind.artifact_schema_ids
        ),
        "asset_ids": tuple(str(item) for item in stage_kind.asset_ids),
        "declared_outcome_ids": tuple(
            str(item) for item in stage_kind.declared_outcome_ids
        ),
        "presentation": _encode_authority_mapping(stage_kind.presentation),
    }


def _decode_stage_kind(record: Record) -> StageKindDeclaration:
    _ensure_record_header(
        record,
        StageKindDeclaration.record_kind,
        StageKindDeclaration.schema_version,
        _STAGE_KIND_KEYS,
    )
    partition_id = _expect_partition_id(record, "partition_id")
    return StageKindDeclaration(
        id=StageKindId(_expect_string(record, "id")),
        partition_id=partition_id,
        runner_binding_id=RunnerBindingId(_expect_string(record, "runner_binding_id")),
        input_queue_family_ids=tuple(
            QueueFamilyId(item)
            for item in _expect_string_tuple(record, "input_queue_family_ids")
        ),
        output_queue_family_ids=tuple(
            QueueFamilyId(item)
            for item in _expect_string_tuple(record, "output_queue_family_ids")
        ),
        artifact_schema_ids=tuple(
            ArtifactSchemaId(item)
            for item in _expect_string_tuple(record, "artifact_schema_ids")
        ),
        asset_ids=tuple(
            AssetId(item) for item in _expect_string_tuple(record, "asset_ids")
        ),
        declared_outcome_ids=tuple(
            OutcomeId(item)
            for item in _expect_string_tuple(record, "declared_outcome_ids")
        ),
        presentation=_expect_authority_mapping(record, "presentation"),
    )


def _encode_terminal_outcome(
    outcome: TerminalOutcomeDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": TerminalOutcomeDeclaration.record_kind,
        "schema_version": TerminalOutcomeDeclaration.schema_version,
        "id": str(outcome.id),
        "stage_kind_id": str(outcome.stage_kind_id),
        "marker": outcome.marker,
        "presentation": _encode_authority_mapping(outcome.presentation),
    }


def _decode_terminal_outcome(record: Record) -> TerminalOutcomeDeclaration:
    _ensure_record_header(
        record,
        TerminalOutcomeDeclaration.record_kind,
        TerminalOutcomeDeclaration.schema_version,
        _TERMINAL_OUTCOME_KEYS,
    )
    return TerminalOutcomeDeclaration(
        id=OutcomeId(_expect_string(record, "id")),
        stage_kind_id=StageKindId(_expect_string(record, "stage_kind_id")),
        marker=_expect_string(record, "marker"),
        presentation=_expect_authority_mapping(record, "presentation"),
    )


def _encode_terminal_action(
    action: TerminalActionDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": TerminalActionDeclaration.record_kind,
        "schema_version": TerminalActionDeclaration.schema_version,
        "id": str(action.id),
        "stage_kind_id": str(action.stage_kind_id),
        "outcome_id": str(action.outcome_id),
        "action_kind": action.action_kind,
        "target_stage_kind_id": _optional_id(action.target_stage_kind_id),
        "target_graph_node_id": action.target_graph_node_id,
        "emitted_queue_family_id": _optional_id(action.emitted_queue_family_id),
        "artifact_schema_id": _optional_id(action.artifact_schema_id),
        "runner_binding_id": _optional_id(action.runner_binding_id),
        "asset_ids": tuple(str(item) for item in action.asset_ids),
        "payload_projection": _encode_authority_value(action.payload_projection),
        "presentation": _encode_authority_mapping(action.presentation),
        "dynamic_target_selector": _encode_authority_value(
            action.dynamic_target_selector
        ),
    }


def _decode_terminal_action(record: Record) -> TerminalActionDeclaration:
    _ensure_record_header(
        record,
        TerminalActionDeclaration.record_kind,
        TerminalActionDeclaration.schema_version,
        _TERMINAL_ACTION_KEYS,
    )
    return TerminalActionDeclaration(
        id=ActionId(_expect_string(record, "id")),
        stage_kind_id=StageKindId(_expect_string(record, "stage_kind_id")),
        outcome_id=OutcomeId(_expect_string(record, "outcome_id")),
        action_kind=_expect_string(record, "action_kind"),
        target_stage_kind_id=_optional_stage_kind_id(record, "target_stage_kind_id"),
        target_graph_node_id=_expect_optional_string(record, "target_graph_node_id"),
        emitted_queue_family_id=_optional_queue_family_id(
            record,
            "emitted_queue_family_id",
        ),
        artifact_schema_id=_optional_artifact_schema_id(record, "artifact_schema_id"),
        runner_binding_id=_optional_runner_binding_id(record, "runner_binding_id"),
        asset_ids=tuple(
            AssetId(item) for item in _expect_string_tuple(record, "asset_ids")
        ),
        payload_projection=_expect_authority_value(record, "payload_projection"),
        presentation=_expect_authority_mapping(record, "presentation"),
        dynamic_target_selector=_expect_authority_value(
            record,
            "dynamic_target_selector",
        ),
    )


def _encode_effect_declaration(
    declaration: EffectDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": EffectDeclaration.record_kind,
        "schema_version": EffectDeclaration.schema_version,
        "effect_declaration_id": str(declaration.effect_declaration_id),
        "terminal_action_id": str(declaration.terminal_action_id),
        "artifact_schema_id": str(declaration.artifact_schema_id),
        "provider_ref": declaration.provider_ref,
        "capability_policy_ref": declaration.capability_policy_ref,
        "target_ref_kind": declaration.target_ref_kind,
        "target_ref_schema": declaration.target_ref_schema,
        "allowed_reconciliation_statuses": tuple(
            declaration.allowed_reconciliation_statuses
        ),
        "real_side_effects_allowed": declaration.real_side_effects_allowed,
    }


def _decode_effect_declaration(record: Record) -> EffectDeclaration:
    _ensure_record_header(
        record,
        EffectDeclaration.record_kind,
        EffectDeclaration.schema_version,
        _EFFECT_DECLARATION_KEYS,
    )
    return EffectDeclaration(
        effect_declaration_id=EffectDeclarationId(
            _expect_string(record, "effect_declaration_id")
        ),
        terminal_action_id=ActionId(_expect_string(record, "terminal_action_id")),
        artifact_schema_id=ArtifactSchemaId(
            _expect_string(record, "artifact_schema_id")
        ),
        provider_ref=_expect_string(record, "provider_ref"),
        capability_policy_ref=_expect_string(record, "capability_policy_ref"),
        target_ref_kind=_expect_string(record, "target_ref_kind"),
        target_ref_schema=_expect_string(record, "target_ref_schema"),
        allowed_reconciliation_statuses=_expect_string_tuple(
            record,
            "allowed_reconciliation_statuses",
        ),
        real_side_effects_allowed=_expect_bool(record, "real_side_effects_allowed"),
    )


def _encode_fanout(fanout: FanoutDeclaration) -> Mapping[str, JsonValue]:
    return {
        "record_kind": FanoutDeclaration.record_kind,
        "schema_version": FanoutDeclaration.schema_version,
        "id": str(fanout.id),
        "source_action_id": str(fanout.source_action_id),
        "source_artifact_schema_id": str(fanout.source_artifact_schema_id),
        "item_source_path": tuple(fanout.item_source_path),
        "item_id_key": fanout.item_id_key,
        "target_route_id": fanout.target_route_id,
        "source_state_policy": fanout.source_state_policy,
        "target_queue_family_id": str(fanout.target_queue_family_id),
        "target_stage_kind_id": str(fanout.target_stage_kind_id),
        "target_graph_node_id": fanout.target_graph_node_id,
        "target_runner_binding_id": str(fanout.target_runner_binding_id),
        "target_payload_schema_id": str(fanout.target_payload_schema_id),
        "target_payload_mapping": _encode_authority_mapping(
            fanout.target_payload_mapping
        ),
        "duplicate_policy": fanout.duplicate_policy,
        "root_lineage_policy": fanout.root_lineage_policy,
        "dependency_policy": fanout.dependency_policy,
    }


def _decode_fanout(record: Record) -> FanoutDeclaration:
    _ensure_record_header(
        record,
        FanoutDeclaration.record_kind,
        FanoutDeclaration.schema_version,
        _FANOUT_KEYS,
    )
    return FanoutDeclaration(
        id=FanoutId(_expect_string(record, "id")),
        source_action_id=ActionId(_expect_string(record, "source_action_id")),
        source_artifact_schema_id=ArtifactSchemaId(
            _expect_string(record, "source_artifact_schema_id")
        ),
        item_source_path=_expect_string_tuple(record, "item_source_path"),
        item_id_key=_expect_string(record, "item_id_key"),
        target_route_id=_expect_string(record, "target_route_id"),
        source_state_policy=_expect_string(record, "source_state_policy"),
        target_queue_family_id=QueueFamilyId(
            _expect_string(record, "target_queue_family_id")
        ),
        target_stage_kind_id=StageKindId(
            _expect_string(record, "target_stage_kind_id")
        ),
        target_graph_node_id=_expect_string(record, "target_graph_node_id"),
        target_runner_binding_id=RunnerBindingId(
            _expect_string(record, "target_runner_binding_id")
        ),
        target_payload_schema_id=ArtifactSchemaId(
            _expect_string(record, "target_payload_schema_id")
        ),
        target_payload_mapping=_expect_authority_mapping(
            record,
            "target_payload_mapping",
        ),
        duplicate_policy=_expect_string(record, "duplicate_policy"),
        root_lineage_policy=_expect_string(record, "root_lineage_policy"),
        dependency_policy=_expect_string(record, "dependency_policy"),
    )


def _encode_join(join: JoinDeclaration) -> Mapping[str, JsonValue]:
    return {
        "record_kind": JoinDeclaration.record_kind,
        "schema_version": JoinDeclaration.schema_version,
        "id": join.id,
        "target_stage_kind_id": str(join.target_stage_kind_id),
        "correlation_key": join.correlation_key,
        "required_artifact_schema_ids": tuple(
            str(schema_id) for schema_id in join.required_artifact_schema_ids
        ),
        "missing_policy": join.missing_policy,
    }


def _decode_join(record: Record) -> JoinDeclaration:
    _ensure_record_header(
        record,
        JoinDeclaration.record_kind,
        JoinDeclaration.schema_version,
        _JOIN_KEYS,
    )
    return JoinDeclaration(
        id=_expect_string(record, "id"),
        target_stage_kind_id=StageKindId(
            _expect_string(record, "target_stage_kind_id")
        ),
        correlation_key=_expect_string(record, "correlation_key"),
        required_artifact_schema_ids=tuple(
            ArtifactSchemaId(item)
            for item in _expect_string_tuple(record, "required_artifact_schema_ids")
        ),
        missing_policy=_expect_string(record, "missing_policy"),
    )


def _encode_concurrency_policy(
    policy: ConcurrencyPolicyDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": ConcurrencyPolicyDeclaration.record_kind,
        "schema_version": ConcurrencyPolicyDeclaration.schema_version,
        "id": policy.id,
        "partition_id": str(policy.partition_id),
        "max_active_runs": policy.max_active_runs,
        "coexist_partition_ids": tuple(
            str(partition_id) for partition_id in policy.coexist_partition_ids
        ),
    }


def _decode_concurrency_policy(record: Record) -> ConcurrencyPolicyDeclaration:
    _ensure_record_header(
        record,
        ConcurrencyPolicyDeclaration.record_kind,
        ConcurrencyPolicyDeclaration.schema_version,
        _CONCURRENCY_POLICY_KEYS,
    )
    return ConcurrencyPolicyDeclaration(
        id=_expect_string(record, "id"),
        partition_id=PartitionId(_expect_string(record, "partition_id")),
        max_active_runs=_expect_int(record, "max_active_runs"),
        coexist_partition_ids=tuple(
            PartitionId(item)
            for item in _expect_string_tuple(record, "coexist_partition_ids")
        ),
    )


def _encode_recovery_policy(
    policy: RecoveryPolicyDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": RecoveryPolicyDeclaration.record_kind,
        "schema_version": RecoveryPolicyDeclaration.schema_version,
        "id": str(policy.id),
        "source_recovery_action_ids": tuple(
            str(item) for item in policy.source_recovery_action_ids
        ),
        "return_action_ids": tuple(str(item) for item in policy.return_action_ids),
        "quarantine_action_ids": tuple(
            str(item) for item in policy.quarantine_action_ids
        ),
        "recovery_stage_kind_id": str(policy.recovery_stage_kind_id),
        "recorded_source_selector": policy.recorded_source_selector,
        "attempt_scope": policy.attempt_scope,
        "immediate_recovery_limit": policy.immediate_recovery_limit,
        "cooldown_starts_at_attempt": policy.cooldown_starts_at_attempt,
        "quarantine_threshold_attempt": policy.quarantine_threshold_attempt,
        "threshold_behavior": policy.threshold_behavior,
        "return_allowed_phases": tuple(policy.return_allowed_phases),
        "reset_trigger_action_ids": tuple(
            str(item) for item in policy.reset_trigger_action_ids
        ),
        "default_cooldown_seconds": policy.default_cooldown_seconds,
        "cooldown_wait_state_id": _optional_id(policy.cooldown_wait_state_id),
    }


def _decode_recovery_policy(record: Record) -> RecoveryPolicyDeclaration:
    _ensure_record_header(
        record,
        RecoveryPolicyDeclaration.record_kind,
        RecoveryPolicyDeclaration.schema_version,
        _RECOVERY_POLICY_KEYS,
    )
    return RecoveryPolicyDeclaration(
        id=RecoveryPolicyId(_expect_string(record, "id")),
        source_recovery_action_ids=tuple(
            ActionId(item)
            for item in _expect_string_tuple(record, "source_recovery_action_ids")
        ),
        return_action_ids=tuple(
            ActionId(item) for item in _expect_string_tuple(record, "return_action_ids")
        ),
        quarantine_action_ids=tuple(
            ActionId(item)
            for item in _expect_string_tuple(record, "quarantine_action_ids")
        ),
        recovery_stage_kind_id=StageKindId(
            _expect_string(record, "recovery_stage_kind_id")
        ),
        recorded_source_selector=_expect_string(record, "recorded_source_selector"),
        attempt_scope=_expect_string(record, "attempt_scope"),
        immediate_recovery_limit=_expect_int(record, "immediate_recovery_limit"),
        cooldown_starts_at_attempt=_expect_int(record, "cooldown_starts_at_attempt"),
        quarantine_threshold_attempt=_expect_int(
            record,
            "quarantine_threshold_attempt",
        ),
        threshold_behavior=_expect_string(record, "threshold_behavior"),
        return_allowed_phases=_expect_string_tuple(record, "return_allowed_phases"),
        reset_trigger_action_ids=tuple(
            ActionId(item)
            for item in _expect_string_tuple(record, "reset_trigger_action_ids")
        ),
        default_cooldown_seconds=_expect_int(record, "default_cooldown_seconds"),
        cooldown_wait_state_id=_optional_wait_state_id(
            record,
            "cooldown_wait_state_id",
        ),
    )


def _encode_wait_state(wait: WaitStateDeclaration) -> Mapping[str, JsonValue]:
    return {
        "record_kind": WaitStateDeclaration.record_kind,
        "schema_version": WaitStateDeclaration.schema_version,
        "id": str(wait.id),
        "wait_kind": wait.wait_kind,
        "policy_id": str(wait.policy_id),
        "starts_at_attempt": wait.starts_at_attempt,
        "duration_seconds": wait.duration_seconds,
    }


def _decode_wait_state(record: Record) -> WaitStateDeclaration:
    _ensure_record_header(
        record,
        WaitStateDeclaration.record_kind,
        WaitStateDeclaration.schema_version,
        _WAIT_STATE_KEYS,
    )
    return WaitStateDeclaration(
        id=WaitStateId(_expect_string(record, "id")),
        wait_kind=_expect_string(record, "wait_kind"),
        policy_id=RecoveryPolicyId(_expect_string(record, "policy_id")),
        starts_at_attempt=_expect_int(record, "starts_at_attempt"),
        duration_seconds=_expect_int(record, "duration_seconds"),
    )


def _encode_counter(counter: CounterDeclaration) -> Mapping[str, JsonValue]:
    return {
        "record_kind": CounterDeclaration.record_kind,
        "schema_version": CounterDeclaration.schema_version,
        "id": str(counter.id),
        "counter_kind": counter.counter_kind,
        "scope": counter.scope,
        "stage_kind_id": str(counter.stage_kind_id),
        "increment_action_id": str(counter.increment_action_id),
        "threshold_action_id": str(counter.threshold_action_id),
        "threshold_count": counter.threshold_count,
    }


def _decode_counter(record: Record) -> CounterDeclaration:
    _ensure_record_header(
        record,
        CounterDeclaration.record_kind,
        CounterDeclaration.schema_version,
        _COUNTER_KEYS,
    )
    return CounterDeclaration(
        id=CounterId(_expect_string(record, "id")),
        counter_kind=_expect_string(record, "counter_kind"),
        scope=_expect_string(record, "scope"),
        stage_kind_id=StageKindId(_expect_string(record, "stage_kind_id")),
        increment_action_id=ActionId(_expect_string(record, "increment_action_id")),
        threshold_action_id=ActionId(_expect_string(record, "threshold_action_id")),
        threshold_count=_expect_int(record, "threshold_count"),
    )


def _encode_completion_behavior(
    behavior: CompletionBehaviorDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": CompletionBehaviorDeclaration.record_kind,
        "schema_version": CompletionBehaviorDeclaration.schema_version,
        "id": str(behavior.id),
        "trigger": behavior.trigger,
        "readiness_rule": behavior.readiness_rule,
        "request_kind": behavior.request_kind,
        "target_selector": behavior.target_selector,
        "target_stage_kind_id": str(behavior.target_stage_kind_id),
        "target_graph_node_id": behavior.target_graph_node_id,
        "runner_binding_id": str(behavior.runner_binding_id),
        "request_queue_family_id": str(behavior.request_queue_family_id),
        "pass_action_id": str(behavior.pass_action_id),
        "gap_action_id": str(behavior.gap_action_id),
        "blocked_action_id": str(behavior.blocked_action_id),
        "verdict_artifact_schema_id": str(behavior.verdict_artifact_schema_id),
        "evidence_artifact_schema_ids": tuple(
            str(item) for item in behavior.evidence_artifact_schema_ids
        ),
        "evidence_item_limit": behavior.evidence_item_limit,
        "request_payload_byte_limit": behavior.request_payload_byte_limit,
        "remediation_policy_id": str(behavior.remediation_policy_id),
        "accepted_root_source_kinds": tuple(behavior.accepted_root_source_kinds),
        "root_source_resolution": behavior.root_source_resolution,
        "evidence_window_policy": behavior.evidence_window_policy,
        "rubric_policy": behavior.rubric_policy,
        "blocked_work_policy": behavior.blocked_work_policy,
        "skip_if_closed": behavior.skip_if_closed,
        "presentation": _encode_authority_mapping(behavior.presentation),
    }


def _decode_completion_behavior(record: Record) -> CompletionBehaviorDeclaration:
    _ensure_record_header(
        record,
        CompletionBehaviorDeclaration.record_kind,
        CompletionBehaviorDeclaration.schema_version,
        _COMPLETION_BEHAVIOR_KEYS,
    )
    return CompletionBehaviorDeclaration(
        id=CompletionBehaviorId(_expect_string(record, "id")),
        trigger=_expect_string(record, "trigger"),
        readiness_rule=_expect_string(record, "readiness_rule"),
        request_kind=_expect_string(record, "request_kind"),
        target_selector=_expect_string(record, "target_selector"),
        target_stage_kind_id=StageKindId(
            _expect_string(record, "target_stage_kind_id")
        ),
        target_graph_node_id=_expect_string(record, "target_graph_node_id"),
        runner_binding_id=RunnerBindingId(_expect_string(record, "runner_binding_id")),
        request_queue_family_id=QueueFamilyId(
            _expect_string(record, "request_queue_family_id")
        ),
        pass_action_id=ActionId(_expect_string(record, "pass_action_id")),
        gap_action_id=ActionId(_expect_string(record, "gap_action_id")),
        blocked_action_id=ActionId(_expect_string(record, "blocked_action_id")),
        verdict_artifact_schema_id=ArtifactSchemaId(
            _expect_string(record, "verdict_artifact_schema_id")
        ),
        evidence_artifact_schema_ids=tuple(
            ArtifactSchemaId(item)
            for item in _expect_string_tuple(record, "evidence_artifact_schema_ids")
        ),
        evidence_item_limit=_expect_int(record, "evidence_item_limit"),
        request_payload_byte_limit=_expect_positive_int(
            record,
            "request_payload_byte_limit",
        ),
        remediation_policy_id=RemediationPolicyId(
            _expect_string(record, "remediation_policy_id")
        ),
        accepted_root_source_kinds=_expect_string_tuple(
            record,
            "accepted_root_source_kinds",
        ),
        root_source_resolution=_expect_string(record, "root_source_resolution"),
        evidence_window_policy=_expect_string(record, "evidence_window_policy"),
        rubric_policy=_expect_string(record, "rubric_policy"),
        blocked_work_policy=_expect_string(record, "blocked_work_policy"),
        skip_if_closed=_expect_bool(record, "skip_if_closed"),
        presentation=_expect_authority_mapping(record, "presentation"),
    )


def _encode_remediation_policy(
    policy: RemediationPolicyDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": RemediationPolicyDeclaration.record_kind,
        "schema_version": RemediationPolicyDeclaration.schema_version,
        "id": str(policy.id),
        "source_action_id": str(policy.source_action_id),
        "target_queue_family_id": str(policy.target_queue_family_id),
        "target_stage_kind_id": str(policy.target_stage_kind_id),
        "target_graph_node_id": policy.target_graph_node_id,
        "target_runner_binding_id": str(policy.target_runner_binding_id),
        "payload_schema_id": str(policy.payload_schema_id),
        "guidance_source": policy.guidance_source,
        "dedupe_key": policy.dedupe_key,
        "duplicate_policy": policy.duplicate_policy,
        "suppression_policy": policy.suppression_policy,
        "root_source_kind": policy.root_source_kind,
        "presentation": _encode_authority_mapping(policy.presentation),
    }


def _decode_remediation_policy(record: Record) -> RemediationPolicyDeclaration:
    _ensure_record_header(
        record,
        RemediationPolicyDeclaration.record_kind,
        RemediationPolicyDeclaration.schema_version,
        _REMEDIATION_POLICY_KEYS,
    )
    return RemediationPolicyDeclaration(
        id=RemediationPolicyId(_expect_string(record, "id")),
        source_action_id=ActionId(_expect_string(record, "source_action_id")),
        target_queue_family_id=QueueFamilyId(
            _expect_string(record, "target_queue_family_id")
        ),
        target_stage_kind_id=StageKindId(
            _expect_string(record, "target_stage_kind_id")
        ),
        target_graph_node_id=_expect_string(record, "target_graph_node_id"),
        target_runner_binding_id=RunnerBindingId(
            _expect_string(record, "target_runner_binding_id")
        ),
        payload_schema_id=ArtifactSchemaId(
            _expect_string(record, "payload_schema_id")
        ),
        guidance_source=_expect_string(record, "guidance_source"),
        dedupe_key=_expect_string(record, "dedupe_key"),
        duplicate_policy=_expect_string(record, "duplicate_policy"),
        suppression_policy=_expect_string(record, "suppression_policy"),
        root_source_kind=_expect_string(record, "root_source_kind"),
        presentation=_expect_authority_mapping(record, "presentation"),
    )


def _encode_intervention_option(
    option: InterventionOptionDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": InterventionOptionDeclaration.record_kind,
        "schema_version": InterventionOptionDeclaration.schema_version,
        "id": str(option.id),
        "policy_id": str(option.policy_id),
        "kind": option.option_kind,
        "legal_source_state": option.legal_source_state,
        "target_selector": option.target_selector,
        "resume_target_selector": option.resume_target_selector,
        "close_behavior": option.close_behavior,
        "payload_schema_id": _optional_id(option.payload_schema_id),
        "target_queue_family_id": _optional_id(option.target_queue_family_id),
        "target_stage_kind_id": _optional_id(option.target_stage_kind_id),
        "target_graph_node_id": option.target_graph_node_id,
        "target_runner_binding_id": _optional_id(option.target_runner_binding_id),
        "supersede_behavior": option.supersede_behavior,
        "attempt_effect": option.attempt_effect,
        "actor_kind": option.actor_kind,
        "audit_metadata_requirements": tuple(option.audit_metadata_requirements),
    }


def _decode_intervention_option(record: Record) -> InterventionOptionDeclaration:
    _ensure_record_header(
        record,
        InterventionOptionDeclaration.record_kind,
        InterventionOptionDeclaration.schema_version,
        _INTERVENTION_OPTION_KEYS,
    )
    return InterventionOptionDeclaration(
        id=InterventionOptionId(_expect_string(record, "id")),
        policy_id=RecoveryPolicyId(_expect_string(record, "policy_id")),
        option_kind=_expect_string(record, "kind"),
        legal_source_state=_expect_string(record, "legal_source_state"),
        target_selector=_expect_string(record, "target_selector"),
        resume_target_selector=_expect_optional_string(
            record,
            "resume_target_selector",
        ),
        close_behavior=_expect_optional_string(record, "close_behavior"),
        payload_schema_id=_optional_artifact_schema_id(record, "payload_schema_id"),
        target_queue_family_id=_optional_queue_family_id(
            record,
            "target_queue_family_id",
        ),
        target_stage_kind_id=_optional_stage_kind_id(
            record,
            "target_stage_kind_id",
        ),
        target_graph_node_id=_expect_optional_string(record, "target_graph_node_id"),
        target_runner_binding_id=_optional_runner_binding_id(
            record,
            "target_runner_binding_id",
        ),
        supersede_behavior=_expect_string(record, "supersede_behavior"),
        attempt_effect=_expect_string(record, "attempt_effect"),
        actor_kind=_expect_string(record, "actor_kind"),
        audit_metadata_requirements=_expect_string_tuple(
            record,
            "audit_metadata_requirements",
        ),
    )


def _encode_operator_wait(wait: OperatorWaitDeclaration) -> Mapping[str, JsonValue]:
    return {
        "record_kind": OperatorWaitDeclaration.record_kind,
        "schema_version": OperatorWaitDeclaration.schema_version,
        "id": str(wait.id),
        "source_action_ids": tuple(str(item) for item in wait.source_action_ids),
        "wait_scope": wait.wait_scope,
        "source_work_item_behavior": wait.source_work_item_behavior,
        "project_source_artifact": wait.project_source_artifact,
        "unrelated_lineages_continue": wait.unrelated_lineages_continue,
        "allowed_resolution_kinds": tuple(wait.allowed_resolution_kinds),
        "payload_schema_id": _optional_id(wait.payload_schema_id),
        "target_queue_family_id": _optional_id(wait.target_queue_family_id),
        "target_stage_kind_id": _optional_id(wait.target_stage_kind_id),
        "target_graph_node_id": wait.target_graph_node_id,
        "target_runner_binding_id": _optional_id(wait.target_runner_binding_id),
        "actor_kind": wait.actor_kind,
        "audit_metadata_requirements": tuple(wait.audit_metadata_requirements),
        "correlation_key": wait.correlation_key,
        "idempotency": wait.idempotency,
        "timeout_policy": wait.timeout_policy,
        "expiry_policy": wait.expiry_policy,
        "cancellation_policy": wait.cancellation_policy,
        "status_effect": wait.status_effect,
    }


def _decode_operator_wait(record: Record) -> OperatorWaitDeclaration:
    _ensure_record_header(
        record,
        OperatorWaitDeclaration.record_kind,
        OperatorWaitDeclaration.schema_version,
        _OPERATOR_WAIT_KEYS,
    )
    return OperatorWaitDeclaration(
        id=OperatorWaitId(_expect_string(record, "id")),
        source_action_ids=tuple(
            ActionId(item)
            for item in _expect_string_tuple(record, "source_action_ids")
        ),
        wait_scope=_expect_string(record, "wait_scope"),
        source_work_item_behavior=_expect_string(record, "source_work_item_behavior"),
        project_source_artifact=_expect_bool(record, "project_source_artifact"),
        unrelated_lineages_continue=_expect_bool(
            record,
            "unrelated_lineages_continue",
        ),
        allowed_resolution_kinds=_expect_string_tuple(
            record,
            "allowed_resolution_kinds",
        ),
        payload_schema_id=_optional_artifact_schema_id(record, "payload_schema_id"),
        target_queue_family_id=_optional_queue_family_id(
            record,
            "target_queue_family_id",
        ),
        target_stage_kind_id=_optional_stage_kind_id(
            record,
            "target_stage_kind_id",
        ),
        target_graph_node_id=_expect_optional_string(record, "target_graph_node_id"),
        target_runner_binding_id=_optional_runner_binding_id(
            record,
            "target_runner_binding_id",
        ),
        actor_kind=_expect_string(record, "actor_kind"),
        audit_metadata_requirements=_expect_string_tuple(
            record,
            "audit_metadata_requirements",
        ),
        correlation_key=_expect_string(record, "correlation_key"),
        idempotency=_expect_string(record, "idempotency"),
        timeout_policy=_expect_string(record, "timeout_policy"),
        expiry_policy=_expect_string(record, "expiry_policy"),
        cancellation_policy=_expect_string(record, "cancellation_policy"),
        status_effect=_expect_string(record, "status_effect"),
    )


def _encode_runner_binding(
    runner_binding: RunnerBindingDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": RunnerBindingDeclaration.record_kind,
        "schema_version": RunnerBindingDeclaration.schema_version,
        "id": str(runner_binding.id),
        "adapter_kind": runner_binding.adapter_kind,
        "stage_kind_ids": tuple(str(item) for item in runner_binding.stage_kind_ids),
        "invocation_timeout_seconds": runner_binding.invocation_timeout_seconds,
        "presentation": _encode_authority_mapping(runner_binding.presentation),
        "required_capability_ids": tuple(
            str(item) for item in runner_binding.required_capability_ids
        ),
        "component_pin": _encode_runner_component_pin(runner_binding.component_pin),
        "terminal_result_mappings": tuple(
            _encode_runner_terminal_result_mapping(item)
            for item in runner_binding.terminal_result_mappings
        ),
    }


def _encode_runner_component_pin(
    pin: RunnerComponentPin | None,
) -> Mapping[str, JsonValue] | None:
    if pin is None:
        return None
    return {
        "record_kind": RunnerComponentPin.record_kind,
        "schema_version": RunnerComponentPin.schema_version,
        "component_kind": pin.component_kind,
        "component_id": pin.component_id,
        "component_version": pin.component_version,
        "provider_distribution": pin.provider_distribution,
        "provider_version": pin.provider_version,
        "descriptor_media_type": pin.descriptor_media_type,
        "descriptor_sha256": pin.descriptor_sha256,
        "required_capability_ids": tuple(
            str(item) for item in pin.required_capability_ids
        ),
        "legal_terminal_result_ids": tuple(pin.legal_terminal_result_ids),
        "max_work_item_payload_bytes": pin.max_work_item_payload_bytes,
    }


def _encode_runner_terminal_result_mapping(
    mapping: RunnerTerminalResultMapping,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": RunnerTerminalResultMapping.record_kind,
        "schema_version": RunnerTerminalResultMapping.schema_version,
        "stage_kind_id": str(mapping.stage_kind_id),
        "runner_result_id": mapping.runner_result_id,
        "outcome_id": str(mapping.outcome_id),
    }


def _decode_runner_binding(record: Record) -> RunnerBindingDeclaration:
    _ensure_record_header(
        record,
        RunnerBindingDeclaration.record_kind,
        RunnerBindingDeclaration.schema_version,
        _RUNNER_BINDING_KEYS,
    )
    try:
        return RunnerBindingDeclaration(
            id=RunnerBindingId(_expect_string(record, "id")),
            adapter_kind=_expect_string(record, "adapter_kind"),
            stage_kind_ids=tuple(
                StageKindId(item)
                for item in _expect_string_tuple(record, "stage_kind_ids")
            ),
            invocation_timeout_seconds=_expect_positive_int(
                record,
                "invocation_timeout_seconds",
            ),
            presentation=_expect_authority_mapping(record, "presentation"),
            required_capability_ids=tuple(
                CapabilityId(item)
                for item in _expect_string_tuple(record, "required_capability_ids")
            ),
            component_pin=_decode_runner_component_pin(
                _required_value(record, "component_pin")
            ),
            terminal_result_mappings=tuple(
                _decode_runner_terminal_result_mapping(item)
                for item in _expect_record_tuple(
                    record,
                    "terminal_result_mappings",
                )
            ),
        )
    except (InvalidCasObject, UnsupportedRecordKind, UnsupportedSchemaVersion):
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidCasObject(f"CAS object runner binding is invalid: {exc}") from exc


def _decode_runner_component_pin(value: JsonValue) -> RunnerComponentPin | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise InvalidCasObject("CAS object component_pin must be an object or null")
    record = value
    _ensure_record_header(
        record,
        RunnerComponentPin.record_kind,
        RunnerComponentPin.schema_version,
        _RUNNER_COMPONENT_PIN_KEYS,
    )
    try:
        return RunnerComponentPin(
            component_kind=_expect_string(record, "component_kind"),
            component_id=_expect_string(record, "component_id"),
            component_version=_expect_string(record, "component_version"),
            provider_distribution=_expect_string(record, "provider_distribution"),
            provider_version=_expect_string(record, "provider_version"),
            descriptor_media_type=_expect_string(record, "descriptor_media_type"),
            descriptor_sha256=_expect_string(record, "descriptor_sha256"),
            required_capability_ids=tuple(
                CapabilityId(item)
                for item in _expect_string_tuple(record, "required_capability_ids")
            ),
            legal_terminal_result_ids=_expect_string_tuple(
                record,
                "legal_terminal_result_ids",
            ),
            max_work_item_payload_bytes=_expect_optional_positive_int(
                record,
                "max_work_item_payload_bytes",
            ),
        )
    except InvalidCasObject:
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidCasObject(f"CAS object component pin is invalid: {exc}") from exc


def _decode_runner_terminal_result_mapping(
    record: Record,
) -> RunnerTerminalResultMapping:
    _ensure_record_header(
        record,
        RunnerTerminalResultMapping.record_kind,
        RunnerTerminalResultMapping.schema_version,
        _RUNNER_TERMINAL_RESULT_MAPPING_KEYS,
    )
    try:
        return RunnerTerminalResultMapping(
            stage_kind_id=StageKindId(_expect_string(record, "stage_kind_id")),
            runner_result_id=_expect_string(record, "runner_result_id"),
            outcome_id=OutcomeId(_expect_string(record, "outcome_id")),
        )
    except InvalidCasObject:
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidCasObject(
            f"CAS object terminal result mapping is invalid: {exc}"
        ) from exc


def _encode_capability(
    capability: CapabilityDeclaration,
) -> Mapping[str, JsonValue]:
    return {
        "record_kind": CapabilityDeclaration.record_kind,
        "schema_version": CapabilityDeclaration.schema_version,
        "id": str(capability.id),
        "capability_kind": capability.capability_kind,
        "support_status": capability.support_status,
        "grant_status": capability.grant_status,
        "approval_policy_id": capability.approval_policy_id,
    }


def _decode_capability(record: Record) -> CapabilityDeclaration:
    _ensure_record_header(
        record,
        CapabilityDeclaration.record_kind,
        CapabilityDeclaration.schema_version,
        _CAPABILITY_KEYS,
    )
    approval_policy_id = _expect_optional_string(record, "approval_policy_id")
    return CapabilityDeclaration(
        id=CapabilityId(_expect_string(record, "id")),
        capability_kind=_expect_string(record, "capability_kind"),
        support_status=_expect_string(record, "support_status"),
        grant_status=_expect_string(record, "grant_status"),
        approval_policy_id=approval_policy_id,
    )


def _parse_json_object(object_bytes: bytes) -> Record:
    try:
        object_text = object_bytes.decode("utf-8")
        parsed = json.loads(
            object_text,
            object_pairs_hook=_parse_json_object_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_json_constant,
        )
    except InvalidCasObject:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCasObject("CAS object bytes must be valid JSON") from exc
    if _canonical_json_bytes(parsed) != object_bytes:
        raise InvalidCasObject("CAS object bytes must be canonical JSON")
    frozen = freeze_json_value(parsed)
    if not isinstance(frozen, Mapping):
        raise InvalidCasObject("CAS object bytes must decode to a JSON object")
    return frozen


def _parse_json_object_pairs(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise InvalidCasObject(f"duplicate JSON object key: {key}")
        parsed[key] = value
    return parsed


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _reject_float(value: str) -> object:
    raise InvalidCasObject(f"CAS JSON value must not be a float: {value}")


def _reject_json_constant(value: str) -> object:
    raise InvalidCasObject(f"CAS JSON value must not be non-finite: {value}")


def _json_ready_value(value: JsonValue) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, tuple):
        return [_json_ready_value(item) for item in value]
    return _json_ready_mapping(value)


def _json_ready_mapping(value: Mapping[str, JsonValue]) -> dict[str, object]:
    return {key: _json_ready_value(nested_value) for key, nested_value in value.items()}


def _encode_authority_value(value: AuthorityValue | None) -> JsonValue:
    return freeze_json_value(value)


def _encode_authority_mapping(
    value: Mapping[str, AuthorityValue],
) -> Mapping[str, JsonValue]:
    return freeze_json_mapping(cast(Mapping[object, object], value))


def _ensure_envelope_object_kind(
    envelope: CasObjectEnvelope,
    *,
    expected_object_kind: str,
) -> None:
    if envelope.object_kind != expected_object_kind:
        raise CasObjectKindMismatch(
            "expected CAS object kind "
            f"{expected_object_kind}, got {envelope.object_kind}"
        )


def _ensure_payload_object_kind(expected_object_kind: str) -> None:
    if expected_object_kind not in (PAYLOAD_OBJECT_KIND, ARTIFACT_PAYLOAD_OBJECT_KIND):
        raise CasObjectKindMismatch(
            f"payload codec cannot decode {expected_object_kind}"
        )


def _ensure_record_header(
    record: Record,
    expected_record_kind: str,
    expected_schema_version: int,
    expected_keys: frozenset[str],
) -> None:
    record_kind = _expect_string(record, "record_kind")
    if record_kind != expected_record_kind:
        raise UnsupportedRecordKind(
            f"expected record kind {expected_record_kind}, got {record_kind}"
        )
    schema_version = _expect_int(record, "schema_version")
    if schema_version != expected_schema_version:
        raise UnsupportedSchemaVersion(
            f"expected schema version {expected_schema_version}, got {schema_version}"
        )
    _ensure_exact_keys(record, expected_keys)


def _ensure_exact_keys(record: Record, expected_keys: frozenset[str]) -> None:
    actual_keys = frozenset(record)
    extra_keys = actual_keys - expected_keys
    if extra_keys:
        extra = ", ".join(sorted(extra_keys))
        raise InvalidCasObject(f"unexpected CAS object fields: {extra}")
    missing_keys = expected_keys - actual_keys
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        raise InvalidCasObject(f"missing CAS object fields: {missing}")


def _required_value(record: Record, field_name: str) -> JsonValue:
    try:
        return record[field_name]
    except KeyError as exc:
        raise InvalidCasObject(f"missing CAS object field: {field_name}") from exc


def _expect_string(record: Record, field_name: str) -> str:
    value = _required_value(record, field_name)
    if not isinstance(value, str):
        raise InvalidCasObject(f"CAS object field must be a string: {field_name}")
    return value


def _expect_non_empty_string(record: Record, field_name: str) -> str:
    value = _expect_string(record, field_name)
    if not value:
        raise InvalidCasObject(
            f"CAS object field must be a non-empty string: {field_name}"
        )
    return value


def _expect_sha256_digest(record: Record, field_name: str) -> str:
    value = _expect_non_empty_string(record, field_name)
    if _SHA256_DIGEST_RE.fullmatch(value) is None:
        raise InvalidCasObject(
            f"CAS object field must be a sha256 digest: {field_name}"
        )
    return value


def _expect_optional_string(record: Record, field_name: str) -> str | None:
    value = _required_value(record, field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidCasObject(
            f"CAS object field must be a string or null: {field_name}"
        )
    return value


def _expect_partition_id(record: Record, field_name: str) -> PartitionId | None:
    value = _required_value(record, field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidCasObject(
            f"CAS object field must be a string or null: {field_name}"
        )
    return PartitionId(value)


def _expect_int(record: Record, field_name: str) -> int:
    value = _required_value(record, field_name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidCasObject(f"CAS object field must be an integer: {field_name}")
    return value


def _expect_positive_int(record: Record, field_name: str) -> int:
    value = _expect_int(record, field_name)
    if value <= 0:
        raise InvalidCasObject(
            f"CAS object field must be a positive integer: {field_name}"
        )
    return value


def _expect_optional_positive_int(record: Record, field_name: str) -> int | None:
    value = _required_value(record, field_name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidCasObject(
            f"CAS object field must be a positive integer or null: {field_name}"
        )
    return value


def _expect_bool(record: Record, field_name: str) -> bool:
    value = _required_value(record, field_name)
    if not isinstance(value, bool):
        raise InvalidCasObject(f"CAS object field must be a boolean: {field_name}")
    return value


def _expect_record(record: Record, field_name: str) -> Record:
    value = _required_value(record, field_name)
    if not isinstance(value, Mapping):
        raise InvalidCasObject(f"CAS object field must be an object: {field_name}")
    return value


def _expect_record_tuple(record: Record, field_name: str) -> tuple[Record, ...]:
    value = _required_value(record, field_name)
    if not isinstance(value, tuple):
        raise InvalidCasObject(f"CAS object field must be an array: {field_name}")
    return tuple(_expect_record_item(field_name, item) for item in value)


def _expect_record_item(field_name: str, value: JsonValue) -> Record:
    if not isinstance(value, Mapping):
        raise InvalidCasObject(f"CAS object array must contain objects: {field_name}")
    return value


def _expect_string_tuple(record: Record, field_name: str) -> tuple[str, ...]:
    value = _required_value(record, field_name)
    if not isinstance(value, tuple):
        raise InvalidCasObject(f"CAS object field must be an array: {field_name}")
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise InvalidCasObject(
                f"CAS object array must contain strings: {field_name}"
            )
        strings.append(item)
    return tuple(strings)


def _expect_authority_mapping(
    record: Record,
    field_name: str,
) -> Mapping[str, AuthorityValue]:
    return _expect_record(record, field_name)


def _expect_authority_value(record: Record, field_name: str) -> AuthorityValue | None:
    return _required_value(record, field_name)


def _optional_id(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_stage_kind_id(record: Record, field_name: str) -> StageKindId | None:
    value = _expect_optional_string(record, field_name)
    if value is None:
        return None
    return StageKindId(value)


def _optional_queue_family_id(record: Record, field_name: str) -> QueueFamilyId | None:
    value = _expect_optional_string(record, field_name)
    if value is None:
        return None
    return QueueFamilyId(value)


def _optional_artifact_schema_id(
    record: Record,
    field_name: str,
) -> ArtifactSchemaId | None:
    value = _expect_optional_string(record, field_name)
    if value is None:
        return None
    return ArtifactSchemaId(value)


def _optional_runner_binding_id(
    record: Record,
    field_name: str,
) -> RunnerBindingId | None:
    value = _expect_optional_string(record, field_name)
    if value is None:
        return None
    return RunnerBindingId(value)


def _optional_wait_state_id(record: Record, field_name: str) -> WaitStateId | None:
    value = _expect_optional_string(record, field_name)
    if value is None:
        return None
    return WaitStateId(value)


__all__ = (
    "decode_payload",
    "decode_selected_compiled_plan",
    "dumps_cas_object",
    "encode_payload",
    "encode_selected_compiled_plan",
    "loads_cas_object",
)
