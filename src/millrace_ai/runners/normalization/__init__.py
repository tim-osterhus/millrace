"""Runner output normalization facade."""

from __future__ import annotations

from millrace_ai.contracts import ResultClass, StageResultEnvelope, TerminalOutcome
from millrace_ai.contracts.stage_metadata import blocked_terminal_for_plane
from millrace_ai.runners.normalization.artifacts import (
    discovered_stage_artifact_paths,
    merge_artifact_paths,
    resolved_report_artifact,
    stage_artifact_metadata,
)
from millrace_ai.runners.normalization.errors import (
    FailureClassification,
    classification_for_failure_class,
    classify_raw_exit_failure,
    failure_origin_for_failure_class,
    raw_exit_code,
    raw_exit_kind,
    timeout_reconciled,
    transport_reconciliation_notes,
)
from millrace_ai.runners.normalization.parsing import extract_terminal_result
from millrace_ai.runners.normalization.provenance import (
    request_metadata,
    request_result_identity,
    resolved_thinking_level,
)
from millrace_ai.runners.requests import RunnerRawResult, StageRunRequest


def normalize_stage_result(
    request: StageRunRequest,
    raw_result: RunnerRawResult,
) -> StageResultEnvelope:
    """Normalize one runner output into a deterministic stage result envelope."""

    work_item_family_id, work_item_kind, work_item_id = request_result_identity(request)

    identity_notes = _identity_mismatch_notes(request, raw_result)
    if identity_notes:
        return _failure_envelope(
            request,
            raw_result,
            failure_class="runner_transport_failure",
            notes=identity_notes,
        )

    exit_failure = (
        None if raw_result.exit_kind == "completed" else classify_raw_exit_failure(raw_result)
    )
    if exit_failure is not None:
        return _failure_envelope(
            request,
            raw_result,
            failure_class=exit_failure.failure_class,
            notes=(f"runner exited with {raw_result.exit_kind}",),
            classification=exit_failure,
        )
    if raw_result.exit_kind == "completed" and raw_result.exit_code not in (None, 0):
        exit_failure = classify_raw_exit_failure(
            raw_result
        ) or classification_for_failure_class("runner_transport_failure")
        return _failure_envelope(
            request,
            raw_result,
            failure_class=exit_failure.failure_class,
            notes=("runner completed with non-zero exit code",),
            classification=exit_failure,
        )
    if raw_result.missing_capability_evidence_refs:
        missing_refs = ", ".join(raw_result.missing_capability_evidence_refs)
        return _failure_envelope(
            request,
            raw_result,
            failure_class="capability_evidence_missing",
            notes=(f"missing required capability evidence: {missing_refs}",),
        )

    extraction = extract_terminal_result(request, raw_result)
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
    assert isinstance(terminal_result, TerminalOutcome)
    report_artifact = resolved_report_artifact(request)
    discovered_artifacts = discovered_stage_artifact_paths(request, terminal_result)
    artifact_metadata = stage_artifact_metadata(request, terminal_result)

    return StageResultEnvelope(
        run_id=request.run_id,
        plane=request.plane,
        stage=request.stage,
        node_id=request.node_id,
        stage_kind_id=request.stage_kind_id,
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result=terminal_result,
        result_class=result_class,
        summary_status_marker=f"### {terminal_result.value}",
        success=result_class is ResultClass.SUCCESS,
        retryable=False,
        exit_code=raw_result.exit_code or 0,
        duration_seconds=(raw_result.ended_at - raw_result.started_at).total_seconds(),
        artifact_paths=merge_artifact_paths(
            extraction.artifact_paths,
            report_artifact,
            *discovered_artifacts,
            raw_result.event_log_path,
        ),
        report_artifact=report_artifact,
        detected_marker=extraction.detected_marker,
        stdout_path=raw_result.stdout_path,
        stderr_path=raw_result.stderr_path,
        runner_name=raw_result.runner_name,
        model_name=raw_result.model_name,
        thinking_level=resolved_thinking_level(request, raw_result),
        model_reasoning_effort=raw_result.model_reasoning_effort or request.model_reasoning_effort,
        model_assignment_alias_id=request.model_assignment_alias_id,
        model_assignment_source=request.model_assignment_source,
        token_usage=raw_result.token_usage,
        notes=extraction.notes + transport_reconciliation_notes(raw_result),
        metadata={
            **request_metadata(request),
            **artifact_metadata,
            "normalization_source": (
                "structured_result_file"
                if raw_result.terminal_result_path
                else "stdout_terminal_token"
            ),
            "failure_class": None,
            "failure_origin": None,
            "valid_terminal_result": True,
            "raw_exit_kind": raw_exit_kind(raw_result),
            "raw_exit_code": raw_exit_code(raw_result),
            "timeout_reconciled": timeout_reconciled(raw_result),
            "capability_evidence_refs": list(raw_result.capability_evidence_refs),
            "missing_capability_evidence_refs": list(
                raw_result.missing_capability_evidence_refs
            ),
        },
        started_at=raw_result.started_at,
        completed_at=raw_result.ended_at,
    )


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
    classification: FailureClassification | None = None,
) -> StageResultEnvelope:
    blocked_terminal = blocked_terminal_for_plane(request.plane)
    work_item_family_id, work_item_kind, work_item_id = request_result_identity(request)
    report_artifact = resolved_report_artifact(request)

    failure_classification = classification or classification_for_failure_class(failure_class)
    return StageResultEnvelope(
        run_id=request.run_id,
        plane=request.plane,
        stage=request.stage,
        node_id=request.node_id,
        stage_kind_id=request.stage_kind_id,
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result=blocked_terminal,
        result_class=ResultClass.RECOVERABLE_FAILURE,
        summary_status_marker="### BLOCKED",
        success=False,
        retryable=True,
        exit_code=raw_result.exit_code or 1,
        duration_seconds=(raw_result.ended_at - raw_result.started_at).total_seconds(),
        artifact_paths=merge_artifact_paths(
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
        thinking_level=resolved_thinking_level(request, raw_result),
        model_reasoning_effort=raw_result.model_reasoning_effort or request.model_reasoning_effort,
        model_assignment_alias_id=request.model_assignment_alias_id,
        model_assignment_source=request.model_assignment_source,
        token_usage=raw_result.token_usage,
        notes=notes,
        metadata={
            **request_metadata(request),
            "normalization_source": "failure",
            "failure_class": failure_classification.failure_class,
            "failure_origin": failure_origin_for_failure_class(
                failure_classification.failure_class,
            ),
            "blocked_origin": failure_classification.blocked_origin,
            "failure_scope": failure_classification.failure_scope,
            "auto_requeue_candidate": failure_classification.auto_requeue_candidate,
            "failure_classifier_code": failure_classification.classifier_code,
            "valid_terminal_result": False,
            "raw_exit_kind": raw_exit_kind(raw_result),
            "raw_exit_code": raw_exit_code(raw_result),
            "timeout_reconciled": timeout_reconciled(raw_result),
            "capability_evidence_refs": list(raw_result.capability_evidence_refs),
            "missing_capability_evidence_refs": list(
                raw_result.missing_capability_evidence_refs
            ),
        },
        started_at=raw_result.started_at,
        completed_at=raw_result.ended_at,
    )


__all__ = ["normalize_stage_result"]
