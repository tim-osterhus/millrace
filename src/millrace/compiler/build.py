"""Selected compiled plan construction.

This module owns construction of immutable selected plan records after
validation passes. It must not validate source semantics or include unselected
catalog data.
"""

from __future__ import annotations

from collections.abc import Mapping

from millrace.compiler.source import (
    SourceRecord,
    authority_mapping,
    is_non_empty_text,
    is_sequence,
    mapping,
    records,
    text_tuple,
)
from millrace.contracts import (
    ActionId,
    ArtifactSchemaDeclaration,
    ArtifactSchemaId,
    AssetDeclaration,
    AssetId,
    CapabilityDeclaration,
    CapabilityId,
    CompletionBehaviorDeclaration,
    CompletionBehaviorId,
    ConcurrencyPolicyDeclaration,
    CounterDeclaration,
    CounterId,
    EffectDeclaration,
    EffectDeclarationId,
    ExternalEnqueueRouteDeclaration,
    FanoutDeclaration,
    FanoutId,
    GeneratedWorkRouteDeclaration,
    GraphDeclaration,
    GraphId,
    InterventionOptionDeclaration,
    InterventionOptionId,
    JoinDeclaration,
    OperatorWaitDeclaration,
    OperatorWaitId,
    OutcomeId,
    PartitionDeclaration,
    PartitionId,
    QueueFamilyDeclaration,
    QueueFamilyId,
    RecoveryPolicyDeclaration,
    RecoveryPolicyId,
    RemediationPolicyDeclaration,
    RemediationPolicyId,
    RunnerBindingDeclaration,
    RunnerBindingId,
    RunnerComponentPin,
    RunnerTerminalResultMapping,
    SelectedCompiledPlan,
    StageKindDeclaration,
    StageKindId,
    TerminalActionDeclaration,
    TerminalOutcomeDeclaration,
    WaitStateDeclaration,
    WaitStateId,
    WorkflowId,
    WorkflowIdentity,
    WorkflowVersion,
)
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    ContextSourceDeclaration,
    ContextWriteRule,
    StageContextBindingDeclaration,
    freeze_authority_value,
)
from millrace.contracts.operator_waits import (
    _canonical_operator_wait_resolution_kinds,
    _canonical_operator_wait_source_action_ids,
)


def build_selected_plan(
    source: Mapping[str, object],
    workflow: SourceRecord,
) -> SelectedCompiledPlan:
    selected_artifact_schema_ids = _selected_artifact_schema_ids(source)
    return SelectedCompiledPlan(
        workflow=WorkflowIdentity(
            workflow_id=WorkflowId(str(workflow["id"])),
            workflow_version=WorkflowVersion(str(workflow["version"])),
            workflow_name=str(workflow.get("name", "")),
        ),
        compatibility_profile=None,
        required_extensions=tuple(text_tuple(workflow.get("required_extensions", ()))),
        graphs=tuple(
            GraphDeclaration(
                id=GraphId(str(record["id"])),
                node_ids=tuple(text_tuple(record.get("node_ids", ()))),
                presentation=authority_mapping(record.get("presentation")),
            )
            for record in _records_by_id(source, "graphs")
        ),
        partitions=tuple(
            PartitionDeclaration(
                id=PartitionId(str(record["id"])),
                partition_kind=str(record.get("kind", "")),
                presentation=authority_mapping(record.get("presentation")),
            )
            for record in _records_by_id(source, "partitions")
        ),
        queue_families=tuple(
            QueueFamilyDeclaration(
                id=QueueFamilyId(str(record["id"])),
                external_enqueue=bool(record.get("external_enqueue", False)),
                presentation=authority_mapping(record.get("presentation")),
            )
            for record in _records_by_id(source, "queue_families")
        ),
        external_enqueue_routes=tuple(
            ExternalEnqueueRouteDeclaration(
                id=str(record["id"]),
                queue_family_id=QueueFamilyId(str(record["queue_family_id"])),
                graph_node_id=str(record["graph_node_id"]),
                stage_kind_id=StageKindId(str(record["stage_kind_id"])),
                runner_binding_id=RunnerBindingId(str(record["runner_binding_id"])),
                payload_schema_id=_optional_artifact_schema_id(
                    record.get("payload_schema_id")
                ),
            )
            for record in _records_by_id(source, "external_enqueue_routes")
        ),
        generated_work_routes=tuple(
            GeneratedWorkRouteDeclaration(
                id=str(record["id"]),
                queue_family_id=QueueFamilyId(str(record["queue_family_id"])),
                graph_node_id=str(record["graph_node_id"]),
                stage_kind_id=StageKindId(str(record["stage_kind_id"])),
                runner_binding_id=RunnerBindingId(str(record["runner_binding_id"])),
                payload_schema_id=_optional_artifact_schema_id(
                    record.get("payload_schema_id")
                ),
            )
            for record in _records_by_id(source, "generated_work_routes")
        ),
        artifact_schemas=tuple(
            ArtifactSchemaDeclaration(
                id=ArtifactSchemaId(str(record["id"])),
                schema=authority_mapping(record.get("schema")),
                presentation=authority_mapping(record.get("presentation")),
            )
            for record in _records_by_id(source, "artifact_schemas")
            if str(record["id"]) in selected_artifact_schema_ids
        ),
        assets=tuple(
            AssetDeclaration(
                id=AssetId(str(record["id"])),
                asset_kind=str(record.get("kind", "")),
                body=str(record.get("body", "")),
                presentation=authority_mapping(record.get("presentation")),
            )
            for record in _records_by_id(source, "assets")
        ),
        stage_kinds=tuple(
            StageKindDeclaration(
                id=StageKindId(str(record["id"])),
                partition_id=_optional_partition_id(record.get("partition_id")),
                runner_binding_id=RunnerBindingId(str(record["runner_binding_id"])),
                input_queue_family_ids=tuple(
                    QueueFamilyId(value)
                    for value in text_tuple(record.get("input_queue_family_ids", ()))
                ),
                output_queue_family_ids=tuple(
                    QueueFamilyId(value)
                    for value in text_tuple(record.get("output_queue_family_ids", ()))
                ),
                artifact_schema_ids=tuple(
                    ArtifactSchemaId(value)
                    for value in text_tuple(record.get("artifact_schema_ids", ()))
                ),
                asset_ids=tuple(
                    AssetId(value) for value in text_tuple(record.get("asset_ids", ()))
                ),
                declared_outcome_ids=tuple(
                    OutcomeId(value)
                    for value in text_tuple(record.get("declared_outcome_ids", ()))
                ),
                presentation=authority_mapping(record.get("presentation")),
            )
            for record in _records_by_id(source, "stage_kinds")
        ),
        terminal_outcomes=tuple(
            TerminalOutcomeDeclaration(
                id=OutcomeId(str(record["id"])),
                stage_kind_id=StageKindId(str(record["stage_kind_id"])),
                marker=str(record.get("marker", "")),
                presentation=authority_mapping(record.get("presentation")),
            )
            for record in _records_by_id(source, "terminal_outcomes")
        ),
        terminal_actions=tuple(
            TerminalActionDeclaration(
                id=ActionId(str(record["id"])),
                stage_kind_id=StageKindId(str(record["stage_kind_id"])),
                outcome_id=OutcomeId(str(record["outcome_id"])),
                action_kind=str(record.get("kind", "")),
                target_stage_kind_id=_optional_stage_kind_id(
                    record.get("target_stage_kind_id")
                ),
                target_graph_node_id=_optional_text(record.get("target_graph_node_id")),
                emitted_queue_family_id=_optional_queue_family_id(
                    record.get("emitted_queue_family_id")
                ),
                artifact_schema_id=_optional_artifact_schema_id(
                    record.get("artifact_schema_id")
                ),
                runner_binding_id=_optional_runner_binding_id(
                    record.get("runner_binding_id")
                ),
                asset_ids=tuple(
                    AssetId(value) for value in text_tuple(record.get("asset_ids", ()))
                ),
                payload_projection=_optional_authority_value(
                    record.get("payload_projection")
                ),
                presentation=authority_mapping(record.get("presentation")),
                dynamic_target_selector=_optional_authority_value(
                    record.get("dynamic_target_selector")
                ),
            )
            for record in _records_by_id(source, "terminal_actions")
        ),
        effect_declarations=tuple(
            EffectDeclaration(
                effect_declaration_id=EffectDeclarationId(str(record["id"])),
                terminal_action_id=ActionId(str(record["terminal_action_id"])),
                artifact_schema_id=ArtifactSchemaId(str(record["artifact_schema_id"])),
                provider_ref=str(record["provider_ref"]),
                capability_policy_ref=str(record["capability_policy_ref"]),
                target_ref_kind=str(record["target_ref_kind"]),
                target_ref_schema=str(record["target_ref_schema"]),
                allowed_reconciliation_statuses=tuple(
                    text_tuple(record.get("allowed_reconciliation_statuses", ()))
                ),
                real_side_effects_allowed=bool(
                    record.get("real_side_effects_allowed", False)
                ),
            )
            for record in _records_by_id(source, "effect_declarations")
        ),
        fanout_declarations=tuple(
            _build_fanout_declaration(source, record)
            for record in _records_by_id(source, "fanout_declarations")
        ),
        join_declarations=tuple(
            JoinDeclaration(
                id=str(record["id"]),
                target_stage_kind_id=StageKindId(str(record["target_stage_kind_id"])),
                correlation_key=str(record["correlation_key"]),
                required_artifact_schema_ids=tuple(
                    ArtifactSchemaId(value)
                    for value in text_tuple(
                        record.get("required_artifact_schema_ids", ())
                    )
                ),
                missing_policy=str(record["missing_policy"]),
            )
            for record in _records_by_id(source, "join_declarations")
        ),
        concurrency_policies=tuple(
            ConcurrencyPolicyDeclaration(
                id=str(record["id"]),
                partition_id=PartitionId(str(record["partition_id"])),
                max_active_runs=_required_int(record["max_active_runs"]),
                coexist_partition_ids=tuple(
                    PartitionId(value)
                    for value in text_tuple(record.get("coexist_partition_ids", ()))
                ),
            )
            for record in _records_by_id(source, "concurrency_policies")
        ),
        recovery_policies=tuple(
            RecoveryPolicyDeclaration(
                id=RecoveryPolicyId(str(record["id"])),
                source_recovery_action_ids=tuple(
                    ActionId(value)
                    for value in text_tuple(record.get("source_recovery_action_ids"))
                ),
                return_action_ids=tuple(
                    ActionId(value)
                    for value in text_tuple(record.get("return_action_ids"))
                ),
                quarantine_action_ids=tuple(
                    ActionId(value)
                    for value in text_tuple(record.get("quarantine_action_ids"))
                ),
                recovery_stage_kind_id=StageKindId(
                    str(record["recovery_stage_kind_id"])
                ),
                recorded_source_selector=str(
                    record.get("recorded_source_selector", "")
                ),
                attempt_scope=str(record.get("attempt_scope", "")),
                immediate_recovery_limit=_required_int(
                    record.get("immediate_recovery_limit")
                ),
                cooldown_starts_at_attempt=_required_int(
                    record.get("cooldown_starts_at_attempt")
                ),
                quarantine_threshold_attempt=_required_int(
                    record.get("quarantine_threshold_attempt")
                ),
                threshold_behavior=str(record.get("threshold_behavior", "")),
                return_allowed_phases=tuple(
                    text_tuple(record.get("return_allowed_phases"))
                ),
                reset_trigger_action_ids=tuple(
                    ActionId(value)
                    for value in text_tuple(record.get("reset_trigger_action_ids"))
                ),
                default_cooldown_seconds=_required_int(
                    record.get("default_cooldown_seconds")
                ),
                cooldown_wait_state_id=_optional_wait_state_id(
                    record.get("cooldown_wait_state_id")
                ),
            )
            for record in _records_by_id(source, "recovery_policies")
        ),
        wait_states=tuple(
            WaitStateDeclaration(
                id=WaitStateId(str(record["id"])),
                wait_kind=str(record.get("kind", "")),
                policy_id=RecoveryPolicyId(str(record["policy_id"])),
                starts_at_attempt=_required_int(record.get("starts_at_attempt")),
                duration_seconds=_required_int(record.get("duration_seconds")),
            )
            for record in _records_by_id(source, "wait_states")
        ),
        counters=tuple(
            CounterDeclaration(
                id=CounterId(str(record["id"])),
                counter_kind=str(record.get("kind", "")),
                scope=str(record.get("scope", "")),
                stage_kind_id=StageKindId(str(record["stage_kind_id"])),
                increment_action_id=ActionId(str(record["increment_action_id"])),
                threshold_action_id=ActionId(str(record["threshold_action_id"])),
                threshold_count=_required_int(record.get("threshold_count")),
            )
            for record in _records_by_id(source, "counters")
        ),
        completion_behaviors=tuple(
            CompletionBehaviorDeclaration(
                id=CompletionBehaviorId(str(record["id"])),
                trigger=str(record.get("trigger", "")),
                readiness_rule=str(record.get("readiness_rule", "")),
                request_kind=str(record.get("request_kind", "")),
                target_selector=str(record.get("target_selector", "")),
                target_stage_kind_id=StageKindId(
                    str(record["target_stage_kind_id"])
                ),
                target_graph_node_id=str(record["target_graph_node_id"]),
                runner_binding_id=RunnerBindingId(str(record["runner_binding_id"])),
                request_queue_family_id=QueueFamilyId(
                    str(record["request_queue_family_id"])
                ),
                pass_action_id=ActionId(str(record["pass_action_id"])),
                gap_action_id=ActionId(str(record["gap_action_id"])),
                blocked_action_id=ActionId(str(record["blocked_action_id"])),
                verdict_artifact_schema_id=ArtifactSchemaId(
                    str(record["verdict_artifact_schema_id"])
                ),
                evidence_artifact_schema_ids=tuple(
                    ArtifactSchemaId(value)
                    for value in text_tuple(
                        record["evidence_artifact_schema_ids"]
                    )
                ),
                evidence_item_limit=_required_int(record["evidence_item_limit"]),
                request_payload_byte_limit=_required_int(
                    record["request_payload_byte_limit"]
                ),
                remediation_policy_id=RemediationPolicyId(
                    str(record["remediation_policy_id"])
                ),
                accepted_root_source_kinds=tuple(
                    text_tuple(record.get("accepted_root_source_kinds", ()))
                ),
                root_source_resolution=str(record.get("root_source_resolution", "")),
                evidence_window_policy=str(record.get("evidence_window_policy", "")),
                rubric_policy=str(record.get("rubric_policy", "")),
                blocked_work_policy=str(record.get("blocked_work_policy", "")),
                skip_if_closed=bool(record.get("skip_if_closed", False)),
                presentation=authority_mapping(record.get("presentation")),
            )
            for record in _records_by_id(source, "completion_behaviors")
        ),
        remediation_policies=tuple(
            RemediationPolicyDeclaration(
                id=RemediationPolicyId(str(record["id"])),
                source_action_id=ActionId(str(record["source_action_id"])),
                target_queue_family_id=QueueFamilyId(
                    str(record["target_queue_family_id"])
                ),
                target_stage_kind_id=StageKindId(str(record["target_stage_kind_id"])),
                target_graph_node_id=str(record["target_graph_node_id"]),
                target_runner_binding_id=RunnerBindingId(
                    str(record["target_runner_binding_id"])
                ),
                payload_schema_id=ArtifactSchemaId(str(record["payload_schema_id"])),
                guidance_source=str(record.get("guidance_source", "")),
                dedupe_key=str(record.get("dedupe_key", "")),
                duplicate_policy=str(record.get("duplicate_policy", "")),
                suppression_policy=str(record.get("suppression_policy", "")),
                root_source_kind=str(record.get("root_source_kind", "")),
                presentation=authority_mapping(record.get("presentation")),
            )
            for record in _records_by_id(source, "remediation_policies")
        ),
        lineage_policy=str(source["lineage_policy"]),
        runner_bindings=tuple(
            RunnerBindingDeclaration(
                id=RunnerBindingId(str(record["id"])),
                adapter_kind=str(record.get("adapter_kind", "")),
                stage_kind_ids=tuple(
                    StageKindId(value)
                    for value in text_tuple(record.get("stage_kind_ids", ()))
                ),
                invocation_timeout_seconds=_required_int(
                    record["invocation_timeout_seconds"]
                ),
                presentation=authority_mapping(record.get("presentation")),
                required_capability_ids=tuple(
                    CapabilityId(value)
                    for value in text_tuple(
                        record.get("required_capability_ids", ())
                    )
                ),
                component_pin=_optional_runner_component_pin(
                    record.get("component_pin")
                ),
                terminal_result_mappings=_runner_terminal_result_mappings(
                    record.get("terminal_result_mappings", ())
                ),
            )
            for record in _records_by_id(source, "runner_bindings")
        ),
        intervention_options=tuple(
            InterventionOptionDeclaration(
                id=InterventionOptionId(str(record["id"])),
                policy_id=RecoveryPolicyId(str(record["policy_id"])),
                option_kind=str(record.get("kind", "")),
                legal_source_state=str(record.get("legal_source_state", "")),
                target_selector=str(record.get("target_selector", "")),
                resume_target_selector=_optional_text(
                    record.get("resume_target_selector")
                ),
                close_behavior=_optional_text(record.get("close_behavior")),
                payload_schema_id=_optional_artifact_schema_id(
                    record.get("payload_schema_id")
                ),
                target_queue_family_id=_optional_queue_family_id(
                    record.get("target_queue_family_id")
                ),
                target_stage_kind_id=_optional_stage_kind_id(
                    record.get("target_stage_kind_id")
                ),
                target_graph_node_id=_optional_text(
                    record.get("target_graph_node_id")
                ),
                target_runner_binding_id=_optional_runner_binding_id(
                    record.get("target_runner_binding_id")
                ),
                supersede_behavior=str(record.get("supersede_behavior", "")),
                attempt_effect=str(record.get("attempt_effect", "")),
                actor_kind=str(record.get("actor_kind", "local_operator")),
                audit_metadata_requirements=tuple(
                    text_tuple(record.get("audit_metadata_requirements", ()))
                ),
            )
            for record in _records_by_id(source, "intervention_options")
        ),
        operator_waits=tuple(
            OperatorWaitDeclaration(
                id=OperatorWaitId(str(record["id"])),
                source_action_ids=tuple(
                    ActionId(value)
                    for value in _canonical_operator_wait_source_action_ids(
                        text_tuple(record.get("source_action_ids", ()))
                    )
                ),
                wait_scope=str(record.get("wait_scope", "")),
                source_work_item_behavior=str(
                    record.get("source_work_item_behavior", "")
                ),
                project_source_artifact=bool(record["project_source_artifact"]),
                unrelated_lineages_continue=bool(
                    record.get("unrelated_lineages_continue", False)
                ),
                allowed_resolution_kinds=tuple(
                    _canonical_operator_wait_resolution_kinds(
                        text_tuple(record.get("allowed_resolution_kinds", ()))
                    )
                ),
                payload_schema_id=_optional_artifact_schema_id(
                    record.get("payload_schema_id")
                ),
                target_queue_family_id=_optional_queue_family_id(
                    record.get("target_queue_family_id")
                ),
                target_stage_kind_id=_optional_stage_kind_id(
                    record.get("target_stage_kind_id")
                ),
                target_graph_node_id=_optional_text(
                    record.get("target_graph_node_id")
                ),
                target_runner_binding_id=_optional_runner_binding_id(
                    record.get("target_runner_binding_id")
                ),
                actor_kind=str(record.get("actor_kind", "local_operator")),
                audit_metadata_requirements=tuple(
                    text_tuple(record.get("audit_metadata_requirements", ()))
                ),
                correlation_key=str(record.get("correlation_key", "")),
                idempotency=str(record.get("idempotency", "")),
                timeout_policy=str(record.get("timeout_policy", "")),
                expiry_policy=str(record.get("expiry_policy", "")),
                cancellation_policy=str(record.get("cancellation_policy", "")),
                status_effect=str(record.get("status_effect", "")),
            )
            for record in _records_by_id(source, "operator_waits")
        ),
        capabilities=tuple(
            CapabilityDeclaration(
                id=CapabilityId(str(record["id"])),
                capability_kind=str(record.get("kind", "")),
                support_status=str(record.get("support_status", "")),
                grant_status=str(record.get("grant_status", "")),
                approval_policy_id=_optional_text(record.get("approval_policy_id")),
            )
            for record in _records_by_id(source, "capabilities")
        ),
        context_bindings=tuple(
            _build_context_binding(record)
            for record in _records_by_id(source, "context_bindings")
        ),
    )


def _records_by_id(source: Mapping[str, object], key: str) -> tuple[SourceRecord, ...]:
    return tuple(sorted(records(source, key), key=lambda record: str(record["id"])))


def _optional_runner_component_pin(value: object) -> RunnerComponentPin | None:
    if value is None:
        return None
    record = mapping(value)
    return RunnerComponentPin(
        component_kind=str(record["component_kind"]),
        component_id=str(record["component_id"]),
        component_version=str(record["component_version"]),
        provider_distribution=str(record["provider_distribution"]),
        provider_version=str(record["provider_version"]),
        descriptor_media_type=str(record["descriptor_media_type"]),
        descriptor_sha256=str(record["descriptor_sha256"]),
        required_capability_ids=tuple(
            CapabilityId(item)
            for item in text_tuple(record.get("required_capability_ids", ()))
        ),
        legal_terminal_result_ids=tuple(
            text_tuple(record.get("legal_terminal_result_ids", ()))
        ),
        max_work_item_payload_bytes=_optional_int(
            record.get("max_work_item_payload_bytes")
        ),
    )


def _runner_terminal_result_mappings(
    value: object,
) -> tuple[RunnerTerminalResultMapping, ...]:
    if not is_sequence(value):
        return ()
    return tuple(
        RunnerTerminalResultMapping(
            stage_kind_id=StageKindId(str(record["stage_kind_id"])),
            runner_result_id=str(record["runner_result_id"]),
            outcome_id=OutcomeId(str(record["outcome_id"])),
        )
        for record in (mapping(item) for item in value)
    )


def _selected_artifact_schema_ids(source: Mapping[str, object]) -> frozenset[str]:
    schema_ids: set[str] = set()
    for record in records(source, "stage_kinds"):
        schema_ids.update(text_tuple(record.get("artifact_schema_ids", ())))
    for record in records(source, "terminal_actions"):
        raw_schema_id = record.get("artifact_schema_id")
        if is_non_empty_text(raw_schema_id):
            schema_ids.add(str(raw_schema_id))
    for record in records(source, "fanout_declarations"):
        for field_name in ("source_artifact_schema_id", "target_payload_schema_id"):
            raw_schema_id = record.get(field_name)
            if is_non_empty_text(raw_schema_id):
                schema_ids.add(str(raw_schema_id))
    for record in records(source, "join_declarations"):
        schema_ids.update(text_tuple(record.get("required_artifact_schema_ids", ())))
    for record in records(source, "external_enqueue_routes"):
        raw_schema_id = record.get("payload_schema_id")
        if is_non_empty_text(raw_schema_id):
            schema_ids.add(str(raw_schema_id))
    for record in records(source, "generated_work_routes"):
        raw_schema_id = record.get("payload_schema_id")
        if is_non_empty_text(raw_schema_id):
            schema_ids.add(str(raw_schema_id))
    for record in records(source, "intervention_options"):
        raw_schema_id = record.get("payload_schema_id")
        if is_non_empty_text(raw_schema_id):
            schema_ids.add(str(raw_schema_id))
    for record in records(source, "operator_waits"):
        raw_schema_id = record.get("payload_schema_id")
        if is_non_empty_text(raw_schema_id):
            schema_ids.add(str(raw_schema_id))
    for record in records(source, "completion_behaviors"):
        raw_schema_id = record.get("verdict_artifact_schema_id")
        if is_non_empty_text(raw_schema_id):
            schema_ids.add(str(raw_schema_id))
        schema_ids.update(text_tuple(record.get("evidence_artifact_schema_ids", ())))
    for record in records(source, "remediation_policies"):
        raw_schema_id = record.get("payload_schema_id")
        if is_non_empty_text(raw_schema_id):
            schema_ids.add(str(raw_schema_id))
    for record in records(source, "context_bindings"):
        raw_schema_id = record.get("writeback_artifact_schema_id")
        if is_non_empty_text(raw_schema_id):
            schema_ids.add(str(raw_schema_id))
    return frozenset(schema_ids)


def _build_context_binding(record: SourceRecord) -> StageContextBindingDeclaration:
    return StageContextBindingDeclaration(
        id=str(record["id"]),
        stage_kind_id=StageKindId(str(record["stage_kind_id"])),
        router_asset_id=AssetId(str(record["router_asset_id"])),
        checkout_root=str(record["checkout_root"]),
        required_sources=tuple(
            ContextSourceDeclaration(
                source_kind=str(source["source_kind"]),
                source_ref=str(source["source_ref"]),
                max_files=_required_int(source["max_files"]),
                max_bytes=_required_int(source["max_bytes"]),
            )
            for source in records(record, "required_sources")
        ),
        discoverable_sources=tuple(
            ContextSourceDeclaration(
                source_kind=str(source["source_kind"]),
                source_ref=str(source["source_ref"]),
                max_files=_required_int(source["max_files"]),
                max_bytes=_required_int(source["max_bytes"]),
            )
            for source in records(record, "discoverable_sources")
        ),
        write_rules=tuple(
            ContextWriteRule(
                relative_root=str(rule["relative_root"]),
                disposition=str(rule["disposition"]),
            )
            for rule in records(record, "write_rules")
        ),
        writeback_terminal_action_id=_optional_context_action_id(
            record.get("writeback_terminal_action_id")
        ),
        writeback_artifact_schema_id=_optional_context_artifact_schema_id(
            record.get("writeback_artifact_schema_id")
        ),
    )


def _build_fanout_declaration(
    source: Mapping[str, object],
    record: SourceRecord,
) -> FanoutDeclaration:
    target_route = _route_by_id(source, str(record["target_route_id"]))
    return FanoutDeclaration(
        id=FanoutId(str(record["id"])),
        source_action_id=ActionId(str(record["source_action_id"])),
        source_artifact_schema_id=ArtifactSchemaId(
            str(record["source_artifact_schema_id"])
        ),
        item_source_path=tuple(text_tuple(record.get("item_source_path", ()))),
        item_id_key=str(record["item_id_key"]),
        target_route_id=str(record["target_route_id"]),
        source_state_policy=str(record.get("source_state_policy", "source_closed")),
        target_queue_family_id=QueueFamilyId(str(target_route["queue_family_id"])),
        target_stage_kind_id=StageKindId(str(target_route["stage_kind_id"])),
        target_graph_node_id=str(target_route["graph_node_id"]),
        target_runner_binding_id=RunnerBindingId(str(target_route["runner_binding_id"])),
        target_payload_schema_id=ArtifactSchemaId(
            str(record["target_payload_schema_id"])
        ),
        target_payload_mapping=authority_mapping(record.get("target_payload_mapping")),
        duplicate_policy=str(record["duplicate_policy"]),
        root_lineage_policy=str(record["root_lineage_policy"]),
        dependency_policy=str(record["dependency_policy"]),
    )


def _route_by_id(
    source: Mapping[str, object],
    route_id: str,
) -> SourceRecord:
    for collection in ("external_enqueue_routes", "generated_work_routes"):
        for record in records(source, collection):
            if record.get("id") == route_id:
                return record
    raise KeyError(route_id)


def _required_int(value: object) -> int:
    if type(value) is int:
        return value
    raise TypeError("compiled source value must be an integer")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is int:
        return value
    raise TypeError("compiled source value must be an integer or null")


def _optional_stage_kind_id(value: object) -> StageKindId | None:
    return StageKindId(str(value)) if is_non_empty_text(value) else None


def _optional_partition_id(value: object) -> PartitionId | None:
    return PartitionId(str(value)) if is_non_empty_text(value) else None


def _optional_text(value: object) -> str | None:
    return str(value) if is_non_empty_text(value) else None


def _optional_authority_value(value: object) -> AuthorityValue | None:
    return None if value is None else freeze_authority_value(value)


def _optional_queue_family_id(value: object) -> QueueFamilyId | None:
    return QueueFamilyId(str(value)) if is_non_empty_text(value) else None


def _optional_action_id(value: object) -> ActionId | None:
    return ActionId(str(value)) if is_non_empty_text(value) else None


def _optional_artifact_schema_id(value: object) -> ArtifactSchemaId | None:
    return ArtifactSchemaId(str(value)) if is_non_empty_text(value) else None


def _optional_context_action_id(value: object) -> ActionId | None:
    if value is None:
        return None
    if type(value) is str and value:
        return ActionId(value)
    raise TypeError("context writeback action linkage must be text or null")


def _optional_context_artifact_schema_id(
    value: object,
) -> ArtifactSchemaId | None:
    if value is None:
        return None
    if type(value) is str and value:
        return ArtifactSchemaId(value)
    raise TypeError("context writeback schema linkage must be text or null")


def _optional_runner_binding_id(value: object) -> RunnerBindingId | None:
    return RunnerBindingId(str(value)) if is_non_empty_text(value) else None


def _optional_wait_state_id(value: object) -> WaitStateId | None:
    return WaitStateId(str(value)) if is_non_empty_text(value) else None


__all__ = ("build_selected_plan",)
