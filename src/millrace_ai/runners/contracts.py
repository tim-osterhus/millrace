"""Runner invocation/completion artifacts persisted under per-run directories."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from millrace_ai.contracts import TokenUsage
from millrace_ai.runners.requests import RunnerRawResult, StageRunRequest


class _ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunnerInvocationArtifact(_ArtifactModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runner_invocation"] = "runner_invocation"

    request_id: str
    run_id: str
    stage: str
    request_kind: str
    active_work_item_id: str | None = None
    closure_target_root_spec_id: str | None = None
    closure_target_root_source_kind: str | None = None
    closure_target_root_source_id: str | None = None
    runner_name: str
    model_name: str | None = None
    thinking_level: str | None = None
    model_reasoning_effort: str | None = None
    model_assignment_alias_id: str | None = None
    model_assignment_source: str | None = None
    command: tuple[str, ...]
    prompt_path: str
    emitted_at: datetime
    execution_capability_grants: tuple[dict[str, JsonValue], ...] = ()
    capability_support_decisions: tuple[dict[str, JsonValue], ...] = ()


class RunnerCompletionArtifact(_ArtifactModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runner_completion"] = "runner_completion"

    request_id: str
    run_id: str
    stage: str
    request_kind: str
    active_work_item_id: str | None = None
    closure_target_root_spec_id: str | None = None
    closure_target_root_source_kind: str | None = None
    closure_target_root_source_id: str | None = None
    runner_name: str
    thinking_level: str | None = None
    model_reasoning_effort: str | None = None
    model_assignment_alias_id: str | None = None
    model_assignment_source: str | None = None
    exit_kind: str
    exit_code: int | None = None
    observed_exit_kind: str | None = None
    observed_exit_code: int | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    terminal_result_path: str | None = None
    event_log_path: str | None = None
    token_usage: TokenUsage | None = None
    started_at: datetime
    ended_at: datetime
    failure_class: str | None = None
    notes: tuple[str, ...] = ()
    command: tuple[str, ...]
    emitted_at: datetime
    failure_capability_class: str | None = None
    execution_capability_grants: tuple[dict[str, JsonValue], ...] = ()
    capability_support_decisions: tuple[dict[str, JsonValue], ...] = ()
    capability_evidence_refs: tuple[str, ...] = ()
    missing_capability_evidence_refs: tuple[str, ...] = ()


def write_runner_invocation(path: Path, artifact: RunnerInvocationArtifact) -> None:
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")


def write_runner_completion(path: Path, artifact: RunnerCompletionArtifact) -> None:
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")


def invocation_artifact_from_request(
    *,
    request: StageRunRequest,
    runner_name: str,
    command: tuple[str, ...],
    prompt_path: str,
    emitted_at: datetime,
) -> RunnerInvocationArtifact:
    return RunnerInvocationArtifact(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage.value,
        request_kind=request.request_kind,
        active_work_item_id=request.active_work_item_id,
        closure_target_root_spec_id=request.closure_target_root_spec_id,
        closure_target_root_source_kind=request.closure_target_root_source_kind,
        closure_target_root_source_id=request.closure_target_root_source_id,
        runner_name=runner_name,
        model_name=request.model_name,
        thinking_level=request.thinking_level,
        model_reasoning_effort=request.model_reasoning_effort,
        model_assignment_alias_id=request.model_assignment_alias_id,
        model_assignment_source=request.model_assignment_source,
        command=command,
        prompt_path=prompt_path,
        emitted_at=emitted_at,
        execution_capability_grants=tuple(
            grant.model_dump(mode="json") for grant in request.execution_capability_grants
        ),
        capability_support_decisions=tuple(
            decision.model_dump(mode="json")
            for decision in request.capability_support_decisions
        ),
    )


def completion_artifact_from_raw_result(
    *,
    request: StageRunRequest,
    runner_name: str,
    raw_result: RunnerRawResult,
    command: tuple[str, ...],
    emitted_at: datetime,
    failure_class: str | None = None,
    notes: tuple[str, ...] = (),
) -> RunnerCompletionArtifact:
    return RunnerCompletionArtifact(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage.value,
        request_kind=request.request_kind,
        active_work_item_id=request.active_work_item_id,
        closure_target_root_spec_id=request.closure_target_root_spec_id,
        closure_target_root_source_kind=request.closure_target_root_source_kind,
        closure_target_root_source_id=request.closure_target_root_source_id,
        runner_name=runner_name,
        thinking_level=raw_result.thinking_level,
        model_reasoning_effort=raw_result.model_reasoning_effort,
        model_assignment_alias_id=request.model_assignment_alias_id,
        model_assignment_source=request.model_assignment_source,
        exit_kind=raw_result.exit_kind,
        exit_code=raw_result.exit_code,
        observed_exit_kind=raw_result.observed_exit_kind,
        observed_exit_code=raw_result.observed_exit_code,
        stdout_path=raw_result.stdout_path,
        stderr_path=raw_result.stderr_path,
        terminal_result_path=raw_result.terminal_result_path,
        event_log_path=raw_result.event_log_path,
        token_usage=raw_result.token_usage,
        started_at=raw_result.started_at,
        ended_at=raw_result.ended_at,
        failure_class=failure_class,
        notes=notes,
        command=command,
        emitted_at=emitted_at,
        failure_capability_class=raw_result.failure_class,
        execution_capability_grants=tuple(
            grant.model_dump(mode="json") for grant in request.execution_capability_grants
        ),
        capability_support_decisions=tuple(
            decision.model_dump(mode="json")
            for decision in (
                raw_result.capability_support_decisions
                or request.capability_support_decisions
            )
        ),
        capability_evidence_refs=raw_result.capability_evidence_refs,
        missing_capability_evidence_refs=raw_result.missing_capability_evidence_refs,
    )


__all__ = [
    "RunnerCompletionArtifact",
    "RunnerInvocationArtifact",
    "completion_artifact_from_raw_result",
    "invocation_artifact_from_request",
    "write_runner_completion",
    "write_runner_invocation",
]
