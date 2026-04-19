"""Read-only helpers for enumerating and summarizing runtime run artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Literal

from pydantic import ValidationError

from millrace_ai.contracts import StageResultEnvelope, TokenUsage, WorkItemKind
from millrace_ai.paths import WorkspacePaths, workspace_paths

RunInspectionStatus = Literal["valid", "incomplete", "malformed"]


@dataclass(frozen=True, slots=True)
class InspectedStageResult:
    stage_result_path: str
    stage: str
    request_kind: str | None
    closure_target_root_spec_id: str | None
    terminal_result: str
    result_class: str
    work_item_kind: WorkItemKind
    work_item_id: str
    failure_class: str | None
    stdout_path: str | None
    stderr_path: str | None
    report_artifact: str | None
    artifact_paths: tuple[str, ...]
    runner_name: str | None
    model_name: str | None
    started_at: str
    completed_at: str
    duration_seconds: float = 0.0
    token_usage: TokenUsage | None = None


@dataclass(frozen=True, slots=True)
class InspectedRunSummary:
    run_id: str
    run_dir: str
    status: RunInspectionStatus
    request_kind: str | None
    closure_target_root_spec_id: str | None
    work_item_kind: WorkItemKind | None
    work_item_id: str | None
    failure_class: str | None
    troubleshoot_report_path: str | None
    primary_stdout_path: str | None
    primary_stderr_path: str | None
    stage_results: tuple[InspectedStageResult, ...]
    notes: tuple[str, ...]
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    token_usage: TokenUsage | None = None


def inspect_run(run_dir: Path | str) -> InspectedRunSummary:
    """Inspect one run directory without mutating runtime state."""

    resolved_run_dir = Path(run_dir).expanduser().resolve()
    stage_results_dir = resolved_run_dir / "stage_results"
    notes: list[str] = []
    inspected_stage_results: list[InspectedStageResult] = []
    status: RunInspectionStatus = "valid"

    if not stage_results_dir.exists():
        return InspectedRunSummary(
            run_id=resolved_run_dir.name,
            run_dir=str(resolved_run_dir),
            status="incomplete",
            request_kind=None,
            closure_target_root_spec_id=None,
            work_item_kind=None,
            work_item_id=None,
            failure_class=None,
            troubleshoot_report_path=None,
            primary_stdout_path=None,
            primary_stderr_path=None,
            stage_results=(),
            notes=("no stage result artifacts found",),
        )

    stage_result_paths = sorted(
        path for path in stage_results_dir.iterdir() if path.is_file() and path.suffix == ".json"
    )
    if not stage_result_paths:
        return InspectedRunSummary(
            run_id=resolved_run_dir.name,
            run_dir=str(resolved_run_dir),
            status="incomplete",
            request_kind=None,
            closure_target_root_spec_id=None,
            work_item_kind=None,
            work_item_id=None,
            failure_class=None,
            troubleshoot_report_path=None,
            primary_stdout_path=None,
            primary_stderr_path=None,
            stage_results=(),
            notes=("no stage result artifacts found",),
        )

    for stage_result_path in stage_result_paths:
        try:
            payload = json.loads(stage_result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            status = "malformed"
            notes.append(f"{stage_result_path.name}: invalid JSON: {exc}")
            continue

        try:
            stage_result = StageResultEnvelope.model_validate(payload)
        except ValidationError as exc:
            status = "malformed"
            notes.append(f"{stage_result_path.name}: invalid stage result payload: {exc}")
            continue

        inspected_stage_results.append(
            InspectedStageResult(
                stage_result_path=_normalize_run_relative_path(resolved_run_dir, stage_result_path),
                stage=stage_result.stage.value,
                request_kind=_string_metadata(stage_result, "request_kind"),
                closure_target_root_spec_id=_string_metadata(
                    stage_result,
                    "closure_target_root_spec_id",
                ),
                terminal_result=stage_result.terminal_result.value,
                result_class=stage_result.result_class.value,
                work_item_kind=stage_result.work_item_kind,
                work_item_id=stage_result.work_item_id,
                failure_class=_failure_class_from_stage_result(stage_result),
                stdout_path=_normalize_optional_run_relative_path(
                    resolved_run_dir, stage_result.stdout_path
                ),
                stderr_path=_normalize_optional_run_relative_path(
                    resolved_run_dir, stage_result.stderr_path
                ),
                report_artifact=_normalize_optional_run_relative_path(
                    resolved_run_dir, stage_result.report_artifact
                ),
                artifact_paths=tuple(
                    _normalize_optional_run_relative_path(resolved_run_dir, artifact_path)
                    or artifact_path
                    for artifact_path in stage_result.artifact_paths
                ),
                runner_name=stage_result.runner_name,
                model_name=stage_result.model_name,
                started_at=stage_result.started_at.isoformat(),
                completed_at=stage_result.completed_at.isoformat(),
                duration_seconds=stage_result.duration_seconds,
                token_usage=stage_result.token_usage,
            )
        )

    inspected_stage_results.sort(
        key=lambda item: (item.completed_at, item.started_at, item.stage_result_path)
    )
    if not inspected_stage_results and status == "valid":
        status = "incomplete"
        notes.append("no stage result artifacts found")

    latest_stage_result = inspected_stage_results[-1] if inspected_stage_results else None
    first_stage_result = inspected_stage_results[0] if inspected_stage_results else None
    return InspectedRunSummary(
        run_id=resolved_run_dir.name,
        run_dir=str(resolved_run_dir),
        status=status,
        request_kind=latest_stage_result.request_kind if latest_stage_result else None,
        closure_target_root_spec_id=(
            latest_stage_result.closure_target_root_spec_id if latest_stage_result else None
        ),
        work_item_kind=latest_stage_result.work_item_kind if latest_stage_result else None,
        work_item_id=latest_stage_result.work_item_id if latest_stage_result else None,
        failure_class=latest_stage_result.failure_class if latest_stage_result else None,
        troubleshoot_report_path=(
            latest_stage_result.report_artifact if latest_stage_result else None
        ),
        primary_stdout_path=latest_stage_result.stdout_path if latest_stage_result else None,
        primary_stderr_path=latest_stage_result.stderr_path if latest_stage_result else None,
        stage_results=tuple(inspected_stage_results),
        notes=tuple(notes),
        started_at=first_stage_result.started_at if first_stage_result else None,
        completed_at=latest_stage_result.completed_at if latest_stage_result else None,
        duration_seconds=_run_duration_seconds(first_stage_result, latest_stage_result),
        token_usage=_aggregate_token_usage(stage_result.token_usage for stage_result in inspected_stage_results),
    )


def list_runs(target: WorkspacePaths | Path | str) -> tuple[InspectedRunSummary, ...]:
    """List run summaries from a workspace in deterministic directory order."""

    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    run_dirs = sorted(path for path in paths.runs_dir.iterdir() if path.is_dir())
    return tuple(inspect_run(run_dir) for run_dir in run_dirs)


def inspect_run_id(target: WorkspacePaths | Path | str, run_id: str) -> InspectedRunSummary | None:
    """Inspect one run by id from a workspace, returning None when absent."""

    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    run_dir = paths.runs_dir / run_id
    if not run_dir.is_dir():
        return None
    return inspect_run(run_dir)


def select_primary_run_artifact(summary: InspectedRunSummary) -> str | None:
    """Return the preferred tail target for one inspected run summary."""

    if summary.troubleshoot_report_path:
        return summary.troubleshoot_report_path
    if summary.primary_stdout_path:
        return summary.primary_stdout_path
    if summary.primary_stderr_path:
        return summary.primary_stderr_path
    if summary.stage_results:
        return summary.stage_results[-1].stage_result_path
    return None


def _failure_class_from_stage_result(stage_result: StageResultEnvelope) -> str | None:
    value = stage_result.metadata.get("failure_class")
    return value if isinstance(value, str) else None


def _string_metadata(stage_result: StageResultEnvelope, key: str) -> str | None:
    value = stage_result.metadata.get(key)
    return value if isinstance(value, str) else None


def _aggregate_token_usage(usages: Iterable[TokenUsage | None]) -> TokenUsage | None:
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    thinking_tokens = 0
    total_tokens = 0
    found = False
    for usage in usages:
        if usage is None:
            continue
        found = True
        input_tokens += usage.input_tokens
        cached_input_tokens += usage.cached_input_tokens
        output_tokens += usage.output_tokens
        thinking_tokens += usage.thinking_tokens
        total_tokens += usage.total_tokens
    if not found:
        return None
    return TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        total_tokens=total_tokens,
    )


def _run_duration_seconds(
    first_stage_result: InspectedStageResult | None,
    latest_stage_result: InspectedStageResult | None,
) -> float | None:
    if first_stage_result is None or latest_stage_result is None:
        return None
    return (
        _parse_iso_datetime(latest_stage_result.completed_at)
        - _parse_iso_datetime(first_stage_result.started_at)
    ).total_seconds()


def _parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _normalize_optional_run_relative_path(run_dir: Path, path_value: str | None) -> str | None:
    if path_value is None:
        return None
    return _normalize_run_relative_path(run_dir, Path(path_value))


def _normalize_run_relative_path(run_dir: Path, path_value: Path | str) -> str:
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = run_dir / candidate

    try:
        resolved_candidate = candidate.resolve()
    except OSError:
        resolved_candidate = candidate

    try:
        relative = resolved_candidate.relative_to(run_dir)
    except ValueError:
        return Path(path_value).as_posix()
    return relative.as_posix()


__all__ = [
    "InspectedRunSummary",
    "InspectedStageResult",
    "RunInspectionStatus",
    "inspect_run_id",
    "inspect_run",
    "list_runs",
    "select_primary_run_artifact",
]
