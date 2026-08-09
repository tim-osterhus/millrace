from __future__ import annotations

import pytest

from millrace.contracts.schema import (
    validate_closure_verdict_schema_declaration,
    validate_schema,
    validate_schema_declaration,
)


def _unique_item_schema() -> dict[str, object]:
    return {
        "type": "array",
        "unique_by": "item_id",
        "items": {
            "type": "object",
            "required": ("item_id", "label"),
            "properties": {
                "item_id": {"type": "string", "min_length": 1},
                "label": {"type": "string", "min_length": 1},
            },
        },
    }


def test_schema_declaration_accepts_unique_by() -> None:
    result = validate_schema_declaration(_unique_item_schema())

    assert result.accepted is True
    assert result.issues == ()


def test_schema_validation_rejects_duplicate_unique_by_values() -> None:
    result = validate_schema(
        _unique_item_schema(),
        (
            {"item_id": "item-1", "label": "First"},
            {"item_id": "item-1", "label": "Second"},
        ),
    )

    assert result.accepted is False
    assert [issue.reason for issue in result.issues] == [
        "array_duplicate_unique_key"
    ]
    assert result.issues[0].path == "$[1].item_id"


def test_schema_declaration_rejects_invalid_unique_by() -> None:
    result = validate_schema_declaration({"type": "array", "unique_by": ""})

    assert result.accepted is False
    assert [issue.reason for issue in result.issues] == [
        "unsupported_schema_value"
    ]
    assert result.issues[0].detail == "unique_by"


def test_schema_declaration_rejects_unique_by_on_non_array() -> None:
    result = validate_schema_declaration({"type": "object", "unique_by": "item_id"})

    assert result.accepted is False
    assert [issue.reason for issue in result.issues] == [
        "unsupported_schema_value"
    ]
    assert result.issues[0].detail == "unique_by"


def test_schema_declaration_rejects_unique_by_missing_item_property() -> None:
    schema = _unique_item_schema()
    item_schema = schema["items"]
    assert isinstance(item_schema, dict)
    item_schema["required"] = ("label",)

    result = validate_schema_declaration(schema)

    assert result.accepted is False
    assert [issue.reason for issue in result.issues] == [
        "unsupported_schema_value"
    ]
    assert result.issues[0].detail == "unique_by"


def test_schema_declaration_rejects_unique_by_non_scalar_item_property() -> None:
    schema = _unique_item_schema()
    item_schema = schema["items"]
    assert isinstance(item_schema, dict)
    properties = item_schema["properties"]
    assert isinstance(properties, dict)
    properties["item_id"] = {
        "type": "object",
        "required": ("nested",),
        "properties": {"nested": {"type": "string"}},
    }

    result = validate_schema_declaration(schema)

    assert result.accepted is False
    assert [issue.reason for issue in result.issues] == [
        "unsupported_schema_value"
    ]
    assert result.issues[0].detail == "unique_by"


def _closure_verdict_schema() -> dict[str, object]:
    string = {"type": "string", "min_length": 1}
    evidence_ref = {
        "type": "object",
        "required": ("evidence_id", "summary"),
        "properties": {"evidence_id": string, "summary": string},
    }
    criterion = {
        "type": "object",
        "required": ("criterion_id", "requirement", "evidence_rule"),
        "properties": {
            "criterion_id": string,
            "requirement": string,
            "evidence_rule": string,
        },
    }
    criterion_result = {
        "type": "object",
        "required": ("criterion_id", "status", "provenance", "evidence_refs"),
        "properties": {
            "criterion_id": string,
            "status": {"enum": ("passed", "failed", "blocked")},
            "provenance": {
                "enum": (
                    "fresh",
                    "revalidated",
                    "historical_only",
                    "missing",
                )
            },
            "evidence_refs": {
                "type": "array",
                "items": evidence_ref,
                "unique_by": "evidence_id",
            },
        },
    }
    observation = {
        "type": "object",
        "required": ("observation_id", "summary"),
        "properties": {"observation_id": string, "summary": string},
    }
    guidance = {
        "type": "object",
        "required": ("guidance_id", "summary", "criterion_refs"),
        "properties": {
            "guidance_id": string,
            "summary": string,
            "criterion_refs": {
                "type": "array",
                "min_items": 1,
                "items": {
                    "type": "object",
                    "required": ("criterion_id",),
                    "properties": {"criterion_id": string},
                },
                "unique_by": "criterion_id",
            },
        },
    }
    properties = {
        "artifact_kind": string,
        "summary": string,
        "closure_target_id": string,
        "root_contract_digest": string,
        "freshness_anchor_digest": string,
        "rubric": {
            "type": "object",
            "required": ("criteria",),
            "properties": {
                "criteria": {
                    "type": "array",
                    "min_items": 1,
                    "items": criterion,
                    "unique_by": "criterion_id",
                }
            },
        },
        "criterion_results": {
            "type": "array",
            "min_items": 1,
            "items": criterion_result,
            "unique_by": "criterion_id",
        },
        "observations": {
            "type": "array",
            "items": observation,
            "unique_by": "observation_id",
        },
        "remediation_guidance": {
            "type": "array",
            "items": guidance,
            "unique_by": "guidance_id",
        },
        "confidence": {"enum": ("high", "medium", "low")},
        "residual_uncertainty": string,
    }
    return {
        "type": "object",
        "required": tuple(properties),
        "properties": properties,
    }


def test_closure_verdict_schema_contract_accepts_complete_shape() -> None:
    result = validate_closure_verdict_schema_declaration(_closure_verdict_schema())

    assert result.accepted is True
    assert result.issues == ()


def test_closure_verdict_schema_contract_requires_observation_identity_declaration(
) -> None:
    schema = _closure_verdict_schema()
    properties = schema["properties"]
    assert isinstance(properties, dict)
    observations = properties["observations"]
    assert isinstance(observations, dict)
    observations.pop("unique_by")

    result = validate_closure_verdict_schema_declaration(schema)

    assert result.accepted is False
    assert any(
        issue.reason == "invalid_closure_verdict_schema"
        and issue.path == "$.observations"
        for issue in result.issues
    )


@pytest.mark.parametrize(
    "corruption",
    (
        "rubric_criterion",
        "criterion_result",
        "observation",
        "guidance_reference",
        "confidence",
        "confidence_enum",
        "residual_uncertainty",
    ),
)
def test_closure_verdict_schema_contract_rejects_malformed_nested_shapes(
    corruption: str,
) -> None:
    schema = _closure_verdict_schema()
    properties = schema["properties"]
    assert isinstance(properties, dict)
    if corruption == "rubric_criterion":
        rubric = properties["rubric"]
        assert isinstance(rubric, dict)
        criteria = rubric["properties"]["criteria"]
        criteria["items"]["properties"].pop("evidence_rule")
    elif corruption == "criterion_result":
        criterion_results = properties["criterion_results"]
        assert isinstance(criterion_results, dict)
        criterion_results["items"]["properties"].pop("evidence_refs")
    elif corruption == "observation":
        observations = properties["observations"]
        assert isinstance(observations, dict)
        observations["items"]["properties"]["details"] = {
            "type": "string",
            "min_length": 1,
        }
    elif corruption == "guidance_reference":
        guidance = properties["remediation_guidance"]
        assert isinstance(guidance, dict)
        refs = guidance["items"]["properties"]["criterion_refs"]
        refs["items"]["properties"]["criterion_id"] = {"type": "array"}
    elif corruption == "confidence":
        properties["confidence"] = {"type": "string"}
    elif corruption == "confidence_enum":
        properties["confidence"] = {"enum": (("high",),)}
    else:
        properties["residual_uncertainty"] = {"type": "integer"}

    result = validate_closure_verdict_schema_declaration(schema)

    assert result.accepted is False
    assert result.issues
