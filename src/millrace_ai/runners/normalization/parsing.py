"""Terminal result parsing for runner normalization."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from millrace_ai.runners.normalization.artifacts import artifact_exists
from millrace_ai.runners.normalization.terminal_results import (
    resolve_result_class,
    terminal_result_for_request,
)
from millrace_ai.runners.requests import RunnerRawResult, StageRunRequest, _TerminalExtraction

_TERMINAL_TOKEN_PATTERN = re.compile(r"^###\s+([A-Z_]+)\s*$")


class _StructuredTerminalResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str | None = None
    terminal_result: str
    result_class: str | None = None
    summary_artifact_paths: tuple[str, ...] = ()


def extract_terminal_result(
    request: StageRunRequest,
    raw_result: RunnerRawResult,
) -> _TerminalExtraction:
    if raw_result.terminal_result_path:
        return extract_from_structured_result_file(
            request,
            Path(raw_result.terminal_result_path),
        )

    return extract_from_stdout_tokens(request, raw_result.stdout_path)


def extract_from_structured_result_file(
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
            notes=("structured terminal result stage does not match run request stage",),
        )

    terminal_result = terminal_result_for_request(request, payload.terminal_result)
    if terminal_result is None:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=payload.summary_artifact_paths,
            failure_class="illegal_terminal_result",
            notes=(
                f"terminal result {payload.terminal_result!r} is illegal "
                f"for request node {request.node_id}",
            ),
        )

    resolved_result_class = resolve_result_class(
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
            notes=("structured terminal result class is incompatible with terminal_result",),
        )

    missing_artifacts = tuple(
        candidate
        for candidate in payload.summary_artifact_paths
        if not artifact_exists(request.run_dir, candidate)
    )
    if missing_artifacts:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=None,
            artifact_paths=payload.summary_artifact_paths,
            failure_class="missing_required_artifact",
            notes=("missing required summary artifacts: " + ", ".join(missing_artifacts),),
        )

    return _TerminalExtraction(
        terminal_result=terminal_result,
        result_class=resolved_result_class,
        detected_marker=f"### {terminal_result.value}",
        artifact_paths=payload.summary_artifact_paths,
        failure_class=None,
        notes=("terminal result resolved from structured result file",),
    )


def extract_from_stdout_tokens(
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
    terminal_result = terminal_result_for_request(request, final_token)
    if terminal_result is None:
        return _TerminalExtraction(
            terminal_result=None,
            result_class=None,
            detected_marker=f"### {final_token}",
            artifact_paths=(),
            failure_class="illegal_terminal_result",
            notes=(f"terminal token {final_token!r} is illegal for request node {request.node_id}",),
        )

    result_class = resolve_result_class(request, final_token, None)
    assert result_class is not None

    return _TerminalExtraction(
        terminal_result=terminal_result,
        result_class=result_class,
        detected_marker=f"### {final_token}",
        artifact_paths=(),
        failure_class=None,
        notes=("terminal result resolved from stdout token",),
    )


__all__ = [
    "extract_from_structured_result_file",
    "extract_from_stdout_tokens",
    "extract_terminal_result",
]
