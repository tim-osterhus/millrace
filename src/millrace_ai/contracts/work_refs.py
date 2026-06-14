"""Family-id based work item reference helpers."""

from __future__ import annotations

from .enums import Plane, WorkItemKind
from .stage_metadata import validate_safe_identifier

_LEGACY_KIND_BY_FAMILY_ID: dict[str, WorkItemKind] = {
    WorkItemKind.TASK.value: WorkItemKind.TASK,
    WorkItemKind.PROBE.value: WorkItemKind.PROBE,
    WorkItemKind.SPEC.value: WorkItemKind.SPEC,
    WorkItemKind.INCIDENT.value: WorkItemKind.INCIDENT,
    WorkItemKind.LEARNING_REQUEST.value: WorkItemKind.LEARNING_REQUEST,
}

_PLANE_BY_FAMILY_ID: dict[str, Plane] = {
    WorkItemKind.TASK.value: Plane.EXECUTION,
    WorkItemKind.PROBE.value: Plane.PLANNING,
    WorkItemKind.SPEC.value: Plane.PLANNING,
    WorkItemKind.INCIDENT.value: Plane.PLANNING,
    WorkItemKind.LEARNING_REQUEST.value: Plane.LEARNING,
}


def normalize_work_item_family_id(value: str, *, field_name: str = "work_item_family_id") -> str:
    return validate_safe_identifier(value, field_name=field_name)


def family_id_for_work_item_kind(kind: WorkItemKind | str | None) -> str | None:
    if kind is None:
        return None
    if isinstance(kind, WorkItemKind):
        return kind.value
    try:
        return WorkItemKind(kind).value
    except ValueError:
        return normalize_work_item_family_id(kind, field_name="work_item_kind")


def legacy_work_item_kind_for_family_id(family_id: str | None) -> WorkItemKind | None:
    if family_id is None:
        return None
    normalized = normalize_work_item_family_id(family_id)
    return _LEGACY_KIND_BY_FAMILY_ID.get(normalized)


def plane_for_work_item_family_id(family_id: str | None) -> Plane | None:
    if family_id is None:
        return None
    normalized = normalize_work_item_family_id(family_id)
    return _PLANE_BY_FAMILY_ID.get(normalized)


def coerce_family_and_kind(
    *,
    family_id: str | None,
    work_item_kind: WorkItemKind | str | None,
) -> tuple[str | None, WorkItemKind | None]:
    kind = None
    if work_item_kind is not None:
        if isinstance(work_item_kind, WorkItemKind):
            kind = work_item_kind
        else:
            try:
                kind = WorkItemKind(work_item_kind)
            except ValueError:
                kind = None
    normalized_family = normalize_work_item_family_id(family_id) if family_id is not None else None
    if normalized_family is None:
        if kind is not None:
            normalized_family = family_id_for_work_item_kind(kind)
        elif isinstance(work_item_kind, str):
            normalized_family = normalize_work_item_family_id(work_item_kind, field_name="work_item_kind")
    if kind is None and normalized_family is not None:
        kind = legacy_work_item_kind_for_family_id(normalized_family)
    if kind is not None and normalized_family is not None and kind.value != normalized_family:
        raise ValueError("work_item_family_id must agree with work_item_kind")
    return normalized_family, kind


__all__ = [
    "coerce_family_and_kind",
    "family_id_for_work_item_kind",
    "legacy_work_item_kind_for_family_id",
    "normalize_work_item_family_id",
    "plane_for_work_item_family_id",
]
