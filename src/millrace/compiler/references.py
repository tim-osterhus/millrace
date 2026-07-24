"""Reference validation for authored compiler source.

This module owns ID indexing and cross-reference diagnostics. It must not build
selected plan records or apply runtime defaults.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from unicodedata import normalize

from millrace.compiler.diagnostics import (
    canonically_equivalent_id_diagnostic,
    compiler_error,
    duplicate_id_diagnostic,
    missing_id_diagnostic,
    missing_reference_diagnostic,
    non_nfc_id_diagnostic,
    outcome_stage_mismatch_diagnostic,
    outcome_without_action_diagnostic,
)
from millrace.compiler.source import (
    SourceRecord,
    is_non_empty_text,
    is_sequence,
    records,
    text_tuple,
)
from millrace.contracts import Diagnostic


@dataclass(frozen=True, slots=True)
class IdIndex:
    ids: frozenset[str]
    paths_by_id: Mapping[str, str]


COLLECTION_NAMESPACES: tuple[tuple[str, str], ...] = (
    ("partitions", "partition"),
    ("queue_families", "queue_family"),
    ("external_enqueue_routes", "external_enqueue_route"),
    ("generated_work_routes", "generated_work_route"),
    ("artifact_schemas", "artifact_schema"),
    ("assets", "asset"),
    ("stage_kinds", "stage_kind"),
    ("terminal_outcomes", "terminal_outcome"),
    ("terminal_actions", "terminal_action"),
    ("effect_declarations", "effect_declaration"),
    ("fanout_declarations", "fanout_declaration"),
    ("join_declarations", "join_declaration"),
    ("concurrency_policies", "concurrency_policy"),
    ("recovery_policies", "recovery_policy"),
    ("completion_behaviors", "completion_behavior"),
    ("remediation_policies", "remediation_policy"),
    ("wait_states", "wait_state"),
    ("counters", "counter"),
    ("intervention_options", "intervention_option"),
    ("operator_waits", "operator_wait"),
    ("runner_bindings", "runner_binding"),
    ("capabilities", "capability"),
    ("graphs", "graph"),
)

SUPPORTED_CAPABILITY_KINDS = frozenset({"runner.invoke"})
SUPPORTED_CAPABILITY_SUPPORT_STATUSES = frozenset({"supported", "unsupported"})
SUPPORTED_CAPABILITY_GRANT_STATUSES = frozenset(
    {"granted", "denied", "approval_pending"}
)

_RUNNER_COMPONENT_PIN_FIELDS = frozenset(
    {
        "component_kind",
        "component_id",
        "component_version",
        "provider_distribution",
        "provider_version",
        "descriptor_media_type",
        "descriptor_sha256",
        "required_capability_ids",
        "legal_terminal_result_ids",
    }
)
_RUNNER_TERMINAL_RESULT_MAPPING_FIELDS = frozenset(
    {"stage_kind_id", "runner_result_id", "outcome_id"}
)

INTERVENTION_OPTION_FIELDS = frozenset(
    {
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

JOIN_DECLARATION_FIELDS = frozenset(
    {
        "id",
        "target_stage_kind_id",
        "correlation_key",
        "required_artifact_schema_ids",
        "missing_policy",
    }
)

_COMMON_INTERVENTION_AUDIT_REQUIREMENTS = (
    "input_id",
    "input_digest",
    "selected_plan_fingerprint",
    "actor_id",
    "actor_kind",
    "reason",
    "option_id",
    "policy_id",
    "lineage_id",
    "quarantine_id",
    "recovery_attempt_record_id",
)

_RESUME_INTERVENTION_AUDIT_REQUIREMENTS = (
    *_COMMON_INTERVENTION_AUDIT_REQUIREMENTS,
    "target_activation_id",
    "empty_payload",
)

_CLOSE_INTERVENTION_AUDIT_REQUIREMENTS = (
    *_COMMON_INTERVENTION_AUDIT_REQUIREMENTS,
    "closed_work_item_ids",
    "closed_activation_ids",
    "closed_run_ids",
    "empty_payload",
)

_REVISE_INTERVENTION_AUDIT_REQUIREMENTS = (
    *_COMMON_INTERVENTION_AUDIT_REQUIREMENTS,
    "recovery_attempt_count",
    "target_work_item_id",
    "target_activation_id",
    "payload_digest",
    "payload_reference",
)


def collect_id_indexes(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> Mapping[str, IdIndex]:
    indexes: dict[str, IdIndex] = {}
    for collection_key, namespace in COLLECTION_NAMESPACES:
        indexes[collection_key] = collect_id_index(
            records=records(source, collection_key),
            collection_key=collection_key,
            namespace=namespace,
            diagnostics=diagnostics,
        )
    return indexes


def collect_id_index(
    *,
    records: tuple[SourceRecord, ...],
    collection_key: str,
    namespace: str,
    diagnostics: list[Diagnostic],
) -> IdIndex:
    ids: set[str] = set()
    paths_by_id: dict[str, str] = {}
    paths_by_nfc_id: dict[str, str] = {}
    for index, record in enumerate(records):
        path = f"{collection_key}[{index}].id"
        raw_id = record.get("id")
        if not is_non_empty_text(raw_id):
            diagnostics.append(
                missing_id_diagnostic(
                    declaration_path=path,
                    namespace=namespace,
                    field="id",
                )
            )
            continue

        identifier = str(raw_id)
        if identifier in paths_by_id:
            diagnostics.append(
                duplicate_id_diagnostic(
                    declaration_path=path,
                    related_declaration_path=paths_by_id[identifier],
                    namespace=namespace,
                    duplicate_id=identifier,
                )
            )
            continue

        identifier_nfc = normalize("NFC", identifier)
        existing_canonical = paths_by_nfc_id.get(identifier_nfc)
        has_identifier_error = False
        if existing_canonical is not None:
            diagnostics.append(
                canonically_equivalent_id_diagnostic(
                    declaration_path=path,
                    related_declaration_path=existing_canonical,
                    namespace=namespace,
                    identifier=identifier,
                    canonical_id=identifier_nfc,
                )
            )
            has_identifier_error = True
        if identifier != identifier_nfc:
            diagnostics.append(
                non_nfc_id_diagnostic(
                    declaration_path=path,
                    namespace=namespace,
                    identifier=identifier,
                    identifier_nfc=identifier_nfc,
                )
            )
            has_identifier_error = True

        paths_by_nfc_id.setdefault(identifier_nfc, path)
        if has_identifier_error:
            continue

        ids.add(identifier)
        paths_by_id[identifier] = path

    return IdIndex(ids=frozenset(ids), paths_by_id=paths_by_id)


def validate_stage_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    for index, record in enumerate(records(source, "stage_kinds")):
        referrer_path = f"stage_kinds[{index}]"
        _validate_single_reference(
            raw_value=record.get("partition_id"),
            ids=indexes["partitions"].ids,
            declaration_path=f"{referrer_path}.partition_id",
            referrer_path=referrer_path,
            reference_kind="partition",
            diagnostics=diagnostics,
            optional=True,
        )
        _validate_single_reference(
            raw_value=record.get("runner_binding_id"),
            ids=indexes["runner_bindings"].ids,
            declaration_path=f"{referrer_path}.runner_binding_id",
            referrer_path=referrer_path,
            reference_kind="runner_binding",
            diagnostics=diagnostics,
        )
        _validate_many_references(
            raw_values=record.get("input_queue_family_ids", ()),
            ids=indexes["queue_families"].ids,
            declaration_path=f"{referrer_path}.input_queue_family_ids",
            referrer_path=referrer_path,
            reference_kind="queue_family",
            diagnostics=diagnostics,
        )
        _validate_many_references(
            raw_values=record.get("output_queue_family_ids", ()),
            ids=indexes["queue_families"].ids,
            declaration_path=f"{referrer_path}.output_queue_family_ids",
            referrer_path=referrer_path,
            reference_kind="queue_family",
            diagnostics=diagnostics,
        )
        _validate_many_references(
            raw_values=record.get("artifact_schema_ids", ()),
            ids=indexes["artifact_schemas"].ids,
            declaration_path=f"{referrer_path}.artifact_schema_ids",
            referrer_path=referrer_path,
            reference_kind="artifact_schema",
            diagnostics=diagnostics,
        )
        _validate_many_references(
            raw_values=record.get("asset_ids", ()),
            ids=indexes["assets"].ids,
            declaration_path=f"{referrer_path}.asset_ids",
            referrer_path=referrer_path,
            reference_kind="asset",
            diagnostics=diagnostics,
        )
        _validate_many_references(
            raw_values=record.get("declared_outcome_ids", ()),
            ids=indexes["terminal_outcomes"].ids,
            declaration_path=f"{referrer_path}.declared_outcome_ids",
            referrer_path=referrer_path,
            reference_kind="terminal_outcome",
            diagnostics=diagnostics,
        )


def validate_partition_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    referenced_partition_ids: set[str] = set()
    for record in records(source, "stage_kinds"):
        partition_id = record.get("partition_id")
        if is_non_empty_text(partition_id):
            referenced_partition_ids.add(str(partition_id))
    for record in records(source, "concurrency_policies"):
        partition_id = record.get("partition_id")
        if is_non_empty_text(partition_id):
            referenced_partition_ids.add(str(partition_id))
        referenced_partition_ids.update(
            text_tuple(record.get("coexist_partition_ids", ()))
        )

    for partition_id in sorted(indexes["partitions"].ids - referenced_partition_ids):
        diagnostics.append(
            compiler_error(
                code="unreferenced_partition",
                declaration_path=indexes["partitions"].paths_by_id[partition_id],
                message="Partition is not referenced by selected authority.",
                context={"partition_id": partition_id},
                hint=(
                    "Remove unused partitions or reference them from selected "
                    "authority."
                ),
            )
        )


def validate_outcome_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    for index, record in enumerate(records(source, "terminal_outcomes")):
        referrer_path = f"terminal_outcomes[{index}]"
        _validate_single_reference(
            raw_value=record.get("stage_kind_id"),
            ids=indexes["stage_kinds"].ids,
            declaration_path=f"{referrer_path}.stage_kind_id",
            referrer_path=referrer_path,
            reference_kind="stage_kind",
            diagnostics=diagnostics,
        )


def validate_external_enqueue_route_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    queue_external_flags = _queue_family_external_flags(source)
    stage_route_contracts = _stage_route_contracts(source)
    runner_stage_ids = _runner_stage_ids(source)
    graph_node_ids = _declared_graph_node_ids(source)
    route_paths_by_queue_family_id: dict[str, tuple[str, str]] = {}
    for index, record in enumerate(records(source, "external_enqueue_routes")):
        referrer_path = f"external_enqueue_routes[{index}]"
        graph_node_id = record.get("graph_node_id")
        if not is_non_empty_text(graph_node_id):
            diagnostics.append(
                missing_id_diagnostic(
                    declaration_path=f"{referrer_path}.graph_node_id",
                    namespace="external_enqueue_route",
                    field="graph_node_id",
                )
            )
        elif str(graph_node_id) not in graph_node_ids:
            diagnostics.append(
                _missing_graph_node_reference_diagnostic(
                    declaration_path=f"{referrer_path}.graph_node_id",
                    referrer_path=referrer_path,
                    referenced_id=str(graph_node_id),
                    message=(
                        "External enqueue route references an unknown graph node."
                    ),
                )
            )
        queue_family_id = record.get("queue_family_id")
        stage_kind_id = record.get("stage_kind_id")
        runner_binding_id = record.get("runner_binding_id")
        _validate_single_reference(
            raw_value=queue_family_id,
            ids=indexes["queue_families"].ids,
            declaration_path=f"{referrer_path}.queue_family_id",
            referrer_path=referrer_path,
            reference_kind="queue_family",
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=stage_kind_id,
            ids=indexes["stage_kinds"].ids,
            declaration_path=f"{referrer_path}.stage_kind_id",
            referrer_path=referrer_path,
            reference_kind="stage_kind",
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=runner_binding_id,
            ids=indexes["runner_bindings"].ids,
            declaration_path=f"{referrer_path}.runner_binding_id",
            referrer_path=referrer_path,
            reference_kind="runner_binding",
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=record.get("payload_schema_id"),
            ids=indexes["artifact_schemas"].ids,
            declaration_path=f"{referrer_path}.payload_schema_id",
            referrer_path=referrer_path,
            reference_kind="artifact_schema",
            diagnostics=diagnostics,
            optional=True,
        )

        if not is_non_empty_text(queue_family_id):
            continue
        route_queue_family_id = str(queue_family_id)
        if route_queue_family_id not in indexes["queue_families"].ids:
            continue
        if queue_external_flags.get(route_queue_family_id) is not True:
            diagnostics.append(
                compiler_error(
                    code="external_enqueue_route_internal_queue",
                    declaration_path=f"{referrer_path}.queue_family_id",
                    message=(
                        "External enqueue route references a queue family that "
                        "does not allow external enqueue."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "queue_family_id": route_queue_family_id,
                        "external_enqueue": False,
                    },
                    hint=(
                        "Set the queue family external_enqueue flag to true "
                        "or use a queue family that already allows external enqueue."
                    ),
                )
            )

        route_id = str(record.get("id", ""))
        route_path = f"{referrer_path}.queue_family_id"
        existing = route_paths_by_queue_family_id.get(route_queue_family_id)
        if existing is not None:
            diagnostics.append(
                compiler_error(
                    code="ambiguous_external_enqueue_route",
                    declaration_path=route_path,
                    related_declaration_path=existing[0],
                    message=("Queue family has more than one external enqueue route."),
                    context={
                        "queue_family_id": route_queue_family_id,
                        "route_id": route_id,
                        "related_route_id": existing[1],
                    },
                    hint=(
                        "Declare at most one external enqueue route per queue family."
                    ),
                )
            )
            continue
        route_paths_by_queue_family_id[route_queue_family_id] = (
            route_path,
            route_id,
        )

        if not is_non_empty_text(stage_kind_id):
            continue
        route_stage_kind_id = str(stage_kind_id)
        if route_stage_kind_id not in indexes["stage_kinds"].ids:
            continue
        input_queue_family_ids, stage_runner_binding_id = stage_route_contracts[
            route_stage_kind_id
        ]
        if route_queue_family_id not in input_queue_family_ids:
            diagnostics.append(
                compiler_error(
                    code="external_enqueue_route_stage_input_mismatch",
                    declaration_path=f"{referrer_path}.queue_family_id",
                    message=(
                        "External enqueue route queue family is not an input "
                        "for the target stage."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "queue_family_id": route_queue_family_id,
                        "stage_kind_id": route_stage_kind_id,
                    },
                    hint=(
                        "Route only to a stage that declares the queue family "
                        "as an input."
                    ),
                )
            )

        if not is_non_empty_text(runner_binding_id):
            continue
        route_runner_binding_id = str(runner_binding_id)
        if route_runner_binding_id not in indexes["runner_bindings"].ids:
            continue
        if route_runner_binding_id != stage_runner_binding_id:
            diagnostics.append(
                compiler_error(
                    code="external_enqueue_route_stage_runner_mismatch",
                    declaration_path=f"{referrer_path}.runner_binding_id",
                    message=(
                        "External enqueue route runner binding does not match "
                        "the target stage runner binding."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "stage_kind_id": route_stage_kind_id,
                        "route_runner_binding_id": route_runner_binding_id,
                        "stage_runner_binding_id": stage_runner_binding_id,
                    },
                    hint=("Use the runner binding declared by the target stage."),
                )
            )
        if route_stage_kind_id not in runner_stage_ids.get(
            route_runner_binding_id,
            frozenset(),
        ):
            diagnostics.append(
                compiler_error(
                    code="external_enqueue_route_runner_stage_mismatch",
                    declaration_path=f"{referrer_path}.runner_binding_id",
                    message=(
                        "External enqueue route runner binding does not list "
                        "the target stage."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "stage_kind_id": route_stage_kind_id,
                        "runner_binding_id": route_runner_binding_id,
                    },
                    hint=("List the target stage in the referenced runner binding."),
                )
            )


def validate_generated_work_route_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    stage_route_contracts = _stage_route_contracts(source)
    runner_stage_ids = _runner_stage_ids(source)
    graph_node_ids = _declared_graph_node_ids(source)
    external_route_paths = indexes["external_enqueue_routes"].paths_by_id
    for index, record in enumerate(records(source, "generated_work_routes")):
        referrer_path = f"generated_work_routes[{index}]"
        route_id = record.get("id")
        if is_non_empty_text(route_id) and str(route_id) in external_route_paths:
            diagnostics.append(
                compiler_error(
                    code="ambiguous_selected_enqueue_route",
                    declaration_path=f"{referrer_path}.id",
                    related_declaration_path=external_route_paths[str(route_id)],
                    message=(
                        "Generated work route ID conflicts with an external "
                        "enqueue route ID."
                    ),
                    context={
                        "route_id": str(route_id),
                        "referrer_path": referrer_path,
                    },
                    hint=(
                        "Use distinct route IDs across external and generated "
                        "selected enqueue routes."
                    ),
                )
            )
        graph_node_id = record.get("graph_node_id")
        if not is_non_empty_text(graph_node_id):
            diagnostics.append(
                missing_id_diagnostic(
                    declaration_path=f"{referrer_path}.graph_node_id",
                    namespace="generated_work_route",
                    field="graph_node_id",
                )
            )
        elif str(graph_node_id) not in graph_node_ids:
            diagnostics.append(
                _missing_graph_node_reference_diagnostic(
                    declaration_path=f"{referrer_path}.graph_node_id",
                    referrer_path=referrer_path,
                    referenced_id=str(graph_node_id),
                    message="Generated work route references an unknown graph node.",
                )
            )
        queue_family_id = record.get("queue_family_id")
        stage_kind_id = record.get("stage_kind_id")
        runner_binding_id = record.get("runner_binding_id")
        _validate_single_reference(
            raw_value=queue_family_id,
            ids=indexes["queue_families"].ids,
            declaration_path=f"{referrer_path}.queue_family_id",
            referrer_path=referrer_path,
            reference_kind="queue_family",
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=stage_kind_id,
            ids=indexes["stage_kinds"].ids,
            declaration_path=f"{referrer_path}.stage_kind_id",
            referrer_path=referrer_path,
            reference_kind="stage_kind",
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=runner_binding_id,
            ids=indexes["runner_bindings"].ids,
            declaration_path=f"{referrer_path}.runner_binding_id",
            referrer_path=referrer_path,
            reference_kind="runner_binding",
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=record.get("payload_schema_id"),
            ids=indexes["artifact_schemas"].ids,
            declaration_path=f"{referrer_path}.payload_schema_id",
            referrer_path=referrer_path,
            reference_kind="artifact_schema",
            diagnostics=diagnostics,
            optional=True,
        )
        if not (
            is_non_empty_text(queue_family_id)
            and is_non_empty_text(stage_kind_id)
            and is_non_empty_text(runner_binding_id)
            and str(stage_kind_id) in stage_route_contracts
        ):
            continue
        stage_queues, stage_runner_binding_id = stage_route_contracts[
            str(stage_kind_id)
        ]
        if str(queue_family_id) not in stage_queues:
            diagnostics.append(
                compiler_error(
                    code="generated_work_route_stage_input_mismatch",
                    declaration_path=f"{referrer_path}.queue_family_id",
                    message=(
                        "Generated work route queue family is not an input for "
                        "the target stage."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "queue_family_id": str(queue_family_id),
                        "stage_kind_id": str(stage_kind_id),
                    },
                    hint="Route generated work only to a stage that accepts it.",
                )
            )
        if str(runner_binding_id) != stage_runner_binding_id:
            diagnostics.append(
                compiler_error(
                    code="generated_work_route_stage_runner_mismatch",
                    declaration_path=f"{referrer_path}.runner_binding_id",
                    message=(
                        "Generated work route runner binding does not match "
                        "the target stage runner binding."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "stage_kind_id": str(stage_kind_id),
                        "route_runner_binding_id": str(runner_binding_id),
                        "stage_runner_binding_id": stage_runner_binding_id,
                    },
                    hint="Use the runner binding declared by the target stage.",
                )
            )
        if str(stage_kind_id) not in runner_stage_ids.get(
            str(runner_binding_id),
            frozenset(),
        ):
            diagnostics.append(
                compiler_error(
                    code="generated_work_route_runner_stage_mismatch",
                    declaration_path=f"{referrer_path}.runner_binding_id",
                    message=(
                        "Generated work route runner binding does not list "
                        "the target stage."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "stage_kind_id": str(stage_kind_id),
                        "runner_binding_id": str(runner_binding_id),
                    },
                    hint="List the target stage in the referenced runner binding.",
                )
            )


def validate_fanout_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    actions = {
        str(record.get("id")): record for record in records(source, "terminal_actions")
    }
    external_routes = {
        str(record.get("id")): record
        for record in records(source, "external_enqueue_routes")
    }
    generated_routes = {
        str(record.get("id")): record
        for record in records(source, "generated_work_routes")
    }
    routes = {**external_routes, **generated_routes}
    stage_contracts = _stage_route_contracts(source)
    fanout_target_paths: dict[tuple[str, str], str] = {}
    for index, record in enumerate(records(source, "fanout_declarations")):
        referrer_path = f"fanout_declarations[{index}]"
        source_action_id = record.get("source_action_id")
        target_route_id = record.get("target_route_id")
        if is_non_empty_text(source_action_id) and is_non_empty_text(target_route_id):
            fanout_key = (str(source_action_id), str(target_route_id))
            previous_path = fanout_target_paths.get(fanout_key)
            if previous_path is not None:
                diagnostics.append(
                    _fanout_diagnostic(
                        declaration_path=f"{referrer_path}.target_route_id",
                        referrer_path=referrer_path,
                        fanout_id=str(record.get("id", "")),
                        reason="duplicate_target_route",
                        related_declaration_path=previous_path,
                    )
                )
            else:
                fanout_target_paths[fanout_key] = f"{referrer_path}.target_route_id"
        _validate_single_reference(
            raw_value=record.get("source_action_id"),
            ids=indexes["terminal_actions"].ids,
            declaration_path=f"{referrer_path}.source_action_id",
            referrer_path=referrer_path,
            reference_kind="terminal_action",
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=record.get("source_artifact_schema_id"),
            ids=indexes["artifact_schemas"].ids,
            declaration_path=f"{referrer_path}.source_artifact_schema_id",
            referrer_path=referrer_path,
            reference_kind="artifact_schema",
            diagnostics=diagnostics,
        )
        target_route_reference_kind = (
            "generated_or_external_route"
            if generated_routes
            else "external_enqueue_route"
        )
        _validate_single_reference(
            raw_value=record.get("target_route_id"),
            ids=frozenset(routes),
            declaration_path=f"{referrer_path}.target_route_id",
            referrer_path=referrer_path,
            reference_kind=target_route_reference_kind,
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=record.get("target_payload_schema_id"),
            ids=indexes["artifact_schemas"].ids,
            declaration_path=f"{referrer_path}.target_payload_schema_id",
            referrer_path=referrer_path,
            reference_kind="artifact_schema",
            diagnostics=diagnostics,
        )
        _validate_fanout_shape(record, referrer_path, diagnostics)

        action = actions.get(str(record.get("source_action_id", "")))
        route = routes.get(str(record.get("target_route_id", "")))
        if action is not None and (
            action.get("artifact_schema_id") != record.get("source_artifact_schema_id")
        ):
            diagnostics.append(
                _fanout_diagnostic(
                    declaration_path=f"{referrer_path}.source_artifact_schema_id",
                    referrer_path=referrer_path,
                    fanout_id=str(record.get("id", "")),
                    reason="source_action_schema_mismatch",
                )
            )
        source_state_policy = record.get("source_state_policy", "source_closed")
        supported_action_kinds = (
            {"close", "complete_work_item"}
            if source_state_policy == "source_closed"
            else {
                "route",
                "create_incident_route",
                "close",
                "complete_work_item",
                "close_with_escalation",
                "block_work_item",
            }
        )
        if action is not None and action.get("kind") not in supported_action_kinds:
            diagnostics.append(
                _fanout_diagnostic(
                    declaration_path=f"{referrer_path}.source_action_id",
                    referrer_path=referrer_path,
                    fanout_id=str(record.get("id", "")),
                    reason="unsupported_source_action_kind",
                )
            )
        if route is None:
            continue
        if route.get("payload_schema_id") != record.get("target_payload_schema_id"):
            diagnostics.append(
                _fanout_diagnostic(
                    declaration_path=f"{referrer_path}.target_route_id",
                    referrer_path=referrer_path,
                    fanout_id=str(record.get("id", "")),
                    reason="target_route_contract_mismatch",
                )
            )
        target_stage = str(route.get("stage_kind_id", ""))
        target_queue = str(route.get("queue_family_id", ""))
        target_contract = stage_contracts.get(target_stage)
        if target_contract is not None and target_queue not in target_contract[0]:
            diagnostics.append(
                _fanout_diagnostic(
                    declaration_path=f"{referrer_path}.target_route_id",
                    referrer_path=referrer_path,
                    fanout_id=str(record.get("id", "")),
                    reason="target_route_contract_mismatch",
                )
            )


def validate_join_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    stage_schema_ids = {
        str(record["id"]): frozenset(
            text_tuple(record.get("artifact_schema_ids", ()))
        )
        for record in records(source, "stage_kinds")
        if is_non_empty_text(record.get("id"))
    }
    schema_property_ids = _artifact_schema_property_ids(source)
    generated_route_count_by_stage = _generated_route_count_by_stage(source)
    terminal_action_ids = indexes["terminal_actions"].ids
    for index, record in enumerate(records(source, "join_declarations")):
        referrer_path = f"join_declarations[{index}]"
        join_id = str(record.get("id", ""))
        for field_name in sorted(frozenset(record) - JOIN_DECLARATION_FIELDS):
            diagnostics.append(
                compiler_error(
                    code="unknown_join_declaration_field",
                    declaration_path=f"{referrer_path}.{field_name}",
                    message="Join declaration contains an unsupported field.",
                    context={"referrer_path": referrer_path, "field_name": field_name},
                    hint="Use only fields declared by the join declaration contract.",
                )
            )
        for field_name in sorted(JOIN_DECLARATION_FIELDS - frozenset(record)):
            diagnostics.append(
                compiler_error(
                    code="missing_join_declaration_field",
                    declaration_path=f"{referrer_path}.{field_name}",
                    message="Join declaration is missing required authority.",
                    context={"referrer_path": referrer_path, "field_name": field_name},
                    hint="Declare every required join declaration field.",
                )
            )
        if join_id in terminal_action_ids:
            diagnostics.append(
                _invalid_join_declaration_diagnostic(
                    declaration_path=f"{referrer_path}.id",
                    referrer_path=referrer_path,
                    join_id=join_id,
                    reason="id_collision",
                )
            )

        _validate_single_reference(
            raw_value=record.get("target_stage_kind_id"),
            ids=indexes["stage_kinds"].ids,
            declaration_path=f"{referrer_path}.target_stage_kind_id",
            referrer_path=referrer_path,
            reference_kind="stage_kind",
            diagnostics=diagnostics,
        )
        required_schema_ids = text_tuple(record.get("required_artifact_schema_ids", ()))
        _validate_many_references(
            raw_values=required_schema_ids,
            ids=indexes["artifact_schemas"].ids,
            declaration_path=f"{referrer_path}.required_artifact_schema_ids",
            referrer_path=referrer_path,
            reference_kind="artifact_schema",
            diagnostics=diagnostics,
        )
        _validate_join_values(
            record=record,
            referrer_path=referrer_path,
            join_id=join_id,
            required_schema_ids=required_schema_ids,
            stage_schema_ids=stage_schema_ids,
            schema_property_ids=schema_property_ids,
            generated_route_count_by_stage=generated_route_count_by_stage,
            diagnostics=diagnostics,
        )


def _validate_join_values(
    *,
    record: SourceRecord,
    referrer_path: str,
    join_id: str,
    required_schema_ids: tuple[str, ...],
    stage_schema_ids: Mapping[str, frozenset[str]],
    schema_property_ids: Mapping[str, frozenset[str]],
    generated_route_count_by_stage: Mapping[str, int],
    diagnostics: list[Diagnostic],
) -> None:
    raw_correlation_key = record.get("correlation_key")
    if not _non_blank_text(raw_correlation_key):
        diagnostics.append(
            _invalid_join_declaration_diagnostic(
                declaration_path=f"{referrer_path}.correlation_key",
                referrer_path=referrer_path,
                join_id=join_id,
                reason="invalid_correlation_key",
            )
        )
        correlation_key = None
    else:
        correlation_key = str(raw_correlation_key)
    if record.get("missing_policy") != "wait":
        diagnostics.append(
            _invalid_join_declaration_diagnostic(
                declaration_path=f"{referrer_path}.missing_policy",
                referrer_path=referrer_path,
                join_id=join_id,
                reason="unsupported_missing_policy",
            )
        )
    if not required_schema_ids:
        diagnostics.append(
            _invalid_join_declaration_diagnostic(
                declaration_path=f"{referrer_path}.required_artifact_schema_ids",
                referrer_path=referrer_path,
                join_id=join_id,
                reason="invalid_required_artifact_schema_ids",
            )
        )
        return
    if len(required_schema_ids) != len(set(required_schema_ids)):
        diagnostics.append(
            _invalid_join_declaration_diagnostic(
                declaration_path=f"{referrer_path}.required_artifact_schema_ids",
                referrer_path=referrer_path,
                join_id=join_id,
                reason="duplicate_required_artifact_schema",
            )
        )
    target_stage_id = record.get("target_stage_kind_id")
    if not is_non_empty_text(target_stage_id):
        return
    target_stage_schema_ids = stage_schema_ids.get(str(target_stage_id))
    if target_stage_schema_ids is None:
        return
    if generated_route_count_by_stage.get(str(target_stage_id), 0) != 1:
        diagnostics.append(
            _invalid_join_declaration_diagnostic(
                declaration_path=f"{referrer_path}.target_stage_kind_id",
                referrer_path=referrer_path,
                join_id=join_id,
                reason="target_route_mismatch",
            )
        )
    for schema_id in required_schema_ids:
        if schema_id in target_stage_schema_ids:
            continue
        diagnostics.append(
            _invalid_join_declaration_diagnostic(
                declaration_path=f"{referrer_path}.required_artifact_schema_ids",
                referrer_path=referrer_path,
                join_id=join_id,
                reason="target_stage_schema_mismatch",
            )
        )
        return
    if correlation_key is None:
        return
    for schema_id in required_schema_ids:
        if correlation_key in schema_property_ids.get(schema_id, frozenset()):
            continue
        diagnostics.append(
            _invalid_join_declaration_diagnostic(
                declaration_path=f"{referrer_path}.correlation_key",
                referrer_path=referrer_path,
                join_id=join_id,
                reason="correlation_key_schema_mismatch",
            )
        )
        return


def _generated_route_count_by_stage(source: Mapping[str, object]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for record in records(source, "generated_work_routes"):
        raw_stage_id = record.get("stage_kind_id")
        if is_non_empty_text(raw_stage_id):
            counts[str(raw_stage_id)] = counts.get(str(raw_stage_id), 0) + 1
    return counts


def _artifact_schema_property_ids(
    source: Mapping[str, object],
) -> Mapping[str, frozenset[str]]:
    property_ids: dict[str, frozenset[str]] = {}
    for record in records(source, "artifact_schemas"):
        raw_id = record.get("id")
        if not is_non_empty_text(raw_id):
            continue
        raw_schema = record.get("schema")
        if not isinstance(raw_schema, Mapping):
            continue
        raw_properties = raw_schema.get("properties", {})
        if not isinstance(raw_properties, Mapping):
            continue
        property_ids[str(raw_id)] = frozenset(
            key for key in raw_properties if isinstance(key, str)
        )
    return property_ids


def _invalid_join_declaration_diagnostic(
    *,
    declaration_path: str,
    referrer_path: str,
    join_id: str,
    reason: str,
) -> Diagnostic:
    return compiler_error(
        code="invalid_join_declaration",
        declaration_path=declaration_path,
        message="Join declaration is outside supported selected authority.",
        context={
            "referrer_path": referrer_path,
            "join_id": join_id,
            "reason": reason,
        },
        hint=(
            "Declare a selected join with a target stage, evidence schemas, "
            "and wait policy."
        ),
    )


def validate_completion_remediation_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    actions = {
        str(record.get("id")): record for record in records(source, "terminal_actions")
    }
    policies = {
        str(record.get("id")): record
        for record in records(source, "remediation_policies")
    }
    stage_contracts = _stage_route_contracts(source)
    runner_stage_ids = _runner_stage_ids(source)
    graph_node_ids = _declared_graph_node_ids(source)
    graph_node_stage_pairs = _declared_graph_node_stage_pairs(source)
    for index, record in enumerate(records(source, "remediation_policies")):
        referrer_path = f"remediation_policies[{index}]"
        _validate_remediation_policy_references(
            record=record,
            referrer_path=referrer_path,
            actions=actions,
            stage_contracts=stage_contracts,
            runner_stage_ids=runner_stage_ids,
            graph_node_ids=graph_node_ids,
            graph_node_stage_pairs=graph_node_stage_pairs,
            indexes=indexes,
            diagnostics=diagnostics,
        )
    for index, record in enumerate(records(source, "completion_behaviors")):
        referrer_path = f"completion_behaviors[{index}]"
        _validate_completion_behavior_references(
            record=record,
            referrer_path=referrer_path,
            actions=actions,
            policies=policies,
            stage_contracts=stage_contracts,
            runner_stage_ids=runner_stage_ids,
            graph_node_ids=graph_node_ids,
            graph_node_stage_pairs=graph_node_stage_pairs,
            indexes=indexes,
            diagnostics=diagnostics,
        )


def _validate_completion_behavior_references(
    *,
    record: SourceRecord,
    referrer_path: str,
    actions: Mapping[str, SourceRecord],
    policies: Mapping[str, SourceRecord],
    stage_contracts: Mapping[str, tuple[frozenset[str], str]],
    runner_stage_ids: Mapping[str, frozenset[str]],
    graph_node_ids: frozenset[str],
    graph_node_stage_pairs: frozenset[tuple[str, str]],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    _validate_single_reference(
        raw_value=record.get("target_stage_kind_id"),
        ids=indexes["stage_kinds"].ids,
        declaration_path=f"{referrer_path}.target_stage_kind_id",
        referrer_path=referrer_path,
        reference_kind="stage_kind",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("runner_binding_id"),
        ids=indexes["runner_bindings"].ids,
        declaration_path=f"{referrer_path}.runner_binding_id",
        referrer_path=referrer_path,
        reference_kind="runner_binding",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("request_queue_family_id"),
        ids=indexes["queue_families"].ids,
        declaration_path=f"{referrer_path}.request_queue_family_id",
        referrer_path=referrer_path,
        reference_kind="queue_family",
        diagnostics=diagnostics,
    )
    for field_name in ("pass_action_id", "gap_action_id", "blocked_action_id"):
        _validate_single_reference(
            raw_value=record.get(field_name),
            ids=indexes["terminal_actions"].ids,
            declaration_path=f"{referrer_path}.{field_name}",
            referrer_path=referrer_path,
            reference_kind="terminal_action",
            diagnostics=diagnostics,
        )
    _validate_single_reference(
        raw_value=record.get("verdict_artifact_schema_id"),
        ids=indexes["artifact_schemas"].ids,
        declaration_path=f"{referrer_path}.verdict_artifact_schema_id",
        referrer_path=referrer_path,
        reference_kind="artifact_schema",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("remediation_policy_id"),
        ids=indexes["remediation_policies"].ids,
        declaration_path=f"{referrer_path}.remediation_policy_id",
        referrer_path=referrer_path,
        reference_kind="remediation_policy",
        diagnostics=diagnostics,
    )
    _validate_graph_node_reference(
        raw_value=record.get("target_graph_node_id"),
        graph_node_ids=graph_node_ids,
        declaration_path=f"{referrer_path}.target_graph_node_id",
        referrer_path=referrer_path,
        namespace="completion_behavior",
        diagnostics=diagnostics,
    )
    _validate_completion_behavior_values(record, referrer_path, diagnostics)
    _validate_completion_route_contract(
        record=record,
        referrer_path=referrer_path,
        stage_contracts=stage_contracts,
        runner_stage_ids=runner_stage_ids,
        graph_node_stage_pairs=graph_node_stage_pairs,
        diagnostics=diagnostics,
    )
    _validate_completion_actions(
        record=record,
        referrer_path=referrer_path,
        actions=actions,
        policies=policies,
        diagnostics=diagnostics,
    )


def _validate_remediation_policy_references(
    *,
    record: SourceRecord,
    referrer_path: str,
    actions: Mapping[str, SourceRecord],
    stage_contracts: Mapping[str, tuple[frozenset[str], str]],
    runner_stage_ids: Mapping[str, frozenset[str]],
    graph_node_ids: frozenset[str],
    graph_node_stage_pairs: frozenset[tuple[str, str]],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    _validate_single_reference(
        raw_value=record.get("source_action_id"),
        ids=indexes["terminal_actions"].ids,
        declaration_path=f"{referrer_path}.source_action_id",
        referrer_path=referrer_path,
        reference_kind="terminal_action",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("target_queue_family_id"),
        ids=indexes["queue_families"].ids,
        declaration_path=f"{referrer_path}.target_queue_family_id",
        referrer_path=referrer_path,
        reference_kind="queue_family",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("target_stage_kind_id"),
        ids=indexes["stage_kinds"].ids,
        declaration_path=f"{referrer_path}.target_stage_kind_id",
        referrer_path=referrer_path,
        reference_kind="stage_kind",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("target_runner_binding_id"),
        ids=indexes["runner_bindings"].ids,
        declaration_path=f"{referrer_path}.target_runner_binding_id",
        referrer_path=referrer_path,
        reference_kind="runner_binding",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("payload_schema_id"),
        ids=indexes["artifact_schemas"].ids,
        declaration_path=f"{referrer_path}.payload_schema_id",
        referrer_path=referrer_path,
        reference_kind="artifact_schema",
        diagnostics=diagnostics,
    )
    _validate_graph_node_reference(
        raw_value=record.get("target_graph_node_id"),
        graph_node_ids=graph_node_ids,
        declaration_path=f"{referrer_path}.target_graph_node_id",
        referrer_path=referrer_path,
        namespace="remediation_policy",
        diagnostics=diagnostics,
    )
    _validate_remediation_policy_values(record, referrer_path, diagnostics)
    source_action = actions.get(str(record.get("source_action_id", "")))
    if source_action is not None and source_action.get("kind") != "closure_gap":
        diagnostics.append(
            _remediation_policy_diagnostic(
                declaration_path=f"{referrer_path}.source_action_id",
                referrer_path=referrer_path,
                policy_id=str(record.get("id", "")),
                reason="unsupported_source_action_kind",
            )
        )
    _validate_target_contract(
        record=record,
        referrer_path=referrer_path,
        diagnostic_builder=_remediation_policy_diagnostic,
        id_field_name="policy_id",
        id_value=str(record.get("id", "")),
        stage_field_name="target_stage_kind_id",
        queue_field_name="target_queue_family_id",
        graph_node_field_name="target_graph_node_id",
        runner_field_name="target_runner_binding_id",
        stage_contracts=stage_contracts,
        runner_stage_ids=runner_stage_ids,
        graph_node_stage_pairs=graph_node_stage_pairs,
        diagnostics=diagnostics,
    )


def _validate_completion_behavior_values(
    record: SourceRecord,
    referrer_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    expected_values = {
        "trigger": "backlog_drained",
        "readiness_rule": "no_open_lineage_work",
        "request_kind": "closure_target",
        "target_selector": "active_closure_target",
        "root_source_resolution": "runtime_inventory",
        "evidence_window_policy": "lineage",
        "rubric_policy": "reuse_or_create",
        "blocked_work_policy": "suppress",
    }
    behavior_id = str(record.get("id", ""))
    for field_name, expected_value in expected_values.items():
        if record.get(field_name) == expected_value:
            continue
        diagnostics.append(
            _completion_behavior_diagnostic(
                declaration_path=f"{referrer_path}.{field_name}",
                referrer_path=referrer_path,
                behavior_id=behavior_id,
                reason=f"unsupported_{field_name}",
            )
        )
    if record.get("skip_if_closed") is not True:
        diagnostics.append(
            _completion_behavior_diagnostic(
                declaration_path=f"{referrer_path}.skip_if_closed",
                referrer_path=referrer_path,
                behavior_id=behavior_id,
                reason="unsupported_skip_if_closed",
            )
        )
    root_kinds = text_tuple(record.get("accepted_root_source_kinds", ()))
    if (
        not root_kinds
        or len(root_kinds) != len(set(root_kinds))
        or any(not _non_blank_text(item) for item in root_kinds)
    ):
        diagnostics.append(
            _completion_behavior_diagnostic(
                declaration_path=f"{referrer_path}.accepted_root_source_kinds",
                referrer_path=referrer_path,
                behavior_id=behavior_id,
                reason="invalid_accepted_root_source_kinds",
            )
        )


def _validate_remediation_policy_values(
    record: SourceRecord,
    referrer_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    expected_values = {
        "guidance_source": "source_artifact",
        "dedupe_key": "closure_target_and_source_artifact",
        "duplicate_policy": "refuse",
        "suppression_policy": "suppress_repeated_same_evidence",
    }
    policy_id = str(record.get("id", ""))
    for field_name, expected_value in expected_values.items():
        if record.get(field_name) == expected_value:
            continue
        diagnostics.append(
            _remediation_policy_diagnostic(
                declaration_path=f"{referrer_path}.{field_name}",
                referrer_path=referrer_path,
                policy_id=policy_id,
                reason=f"unsupported_{field_name}",
            )
        )
    if not _non_blank_text(record.get("root_source_kind")):
        diagnostics.append(
            _remediation_policy_diagnostic(
                declaration_path=f"{referrer_path}.root_source_kind",
                referrer_path=referrer_path,
                policy_id=policy_id,
                reason="invalid_root_source_kind",
            )
        )


def _validate_completion_route_contract(
    *,
    record: SourceRecord,
    referrer_path: str,
    stage_contracts: Mapping[str, tuple[frozenset[str], str]],
    runner_stage_ids: Mapping[str, frozenset[str]],
    graph_node_stage_pairs: frozenset[tuple[str, str]],
    diagnostics: list[Diagnostic],
) -> None:
    _validate_target_contract(
        record=record,
        referrer_path=referrer_path,
        diagnostic_builder=_completion_behavior_diagnostic,
        id_field_name="behavior_id",
        id_value=str(record.get("id", "")),
        stage_field_name="target_stage_kind_id",
        queue_field_name="request_queue_family_id",
        graph_node_field_name="target_graph_node_id",
        runner_field_name="runner_binding_id",
        stage_contracts=stage_contracts,
        runner_stage_ids=runner_stage_ids,
        graph_node_stage_pairs=graph_node_stage_pairs,
        diagnostics=diagnostics,
    )


def _validate_completion_actions(
    *,
    record: SourceRecord,
    referrer_path: str,
    actions: Mapping[str, SourceRecord],
    policies: Mapping[str, SourceRecord],
    diagnostics: list[Diagnostic],
) -> None:
    behavior_id = str(record.get("id", ""))
    stage_id = str(record.get("target_stage_kind_id", ""))
    action_fields = (
        ("pass_action_id", frozenset({"close", "complete_work_item"})),
        ("gap_action_id", frozenset({"closure_gap"})),
        ("blocked_action_id", frozenset({"close", "block_work_item"})),
    )
    for field_name, expected_kinds in action_fields:
        action = actions.get(str(record.get(field_name, "")))
        if (
            action is None
            or action.get("stage_kind_id") != stage_id
            or action.get("kind") not in expected_kinds
        ):
            diagnostics.append(
                _completion_behavior_diagnostic(
                    declaration_path=f"{referrer_path}.{field_name}",
                    referrer_path=referrer_path,
                    behavior_id=behavior_id,
                    reason="invalid_action_contract",
                )
            )
    pass_action = actions.get(str(record.get("pass_action_id", "")))
    if (
        pass_action is not None
        and pass_action.get("artifact_schema_id")
        != record.get("verdict_artifact_schema_id")
    ):
        diagnostics.append(
            _completion_behavior_diagnostic(
                declaration_path=f"{referrer_path}.verdict_artifact_schema_id",
                referrer_path=referrer_path,
                behavior_id=behavior_id,
                reason="verdict_action_schema_mismatch",
            )
        )
    policy = policies.get(str(record.get("remediation_policy_id", "")))
    if policy is not None and policy.get("source_action_id") != record.get(
        "gap_action_id"
    ):
        diagnostics.append(
            _completion_behavior_diagnostic(
                declaration_path=f"{referrer_path}.remediation_policy_id",
                referrer_path=referrer_path,
                behavior_id=behavior_id,
                reason="remediation_policy_source_mismatch",
            )
        )


def _validate_target_contract(
    *,
    record: SourceRecord,
    referrer_path: str,
    diagnostic_builder: Callable[..., Diagnostic],
    id_field_name: str,
    id_value: str,
    stage_field_name: str,
    queue_field_name: str,
    graph_node_field_name: str,
    runner_field_name: str,
    stage_contracts: Mapping[str, tuple[frozenset[str], str]],
    runner_stage_ids: Mapping[str, frozenset[str]],
    graph_node_stage_pairs: frozenset[tuple[str, str]],
    diagnostics: list[Diagnostic],
) -> None:
    stage_id = record.get(stage_field_name)
    queue_id = record.get(queue_field_name)
    graph_node_id = record.get(graph_node_field_name)
    runner_id = record.get(runner_field_name)
    if not (
        is_non_empty_text(stage_id)
        and is_non_empty_text(queue_id)
        and is_non_empty_text(graph_node_id)
        and is_non_empty_text(runner_id)
    ):
        return
    stage = str(stage_id)
    queue = str(queue_id)
    node = str(graph_node_id)
    runner = str(runner_id)
    stage_contract = stage_contracts.get(stage)
    if stage_contract is None:
        return
    known_node_stage_pairs = {
        node_stage_pair
        for node_stage_pair in graph_node_stage_pairs
        if node_stage_pair[1] == node
    }
    if known_node_stage_pairs and (stage, node) not in known_node_stage_pairs:
        diagnostics.append(
            diagnostic_builder(
                declaration_path=f"{referrer_path}.{graph_node_field_name}",
                referrer_path=referrer_path,
                **{id_field_name: id_value},
                reason="graph_node_stage_mismatch",
            )
        )
    input_queue_ids, stage_runner_id = stage_contract
    if queue not in input_queue_ids:
        diagnostics.append(
            diagnostic_builder(
                declaration_path=f"{referrer_path}.{queue_field_name}",
                referrer_path=referrer_path,
                **{id_field_name: id_value},
                reason="stage_input_mismatch",
            )
        )
    if runner != stage_runner_id:
        diagnostics.append(
            diagnostic_builder(
                declaration_path=f"{referrer_path}.{runner_field_name}",
                referrer_path=referrer_path,
                **{id_field_name: id_value},
                reason="stage_runner_mismatch",
            )
        )
    if stage not in runner_stage_ids.get(runner, frozenset()):
        diagnostics.append(
            diagnostic_builder(
                declaration_path=f"{referrer_path}.{runner_field_name}",
                referrer_path=referrer_path,
                **{id_field_name: id_value},
                reason="runner_stage_mismatch",
            )
        )


def _validate_graph_node_reference(
    *,
    raw_value: object,
    graph_node_ids: frozenset[str],
    declaration_path: str,
    referrer_path: str,
    namespace: str,
    diagnostics: list[Diagnostic],
) -> None:
    if not is_non_empty_text(raw_value):
        diagnostics.append(
            missing_id_diagnostic(
                declaration_path=declaration_path,
                namespace=namespace,
                field="target_graph_node_id",
            )
        )
        return
    if str(raw_value) not in graph_node_ids:
        diagnostics.append(
            _missing_graph_node_reference_diagnostic(
                declaration_path=declaration_path,
                referrer_path=referrer_path,
                referenced_id=str(raw_value),
                message=f"{namespace} references an unknown graph node.",
            )
        )


def _non_blank_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _completion_behavior_diagnostic(
    *,
    declaration_path: str,
    referrer_path: str,
    behavior_id: str,
    reason: str,
) -> Diagnostic:
    return compiler_error(
        code="invalid_completion_behavior_declaration",
        declaration_path=declaration_path,
        message="Completion behavior is outside supported selected authority.",
        context={
            "referrer_path": referrer_path,
            "behavior_id": behavior_id,
            "reason": reason,
        },
        hint="Declare a supported closure-target completion behavior.",
    )


def _remediation_policy_diagnostic(
    *,
    declaration_path: str,
    referrer_path: str,
    policy_id: str,
    reason: str,
) -> Diagnostic:
    return compiler_error(
        code="invalid_remediation_policy_declaration",
        declaration_path=declaration_path,
        message="Remediation policy is outside supported selected authority.",
        context={
            "referrer_path": referrer_path,
            "policy_id": policy_id,
            "reason": reason,
        },
        hint="Declare a supported closure-gap remediation policy.",
    )


def _validate_fanout_shape(
    record: SourceRecord,
    referrer_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    fanout_id = str(record.get("id", ""))
    item_source_path = record.get("item_source_path")
    if (
        not is_sequence(item_source_path)
        or not item_source_path
        or any(not is_non_empty_text(item) for item in item_source_path)
    ):
        diagnostics.append(
            _fanout_diagnostic(
                declaration_path=f"{referrer_path}.item_source_path",
                referrer_path=referrer_path,
                fanout_id=fanout_id,
                reason="unsupported_item_source_path",
            )
        )
    if not is_non_empty_text(record.get("item_id_key")):
        diagnostics.append(
            _fanout_diagnostic(
                declaration_path=f"{referrer_path}.item_id_key",
                referrer_path=referrer_path,
                fanout_id=fanout_id,
                reason="unsupported_item_id_key",
            )
        )
    mapping = record.get("target_payload_mapping")
    if not isinstance(mapping, Mapping) or not mapping:
        diagnostics.append(
            _fanout_diagnostic(
                declaration_path=f"{referrer_path}.target_payload_mapping",
                referrer_path=referrer_path,
                fanout_id=fanout_id,
                reason="unsupported_target_payload_mapping",
            )
        )
    elif any(
        not is_non_empty_text(key)
        or not is_sequence(value)
        or not value
        or any(not is_non_empty_text(item) for item in value)
        for key, value in mapping.items()
    ):
        diagnostics.append(
            _fanout_diagnostic(
                declaration_path=f"{referrer_path}.target_payload_mapping",
                referrer_path=referrer_path,
                fanout_id=fanout_id,
                reason="unsupported_target_payload_mapping",
            )
        )
    if record.get("duplicate_policy") != "refuse":
        diagnostics.append(
            _fanout_diagnostic(
                declaration_path=f"{referrer_path}.duplicate_policy",
                referrer_path=referrer_path,
                fanout_id=fanout_id,
                reason="unsupported_duplicate_policy",
            )
        )
    if record.get("root_lineage_policy") != "inherit_source_lineage":
        diagnostics.append(
            _fanout_diagnostic(
                declaration_path=f"{referrer_path}.root_lineage_policy",
                referrer_path=referrer_path,
                fanout_id=fanout_id,
                reason="unsupported_root_lineage_policy",
            )
        )
    source_state_policy = record.get("source_state_policy", "source_closed")
    dependency_policy = record.get("dependency_policy")
    if (source_state_policy, dependency_policy) not in {
        ("source_closed", "depends_on_source_work_item"),
        ("accepted_terminal_observation", "none"),
    }:
        diagnostics.append(
            _fanout_diagnostic(
                declaration_path=f"{referrer_path}.dependency_policy",
                referrer_path=referrer_path,
                fanout_id=fanout_id,
                reason="unsupported_dependency_policy",
            )
        )
    if record.get("source_state_policy", "source_closed") not in {
        "source_closed",
        "accepted_terminal_observation",
    }:
        diagnostics.append(
            _fanout_diagnostic(
                declaration_path=f"{referrer_path}.source_state_policy",
                referrer_path=referrer_path,
                fanout_id=fanout_id,
                reason="unsupported_source_state_policy",
            )
        )


def _fanout_diagnostic(
    *,
    declaration_path: str,
    referrer_path: str,
    fanout_id: str,
    reason: str,
    related_declaration_path: str | None = None,
) -> Diagnostic:
    return compiler_error(
        code="invalid_fanout_declaration",
        declaration_path=declaration_path,
        related_declaration_path=related_declaration_path,
        message="Fanout declaration is outside supported selected authority.",
        context={
            "referrer_path": referrer_path,
            "fanout_id": fanout_id,
            "reason": reason,
        },
        hint="Declare a supported artifact fanout over a selected target route.",
    )


def validate_concurrency_policy_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    owner_by_partition: dict[str, str] = {}
    policy_paths_by_partition: dict[str, str] = {}
    coexist_by_partition: dict[str, tuple[str, ...]] = {}
    for index, record in enumerate(records(source, "concurrency_policies")):
        referrer_path = f"concurrency_policies[{index}]"
        partition_id = record.get("partition_id")
        coexist_partition_ids = text_tuple(record.get("coexist_partition_ids", ()))
        _validate_single_reference(
            raw_value=partition_id,
            ids=indexes["partitions"].ids,
            declaration_path=f"{referrer_path}.partition_id",
            referrer_path=referrer_path,
            reference_kind="partition",
            diagnostics=diagnostics,
        )
        _validate_many_references(
            raw_values=coexist_partition_ids,
            ids=indexes["partitions"].ids,
            declaration_path=f"{referrer_path}.coexist_partition_ids",
            referrer_path=referrer_path,
            reference_kind="partition",
            diagnostics=diagnostics,
        )
        if not is_non_empty_text(partition_id):
            continue
        existing = owner_by_partition.get(str(partition_id))
        if existing is not None:
            diagnostics.append(
                compiler_error(
                    code="invalid_concurrency_policy",
                    declaration_path=f"{referrer_path}.partition_id",
                    related_declaration_path=existing,
                    message="Partition has more than one concurrency policy.",
                    context={
                        "referrer_path": referrer_path,
                        "partition_id": str(partition_id),
                        "reason": "duplicate_partition_policy",
                    },
                    hint="Declare at most one concurrency policy per partition.",
                )
            )
            continue
        partition = str(partition_id)
        owner_by_partition[partition] = f"{referrer_path}.partition_id"
        policy_paths_by_partition[partition] = referrer_path
        coexist_by_partition[partition] = coexist_partition_ids
        max_active_runs = record.get("max_active_runs")
        if type(max_active_runs) is int and max_active_runs <= 0:
            diagnostics.append(
                compiler_error(
                    code="invalid_concurrency_policy",
                    declaration_path=f"{referrer_path}.max_active_runs",
                    message="Concurrency policy max_active_runs must be positive.",
                    context={
                        "referrer_path": referrer_path,
                        "partition_id": str(partition_id),
                        "reason": "invalid_max_active_runs",
                    },
                    hint="Use a positive integer max_active_runs value.",
                )
            )
        if partition in coexist_partition_ids:
            diagnostics.append(
                _invalid_concurrency_policy_diagnostic(
                    declaration_path=f"{referrer_path}.coexist_partition_ids",
                    referrer_path=referrer_path,
                    partition_id=partition,
                    reason="self_coexist",
                )
            )
        if len(coexist_partition_ids) != len(set(coexist_partition_ids)):
            diagnostics.append(
                _invalid_concurrency_policy_diagnostic(
                    declaration_path=f"{referrer_path}.coexist_partition_ids",
                    referrer_path=referrer_path,
                    partition_id=partition,
                    reason="duplicate_coexist_partition",
                )
            )

    for partition, peer_partitions in coexist_by_partition.items():
        referrer_path = policy_paths_by_partition[partition]
        for peer_partition in peer_partitions:
            peer_coexist = coexist_by_partition.get(peer_partition)
            if peer_coexist is None or partition in peer_coexist:
                continue
            diagnostics.append(
                _invalid_concurrency_policy_diagnostic(
                    declaration_path=f"{referrer_path}.coexist_partition_ids",
                    referrer_path=referrer_path,
                    partition_id=partition,
                    reason="asymmetric_coexist",
                    peer_partition_id=peer_partition,
                    related_declaration_path=(
                        f"{policy_paths_by_partition[peer_partition]}"
                        ".coexist_partition_ids"
                    ),
                )
            )


def _invalid_concurrency_policy_diagnostic(
    *,
    declaration_path: str,
    referrer_path: str,
    partition_id: str,
    reason: str,
    peer_partition_id: str | None = None,
    related_declaration_path: str | None = None,
) -> Diagnostic:
    context = {
        "referrer_path": referrer_path,
        "partition_id": partition_id,
        "reason": reason,
    }
    if peer_partition_id is not None:
        context["peer_partition_id"] = peer_partition_id
    return compiler_error(
        code="invalid_concurrency_policy",
        declaration_path=declaration_path,
        related_declaration_path=related_declaration_path,
        message="Concurrency policy is outside supported selected authority.",
        context=context,
        hint="Declare each partition coexistence relation symmetrically and once.",
    )


def validate_action_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    graph_node_ids = _declared_graph_node_ids(source)
    for index, record in enumerate(records(source, "terminal_actions")):
        referrer_path = f"terminal_actions[{index}]"
        _validate_single_reference(
            raw_value=record.get("outcome_id"),
            ids=indexes["terminal_outcomes"].ids,
            declaration_path=f"{referrer_path}.outcome_id",
            referrer_path=referrer_path,
            reference_kind="terminal_outcome",
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=record.get("stage_kind_id"),
            ids=indexes["stage_kinds"].ids,
            declaration_path=f"{referrer_path}.stage_kind_id",
            referrer_path=referrer_path,
            reference_kind="stage_kind",
            diagnostics=diagnostics,
        )
        _validate_single_reference(
            raw_value=record.get("target_stage_kind_id"),
            ids=indexes["stage_kinds"].ids,
            declaration_path=f"{referrer_path}.target_stage_kind_id",
            referrer_path=referrer_path,
            reference_kind="stage_kind",
            diagnostics=diagnostics,
            optional=True,
        )
        _validate_single_reference(
            raw_value=record.get("emitted_queue_family_id"),
            ids=indexes["queue_families"].ids,
            declaration_path=f"{referrer_path}.emitted_queue_family_id",
            referrer_path=referrer_path,
            reference_kind="queue_family",
            diagnostics=diagnostics,
            optional=True,
        )
        _validate_single_reference(
            raw_value=record.get("artifact_schema_id"),
            ids=indexes["artifact_schemas"].ids,
            declaration_path=f"{referrer_path}.artifact_schema_id",
            referrer_path=referrer_path,
            reference_kind="artifact_schema",
            diagnostics=diagnostics,
            optional=True,
        )
        _validate_single_reference(
            raw_value=record.get("runner_binding_id"),
            ids=indexes["runner_bindings"].ids,
            declaration_path=f"{referrer_path}.runner_binding_id",
            referrer_path=referrer_path,
            reference_kind="runner_binding",
            diagnostics=diagnostics,
            optional=True,
        )
        _validate_many_references(
            raw_values=record.get("asset_ids", ()),
            ids=indexes["assets"].ids,
            declaration_path=f"{referrer_path}.asset_ids",
            referrer_path=referrer_path,
            reference_kind="asset",
            diagnostics=diagnostics,
        )
        target_graph_node_id = record.get("target_graph_node_id")
        if (
            is_non_empty_text(target_graph_node_id)
            and str(target_graph_node_id) not in graph_node_ids
        ):
            diagnostics.append(
                _missing_graph_node_reference_diagnostic(
                    declaration_path=f"{referrer_path}.target_graph_node_id",
                    referrer_path=referrer_path,
                    referenced_id=str(target_graph_node_id),
                    message="Terminal action references an unknown graph node.",
                )
            )


def validate_effect_declarations(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    terminal_actions = {
        record_id: record
        for record_id, record in (
            (str(record.get("id", "")), record)
            for record in records(source, "terminal_actions")
        )
        if record_id
    }
    seen_terminal_action_ids: dict[str, str] = {}
    for index, record in enumerate(records(source, "effect_declarations")):
        referrer_path = f"effect_declarations[{index}]"
        effect_id = str(record.get("id", ""))
        for field_name in ("target_ref_kind", "target_ref_schema"):
            if not _non_blank_text(record.get(field_name)):
                diagnostics.append(
                    compiler_error(
                        code="invalid_effect_declaration",
                        declaration_path=f"{referrer_path}.{field_name}",
                        message="Effect declaration target refs must be non-empty.",
                        context={
                            "field_name": field_name,
                            "effect_declaration_id": effect_id,
                        },
                        hint="Declare the selected fake/local effect target.",
                    )
                )
        _validate_fake_local_effect_field(
            record=record,
            field_name="provider_ref",
            expected_value="provider.fake_local.workspace",
            referrer_path=referrer_path,
            effect_id=effect_id,
            diagnostics=diagnostics,
        )
        _validate_fake_local_effect_field(
            record=record,
            field_name="capability_policy_ref",
            expected_value="policy.fake_local.no_real_side_effects",
            referrer_path=referrer_path,
            effect_id=effect_id,
            diagnostics=diagnostics,
        )
        real_side_effects_allowed = record.get("real_side_effects_allowed")
        if real_side_effects_allowed is not False:
            diagnostics.append(
                compiler_error(
                    code="invalid_effect_declaration",
                    declaration_path=f"{referrer_path}.real_side_effects_allowed",
                    message="Effect declarations must not allow real side effects.",
                    context={
                        "field_name": "real_side_effects_allowed",
                        "effect_declaration_id": effect_id,
                    },
                    hint="Use fake/local reconciliation evidence in v0.22.",
                )
            )
        statuses = tuple(text_tuple(record.get("allowed_reconciliation_statuses")))
        if statuses != ("applied", "no_op", "refused"):
            diagnostics.append(
                compiler_error(
                    code="invalid_effect_declaration",
                    declaration_path=(
                        f"{referrer_path}.allowed_reconciliation_statuses"
                    ),
                    message=(
                        "Effect declarations must use the supported "
                        "fake/local statuses."
                    ),
                    context={
                        "field_name": "allowed_reconciliation_statuses",
                        "effect_declaration_id": effect_id,
                    },
                    hint="Use exactly applied, no_op, and refused.",
                )
            )
        terminal_action_id = str(record.get("terminal_action_id", ""))
        action = terminal_actions.get(terminal_action_id)
        if action is None:
            diagnostics.append(
                missing_reference_diagnostic(
                    declaration_path=f"{referrer_path}.terminal_action_id",
                    referrer_path=referrer_path,
                    reference_kind="terminal_action",
                    referenced_id=terminal_action_id,
                )
            )
            continue
        previous_effect_id = seen_terminal_action_ids.get(terminal_action_id)
        if previous_effect_id is not None:
            diagnostics.append(
                _invalid_effect_declaration_diagnostic(
                    referrer_path=referrer_path,
                    field_name="terminal_action_id",
                    effect_id=effect_id,
                    terminal_action_id=terminal_action_id,
                    message=(
                        "Effect declarations must be unique per terminal "
                        "action."
                    ),
                )
            )
        else:
            seen_terminal_action_ids[terminal_action_id] = effect_id
        if str(action.get("kind", "")) not in {"close", "complete_work_item"}:
            diagnostics.append(
                _invalid_effect_declaration_diagnostic(
                    referrer_path=referrer_path,
                    field_name="terminal_action_id",
                    effect_id=effect_id,
                    terminal_action_id=terminal_action_id,
                    message=(
                        "Effect declarations must attach to closing terminal "
                        "actions."
                    ),
                )
            )
        artifact_schema_id = str(record.get("artifact_schema_id", ""))
        if artifact_schema_id not in indexes["artifact_schemas"].ids:
            diagnostics.append(
                missing_reference_diagnostic(
                    declaration_path=f"{referrer_path}.artifact_schema_id",
                    referrer_path=referrer_path,
                    reference_kind="artifact_schema",
                    referenced_id=artifact_schema_id,
                )
            )
        if str(action.get("artifact_schema_id", "")) != artifact_schema_id:
            diagnostics.append(
                _invalid_effect_declaration_diagnostic(
                    referrer_path=referrer_path,
                    field_name="terminal_action_id",
                    effect_id=effect_id,
                    terminal_action_id=terminal_action_id,
                    message=(
                        "Effect declaration terminal action must match the "
                        "declared effect artifact schema."
                    ),
                )
            )
            diagnostics.append(
                _invalid_effect_declaration_diagnostic(
                    referrer_path=referrer_path,
                    field_name="artifact_schema_id",
                    effect_id=effect_id,
                    terminal_action_id=terminal_action_id,
                    message=(
                        "Effect declaration artifact schema must match the "
                        "terminal action artifact schema."
                    ),
                )
            )


def _validate_fake_local_effect_field(
    *,
    record: SourceRecord,
    field_name: str,
    expected_value: str,
    referrer_path: str,
    effect_id: str,
    diagnostics: list[Diagnostic],
) -> None:
    if record.get(field_name) == expected_value:
        return
    diagnostics.append(
        compiler_error(
            code="invalid_effect_declaration",
            declaration_path=f"{referrer_path}.{field_name}",
            message="Effect declaration field is not supported by v0.22.",
            context={
                "field_name": field_name,
                "effect_declaration_id": effect_id,
            },
            hint="Use the fake/local no-real-side-effect policy.",
        )
    )


def _invalid_effect_declaration_diagnostic(
    *,
    referrer_path: str,
    field_name: str,
    effect_id: str,
    terminal_action_id: str,
    message: str,
) -> Diagnostic:
    return compiler_error(
        code="invalid_effect_declaration",
        declaration_path=f"{referrer_path}.{field_name}",
        message=message,
        context={
            "field_name": field_name,
            "effect_declaration_id": effect_id,
            "terminal_action_id": terminal_action_id,
        },
        hint="Keep effect authority aligned with the selected terminal action.",
    )


def validate_runner_binding_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    capability_kinds = {
        str(record["id"]): str(record.get("kind", ""))
        for record in records(source, "capabilities")
        if is_non_empty_text(record.get("id"))
    }
    for index, record in enumerate(records(source, "runner_bindings")):
        referrer_path = f"runner_bindings[{index}]"
        _validate_many_references(
            raw_values=record.get("stage_kind_ids", ()),
            ids=indexes["stage_kinds"].ids,
            declaration_path=f"{referrer_path}.stage_kind_ids",
            referrer_path=referrer_path,
            reference_kind="stage_kind",
            diagnostics=diagnostics,
        )
        _validate_many_references(
            raw_values=record.get("required_capability_ids", ()),
            ids=indexes["capabilities"].ids,
            declaration_path=f"{referrer_path}.required_capability_ids",
            referrer_path=referrer_path,
            reference_kind="capability",
            diagnostics=diagnostics,
        )
        _validate_runner_binding_requires_runner_invoke(
            record=record,
            referrer_path=referrer_path,
            capability_kinds=capability_kinds,
            diagnostics=diagnostics,
        )
        _validate_runner_component_authority(
            source=source,
            record=record,
            referrer_path=referrer_path,
            diagnostics=diagnostics,
        )


def _validate_runner_component_authority(
    *,
    source: Mapping[str, object],
    record: SourceRecord,
    referrer_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    raw_pin = record.get("component_pin")
    raw_mappings = record.get("terminal_result_mappings", ())
    if raw_pin is None:
        if is_sequence(raw_mappings) and raw_mappings:
            _append_runner_component_error(
                diagnostics,
                code="runner_terminal_mapping_without_component",
                declaration_path=f"{referrer_path}.terminal_result_mappings",
                message="Terminal result mappings require a component pin.",
            )
        elif "terminal_result_mappings" in record and not is_sequence(raw_mappings):
            _append_runner_component_error(
                diagnostics,
                code="invalid_runner_terminal_result_mapping",
                declaration_path=f"{referrer_path}.terminal_result_mappings",
                message="Terminal result mappings must be an array.",
            )
        return
    if not isinstance(raw_pin, Mapping) or set(raw_pin) != _RUNNER_COMPONENT_PIN_FIELDS:
        _append_runner_component_error(
            diagnostics,
            code="invalid_runner_component_pin",
            declaration_path=f"{referrer_path}.component_pin",
            message="Runner component pin must contain exactly the selected fields.",
        )
        return

    for field_name in (
        "component_kind",
        "component_id",
        "component_version",
        "provider_distribution",
        "provider_version",
        "descriptor_media_type",
    ):
        value = raw_pin[field_name]
        if isinstance(value, str) and value.strip():
            continue
        _append_runner_component_error(
            diagnostics,
            code="invalid_runner_component_pin",
            declaration_path=f"{referrer_path}.component_pin.{field_name}",
            message="Runner component identity fields must be nonblank strings.",
        )

    descriptor_sha256 = raw_pin["descriptor_sha256"]
    if not (
        isinstance(descriptor_sha256, str)
        and len(descriptor_sha256) == 64
        and all(character in "0123456789abcdef" for character in descriptor_sha256)
    ):
        _append_runner_component_error(
            diagnostics,
            code="invalid_runner_component_descriptor_digest",
            declaration_path=f"{referrer_path}.component_pin.descriptor_sha256",
            message=(
                "Runner component descriptor digest must be 64 lowercase "
                "hexadecimal characters."
            ),
        )

    component_capability_ids = _validated_component_text_sequence(
        raw_pin["required_capability_ids"],
        declaration_path=f"{referrer_path}.component_pin.required_capability_ids",
        invalid_code="invalid_runner_component_pin",
        duplicate_code="duplicate_runner_component_capability",
        diagnostics=diagnostics,
    )
    legal_result_ids = _validated_component_text_sequence(
        raw_pin["legal_terminal_result_ids"],
        declaration_path=f"{referrer_path}.component_pin.legal_terminal_result_ids",
        invalid_code="invalid_runner_component_pin",
        duplicate_code="duplicate_runner_terminal_result",
        diagnostics=diagnostics,
    )
    binding_capability_ids = set(
        text_tuple(record.get("required_capability_ids", ()))
    )
    for capability_id in component_capability_ids or ():
        if capability_id in binding_capability_ids:
            continue
        _append_runner_component_error(
            diagnostics,
            code="runner_component_capability_not_required",
            declaration_path=f"{referrer_path}.component_pin.required_capability_ids",
            message=(
                "Every component capability must be required by the runner binding."
            ),
        )
        break

    if not is_sequence(raw_mappings):
        _append_runner_component_error(
            diagnostics,
            code="invalid_runner_terminal_result_mapping",
            declaration_path=f"{referrer_path}.terminal_result_mappings",
            message="Terminal result mappings must be an array.",
        )
        return

    binding_id = str(record.get("id", ""))
    binding_stage_ids = set(text_tuple(record.get("stage_kind_ids", ())))
    stage_by_id = {
        str(stage.get("id")): stage
        for stage in records(source, "stage_kinds")
        if is_non_empty_text(stage.get("id"))
    }
    outcome_by_id = {
        str(outcome.get("id")): outcome
        for outcome in records(source, "terminal_outcomes")
        if is_non_empty_text(outcome.get("id"))
    }
    seen_mapping_keys: dict[tuple[str, str], str] = {}
    seen_mapping_outcomes: dict[tuple[str, str], str] = {}
    for index, raw_mapping in enumerate(raw_mappings):
        mapping_path = f"{referrer_path}.terminal_result_mappings[{index}]"
        if not isinstance(raw_mapping, Mapping) or set(
            raw_mapping
        ) != _RUNNER_TERMINAL_RESULT_MAPPING_FIELDS:
            _append_runner_component_error(
                diagnostics,
                code="invalid_runner_terminal_result_mapping",
                declaration_path=mapping_path,
                message=(
                    "Runner terminal result mapping must contain exactly the "
                    "selected fields."
                ),
            )
            continue
        if any(
            not isinstance(raw_mapping[field_name], str)
            or not str(raw_mapping[field_name]).strip()
            for field_name in _RUNNER_TERMINAL_RESULT_MAPPING_FIELDS
        ):
            _append_runner_component_error(
                diagnostics,
                code="invalid_runner_terminal_result_mapping",
                declaration_path=mapping_path,
                message="Runner terminal result mapping fields must be nonblank.",
            )
            continue
        stage_kind_id = str(raw_mapping["stage_kind_id"])
        runner_result_id = str(raw_mapping["runner_result_id"])
        outcome_id = str(raw_mapping["outcome_id"])
        mapping_key = (stage_kind_id, runner_result_id)
        previous_path = seen_mapping_keys.get(mapping_key)
        if previous_path is not None:
            diagnostics.append(
                compiler_error(
                    code="duplicate_runner_terminal_result_mapping",
                    declaration_path=mapping_path,
                    related_declaration_path=previous_path,
                    message="Runner terminal result mapping key is duplicated.",
                    context={
                        "stage_kind_id": stage_kind_id,
                        "runner_result_id": runner_result_id,
                    },
                    hint="Declare each stage/result mapping at most once.",
                )
            )
            continue
        seen_mapping_keys[mapping_key] = mapping_path
        outcome_key = (stage_kind_id, outcome_id)
        previous_outcome_path = seen_mapping_outcomes.get(outcome_key)
        if previous_outcome_path is not None:
            diagnostics.append(
                compiler_error(
                    code="duplicate_runner_terminal_result_outcome",
                    declaration_path=f"{mapping_path}.outcome_id",
                    related_declaration_path=(
                        f"{previous_outcome_path}.outcome_id"
                    ),
                    message=(
                        "Runner terminal outcome is mapped from multiple results."
                    ),
                    context={
                        "stage_kind_id": stage_kind_id,
                        "outcome_id": outcome_id,
                    },
                    hint="Map each stage outcome from at most one runner result.",
                )
            )
            continue
        seen_mapping_outcomes[outcome_key] = mapping_path
        if legal_result_ids is not None and runner_result_id not in legal_result_ids:
            _append_runner_component_error(
                diagnostics,
                code="unknown_runner_terminal_result",
                declaration_path=f"{mapping_path}.runner_result_id",
                message="Mapping result is not legal for the selected component.",
            )
        if stage_kind_id not in binding_stage_ids:
            _append_runner_component_error(
                diagnostics,
                code="runner_terminal_mapping_stage_not_owned",
                declaration_path=f"{mapping_path}.stage_kind_id",
                message="Mapping stage is not owned by the runner binding.",
            )
            continue
        stage = stage_by_id.get(stage_kind_id)
        if stage is None or str(stage.get("runner_binding_id", "")) != binding_id:
            _append_runner_component_error(
                diagnostics,
                code="runner_terminal_mapping_stage_not_owned",
                declaration_path=f"{mapping_path}.stage_kind_id",
                message="Mapping stage is not owned by the runner binding.",
            )
            continue
        outcome = outcome_by_id.get(outcome_id)
        if outcome is None:
            _append_runner_component_error(
                diagnostics,
                code="runner_terminal_mapping_outcome_missing",
                declaration_path=f"{mapping_path}.outcome_id",
                message="Mapping outcome does not exist.",
            )
            continue
        if str(outcome.get("stage_kind_id", "")) != stage_kind_id:
            _append_runner_component_error(
                diagnostics,
                code="runner_terminal_mapping_outcome_stage_mismatch",
                declaration_path=f"{mapping_path}.outcome_id",
                message="Mapping outcome belongs to another stage.",
            )
            continue
        if outcome_id not in text_tuple(stage.get("declared_outcome_ids", ())):
            _append_runner_component_error(
                diagnostics,
                code="runner_terminal_mapping_outcome_not_declared",
                declaration_path=f"{mapping_path}.outcome_id",
                message="Mapping outcome is not declared by the stage.",
            )


def _validated_component_text_sequence(
    value: object,
    *,
    declaration_path: str,
    invalid_code: str,
    duplicate_code: str,
    diagnostics: list[Diagnostic],
) -> tuple[str, ...] | None:
    if not is_sequence(value) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        _append_runner_component_error(
            diagnostics,
            code=invalid_code,
            declaration_path=declaration_path,
            message="Runner component collection must contain nonblank strings.",
        )
        return None
    values = tuple(str(item) for item in value)
    if len(set(values)) != len(values):
        _append_runner_component_error(
            diagnostics,
            code=duplicate_code,
            declaration_path=declaration_path,
            message="Runner component collection values must be unique.",
        )
    return values


def _append_runner_component_error(
    diagnostics: list[Diagnostic],
    *,
    code: str,
    declaration_path: str,
    message: str,
) -> None:
    diagnostics.append(
        compiler_error(
            code=code,
            declaration_path=declaration_path,
            message=message,
            context={"referrer_path": declaration_path.split(".", 1)[0]},
            hint="Correct the selected runner component authority.",
        )
    )


def _validate_runner_binding_requires_runner_invoke(
    *,
    record: SourceRecord,
    referrer_path: str,
    capability_kinds: Mapping[str, str],
    diagnostics: list[Diagnostic],
) -> None:
    if not capability_kinds and "required_capability_ids" not in record:
        return
    required_capability_ids = text_tuple(record.get("required_capability_ids", ()))
    valid_required_capability_ids = tuple(
        capability_id
        for capability_id in required_capability_ids
        if capability_id in capability_kinds
    )
    if not required_capability_ids:
        diagnostics.append(
            compiler_error(
                code="runner_binding_missing_runner_invoke",
                declaration_path=f"{referrer_path}.required_capability_ids",
                message="Runner binding does not require runner.invoke.",
                context={
                    "referrer_path": referrer_path,
                    "required_capability_kind": "runner.invoke",
                },
                hint="Declare a selected runner.invoke capability for dispatch.",
            )
        )
        return
    if not valid_required_capability_ids:
        return
    if any(
        capability_kinds[capability_id] not in SUPPORTED_CAPABILITY_KINDS
        for capability_id in valid_required_capability_ids
    ):
        return
    if all(
        capability_kinds[capability_id] != "runner.invoke"
        for capability_id in valid_required_capability_ids
    ):
        diagnostics.append(
            compiler_error(
                code="runner_binding_missing_runner_invoke",
                declaration_path=f"{referrer_path}.required_capability_ids",
                message="Runner binding does not require runner.invoke.",
                context={
                    "referrer_path": referrer_path,
                    "required_capability_kind": "runner.invoke",
                },
                hint="Declare a selected runner.invoke capability for dispatch.",
            )
        )


def validate_capability_values(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    for index, record in enumerate(records(source, "capabilities")):
        referrer_path = f"capabilities[{index}]"
        capability_id = str(record.get("id", ""))
        _validate_capability_field(
            raw_value=record.get("kind"),
            allowed_values=SUPPORTED_CAPABILITY_KINDS,
            declaration_path=f"{referrer_path}.kind",
            capability_id=capability_id,
            field_name="kind",
            diagnostics=diagnostics,
        )
        _validate_capability_field(
            raw_value=record.get("support_status"),
            allowed_values=SUPPORTED_CAPABILITY_SUPPORT_STATUSES,
            declaration_path=f"{referrer_path}.support_status",
            capability_id=capability_id,
            field_name="support_status",
            diagnostics=diagnostics,
        )
        _validate_capability_field(
            raw_value=record.get("grant_status"),
            allowed_values=SUPPORTED_CAPABILITY_GRANT_STATUSES,
            declaration_path=f"{referrer_path}.grant_status",
            capability_id=capability_id,
            field_name="grant_status",
            diagnostics=diagnostics,
        )
        approval_policy_id = record.get("approval_policy_id")
        if is_non_empty_text(approval_policy_id):
            diagnostics.append(
                compiler_error(
                    code="unsupported_capability_value",
                    declaration_path=f"{referrer_path}.approval_policy_id",
                    message="Capability approval policy is unsupported.",
                    context={
                        "capability_id": capability_id,
                        "field_name": "approval_policy_id",
                        "value": str(approval_policy_id),
                    },
                    hint=(
                        "Leave approval_policy_id null until approval policy "
                        "authority exists."
                    ),
                )
            )


def _validate_capability_field(
    *,
    raw_value: object,
    allowed_values: frozenset[str],
    declaration_path: str,
    capability_id: str,
    field_name: str,
    diagnostics: list[Diagnostic],
) -> None:
    if not is_non_empty_text(raw_value) or str(raw_value) not in allowed_values:
        diagnostics.append(
            compiler_error(
                code="unsupported_capability_value",
                declaration_path=declaration_path,
                message="Capability declaration value is unsupported.",
                context={
                    "capability_id": capability_id,
                    "field_name": field_name,
                    "value": str(raw_value) if raw_value is not None else None,
                    "supported_values": tuple(sorted(allowed_values)),
                },
                hint="Use a supported capability declaration value.",
            )
        )


def validate_recovery_policy_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    actions = {
        str(record["id"]): record
        for record in records(source, "terminal_actions")
        if is_non_empty_text(record.get("id"))
    }
    counters_by_increment_action: dict[str, SourceRecord] = {}
    for counter in records(source, "counters"):
        if (
            is_non_empty_text(counter.get("increment_action_id"))
            and is_non_empty_text(counter.get("threshold_action_id"))
        ):
            counters_by_increment_action.setdefault(
                str(counter["increment_action_id"]),
                counter,
            )
    threshold_action_ids = frozenset(
        str(record["threshold_action_id"])
        for record in records(source, "counters")
        if is_non_empty_text(record.get("threshold_action_id"))
    )
    counter_ids_by_threshold_action = {
        str(record["threshold_action_id"]): str(record["id"])
        for record in records(source, "counters")
        if is_non_empty_text(record.get("id"))
        and is_non_empty_text(record.get("threshold_action_id"))
    }
    for index, record in enumerate(records(source, "recovery_policies")):
        referrer_path = f"recovery_policies[{index}]"
        recovery_stage_id = str(record.get("recovery_stage_kind_id", ""))
        _validate_single_reference(
            raw_value=record.get("recovery_stage_kind_id"),
            ids=indexes["stage_kinds"].ids,
            declaration_path=f"{referrer_path}.recovery_stage_kind_id",
            referrer_path=referrer_path,
            reference_kind="stage_kind",
            diagnostics=diagnostics,
        )
        _validate_recovery_policy_action_list(
            record=record,
            field_name="source_recovery_action_ids",
            expected_action_kind="recovery_route",
            referrer_path=referrer_path,
            actions=actions,
            counters_by_increment_action=counters_by_increment_action,
            counter_ids_by_threshold_action=counter_ids_by_threshold_action,
            threshold_action_ids=threshold_action_ids,
            diagnostics=diagnostics,
            recovery_stage_id=recovery_stage_id,
        )
        _validate_recovery_policy_action_list(
            record=record,
            field_name="return_action_ids",
            expected_action_kind="return_to_recorded_source",
            referrer_path=referrer_path,
            actions=actions,
            counters_by_increment_action={},
            counter_ids_by_threshold_action={},
            threshold_action_ids=frozenset(),
            diagnostics=diagnostics,
            recovery_stage_id=recovery_stage_id,
        )
        _validate_recovery_policy_action_list(
            record=record,
            field_name="quarantine_action_ids",
            expected_action_kind="quarantine_lineage",
            referrer_path=referrer_path,
            actions=actions,
            counters_by_increment_action={},
            counter_ids_by_threshold_action={},
            threshold_action_ids=frozenset(),
            diagnostics=diagnostics,
            recovery_stage_id=recovery_stage_id,
        )
        _validate_recovery_policy_reset_triggers(
            record=record,
            referrer_path=referrer_path,
            action_ids=indexes["terminal_actions"].ids,
            diagnostics=diagnostics,
        )
        _validate_recovery_policy_values(record, referrer_path, diagnostics)


def validate_wait_state_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    wait_ids = indexes["wait_states"].ids
    for index, record in enumerate(records(source, "wait_states")):
        referrer_path = f"wait_states[{index}]"
        _validate_single_reference(
            raw_value=record.get("policy_id"),
            ids=indexes["recovery_policies"].ids,
            declaration_path=f"{referrer_path}.policy_id",
            referrer_path=referrer_path,
            reference_kind="recovery_policy",
            diagnostics=diagnostics,
        )
        wait_kind = record.get("kind")
        if wait_kind != "timer":
            diagnostics.append(
                compiler_error(
                    code="unsupported_wait_state_value",
                    declaration_path=f"{referrer_path}.kind",
                    message="Wait state kind is unsupported.",
                    context={
                        "referrer_path": referrer_path,
                        "field_name": "kind",
                        "value": str(wait_kind or ""),
                    },
                    hint="Use the timer wait state kind.",
                )
            )
        for field_name in ("starts_at_attempt", "duration_seconds"):
            value = _policy_int(record.get(field_name))
            if value is not None and value > 0:
                continue
            diagnostics.append(
                compiler_error(
                    code="invalid_wait_state_value",
                    declaration_path=f"{referrer_path}.{field_name}",
                    message="Wait state numeric authority must be positive.",
                    context={
                        "referrer_path": referrer_path,
                        "field_name": field_name,
                        "value": value,
                    },
                    hint="Declare positive wait attempt and duration values.",
                )
            )
    for index, record in enumerate(records(source, "recovery_policies")):
        referrer_path = f"recovery_policies[{index}]"
        wait_state_id = record.get("cooldown_wait_state_id")
        _validate_single_reference(
            raw_value=wait_state_id,
            ids=wait_ids,
            declaration_path=f"{referrer_path}.cooldown_wait_state_id",
            referrer_path=referrer_path,
            reference_kind="wait_state",
            diagnostics=diagnostics,
        )
        if not is_non_empty_text(wait_state_id):
            continue
        wait = next(
            (
                wait_record
                for wait_record in records(source, "wait_states")
                if wait_record.get("id") == wait_state_id
            ),
            None,
        )
        if wait is None:
            continue
        if wait.get("policy_id") != record.get("id"):
            diagnostics.append(
                compiler_error(
                    code="wait_state_policy_mismatch",
                    declaration_path=f"{referrer_path}.cooldown_wait_state_id",
                    message="Recovery policy wait state references a different policy.",
                    context={
                        "referrer_path": referrer_path,
                        "wait_state_id": str(wait_state_id),
                    },
                    hint="Reference a wait state declared for this recovery policy.",
                )
            )


def validate_counter_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    actions = {
        str(record["id"]): record
        for record in records(source, "terminal_actions")
        if is_non_empty_text(record.get("id"))
    }
    policy_sources_by_recovery_stage: dict[str, set[str]] = {}
    for policy in records(source, "recovery_policies"):
        recovery_stage_id = policy.get("recovery_stage_kind_id")
        if not is_non_empty_text(recovery_stage_id):
            continue
        policy_sources_by_recovery_stage.setdefault(
            str(recovery_stage_id),
            set(),
        ).update(text_tuple(policy.get("source_recovery_action_ids", ())))
    counter_action_owner: dict[str, tuple[str, str, str]] = {}
    for index, record in enumerate(records(source, "counters")):
        referrer_path = f"counters[{index}]"
        counter_id = (
            str(record["id"]) if is_non_empty_text(record.get("id")) else ""
        )
        counter_kind = record.get("kind")
        if counter_kind != "lineage_terminal_action_counter":
            diagnostics.append(
                compiler_error(
                    code="unsupported_counter_value",
                    declaration_path=f"{referrer_path}.kind",
                    message="Counter kind is unsupported.",
                    context={
                        "referrer_path": referrer_path,
                        "field_name": "kind",
                        "value": str(counter_kind or ""),
                    },
                    hint="Use lineage_terminal_action_counter.",
                )
            )
        if record.get("scope") != "lineage":
            diagnostics.append(
                compiler_error(
                    code="unsupported_counter_value",
                    declaration_path=f"{referrer_path}.scope",
                    message="Counter scope is unsupported.",
                    context={
                        "referrer_path": referrer_path,
                        "field_name": "scope",
                        "value": str(record.get("scope") or ""),
                    },
                    hint="Use lineage counter scope.",
                )
            )
        _validate_single_reference(
            raw_value=record.get("stage_kind_id"),
            ids=indexes["stage_kinds"].ids,
            declaration_path=f"{referrer_path}.stage_kind_id",
            referrer_path=referrer_path,
            reference_kind="stage_kind",
            diagnostics=diagnostics,
        )
        for field_name in ("increment_action_id", "threshold_action_id"):
            _validate_single_reference(
                raw_value=record.get(field_name),
                ids=indexes["terminal_actions"].ids,
                declaration_path=f"{referrer_path}.{field_name}",
                referrer_path=referrer_path,
                reference_kind="terminal_action",
                diagnostics=diagnostics,
            )
            raw_action_id = record.get(field_name)
            if not is_non_empty_text(raw_action_id):
                continue
            action_id = str(raw_action_id)
            existing_owner = counter_action_owner.get(action_id)
            if existing_owner is not None and existing_owner[0] != counter_id:
                diagnostics.append(
                    compiler_error(
                        code="duplicate_counter_action",
                        declaration_path=f"{referrer_path}.{field_name}",
                        message="Counter action is already owned by another counter.",
                        context={
                            "referrer_path": referrer_path,
                            "counter_id": counter_id,
                            "field_name": field_name,
                            "action_id": action_id,
                            "existing_counter_id": existing_owner[0],
                            "existing_field_name": existing_owner[1],
                            "existing_declaration_path": existing_owner[2],
                        },
                        hint=(
                            "Each terminal action may be owned by only one "
                            "selected counter."
                        ),
                    )
                )
            counter_action_owner.setdefault(
                action_id,
                (counter_id, field_name, f"{referrer_path}.{field_name}"),
            )
        threshold = _policy_int(record.get("threshold_count"))
        if threshold is None or threshold <= 1:
            diagnostics.append(
                compiler_error(
                    code="invalid_counter_value",
                    declaration_path=f"{referrer_path}.threshold_count",
                    message="Counter threshold must be greater than one.",
                    context={
                        "referrer_path": referrer_path,
                        "field_name": "threshold_count",
                        "value": threshold,
                    },
                    hint="Declare the escalation threshold as a positive count.",
                )
        )
        stage_id = record.get("stage_kind_id")
        for field_name in ("increment_action_id", "threshold_action_id"):
            counter_action_id = record.get(field_name)
            action = (
                actions.get(str(counter_action_id))
                if is_non_empty_text(counter_action_id)
                else None
            )
            if action is None or not is_non_empty_text(stage_id):
                continue
            if action.get("stage_kind_id") != stage_id:
                diagnostics.append(
                    compiler_error(
                        code="counter_action_stage_mismatch",
                        declaration_path=f"{referrer_path}.{field_name}",
                        message="Counter action must belong to the counter stage.",
                        context={
                            "referrer_path": referrer_path,
                            "stage_kind_id": str(stage_id),
                            "action_id": str(counter_action_id),
                            "action_stage_kind_id": str(
                                action.get("stage_kind_id", "")
                            ),
                        },
                        hint=(
                            "Reference terminal actions declared on the counter "
                            "stage."
                        ),
                    )
                )
        increment_action_id = record.get("increment_action_id")
        threshold_action_id = record.get("threshold_action_id")
        threshold_action = (
            actions.get(str(threshold_action_id))
            if is_non_empty_text(threshold_action_id)
            else None
        )
        if (
            is_non_empty_text(increment_action_id)
            and threshold_action is not None
            and threshold_action.get("kind") == "recovery_route"
        ):
            recovery_stage_id = threshold_action.get("target_stage_kind_id")
            if (
                not is_non_empty_text(recovery_stage_id)
                or str(increment_action_id)
                not in policy_sources_by_recovery_stage.get(
                    str(recovery_stage_id),
                    set(),
                )
            ):
                diagnostics.append(
                    compiler_error(
                        code="counter_recovery_policy_source_missing",
                        declaration_path=f"{referrer_path}.increment_action_id",
                        message=(
                            "Recovery threshold counter increment action is not "
                            "listed by a matching recovery policy."
                        ),
                        context={
                            "referrer_path": referrer_path,
                            "counter_id": counter_id,
                            "increment_action_id": str(increment_action_id),
                            "threshold_action_id": str(threshold_action_id),
                            "recovery_stage_kind_id": str(recovery_stage_id or ""),
                        },
                        hint=(
                            "List the ordinary counter increment action in the "
                            "source_recovery_action_ids of the policy whose "
                            "recovery stage matches the threshold action."
                        ),
                    )
                )


def validate_lineage_policy_references(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    if "lineage_policy" not in source:
        diagnostics.append(
            compiler_error(
                code="missing_lineage_policy",
                declaration_path="lineage_policy",
                message="Workflow source must declare a lineage policy.",
                context={},
                hint="Use root_from_external_enqueue or none.",
            )
        )
        return
    lineage_policy = source.get("lineage_policy")
    if lineage_policy not in {"root_from_external_enqueue", "none"}:
        diagnostics.append(
            compiler_error(
                code="unsupported_lineage_policy",
                declaration_path="lineage_policy",
                message="Lineage policy is unsupported.",
                context={"lineage_policy": str(lineage_policy or "")},
                hint="Use root_from_external_enqueue or none.",
            )
        )
        return
    if lineage_policy != "none":
        return
    for selected_key in (
        "recovery_policies",
        "intervention_options",
        "operator_waits",
        "counters",
    ):
        if not records(source, selected_key):
            continue
        diagnostics.append(
            compiler_error(
                code="lineage_policy_conflict",
                declaration_path=selected_key,
                message=(
                    "Lineage-dependent authority cannot be selected without "
                    "lineage."
                ),
                context={
                    "lineage_policy": "none",
                    "selected_key": selected_key,
                },
                hint="Remove lineage-dependent authority or use lineage policy.",
            )
        )


def validate_intervention_option_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    for index, record in enumerate(records(source, "intervention_options")):
        referrer_path = f"intervention_options[{index}]"
        for field_name in sorted(frozenset(record) - INTERVENTION_OPTION_FIELDS):
            diagnostics.append(
                compiler_error(
                    code="unknown_intervention_option_field",
                    declaration_path=f"{referrer_path}.{field_name}",
                    message="Intervention option contains an unsupported field.",
                    context={
                        "referrer_path": referrer_path,
                        "field_name": field_name,
                    },
                    hint=(
                        "Use only fields declared by the intervention option "
                        "contract."
                    ),
                )
            )
        for field_name in _missing_intervention_option_fields(record):
            diagnostics.append(
                compiler_error(
                    code="missing_intervention_option_field",
                    declaration_path=f"{referrer_path}.{field_name}",
                    message="Intervention option is missing required authority.",
                    context={
                        "referrer_path": referrer_path,
                        "field_name": field_name,
                    },
                    hint="Declare every required intervention option field.",
                )
            )
        _validate_single_reference(
            raw_value=record.get("policy_id"),
            ids=indexes["recovery_policies"].ids,
            declaration_path=f"{referrer_path}.policy_id",
            referrer_path=referrer_path,
            reference_kind="recovery_policy",
            diagnostics=diagnostics,
        )
        _validate_intervention_option_revise_references(
            record,
            referrer_path,
            source,
            indexes,
            diagnostics,
        )
        _validate_intervention_option_values(record, referrer_path, diagnostics)


def _missing_intervention_option_fields(record: SourceRecord) -> tuple[str, ...]:
    required = {
        "id",
        "policy_id",
        "kind",
        "legal_source_state",
        "target_selector",
        "supersede_behavior",
        "attempt_effect",
        "audit_metadata_requirements",
    }
    option_kind = record.get("kind")
    if option_kind == "revise_lineage":
        required.update(
            {
                "payload_schema_id",
                "target_queue_family_id",
                "target_stage_kind_id",
                "target_graph_node_id",
                "target_runner_binding_id",
                "resume_target_selector",
                "close_behavior",
            }
        )
    elif option_kind != "close_lineage":
        required.add("resume_target_selector")
    if option_kind == "close_lineage":
        required.add("close_behavior")
    nullable_fields = (
        {"resume_target_selector", "close_behavior"}
        if option_kind == "revise_lineage"
        else set()
    )
    return tuple(
        sorted(
            field_name
            for field_name in required
            if field_name not in record
            or (field_name not in nullable_fields and record.get(field_name) is None)
        )
    )


def _validate_intervention_option_values(
    record: SourceRecord,
    referrer_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    option_id = str(record.get("id", ""))
    option_kind = str(record.get("kind", ""))
    if option_kind not in {"resume_lineage", "close_lineage", "revise_lineage"}:
        diagnostics.append(
            compiler_error(
                code="unsupported_intervention_option_kind",
                declaration_path=f"{referrer_path}.kind",
                message="Intervention option kind is unsupported.",
                context={
                    "referrer_path": referrer_path,
                    "option_id": option_id,
                    "option_kind": option_kind,
                },
                hint="Use resume_lineage, close_lineage, or revise_lineage.",
            )
        )
        return

    expected_values = {
        "legal_source_state": "active_lineage_quarantine",
        "target_selector": "selected_quarantine_or_active_quarantine_by_lineage",
        "supersede_behavior": "supersede_quarantine",
        "attempt_effect": "resolve_attempt",
        "actor_kind": "local_operator",
    }
    for field_name, expected_value in expected_values.items():
        default_value = "local_operator" if field_name == "actor_kind" else None
        value = record.get(field_name, default_value)
        if value == expected_value:
            continue
        _invalid_intervention_option_field(
            diagnostics,
            referrer_path=referrer_path,
            field_name=field_name,
            value=str(value or ""),
            option_id=option_id,
        )

    resume_target = record.get("resume_target_selector")
    close_behavior = record.get("close_behavior")
    if option_kind == "resume_lineage":
        if resume_target != "recorded_source":
            _invalid_intervention_option_field(
                diagnostics,
                referrer_path=referrer_path,
                field_name="resume_target_selector",
                value=str(resume_target or ""),
                option_id=option_id,
            )
        if close_behavior is not None:
            _invalid_intervention_option_field(
                diagnostics,
                referrer_path=referrer_path,
                field_name="close_behavior",
                value=str(close_behavior),
                option_id=option_id,
            )
    if option_kind == "close_lineage":
        if resume_target is not None:
            _invalid_intervention_option_field(
                diagnostics,
                referrer_path=referrer_path,
                field_name="resume_target_selector",
                value=str(resume_target),
                option_id=option_id,
            )
        if close_behavior != "close_ready_or_active_work_in_lineage":
            _invalid_intervention_option_field(
                diagnostics,
                referrer_path=referrer_path,
                field_name="close_behavior",
                value=str(close_behavior or ""),
                option_id=option_id,
            )

    if option_kind == "revise_lineage":
        if resume_target is not None:
            _invalid_intervention_option_field(
                diagnostics,
                referrer_path=referrer_path,
                field_name="resume_target_selector",
                value=str(resume_target),
                option_id=option_id,
            )
        if close_behavior is not None:
            _invalid_intervention_option_field(
                diagnostics,
                referrer_path=referrer_path,
                field_name="close_behavior",
                value=str(close_behavior),
                option_id=option_id,
            )

    requirements = text_tuple(record.get("audit_metadata_requirements", ()))
    expected_requirements = _expected_intervention_audit_requirements(option_kind)
    if requirements != expected_requirements:
        _invalid_intervention_option_field(
            diagnostics,
            referrer_path=referrer_path,
            field_name="audit_metadata_requirements",
            value=",".join(requirements),
            option_id=option_id,
        )


def _validate_intervention_option_revise_references(
    record: SourceRecord,
    referrer_path: str,
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    if record.get("kind") != "revise_lineage":
        return
    _validate_single_reference(
        raw_value=record.get("payload_schema_id"),
        ids=indexes["artifact_schemas"].ids,
        declaration_path=f"{referrer_path}.payload_schema_id",
        referrer_path=referrer_path,
        reference_kind="artifact_schema",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("target_queue_family_id"),
        ids=indexes["queue_families"].ids,
        declaration_path=f"{referrer_path}.target_queue_family_id",
        referrer_path=referrer_path,
        reference_kind="queue_family",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("target_stage_kind_id"),
        ids=indexes["stage_kinds"].ids,
        declaration_path=f"{referrer_path}.target_stage_kind_id",
        referrer_path=referrer_path,
        reference_kind="stage_kind",
        diagnostics=diagnostics,
    )
    _validate_single_reference(
        raw_value=record.get("target_runner_binding_id"),
        ids=indexes["runner_bindings"].ids,
        declaration_path=f"{referrer_path}.target_runner_binding_id",
        referrer_path=referrer_path,
        reference_kind="runner_binding",
        diagnostics=diagnostics,
    )
    target_graph_node_id = record.get("target_graph_node_id")
    if not is_non_empty_text(target_graph_node_id):
        diagnostics.append(
            missing_id_diagnostic(
                declaration_path=f"{referrer_path}.target_graph_node_id",
                namespace="intervention_option",
                field="target_graph_node_id",
            )
        )
    elif str(target_graph_node_id) not in _declared_graph_node_ids(source):
        diagnostics.append(
            _missing_graph_node_reference_diagnostic(
                declaration_path=f"{referrer_path}.target_graph_node_id",
                referrer_path=referrer_path,
                referenced_id=str(target_graph_node_id),
                message="Intervention option references an unknown graph node.",
            )
        )
    payload_schema_id = record.get("payload_schema_id")
    selected_artifact_schema_ids = _selected_artifact_schema_ids(source)
    if (
        is_non_empty_text(payload_schema_id)
        and str(payload_schema_id) in indexes["artifact_schemas"].ids
        and str(payload_schema_id) not in selected_artifact_schema_ids
    ):
        diagnostics.append(
            compiler_error(
                code="unselected_intervention_payload_schema_reference",
                declaration_path=f"{referrer_path}.payload_schema_id",
                message="Revise intervention payload schema is not selected authority.",
                context={
                    "referrer_path": referrer_path,
                    "reference_kind": "artifact_schema",
                    "referenced_id": str(payload_schema_id),
                },
                hint=(
                    "Use a payload schema already selected by stage or "
                    "terminal-action authority."
                ),
            )
        )
    _validate_revise_target_contract(record, referrer_path, source, diagnostics)


def _validate_revise_target_contract(
    record: SourceRecord,
    referrer_path: str,
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    target_stage_kind_id = record.get("target_stage_kind_id")
    target_queue_family_id = record.get("target_queue_family_id")
    target_graph_node_id = record.get("target_graph_node_id")
    target_runner_binding_id = record.get("target_runner_binding_id")
    if not (
        is_non_empty_text(target_stage_kind_id)
        and is_non_empty_text(target_queue_family_id)
        and is_non_empty_text(target_graph_node_id)
        and is_non_empty_text(target_runner_binding_id)
    ):
        return
    stage_contracts = _stage_route_contracts(source)
    runner_stage_ids = _runner_stage_ids(source)
    stage_id = str(target_stage_kind_id)
    queue_id = str(target_queue_family_id)
    graph_node_id = str(target_graph_node_id) if is_non_empty_text(
        target_graph_node_id
    ) else ""
    runner_id = str(target_runner_binding_id)
    if stage_id not in stage_contracts:
        return
    if (
        graph_node_id
        and (stage_id, graph_node_id) not in _declared_graph_node_stage_pairs(source)
    ):
        diagnostics.append(
            compiler_error(
                code="intervention_target_graph_node_stage_mismatch",
                declaration_path=f"{referrer_path}.target_graph_node_id",
                message=(
                    "Revise target graph node does not belong to the target stage."
                ),
                context={
                    "referrer_path": referrer_path,
                    "stage_kind_id": stage_id,
                    "target_graph_node_id": graph_node_id,
                },
                hint=(
                    "Use a graph node declared with the same target stage by "
                    "an external route or terminal action."
                ),
            )
        )
    target_tuple = (queue_id, stage_id, graph_node_id, runner_id)
    if target_tuple not in _declared_route_target_tuples(source):
        diagnostics.append(
            compiler_error(
                code="intervention_target_route_mismatch",
                declaration_path=f"{referrer_path}.target_graph_node_id",
                message="Revise target does not match a declared route target.",
                context={
                    "referrer_path": referrer_path,
                    "queue_family_id": queue_id,
                    "stage_kind_id": stage_id,
                    "target_graph_node_id": graph_node_id,
                    "runner_binding_id": runner_id,
                },
                hint=(
                    "Use a queue, stage, graph-node, and runner tuple declared "
                    "by an external route or terminal action."
                ),
            )
        )
    else:
        target_payload_schema_ids = _declared_route_target_payload_schema_ids(
            source,
            queue_family_id=queue_id,
            stage_kind_id=stage_id,
            graph_node_id=graph_node_id,
            runner_binding_id=runner_id,
        )
        payload_schema_id = record.get("payload_schema_id")
        if (
            target_payload_schema_ids
            and is_non_empty_text(payload_schema_id)
            and str(payload_schema_id) not in target_payload_schema_ids
        ):
            diagnostics.append(
                compiler_error(
                    code="intervention_target_payload_schema_mismatch",
                    declaration_path=f"{referrer_path}.payload_schema_id",
                    message=(
                        "Revise payload schema does not match a selected target "
                        "route payload schema."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "payload_schema_id": str(payload_schema_id),
                        "target_payload_schema_id": sorted(
                            target_payload_schema_ids
                        )[0],
                        "queue_family_id": queue_id,
                        "stage_kind_id": stage_id,
                        "target_graph_node_id": graph_node_id,
                        "runner_binding_id": runner_id,
                    },
                    hint=(
                        "Use the payload schema selected by the declared target "
                        "route."
                    ),
                )
            )
    stage_input_queue_ids, stage_runner_id = stage_contracts[stage_id]
    if queue_id not in stage_input_queue_ids:
        diagnostics.append(
            compiler_error(
                code="intervention_target_stage_input_mismatch",
                declaration_path=f"{referrer_path}.target_queue_family_id",
                message=(
                    "Revise target queue family is not an input for the "
                    "target stage."
                ),
                context={
                    "referrer_path": referrer_path,
                    "queue_family_id": queue_id,
                    "stage_kind_id": stage_id,
                },
                hint=(
                    "Route revise work only to a stage that declares the target "
                    "queue family as an input."
                ),
            )
        )
    if runner_id != stage_runner_id:
        diagnostics.append(
            compiler_error(
                code="intervention_target_stage_runner_mismatch",
                declaration_path=f"{referrer_path}.target_runner_binding_id",
                message="Revise target runner binding does not match the target stage.",
                context={
                    "referrer_path": referrer_path,
                    "stage_kind_id": stage_id,
                    "target_runner_binding_id": runner_id,
                    "stage_runner_binding_id": stage_runner_id,
                },
                hint="Use the runner binding declared by the target stage.",
            )
        )
    if stage_id not in runner_stage_ids.get(runner_id, frozenset()):
        diagnostics.append(
            compiler_error(
                code="intervention_target_runner_stage_mismatch",
                declaration_path=f"{referrer_path}.target_runner_binding_id",
                message="Revise target runner binding does not list the target stage.",
                context={
                    "referrer_path": referrer_path,
                    "stage_kind_id": stage_id,
                    "runner_binding_id": runner_id,
                },
                hint="Use a runner binding that can run the target stage.",
            )
        )


def _expected_intervention_audit_requirements(
    option_kind: str,
) -> tuple[str, ...]:
    if option_kind == "resume_lineage":
        return _RESUME_INTERVENTION_AUDIT_REQUIREMENTS
    if option_kind == "close_lineage":
        return _CLOSE_INTERVENTION_AUDIT_REQUIREMENTS
    if option_kind == "revise_lineage":
        return _REVISE_INTERVENTION_AUDIT_REQUIREMENTS
    return ()


def _invalid_intervention_option_field(
    diagnostics: list[Diagnostic],
    *,
    referrer_path: str,
    field_name: str,
    value: str,
    option_id: str,
) -> None:
    diagnostics.append(
        compiler_error(
            code="invalid_intervention_option_field",
            declaration_path=f"{referrer_path}.{field_name}",
            message="Intervention option field value is unsupported.",
            context={
                "referrer_path": referrer_path,
                "option_id": option_id,
                "field_name": field_name,
                "value": value,
            },
            hint="Use the intervention option values supported by this runtime.",
        )
    )


def _validate_recovery_policy_action_list(
    *,
    record: SourceRecord,
    field_name: str,
    expected_action_kind: str,
    referrer_path: str,
    actions: Mapping[str, SourceRecord],
    counters_by_increment_action: Mapping[str, SourceRecord],
    counter_ids_by_threshold_action: Mapping[str, str],
    threshold_action_ids: frozenset[str],
    diagnostics: list[Diagnostic],
    recovery_stage_id: str,
) -> None:
    for item_index, action_id in enumerate(text_tuple(record.get(field_name, ()))):
        declaration_path = f"{referrer_path}.{field_name}[{item_index}]"
        action = actions.get(action_id)
        if action is None:
            diagnostics.append(
                missing_reference_diagnostic(
                    declaration_path=declaration_path,
                    referrer_path=referrer_path,
                    reference_kind="terminal_action",
                    referenced_id=action_id,
                )
            )
            continue
        action_kind = str(action.get("kind", ""))
        if (
            expected_action_kind == "recovery_route"
            and action_id in threshold_action_ids
        ):
            diagnostics.append(
                compiler_error(
                    code="recovery_policy_threshold_action_source",
                    declaration_path=declaration_path,
                    message=(
                        "Recovery policy references a counter threshold action "
                        "instead of its ordinary source action."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "reference_kind": "terminal_action",
                        "referenced_id": action_id,
                        "counter_id": counter_ids_by_threshold_action.get(
                            action_id,
                            "",
                        ),
                        "action_kind": action_kind,
                        "expected_action_kind": (
                            "recovery_route_or_counter_increment"
                        ),
                    },
                    hint=(
                        "Reference the selected counter increment action; the "
                        "runtime applies the threshold recovery action."
                    ),
                )
            )
            continue
        if (
            expected_action_kind == "recovery_route"
            and action_kind != "recovery_route"
        ):
            counter = counters_by_increment_action.get(action_id)
            threshold_action_id = (
                str(counter.get("threshold_action_id", ""))
                if counter is not None
                else ""
            )
            threshold_action = actions.get(threshold_action_id)
            if (
                counter is not None
                and threshold_action is not None
                and threshold_action.get("kind") == "recovery_route"
                and str(threshold_action.get("target_stage_kind_id", ""))
                == recovery_stage_id
            ):
                continue
            diagnostics.append(
                compiler_error(
                    code="invalid_recovery_policy_action_kind",
                    declaration_path=declaration_path,
                    message=(
                        "Recovery policy references a terminal action with the "
                        "wrong kind."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "reference_kind": "terminal_action",
                        "referenced_id": action_id,
                        "action_kind": action_kind,
                        "expected_action_kind": (
                            "recovery_route_or_counter_increment"
                        ),
                    },
                    hint=(
                        "Reference a recovery route or a selected counter "
                        "increment whose threshold action is a recovery route."
                    ),
                )
            )
            continue
        if action_kind != expected_action_kind:
            diagnostics.append(
                compiler_error(
                    code="invalid_recovery_policy_action_kind",
                    declaration_path=declaration_path,
                    message=(
                        "Recovery policy references a terminal action with the "
                        "wrong kind."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "reference_kind": "terminal_action",
                        "referenced_id": action_id,
                        "action_kind": action_kind,
                        "expected_action_kind": expected_action_kind,
                    },
                    hint="Reference only terminal actions with the expected kind.",
                )
            )
            continue
        if expected_action_kind == "recovery_route":
            target_stage_id = str(action.get("target_stage_kind_id", ""))
            if target_stage_id == recovery_stage_id:
                continue
            diagnostics.append(
                compiler_error(
                    code="recovery_policy_source_target_mismatch",
                    declaration_path=declaration_path,
                    message=(
                        "Recovery policy source action targets a different "
                        "recovery stage."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "reference_kind": "terminal_action",
                        "referenced_id": action_id,
                        "recovery_stage_kind_id": recovery_stage_id,
                        "target_stage_kind_id": target_stage_id,
                    },
                    hint="Use source recovery actions that target the policy stage.",
                )
            )
            continue
        action_stage_id = str(action.get("stage_kind_id", ""))
        if action_stage_id == recovery_stage_id:
            continue
        diagnostics.append(
            compiler_error(
                code="recovery_policy_action_stage_mismatch",
                declaration_path=declaration_path,
                message=(
                    "Recovery policy action belongs to a different recovery stage."
                ),
                context={
                    "referrer_path": referrer_path,
                    "reference_kind": "terminal_action",
                    "referenced_id": action_id,
                    "recovery_stage_kind_id": recovery_stage_id,
                    "stage_kind_id": action_stage_id,
                },
                hint="Use return/quarantine actions declared by the policy stage.",
            )
        )


def _validate_recovery_policy_reset_triggers(
    *,
    record: SourceRecord,
    referrer_path: str,
    action_ids: frozenset[str],
    diagnostics: list[Diagnostic],
) -> None:
    seen_paths_by_id: dict[str, str] = {}
    for item_index, action_id in enumerate(
        text_tuple(record.get("reset_trigger_action_ids", ()))
    ):
        declaration_path = f"{referrer_path}.reset_trigger_action_ids[{item_index}]"
        existing_path = seen_paths_by_id.get(action_id)
        if existing_path is not None:
            diagnostics.append(
                compiler_error(
                    code="duplicate_recovery_policy_reset_trigger",
                    declaration_path=declaration_path,
                    related_declaration_path=existing_path,
                    message="Recovery policy reset trigger is duplicated.",
                    context={
                        "referrer_path": referrer_path,
                        "reference_kind": "terminal_action",
                        "referenced_id": action_id,
                    },
                    hint="List each reset trigger action at most once.",
                )
            )
            continue
        seen_paths_by_id[action_id] = declaration_path
        if action_id in action_ids:
            continue
        diagnostics.append(
            missing_reference_diagnostic(
                declaration_path=declaration_path,
                referrer_path=referrer_path,
                reference_kind="terminal_action",
                referenced_id=action_id,
            )
        )


def _validate_recovery_policy_values(
    record: SourceRecord,
    referrer_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    expected_values = {
        "attempt_scope": "lineage",
        "recorded_source_selector": "latest_recovery_attempt_for_lineage",
        "threshold_behavior": "runtime_quarantine_at_threshold",
    }
    for field_name, expected_value in expected_values.items():
        value = record.get(field_name)
        if value == expected_value:
            continue
        diagnostics.append(
            _unsupported_recovery_policy_value(
                referrer_path=referrer_path,
                field_name=field_name,
                value=str(value or ""),
            )
        )

    return_allowed_phases = text_tuple(record.get("return_allowed_phases", ()))
    if not set(return_allowed_phases).issubset(
        {"active_recovery", "quarantine_eligible"}
    ):
        diagnostics.append(
            _unsupported_recovery_policy_value(
                referrer_path=referrer_path,
                field_name="return_allowed_phases",
                value=",".join(return_allowed_phases),
            )
        )

    immediate = _policy_int(record.get("immediate_recovery_limit"))
    cooldown = _policy_int(record.get("cooldown_starts_at_attempt"))
    quarantine = _policy_int(record.get("quarantine_threshold_attempt"))
    default_cooldown = _policy_int(record.get("default_cooldown_seconds"))
    threshold_values = (
        ("immediate_recovery_limit", immediate),
        ("cooldown_starts_at_attempt", cooldown),
        ("quarantine_threshold_attempt", quarantine),
        ("default_cooldown_seconds", default_cooldown),
    )
    for field_name, value in threshold_values:
        if value is not None and value > 0:
            continue
        diagnostics.append(
            _invalid_recovery_policy_threshold(
                referrer_path=referrer_path,
                field_name=field_name,
                value=value,
            )
        )
    if cooldown is None or quarantine is None:
        return
    if quarantine >= cooldown:
        return
    diagnostics.append(
        _invalid_recovery_policy_threshold(
            referrer_path=referrer_path,
            field_name="quarantine_threshold_attempt",
            value=quarantine,
        )
    )


def _policy_int(value: object) -> int | None:
    return value if type(value) is int else None


def _unsupported_recovery_policy_value(
    *,
    referrer_path: str,
    field_name: str,
    value: str,
) -> Diagnostic:
    return compiler_error(
        code="unsupported_recovery_policy_value",
        declaration_path=f"{referrer_path}.{field_name}",
        message="Recovery policy field value is not supported.",
        context={
            "referrer_path": referrer_path,
            "reference_kind": "recovery_policy",
            "field_name": field_name,
            "value": value,
        },
        hint="Use one of the recovery policy values supported by this slice.",
    )


def _invalid_recovery_policy_threshold(
    *,
    referrer_path: str,
    field_name: str,
    value: int | None,
) -> Diagnostic:
    return compiler_error(
        code="invalid_recovery_policy_threshold",
        declaration_path=f"{referrer_path}.{field_name}",
        message="Recovery policy threshold is not valid.",
        context={
            "referrer_path": referrer_path,
            "reference_kind": "recovery_policy",
            "field_name": field_name,
            "value": "" if value is None else str(value),
        },
        hint="Use positive, ordered recovery attempt thresholds.",
    )


def validate_declared_outcomes_have_actions(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    action_keys = {
        (str(action.get("stage_kind_id")), str(action.get("outcome_id")))
        for action in records(source, "terminal_actions")
        if is_non_empty_text(action.get("stage_kind_id"))
        and is_non_empty_text(action.get("outcome_id"))
    }
    for index, outcome in enumerate(records(source, "terminal_outcomes")):
        stage_kind_id = outcome.get("stage_kind_id")
        outcome_id = outcome.get("id")
        if not is_non_empty_text(stage_kind_id) or not is_non_empty_text(outcome_id):
            continue
        key = (str(stage_kind_id), str(outcome_id))
        if key not in action_keys:
            diagnostics.append(
                outcome_without_action_diagnostic(
                    declaration_path=f"terminal_outcomes[{index}].id",
                    stage_kind_id=key[0],
                    outcome_id=key[1],
                )
            )


def validate_action_outcomes_belong_to_action_stage(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    outcome_stage_kind_ids = {
        str(outcome.get("id")): str(outcome.get("stage_kind_id"))
        for outcome in records(source, "terminal_outcomes")
        if is_non_empty_text(outcome.get("id"))
        and is_non_empty_text(outcome.get("stage_kind_id"))
    }
    for action_index, action in enumerate(records(source, "terminal_actions")):
        stage_kind_id = action.get("stage_kind_id")
        outcome_id = action.get("outcome_id")
        if not is_non_empty_text(stage_kind_id) or not is_non_empty_text(outcome_id):
            continue
        outcome_stage_kind_id = outcome_stage_kind_ids.get(str(outcome_id))
        if outcome_stage_kind_id is None:
            continue
        if outcome_stage_kind_id != stage_kind_id:
            diagnostics.append(
                outcome_stage_mismatch_diagnostic(
                    declaration_path=f"terminal_actions[{action_index}].outcome_id",
                    stage_kind_id=str(stage_kind_id),
                    outcome_id=str(outcome_id),
                    outcome_stage_kind_id=outcome_stage_kind_id,
                )
            )


def validate_declared_outcomes_belong_to_stage(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    outcome_stage_kind_ids = {
        str(outcome.get("id")): str(outcome.get("stage_kind_id"))
        for outcome in records(source, "terminal_outcomes")
        if is_non_empty_text(outcome.get("id"))
        and is_non_empty_text(outcome.get("stage_kind_id"))
    }
    for stage_index, stage in enumerate(records(source, "stage_kinds")):
        stage_kind_id = stage.get("id")
        if not is_non_empty_text(stage_kind_id):
            continue
        for outcome_index, outcome_id in enumerate(
            text_tuple(stage.get("declared_outcome_ids", ()))
        ):
            outcome_stage_kind_id = outcome_stage_kind_ids.get(outcome_id)
            if outcome_stage_kind_id is None:
                continue
            if outcome_stage_kind_id != stage_kind_id:
                diagnostics.append(
                    outcome_stage_mismatch_diagnostic(
                        declaration_path=(
                            "stage_kinds"
                            f"[{stage_index}].declared_outcome_ids[{outcome_index}]"
                        ),
                        stage_kind_id=str(stage_kind_id),
                        outcome_id=outcome_id,
                        outcome_stage_kind_id=outcome_stage_kind_id,
                    )
                )


def _queue_family_external_flags(source: Mapping[str, object]) -> Mapping[str, bool]:
    return {
        str(record["id"]): record.get("external_enqueue") is True
        for record in records(source, "queue_families")
        if is_non_empty_text(record.get("id"))
    }


def _stage_route_contracts(
    source: Mapping[str, object],
) -> Mapping[str, tuple[frozenset[str], str]]:
    return {
        str(record["id"]): (
            frozenset(text_tuple(record.get("input_queue_family_ids", ()))),
            str(record.get("runner_binding_id", "")),
        )
        for record in records(source, "stage_kinds")
        if is_non_empty_text(record.get("id"))
    }


def _runner_stage_ids(source: Mapping[str, object]) -> Mapping[str, frozenset[str]]:
    return {
        str(record["id"]): frozenset(text_tuple(record.get("stage_kind_ids", ())))
        for record in records(source, "runner_bindings")
        if is_non_empty_text(record.get("id"))
    }


def _selected_artifact_schema_ids(source: Mapping[str, object]) -> frozenset[str]:
    schema_ids: set[str] = set()
    for record in records(source, "stage_kinds"):
        schema_ids.update(text_tuple(record.get("artifact_schema_ids", ())))
    for record in records(source, "terminal_actions"):
        raw_schema_id = record.get("artifact_schema_id")
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
    return frozenset(schema_ids)


def _declared_graph_node_ids(source: Mapping[str, object]) -> frozenset[str]:
    node_ids: set[str] = set()
    for record in records(source, "graphs"):
        node_ids.update(text_tuple(record.get("node_ids", ())))
    return frozenset(node_ids)


def _missing_graph_node_reference_diagnostic(
    *,
    declaration_path: str,
    referrer_path: str,
    referenced_id: str,
    message: str,
) -> Diagnostic:
    return compiler_error(
        code="missing_reference",
        declaration_path=declaration_path,
        message=message,
        context={
            "referrer_path": referrer_path,
            "reference_kind": "graph_node",
            "referenced_id": referenced_id,
        },
        hint="Reference a graph node declared in graphs[].node_ids.",
    )


def _declared_graph_node_stage_pairs(
    source: Mapping[str, object],
) -> frozenset[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for record in records(source, "external_enqueue_routes"):
        raw_stage_id = record.get("stage_kind_id")
        raw_node_id = record.get("graph_node_id")
        if is_non_empty_text(raw_stage_id) and is_non_empty_text(raw_node_id):
            pairs.add((str(raw_stage_id), str(raw_node_id)))
    for record in records(source, "generated_work_routes"):
        raw_stage_id = record.get("stage_kind_id")
        raw_node_id = record.get("graph_node_id")
        if is_non_empty_text(raw_stage_id) and is_non_empty_text(raw_node_id):
            pairs.add((str(raw_stage_id), str(raw_node_id)))
    for record in records(source, "terminal_actions"):
        raw_stage_id = record.get("target_stage_kind_id")
        raw_node_id = record.get("target_graph_node_id")
        if is_non_empty_text(raw_stage_id) and is_non_empty_text(raw_node_id):
            pairs.add((str(raw_stage_id), str(raw_node_id)))
    return frozenset(pairs)


def _declared_route_target_tuples(
    source: Mapping[str, object],
) -> frozenset[tuple[str, str, str, str]]:
    route_targets: set[tuple[str, str, str, str]] = set()
    for record in records(source, "external_enqueue_routes"):
        _add_route_target_tuple(
            route_targets,
            queue_family_id=record.get("queue_family_id"),
            stage_kind_id=record.get("stage_kind_id"),
            graph_node_id=record.get("graph_node_id"),
            runner_binding_id=record.get("runner_binding_id"),
        )
    for record in records(source, "generated_work_routes"):
        _add_route_target_tuple(
            route_targets,
            queue_family_id=record.get("queue_family_id"),
            stage_kind_id=record.get("stage_kind_id"),
            graph_node_id=record.get("graph_node_id"),
            runner_binding_id=record.get("runner_binding_id"),
        )
    for record in records(source, "terminal_actions"):
        _add_route_target_tuple(
            route_targets,
            queue_family_id=record.get("emitted_queue_family_id"),
            stage_kind_id=record.get("target_stage_kind_id"),
            graph_node_id=record.get("target_graph_node_id"),
            runner_binding_id=record.get("runner_binding_id"),
        )
    return frozenset(route_targets)


def _declared_route_target_payload_schema_ids(
    source: Mapping[str, object],
    *,
    queue_family_id: str,
    stage_kind_id: str,
    graph_node_id: str,
    runner_binding_id: str,
) -> frozenset[str]:
    schema_ids: set[str] = set()
    for record in records(source, "external_enqueue_routes"):
        _add_route_target_payload_schema_id(
            schema_ids,
            record=record,
            queue_family_id=queue_family_id,
            stage_kind_id=stage_kind_id,
            graph_node_id=graph_node_id,
            runner_binding_id=runner_binding_id,
            schema_field_name="payload_schema_id",
        )
    for record in records(source, "generated_work_routes"):
        _add_route_target_payload_schema_id(
            schema_ids,
            record=record,
            queue_family_id=queue_family_id,
            stage_kind_id=stage_kind_id,
            graph_node_id=graph_node_id,
            runner_binding_id=runner_binding_id,
            schema_field_name="payload_schema_id",
        )
    for record in records(source, "terminal_actions"):
        _add_route_target_payload_schema_id(
            schema_ids,
            record=record,
            queue_family_id=queue_family_id,
            stage_kind_id=stage_kind_id,
            graph_node_id=graph_node_id,
            runner_binding_id=runner_binding_id,
            schema_field_name="artifact_schema_id",
            queue_field_name="emitted_queue_family_id",
            stage_field_name="target_stage_kind_id",
            node_field_name="target_graph_node_id",
        )
    return frozenset(schema_ids)


def _add_route_target_payload_schema_id(
    schema_ids: set[str],
    *,
    record: SourceRecord,
    queue_family_id: str,
    stage_kind_id: str,
    graph_node_id: str,
    runner_binding_id: str,
    schema_field_name: str,
    queue_field_name: str = "queue_family_id",
    stage_field_name: str = "stage_kind_id",
    node_field_name: str = "graph_node_id",
) -> None:
    if (
        record.get(queue_field_name) != queue_family_id
        or record.get(stage_field_name) != stage_kind_id
        or record.get(node_field_name) != graph_node_id
        or record.get("runner_binding_id") != runner_binding_id
    ):
        return
    raw_schema_id = record.get(schema_field_name)
    if is_non_empty_text(raw_schema_id):
        schema_ids.add(str(raw_schema_id))


def _add_route_target_tuple(
    route_targets: set[tuple[str, str, str, str]],
    *,
    queue_family_id: object,
    stage_kind_id: object,
    graph_node_id: object,
    runner_binding_id: object,
) -> None:
    if not (
        is_non_empty_text(queue_family_id)
        and is_non_empty_text(stage_kind_id)
        and is_non_empty_text(graph_node_id)
        and is_non_empty_text(runner_binding_id)
    ):
        return
    route_targets.add(
        (
            str(queue_family_id),
            str(stage_kind_id),
            str(graph_node_id),
            str(runner_binding_id),
        )
    )


def _validate_single_reference(
    *,
    raw_value: object,
    ids: frozenset[str],
    declaration_path: str,
    referrer_path: str,
    reference_kind: str,
    diagnostics: list[Diagnostic],
    optional: bool = False,
) -> None:
    if not is_non_empty_text(raw_value):
        if optional and raw_value is None:
            return
        diagnostics.append(
            missing_reference_diagnostic(
                declaration_path=declaration_path,
                referrer_path=referrer_path,
                reference_kind=reference_kind,
                referenced_id="",
            )
        )
        return
    referenced_id = str(raw_value)
    if referenced_id not in ids:
        diagnostics.append(
            missing_reference_diagnostic(
                declaration_path=declaration_path,
                referrer_path=referrer_path,
                reference_kind=reference_kind,
                referenced_id=referenced_id,
            )
        )


def _validate_many_references(
    *,
    raw_values: object,
    ids: frozenset[str],
    declaration_path: str,
    referrer_path: str,
    reference_kind: str,
    diagnostics: list[Diagnostic],
) -> None:
    for index, referenced_id in enumerate(text_tuple(raw_values)):
        if referenced_id not in ids:
            diagnostics.append(
                missing_reference_diagnostic(
                    declaration_path=f"{declaration_path}[{index}]",
                    referrer_path=referrer_path,
                    reference_kind=reference_kind,
                    referenced_id=referenced_id,
                )
            )


__all__ = (
    "COLLECTION_NAMESPACES",
    "IdIndex",
    "collect_id_index",
    "collect_id_indexes",
    "validate_action_outcomes_belong_to_action_stage",
    "validate_action_references",
    "validate_completion_remediation_references",
    "validate_concurrency_policy_references",
    "validate_counter_references",
    "validate_declared_outcomes_belong_to_stage",
    "validate_declared_outcomes_have_actions",
    "validate_external_enqueue_route_references",
    "validate_fanout_references",
    "validate_generated_work_route_references",
    "validate_intervention_option_references",
    "validate_join_references",
    "validate_lineage_policy_references",
    "validate_outcome_references",
    "validate_partition_references",
    "validate_recovery_policy_references",
    "validate_runner_binding_references",
    "validate_stage_references",
    "validate_wait_state_references",
)
