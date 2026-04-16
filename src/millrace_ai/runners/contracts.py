"""Runner invocation/completion artifacts persisted under per-run directories."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from millrace_ai.runners.requests import RunnerRawResult, StageRunRequest


class _ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunnerInvocationArtifact(_ArtifactModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runner_invocation"] = "runner_invocation"

    request_id: str
    run_id: str
    stage: str
    runner_name: str
    model_name: str | None = None
    command: tuple[str, ...]
    prompt_path: str
    emitted_at: datetime


class RunnerCompletionArtifact(_ArtifactModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runner_completion"] = "runner_completion"

    request_id: str
    run_id: str
    stage: str
    runner_name: str
    exit_kind: str
    exit_code: int | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    terminal_result_path: str | None = None
    started_at: datetime
    ended_at: datetime
    failure_class: str | None = None
    notes: tuple[str, ...] = ()
    command: tuple[str, ...]
    emitted_at: datetime


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
        runner_name=runner_name,
        model_name=request.model_name,
        command=command,
        prompt_path=prompt_path,
        emitted_at=emitted_at,
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
        runner_name=runner_name,
        exit_kind=raw_result.exit_kind,
        exit_code=raw_result.exit_code,
        stdout_path=raw_result.stdout_path,
        stderr_path=raw_result.stderr_path,
        terminal_result_path=raw_result.terminal_result_path,
        started_at=raw_result.started_at,
        ended_at=raw_result.ended_at,
        failure_class=failure_class,
        notes=notes,
        command=command,
        emitted_at=emitted_at,
    )


__all__ = [
    "RunnerCompletionArtifact",
    "RunnerInvocationArtifact",
    "completion_artifact_from_raw_result",
    "invocation_artifact_from_request",
    "write_runner_completion",
    "write_runner_invocation",
]
