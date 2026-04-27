"""Stage ownership and terminal-result metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

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
    TerminalResult,
)

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class StageMetadata:
    stage: StageName
    plane: Plane
    legal_terminal_results: tuple[str, ...]
    allowed_result_classes_by_outcome: Mapping[str, tuple[ResultClass, ...]]

    @property
    def running_status_marker(self) -> str:
        return f"{self.stage.value.upper()}_RUNNING"

    @property
    def legal_terminal_markers(self) -> tuple[str, ...]:
        return tuple(f"### {outcome}" for outcome in self.legal_terminal_results)


def _allowed(
    values: dict[str, tuple[ResultClass, ...]],
) -> Mapping[str, tuple[ResultClass, ...]]:
    return MappingProxyType(values)


STAGE_METADATA_BY_VALUE: Mapping[str, StageMetadata] = MappingProxyType(
    {
        ExecutionStageName.BUILDER.value: StageMetadata(
            stage=ExecutionStageName.BUILDER,
            plane=Plane.EXECUTION,
            legal_terminal_results=(
                ExecutionTerminalResult.BUILDER_COMPLETE.value,
                ExecutionTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    ExecutionTerminalResult.BUILDER_COMPLETE.value: (ResultClass.SUCCESS,),
                    ExecutionTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        ExecutionStageName.CHECKER.value: StageMetadata(
            stage=ExecutionStageName.CHECKER,
            plane=Plane.EXECUTION,
            legal_terminal_results=(
                ExecutionTerminalResult.CHECKER_PASS.value,
                ExecutionTerminalResult.FIX_NEEDED.value,
                ExecutionTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    ExecutionTerminalResult.CHECKER_PASS.value: (ResultClass.SUCCESS,),
                    ExecutionTerminalResult.FIX_NEEDED.value: (ResultClass.FOLLOWUP_NEEDED,),
                    ExecutionTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        ExecutionStageName.FIXER.value: StageMetadata(
            stage=ExecutionStageName.FIXER,
            plane=Plane.EXECUTION,
            legal_terminal_results=(
                ExecutionTerminalResult.FIXER_COMPLETE.value,
                ExecutionTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    ExecutionTerminalResult.FIXER_COMPLETE.value: (ResultClass.SUCCESS,),
                    ExecutionTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        ExecutionStageName.DOUBLECHECKER.value: StageMetadata(
            stage=ExecutionStageName.DOUBLECHECKER,
            plane=Plane.EXECUTION,
            legal_terminal_results=(
                ExecutionTerminalResult.DOUBLECHECK_PASS.value,
                ExecutionTerminalResult.FIX_NEEDED.value,
                ExecutionTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    ExecutionTerminalResult.DOUBLECHECK_PASS.value: (ResultClass.SUCCESS,),
                    ExecutionTerminalResult.FIX_NEEDED.value: (ResultClass.FOLLOWUP_NEEDED,),
                    ExecutionTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        ExecutionStageName.UPDATER.value: StageMetadata(
            stage=ExecutionStageName.UPDATER,
            plane=Plane.EXECUTION,
            legal_terminal_results=(
                ExecutionTerminalResult.UPDATE_COMPLETE.value,
                ExecutionTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    ExecutionTerminalResult.UPDATE_COMPLETE.value: (ResultClass.SUCCESS,),
                    ExecutionTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        ExecutionStageName.TROUBLESHOOTER.value: StageMetadata(
            stage=ExecutionStageName.TROUBLESHOOTER,
            plane=Plane.EXECUTION,
            legal_terminal_results=(
                ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
                ExecutionTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value: (ResultClass.SUCCESS,),
                    ExecutionTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        ExecutionStageName.CONSULTANT.value: StageMetadata(
            stage=ExecutionStageName.CONSULTANT,
            plane=Plane.EXECUTION,
            legal_terminal_results=(
                ExecutionTerminalResult.CONSULT_COMPLETE.value,
                ExecutionTerminalResult.NEEDS_PLANNING.value,
                ExecutionTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    ExecutionTerminalResult.CONSULT_COMPLETE.value: (ResultClass.SUCCESS,),
                    ExecutionTerminalResult.NEEDS_PLANNING.value: (
                        ResultClass.ESCALATE_PLANNING,
                    ),
                    ExecutionTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        PlanningStageName.PLANNER.value: StageMetadata(
            stage=PlanningStageName.PLANNER,
            plane=Plane.PLANNING,
            legal_terminal_results=(
                PlanningTerminalResult.PLANNER_COMPLETE.value,
                PlanningTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    PlanningTerminalResult.PLANNER_COMPLETE.value: (ResultClass.SUCCESS,),
                    PlanningTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        PlanningStageName.MANAGER.value: StageMetadata(
            stage=PlanningStageName.MANAGER,
            plane=Plane.PLANNING,
            legal_terminal_results=(
                PlanningTerminalResult.MANAGER_COMPLETE.value,
                PlanningTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    PlanningTerminalResult.MANAGER_COMPLETE.value: (ResultClass.SUCCESS,),
                    PlanningTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        PlanningStageName.MECHANIC.value: StageMetadata(
            stage=PlanningStageName.MECHANIC,
            plane=Plane.PLANNING,
            legal_terminal_results=(
                PlanningTerminalResult.MECHANIC_COMPLETE.value,
                PlanningTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    PlanningTerminalResult.MECHANIC_COMPLETE.value: (ResultClass.SUCCESS,),
                    PlanningTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        PlanningStageName.AUDITOR.value: StageMetadata(
            stage=PlanningStageName.AUDITOR,
            plane=Plane.PLANNING,
            legal_terminal_results=(
                PlanningTerminalResult.AUDITOR_COMPLETE.value,
                PlanningTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    PlanningTerminalResult.AUDITOR_COMPLETE.value: (ResultClass.SUCCESS,),
                    PlanningTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        PlanningStageName.ARBITER.value: StageMetadata(
            stage=PlanningStageName.ARBITER,
            plane=Plane.PLANNING,
            legal_terminal_results=(
                PlanningTerminalResult.ARBITER_COMPLETE.value,
                PlanningTerminalResult.REMEDIATION_NEEDED.value,
                PlanningTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    PlanningTerminalResult.ARBITER_COMPLETE.value: (ResultClass.SUCCESS,),
                    PlanningTerminalResult.REMEDIATION_NEEDED.value: (
                        ResultClass.FOLLOWUP_NEEDED,
                    ),
                    PlanningTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        LearningStageName.ANALYST.value: StageMetadata(
            stage=LearningStageName.ANALYST,
            plane=Plane.LEARNING,
            legal_terminal_results=(
                LearningTerminalResult.ANALYST_COMPLETE.value,
                LearningTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    LearningTerminalResult.ANALYST_COMPLETE.value: (ResultClass.SUCCESS,),
                    LearningTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        LearningStageName.PROFESSOR.value: StageMetadata(
            stage=LearningStageName.PROFESSOR,
            plane=Plane.LEARNING,
            legal_terminal_results=(
                LearningTerminalResult.PROFESSOR_COMPLETE.value,
                LearningTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    LearningTerminalResult.PROFESSOR_COMPLETE.value: (ResultClass.SUCCESS,),
                    LearningTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
        LearningStageName.CURATOR.value: StageMetadata(
            stage=LearningStageName.CURATOR,
            plane=Plane.LEARNING,
            legal_terminal_results=(
                LearningTerminalResult.CURATOR_COMPLETE.value,
                LearningTerminalResult.BLOCKED.value,
            ),
            allowed_result_classes_by_outcome=_allowed(
                {
                    LearningTerminalResult.CURATOR_COMPLETE.value: (ResultClass.SUCCESS,),
                    LearningTerminalResult.BLOCKED.value: (
                        ResultClass.BLOCKED,
                        ResultClass.RECOVERABLE_FAILURE,
                    ),
                }
            ),
        ),
    }
)

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


def terminal_result_for_plane(plane: Plane, token: str) -> TerminalResult | None:
    try:
        if plane is Plane.EXECUTION:
            return ExecutionTerminalResult(token)
        if plane is Plane.LEARNING:
            return LearningTerminalResult(token)
        return PlanningTerminalResult(token)
    except ValueError:
        return None


def blocked_terminal_for_plane(plane: Plane) -> TerminalResult:
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
