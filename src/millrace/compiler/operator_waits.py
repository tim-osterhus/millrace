"""Operator-wait compiler validation."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.compiler.diagnostics import compiler_error
from millrace.compiler.references import (
    IdIndex,
    _validate_revise_target_contract,
    _validate_single_reference,
)
from millrace.compiler.source import (
    SourceRecord,
    is_non_empty_text,
    records,
    text_tuple,
)
from millrace.contracts import Diagnostic
from millrace.contracts.operator_waits import (
    _SUPPORTED_OPERATOR_WAIT_RESOLUTION_KINDS,
    _operator_wait_audit_metadata_requirements,
)

OPERATOR_WAIT_FIELDS = frozenset(
    {
        "id",
        "source_action_ids",
        "wait_scope",
        "source_work_item_behavior",
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

_REVISE_TARGET_FIELDS = (
    "payload_schema_id",
    "target_queue_family_id",
    "target_stage_kind_id",
    "target_graph_node_id",
    "target_runner_binding_id",
)


def validate_operator_wait_references(
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    action_records = {
        str(record["id"]): record
        for record in records(source, "terminal_actions")
        if is_non_empty_text(record.get("id"))
    }
    wait_action_ids: set[str] = set()
    owner_by_action_id: dict[str, tuple[str, str]] = {}
    for index, record in enumerate(records(source, "operator_waits")):
        referrer_path = f"operator_waits[{index}]"
        wait_id = str(record.get("id", ""))
        for field_name in sorted(frozenset(record) - OPERATOR_WAIT_FIELDS):
            diagnostics.append(
                compiler_error(
                    code="unknown_operator_wait_field",
                    declaration_path=f"{referrer_path}.{field_name}",
                    message="Operator wait contains an unsupported field.",
                    context={"referrer_path": referrer_path, "field_name": field_name},
                    hint="Use only fields declared by the operator wait contract.",
                )
            )
        for field_name in _missing_operator_wait_fields(record):
            diagnostics.append(
                compiler_error(
                    code="missing_operator_wait_field",
                    declaration_path=f"{referrer_path}.{field_name}",
                    message="Operator wait is missing required authority.",
                    context={"referrer_path": referrer_path, "field_name": field_name},
                    hint="Declare every required operator wait field.",
                )
            )

        seen_action_ids: set[str] = set()
        for action_id in text_tuple(record.get("source_action_ids", ())):
            wait_action_ids.add(action_id)
            if action_id in seen_action_ids:
                diagnostics.append(
                    compiler_error(
                        code="duplicate_operator_wait_source_action",
                        declaration_path=f"{referrer_path}.source_action_ids",
                        message=(
                            "Operator wait source action is declared more than once."
                        ),
                        context={
                            "referrer_path": referrer_path,
                            "operator_wait_id": wait_id,
                            "action_id": action_id,
                        },
                        hint="List each operator wait source action at most once.",
                    )
                )
                continue
            seen_action_ids.add(action_id)

            first_owner = owner_by_action_id.get(action_id)
            if first_owner is not None:
                first_wait_id, first_referrer_path = first_owner
                diagnostics.append(
                    compiler_error(
                        code="duplicate_operator_wait_owner",
                        declaration_path=f"{referrer_path}.source_action_ids",
                        related_declaration_path=(
                            f"{first_referrer_path}.source_action_ids"
                        ),
                        message="Operator wait source action has multiple owners.",
                        context={
                            "referrer_path": referrer_path,
                            "action_id": action_id,
                            "first_operator_wait_id": first_wait_id,
                            "duplicate_operator_wait_id": wait_id,
                        },
                        hint=(
                            "Declare exactly one operator wait owner per source "
                            "action."
                        ),
                    )
                )
            else:
                owner_by_action_id[action_id] = (wait_id, referrer_path)

            _validate_single_reference(
                raw_value=action_id,
                ids=indexes["terminal_actions"].ids,
                declaration_path=f"{referrer_path}.source_action_ids",
                referrer_path=referrer_path,
                reference_kind="terminal_action",
                diagnostics=diagnostics,
            )
            action = action_records.get(action_id)
            if action is not None and action.get("kind") != "operator_wait":
                diagnostics.append(
                    compiler_error(
                        code="invalid_operator_wait_action_kind",
                        declaration_path=f"{referrer_path}.source_action_ids",
                        message=(
                            "Operator wait source action must use operator_wait."
                        ),
                        context={
                            "referrer_path": referrer_path,
                            "action_id": action_id,
                            "action_kind": str(action.get("kind", "")),
                        },
                        hint="Reference only terminal actions with kind operator_wait.",
                    )
                )

        _validate_operator_wait_revise_references(
            record,
            referrer_path,
            source,
            indexes,
            diagnostics,
        )
        _validate_operator_wait_values(record, referrer_path, diagnostics)

    for index, record in enumerate(records(source, "terminal_actions")):
        if record.get("kind") != "operator_wait":
            continue
        action_id = str(record.get("id", ""))
        if action_id in wait_action_ids:
            continue
        diagnostics.append(
            compiler_error(
                code="missing_operator_wait_for_action",
                declaration_path=f"terminal_actions[{index}].kind",
                message="operator_wait terminal action is not backed by authority.",
                context={
                    "referrer_path": f"terminal_actions[{index}]",
                    "action_id": action_id,
                },
                hint="Add an operator_wait declaration referencing this action.",
            )
        )


def _missing_operator_wait_fields(record: SourceRecord) -> tuple[str, ...]:
    required = {
        "id",
        "source_action_ids",
        "wait_scope",
        "source_work_item_behavior",
        "unrelated_lineages_continue",
        "allowed_resolution_kinds",
        "actor_kind",
        "audit_metadata_requirements",
        "correlation_key",
        "idempotency",
        "timeout_policy",
        "expiry_policy",
        "cancellation_policy",
        "status_effect",
    }
    if "revise_recorded_source" in text_tuple(record.get("allowed_resolution_kinds")):
        required.update(_REVISE_TARGET_FIELDS)
    return tuple(
        sorted(
            field_name
            for field_name in required
            if field_name not in record or record.get(field_name) is None
        )
    )


def _validate_operator_wait_revise_references(
    record: SourceRecord,
    referrer_path: str,
    source: Mapping[str, object],
    indexes: Mapping[str, IdIndex],
    diagnostics: list[Diagnostic],
) -> None:
    if "revise_recorded_source" not in text_tuple(
        record.get("allowed_resolution_kinds")
    ):
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
    _validate_revise_target_contract(record, referrer_path, source, diagnostics)


def _validate_operator_wait_values(
    record: SourceRecord,
    referrer_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    wait_id = str(record.get("id", ""))
    expected_values = {
        "wait_scope": "lineage",
        "unrelated_lineages_continue": True,
        "actor_kind": "local_operator",
        "correlation_key": "wait_id",
        "idempotency": "input_receipt_and_active_wait_status",
        "timeout_policy": "none",
        "expiry_policy": "none",
        "cancellation_policy": "selected_resolution_only",
        "status_effect": "operator_wait_active",
    }
    for field_name, expected_value in expected_values.items():
        if record.get(field_name) == expected_value:
            continue
        _invalid_operator_wait_field(
            diagnostics,
            referrer_path=referrer_path,
            field_name=field_name,
            value=str(record.get(field_name, "")),
            wait_id=wait_id,
        )

    if "source_action_ids" in record and not text_tuple(
        record.get("source_action_ids", ())
    ):
        _invalid_operator_wait_field(
            diagnostics,
            referrer_path=referrer_path,
            field_name="source_action_ids",
            value="",
            wait_id=wait_id,
        )

    allowed = text_tuple(record.get("allowed_resolution_kinds", ()))
    allowed_set = set(allowed)
    resolution_error = False
    if not allowed:
        resolution_error = True
        _invalid_operator_wait_field(
            diagnostics,
            referrer_path=referrer_path,
            field_name="allowed_resolution_kinds",
            value="",
            wait_id=wait_id,
        )
    seen_resolution_kinds: set[str] = set()
    for resolution_kind in allowed:
        if resolution_kind in seen_resolution_kinds:
            resolution_error = True
            diagnostics.append(
                compiler_error(
                    code="duplicate_operator_wait_resolution_kind",
                    declaration_path=f"{referrer_path}.allowed_resolution_kinds",
                    message=(
                        "Operator wait resolution kind is declared more than once."
                    ),
                    context={
                        "referrer_path": referrer_path,
                        "operator_wait_id": wait_id,
                        "resolution_kind": resolution_kind,
                    },
                    hint="List each allowed operator wait resolution kind once.",
                )
            )
        seen_resolution_kinds.add(resolution_kind)

    unsupported = allowed_set - _SUPPORTED_OPERATOR_WAIT_RESOLUTION_KINDS
    if unsupported:
        resolution_error = True
        _invalid_operator_wait_field(
            diagnostics,
            referrer_path=referrer_path,
            field_name="allowed_resolution_kinds",
            value=",".join(sorted(unsupported)),
            wait_id=wait_id,
        )

    source_behavior = record.get("source_work_item_behavior")
    source_behavior_error = False
    if source_behavior not in {
        "leave_open",
        "close_on_create",
    }:
        source_behavior_error = True
        _invalid_operator_wait_field(
            diagnostics,
            referrer_path=referrer_path,
            field_name="source_work_item_behavior",
            value=str(source_behavior or ""),
            wait_id=wait_id,
        )
    if source_behavior == "close_on_create" and allowed_set != {
        "close_recorded_source"
    }:
        source_behavior_error = True
        _invalid_operator_wait_field(
            diagnostics,
            referrer_path=referrer_path,
            field_name="source_work_item_behavior",
            value="close_on_create",
            wait_id=wait_id,
        )

    has_revise = "revise_recorded_source" in allowed_set
    revise_target_error = False
    if not has_revise:
        for field_name in _operator_wait_revise_target_fields(record):
            revise_target_error = True
            _invalid_operator_wait_field(
                diagnostics,
                referrer_path=referrer_path,
                field_name=field_name,
                value=str(record.get(field_name, "")),
                wait_id=wait_id,
            )

    if resolution_error or source_behavior_error or revise_target_error:
        return

    requirements = text_tuple(record.get("audit_metadata_requirements", ()))
    expected = _operator_wait_audit_metadata_requirements(allowed)
    if requirements != expected:
        _invalid_operator_wait_field(
            diagnostics,
            referrer_path=referrer_path,
            field_name="audit_metadata_requirements",
            value=",".join(requirements),
            wait_id=wait_id,
        )


def _operator_wait_revise_target_fields(record: SourceRecord) -> tuple[str, ...]:
    return tuple(
        field_name
        for field_name in _REVISE_TARGET_FIELDS
        if record.get(field_name) is not None
    )


def _invalid_operator_wait_field(
    diagnostics: list[Diagnostic],
    *,
    referrer_path: str,
    field_name: str,
    value: str,
    wait_id: str,
) -> None:
    diagnostics.append(
        compiler_error(
            code="invalid_operator_wait_field",
            declaration_path=f"{referrer_path}.{field_name}",
            message="Operator wait field value is unsupported.",
            context={
                "referrer_path": referrer_path,
                "operator_wait_id": wait_id,
                "field_name": field_name,
                "value": value,
            },
            hint="Use the operator wait values supported by this runtime slice.",
        )
    )


__all__ = ("validate_operator_wait_references",)
