from __future__ import annotations

from millrace.contracts.schema import validate_schema, validate_schema_declaration


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
