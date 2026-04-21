"""Shared Millrace-owned prompt construction for runner adapters."""

from __future__ import annotations

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    PlanningStageName,
    PlanningTerminalResult,
)
from millrace_ai.runners.requests import StageRunRequest, render_stage_request_context_lines


def legal_terminal_markers(stage: ExecutionStageName | PlanningStageName) -> tuple[str, ...]:
    if stage is ExecutionStageName.BUILDER:
        return (
            ExecutionTerminalResult.BUILDER_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.CHECKER:
        return (
            ExecutionTerminalResult.CHECKER_PASS.value,
            ExecutionTerminalResult.FIX_NEEDED.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.FIXER:
        return (
            ExecutionTerminalResult.FIXER_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.DOUBLECHECKER:
        return (
            ExecutionTerminalResult.DOUBLECHECK_PASS.value,
            ExecutionTerminalResult.FIX_NEEDED.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.UPDATER:
        return (
            ExecutionTerminalResult.UPDATE_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.TROUBLESHOOTER:
        return (
            ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.CONSULTANT:
        return (
            ExecutionTerminalResult.CONSULT_COMPLETE.value,
            ExecutionTerminalResult.NEEDS_PLANNING.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is PlanningStageName.PLANNER:
        return (
            PlanningTerminalResult.PLANNER_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        )
    if stage is PlanningStageName.MANAGER:
        return (
            PlanningTerminalResult.MANAGER_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        )
    if stage is PlanningStageName.MECHANIC:
        return (
            PlanningTerminalResult.MECHANIC_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        )
    if stage is PlanningStageName.AUDITOR:
        return (
            PlanningTerminalResult.AUDITOR_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        )
    return (
        PlanningTerminalResult.ARBITER_COMPLETE.value,
        PlanningTerminalResult.REMEDIATION_NEEDED.value,
        PlanningTerminalResult.BLOCKED.value,
    )


def build_stage_prompt(request: StageRunRequest) -> str:
    request_context = render_stage_request_context_lines(request)
    legal_markers = ", ".join(
        f"`### {marker}`" for marker in legal_terminal_markers(request.stage)
    )
    return "\n".join(
        (
            "You are executing one Millrace runtime stage request.",
            f"Open `{request.entrypoint_path}` and follow instructions exactly.",
            "",
            "Stage Request Context:",
            *request_context,
            "",
            (
                "When done, print exactly one legal terminal marker defined by the opened "
                "entrypoint contract."
            ),
            f"Legal markers for this stage: {legal_markers}.",
            "Do not invent or rename terminal markers.",
            "Do not print multiple terminal markers.",
        )
    )


__all__ = ["build_stage_prompt", "legal_terminal_markers"]
