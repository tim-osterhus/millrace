"""Shipped stage registry instance loaded from JSON stage-kind assets.

This module is the shipped Millrace stage registry instance.
It loads stage legality data from the canonical JSON stage-kind assets
under ``assets/registry/stage_kinds/`` and exposes typed lookups for
runner normalization, entrypoint linting, graph validation, and
compiled plan materialization.

This module is NOT universal runtime authority for custom or future
stage configurations (see ADR-0013). Custom graphs and compiled plans
derive their authority from graph-loop JSON assets and stage-kind
registry assets, not from this shipped-stage module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .enums import (
    ExecutionStageName,
    ExecutionTerminalResult,
    LearningStageName,
    LearningTerminalResult,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ResultClass,
    StageName,
)
from .terminal_outcomes import TerminalOutcome

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# ---------------------------------------------------------------------------
# Shipped stage-kind asset root (relative to this module)
# ---------------------------------------------------------------------------
_STAGE_KIND_REGISTRY_ROOT = Path(__file__).resolve().parent.parent / "assets" / "registry" / "stage_kinds"
_RUNTIME_STAGE_TO_CANONICAL_STAGE_KIND_ID = {
    "builder": "lad_builder",
    "fixer": "lad_fixer",
    "checker": "lad_checker",
    "doublechecker": "lad_doublechecker",
    "updater": "lad_updater",
    "troubleshooter": "lad_troubleshooter",
    "consultant": "lad_consultant",
    "integrator": "lad_integrator",
    "planner": "lad_planner",
    "manager": "lad_manager",
    "mechanic": "lad_mechanic",
    "auditor": "lad_auditor",
    "arbiter": "lad_arbiter",
}


@dataclass(frozen=True, slots=True)
class StageMetadata:
    """Metadata for one shipped built-in stage."""

    stage: StageName
    plane: Plane
    legal_terminal_results: tuple[str, ...]
    allowed_result_classes_by_outcome: Mapping[str, tuple[ResultClass, ...]]
    running_status_marker: str

    @property
    def legal_terminal_markers(self) -> tuple[str, ...]:
        return tuple(f"### {outcome}" for outcome in self.legal_terminal_results)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_stage_kind_payloads() -> list[dict[str, Any]]:
    """Load all JSON stage-kind assets from the shipped registry root."""
    payloads: list[dict[str, Any]] = []
    if not _STAGE_KIND_REGISTRY_ROOT.is_dir():
        return payloads
    for json_path in sorted(_STAGE_KIND_REGISTRY_ROOT.rglob("*.json")):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == "registered_stage_kind":
            payloads.append(payload)
    return payloads


def _plane_from_value(plane_value: str) -> Plane:
    plane_value = plane_value.strip()
    try:
        return Plane(plane_value)
    except ValueError as exc:
        raise ValueError(f"unknown plane in stage kind asset: {plane_value}") from exc


def _result_class_from_value(value: str) -> ResultClass:
    value = value.strip()
    try:
        return ResultClass(value)
    except ValueError as exc:
        raise ValueError(f"unknown result class in stage kind asset: {value}") from exc


def _resolve_stage_enum(stage_value: str) -> StageName:
    """Resolve a stage value string to its enum member without using the registry."""
    for enum_cls in (ExecutionStageName, PlanningStageName, LearningStageName):
        try:
            return enum_cls(stage_value)
        except ValueError:
            continue
    raise ValueError(f"unknown stage value: {stage_value}")


def _build_stage_metadata_from_payload(payload: dict[str, Any]) -> StageMetadata:
    stage_value = str(payload.get("runtime_stage") or payload["stage_kind_id"])
    plane = _plane_from_value(payload["plane"])
    running_status_marker = str(payload.get("running_status_marker", f"{stage_value.upper()}_RUNNING"))

    legal_outcomes = tuple(
        str(outcome) for outcome in payload.get("legal_outcomes", [])
    )

    raw_result_classes: dict[str, list[str]] = payload.get(
        "allowed_result_classes_by_outcome", {}
    )
    allowed_result_classes: dict[str, tuple[ResultClass, ...]] = {}
    for outcome, classes in raw_result_classes.items():
        allowed_result_classes[str(outcome)] = tuple(
            _result_class_from_value(str(rc)) for rc in classes
        )

    stage_enum = _resolve_stage_enum(stage_value)

    return StageMetadata(
        stage=stage_enum,
        plane=plane,
        legal_terminal_results=legal_outcomes,
        allowed_result_classes_by_outcome=MappingProxyType(allowed_result_classes),
        running_status_marker=running_status_marker,
    )


# ---------------------------------------------------------------------------
# Registry instance – loaded once at module import
# ---------------------------------------------------------------------------

def _build_registry() -> dict[str, StageMetadata]:
    payloads = _load_stage_kind_payloads()
    registry: dict[str, StageMetadata] = {}
    for payload in payloads:
        stage_value = str(payload.get("runtime_stage") or payload.get("stage_kind_id", ""))
        stage_kind_id = str(payload.get("stage_kind_id", ""))
        # Only register stages that correspond to known shipped enum members.
        # Fixture/custom stage kinds (basic_worker, etc.) are not shipped stages.
        try:
            _resolve_stage_enum(stage_value)
        except ValueError:
            continue
        if _RUNTIME_STAGE_TO_CANONICAL_STAGE_KIND_ID.get(stage_value, stage_value) != stage_kind_id:
            continue
        metadata = _build_stage_metadata_from_payload(payload)
        registry[metadata.stage.value] = metadata
    return registry


STAGE_METADATA_BY_VALUE: Mapping[str, StageMetadata] = MappingProxyType(_build_registry())

STAGE_NAME_BY_VALUE: Mapping[str, StageName] = MappingProxyType(
    {metadata.stage.value: metadata.stage for metadata in STAGE_METADATA_BY_VALUE.values()}
)
STAGE_TO_PLANE: Mapping[str, Plane] = MappingProxyType(
    {metadata.stage.value: metadata.plane for metadata in STAGE_METADATA_BY_VALUE.values()}
)
STAGE_LEGAL_TERMINAL_RESULTS: Mapping[str, set[str]] = MappingProxyType(
    {
        metadata.stage.value: set(metadata.legal_terminal_results)
        for metadata in STAGE_METADATA_BY_VALUE.values()
    }
)


# ---------------------------------------------------------------------------
# Public lookup functions (unchanged signatures)
# ---------------------------------------------------------------------------

def stage_metadata(stage: StageName | str) -> StageMetadata:
    stage_value = stage.value if not isinstance(stage, str) else stage
    try:
        return STAGE_METADATA_BY_VALUE[stage_value]
    except KeyError as exc:
        raise ValueError(f"unknown stage: {stage_value}") from exc


def stage_plane(stage: StageName) -> Plane:
    return stage_metadata(stage).plane


def legal_terminal_results(stage: StageName) -> set[str]:
    return set(stage_metadata(stage).legal_terminal_results)


def legal_terminal_markers(stage: StageName) -> tuple[str, ...]:
    return stage_metadata(stage).legal_terminal_markers


def running_status_marker(stage: StageName) -> str:
    return stage_metadata(stage).running_status_marker


def allowed_result_classes_by_outcome(
    stage: StageName,
) -> dict[str, tuple[ResultClass, ...]]:
    return dict(stage_metadata(stage).allowed_result_classes_by_outcome)


def stage_name_for_value(stage_value: str) -> StageName:
    try:
        return STAGE_NAME_BY_VALUE[stage_value]
    except KeyError as exc:
        raise ValueError(f"unknown stage value: {stage_value}") from exc


def stage_name_for_plane(plane: Plane, stage_value: str) -> StageName:
    stage = stage_name_for_value(stage_value)
    if stage_plane(stage) is not plane:
        raise ValueError(f"stage {stage_value!r} does not belong to plane {plane.value}")
    return stage


def known_stage_values() -> set[str]:
    return set(STAGE_METADATA_BY_VALUE)


def known_stage_values_for_plane(plane: Plane) -> set[str]:
    return {
        metadata.stage.value
        for metadata in STAGE_METADATA_BY_VALUE.values()
        if metadata.plane is plane
    }


def terminal_result_for_plane(plane: Plane, token: str) -> TerminalOutcome | None:
    try:
        if plane is Plane.EXECUTION:
            return ExecutionTerminalResult(token)
        if plane is Plane.LEARNING:
            return LearningTerminalResult(token)
        return PlanningTerminalResult(token)
    except ValueError:
        return None


def blocked_terminal_for_plane(plane: Plane) -> TerminalOutcome:
    if plane is Plane.EXECUTION:
        return ExecutionTerminalResult.BLOCKED
    if plane is Plane.LEARNING:
        return LearningTerminalResult.BLOCKED
    return PlanningTerminalResult.BLOCKED


def validate_safe_identifier(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if cleaned != value:
        raise ValueError(f"{field_name} must not include surrounding whitespace")
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    if not SAFE_ID_PATTERN.fullmatch(cleaned):
        raise ValueError(f"{field_name} must match {SAFE_ID_PATTERN.pattern}")
    return cleaned


__all__ = [
    "SAFE_ID_PATTERN",
    "STAGE_METADATA_BY_VALUE",
    "STAGE_LEGAL_TERMINAL_RESULTS",
    "STAGE_NAME_BY_VALUE",
    "STAGE_TO_PLANE",
    "StageMetadata",
    "allowed_result_classes_by_outcome",
    "blocked_terminal_for_plane",
    "known_stage_values",
    "known_stage_values_for_plane",
    "legal_terminal_markers",
    "legal_terminal_results",
    "running_status_marker",
    "stage_metadata",
    "stage_name_for_plane",
    "stage_name_for_value",
    "stage_plane",
    "terminal_result_for_plane",
    "validate_safe_identifier",
]
