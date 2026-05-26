"""Artifact discovery and safety helpers for runner normalization."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.runners.requests import StageRunRequest


def resolved_report_artifact(request: StageRunRequest) -> str | None:
    for candidate in (request.preferred_report_path, request.preferred_troubleshoot_report_path):
        if not candidate:
            continue
        if artifact_exists(request.run_dir, candidate):
            return candidate
    return None


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


__all__ = ["artifact_exists", "merge_artifact_paths", "resolved_report_artifact"]
