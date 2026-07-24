"""Artifact schema declaration validation for the compiler.

This module owns selected artifact schema grammar diagnostics. It must not
interpret artifact payloads or construct runtime validation state.
"""

from __future__ import annotations

from collections.abc import Mapping

from millrace.compiler.diagnostics import compiler_error
from millrace.compiler.source import records
from millrace.contracts import Diagnostic
from millrace.contracts.schema import validate_schema_declaration


def validate_artifact_schema_declarations(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    for index, record in enumerate(records(source, "artifact_schemas")):
        schema = record.get("schema")
        schema_id = str(record.get("id", ""))
        if not isinstance(schema, Mapping):
            diagnostics.append(
                _artifact_schema_diagnostic(
                    declaration_path=f"artifact_schemas[{index}].schema",
                    schema_id=schema_id,
                    reason="unsupported_schema_value",
                    detail="schema",
                )
            )
            continue
        result = validate_schema_declaration(schema)
        for issue in result.issues:
            diagnostics.append(
                _artifact_schema_diagnostic(
                    declaration_path=f"artifact_schemas[{index}].schema",
                    schema_id=schema_id,
                    reason=issue.reason,
                    detail=issue.detail,
                )
            )


def _artifact_schema_diagnostic(
    *,
    declaration_path: str,
    schema_id: str,
    reason: str,
    detail: str | None,
) -> Diagnostic:
    return compiler_error(
        code="invalid_artifact_schema",
        declaration_path=declaration_path,
        message="Artifact schema is outside the supported compiler subset.",
        context={
            "schema_id": schema_id,
            "reason": reason,
            "detail": detail,
        },
        hint=(
            "Use only the supported JSON-compatible schema subset for selected "
            "artifact authority."
        ),
    )


__all__ = ("validate_artifact_schema_declarations",)
