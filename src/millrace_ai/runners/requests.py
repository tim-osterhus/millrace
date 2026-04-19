"""Runner request and raw-result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from millrace_ai.contracts import (
    ExecutionStageName,
    Plane,
    PlanningStageName,
    StageName,
    TokenUsage,
    WorkItemKind,
)

RunnerExitKind = Literal[
    "completed",
    "timeout",
    "runner_error",
    "provider_error",
    "interrupted",
]
RequestKind = Literal["active_work_item", "closure_target"]

_STAGE_TO_PLANE: dict[str, Plane] = {
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
}


class StageRunRequest(BaseModel):
    """Machine-readable request payload for one stage run."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    run_id: str
    plane: Plane
    stage: StageName
    request_kind: RequestKind = "active_work_item"

    mode_id: str
    compiled_plan_id: str
    entrypoint_path: str
    entrypoint_contract_id: str | None = None

    required_skill_paths: tuple[str, ...] = ()
    attached_skill_paths: tuple[str, ...] = ()

    active_work_item_kind: WorkItemKind | None = None
    active_work_item_id: str | None = None
    active_work_item_path: str | None = None
    closure_target_path: str | None = None
    closure_target_root_spec_id: str | None = None
    closure_target_root_idea_id: str | None = None
    canonical_root_spec_path: str | None = None
    canonical_seed_idea_path: str | None = None
    preferred_rubric_path: str | None = None
    preferred_verdict_path: str | None = None
    preferred_report_path: str | None = None

    run_dir: str
    summary_status_path: str
    runtime_snapshot_path: str
    recovery_counters_path: str
    preferred_troubleshoot_report_path: str | None = None
    runtime_error_code: str | None = None
    runtime_error_report_path: str | None = None
    runtime_error_catalog_path: str | None = None

    runner_name: str | None = None
    model_name: str | None = None
    timeout_seconds: int = 0

    @model_validator(mode="after")
    def validate_request_shape(self) -> "StageRunRequest":
        if _STAGE_TO_PLANE[self.stage.value] != self.plane:
            raise ValueError("stage must belong to plane")

        has_kind = self.active_work_item_kind is not None
        has_id = self.active_work_item_id is not None
        if has_kind != has_id:
            raise ValueError(
                "active_work_item_kind and active_work_item_id must be set together"
            )

        closure_fields = (
            self.closure_target_path,
            self.closure_target_root_spec_id,
            self.closure_target_root_idea_id,
            self.canonical_root_spec_path,
            self.canonical_seed_idea_path,
            self.preferred_rubric_path,
            self.preferred_verdict_path,
            self.preferred_report_path,
        )
        if self.request_kind == "active_work_item":
            if any(field is not None for field in closure_fields):
                raise ValueError(
                    "active_work_item requests cannot declare closure target fields"
                )
        else:
            if has_kind or self.active_work_item_path is not None:
                raise ValueError(
                    "closure_target requests cannot declare active work item fields"
                )
            if any(field is None for field in closure_fields):
                raise ValueError("closure_target requests require closure target fields")

        if self.timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")

        return self


def render_stage_request_context_lines(request: StageRunRequest) -> tuple[str, ...]:
    """Render request fields into a runner-agnostic prompt envelope."""

    lines: list[str] = [
        f"Request ID: {request.request_id}",
        f"Run ID: {request.run_id}",
        f"Mode ID: {request.mode_id}",
        f"Compiled Plan ID: {request.compiled_plan_id}",
        f"Stage: {request.stage.value}",
        f"Plane: {request.plane.value}",
        f"Request Kind: {request.request_kind}",
        f"Entrypoint Path: {request.entrypoint_path}",
        f"Entrypoint Contract ID: {request.entrypoint_contract_id or 'none'}",
        (
            "Active Work Item: "
            f"{request.active_work_item_kind.value if request.active_work_item_kind else 'none'} "
            f"{request.active_work_item_id or 'none'}"
        ),
        f"Active Work Item Path: {request.active_work_item_path or 'none'}",
        f"Closure Target Path: {request.closure_target_path or 'none'}",
        f"Closure Target Root Spec ID: {request.closure_target_root_spec_id or 'none'}",
        f"Closure Target Root Idea ID: {request.closure_target_root_idea_id or 'none'}",
        f"Canonical Root Spec Path: {request.canonical_root_spec_path or 'none'}",
        f"Canonical Seed Idea Path: {request.canonical_seed_idea_path or 'none'}",
        f"Preferred Rubric Path: {request.preferred_rubric_path or 'none'}",
        f"Preferred Verdict Path: {request.preferred_verdict_path or 'none'}",
        f"Preferred Report Path: {request.preferred_report_path or 'none'}",
    ]
    lines.extend(_render_path_list("Required Skill Paths", request.required_skill_paths))
    lines.extend(_render_path_list("Attached Skill Paths", request.attached_skill_paths))
    lines.extend(
        [
            f"Run Directory: {request.run_dir}",
            f"Runtime Snapshot Path: {request.runtime_snapshot_path}",
            f"Recovery Counters Path: {request.recovery_counters_path}",
            f"Summary Status Path: {request.summary_status_path}",
            (
                "Preferred Troubleshoot Report Path: "
                f"{request.preferred_troubleshoot_report_path or 'none'}"
            ),
            f"Runtime Error Code: {request.runtime_error_code or 'none'}",
            f"Runtime Error Report Path: {request.runtime_error_report_path or 'none'}",
            f"Runtime Error Catalog Path: {request.runtime_error_catalog_path or 'none'}",
            f"Runner Name: {request.runner_name or 'none'}",
            f"Model Name: {request.model_name or 'none'}",
            f"Timeout Seconds: {request.timeout_seconds}",
        ]
    )
    return tuple(lines)


class RunnerRawResult(BaseModel):
    """Thin raw result emitted by the runner after invoking one stage."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    run_id: str
    stage: StageName
    runner_name: str
    model_name: str | None = None

    exit_kind: RunnerExitKind
    exit_code: int | None = None

    stdout_path: str | None = None
    stderr_path: str | None = None
    terminal_result_path: str | None = None
    event_log_path: str | None = None
    token_usage: TokenUsage | None = None

    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> "RunnerRawResult":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        return self


@dataclass(frozen=True, slots=True)
class _TerminalExtraction:
    terminal_result: object | None
    result_class: object | None
    detected_marker: str | None
    artifact_paths: tuple[str, ...]
    failure_class: str | None
    notes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return (
            self.failure_class is None
            and self.terminal_result is not None
            and self.result_class is not None
        )


def _render_path_list(label: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    if not paths:
        return (f"{label}: none",)
    lines = [f"{label}:"]
    lines.extend(f"- {path}" for path in paths)
    return tuple(lines)


__all__ = [
    "RequestKind",
    "RunnerExitKind",
    "RunnerRawResult",
    "StageRunRequest",
    "render_stage_request_context_lines",
]
