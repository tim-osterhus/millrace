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
CLOSURE_VERDICT_REQUIRED_TOP_LEVEL_PROPERTIES = frozenset(
    {
        "artifact_kind",
        "summary",
        "closure_target_id",
        "root_contract_digest",
        "freshness_anchor_digest",
        "rubric",
        "criterion_results",
        "observations",
        "remediation_guidance",
        "confidence",
        "residual_uncertainty",
    }
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


def validate_closure_verdict_schema_declaration(
    schema: object,
) -> SchemaValidationResult:
    """Validate the complete generic closure-verdict schema contract."""
    issues: list[SchemaValidationIssue] = []
    if not isinstance(schema, Mapping):
        issues.append(
            SchemaValidationIssue(
                "$",
                "unsupported_schema_value",
                "closure_verdict_schema",
            )
        )
        return SchemaValidationResult(accepted=False, issues=tuple(issues))

    issues.extend(validate_schema_declaration(schema).issues)
    _validate_closure_verdict_schema_shape(schema, issues)
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


def _closure_object_shape(
    required: Iterable[str],
    properties: Mapping[str, object],
    *,
    root: bool = False,
) -> Mapping[str, object]:
    shape: dict[str, object] = {
        "kind": "object",
        "required": frozenset(required),
        "properties": properties,
    }
    if root:
        shape["root"] = True
    return shape


def _closure_array_shape(
    items: Mapping[str, object],
    *,
    unique_by: str | None = None,
    min_items: int | None = None,
) -> Mapping[str, object]:
    shape: dict[str, object] = {"kind": "array", "items": items}
    if unique_by is not None:
        shape["unique_by"] = unique_by
    if min_items is not None:
        shape["min_items"] = min_items
    return shape


def _closure_enum_shape(values: Iterable[str]) -> Mapping[str, object]:
    return {"kind": "enum", "values": frozenset(values)}


_CLOSURE_STRING_SHAPE: Mapping[str, object] = {"kind": "string"}
_CLOSURE_CRITERION_SHAPE = _closure_object_shape(
    ("criterion_id", "requirement", "evidence_rule"),
    {
        "criterion_id": _CLOSURE_STRING_SHAPE,
        "requirement": _CLOSURE_STRING_SHAPE,
        "evidence_rule": _CLOSURE_STRING_SHAPE,
    },
)
_CLOSURE_EVIDENCE_REF_SHAPE = _closure_object_shape(
    ("evidence_id", "summary"),
    {"evidence_id": _CLOSURE_STRING_SHAPE, "summary": _CLOSURE_STRING_SHAPE},
)
_CLOSURE_CRITERION_RESULT_SHAPE = _closure_object_shape(
    ("criterion_id", "status", "provenance", "evidence_refs"),
    {
        "criterion_id": _CLOSURE_STRING_SHAPE,
        "status": _closure_enum_shape(("passed", "failed", "blocked")),
        "provenance": _closure_enum_shape(
            ("fresh", "revalidated", "historical_only", "missing")
        ),
        "evidence_refs": _closure_array_shape(
            _CLOSURE_EVIDENCE_REF_SHAPE,
            unique_by="evidence_id",
        ),
    },
)
_CLOSURE_OBSERVATION_SHAPE = _closure_object_shape(
    ("observation_id", "summary"),
    {"observation_id": _CLOSURE_STRING_SHAPE, "summary": _CLOSURE_STRING_SHAPE},
)
_CLOSURE_GUIDANCE_REF_SHAPE = _closure_object_shape(
    ("criterion_id",),
    {"criterion_id": _CLOSURE_STRING_SHAPE},
)
_CLOSURE_GUIDANCE_SHAPE = _closure_object_shape(
    ("guidance_id", "summary", "criterion_refs"),
    {
        "guidance_id": _CLOSURE_STRING_SHAPE,
        "summary": _CLOSURE_STRING_SHAPE,
        "criterion_refs": _closure_array_shape(
            _CLOSURE_GUIDANCE_REF_SHAPE,
            unique_by="criterion_id",
            min_items=1,
        ),
    },
)
_CLOSURE_VERDICT_SHAPE = _closure_object_shape(
    CLOSURE_VERDICT_REQUIRED_TOP_LEVEL_PROPERTIES,
    {
        "artifact_kind": _CLOSURE_STRING_SHAPE,
        "summary": _CLOSURE_STRING_SHAPE,
        "closure_target_id": _CLOSURE_STRING_SHAPE,
        "root_contract_digest": _CLOSURE_STRING_SHAPE,
        "freshness_anchor_digest": _CLOSURE_STRING_SHAPE,
        "rubric": _closure_object_shape(
            ("criteria",),
            {
                "criteria": _closure_array_shape(
                    _CLOSURE_CRITERION_SHAPE,
                    unique_by="criterion_id",
                    min_items=1,
                )
            },
        ),
        "criterion_results": _closure_array_shape(
            _CLOSURE_CRITERION_RESULT_SHAPE,
            unique_by="criterion_id",
            min_items=1,
        ),
        "observations": _closure_array_shape(
            _CLOSURE_OBSERVATION_SHAPE,
            unique_by="observation_id",
        ),
        "remediation_guidance": _closure_array_shape(
            _CLOSURE_GUIDANCE_SHAPE,
            unique_by="guidance_id",
        ),
        "confidence": _closure_enum_shape(("high", "medium", "low")),
        "residual_uncertainty": _CLOSURE_STRING_SHAPE,
    },
    root=True,
)


def _validate_closure_verdict_schema_shape(
    schema: Mapping[object, object],
    issues: list[SchemaValidationIssue],
) -> None:
    _compare_closure_schema(schema, _CLOSURE_VERDICT_SHAPE, "$", issues)


def _compare_closure_schema(
    schema: object,
    shape: Mapping[str, object],
    path: str,
    issues: list[SchemaValidationIssue],
) -> None:
    kind = shape.get("kind")
    if kind == "string":
        if (
            not isinstance(schema, Mapping)
            or set(schema) != {"type", "min_length"}
            or schema.get("type") != "string"
            or schema.get("min_length") != 1
        ):
            _closure_schema_issue(issues, path, "nonblank_string")
        return
    if kind == "enum":
        values = shape.get("values")
        raw_values = schema.get("enum") if isinstance(schema, Mapping) else None
        if (
            not isinstance(schema, Mapping)
            or set(schema) != {"enum"}
            or not _is_sequence(raw_values)
            or not isinstance(values, frozenset)
            or any(not isinstance(item, str) for item in raw_values)
            or set(raw_values) != set(values)
        ):
            _closure_schema_issue(issues, path, "enum")
        return
    if kind == "array":
        if not isinstance(schema, Mapping):
            _closure_schema_issue(issues, path, "array")
            return
        expected_keys = {"type", "items"}
        for key in ("unique_by", "min_items"):
            if key in shape:
                expected_keys.add(key)
        if set(schema) != expected_keys or schema.get("type") != "array":
            _closure_schema_issue(issues, path, "array")
        for key in ("unique_by", "min_items"):
            if key in shape and schema.get(key) != shape[key]:
                _closure_schema_issue(issues, path, "array")
        items = shape.get("items")
        if isinstance(items, Mapping):
            _compare_closure_schema(schema.get("items"), items, f"{path}[]", issues)
        return
    if kind != "object":
        _closure_schema_issue(issues, path, "unsupported_shape")
        return
    if not isinstance(schema, Mapping):
        _closure_schema_issue(issues, path, "object")
        return
    if set(schema) != {"type", "required", "properties"}:
        _closure_schema_issue(issues, path, "object_keys")
    if schema.get("type") != "object":
        _closure_schema_issue(issues, path, "object_type")

    raw_required = schema.get("required")
    required = (
        tuple(raw_required)
        if _is_sequence(raw_required)
        and all(isinstance(item, str) for item in raw_required)
        else ()
    )
    if not required and raw_required not in ((), []):
        _closure_schema_issue(issues, path, "required")
    required_set = set(required)
    if len(required) != len(required_set):
        _closure_schema_issue(issues, path, "duplicate_required_property")
    expected_required = shape.get("required")
    if not isinstance(expected_required, frozenset):
        _closure_schema_issue(issues, path, "unsupported_shape")
        return
    if shape.get("root") is True:
        required_ok = expected_required.issubset(required_set)
    else:
        required_ok = required_set == set(expected_required)
    if not required_ok:
        _closure_schema_issue(issues, path, "required_properties")

    raw_properties = schema.get("properties")
    if not isinstance(raw_properties, Mapping):
        _closure_schema_issue(issues, path, "properties")
        return
    expected_properties = shape.get("properties")
    if not isinstance(expected_properties, Mapping):
        _closure_schema_issue(issues, path, "unsupported_shape")
        return
    actual_names = set(raw_properties)
    expected_names = set(expected_properties)
    if shape.get("root") is True:
        if not expected_names.issubset(actual_names):
            _closure_schema_issue(issues, path, "properties")
    elif actual_names != expected_names:
        _closure_schema_issue(issues, path, "nested_properties")
    if not required_set.issubset(actual_names):
        _closure_schema_issue(issues, path, "required_properties")
    for name, child_shape in expected_properties.items():
        if isinstance(child_shape, Mapping) and name in raw_properties:
            _compare_closure_schema(
                raw_properties[name],
                child_shape,
                f"{path}.{name}",
                issues,
            )


def _closure_schema_issue(
    issues: list[SchemaValidationIssue],
    path: str,
    detail: str,
) -> None:
    issues.append(SchemaValidationIssue(path, "invalid_closure_verdict_schema", detail))


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
    if kind == "coalesce":
        _validate_projection_keys(
            projection,
            required=frozenset(("kind", "candidates", "default")),
            allowed=frozenset(("kind", "candidates", "default")),
            path=path,
            issues=issues,
        )
        candidates = projection.get("candidates")
        if not _is_sequence(candidates):
            issues.append(
                ProjectionDeclarationIssue(
                    (*path, "candidates"),
                    "unsupported_projection",
                )
            )
        elif not candidates:
            issues.append(
                ProjectionDeclarationIssue(
                    (*path, "candidates"),
                    "coalesce_candidates_empty",
                )
            )
        else:
            for index, candidate in enumerate(candidates):
                _validate_projection_declaration(
                    candidate,
                    allowed_source_roots,
                    (*path, "candidates", str(index)),
                    issues,
                )
        _validate_projection_declaration(
            projection.get("default"),
            allowed_source_roots,
            (*path, "default"),
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
    "CLOSURE_VERDICT_REQUIRED_TOP_LEVEL_PROPERTIES",
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
    "validate_closure_verdict_schema_declaration",
    "validate_projection_declaration",
    "validate_schema",
    "validate_schema_declaration",
)
