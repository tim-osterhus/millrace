"""Read-only helpers for enumerating and summarizing runtime run artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Literal, Mapping, cast

from pydantic import ValidationError

from millrace_ai.contracts import RunTraceGraph, StageResultEnvelope, TokenUsage, WorkItemKind
from millrace_ai.paths import WorkspacePaths, workspace_paths
from millrace_ai.runtime.run_traces import (
    inspect_run_trace as _inspect_run_trace,
)
from millrace_ai.runtime.run_traces import (
    inspect_run_trace_id as _inspect_run_trace_id,
)
from millrace_ai.runtime.run_traces import is_stage_result_artifact_path

RunInspectionStatus = Literal["valid", "incomplete", "malformed"]
RunRuntimeOutcome = Literal["active", "complete", "blocked", "handoff", "incomplete", "malformed"]


@dataclass(frozen=True, slots=True)
class InspectedStageResult:
    stage_result_path: str
    request_id: str | None
    compiled_plan_id: str | None
    mode_id: str | None
    stage: str
    node_id: str
    stage_kind_id: str
    request_kind: str | None
    closure_target_root_spec_id: str | None
    closure_target_root_source_kind: str | None
    closure_target_root_source_id: str | None
    closure_target_root_source_path: str | None
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
    model_reasoning_effort: str | None
    started_at: str
    completed_at: str
    duration_seconds: float = 0.0
    token_usage: TokenUsage | None = None
    thinking_level: str | None = None
    model_assignment_alias_id: str | None = None
    model_assignment_source: str | None = None
    capability_grant_summaries: tuple[str, ...] = ()
    capability_support_summaries: tuple[str, ...] = ()
    failure_origin: str | None = None
    request_context_profile_id: str | None = None
    context_bundle_path: str | None = None
    context_artifact_refs: tuple[str, ...] = ()
    context_render_plan_id: str | None = None
    rendered_prompt_context_path: str | None = None
    runtime_effect_handler_id: str | None = None
    runtime_effect_operation_id: str | None = None
    runtime_effect_runner_id: str | None = None
    runtime_effect_legacy_handler_id: str | None = None
    runtime_effect_decision: str | None = None
    runtime_effect_failure_class: str | None = None
    runtime_effect_failure_message: str | None = None
    runtime_effect_mutation_phase: str | None = None
    runtime_effect_created_paths: tuple[str, ...] = ()
    runtime_effect_source_lifecycle_plan_id: str | None = None
    runtime_effect_source_lifecycle_action: str | None = None
    runtime_effect_failure_policy_id: str | None = None
    runtime_effect_recovery_action: str | None = None


@dataclass(frozen=True, slots=True)
class InspectedRunSummary:
    run_id: str
    run_dir: str
    status: RunInspectionStatus
    artifact_status: RunInspectionStatus
    runtime_outcome: RunRuntimeOutcome
    compiled_plan_id: str | None
    mode_id: str | None
    request_kind: str | None
    closure_target_root_spec_id: str | None
    closure_target_root_source_kind: str | None
    closure_target_root_source_id: str | None
    closure_target_root_source_path: str | None
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
    failure_origin: str | None = None
    runtime_effect_handler_id: str | None = None
    runtime_effect_operation_id: str | None = None
    runtime_effect_runner_id: str | None = None
    runtime_effect_legacy_handler_id: str | None = None
    runtime_effect_decision: str | None = None
    runtime_effect_failure_class: str | None = None
    runtime_effect_failure_message: str | None = None
    runtime_effect_mutation_phase: str | None = None
    runtime_effect_failure_policy_id: str | None = None
    runtime_effect_recovery_action: str | None = None
    terminal_state_id: str | None = None
    terminal_action_id: str | None = None
    terminal_action_router_consequence: str | None = None
    lifecycle_mutation_plan_id: str | None = None
    lifecycle_action_id: str | None = None
    terminal_writes_status: str | None = None
    terminal_metadata_source: str = "unknown"
    terminal_create_incident: bool = False
    runtime_operation_id: str | None = None


def inspect_run(run_dir: Path | str) -> InspectedRunSummary:
    """Inspect one run directory without mutating runtime state."""

    resolved_run_dir = Path(run_dir).expanduser().resolve()
    stage_results_dir = resolved_run_dir / "stage_results"
    notes: list[str] = []
    inspected_stage_results: list[InspectedStageResult] = []
    status: RunInspectionStatus = "valid"

    if not stage_results_dir.exists():
        runtime_outcome = _runtime_outcome_for_missing_stage_results(resolved_run_dir)
        return InspectedRunSummary(
            run_id=resolved_run_dir.name,
            run_dir=str(resolved_run_dir),
            status="incomplete",
            artifact_status="incomplete",
            runtime_outcome=runtime_outcome,
            compiled_plan_id=None,
            mode_id=None,
            request_kind=None,
            closure_target_root_spec_id=None,
            closure_target_root_source_kind=None,
            closure_target_root_source_id=None,
            closure_target_root_source_path=None,
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
        path for path in stage_results_dir.iterdir() if path.is_file() and is_stage_result_artifact_path(path)
    )
    if not stage_result_paths:
        runtime_outcome = _runtime_outcome_for_missing_stage_results(resolved_run_dir)
        return InspectedRunSummary(
            run_id=resolved_run_dir.name,
            run_dir=str(resolved_run_dir),
            status="incomplete",
            artifact_status="incomplete",
            runtime_outcome=runtime_outcome,
            compiled_plan_id=None,
            mode_id=None,
            request_kind=None,
            closure_target_root_spec_id=None,
            closure_target_root_source_kind=None,
            closure_target_root_source_id=None,
            closure_target_root_source_path=None,
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
                request_id=_string_metadata(stage_result, "request_id"),
                compiled_plan_id=_string_metadata(stage_result, "compiled_plan_id"),
                mode_id=_string_metadata(stage_result, "mode_id"),
                stage=stage_result.stage.value,
                node_id=stage_result.node_id,
                stage_kind_id=stage_result.stage_kind_id,
                request_kind=_string_metadata(stage_result, "request_kind"),
                closure_target_root_spec_id=_string_metadata(
                    stage_result,
                    "closure_target_root_spec_id",
                ),
                closure_target_root_source_kind=_string_metadata(
                    stage_result,
                    "closure_target_root_source_kind",
                ),
                closure_target_root_source_id=_string_metadata(
                    stage_result,
                    "closure_target_root_source_id",
                ),
                closure_target_root_source_path=_string_metadata(
                    stage_result,
                    "closure_target_root_source_path",
                ),
                terminal_result=stage_result.terminal_result.value,
                result_class=stage_result.result_class.value,
                work_item_kind=cast(WorkItemKind, stage_result.work_item_kind),
                work_item_id=stage_result.work_item_id,
                failure_class=_failure_class_from_stage_result(stage_result),
                failure_origin=_string_metadata(stage_result, "failure_origin"),
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
                thinking_level=stage_result.thinking_level,
                model_reasoning_effort=stage_result.model_reasoning_effort,
                model_assignment_alias_id=stage_result.model_assignment_alias_id,
                model_assignment_source=stage_result.model_assignment_source,
                started_at=stage_result.started_at.isoformat(),
                completed_at=stage_result.completed_at.isoformat(),
                duration_seconds=stage_result.duration_seconds,
                token_usage=stage_result.token_usage,
                capability_grant_summaries=_capability_grant_summaries(stage_result),
                capability_support_summaries=_capability_support_summaries(stage_result),
                request_context_profile_id=_string_metadata(
                    stage_result,
                    "request_context_profile_id",
                ),
                context_bundle_path=_normalize_optional_run_relative_path(
                    resolved_run_dir,
                    _string_metadata(stage_result, "context_bundle_path"),
                ),
                context_artifact_refs=_tuple_str_metadata(
                    stage_result,
                    "context_artifact_refs",
                ),
                context_render_plan_id=_string_metadata(
                    stage_result,
                    "context_render_plan_id",
                ),
                rendered_prompt_context_path=_normalize_optional_run_relative_path(
                    resolved_run_dir,
                    _string_metadata(stage_result, "rendered_prompt_context_path"),
                ),
                runtime_effect_handler_id=_string_metadata(
                    stage_result,
                    "runtime_effect_handler_id",
                ),
                runtime_effect_operation_id=_string_metadata(
                    stage_result,
                    "runtime_effect_operation_id",
                ),
                runtime_effect_runner_id=_string_metadata(
                    stage_result,
                    "runtime_effect_runner_id",
                ),
                runtime_effect_legacy_handler_id=_string_metadata(
                    stage_result,
                    "runtime_effect_legacy_handler_id",
                ),
                runtime_effect_decision=_string_metadata(
                    stage_result,
                    "runtime_effect_decision",
                ),
                runtime_effect_failure_class=_string_metadata(
                    stage_result,
                    "runtime_effect_failure_class",
                ),
                runtime_effect_failure_message=_string_metadata(
                    stage_result,
                    "runtime_effect_failure_message",
                ),
                runtime_effect_mutation_phase=_string_metadata(
                    stage_result,
                    "runtime_effect_mutation_phase",
                ),
                runtime_effect_created_paths=_tuple_str_metadata(
                    stage_result,
                    "runtime_effect_created_paths",
                ),
                runtime_effect_source_lifecycle_plan_id=_string_metadata(
                    stage_result,
                    "runtime_effect_source_lifecycle_plan_id",
                ),
                runtime_effect_source_lifecycle_action=_string_metadata(
                    stage_result,
                    "runtime_effect_source_lifecycle_action",
                ),
                runtime_effect_failure_policy_id=_string_metadata(
                    stage_result,
                    "runtime_effect_failure_policy_id",
                ),
                runtime_effect_recovery_action=_string_metadata(
                    stage_result,
                    "runtime_effect_recovery_action",
                ),
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
    latest_runtime_effect_stage_result = _latest_runtime_effect_stage_result(
        inspected_stage_results
    )
    runtime_outcome = _runtime_outcome_for_run(resolved_run_dir, latest_stage_result, status)
    latest_trace_edge = _latest_trace_edge(resolved_run_dir)
    return InspectedRunSummary(
        run_id=resolved_run_dir.name,
        run_dir=str(resolved_run_dir),
        status=status,
        artifact_status=status,
        runtime_outcome=runtime_outcome,
        compiled_plan_id=latest_stage_result.compiled_plan_id if latest_stage_result else None,
        mode_id=latest_stage_result.mode_id if latest_stage_result else None,
        request_kind=latest_stage_result.request_kind if latest_stage_result else None,
        closure_target_root_spec_id=(
            latest_stage_result.closure_target_root_spec_id if latest_stage_result else None
        ),
        closure_target_root_source_kind=(
            latest_stage_result.closure_target_root_source_kind if latest_stage_result else None
        ),
        closure_target_root_source_id=(
            latest_stage_result.closure_target_root_source_id if latest_stage_result else None
        ),
        closure_target_root_source_path=(
            latest_stage_result.closure_target_root_source_path if latest_stage_result else None
        ),
        work_item_kind=latest_stage_result.work_item_kind if latest_stage_result else None,
        work_item_id=latest_stage_result.work_item_id if latest_stage_result else None,
        failure_class=latest_stage_result.failure_class if latest_stage_result else None,
        failure_origin=latest_stage_result.failure_origin if latest_stage_result else None,
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
        runtime_effect_decision=(
            latest_runtime_effect_stage_result.runtime_effect_decision
            if latest_runtime_effect_stage_result
            else None
        ),
        runtime_effect_handler_id=(
            latest_runtime_effect_stage_result.runtime_effect_handler_id
            if latest_runtime_effect_stage_result
            else None
        ),
        runtime_effect_operation_id=(
            latest_runtime_effect_stage_result.runtime_effect_operation_id
            if latest_runtime_effect_stage_result
            else None
        ),
        runtime_effect_runner_id=(
            latest_runtime_effect_stage_result.runtime_effect_runner_id
            if latest_runtime_effect_stage_result
            else None
        ),
        runtime_effect_legacy_handler_id=(
            latest_runtime_effect_stage_result.runtime_effect_legacy_handler_id
            if latest_runtime_effect_stage_result
            else None
        ),
        runtime_effect_failure_class=(
            latest_runtime_effect_stage_result.runtime_effect_failure_class
            if latest_runtime_effect_stage_result
            else None
        ),
        runtime_effect_failure_message=(
            latest_runtime_effect_stage_result.runtime_effect_failure_message
            if latest_runtime_effect_stage_result
            else None
        ),
        runtime_effect_mutation_phase=(
            latest_runtime_effect_stage_result.runtime_effect_mutation_phase
            if latest_runtime_effect_stage_result
            else None
        ),
        runtime_effect_failure_policy_id=(
            latest_runtime_effect_stage_result.runtime_effect_failure_policy_id
            if latest_runtime_effect_stage_result
            else None
        ),
        runtime_effect_recovery_action=(
            latest_runtime_effect_stage_result.runtime_effect_recovery_action
            if latest_runtime_effect_stage_result
            else None
        ),
        terminal_state_id=(
            latest_trace_edge.terminal_state_id if latest_trace_edge is not None else None
        ),
        terminal_action_id=(
            latest_trace_edge.terminal_action_id if latest_trace_edge is not None else None
        ),
        terminal_action_router_consequence=(
            latest_trace_edge.terminal_action_router_consequence
            if latest_trace_edge is not None
            else None
        ),
        lifecycle_mutation_plan_id=(
            latest_trace_edge.lifecycle_mutation_plan_id
            if latest_trace_edge is not None
            else None
        ),
        lifecycle_action_id=(
            latest_trace_edge.lifecycle_action_id if latest_trace_edge is not None else None
        ),
        terminal_writes_status=(
            latest_trace_edge.terminal_writes_status if latest_trace_edge is not None else None
        ),
        terminal_metadata_source=(
            latest_trace_edge.terminal_metadata_source
            if latest_trace_edge is not None
            else ("inferred" if not (resolved_run_dir / "run_trace.json").is_file() else "unknown")
        ),
        terminal_create_incident=(
            latest_trace_edge.create_incident if latest_trace_edge is not None else False
        ),
        runtime_operation_id=(
            latest_trace_edge.runtime_operation_id if latest_trace_edge is not None else None
        ),
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


def inspect_run_trace(run_dir: Path | str) -> RunTraceGraph:
    """Inspect one run trace without mutating runtime state."""

    return _inspect_run_trace(run_dir)


def inspect_run_trace_id(target: WorkspacePaths | Path | str, run_id: str) -> RunTraceGraph | None:
    """Inspect one run trace by id from a workspace, returning None when absent."""

    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    return _inspect_run_trace_id(paths, run_id)


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
    if isinstance(value, str):
        return value
    runtime_effect_value = stage_result.metadata.get("runtime_effect_failure_class")
    return runtime_effect_value if isinstance(runtime_effect_value, str) else None


def _latest_runtime_effect_stage_result(
    stage_results: list[InspectedStageResult],
) -> InspectedStageResult | None:
    for stage_result in reversed(stage_results):
        if _stage_result_has_runtime_effect_metadata(stage_result):
            return stage_result
    return None


def _stage_result_has_runtime_effect_metadata(stage_result: InspectedStageResult) -> bool:
    return any(
        (
            stage_result.runtime_effect_handler_id,
            stage_result.runtime_effect_operation_id,
            stage_result.runtime_effect_runner_id,
            stage_result.runtime_effect_legacy_handler_id,
            stage_result.runtime_effect_decision,
            stage_result.runtime_effect_failure_class,
            stage_result.runtime_effect_failure_message,
            stage_result.runtime_effect_mutation_phase,
            stage_result.runtime_effect_created_paths,
            stage_result.runtime_effect_source_lifecycle_plan_id,
            stage_result.runtime_effect_source_lifecycle_action,
            stage_result.runtime_effect_failure_policy_id,
            stage_result.runtime_effect_recovery_action,
        )
    )


def _latest_trace_edge(run_dir: Path):
    trace_path = run_dir / "run_trace.json"
    if not trace_path.is_file():
        return None
    trace = _inspect_run_trace(run_dir)
    return trace.edges[-1] if trace.edges else None


def _runtime_outcome_for_missing_stage_results(run_dir: Path) -> RunRuntimeOutcome:
    trace_path = run_dir / "run_trace.json"
    if not trace_path.is_file():
        return "incomplete"
    return _inspect_run_trace(run_dir).status


def _runtime_outcome_for_run(
    run_dir: Path,
    latest_stage_result: InspectedStageResult | None,
    artifact_status: RunInspectionStatus,
) -> RunRuntimeOutcome:
    trace_path = run_dir / "run_trace.json"
    if trace_path.is_file():
        return _inspect_run_trace(run_dir).status
    if latest_stage_result is None:
        return "incomplete" if artifact_status == "valid" else artifact_status
    if artifact_status == "malformed":
        return "malformed"
    if latest_stage_result.result_class != "success":
        return "blocked"
    if latest_stage_result.terminal_result == "BLOCKED":
        return "blocked"
    if latest_stage_result.runtime_effect_failure_class is not None:
        return "blocked"
    return "incomplete"


def _string_metadata(stage_result: StageResultEnvelope, key: str) -> str | None:
    value = stage_result.metadata.get(key)
    return value if isinstance(value, str) else None


def _tuple_str_metadata(stage_result: StageResultEnvelope, key: str) -> tuple[str, ...]:
    value = stage_result.metadata.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _capability_grant_summaries(stage_result: StageResultEnvelope) -> tuple[str, ...]:
    values = stage_result.metadata.get("execution_capability_grants")
    if not isinstance(values, list):
        return ()
    summaries: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        grant_id = _dict_str(value, "grant_id")
        capability_id = _dict_str(value, "capability_id")
        decision = _dict_str(value, "decision_state")
        enforcement = _dict_str(value, "enforcement_mode")
        evidence = _dict_str(value, "evidence_status")
        approval_ref = value.get("approval_policy_ref")
        approval_policy = (
            _dict_str(approval_ref, "policy_id") if isinstance(approval_ref, dict) else None
        )
        parts = [
            _prefixed_value("grant_id", grant_id),
            _prefixed_value("capability", capability_id),
            _prefixed_value("decision", decision),
            _prefixed_value("enforcement", enforcement),
            _prefixed_value("evidence", evidence),
        ]
        if approval_policy is not None:
            parts.append(_prefixed_value("approval_policy", approval_policy))
        summaries.append(" ".join(part for part in parts if part))
    return tuple(summaries)


def _capability_support_summaries(stage_result: StageResultEnvelope) -> tuple[str, ...]:
    values = stage_result.metadata.get("capability_support_decisions")
    if not isinstance(values, list):
        return ()
    summaries: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        parts = [
            _prefixed_value("grant_id", _dict_str(value, "grant_id")),
            _prefixed_value("runner", _dict_str(value, "runner_id")),
            _prefixed_value("support", _dict_str(value, "support_state")),
            _prefixed_value("enforcement", _dict_str(value, "enforcement_mode")),
        ]
        evidence_available = value.get("evidence_available")
        if isinstance(evidence_available, bool):
            parts.append(f"evidence_available={str(evidence_available).lower()}")
        summaries.append(" ".join(part for part in parts if part))
    return tuple(summaries)


def _dict_str(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    return item if isinstance(item, str) and item else None


def _prefixed_value(prefix: str, value: str | None) -> str:
    return f"{prefix}={value}" if value is not None else ""


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
    "RunRuntimeOutcome",
    "inspect_run_id",
    "inspect_run_trace",
    "inspect_run_trace_id",
    "inspect_run",
    "list_runs",
    "select_primary_run_artifact",
]
