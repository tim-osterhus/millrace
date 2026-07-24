"""Workflow identity and required-extension validation.

This module owns source diagnostics for workflow identity fields. It must not
select extensions or load extension-provided defaults.
"""

from __future__ import annotations

from millrace.compiler.authority import unsupported_authority_value_diagnostic
from millrace.compiler.diagnostics import (
    missing_id_diagnostic,
    unsupported_compatibility_profile_diagnostic,
    unsupported_required_extensions_diagnostic,
)
from millrace.compiler.source import SourceRecord, is_non_empty_text, is_sequence
from millrace.contracts import Diagnostic


def validate_workflow_identity(
    workflow: SourceRecord,
    diagnostics: list[Diagnostic],
) -> None:
    if not is_non_empty_text(workflow.get("id")):
        diagnostics.append(
            missing_id_diagnostic(
                declaration_path="workflow.id",
                namespace="workflow",
                field="id",
            )
        )
    if not is_non_empty_text(workflow.get("version")):
        diagnostics.append(
            missing_id_diagnostic(
                declaration_path="workflow.version",
                namespace="workflow",
                field="version",
            )
        )

    compatibility_profile = workflow.get("compatibility_profile")
    if compatibility_profile is not None:
        diagnostics.append(
            unsupported_compatibility_profile_diagnostic(
                declaration_path="workflow.compatibility_profile",
                compatibility_profile=str(compatibility_profile),
            )
        )

    required_extensions = _validate_required_extensions(
        workflow.get("required_extensions", ()),
        diagnostics,
    )
    if required_extensions:
        diagnostics.append(
            unsupported_required_extensions_diagnostic(
                declaration_path="workflow.required_extensions",
                required_extensions=required_extensions,
            )
        )


def _validate_required_extensions(
    value: object,
    diagnostics: list[Diagnostic],
) -> tuple[str, ...]:
    if not is_sequence(value):
        diagnostics.append(
            unsupported_authority_value_diagnostic(
                declaration_path="workflow.required_extensions",
                unsupported_type=type(value).__name__,
                value_kind="value",
            )
        )
        return ()

    required_extensions: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            diagnostics.append(
                unsupported_authority_value_diagnostic(
                    declaration_path=f"workflow.required_extensions[{index}]",
                    unsupported_type=type(item).__name__,
                    value_kind="value",
                )
            )
            continue
        if not item:
            diagnostics.append(
                unsupported_authority_value_diagnostic(
                    declaration_path=f"workflow.required_extensions[{index}]",
                    unsupported_type=type(item).__name__,
                    value_kind="empty_string",
                )
            )
            continue
        required_extensions.append(item)

    return tuple(required_extensions)


__all__ = ("validate_workflow_identity",)
