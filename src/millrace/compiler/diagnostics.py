"""Compiler diagnostic constructors shared by validation passes."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.contracts import Diagnostic
from millrace.contracts.diagnostics import DiagnosticContextValue

COMPILER_PHASE = "semantic_validation"
ERROR_SEVERITY = "error"
WARNING_SEVERITY = "warning"


def compiler_error(
    *,
    code: str,
    declaration_path: str,
    message: str,
    context: Mapping[str, DiagnosticContextValue],
    hint: str | None = None,
    related_declaration_path: str | None = None,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=ERROR_SEVERITY,
        phase=COMPILER_PHASE,
        declaration_path=declaration_path,
        message=message,
        context=context,
        hint=hint,
        related_declaration_path=related_declaration_path,
    )


def compiler_warning(
    *,
    code: str,
    declaration_path: str,
    message: str,
    context: Mapping[str, DiagnosticContextValue],
    hint: str | None = None,
    related_declaration_path: str | None = None,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=WARNING_SEVERITY,
        phase=COMPILER_PHASE,
        declaration_path=declaration_path,
        message=message,
        context=context,
        hint=hint,
        related_declaration_path=related_declaration_path,
    )


def missing_id_diagnostic(
    *,
    declaration_path: str,
    namespace: str,
    field: str,
) -> Diagnostic:
    return compiler_error(
        code="missing_id",
        declaration_path=declaration_path,
        message=f"{namespace} declaration is missing a non-empty {field}.",
        context={"namespace": namespace, "field": field},
        hint="Provide a non-empty opaque identifier.",
    )


def duplicate_id_diagnostic(
    *,
    declaration_path: str,
    related_declaration_path: str,
    namespace: str,
    duplicate_id: str,
) -> Diagnostic:
    return compiler_error(
        code="duplicate_id",
        declaration_path=declaration_path,
        related_declaration_path=related_declaration_path,
        message=f"{namespace} identifier is declared more than once.",
        context={"namespace": namespace, "duplicate_id": duplicate_id},
        hint="Use a distinct opaque identifier in this namespace.",
    )


def non_nfc_id_diagnostic(
    *,
    declaration_path: str,
    namespace: str,
    identifier: str,
    identifier_nfc: str,
) -> Diagnostic:
    return compiler_error(
        code="non_nfc_id",
        declaration_path=declaration_path,
        message=f"{namespace} identifier is not Unicode NFC.",
        context={
            "namespace": namespace,
            "identifier": identifier,
            "identifier_nfc": identifier_nfc,
        },
        hint=(
            "Use an already-NFC opaque identifier; the compiler will not "
            "normalize it."
        ),
    )


def canonically_equivalent_id_diagnostic(
    *,
    declaration_path: str,
    related_declaration_path: str,
    namespace: str,
    identifier: str,
    canonical_id: str,
) -> Diagnostic:
    return compiler_error(
        code="canonically_equivalent_id",
        declaration_path=declaration_path,
        related_declaration_path=related_declaration_path,
        message=f"{namespace} identifier is canonically equivalent to another id.",
        context={
            "namespace": namespace,
            "identifier": identifier,
            "canonical_id": canonical_id,
        },
        hint=(
            "Use identifiers with distinct NFC forms; the compiler will not pick "
            "one representation for ambiguous source."
        ),
    )


def non_nfc_authority_map_key_diagnostic(
    *,
    declaration_path: str,
    map_key: str,
    map_key_nfc: str,
) -> Diagnostic:
    return compiler_error(
        code="non_nfc_authority_map_key",
        declaration_path=declaration_path,
        message="Authority map key is not Unicode NFC.",
        context={"map_key": map_key, "map_key_nfc": map_key_nfc},
        hint="Use an already-NFC map key; authority data is never normalized.",
    )


def missing_reference_diagnostic(
    *,
    declaration_path: str,
    referrer_path: str,
    reference_kind: str,
    referenced_id: str,
) -> Diagnostic:
    return compiler_error(
        code="missing_reference",
        declaration_path=declaration_path,
        message=f"{reference_kind} reference does not resolve.",
        context={
            "referrer_path": referrer_path,
            "reference_kind": reference_kind,
            "referenced_id": referenced_id,
        },
        hint="Declare the referenced identifier or update the reference.",
    )


def outcome_without_action_diagnostic(
    *,
    declaration_path: str,
    stage_kind_id: str,
    outcome_id: str,
) -> Diagnostic:
    return compiler_error(
        code="outcome_without_action",
        declaration_path=declaration_path,
        message="Declared outcome has no terminal action.",
        context={"stage_kind_id": stage_kind_id, "outcome_id": outcome_id},
        hint="Add a terminal action for this declared outcome.",
    )


def outcome_stage_mismatch_diagnostic(
    *,
    declaration_path: str,
    stage_kind_id: str,
    outcome_id: str,
    outcome_stage_kind_id: str,
) -> Diagnostic:
    return compiler_error(
        code="outcome_stage_mismatch",
        declaration_path=declaration_path,
        message="Declared outcome belongs to a different stage kind.",
        context={
            "stage_kind_id": stage_kind_id,
            "outcome_id": outcome_id,
            "outcome_stage_kind_id": outcome_stage_kind_id,
        },
        hint="Declare only outcomes whose stage kind matches this stage.",
    )


def unsupported_compatibility_profile_diagnostic(
    *,
    declaration_path: str,
    compatibility_profile: str,
) -> Diagnostic:
    return compiler_error(
        code="unsupported_compatibility_profile",
        declaration_path=declaration_path,
        message="Compatibility profiles are not supported by this compiler slice.",
        context={"compatibility_profile": compatibility_profile},
        hint="Remove the compatibility profile from this workflow source.",
    )


def unsupported_required_extensions_diagnostic(
    *,
    declaration_path: str,
    required_extensions: tuple[str, ...],
) -> Diagnostic:
    return compiler_error(
        code="unsupported_required_extensions",
        declaration_path=declaration_path,
        message="Required extensions are not supported by this compiler slice.",
        context={"required_extensions": required_extensions},
        hint="Compile only self-contained workflow source in this slice.",
    )


__all__ = (
    "canonically_equivalent_id_diagnostic",
    "compiler_error",
    "compiler_warning",
    "duplicate_id_diagnostic",
    "missing_id_diagnostic",
    "missing_reference_diagnostic",
    "non_nfc_authority_map_key_diagnostic",
    "non_nfc_id_diagnostic",
    "outcome_stage_mismatch_diagnostic",
    "outcome_without_action_diagnostic",
    "unsupported_compatibility_profile_diagnostic",
    "unsupported_required_extensions_diagnostic",
)
