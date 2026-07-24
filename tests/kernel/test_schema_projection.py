from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import ArtifactSchemaId
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.kernel.projection import ProjectionContext, evaluate_projection
from millrace.kernel.schema import validate_schema
from millrace.workflows import kernel_ping, simple_loop

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _assert_accepted(schema: Mapping[str, object], payload: object) -> None:
    result = validate_schema(schema, payload)
    assert result.accepted is True
    assert result.issues == ()


def _assert_rejected(schema: Mapping[str, object], payload: object) -> tuple[str, ...]:
    result = validate_schema(schema, payload)
    assert result.accepted is False
    assert result.issues != ()
    return tuple(issue.reason for issue in result.issues)


def _simple_loop_schema(schema_id: str) -> Mapping[str, object]:
    result = compile_workflow(
        simple_loop.workflow_source(), selected_runner_policy=_CODEX_POLICY
    )
    assert result.plan is not None
    return next(
        schema.schema
        for schema in result.plan.artifact_schemas
        if schema.id == ArtifactSchemaId(schema_id)
    )


def test_schema_validates_declared_object_array_and_scalar_subset() -> None:
    schema = {
        "type": "object",
        "required": ("title", "checks", "enabled", "empty"),
        "properties": {
            "title": {"type": "string", "min_length": 1},
            "checks": {
                "type": "array",
                "min_items": 1,
                "items": {"type": "integer"},
            },
            "enabled": {"type": "boolean"},
            "empty": {"type": "null"},
            "status": {"type": "string", "enum": ("ready", "blocked")},
            "version": {"type": "integer", "const": 1},
        },
    }

    _assert_accepted(
        schema,
        {
            "title": "Executable task",
            "checks": (1, 2),
            "enabled": True,
            "empty": None,
            "status": "ready",
            "version": 1,
        },
    )
    assert "missing_required_property" in _assert_rejected(
        schema,
        {"title": "Executable task", "checks": (1,), "enabled": True},
    )
    assert "unexpected_property" in _assert_rejected(
        schema,
        {
            "title": "Executable task",
            "checks": (1,),
            "enabled": True,
            "empty": None,
            "extra": "not declared",
        },
    )
    assert "array_too_short" in _assert_rejected(
        schema,
        {"title": "Executable task", "checks": (), "enabled": True, "empty": None},
    )
    assert "string_too_short" in _assert_rejected(
        schema,
        {"title": "", "checks": (1,), "enabled": True, "empty": None},
    )
    assert "const_mismatch" in _assert_rejected(
        schema,
        {
            "title": "Executable task",
            "checks": (1,),
            "enabled": True,
            "empty": None,
            "version": 2,
        },
    )
    assert "enum_mismatch" in _assert_rejected(
        schema,
        {
            "title": "Executable task",
            "checks": (1,),
            "enabled": True,
            "empty": None,
            "status": "waiting",
        },
    )


def test_schema_rejects_floats_bool_as_integer_and_unsupported_keywords() -> None:
    assert "type_mismatch" in _assert_rejected({"type": "integer"}, True)
    assert "unsupported_payload_number" in _assert_rejected(
        {"type": "integer"},
        1.2,
    )
    assert "unsupported_schema_number" in _assert_rejected(
        {"type": "number"},
        1,
    )
    assert "unsupported_schema_number" in _assert_rejected(
        {"const": 1.2},
        1.2,
    )
    assert "unsupported_schema_number" in _assert_rejected(
        {"enum": (1, 1.2)},
        1,
    )
    assert "unsupported_schema_keyword" in _assert_rejected(
        {
            "type": "object",
            "properties": {"title": {"type": "string", "pattern": ".+"}},
        },
        {"title": "Executable task"},
    )


@pytest.mark.parametrize(
    "schema",
    (
        {"type": "object", "required": "title", "properties": {}},
        {"type": "object", "required": (1,), "properties": {}},
        {"type": "object", "properties": ("title",)},
        {"type": "array", "items": "string"},
        {"enum": "ready"},
        {"const": {"not": "a supported scalar"}},
        {"type": "array", "min_items": "1"},
        {"type": "string", "min_length": True},
    ),
)
def test_schema_rejects_wrong_shapes_for_supported_keywords(
    schema: Mapping[str, object],
) -> None:
    assert "unsupported_schema_value" in _assert_rejected(schema, None)


def test_schema_const_and_enum_use_type_strict_scalar_equality() -> None:
    assert "const_mismatch" in _assert_rejected({"const": 1}, True)
    assert "enum_mismatch" in _assert_rejected({"enum": (1,)}, True)
    assert "const_mismatch" in _assert_rejected({"const": True}, 1)
    assert "enum_mismatch" in _assert_rejected({"enum": (True,)}, 1)


def test_simple_loop_schemas_reject_blank_required_text() -> None:
    work_packet_schema = _simple_loop_schema("simple_loop.work_packet")
    work_packet = {
        "artifact_kind": "simple_loop.work_packet",
        "source_prompt_id": "prompt-1",
        "title": "Route context",
        "objective": "Preserve downstream context",
        "completion_definition": "Reviewer can evaluate the result.",
    }
    for field_name in (
        "source_prompt_id",
        "title",
        "objective",
        "completion_definition",
    ):
        invalid = dict(work_packet)
        invalid[field_name] = ""
        assert "string_too_short" in _assert_rejected(work_packet_schema, invalid)

    work_result_schema = _simple_loop_schema("simple_loop.work_result")
    assert "string_too_short" in _assert_rejected(
        work_result_schema,
        {"artifact_kind": "simple_loop.work_result", "summary": ""},
    )

    incident_schema = _simple_loop_schema("simple_loop.incident_report")
    assert "string_too_short" in _assert_rejected(
        incident_schema,
        {"artifact_kind": "simple_loop.incident_report", "reason": ""},
    )


def test_simple_loop_array_schemas_reject_blank_and_non_string_items() -> None:
    detail_request_schema = _simple_loop_schema("simple_loop.detail_request")
    assert "string_too_short" in _assert_rejected(
        detail_request_schema,
        {
            "artifact_kind": "simple_loop.detail_request",
            "missing_details": ("",),
        },
    )
    assert "type_mismatch" in _assert_rejected(
        detail_request_schema,
        {
            "artifact_kind": "simple_loop.detail_request",
            "missing_details": (1,),
        },
    )

    gap_packet_schema = _simple_loop_schema("simple_loop.gap_packet")
    assert "string_too_short" in _assert_rejected(
        gap_packet_schema,
        {"artifact_kind": "simple_loop.gap_packet", "gaps": ("",)},
    )
    assert "type_mismatch" in _assert_rejected(
        gap_packet_schema,
        {"artifact_kind": "simple_loop.gap_packet", "gaps": (1,)},
    )


def test_projection_evaluates_declared_literal_source_object_and_array_forms() -> None:
    context = ProjectionContext(
        work_item_payload={"prompt_id": "p-1", "body": "Build it"},
        artifact_payload={"title": "Executable task", "tests": ("pytest",)},
        observation_payload={"marker": "done"},
        run_metadata={"run_id": "run-a"},
        plan_metadata={"plan_id": "plan-a"},
    )
    projection = {
        "kind": "object",
        "fields": {
            "title": {"kind": "source", "path": ("artifact_payload", "title")},
            "prompt_id": {"kind": "source", "path": ("work_item_payload", "prompt_id")},
            "source": {"kind": "source", "path": ("run_metadata", "run_id")},
            "tags": {
                "kind": "array",
                "items": (
                    {"kind": "literal", "value": "accepted"},
                    {"kind": "source", "path": ("observation_payload", "marker")},
                    {"kind": "source", "path": ("plan_metadata", "plan_id")},
                ),
            },
        },
    }

    result = evaluate_projection(projection, context)

    assert result.accepted is True
    assert result.value == {
        "title": "Executable task",
        "prompt_id": "p-1",
        "source": "run-a",
        "tags": ("accepted", "done", "plan-a"),
    }


def test_projection_refuses_missing_sources_and_executable_shapes() -> None:
    context = ProjectionContext(
        work_item_payload={},
        artifact_payload={"title": "Executable task"},
        observation_payload={},
        run_metadata={},
        plan_metadata={},
    )

    missing = evaluate_projection(
        {"kind": "source", "path": ("artifact_payload", "missing")},
        context,
    )
    assert missing.accepted is False
    assert missing.error is not None
    assert missing.error.reason == "missing_source"

    callback = evaluate_projection(
        {"kind": "literal", "value": lambda: "not authority"},
        context,
    )
    assert callback.accepted is False
    assert callback.error is not None
    assert callback.error.reason == "unsupported_projection_value"

    expression_like = evaluate_projection(
        {"kind": "source", "path": ("artifact_payload", "__import__('os')")},
        context,
    )
    assert expression_like.accepted is False
    assert expression_like.error is not None
    assert expression_like.error.reason == "missing_source"


def test_compiled_success_route_projection_is_declarative_authority() -> None:
    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    plan = result.plan
    fingerprint = authority_fingerprint(plan)
    success_action = next(
        action
        for action in plan.terminal_actions
        if str(action.id) == "kernel_ping.route_taskmaster_success"
    )
    artifact_payload = cast(
        Mapping[str, AuthorityValue],
        {
            "artifact_kind": "kernel_ping.task_artifact",
            "source_prompt_id": "p-1",
            "title": "Executable task",
            "objective": "Prove the route",
            "requirements": ({"id": "r1", "description": "Do it"},),
            "completion_tests": (
                {"id": "t1", "description": "Run tests", "expected_result": "pass"},
            ),
        },
    )

    route_payload = evaluate_projection(
        success_action.payload_projection,
        ProjectionContext(
            work_item_payload={"prompt_id": "p-1", "body": "Build it"},
            artifact_payload=artifact_payload,
            observation_payload={"marker": "TASK_COMPLETE"},
            run_metadata={"run_id": "run-a"},
            plan_metadata={"authority_fingerprint": fingerprint},
        ),
    )

    assert route_payload.accepted is True
    assert route_payload.value == artifact_payload
