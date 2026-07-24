from __future__ import annotations

from collections.abc import Iterable
from typing import cast

import pytest

from millrace.contracts import Diagnostic
from millrace.contracts.schema import validate_schema
from support import vendor_selection

Source = dict[str, object]
Record = dict[str, object]


def _records(source: Source, key: str) -> list[Record]:
    return vendor_selection.records(source, key)


def _errors(source: Source) -> tuple[Diagnostic, ...]:
    return vendor_selection.compile_errors(source)


def _find_error(
    errors: Iterable[Diagnostic],
    code: str,
    *,
    declaration_path_suffix: str | None = None,
    reason: str | None = None,
) -> Diagnostic:
    matches = [diagnostic for diagnostic in errors if diagnostic.code == code]
    if declaration_path_suffix is not None:
        matches = [
            diagnostic
            for diagnostic in matches
            if diagnostic.declaration_path.endswith(declaration_path_suffix)
        ]
    if reason is not None:
        matches = [
            diagnostic
            for diagnostic in matches
            if diagnostic.context.get("reason") == reason
        ]
    assert matches, f"missing {code!r} in {tuple(errors)!r}"
    return matches[0]


def _join(source: Source) -> Record:
    return _records(source, "join_declarations")[0]


def _operator_wait(source: Source) -> Record:
    return _records(source, "operator_waits")[0]


def test_vendor_selection_rejects_compatibility_or_extension_authority() -> None:
    compatibility_source = vendor_selection.source()
    cast(Record, compatibility_source["workflow"])[
        "compatibility_profile"
    ] = "lad_codex"

    compatibility_error = _find_error(
        _errors(compatibility_source),
        "unsupported_compatibility_profile",
    )
    assert compatibility_error.declaration_path == "workflow.compatibility_profile"

    extension_source = vendor_selection.source()
    cast(Record, extension_source["workflow"])["required_extensions"] = (
        "vendor.marketplace",
    )

    extension_error = _find_error(
        _errors(extension_source),
        "unsupported_required_extensions",
    )
    assert extension_error.declaration_path == "workflow.required_extensions"


def test_vendor_selection_rejects_missing_or_extra_partition() -> None:
    missing_source = vendor_selection.source()
    _records(missing_source, "partitions")[:] = [
        partition
        for partition in _records(missing_source, "partitions")
        if partition["id"] != "authorization"
    ]

    missing_error = _find_error(
        _errors(missing_source),
        "missing_reference",
        declaration_path_suffix=".partition_id",
    )
    assert missing_error.context["reference_kind"] == "partition"
    assert missing_error.context["referenced_id"] == "authorization"

    extra_source = vendor_selection.source()
    _records(extra_source, "partitions").append(
        {"id": "shadow", "kind": "plane", "presentation": {}}
    )

    extra_error = _find_error(
        _errors(extra_source),
        "unreferenced_partition",
        declaration_path_suffix=".id",
    )
    assert extra_error.context["partition_id"] == "shadow"


def test_vendor_selection_rejects_internal_queue_external_route() -> None:
    source = vendor_selection.source()
    route = _records(source, "external_enqueue_routes")[0]
    route["queue_family_id"] = "candidate_bundle"

    error = _find_error(_errors(source), "external_enqueue_route_internal_queue")

    assert error.declaration_path == "external_enqueue_routes[0].queue_family_id"
    assert error.context["queue_family_id"] == "candidate_bundle"


def test_vendor_selection_rejects_route_schema_mismatch() -> None:
    source = vendor_selection.source()
    route = next(
        route
        for route in _records(source, "generated_work_routes")
        if route["id"] == "vendor_selection.rubric_work"
    )
    route["payload_schema_id"] = "RubricReport"

    error = _find_error(
        _errors(source),
        "invalid_fanout_declaration",
        declaration_path_suffix=".target_route_id",
        reason="target_route_contract_mismatch",
    )
    assert error.context["fanout_id"] == (
        "vendor_selection.candidate_packager.rubric_fanout"
    )


def test_vendor_selection_schema_rejects_blank_required_strings() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    schema = next(
        schema
        for schema in plan.artifact_schemas
        if str(schema.id) == "PurchaseRequest"
    )

    result = validate_schema(
        schema.schema,
        {
            "request_id": "",
            "requester_label": "ops",
            "category": "office",
            "budget_band": "low",
            "required_capabilities": ("standard_office_supplies",),
            "disallowed_vendors": (),
            "approval_policy_hint": "none",
        },
    )

    assert result.accepted is False
    assert any(issue.reason == "string_too_short" for issue in result.issues)


def test_vendor_selection_schema_rejects_invalid_array_items() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    schema = next(
        schema
        for schema in plan.artifact_schemas
        if str(schema.id) == "PurchaseRequest"
    )

    result = validate_schema(
        schema.schema,
        {
            "request_id": "PR-1",
            "requester_label": "ops",
            "category": "office",
            "budget_band": "low",
            "required_capabilities": ("standard_office_supplies", 42),
            "disallowed_vendors": (),
            "approval_policy_hint": "none",
        },
    )

    assert result.accepted is False
    assert any(issue.reason == "type_mismatch" for issue in result.issues)


def test_vendor_selection_rejects_extra_schema_properties() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    schema = next(
        schema
        for schema in plan.artifact_schemas
        if str(schema.id) == "PurchaseRequest"
    )

    result = validate_schema(
        schema.schema,
        {
            "request_id": "PR-1",
            "requester_label": "ops",
            "category": "office",
            "budget_band": "low",
            "required_capabilities": ("standard_office_supplies",),
            "disallowed_vendors": (),
            "approval_policy_hint": "none",
            "surprise": "not selected",
        },
    )

    assert result.accepted is False
    assert any(issue.reason == "unexpected_property" for issue in result.issues)


def test_vendor_selection_rejects_missing_or_dangling_runner_binding() -> None:
    source = vendor_selection.source()
    stage = _records(source, "stage_kinds")[0]
    stage["runner_binding_id"] = "missing.runner"

    error = _find_error(
        _errors(source),
        "missing_reference",
        declaration_path_suffix=".runner_binding_id",
    )

    assert error.context["reference_kind"] == "runner_binding"
    assert error.context["referenced_id"] == "missing.runner"


def test_vendor_selection_rejects_deferred_or_provider_action_kind() -> None:
    source = vendor_selection.source()
    action = _records(source, "terminal_actions")[0]
    action["kind"] = "deferred_terminal_action"

    error = _find_error(_errors(source), "unsupported_terminal_action_kind")

    assert error.context["action_kind"] == "deferred_terminal_action"


def test_vendor_selection_rejects_fanout_target_route_or_schema_drift() -> None:
    source = vendor_selection.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["target_route_id"] = "vendor_selection.conflict_work"

    error = _find_error(
        _errors(source),
        "invalid_fanout_declaration",
        declaration_path_suffix=".target_route_id",
        reason="duplicate_target_route",
    )

    assert error.context["fanout_id"] == (
        "vendor_selection.candidate_packager.conflict_fanout"
    )


@pytest.mark.parametrize(
    ("field_name", "value", "suffix", "code", "reason"),
    (
        (
            "target_stage_kind_id",
            "missing_stage",
            ".target_stage_kind_id",
            "missing_reference",
            None,
        ),
        (
            "required_artifact_schema_ids",
            ("RubricReport", "MissingReport"),
            ".required_artifact_schema_ids[1]",
            "missing_reference",
            None,
        ),
        (
            "required_artifact_schema_ids",
            ("RubricReport", "RubricReport"),
            ".required_artifact_schema_ids",
            "invalid_join_declaration",
            "duplicate_required_artifact_schema",
        ),
        (
            "required_artifact_schema_ids",
            (),
            ".required_artifact_schema_ids",
            "invalid_join_declaration",
            "invalid_required_artifact_schema_ids",
        ),
        (
            "correlation_key",
            "",
            ".correlation_key",
            "invalid_join_declaration",
            "invalid_correlation_key",
        ),
        (
            "correlation_key",
            "request_id",
            ".correlation_key",
            "invalid_join_declaration",
            "correlation_key_schema_mismatch",
        ),
        (
            "missing_policy",
            "skip",
            ".missing_policy",
            "invalid_join_declaration",
            "unsupported_missing_policy",
        ),
    ),
)
def test_vendor_selection_rejects_join_missing_or_dangling_refs(
    field_name: str,
    value: object,
    suffix: str,
    code: str,
    reason: str | None,
) -> None:
    source = vendor_selection.source()
    _join(source)[field_name] = value

    error = _find_error(
        _errors(source),
        code,
        declaration_path_suffix=suffix,
        reason=reason,
    )

    assert error.declaration_path.endswith(suffix)


def test_vendor_selection_rejects_join_missing_required_field() -> None:
    source = vendor_selection.source()
    del _join(source)["missing_policy"]

    error = _find_error(
        _errors(source),
        "missing_join_declaration_field",
        declaration_path_suffix=".missing_policy",
    )

    assert error.context["field_name"] == "missing_policy"


def test_vendor_selection_rejects_join_target_stage_schema_mismatch() -> None:
    source = vendor_selection.source()
    _join(source)["required_artifact_schema_ids"] = ("OperatorDecision",)

    error = _find_error(
        _errors(source),
        "invalid_join_declaration",
        declaration_path_suffix=".required_artifact_schema_ids",
        reason="target_stage_schema_mismatch",
    )

    assert error.context["join_id"] == vendor_selection.JOIN_ID


def test_vendor_selection_rejects_join_without_unique_generated_target_route() -> None:
    source = vendor_selection.source()
    routes = _records(source, "generated_work_routes")
    routes[:] = [
        route
        for route in routes
        if route["id"] != "vendor_selection.award_join_work"
    ]

    missing_error = _find_error(
        _errors(source),
        "invalid_join_declaration",
        declaration_path_suffix=".target_stage_kind_id",
        reason="target_route_mismatch",
    )
    assert missing_error.context["join_id"] == vendor_selection.JOIN_ID

    duplicate_source = vendor_selection.source()
    duplicate_route = dict(
        next(
            route
            for route in _records(duplicate_source, "generated_work_routes")
            if route["id"] == "vendor_selection.award_join_work"
        )
    )
    duplicate_route["id"] = "vendor_selection.award_join_work.v2"
    _records(duplicate_source, "generated_work_routes").append(duplicate_route)

    duplicate_error = _find_error(
        _errors(duplicate_source),
        "invalid_join_declaration",
        declaration_path_suffix=".target_stage_kind_id",
        reason="target_route_mismatch",
    )
    assert duplicate_error.context["join_id"] == vendor_selection.JOIN_ID


def test_vendor_selection_rejects_join_id_collision_with_terminal_action() -> None:
    source = vendor_selection.source()
    _join(source)["id"] = "vendor_selection.request_intake.request_ready"

    error = _find_error(
        _errors(source),
        "invalid_join_declaration",
        declaration_path_suffix=".id",
        reason="id_collision",
    )

    assert error.context["join_id"] == "vendor_selection.request_intake.request_ready"


def test_vendor_selection_rejects_unknown_join_field() -> None:
    source = vendor_selection.source()
    _join(source)["presentation"] = {"display_name": "Candidate Evidence Join"}

    error = _find_error(
        _errors(source),
        "unknown_join_declaration_field",
        declaration_path_suffix=".presentation",
    )

    assert error.context["field_name"] == "presentation"


def test_vendor_selection_rejects_concurrency_policy_dangling_partition() -> None:
    source = vendor_selection.source()
    policy = _records(source, "concurrency_policies")[0]
    policy["partition_id"] = "missing_partition"

    error = _find_error(
        _errors(source),
        "missing_reference",
        declaration_path_suffix=".partition_id",
    )

    assert error.context["reference_kind"] == "partition"
    assert error.context["referenced_id"] == "missing_partition"


def test_vendor_selection_rejects_operator_wait_manual_gate_or_acl_fields() -> None:
    source = vendor_selection.source()
    _operator_wait(source)["approval_labels"] = ("approve", "reject")

    error = _find_error(_errors(source), "unknown_operator_wait_field")

    assert error.context["field_name"] == "approval_labels"


def test_vendor_selection_rejects_unknown_operator_wait_resolution_kind() -> None:
    source = vendor_selection.source()
    _operator_wait(source)["allowed_resolution_kinds"] = (
        "resume_recorded_source",
        "approve",
    )

    error = _find_error(
        _errors(source),
        "invalid_operator_wait_field",
        declaration_path_suffix=".allowed_resolution_kinds",
    )

    assert error.context["value"] == "approve"
