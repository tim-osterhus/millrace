"""Stage runner request/raw-result contracts and normalization helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ResultClass,
    StageName,
    StageResultEnvelope,
    TerminalResult,
    WorkItemKind,
)

RunnerExitKind = Literal[
    "completed",
    "timeout",
    "runner_error",
    "provider_error",
    "interrupted",
]


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
}


_STAGE_LEGAL_TERMINALS: dict[str, set[str]] = {
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
}


_RESULT_CLASS_BY_TERMINAL: dict[str, ResultClass] = {
    ExecutionTerminalResult.BUILDER_COMPLETE.value: ResultClass.SUCCESS,
    ExecutionTerminalResult.CHECKER_PASS.value: ResultClass.SUCCESS,
    ExecutionTerminalResult.FIX_NEEDED.value: ResultClass.FOLLOWUP_NEEDED,
    ExecutionTerminalResult.FIXER_COMPLETE.value: ResultClass.SUCCESS,
    ExecutionTerminalResult.DOUBLECHECK_PASS.value: ResultClass.SUCCESS,
    ExecutionTerminalResult.UPDATE_COMPLETE.value: ResultClass.SUCCESS,
    ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value: ResultClass.SUCCESS,
    ExecutionTerminalResult.CONSULT_COMPLETE.value: ResultClass.SUCCESS,
    ExecutionTerminalResult.NEEDS_PLANNING.value: ResultClass.ESCALATE_PLANNING,
    PlanningTerminalResult.PLANNER_COMPLETE.value: ResultClass.SUCCESS,
    PlanningTerminalResult.MANAGER_COMPLETE.value: ResultClass.SUCCESS,
    PlanningTerminalResult.MECHANIC_COMPLETE.value: ResultClass.SUCCESS,
    PlanningTerminalResult.AUDITOR_COMPLETE.value: ResultClass.SUCCESS,
}


_TERMINAL_TOKEN_PATTERN = re.compile(r"^###\s+([A-Z_]+)\s*$")


class StageRunRequest(BaseModel):
    """Machine-readable request payload for one stage run."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    run_id: str
    plane: Plane
    stage: StageName

    mode_id: str
    compiled_plan_id: str
    entrypoint_path: str
    entrypoint_contract_id: str | None = None

    required_skill_paths: tuple[str, ...] = ()
    attached_skill_paths: tuple[str, ...] = ()

    active_work_item_kind: WorkItemKind | None = None
    active_work_item_id: str | None = None
    active_work_item_path: str | None = None

    run_dir: str
    summary_status_path: str
    runtime_snapshot_path: str
    recovery_counters_path: str
    preferred_troubleshoot_report_path: str | None = None

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
        f"Entrypoint Path: {request.entrypoint_path}",
        f"Entrypoint Contract ID: {request.entrypoint_contract_id or 'none'}",
        (
            "Active Work Item: "
            f"{request.active_work_item_kind.value if request.active_work_item_kind else 'none'} "
            f"{request.active_work_item_id or 'none'}"
        ),
        f"Active Work Item Path: {request.active_work_item_path or 'none'}",
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

    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> "RunnerRawResult":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        return self


class _StructuredTerminalResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str | None = None
    terminal_result: str
    result_class: str | None = None
    summary_artifact_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _TerminalExtraction:
    terminal_result: TerminalResult | None
    result_class: ResultClass | None
    detected_marker: str | None
    artifact_paths: tuple[str, ...]
    failure_class: str | None
    notes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.failure_class is None and self.terminal_result is not None and self.result_class is not None


def normalize_stage_result(
    request: StageRunRequest,
    raw_result: RunnerRawResult,
) -> StageResultEnvelope:
    """Normalize one runner output into a deterministic stage result envelope."""

    if request.active_work_item_kind is None or request.active_work_item_id is None:
        raise ValueError(
            "active_work_item_kind and active_work_item_id are required to normalize stage results"
        )

    identity_notes = _identity_mismatch_notes(request, raw_result)
    if identity_notes:
        return _failure_envelope(
            request,
            raw_result,
            failure_class="runner_transport_failure",
            notes=identity_notes,
        )

    exit_failure = _failure_class_for_exit_kind(raw_result.exit_kind)
    if exit_failure is not None:
        return _failure_envelope(
            request,
            raw_result,
            failure_class=exit_failure,
            notes=(f"runner exited with {raw_result.exit_kind}",),
        )
    if raw_result.exit_kind == "completed" and raw_result.exit_code not in (None, 0):
        return _failure_envelope(
            request,
            raw_result,
            failure_class="runner_transport_failure",
            notes=("runner completed with non-zero exit code",),
        )

    extraction = _extract_terminal_result(request, raw_result)
    if not extraction.ok:
        return _failure_envelope(
            request,
            raw_result,
            failure_class=extraction.failure_class or "illegal_terminal_result",
            notes=extraction.notes,
            detected_marker=extraction.detected_marker,
            artifact_paths=extraction.artifact_paths,
        )

    result_class = extraction.result_class
    assert result_class is not None
    terminal_result = extraction.terminal_result
    assert terminal_result is not None
    report_artifact = _resolved_report_artifact(request)

    return StageResultEnvelope(
        run_id=request.run_id,
        plane=request.plane,
        stage=request.stage,
        work_item_kind=request.active_work_item_kind,
        work_item_id=request.active_work_item_id,
        terminal_result=terminal_result,
        result_class=result_class,
        summary_status_marker=f"### {terminal_result.value}",
        success=result_class is ResultClass.SUCCESS,
        retryable=False,
        exit_code=raw_result.exit_code or 0,
        duration_seconds=(raw_result.ended_at - raw_result.started_at).total_seconds(),
        artifact_paths=_merge_artifact_paths(extraction.artifact_paths, report_artifact),
        report_artifact=report_artifact,
        detected_marker=extraction.detected_marker,
        stdout_path=raw_result.stdout_path,
        stderr_path=raw_result.stderr_path,
        runner_name=raw_result.runner_name,
        model_name=raw_result.model_name,
        notes=extraction.notes,
        metadata={
            "request_id": request.request_id,
            "normalization_source": (
                "structured_result_file"
                if raw_result.terminal_result_path
                else "stdout_terminal_token"
            ),
            "failure_class": None,
            "valid_terminal_result": True,
            "raw_exit_kind": raw_result.exit_kind,
        },
        started_at=raw_result.started_at,
        completed_at=raw_result.ended_at,
    )


def _extract_terminal_result(
    request: StageRunRequest,
    raw_result: RunnerRawResult,
) -> _TerminalExtraction:
    if raw_result.terminal_result_path:
        return _extract_from_structured_result_file(
            request,
            Path(raw_result.terminal_result_path),
        )

    return _extract_from_stdout_tokens(request, raw_result.stdout_path)


def _extract_from_structured_result_file(
    request: StageRunRequest,
    terminal_result_path: Path,
) -> _TerminalExtraction:
    if not terminal_result_path.exists():
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=(),
            failure_class="missing_terminal_result",
            notes=(f"structured terminal result file is missing: {terminal_result_path}",),
        )

    try:
        raw_payload = json.loads(terminal_result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=(),
            failure_class="illegal_terminal_result",
            notes=(f"failed to parse structured terminal result: {exc}",),
        )

    if not isinstance(raw_payload, dict):
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=(),
            failure_class="illegal_terminal_result",
            notes=("structured terminal result payload must be an object",),
        )

    try:
        payload = _StructuredTerminalResultPayload.model_validate(raw_payload)
    except ValidationError as exc:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=(),
            failure_class="illegal_terminal_result",
            notes=(f"structured terminal result payload is invalid: {exc}",),
        )

    if payload.stage is not None and payload.stage != request.stage.value:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=payload.summary_artifact_paths,
            failure_class="illegal_terminal_result",
            notes=(
                "structured terminal result stage does not match run request stage",
            ),
        )

    terminal_result = _terminal_result_for_stage(request.stage, payload.terminal_result)
    if terminal_result is None:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=payload.summary_artifact_paths,
            failure_class="illegal_terminal_result",
            notes=(
                f"terminal result {payload.terminal_result!r} is illegal for stage {request.stage.value}",
            ),
        )

    resolved_result_class = _resolve_result_class(
        terminal_result,
        payload.result_class,
    )
    if resolved_result_class is None:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=payload.summary_artifact_paths,
            failure_class="illegal_terminal_result",
            notes=(
                "structured terminal result class is incompatible with terminal_result",
            ),
        )

    missing_artifacts = tuple(
        candidate
        for candidate in payload.summary_artifact_paths
        if not _artifact_exists(request.run_dir, candidate)
    )
    if missing_artifacts:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=payload.summary_artifact_paths,
            failure_class="missing_required_artifact",
            notes=(
                "missing required summary artifacts: " + ", ".join(missing_artifacts),
            ),
        )

    return _TerminalExtraction(
        terminal_result=terminal_result,
        result_class=resolved_result_class,
        detected_marker=f"### {terminal_result.value}",
        artifact_paths=payload.summary_artifact_paths,
        failure_class=None,
        notes=("terminal result resolved from structured result file",),
    )


def _extract_from_stdout_tokens(
    request: StageRunRequest,
    stdout_path: str | None,
) -> _TerminalExtraction:
    if not stdout_path:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=(),
            failure_class="missing_terminal_result",
            notes=("stdout path is missing and no structured terminal result was provided",),
        )

    path = Path(stdout_path)
    if not path.exists():
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=(),
            failure_class="missing_terminal_result",
            notes=(f"stdout file is missing: {stdout_path}",),
        )

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=(),
            failure_class="runner_transport_failure",
            notes=(f"failed reading stdout file: {exc}",),
        )

    tokens: list[str] = []
    for line in lines:
        match = _TERMINAL_TOKEN_PATTERN.match(line.strip())
        if match is not None:
            tokens.append(match.group(1))

    if not tokens:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=(),
            failure_class="missing_terminal_result",
            notes=("no terminal token found in stdout",),
        )

    unique_tokens = set(tokens)
    if len(unique_tokens) > 1:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=f"### {tokens[-1]}",
            artifact_paths=(),
            failure_class="conflicting_terminal_results",
            notes=("stdout contains conflicting terminal tokens",),
        )

    final_token = tokens[-1]
    terminal_result = _terminal_result_for_stage(request.stage, final_token)
    if terminal_result is None:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=f"### {final_token}",
            artifact_paths=(),
            failure_class="illegal_terminal_result",
            notes=(
                f"terminal token {final_token!r} is illegal for stage {request.stage.value}",
            ),
        )

    result_class = _resolve_result_class(terminal_result, None)
    assert result_class is not None

    return _TerminalExtraction(
        terminal_result=terminal_result,
        result_class=result_class,
        detected_marker=f"### {final_token}",
        artifact_paths=(),
        failure_class=None,
        notes=("terminal result resolved from stdout token",),
    )


def _failure_class_for_exit_kind(exit_kind: RunnerExitKind) -> str | None:
    if exit_kind == "completed":
        return None
    if exit_kind == "timeout":
        return "runner_timeout"
    if exit_kind == "provider_error":
        return "provider_failure"
    return "runner_transport_failure"


def _identity_mismatch_notes(
    request: StageRunRequest,
    raw_result: RunnerRawResult,
) -> tuple[str, ...]:
    notes: list[str] = []
    if raw_result.request_id != request.request_id:
        notes.append("raw result request_id does not match stage run request")
    if raw_result.run_id != request.run_id:
        notes.append("raw result run_id does not match stage run request")
    if raw_result.stage != request.stage:
        notes.append("raw result stage does not match stage run request")
    return tuple(notes)


def _failure_envelope(
    request: StageRunRequest,
    raw_result: RunnerRawResult,
    *,
    failure_class: str,
    notes: tuple[str, ...],
    detected_marker: str | None = None,
    artifact_paths: tuple[str, ...] = (),
) -> StageResultEnvelope:
    blocked_terminal = _blocked_terminal_for_plane(request.plane)
    if request.active_work_item_kind is None or request.active_work_item_id is None:
        raise ValueError(
            "active_work_item_kind and active_work_item_id are required for failure normalization"
        )
    report_artifact = _resolved_report_artifact(request)

    return StageResultEnvelope(
        run_id=request.run_id,
        plane=request.plane,
        stage=request.stage,
        work_item_kind=request.active_work_item_kind,
        work_item_id=request.active_work_item_id,
        terminal_result=blocked_terminal,
        result_class=ResultClass.RECOVERABLE_FAILURE,
        summary_status_marker="### BLOCKED",
        success=False,
        retryable=True,
        exit_code=raw_result.exit_code or 1,
        duration_seconds=(raw_result.ended_at - raw_result.started_at).total_seconds(),
        artifact_paths=_merge_artifact_paths(artifact_paths, report_artifact),
        report_artifact=report_artifact,
        detected_marker=detected_marker,
        stdout_path=raw_result.stdout_path,
        stderr_path=raw_result.stderr_path,
        runner_name=raw_result.runner_name,
        model_name=raw_result.model_name,
        notes=notes,
        metadata={
            "request_id": request.request_id,
            "normalization_source": "failure",
            "failure_class": failure_class,
            "valid_terminal_result": False,
            "raw_exit_kind": raw_result.exit_kind,
        },
        started_at=raw_result.started_at,
        completed_at=raw_result.ended_at,
    )


def _terminal_result_for_stage(stage: StageName, token: str) -> TerminalResult | None:
    legal = _STAGE_LEGAL_TERMINALS[stage.value]
    if token not in legal:
        return None

    stage_plane = _STAGE_TO_PLANE[stage.value]
    if stage_plane is Plane.EXECUTION:
        return ExecutionTerminalResult(token)
    return PlanningTerminalResult(token)


def _resolve_result_class(
    terminal_result: TerminalResult,
    raw_result_class: str | None,
) -> ResultClass | None:
    if raw_result_class is None:
        if terminal_result.value == "BLOCKED":
            return ResultClass.BLOCKED
        return _RESULT_CLASS_BY_TERMINAL[terminal_result.value]

    try:
        result_class = ResultClass(raw_result_class)
    except ValueError:
        return None

    if terminal_result.value == "BLOCKED":
        if result_class in {ResultClass.BLOCKED, ResultClass.RECOVERABLE_FAILURE}:
            return result_class
        return None

    expected = _RESULT_CLASS_BY_TERMINAL.get(terminal_result.value)
    if expected is None:
        return None
    if result_class is not expected:
        return None
    return result_class


def _blocked_terminal_for_plane(plane: Plane) -> TerminalResult:
    if plane is Plane.EXECUTION:
        return ExecutionTerminalResult.BLOCKED
    return PlanningTerminalResult.BLOCKED


def _render_path_list(label: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    if not paths:
        return (f"{label}: none",)
    lines = [f"{label}:"]
    lines.extend(f"- {path}" for path in paths)
    return tuple(lines)


def _resolved_report_artifact(request: StageRunRequest) -> str | None:
    candidate = request.preferred_troubleshoot_report_path
    if not candidate:
        return None
    if not _artifact_exists(request.run_dir, candidate):
        return None
    return candidate


def _merge_artifact_paths(
    artifact_paths: tuple[str, ...],
    report_artifact: str | None,
) -> tuple[str, ...]:
    merged = list(artifact_paths)
    if report_artifact and report_artifact not in merged:
        merged.append(report_artifact)
    return tuple(merged)


def _artifact_exists(run_dir: str, candidate_path: str) -> bool:
    run_root = Path(run_dir).expanduser().resolve()
    candidate = Path(candidate_path)
    if not candidate.is_absolute():
        candidate = run_root / candidate

    try:
        resolved_candidate = candidate.resolve()
    except OSError:
        return False

    try:
        resolved_candidate.relative_to(run_root)
    except ValueError:
        return False

    return resolved_candidate.exists()


__all__ = [
    "RunnerRawResult",
    "StageRunRequest",
    "normalize_stage_result",
    "render_stage_request_context_lines",
]
