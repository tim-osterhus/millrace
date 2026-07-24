"""Schema and projection declaration validation contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    UnsupportedAuthorityValueError,
    freeze_authority_value,
)

SUPPORTED_SCHEMA_KEYS = frozenset(
    (
        "type",
        "required",
        "properties",
        "items",
        "enum",
        "const",
        "min_items",
        "min_length",
        "unique_by",
    )
)
SUPPORTED_SCHEMA_TYPES = frozenset(
    ("object", "array", "string", "integer", "boolean", "null")
)
UNSUPPORTED_SCHEMA_NUMBER_TYPES = frozenset(("number", "float"))
DEFAULT_PROJECTION_SOURCE_ROOTS = frozenset(
    (
        "work_item_payload",
        "artifact_payload",
        "observation_payload",
        "run_metadata",
        "plan_metadata",
    )
)


@dataclass(frozen=True, slots=True)
class SchemaValidationIssue:
    path: str
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class SchemaValidationResult:
    accepted: bool
    issues: tuple[SchemaValidationIssue, ...] = ()


def validate_schema_declaration(schema: Mapping[str, object]) -> SchemaValidationResult:
    issues: list[SchemaValidationIssue] = []
    _validate_schema_declaration(schema, "$", issues)
    return SchemaValidationResult(accepted=not issues, issues=tuple(issues))


def validate_schema(
    schema: Mapping[str, object],
    payload: object,
) -> SchemaValidationResult:
    issues: list[SchemaValidationIssue] = list(
        validate_schema_declaration(schema).issues
    )
    _validate_payload_numbers(payload, "$", issues)
    if not issues:
        _validate_schema_value(schema, payload, "$", issues)
    return SchemaValidationResult(accepted=not issues, issues=tuple(issues))


def _validate_schema_declaration(
    schema: Mapping[str, object],
    path: str,
    issues: list[SchemaValidationIssue],
) -> None:
    for key, value in schema.items():
        if key not in SUPPORTED_SCHEMA_KEYS:
            issues.append(
                SchemaValidationIssue(path, "unsupported_schema_keyword", key)
            )
            continue
        if _contains_float(value):
            issues.append(SchemaValidationIssue(path, "unsupported_schema_number", key))

    schema_type = schema.get("type")
    if "type" in schema:
        if not isinstance(schema_type, str):
            issues.append(
                SchemaValidationIssue(path, "unsupported_schema_value", "type")
            )
        elif schema_type in UNSUPPORTED_SCHEMA_NUMBER_TYPES:
            issues.append(
                SchemaValidationIssue(path, "unsupported_schema_number", schema_type)
            )
        elif schema_type not in SUPPORTED_SCHEMA_TYPES:
            issues.append(SchemaValidationIssue(path, "unsupported_schema_type"))

    required = schema.get("required")
    if "required" in schema and not _string_sequence(required):
        issues.append(
            SchemaValidationIssue(path, "unsupported_schema_value", "required")
        )

    properties = schema.get("properties")
    if "properties" in schema and not isinstance(properties, Mapping):
        issues.append(
            SchemaValidationIssue(path, "unsupported_schema_value", "properties")
        )
    elif isinstance(properties, Mapping):
        for property_name, property_schema in properties.items():
            if isinstance(property_name, str) and isinstance(property_schema, Mapping):
                _validate_schema_declaration(
                    property_schema,
                    f"{path}.{property_name}",
                    issues,
                )
            else:
                issues.append(
                    SchemaValidationIssue(
                        path,
                        "unsupported_schema_value",
                        "properties",
                    )
                )

    items = schema.get("items")
    if "items" in schema and not isinstance(items, Mapping):
        issues.append(SchemaValidationIssue(path, "unsupported_schema_value", "items"))
    elif isinstance(items, Mapping):
        _validate_schema_declaration(items, f"{path}[]", issues)

    enum_values = schema.get("enum")
    if "enum" in schema and not _schema_enum_ok(enum_values):
        issues.append(SchemaValidationIssue(path, "unsupported_schema_value", "enum"))

    if "const" in schema and not _schema_scalar_ok(schema["const"]):
        issues.append(SchemaValidationIssue(path, "unsupported_schema_value", "const"))

    min_items = schema.get("min_items")
    if "min_items" in schema and type(min_items) is not int:
        issues.append(
            SchemaValidationIssue(path, "unsupported_schema_value", "min_items")
        )

    min_length = schema.get("min_length")
    if "min_length" in schema and type(min_length) is not int:
        issues.append(
            SchemaValidationIssue(path, "unsupported_schema_value", "min_length")
        )

    _validate_unique_by_declaration(schema, path, issues)


def _validate_payload_numbers(
    value: object,
    path: str,
    issues: list[SchemaValidationIssue],
) -> None:
    if isinstance(value, float):
        issues.append(SchemaValidationIssue(path, "unsupported_payload_number"))
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _validate_payload_numbers(nested, f"{path}.{key}", issues)
        return
    if _is_sequence(value):
        for index, nested in enumerate(value):
            _validate_payload_numbers(nested, f"{path}[{index}]", issues)


def _validate_unique_by_declaration(
    schema: Mapping[str, object],
    path: str,
    issues: list[SchemaValidationIssue],
) -> None:
    unique_by = schema.get("unique_by")
    if "unique_by" not in schema:
        return
    if not isinstance(unique_by, str) or not unique_by:
        issues.append(
            SchemaValidationIssue(path, "unsupported_schema_value", "unique_by")
        )
        return
    if schema.get("type") != "array":
        issues.append(
            SchemaValidationIssue(path, "unsupported_schema_value", "unique_by")
        )
        return
    items = schema.get("items")
    if not isinstance(items, Mapping) or items.get("type") != "object":
        issues.append(
            SchemaValidationIssue(path, "unsupported_schema_value", "unique_by")
        )
        return
    properties = items.get("properties")
    required = items.get("required", ())
    property_schema = (
        properties.get(unique_by) if isinstance(properties, Mapping) else None
    )
    if (
        not isinstance(properties, Mapping)
        or unique_by not in properties
        or not _is_sequence(required)
        or unique_by not in required
        or not isinstance(property_schema, Mapping)
        or property_schema.get("type") not in {"string", "integer", "boolean"}
    ):
        issues.append(
            SchemaValidationIssue(path, "unsupported_schema_value", "unique_by")
        )


def _validate_schema_value(
    schema: Mapping[str, object],
    value: object,
    path: str,
    issues: list[SchemaValidationIssue],
) -> None:
    if "const" in schema and not _scalar_equal(value, schema["const"]):
        issues.append(SchemaValidationIssue(path, "const_mismatch"))
    enum_values = schema.get("enum")
    if _is_sequence(enum_values) and not any(
        _scalar_equal(value, enum_value) for enum_value in enum_values
    ):
        issues.append(SchemaValidationIssue(path, "enum_mismatch"))

    schema_type = schema.get("type")
    if schema_type == "object":
        _validate_schema_object(schema, value, path, issues)
    elif schema_type == "array":
        _validate_schema_array(schema, value, path, issues)
    elif schema_type == "string":
        _validate_schema_string(schema, value, path, issues)
    elif schema_type == "integer":
        if type(value) is not int:
            issues.append(SchemaValidationIssue(path, "type_mismatch"))
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            issues.append(SchemaValidationIssue(path, "type_mismatch"))
    elif schema_type == "null" and value is not None:
        issues.append(SchemaValidationIssue(path, "type_mismatch"))


def _validate_schema_object(
    schema: Mapping[str, object],
    value: object,
    path: str,
    issues: list[SchemaValidationIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(SchemaValidationIssue(path, "type_mismatch"))
        return

    required = schema.get("required", ())
    if _is_sequence(required):
        for property_name in required:
            if isinstance(property_name, str) and property_name not in value:
                issues.append(
                    SchemaValidationIssue(
                        f"{path}.{property_name}",
                        "missing_required_property",
                    )
                )

    raw_properties = schema.get("properties", {})
    properties = raw_properties if isinstance(raw_properties, Mapping) else {}
    for property_name, property_value in value.items():
        if not isinstance(property_name, str) or property_name not in properties:
            issues.append(
                SchemaValidationIssue(
                    f"{path}.{property_name}",
                    "unexpected_property",
                )
            )
            continue
        property_schema = properties[property_name]
        if isinstance(property_schema, Mapping):
            _validate_schema_value(
                property_schema,
                property_value,
                f"{path}.{property_name}",
                issues,
            )


def _validate_schema_array(
    schema: Mapping[str, object],
    value: object,
    path: str,
    issues: list[SchemaValidationIssue],
) -> None:
    if not _is_sequence(value):
        issues.append(SchemaValidationIssue(path, "type_mismatch"))
        return

    min_items = schema.get("min_items")
    if type(min_items) is int and len(value) < min_items:
        issues.append(SchemaValidationIssue(path, "array_too_short"))

    item_schema = schema.get("items")
    if isinstance(item_schema, Mapping):
        for index, item in enumerate(value):
            _validate_schema_value(item_schema, item, f"{path}[{index}]", issues)
    unique_by = schema.get("unique_by")
    if isinstance(unique_by, str) and unique_by:
        seen: set[str] = set()
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                continue
            unique_value = item.get(unique_by)
            key = f"{type(unique_value).__name__}:{unique_value!r}"
            if key in seen:
                issues.append(
                    SchemaValidationIssue(
                        f"{path}[{index}].{unique_by}",
                        "array_duplicate_unique_key",
                    )
                )
                continue
            seen.add(key)


def _validate_schema_string(
    schema: Mapping[str, object],
    value: object,
    path: str,
    issues: list[SchemaValidationIssue],
) -> None:
    if not isinstance(value, str):
        issues.append(SchemaValidationIssue(path, "type_mismatch"))
        return

    min_length = schema.get("min_length")
    if type(min_length) is int and len(value) < min_length:
        issues.append(SchemaValidationIssue(path, "string_too_short"))


@dataclass(frozen=True, slots=True)
class ProjectionEvaluationError:
    path: tuple[str, ...]
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectionEvaluationResult:
    accepted: bool
    value: AuthorityValue | None = None
    error: ProjectionEvaluationError | None = None


@dataclass(frozen=True, slots=True)
class ProjectionDeclarationIssue:
    path: tuple[str, ...]
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectionDeclarationResult:
    accepted: bool
    issues: tuple[ProjectionDeclarationIssue, ...] = ()


def validate_projection_declaration(
    projection: object,
    *,
    allowed_source_roots: Iterable[str] = DEFAULT_PROJECTION_SOURCE_ROOTS,
) -> ProjectionDeclarationResult:
    issues: list[ProjectionDeclarationIssue] = []
    allowed_roots = frozenset(allowed_source_roots)
    _validate_projection_declaration(projection, allowed_roots, (), issues)
    return ProjectionDeclarationResult(accepted=not issues, issues=tuple(issues))


def _validate_projection_declaration(
    projection: object,
    allowed_source_roots: frozenset[str],
    path: tuple[str, ...],
    issues: list[ProjectionDeclarationIssue],
) -> None:
    if not isinstance(projection, Mapping):
        issues.append(ProjectionDeclarationIssue(path, "unsupported_projection"))
        return

    kind = projection.get("kind")
    if kind == "literal":
        _validate_projection_keys(
            projection,
            required=frozenset(("kind", "value")),
            allowed=frozenset(("kind", "value")),
            path=path,
            issues=issues,
        )
        _validate_projection_literal(projection.get("value"), path, issues)
        return
    if kind == "source":
        _validate_projection_keys(
            projection,
            required=frozenset(("kind", "path")),
            allowed=frozenset(("kind", "path")),
            path=path,
            issues=issues,
        )
        _validate_projection_source(
            projection.get("path"),
            allowed_source_roots,
            path,
            issues,
        )
        return
    if kind == "object":
        _validate_projection_keys(
            projection,
            required=frozenset(("kind", "fields")),
            allowed=frozenset(("kind", "fields")),
            path=path,
            issues=issues,
        )
        _validate_projection_object(
            projection.get("fields"),
            allowed_source_roots,
            path,
            issues,
        )
        return
    if kind == "array":
        _validate_projection_keys(
            projection,
            required=frozenset(("kind", "items")),
            allowed=frozenset(("kind", "items")),
            path=path,
            issues=issues,
        )
        _validate_projection_array(
            projection.get("items"),
            allowed_source_roots,
            path,
            issues,
        )
        return
    issues.append(ProjectionDeclarationIssue(path, "unsupported_projection"))


def _validate_projection_keys(
    projection: Mapping[object, object],
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    path: tuple[str, ...],
    issues: list[ProjectionDeclarationIssue],
) -> None:
    keys = set(projection)
    for required_key in sorted(required):
        if required_key not in keys:
            issues.append(
                ProjectionDeclarationIssue(
                    path,
                    "missing_projection_key",
                    required_key,
                )
            )
    for actual_key in sorted(keys, key=str):
        if not isinstance(actual_key, str) or actual_key not in allowed:
            issues.append(
                ProjectionDeclarationIssue(
                    path,
                    "unsupported_projection_key",
                    str(actual_key),
                )
            )


def _validate_projection_literal(
    value: object,
    path: tuple[str, ...],
    issues: list[ProjectionDeclarationIssue],
) -> None:
    try:
        freeze_authority_value(value)
    except UnsupportedAuthorityValueError:
        issues.append(
            ProjectionDeclarationIssue(
                path,
                "unsupported_projection_value",
                type(value).__name__,
            )
        )


def _validate_projection_source(
    raw_path: object,
    allowed_source_roots: frozenset[str],
    path: tuple[str, ...],
    issues: list[ProjectionDeclarationIssue],
) -> None:
    source_path = _path_parts(raw_path)
    if source_path is None:
        issues.append(ProjectionDeclarationIssue(path, "unsupported_projection_path"))
        return
    root_name = source_path[0]
    if root_name not in allowed_source_roots:
        issues.append(
            ProjectionDeclarationIssue(path, "unknown_source_root", root_name)
        )


def _validate_projection_object(
    fields: object,
    allowed_source_roots: frozenset[str],
    path: tuple[str, ...],
    issues: list[ProjectionDeclarationIssue],
) -> None:
    if not isinstance(fields, Mapping):
        issues.append(ProjectionDeclarationIssue(path, "unsupported_projection"))
        return
    for field_name, field_projection in fields.items():
        if not isinstance(field_name, str):
            issues.append(ProjectionDeclarationIssue(path, "unsupported_projection"))
            continue
        _validate_projection_declaration(
            field_projection,
            allowed_source_roots,
            (*path, field_name),
            issues,
        )


def _validate_projection_array(
    items: object,
    allowed_source_roots: frozenset[str],
    path: tuple[str, ...],
    issues: list[ProjectionDeclarationIssue],
) -> None:
    if not _is_sequence(items):
        issues.append(ProjectionDeclarationIssue(path, "unsupported_projection"))
        return
    for index, item in enumerate(items):
        _validate_projection_declaration(
            item,
            allowed_source_roots,
            (*path, str(index)),
            issues,
        )


def _contains_float(value: object) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, Mapping):
        return any(_contains_float(nested) for nested in value.values())
    if _is_sequence(value):
        return any(_contains_float(nested) for nested in value)
    return False


def _schema_enum_ok(value: object) -> bool:
    return _is_sequence(value) and all(_schema_scalar_ok(nested) for nested in value)


def _schema_scalar_ok(value: object) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if type(value) is int:
        return True
    return False


def _scalar_equal(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _string_sequence(value: object) -> bool:
    return _is_sequence(value) and all(isinstance(item, str) for item in value)


def _path_parts(value: object) -> tuple[str, ...] | None:
    if not _is_sequence(value):
        return None
    source_path: list[str] = []
    for part in value:
        if not isinstance(part, str):
            return None
        source_path.append(part)
    if not source_path:
        return None
    return tuple(source_path)


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


__all__ = (
    "DEFAULT_PROJECTION_SOURCE_ROOTS",
    "ProjectionDeclarationIssue",
    "ProjectionDeclarationResult",
    "ProjectionEvaluationError",
    "ProjectionEvaluationResult",
    "SchemaValidationIssue",
    "SchemaValidationResult",
    "SUPPORTED_SCHEMA_KEYS",
    "SUPPORTED_SCHEMA_TYPES",
    "UNSUPPORTED_SCHEMA_NUMBER_TYPES",
    "validate_projection_declaration",
    "validate_schema",
    "validate_schema_declaration",
)
