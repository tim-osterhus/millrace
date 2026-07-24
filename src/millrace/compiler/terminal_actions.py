"""Terminal marker and terminal action validation for compiler source.

This module owns terminal ambiguity, action kind, and route contract
diagnostics. It must not apply terminal actions or infer workflow meaning from
markers.
"""

from __future__ import annotations

from collections.abc import Mapping

from millrace.compiler.diagnostics import compiler_error
from millrace.compiler.source import (
    SourceRecord,
    is_non_empty_text,
    is_sequence,
    records,
    text_tuple,
)
from millrace.contracts import Diagnostic
from millrace.contracts.schema import validate_projection_declaration

EXECUTABLE_ROUTE_ACTION_KINDS = frozenset(("route", "create_incident_route"))
_RECOVERY_ROUTE_PROMPT_ASSET_KINDS = frozenset(("prompt", "entrypoint_prompt"))
SUPPORTED_TERMINAL_ACTION_KINDS = frozenset(
    (
        "route",
        "create_incident_route",
        "close",
        "complete_work_item",
        "close_with_escalation",
        "block_work_item",
        "pause_quarantine",
        "recovery_route",
        "return_to_recorded_source",
        "quarantine_lineage",
        "operator_wait",
        "closure_gap",
    )
)


def validate_terminal_outcome_markers_are_unambiguous(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    paths_by_key: dict[tuple[str, str], str] = {}
    for index, record in enumerate(records(source, "terminal_outcomes")):
        stage_kind_id = record.get("stage_kind_id")
        marker = record.get("marker")
        if not is_non_empty_text(stage_kind_id) or not is_non_empty_text(marker):
            continue
        key = (str(stage_kind_id), str(marker))
        path = f"terminal_outcomes[{index}].marker"
        existing_path = paths_by_key.get(key)
        if existing_path is not None:
            diagnostics.append(
                compiler_error(
                    code="ambiguous_terminal_marker",
                    declaration_path=path,
                    related_declaration_path=existing_path,
                    message=(
                        "Terminal outcome marker is ambiguous within a stage kind."
                    ),
                    context={"stage_kind_id": key[0], "marker": key[1]},
                    hint=(
                        "Use a distinct rendered marker for each outcome declared "
                        "by the same stage kind."
                    ),
                )
            )
            continue
        paths_by_key[key] = path


def validate_terminal_actions_are_unambiguous(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    paths_by_key: dict[tuple[str, str], str] = {}
    for index, record in enumerate(records(source, "terminal_actions")):
        stage_kind_id = record.get("stage_kind_id")
        outcome_id = record.get("outcome_id")
        if not is_non_empty_text(stage_kind_id) or not is_non_empty_text(outcome_id):
            continue
        key = (str(stage_kind_id), str(outcome_id))
        path = f"terminal_actions[{index}].outcome_id"
        existing_path = paths_by_key.get(key)
        if existing_path is not None:
            diagnostics.append(
                compiler_error(
                    code="ambiguous_terminal_action",
                    declaration_path=path,
                    related_declaration_path=existing_path,
                    message=("Terminal action is ambiguous for a stage/outcome pair."),
                    context={"stage_kind_id": key[0], "outcome_id": key[1]},
                    hint=(
                        "Declare exactly one terminal action for each stage kind "
                        "and terminal outcome pair."
                    ),
                )
            )
            continue
        paths_by_key[key] = path


def validate_terminal_action_kinds(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    for index, record in enumerate(records(source, "terminal_actions")):
        action_kind = record.get("kind")
        if not is_non_empty_text(action_kind):
            diagnostics.append(
                compiler_error(
                    code="unsupported_terminal_action_kind",
                    declaration_path=f"terminal_actions[{index}].kind",
                    message=(
                        "Terminal action kind is not supported by this compiler slice."
                    ),
                    context={
                        "referrer_path": f"terminal_actions[{index}]",
                        "action_id": str(record.get("id", "")),
                        "action_kind": str(action_kind or ""),
                    },
                    hint=(
                        "Use a terminal action kind declared by the compiled-plan "
                        "contract."
                    ),
                )
            )
            continue
        action_kind = str(action_kind)
        if action_kind in SUPPORTED_TERMINAL_ACTION_KINDS:
            continue
        diagnostics.append(
            compiler_error(
                code="unsupported_terminal_action_kind",
                declaration_path=f"terminal_actions[{index}].kind",
                message="Terminal action kind is not supported by this compiler slice.",
                context={
                    "referrer_path": f"terminal_actions[{index}]",
                    "action_id": str(record.get("id", "")),
                    "action_kind": action_kind,
                },
                hint=(
                    "Use a terminal action kind declared by the compiled-plan "
                    "contract."
                ),
            )
        )


def validate_route_action_contracts(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    stage_contracts = _stage_action_contracts(source)
    runner_stage_ids = _runner_stage_ids(source)
    graph_node_stage_owner = _known_graph_node_stage_owner(source)
    for index, record in enumerate(records(source, "terminal_actions")):
        if record.get("kind") == "close_with_escalation":
            _validate_close_with_escalation_contract(
                record=record,
                action_index=index,
                diagnostics=diagnostics,
            )
            continue
        if record.get("dynamic_target_selector") is not None:
            _validate_dynamic_route_target_selector(
                source=source,
                record=record,
                action_index=index,
                diagnostics=diagnostics,
            )
        if record.get("kind") not in EXECUTABLE_ROUTE_ACTION_KINDS:
            continue
        referrer_path = f"terminal_actions[{index}]"
        action_id = str(record.get("id", ""))
        source_stage_id = record.get("stage_kind_id")
        target_stage_id = record.get("target_stage_kind_id")
        emitted_queue_id = record.get("emitted_queue_family_id")
        artifact_schema_id = record.get("artifact_schema_id")
        runner_binding_id = record.get("runner_binding_id")
        missing_fields = _missing_route_action_fields(record)
        for field_name in missing_fields:
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_route_missing_field",
                    declaration_path=f"{referrer_path}.{field_name}",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message="Route action is missing required executable authority.",
                    context={"field_name": field_name},
                    hint=(
                        "Route actions must declare target stage, target graph node, "
                        "emitted queue family, artifact schema, runner binding, "
                        "and payload projection."
                    ),
                )
            )

        projection = record.get("payload_projection")
        if projection is not None:
            projection_result = validate_projection_declaration(projection)
            for issue in projection_result.issues:
                diagnostics.append(
                    _terminal_route_contract_diagnostic(
                        code="invalid_terminal_projection",
                        declaration_path=f"{referrer_path}.payload_projection",
                        referrer_path=referrer_path,
                        action_id=action_id,
                        message=(
                            "Route action payload projection is outside the "
                            "supported projection subset."
                        ),
                        context={
                            "reason": issue.reason,
                            "detail": issue.detail or "",
                        },
                        hint=(
                            "Use only literal, source, object, and array "
                            "projection declarations with supported source roots."
                        ),
                    )
                )

        if missing_fields:
            continue

        source_stage_key = str(source_stage_id)
        target_stage_key = str(target_stage_id)
        emitted_queue_key = str(emitted_queue_id)
        artifact_schema_key = str(artifact_schema_id)
        runner_binding_key = str(runner_binding_id)
        target_graph_node_key = str(record.get("target_graph_node_id", ""))
        source_contract = stage_contracts.get(source_stage_key)
        target_contract = stage_contracts.get(target_stage_key)
        if source_contract is None or target_contract is None:
            continue

        _, source_outputs, source_schemas, _ = source_contract
        target_inputs, _, target_schemas, target_runner_binding_id = target_contract
        if emitted_queue_key not in source_outputs:
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_route_stage_output_mismatch",
                    declaration_path=f"{referrer_path}.emitted_queue_family_id",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Route action emits a queue family not declared by the "
                        "source stage."
                    ),
                    context={
                        "source_stage_kind_id": source_stage_key,
                        "queue_family_id": emitted_queue_key,
                    },
                    hint="Emit only queue families declared as source stage outputs.",
                )
            )
        if emitted_queue_key not in target_inputs:
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_route_stage_input_mismatch",
                    declaration_path=f"{referrer_path}.target_stage_kind_id",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Route action target stage does not accept the emitted "
                        "queue family."
                    ),
                    context={
                        "target_stage_kind_id": target_stage_key,
                        "queue_family_id": emitted_queue_key,
                    },
                    hint=(
                        "Route only to a target stage that declares the emitted "
                        "queue family as input."
                    ),
                )
            )
        known_stage_for_node = graph_node_stage_owner.get(target_graph_node_key)
        if (
            known_stage_for_node is not None
            and known_stage_for_node != target_stage_key
        ):
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_route_graph_node_stage_mismatch",
                    declaration_path=f"{referrer_path}.target_graph_node_id",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Route action target graph node belongs to a different "
                        "selected stage."
                    ),
                    context={
                        "target_stage_kind_id": target_stage_key,
                        "target_graph_node_id": target_graph_node_key,
                        "graph_node_stage_kind_id": known_stage_for_node,
                    },
                    hint="Use a target graph node selected for the target stage.",
                )
            )
        if (
            artifact_schema_key not in source_schemas
            or artifact_schema_key not in target_schemas
        ):
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_route_artifact_schema_mismatch",
                    declaration_path=f"{referrer_path}.artifact_schema_id",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Route action artifact schema is not declared by both "
                        "source and target stages."
                    ),
                    context={
                        "source_stage_kind_id": source_stage_key,
                        "target_stage_kind_id": target_stage_key,
                        "artifact_schema_id": artifact_schema_key,
                    },
                    hint=(
                        "Route only artifact schemas declared by the producing "
                        "and consuming stages."
                    ),
                )
            )
        if runner_binding_key != target_runner_binding_id:
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_route_stage_runner_mismatch",
                    declaration_path=f"{referrer_path}.runner_binding_id",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Route action runner binding does not match the target "
                        "stage runner binding."
                    ),
                    context={
                        "target_stage_kind_id": target_stage_key,
                        "route_runner_binding_id": runner_binding_key,
                        "stage_runner_binding_id": target_runner_binding_id,
                    },
                    hint="Use the runner binding declared by the target stage.",
                )
            )
        if target_stage_key not in runner_stage_ids.get(
            runner_binding_key,
            frozenset(),
        ):
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_route_runner_stage_mismatch",
                    declaration_path=f"{referrer_path}.runner_binding_id",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Route action runner binding does not list the target stage."
                    ),
                    context={
                        "target_stage_kind_id": target_stage_key,
                        "runner_binding_id": runner_binding_key,
                    },
                    hint=("List the target stage in the referenced runner binding."),
                )
            )

    _validate_recovery_route_action_contracts(source, diagnostics)
    _validate_artifact_action_contracts(source, diagnostics)


def _validate_close_with_escalation_contract(
    *,
    record: SourceRecord,
    action_index: int,
    diagnostics: list[Diagnostic],
) -> None:
    referrer_path = f"terminal_actions[{action_index}]"
    action_id = str(record.get("id", ""))
    for field_name in (
        "target_stage_kind_id",
        "target_graph_node_id",
        "emitted_queue_family_id",
        "runner_binding_id",
        "payload_projection",
        "dynamic_target_selector",
    ):
        if record.get(field_name) is None:
            continue
        diagnostics.append(
            _terminal_route_contract_diagnostic(
                code="terminal_close_with_escalation_route_authority",
                declaration_path=f"{referrer_path}.{field_name}",
                referrer_path=referrer_path,
                action_id=action_id,
                message=(
                    "Close-with-escalation actions must not declare route "
                    "or enqueue authority."
                ),
                context={"field_name": field_name},
                hint=(
                    "Use route or create_incident_route when a terminal action "
                    "must create routed follow-up work."
                ),
            )
        )


def _validate_dynamic_route_target_selector(
    *,
    source: Mapping[str, object],
    record: SourceRecord,
    action_index: int,
    diagnostics: list[Diagnostic],
) -> None:
    referrer_path = f"terminal_actions[{action_index}]"
    selector_path = f"{referrer_path}.dynamic_target_selector"
    action_id = str(record.get("id", ""))
    selector = record.get("dynamic_target_selector")
    if record.get("kind") not in EXECUTABLE_ROUTE_ACTION_KINDS:
        diagnostics.append(
            _terminal_route_contract_diagnostic(
                code="invalid_dynamic_route_selector",
                declaration_path=selector_path,
                referrer_path=referrer_path,
                action_id=action_id,
                message=(
                    "Dynamic route target selectors are only valid on executable "
                    "route actions."
                ),
                context={"reason": "unsupported_action_kind"},
                hint="Move this selector to a route or create_incident_route action.",
            )
        )
        return
    if not isinstance(selector, Mapping):
        diagnostics.append(
            _terminal_route_contract_diagnostic(
                code="invalid_dynamic_route_selector",
                declaration_path=selector_path,
                referrer_path=referrer_path,
                action_id=action_id,
                message="Dynamic route target selector must be a mapping.",
                context={"reason": "expected_mapping"},
                hint=(
                    "Declare kind, nonempty field_names, and nonempty targets for "
                    "dynamic route selection."
                ),
            )
        )
        return
    if selector.get("kind") != "observation_payload_route_target":
        diagnostics.append(
            _terminal_route_contract_diagnostic(
                code="invalid_dynamic_route_selector",
                declaration_path=f"{selector_path}.kind",
                referrer_path=referrer_path,
                action_id=action_id,
                message="Dynamic route target selector kind is unsupported.",
                context={"reason": "unsupported_selector_kind"},
                hint="Use observation_payload_route_target.",
            )
        )
        return
    raw_field_names = selector.get("field_names")
    if (
        not is_sequence(raw_field_names)
        or not raw_field_names
        or any(not is_non_empty_text(field_name) for field_name in raw_field_names)
        or len(set(raw_field_names)) != len(raw_field_names)
    ):
        diagnostics.append(
            _terminal_route_contract_diagnostic(
                code="invalid_dynamic_route_selector",
                declaration_path=f"{selector_path}.field_names",
                referrer_path=referrer_path,
                action_id=action_id,
                message="Dynamic route selector field names must be unique text.",
                context={"reason": "invalid_field_names"},
                hint="Declare one or more unique observation-payload field names.",
            )
        )
        return
    targets = selector.get("targets")
    if not isinstance(targets, Mapping) or not targets:
        diagnostics.append(
            _terminal_route_contract_diagnostic(
                code="invalid_dynamic_route_selector",
                declaration_path=f"{selector_path}.targets",
                referrer_path=referrer_path,
                action_id=action_id,
                message="Dynamic route selector targets must be a nonempty mapping.",
                context={"reason": "invalid_targets"},
                hint="Map each allowed selector value to a complete route target.",
            )
        )
        return

    raw_disallowed_targets = selector.get("disallowed_targets", ())
    if not is_sequence(raw_disallowed_targets) or any(
        not is_non_empty_text(target_name) for target_name in raw_disallowed_targets
    ):
        diagnostics.append(
            _terminal_route_contract_diagnostic(
                code="invalid_dynamic_route_selector",
                declaration_path=f"{selector_path}.disallowed_targets",
                referrer_path=referrer_path,
                action_id=action_id,
                message="Dynamic route disallowed targets must be text values.",
                context={"reason": "invalid_disallowed_targets"},
                hint="Declare zero or more disallowed selector target names.",
            )
        )
        return
    disallowed_targets = frozenset(str(item) for item in raw_disallowed_targets)

    graph_node_ids = _graph_node_ids(source)
    stage_contracts = _stage_action_contracts(source)
    runner_stage_ids = _runner_stage_ids(source)
    for target_name, target in targets.items():
        if not is_non_empty_text(target_name) or not isinstance(target, Mapping):
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="invalid_dynamic_route_selector",
                    declaration_path=f"{selector_path}.targets",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Dynamic route selector target keys and values must be "
                        "nonempty text and mappings."
                    ),
                    context={"reason": "invalid_target_record"},
                    hint="Use a nonempty text target name with route target fields.",
                )
            )
            continue
        target_name_text = str(target_name)
        if target_name_text in disallowed_targets:
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="invalid_dynamic_route_selector",
                    declaration_path=f"{selector_path}.targets.{target_name_text}",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message="Dynamic route target is explicitly disallowed.",
                    context={
                        "reason": "disallowed_target",
                        "target_name": target_name_text,
                    },
                    hint="Remove disallowed targets from the selector target map.",
                )
            )
            continue
        target_path = f"{selector_path}.targets.{target_name_text}"
        target_stage_id = target.get("target_stage_kind_id")
        target_graph_node_id = target.get("target_graph_node_id")
        emitted_queue_id = target.get("emitted_queue_family_id")
        runner_binding_id = target.get("runner_binding_id")
        missing = tuple(
            field_name
            for field_name, value in (
                ("target_stage_kind_id", target_stage_id),
                ("target_graph_node_id", target_graph_node_id),
                ("emitted_queue_family_id", emitted_queue_id),
                ("runner_binding_id", runner_binding_id),
            )
            if not is_non_empty_text(value)
        )
        for field_name in missing:
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="invalid_dynamic_route_selector",
                    declaration_path=f"{target_path}.{field_name}",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message="Dynamic route target is missing executable authority.",
                    context={
                        "reason": "missing_target_field",
                        "target_name": str(target_name),
                        "field_name": field_name,
                    },
                    hint=(
                        "Dynamic route targets must declare target stage, graph "
                        "node, emitted queue family, and runner binding."
                    ),
                )
            )
        if missing:
            continue
        target_graph_node_key = str(target_graph_node_id)
        if target_graph_node_key not in graph_node_ids:
            diagnostics.append(
                compiler_error(
                    code="missing_reference",
                    declaration_path=f"{target_path}.target_graph_node_id",
                    message="Dynamic route target references an undeclared graph node.",
                    context={
                        "referrer_path": referrer_path,
                        "action_id": action_id,
                        "reference_kind": "graph_node",
                        "referenced_id": target_graph_node_key,
                    },
                    hint=(
                        "Declare the graph node before selecting it as a route "
                        "target."
                    ),
                )
            )
        if not _dynamic_route_target_contract_supported(
            source_stage_id=str(record.get("stage_kind_id", "")),
            target_stage_id=str(target_stage_id),
            emitted_queue_id=str(emitted_queue_id),
            artifact_schema_id=str(record.get("artifact_schema_id", "")),
            runner_binding_id=str(runner_binding_id),
            stage_contracts=stage_contracts,
            runner_stage_ids=runner_stage_ids,
        ):
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_dynamic_route_target_mismatch",
                    declaration_path=target_path,
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Dynamic route target does not satisfy the selected route "
                        "contract."
                    ),
                    context={
                        "target_name": str(target_name),
                        "source_stage_kind_id": str(record.get("stage_kind_id", "")),
                        "target_stage_kind_id": str(target_stage_id),
                        "emitted_queue_family_id": str(emitted_queue_id),
                        "artifact_schema_id": str(record.get("artifact_schema_id", "")),
                        "runner_binding_id": str(runner_binding_id),
                    },
                    hint=(
                        "Use a queue, stage, schema, and runner tuple declared by "
                        "the source and target stage contracts."
                    ),
                )
            )


def _dynamic_route_target_contract_supported(
    *,
    source_stage_id: str,
    target_stage_id: str,
    emitted_queue_id: str,
    artifact_schema_id: str,
    runner_binding_id: str,
    stage_contracts: Mapping[
        str,
        tuple[frozenset[str], frozenset[str], frozenset[str], str],
    ],
    runner_stage_ids: Mapping[str, frozenset[str]],
) -> bool:
    source_contract = stage_contracts.get(source_stage_id)
    target_contract = stage_contracts.get(target_stage_id)
    if source_contract is None or target_contract is None:
        return False
    _, source_outputs, source_schemas, _ = source_contract
    target_inputs, _, target_schemas, target_runner_binding_id = target_contract
    return (
        emitted_queue_id in source_outputs
        and emitted_queue_id in target_inputs
        and artifact_schema_id in source_schemas
        and artifact_schema_id in target_schemas
        and runner_binding_id == target_runner_binding_id
        and target_stage_id in runner_stage_ids.get(runner_binding_id, frozenset())
    )


def _graph_node_ids(source: Mapping[str, object]) -> frozenset[str]:
    return frozenset(
        node_id
        for record in records(source, "graphs")
        for node_id in text_tuple(record.get("node_ids", ()))
    )


def _validate_artifact_action_contracts(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    stage_contracts = _stage_action_contracts(source)
    for index, record in enumerate(records(source, "terminal_actions")):
        if record.get("kind") in EXECUTABLE_ROUTE_ACTION_KINDS:
            continue
        artifact_schema_id = record.get("artifact_schema_id")
        if not is_non_empty_text(artifact_schema_id):
            continue
        source_stage_id = record.get("stage_kind_id")
        if not is_non_empty_text(source_stage_id):
            continue
        action_id = str(record.get("id", ""))
        source_stage_key = str(source_stage_id)
        artifact_schema_key = str(artifact_schema_id)
        source_contract = stage_contracts.get(source_stage_key)
        if source_contract is None:
            continue
        _, _, source_schemas, _ = source_contract
        if artifact_schema_key in source_schemas:
            continue
        referrer_path = f"terminal_actions[{index}]"
        diagnostics.append(
            _terminal_route_contract_diagnostic(
                code="terminal_action_artifact_schema_mismatch",
                declaration_path=f"{referrer_path}.artifact_schema_id",
                referrer_path=referrer_path,
                action_id=action_id,
                message=(
                    "Terminal action artifact schema is not declared by the "
                    "source stage."
                ),
                context={
                    "source_stage_kind_id": source_stage_key,
                    "artifact_schema_id": artifact_schema_key,
                },
                hint=(
                    "Artifact-writing terminal actions must use a schema "
                    "declared by their source stage kind."
                ),
            )
        )


def _validate_recovery_route_action_contracts(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    stage_contracts = _stage_action_contracts(source)
    runner_stage_ids = _runner_stage_ids(source)
    asset_kinds = _asset_kinds(source)
    graph_node_stage_owner = _known_graph_node_stage_owner(source)
    for index, record in enumerate(records(source, "terminal_actions")):
        if record.get("kind") != "recovery_route":
            continue
        referrer_path = f"terminal_actions[{index}]"
        action_id = str(record.get("id", ""))
        target_stage_id = record.get("target_stage_kind_id")
        runner_binding_id = record.get("runner_binding_id")
        missing_fields = _missing_recovery_route_fields(record)
        for field_name in missing_fields:
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_recovery_route_missing_field",
                    declaration_path=f"{referrer_path}.{field_name}",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Recovery route action is missing required recovery "
                        "target authority."
                    ),
                    context={"field_name": field_name},
                    hint=(
                        "Recovery route actions must declare target stage, "
                        "target graph node, runner binding, and prompt assets."
                    ),
                )
            )
        if missing_fields:
            continue

        target_stage_key = str(target_stage_id)
        target_graph_node_key = str(record.get("target_graph_node_id", ""))
        known_stage_for_node = graph_node_stage_owner.get(target_graph_node_key)
        if (
            known_stage_for_node is not None
            and known_stage_for_node != target_stage_key
        ):
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_recovery_route_graph_node_stage_mismatch",
                    declaration_path=f"{referrer_path}.target_graph_node_id",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Recovery route target graph node belongs to a different "
                        "selected stage."
                    ),
                    context={
                        "target_stage_kind_id": target_stage_key,
                        "target_graph_node_id": target_graph_node_key,
                        "graph_node_stage_kind_id": known_stage_for_node,
                    },
                    hint=(
                        "Use a recovery graph node selected for the target "
                        "recovery stage."
                    ),
                )
            )
        runner_binding_key = str(runner_binding_id)
        target_contract = stage_contracts.get(target_stage_key)
        if target_contract is None:
            continue

        _, _, _, target_runner_binding_id = target_contract
        if runner_binding_key != target_runner_binding_id:
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_recovery_route_stage_runner_mismatch",
                    declaration_path=f"{referrer_path}.runner_binding_id",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Recovery route runner binding does not match the "
                        "target stage runner binding."
                    ),
                    context={
                        "target_stage_kind_id": target_stage_key,
                        "route_runner_binding_id": runner_binding_key,
                        "stage_runner_binding_id": target_runner_binding_id,
                    },
                    hint="Use the runner binding declared by the target stage.",
                )
            )
        if target_stage_key not in runner_stage_ids.get(
            runner_binding_key,
            frozenset(),
        ):
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_recovery_route_runner_stage_mismatch",
                    declaration_path=f"{referrer_path}.runner_binding_id",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message=(
                        "Recovery route runner binding does not list the target "
                        "stage."
                    ),
                    context={
                        "target_stage_kind_id": target_stage_key,
                        "runner_binding_id": runner_binding_key,
                    },
                    hint=("List the target stage in the referenced runner binding."),
                )
            )

        for asset_index, asset_id in enumerate(text_tuple(record.get("asset_ids", ()))):
            asset_kind = asset_kinds.get(asset_id)
            if asset_kind is None or asset_kind in _RECOVERY_ROUTE_PROMPT_ASSET_KINDS:
                continue
            diagnostics.append(
                _terminal_route_contract_diagnostic(
                    code="terminal_recovery_route_asset_kind_mismatch",
                    declaration_path=f"{referrer_path}.asset_ids[{asset_index}]",
                    referrer_path=referrer_path,
                    action_id=action_id,
                    message="Recovery route asset is not a prompt-like asset.",
                    context={"asset_id": asset_id, "asset_kind": asset_kind},
                    hint=(
                        "Reference only prompt or entrypoint prompt assets from "
                        "recovery route actions."
                    ),
                )
            )


def _known_graph_node_stage_owner(source: Mapping[str, object]) -> Mapping[str, str]:
    owners: dict[str, str] = {}
    for record in records(source, "external_enqueue_routes"):
        _record_graph_node_stage_owner(
            owners,
            stage_kind_id=record.get("stage_kind_id"),
            graph_node_id=record.get("graph_node_id"),
        )
    for record in records(source, "generated_work_routes"):
        _record_graph_node_stage_owner(
            owners,
            stage_kind_id=record.get("stage_kind_id"),
            graph_node_id=record.get("graph_node_id"),
        )
    for record in records(source, "completion_behaviors"):
        _record_graph_node_stage_owner(
            owners,
            stage_kind_id=record.get("target_stage_kind_id"),
            graph_node_id=record.get("target_graph_node_id"),
        )
    for record in records(source, "remediation_policies"):
        _record_graph_node_stage_owner(
            owners,
            stage_kind_id=record.get("target_stage_kind_id"),
            graph_node_id=record.get("target_graph_node_id"),
        )
    for record in records(source, "terminal_actions"):
        if record.get("kind") == "recovery_route":
            continue
        _record_graph_node_stage_owner(
            owners,
            stage_kind_id=record.get("target_stage_kind_id"),
            graph_node_id=record.get("target_graph_node_id"),
        )
        selector = record.get("dynamic_target_selector")
        if not isinstance(selector, Mapping):
            continue
        targets = selector.get("targets")
        if not isinstance(targets, Mapping):
            continue
        for target in targets.values():
            if not isinstance(target, Mapping):
                continue
            _record_graph_node_stage_owner(
                owners,
                stage_kind_id=target.get("target_stage_kind_id"),
                graph_node_id=target.get("target_graph_node_id"),
            )
    return owners


def _record_graph_node_stage_owner(
    owners: dict[str, str],
    *,
    stage_kind_id: object,
    graph_node_id: object,
) -> None:
    if is_non_empty_text(stage_kind_id) and is_non_empty_text(graph_node_id):
        owners.setdefault(str(graph_node_id), str(stage_kind_id))


def _missing_route_action_fields(record: SourceRecord) -> tuple[str, ...]:
    missing: list[str] = []
    for field_name in (
        "stage_kind_id",
        "target_stage_kind_id",
        "target_graph_node_id",
        "emitted_queue_family_id",
        "artifact_schema_id",
        "runner_binding_id",
    ):
        if not is_non_empty_text(record.get(field_name)):
            missing.append(field_name)
    if record.get("payload_projection") is None:
        missing.append("payload_projection")
    return tuple(missing)


def _missing_recovery_route_fields(record: SourceRecord) -> tuple[str, ...]:
    missing: list[str] = []
    for field_name in (
        "target_stage_kind_id",
        "target_graph_node_id",
        "runner_binding_id",
    ):
        if not is_non_empty_text(record.get(field_name)):
            missing.append(field_name)
    if not text_tuple(record.get("asset_ids", ())):
        missing.append("asset_ids")
    return tuple(missing)


def _terminal_route_contract_diagnostic(
    *,
    code: str,
    declaration_path: str,
    referrer_path: str,
    action_id: str,
    message: str,
    context: Mapping[str, str],
    hint: str,
) -> Diagnostic:
    return compiler_error(
        code=code,
        declaration_path=declaration_path,
        message=message,
        context={
            "referrer_path": referrer_path,
            "action_id": action_id,
            **context,
        },
        hint=hint,
    )


def _stage_action_contracts(
    source: Mapping[str, object],
) -> Mapping[str, tuple[frozenset[str], frozenset[str], frozenset[str], str]]:
    return {
        str(record["id"]): (
            frozenset(text_tuple(record.get("input_queue_family_ids", ()))),
            frozenset(text_tuple(record.get("output_queue_family_ids", ()))),
            frozenset(text_tuple(record.get("artifact_schema_ids", ()))),
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


def _asset_kinds(source: Mapping[str, object]) -> Mapping[str, str]:
    return {
        str(record["id"]): str(record.get("kind", ""))
        for record in records(source, "assets")
        if is_non_empty_text(record.get("id"))
    }


__all__ = (
    "EXECUTABLE_ROUTE_ACTION_KINDS",
    "SUPPORTED_TERMINAL_ACTION_KINDS",
    "validate_route_action_contracts",
    "validate_terminal_action_kinds",
    "validate_terminal_actions_are_unambiguous",
    "validate_terminal_outcome_markers_are_unambiguous",
)
