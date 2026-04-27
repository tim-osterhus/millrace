"""Stage ownership and terminal-result metadata."""

from __future__ import annotations

import re

from .enums import (
    ExecutionStageName,
    ExecutionTerminalResult,
    LearningStageName,
    LearningTerminalResult,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    StageName,
)

STAGE_TO_PLANE: dict[str, Plane] = {
    ExecutionStageName.BUILDER.value: Plane.EXECUTION,
    ExecutionStageName.CHECKER.value: Plane.EXECUTION,
    ExecutionStageName.FIXER.value: Plane.EXECUTION,
    ExecutionStageName.DOUBLECHECKER.value: Plane.EXECUTION,
    ExecutionStageName.UPDATER.value: Plane.EXECUTION,
    ExecutionStageName.TROUBLESHOOTER.value: Plane.EXECUTION,
    ExecutionStageName.CONSULTANT.value: Plane.EXECUTION,
    PlanningStageName.PLANNER.value: Plane.PLANNING,
    PlanningStageName.MANAGER.value: Plane.PLANNING,
    PlanningStageName.MECHANIC.value: Plane.PLANNING,
    PlanningStageName.AUDITOR.value: Plane.PLANNING,
    PlanningStageName.ARBITER.value: Plane.PLANNING,
    LearningStageName.ANALYST.value: Plane.LEARNING,
    LearningStageName.PROFESSOR.value: Plane.LEARNING,
    LearningStageName.CURATOR.value: Plane.LEARNING,
}

STAGE_LEGAL_TERMINAL_RESULTS: dict[str, set[str]] = {
    ExecutionStageName.BUILDER.value: {
        ExecutionTerminalResult.BUILDER_COMPLETE.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.CHECKER.value: {
        ExecutionTerminalResult.CHECKER_PASS.value,
        ExecutionTerminalResult.FIX_NEEDED.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.FIXER.value: {
        ExecutionTerminalResult.FIXER_COMPLETE.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.DOUBLECHECKER.value: {
        ExecutionTerminalResult.DOUBLECHECK_PASS.value,
        ExecutionTerminalResult.FIX_NEEDED.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.UPDATER.value: {
        ExecutionTerminalResult.UPDATE_COMPLETE.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.TROUBLESHOOTER.value: {
        ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    ExecutionStageName.CONSULTANT.value: {
        ExecutionTerminalResult.CONSULT_COMPLETE.value,
        ExecutionTerminalResult.NEEDS_PLANNING.value,
        ExecutionTerminalResult.BLOCKED.value,
    },
    PlanningStageName.PLANNER.value: {
        PlanningTerminalResult.PLANNER_COMPLETE.value,
        PlanningTerminalResult.BLOCKED.value,
    },
    PlanningStageName.MANAGER.value: {
        PlanningTerminalResult.MANAGER_COMPLETE.value,
        PlanningTerminalResult.BLOCKED.value,
    },
    PlanningStageName.MECHANIC.value: {
        PlanningTerminalResult.MECHANIC_COMPLETE.value,
        PlanningTerminalResult.BLOCKED.value,
    },
    PlanningStageName.AUDITOR.value: {
        PlanningTerminalResult.AUDITOR_COMPLETE.value,
        PlanningTerminalResult.BLOCKED.value,
    },
    PlanningStageName.ARBITER.value: {
        PlanningTerminalResult.ARBITER_COMPLETE.value,
        PlanningTerminalResult.REMEDIATION_NEEDED.value,
        PlanningTerminalResult.BLOCKED.value,
    },
    LearningStageName.ANALYST.value: {
        LearningTerminalResult.ANALYST_COMPLETE.value,
        LearningTerminalResult.BLOCKED.value,
    },
    LearningStageName.PROFESSOR.value: {
        LearningTerminalResult.PROFESSOR_COMPLETE.value,
        LearningTerminalResult.BLOCKED.value,
    },
    LearningStageName.CURATOR.value: {
        LearningTerminalResult.CURATOR_COMPLETE.value,
        LearningTerminalResult.BLOCKED.value,
    },
}

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def stage_plane(stage: StageName) -> Plane:
    return STAGE_TO_PLANE[stage.value]


def legal_terminal_results(stage: StageName) -> set[str]:
    return STAGE_LEGAL_TERMINAL_RESULTS[stage.value]


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
    "STAGE_LEGAL_TERMINAL_RESULTS",
    "STAGE_TO_PLANE",
    "legal_terminal_results",
    "stage_plane",
    "validate_safe_identifier",
]
