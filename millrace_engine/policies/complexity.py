"""Deterministic complexity-band normalization and model-profile selection."""

from __future__ import annotations

from ..config import (
    ComplexityBand,
    ComplexityRouteSelection,
    ComplexitySelectionReason,
    EngineConfig,
)
from ..contracts import StageType
from ..execution_nodes import execution_stage_type_for_node


_BAND_TOKENS = {
    "MODERATE": ComplexityBand.MODERATE,
    "INVOLVED": ComplexityBand.INVOLVED,
    "COMPLEX": ComplexityBand.COMPLEX,
}
_BUILDER_CHAIN_STAGE_TYPES = (
    StageType.BUILDER,
    StageType.LARGE_PLAN,
    StageType.LARGE_EXECUTE,
    StageType.REASSESS,
    StageType.REFACTOR,
)
_QA_CHAIN_STAGE_TYPES = (
    StageType.QA,
    StageType.HOTFIX,
    StageType.DOUBLECHECK,
)
COMPLEXITY_ROUTED_STAGE_TYPES = _BUILDER_CHAIN_STAGE_TYPES + _QA_CHAIN_STAGE_TYPES


def normalize_complexity_band(
    value: str | None,
    *,
    default_band: ComplexityBand = ComplexityBand.MODERATE,
) -> ComplexityBand:
    """Normalize one task-card complexity token into a routing band."""

    if value is None:
        return default_band
    normalized = " ".join(value.strip().split()).upper()
    if not normalized or normalized == "UNKNOWN":
        return default_band
    return _BAND_TOKENS.get(normalized, default_band)


def routed_stage_types_for_node_ids(
    node_ids: tuple[str, ...] | list[str],
) -> tuple[StageType, ...]:
    """Return the routed execution stage types present in one compiled loop."""

    routed: list[StageType] = []
    seen: set[StageType] = set()
    for node_id in node_ids:
        stage_type = execution_stage_type_for_node(str(node_id).strip().lower())
        if stage_type is None or stage_type not in COMPLEXITY_ROUTED_STAGE_TYPES or stage_type in seen:
            continue
        seen.add(stage_type)
        routed.append(stage_type)
    return tuple(routed)


def routed_node_ids_for_node_ids(
    node_ids: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """Return only the execution node ids that participate in complexity routing."""

    routed: list[str] = []
    seen: set[str] = set()
    for node_id in node_ids:
        normalized = str(node_id).strip().lower()
        stage_type = execution_stage_type_for_node(normalized)
        if (
            not normalized
            or stage_type is None
            or stage_type not in COMPLEXITY_ROUTED_STAGE_TYPES
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        routed.append(normalized)
    return tuple(routed)


def select_complexity_route(
    config: EngineConfig,
    *,
    task_complexity: str | None,
    node_ids: tuple[str, ...] | list[str],
) -> ComplexityRouteSelection:
    """Resolve the effective complexity route for one execution loop selection."""

    routed_stage_types = routed_stage_types_for_node_ids(node_ids)
    routed_node_ids = routed_node_ids_for_node_ids(node_ids)
    default_band = config.policies.complexity.default_band
    normalized_task = None if task_complexity is None else " ".join(task_complexity.strip().split()).upper() or None
    if normalized_task in _BAND_TOKENS:
        band = _BAND_TOKENS[normalized_task]
        reason = ComplexitySelectionReason.TASK_COMPLEXITY
    else:
        band = default_band
        reason = ComplexitySelectionReason.DEFAULT_BAND

    if not config.policies.complexity.enabled:
        return ComplexityRouteSelection(
            enabled=False,
            task_complexity=normalized_task,
            band=band,
            reason=ComplexitySelectionReason.DISABLED,
            routed_node_ids=routed_node_ids,
            routed_stage_types=routed_stage_types,
        )

    if not routed_stage_types:
        return ComplexityRouteSelection(
            enabled=True,
            task_complexity=normalized_task,
            band=band,
            reason=ComplexitySelectionReason.NO_ROUTED_STAGES,
            routed_node_ids=(),
            routed_stage_types=(),
        )

    profile_refs = config.policies.complexity.profiles
    selected_model_profile_ref = getattr(profile_refs, band.value)
    return ComplexityRouteSelection(
        enabled=True,
        task_complexity=normalized_task,
        band=band,
        reason=reason,
        selected_model_profile_ref=selected_model_profile_ref,
        routed_node_ids=routed_node_ids,
        routed_stage_types=routed_stage_types,
    )


__all__ = [
    "COMPLEXITY_ROUTED_STAGE_TYPES",
    "normalize_complexity_band",
    "routed_node_ids_for_node_ids",
    "routed_stage_types_for_node_ids",
    "select_complexity_route",
]
