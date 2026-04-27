"""Runner output normalization and terminal extraction."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from millrace_ai.contracts import (
    ExecutionTerminalResult,
    LearningTerminalResult,
    PlanningTerminalResult,
    ResultClass,
    StageResultEnvelope,
    TerminalResult,
    WorkItemKind,
)
from millrace_ai.contracts.stage_metadata import (
    blocked_terminal_for_plane,
    terminal_result_for_plane,
)

from .requests import (
    RunnerExitKind,
    RunnerRawResult,
    StageRunRequest,
    _TerminalExtraction,
)

_TERMINAL_TOKEN_PATTERN = re.compile(r"^###\s+([A-Z_]+)\s*$")


class _StructuredTerminalResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str | None = None
    terminal_result: str
    result_class: str | None = None
    summary_artifact_paths: tuple[str, ...] = ()


def normalize_stage_result(
    request: StageRunRequest,
    raw_result: RunnerRawResult,
) -> StageResultEnvelope:
    """Normalize one runner output into a deterministic stage result envelope."""

    work_item_kind, work_item_id = _request_result_identity(request)

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
    assert isinstance(result_class, ResultClass)
    terminal_result = extraction.terminal_result
    assert isinstance(
        terminal_result,
        (ExecutionTerminalResult, PlanningTerminalResult, LearningTerminalResult),
    )
    report_artifact = _resolved_report_artifact(request)

    return StageResultEnvelope(
        run_id=request.run_id,
        plane=request.plane,
        stage=request.stage,
        node_id=request.node_id,
        stage_kind_id=request.stage_kind_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result=terminal_result,
        result_class=result_class,
        summary_status_marker=f"### {terminal_result.value}",
        success=result_class is ResultClass.SUCCESS,
        retryable=False,
        exit_code=raw_result.exit_code or 0,
        duration_seconds=(raw_result.ended_at - raw_result.started_at).total_seconds(),
        artifact_paths=_merge_artifact_paths(
            extraction.artifact_paths,
            report_artifact,
            raw_result.event_log_path,
        ),
        report_artifact=report_artifact,
        detected_marker=extraction.detected_marker,
        stdout_path=raw_result.stdout_path,
        stderr_path=raw_result.stderr_path,
        runner_name=raw_result.runner_name,
        model_name=raw_result.model_name,
        model_reasoning_effort=raw_result.model_reasoning_effort or request.model_reasoning_effort,
        token_usage=raw_result.token_usage,
        notes=extraction.notes + _transport_reconciliation_notes(raw_result),
        metadata={
            **_request_metadata(request),
            "normalization_source": (
                "structured_result_file"
                if raw_result.terminal_result_path
                else "stdout_terminal_token"
            ),
            "failure_class": None,
            "valid_terminal_result": True,
            "raw_exit_kind": _raw_exit_kind(raw_result),
            "raw_exit_code": _raw_exit_code(raw_result),
            "timeout_reconciled": _timeout_reconciled(raw_result),
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

    terminal_result = _terminal_result_for_request(request, payload.terminal_result)
    if terminal_result is None:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=payload.summary_artifact_paths,
            failure_class="illegal_terminal_result",
            notes=(
                f"terminal result {payload.terminal_result!r} is illegal for request node {request.node_id}",
            ),
        )

    resolved_result_class = _resolve_result_class(
        request,
        payload.terminal_result,
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
    terminal_result = _terminal_result_for_request(request, final_token)
    if terminal_result is None:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=f"### {final_token}",
            artifact_paths=(),
            failure_class="illegal_terminal_result",
            notes=(
                f"terminal token {final_token!r} is illegal for request node {request.node_id}",
            ),
        )

    result_class = _resolve_result_class(request, final_token, None)
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
    blocked_terminal = blocked_terminal_for_plane(request.plane)
    work_item_kind, work_item_id = _request_result_identity(request)
    report_artifact = _resolved_report_artifact(request)

    return StageResultEnvelope(
        run_id=request.run_id,
        plane=request.plane,
        stage=request.stage,
        node_id=request.node_id,
        stage_kind_id=request.stage_kind_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result=blocked_terminal,
        result_class=ResultClass.RECOVERABLE_FAILURE,
        summary_status_marker="### BLOCKED",
        success=False,
        retryable=True,
        exit_code=raw_result.exit_code or 1,
        duration_seconds=(raw_result.ended_at - raw_result.started_at).total_seconds(),
        artifact_paths=_merge_artifact_paths(
            artifact_paths,
            report_artifact,
            raw_result.event_log_path,
        ),
        report_artifact=report_artifact,
        detected_marker=detected_marker,
        stdout_path=raw_result.stdout_path,
        stderr_path=raw_result.stderr_path,
        runner_name=raw_result.runner_name,
        model_name=raw_result.model_name,
        model_reasoning_effort=raw_result.model_reasoning_effort or request.model_reasoning_effort,
        token_usage=raw_result.token_usage,
        notes=notes,
        metadata={
            **_request_metadata(request),
            "normalization_source": "failure",
            "failure_class": failure_class,
            "valid_terminal_result": False,
            "raw_exit_kind": _raw_exit_kind(raw_result),
            "raw_exit_code": _raw_exit_code(raw_result),
            "timeout_reconciled": _timeout_reconciled(raw_result),
        },
        started_at=raw_result.started_at,
        completed_at=raw_result.ended_at,
    )


def _terminal_result_for_request(
    request: StageRunRequest,
    token: str,
) -> TerminalResult | None:
    if f"### {token}" not in request.legal_terminal_markers:
        return None
    return terminal_result_for_plane(request.plane, token)


def _resolve_result_class(
    request: StageRunRequest,
    terminal_token: str,
    raw_result_class: str | None,
) -> ResultClass | None:
    allowed_result_classes = request.allowed_result_classes_by_outcome.get(terminal_token)
    if not allowed_result_classes:
        return None
    if raw_result_class is None:
        if len(allowed_result_classes) == 1:
            return allowed_result_classes[0]
        if terminal_token == "BLOCKED" and ResultClass.BLOCKED in allowed_result_classes:
            return ResultClass.BLOCKED
        return None

    try:
        result_class = ResultClass(raw_result_class)
    except ValueError:
        return None

    if result_class not in allowed_result_classes:
        return None
    return result_class


def _raw_exit_kind(raw_result: RunnerRawResult) -> str:
    return raw_result.observed_exit_kind or raw_result.exit_kind


def _raw_exit_code(raw_result: RunnerRawResult) -> int | None:
    if raw_result.observed_exit_code is not None:
        return raw_result.observed_exit_code
    return raw_result.exit_code


def _timeout_reconciled(raw_result: RunnerRawResult) -> bool:
    return raw_result.observed_exit_kind == "timeout" and raw_result.exit_kind == "completed"


def _transport_reconciliation_notes(raw_result: RunnerRawResult) -> tuple[str, ...]:
    if not _timeout_reconciled(raw_result):
        return ()
    return (
        "runner timeout was reconciled after a final terminal marker was captured",
    )


def _resolved_report_artifact(request: StageRunRequest) -> str | None:
    for candidate in (request.preferred_report_path, request.preferred_troubleshoot_report_path):
        if not candidate:
            continue
        if _artifact_exists(request.run_dir, candidate):
            return candidate
    return None


def _request_result_identity(request: StageRunRequest) -> tuple[WorkItemKind, str]:
    if request.request_kind == "closure_target":
        if request.closure_target_root_spec_id is None:
            raise ValueError("closure_target_root_spec_id is required for closure_target requests")
        return (WorkItemKind.SPEC, request.closure_target_root_spec_id)
    if request.request_kind == "learning_request":
        if request.active_work_item_id is None:
            raise ValueError("active_work_item_id is required for learning_request requests")
        return (WorkItemKind.LEARNING_REQUEST, request.active_work_item_id)

    if request.active_work_item_kind is None or request.active_work_item_id is None:
        raise ValueError(
            "active_work_item_kind and active_work_item_id are required to normalize stage results"
        )
    return (request.active_work_item_kind, request.active_work_item_id)


def _merge_artifact_paths(
    artifact_paths: tuple[str, ...],
    *additional_artifacts: str | None,
) -> tuple[str, ...]:
    merged = list(artifact_paths)
    for artifact in additional_artifacts:
        if artifact and artifact not in merged:
            merged.append(artifact)
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


def _request_metadata(request: StageRunRequest) -> dict[str, JsonValue]:
    return {
        "request_id": request.request_id,
        "request_kind": request.request_kind,
        "mode_id": request.mode_id,
        "compiled_plan_id": request.compiled_plan_id,
        "closure_target_root_spec_id": request.closure_target_root_spec_id,
        "closure_target_root_idea_id": request.closure_target_root_idea_id,
        "preferred_rubric_path": request.preferred_rubric_path,
        "preferred_verdict_path": request.preferred_verdict_path,
        "preferred_report_path": request.preferred_report_path,
        "skill_revision_evidence_path": request.skill_revision_evidence_path,
        "model_reasoning_effort": request.model_reasoning_effort,
    }


__all__ = ["normalize_stage_result"]
