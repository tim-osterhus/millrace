"""Leaf helpers for workspace schema epoch marker files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .paths import WorkspacePaths, workspace_paths

CURRENT_WORKSPACE_SCHEMA_EPOCH = "v0.20"
MARKER_FILENAME = "workspace_schema_epoch.json"


class SchemaEpochError(RuntimeError):
    """Raised when workspace schema epoch reset or validation fails."""


@dataclass(frozen=True, slots=True)
class WorkspaceSchemaEpochMarker:
    epoch_id: str
    written_at: datetime


def workspace_schema_epoch_marker_path(target: WorkspacePaths | Path | str) -> Path:
    paths = _resolve_paths(target)
    return paths.state_dir / MARKER_FILENAME


def load_workspace_schema_epoch_marker(target: WorkspacePaths | Path | str) -> WorkspaceSchemaEpochMarker:
    marker_path = workspace_schema_epoch_marker_path(target)
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaEpochError("workspace schema epoch marker is missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaEpochError(f"workspace schema epoch marker is invalid: {exc}") from exc
    try:
        epoch_id = payload["epoch_id"]
        written_at = datetime.fromisoformat(payload["written_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaEpochError("workspace schema epoch marker is invalid") from exc
    if not isinstance(epoch_id, str) or not epoch_id:
        raise SchemaEpochError("workspace schema epoch marker is invalid")
    if written_at.tzinfo is None:
        written_at = written_at.replace(tzinfo=timezone.utc)
    return WorkspaceSchemaEpochMarker(epoch_id=epoch_id, written_at=written_at)


def write_workspace_schema_epoch_marker(
    target: WorkspacePaths | Path | str,
    *,
    epoch_id: str = CURRENT_WORKSPACE_SCHEMA_EPOCH,
    now: datetime | None = None,
) -> Path:
    paths = _resolve_paths(target)
    marker_path = workspace_schema_epoch_marker_path(paths)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    written_at = now or datetime.now(timezone.utc)
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "workspace_schema_epoch_marker",
        "epoch_id": epoch_id,
        "written_at": written_at.isoformat(),
    }
    _atomic_write_json(marker_path, payload)
    return marker_path


def ensure_workspace_schema_epoch_current(
    target: WorkspacePaths | Path | str,
    *,
    required_epoch_id: str = CURRENT_WORKSPACE_SCHEMA_EPOCH,
) -> WorkspaceSchemaEpochMarker:
    marker = load_workspace_schema_epoch_marker(target)
    if marker.epoch_id != required_epoch_id:
        raise SchemaEpochError(
            f"workspace schema epoch {marker.epoch_id} is incompatible; expected {required_epoch_id}"
        )
    return marker


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


__all__ = [
    "CURRENT_WORKSPACE_SCHEMA_EPOCH",
    "MARKER_FILENAME",
    "SchemaEpochError",
    "WorkspaceSchemaEpochMarker",
    "ensure_workspace_schema_epoch_current",
    "load_workspace_schema_epoch_marker",
    "workspace_schema_epoch_marker_path",
    "write_workspace_schema_epoch_marker",
]
