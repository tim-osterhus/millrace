"""Codex CLI artifact materialization helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from millrace_ai.runners.adapters._prompting import legal_terminal_markers
from millrace_ai.runners.requests import StageRunRequest


@dataclass(frozen=True, slots=True)
class CodexCliArtifactPaths:
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path
    event_log_path: Path
    output_last_message_path: Path
    invocation_path: Path
    completion_path: Path


def codex_cli_artifact_paths(run_dir: Path, request_id: str) -> CodexCliArtifactPaths:
    return CodexCliArtifactPaths(
        prompt_path=run_dir / f"runner_prompt.{request_id}.md",
        stdout_path=run_dir / f"runner_stdout.{request_id}.txt",
        stderr_path=run_dir / f"runner_stderr.{request_id}.txt",
        event_log_path=run_dir / f"runner_events.{request_id}.jsonl",
        output_last_message_path=run_dir / f"runner_last_message.{request_id}.txt",
        invocation_path=run_dir / f"runner_invocation.{request_id}.json",
        completion_path=run_dir / f"runner_completion.{request_id}.json",
    )


def persist_event_log(stdout_path: Path, event_log_path: Path) -> Path | None:
    if not stdout_path.exists():
        return None
    event_log_path.write_bytes(stdout_path.read_bytes())
    stdout_path.unlink()
    return event_log_path


def materialize_stdout_artifact(
    *,
    stdout_path: Path,
    output_last_message_path: Path,
    event_log_path: Path | None,
) -> Path | None:
    if output_last_message_path.exists():
        stdout_path.write_text(output_last_message_path.read_text(encoding="utf-8"), encoding="utf-8")
        return stdout_path
    if event_log_path is not None and event_log_path.exists():
        stdout_path.write_text(event_log_path.read_text(encoding="utf-8"), encoding="utf-8")
        return stdout_path
    return None


TERMINAL_MARKER_PATTERN = re.compile(r"^###\s+([A-Z_]+)\s*$")


def reconciled_timeout_terminal_marker(
    request: StageRunRequest,
    *,
    output_last_message_path: Path,
) -> str | None:
    if not output_last_message_path.exists():
        return None

    try:
        lines = output_last_message_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    stripped_nonempty = [line.strip() for line in lines if line.strip()]
    if not stripped_nonempty:
        return None

    legal_markers = {
        marker.removeprefix("### ").strip()
        for marker in legal_terminal_markers(request)
    }
    observed_markers: list[str] = []
    for line in lines:
        match = TERMINAL_MARKER_PATTERN.match(line.strip())
        if match is None:
            continue
        marker = match.group(1)
        if marker in legal_markers:
            observed_markers.append(marker)

    if len(observed_markers) != 1:
        return None

    marker = observed_markers[0]
    if stripped_nonempty[-1] != f"### {marker}":
        return None

    return marker


__all__ = [
    "CodexCliArtifactPaths",
    "codex_cli_artifact_paths",
    "materialize_stdout_artifact",
    "persist_event_log",
    "reconciled_timeout_terminal_marker",
]
