"""Shared helpers for persisted policy evidence parsing."""

from __future__ import annotations

from collections.abc import Mapping

from .hooks import PolicyEvaluationRecord, PolicyEvidenceKind


def _policy_evidence_details(
    record: PolicyEvaluationRecord,
    *,
    kind: PolicyEvidenceKind,
) -> dict[str, object] | None:
    return next(
        (
            evidence.details
            for evidence in record.evidence
            if evidence.kind is kind
        ),
        None,
    )


def _require_bool_detail(
    details: Mapping[str, object],
    field_name: str,
    *,
    error_prefix: str,
) -> bool:
    """Reject malformed persisted boolean fields instead of coercing them."""

    value = details.get(field_name)
    if not isinstance(value, bool):
        raise ValueError(f"{error_prefix} field {field_name} must be a boolean")
    return value


__all__ = ["_policy_evidence_details", "_require_bool_detail"]
