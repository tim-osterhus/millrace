"""Private operator-wait authority helpers."""

from __future__ import annotations

from collections.abc import Iterable

_OPERATOR_WAIT_RESOLUTION_KIND_ORDER = (
    "resume_recorded_source",
    "close_recorded_source",
    "revise_recorded_source",
)

_SUPPORTED_OPERATOR_WAIT_RESOLUTION_KINDS = frozenset(
    _OPERATOR_WAIT_RESOLUTION_KIND_ORDER
)

_COMMON_OPERATOR_WAIT_AUDIT_REQUIREMENTS = (
    "input_id",
    "input_digest",
    "selected_plan_fingerprint",
    "actor_id",
    "actor_kind",
    "wait_id",
    "operator_wait_id",
    "lineage_id",
)

_RESUME_OPERATOR_WAIT_AUDIT_REQUIREMENTS = (
    *_COMMON_OPERATOR_WAIT_AUDIT_REQUIREMENTS,
    "target_activation_id",
    "empty_payload",
)

_CLOSE_OPERATOR_WAIT_AUDIT_REQUIREMENTS = (
    *_COMMON_OPERATOR_WAIT_AUDIT_REQUIREMENTS,
    "closed_work_item_ids",
    "empty_payload",
)

_REVISE_OPERATOR_WAIT_AUDIT_REQUIREMENTS = (
    *_COMMON_OPERATOR_WAIT_AUDIT_REQUIREMENTS,
    "target_work_item_id",
    "target_activation_id",
    "payload_digest",
    "payload_reference",
)


def _canonical_operator_wait_resolution_kinds(
    allowed_resolution_kinds: Iterable[str],
) -> tuple[str, ...]:
    allowed = set(allowed_resolution_kinds)
    return tuple(
        resolution_kind
        for resolution_kind in _OPERATOR_WAIT_RESOLUTION_KIND_ORDER
        if resolution_kind in allowed
    )


def _canonical_operator_wait_source_action_ids(
    source_action_ids: Iterable[str],
) -> tuple[str, ...]:
    return tuple(sorted(source_action_ids))


def _operator_wait_audit_metadata_requirements(
    allowed_resolution_kinds: Iterable[str],
) -> tuple[str, ...]:
    requirements_by_kind = {
        "resume_recorded_source": _RESUME_OPERATOR_WAIT_AUDIT_REQUIREMENTS,
        "close_recorded_source": _CLOSE_OPERATOR_WAIT_AUDIT_REQUIREMENTS,
        "revise_recorded_source": _REVISE_OPERATOR_WAIT_AUDIT_REQUIREMENTS,
    }
    requirements: list[str] = []
    for resolution_kind in _canonical_operator_wait_resolution_kinds(
        allowed_resolution_kinds
    ):
        for requirement in requirements_by_kind[resolution_kind]:
            if requirement not in requirements:
                requirements.append(requirement)
    return tuple(requirements)


def _operator_wait_record_id(
    *,
    authority_fingerprint: str,
    operator_wait_id: str,
    lineage_id: str,
    created_by_input_id: str,
) -> str:
    return (
        "operator-wait:"
        f"{authority_fingerprint}:"
        f"{operator_wait_id}:"
        f"{lineage_id}:"
        f"{created_by_input_id}"
    )
