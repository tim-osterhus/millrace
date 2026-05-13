"""Runtime-owned durable source artifacts for idea intake."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from millrace_ai.contracts.stage_metadata import validate_safe_identifier

from .paths import WorkspacePaths, workspace_paths


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def idea_source_artifact_path(
    target: WorkspacePaths | Path | str,
    *,
    root_idea_id: str,
) -> Path:
    """Return the durable runtime-owned source path for an idea lineage root."""

    validate_safe_identifier(root_idea_id, field_name="root_idea_id")
    paths = _resolve_paths(target)
    return paths.intake_ideas_dir / f"{root_idea_id}.md"


def write_idea_source_artifact(
    target: WorkspacePaths | Path | str,
    *,
    root_idea_id: str,
    markdown: str,
) -> Path:
    """Persist the original idea markdown under runtime-owned intake storage."""

    path = idea_source_artifact_path(target, root_idea_id=root_idea_id)
    _atomic_write_text(path, markdown)
    return path


__all__ = ["idea_source_artifact_path", "write_idea_source_artifact"]
