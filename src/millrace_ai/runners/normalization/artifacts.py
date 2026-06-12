"""Artifact discovery and safety helpers for runner normalization."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import JsonValue

from millrace_ai.contracts import ExecutionStageName, ExecutionTerminalResult, TerminalOutcome
from millrace_ai.contracts.terminal_outcomes import terminal_outcome_value
from millrace_ai.runners.requests import StageRunRequest


def resolved_report_artifact(request: StageRunRequest) -> str | None:
    for candidate in (request.preferred_report_path, request.preferred_troubleshoot_report_path):
        if not candidate:
            continue
        if artifact_exists(request.run_dir, candidate):
            return candidate
    return None


def discovered_stage_artifact_paths(
    request: StageRunRequest,
    terminal_result: TerminalOutcome,
) -> tuple[str, ...]:
    if not _is_successful_consultant_continuation(request, terminal_result):
        return ()

    paths: list[str] = []
    for filename in ("consultant_decision.json", "consultant_summary.md"):
        artifact_path = _run_artifact_path(request, filename)
        if artifact_path is not None:
            paths.append(str(artifact_path))
    return tuple(paths)


def stage_artifact_metadata(
    request: StageRunRequest,
    terminal_result: TerminalOutcome,
) -> dict[str, JsonValue]:
    if not _is_successful_consultant_continuation(request, terminal_result):
        return {}

    decision_path = _run_artifact_path(request, "consultant_decision.json")
    if decision_path is None:
        return {}

    try:
        raw_payload = json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw_payload, dict):
        return {}

    metadata: dict[str, JsonValue] = {"consultant_decision_path": str(decision_path)}
    for key in ("target_stage", "resume_stage"):
        value = raw_payload.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized:
            metadata[key] = normalized
    return metadata


def merge_artifact_paths(
    artifact_paths: tuple[str, ...],
    *additional_artifacts: str | None,
) -> tuple[str, ...]:
    merged = list(artifact_paths)
    for artifact in additional_artifacts:
        if artifact and artifact not in merged:
            merged.append(artifact)
    return tuple(merged)


def artifact_exists(run_dir: str, candidate_path: str) -> bool:
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


def _is_successful_consultant_continuation(
    request: StageRunRequest,
    terminal_result: TerminalOutcome,
) -> bool:
    return (
        request.stage is ExecutionStageName.CONSULTANT
        and terminal_outcome_value(terminal_result) == ExecutionTerminalResult.CONSULT_COMPLETE.value
    )


def _run_artifact_path(request: StageRunRequest, filename: str) -> Path | None:
    run_root = Path(request.run_dir).expanduser().resolve()
    candidate = run_root / filename
    try:
        resolved_candidate = candidate.resolve()
    except OSError:
        return None
    try:
        resolved_candidate.relative_to(run_root)
    except ValueError:
        return None
    if not resolved_candidate.exists():
        return None
    return resolved_candidate


__all__ = [
    "artifact_exists",
    "discovered_stage_artifact_paths",
    "merge_artifact_paths",
    "resolved_report_artifact",
    "stage_artifact_metadata",
]
