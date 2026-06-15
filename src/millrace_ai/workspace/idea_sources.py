"""Runtime-owned durable source artifacts for idea intake."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from millrace_ai.contracts.stage_metadata import validate_safe_identifier

from .paths import WorkspacePaths, workspace_paths

_IDEA_ID_SANITIZER = re.compile(r"[^a-z0-9._-]+")


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


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def normalized_content_hash(markdown: str) -> str:
    """Return a short hash over normalized idea markdown content."""

    normalized = "\n".join(line.rstrip() for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    normalized = normalized.strip() + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]


def stable_idea_id(*, title: str, markdown: str) -> str:
    """Derive a stable idea id from normalized title plus normalized-content hash."""

    slug = _IDEA_ID_SANITIZER.sub("-", title.strip().lower()).strip("-.")
    if not slug:
        slug = "idea"
    if slug.startswith("idea-"):
        slug = slug[5:]
    return f"idea-{slug}-{normalized_content_hash(markdown)}"


def idea_source_artifact_path(
    target: WorkspacePaths | Path | str,
    *,
    root_idea_id: str,
) -> Path:
    """Return the durable runtime-owned source path for an idea lineage root."""

    validate_safe_identifier(root_idea_id, field_name="root_idea_id")
    paths = _resolve_paths(target)
    return paths.intake_sources_idea_dir / f"{root_idea_id}.md"


def idea_inbox_artifact_path(
    target: WorkspacePaths | Path | str,
    *,
    source_name: str,
) -> Path:
    """Return the canonical runtime-owned idea inbox path for operator intake."""

    validate_safe_identifier(source_name, field_name="source_name")
    paths = _resolve_paths(target)
    return paths.intake_ideas_inbox_dir / source_name


def idea_normalized_artifact_path(
    target: WorkspacePaths | Path | str,
    *,
    root_idea_id: str,
) -> Path:
    """Return the normalized metadata path for an idea-derived spec."""

    validate_safe_identifier(root_idea_id, field_name="root_idea_id")
    paths = _resolve_paths(target)
    return paths.intake_ideas_normalized_dir / f"{root_idea_id}.json"


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


def write_idea_inbox_artifact(
    target: WorkspacePaths | Path | str,
    *,
    source_name: str,
    markdown: str,
) -> Path:
    """Stage operator-provided idea markdown in the canonical runtime intake inbox."""

    path = idea_inbox_artifact_path(target, source_name=source_name)
    if path.exists():
        raise FileExistsError(path)
    _atomic_write_text(path, markdown)
    return path


def write_idea_normalized_artifact(
    target: WorkspacePaths | Path | str,
    *,
    root_idea_id: str,
    metadata: dict[str, Any],
) -> Path:
    """Persist normalized metadata for an idea-derived spec."""

    path = idea_normalized_artifact_path(target, root_idea_id=root_idea_id)
    _atomic_write_json(path, metadata)
    return path


def archive_idea_inbox_artifact(
    target: WorkspacePaths | Path | str,
    idea_path: Path,
    *,
    legacy: bool,
) -> Path:
    """Archive a consumed idea inbox markdown file."""

    paths = _resolve_paths(target)
    archive_dir = paths.intake_ideas_archived_legacy_dir if legacy else paths.intake_ideas_archived_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_artifact_path(archive_dir / idea_path.name)
    idea_path.replace(destination)
    return destination


def archive_invalid_legacy_idea_artifacts(
    target: WorkspacePaths | Path | str,
    idea_path: Path,
    *,
    reason: str,
    detail: str,
) -> tuple[Path, Path]:
    """Archive invalid legacy idea markdown with diagnostic JSON metadata."""

    paths = _resolve_paths(target)
    paths.intake_ideas_invalid_dir.mkdir(parents=True, exist_ok=True)
    markdown_destination = unique_artifact_path(paths.intake_ideas_invalid_dir / idea_path.name)
    try:
        original_path = str(idea_path.relative_to(paths.root))
    except ValueError:
        original_path = idea_path.as_posix()
    idea_path.replace(markdown_destination)
    metadata_destination = unique_artifact_path(markdown_destination.with_suffix(".json"))
    _atomic_write_json(
        metadata_destination,
        {
            "schema_version": 1,
            "reason": reason,
            "detail": detail,
            "original_path": original_path,
            "invalid_artifact": str(markdown_destination.relative_to(paths.root)),
        },
    )
    return markdown_destination, metadata_destination


def unique_artifact_path(path: Path) -> Path:
    """Return a non-existing sibling path by appending a numeric suffix if needed."""

    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"could not allocate artifact path for {path}")


__all__ = [
    "archive_idea_inbox_artifact",
    "archive_invalid_legacy_idea_artifacts",
    "idea_inbox_artifact_path",
    "idea_normalized_artifact_path",
    "idea_source_artifact_path",
    "normalized_content_hash",
    "stable_idea_id",
    "unique_artifact_path",
    "write_idea_inbox_artifact",
    "write_idea_normalized_artifact",
    "write_idea_source_artifact",
]
