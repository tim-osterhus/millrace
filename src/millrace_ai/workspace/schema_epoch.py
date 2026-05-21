"""Workspace schema epoch marker and archive-reset helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.runtime_lock import inspect_runtime_ownership_lock

from .paths import WorkspacePaths, workspace_paths
from .schema_epoch_marker import (
    CURRENT_WORKSPACE_SCHEMA_EPOCH,
    SchemaEpochError,
    WorkspaceSchemaEpochMarker,
    ensure_workspace_schema_epoch_current,
    load_workspace_schema_epoch_marker,
    workspace_schema_epoch_marker_path,
    write_workspace_schema_epoch_marker,
)

_RESET_LOCK_FILENAME = ".schema-reset.lock"
_MUTABLE_RUNTIME_NAMES = (
    "state",
    "runs",
    "tasks",
    "specs",
    "incidents",
    "probes",
    "recon",
    "learning",
    "arbiter",
)


@dataclass(frozen=True, slots=True)
class SchemaArchiveResetResult:
    epoch_id: str
    archive_dir: Path
    manifest_path: Path
    moved_paths: tuple[str, ...]


def archive_reset_workspace_schema(
    target: WorkspacePaths | Path | str,
    *,
    reason: str,
    config: RuntimeConfig | None = None,
    requested_mode_id: str | None = None,
    assets_root: Path | None = None,
    now: datetime | None = None,
) -> SchemaArchiveResetResult:
    paths = _resolve_paths(target)
    reset_time = now or datetime.now(timezone.utc)
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise SchemaEpochError("schema reset reason is required")

    daemon_status = inspect_runtime_ownership_lock(paths)
    if daemon_status.state == "active":
        raise SchemaEpochError(f"workspace has an active daemon owner: {daemon_status.detail}")

    lock_path = _acquire_reset_lock(paths)
    try:
        archive_dir = _allocate_archive_dir(paths, reset_time)
        moved_paths = _archive_mutable_runtime_state(paths, archive_dir)
        from .initialization import initialize_workspace

        initialize_workspace(paths)
        write_workspace_schema_epoch_marker(paths, now=reset_time)
        manifest_path = _write_archive_manifest(
            archive_dir,
            reason=cleaned_reason,
            moved_paths=moved_paths,
            now=reset_time,
        )
        if config is not None:
            outcome = compile_and_persist_workspace_plan(
                paths,
                config=config,
                requested_mode_id=requested_mode_id,
                assets_root=assets_root,
                refuse_stale_last_known_good=True,
            )
            if not outcome.diagnostics.ok or outcome.active_plan is None:
                errors = ", ".join(outcome.diagnostics.errors) or "unknown compile failure"
                raise SchemaEpochError(f"post-reset compile failed: {errors}")
        return SchemaArchiveResetResult(
            epoch_id=CURRENT_WORKSPACE_SCHEMA_EPOCH,
            archive_dir=archive_dir,
            manifest_path=manifest_path,
            moved_paths=moved_paths,
        )
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _archive_mutable_runtime_state(paths: WorkspacePaths, archive_dir: Path) -> tuple[str, ...]:
    moved: list[str] = []
    for name in _MUTABLE_RUNTIME_NAMES:
        source = paths.runtime_root / name
        if not source.exists():
            continue
        destination = archive_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        moved.extend(
            path.relative_to(archive_dir).as_posix()
            for path in sorted(destination.rglob("*"))
            if path.is_file()
        )
    return tuple(sorted(moved))


def _write_archive_manifest(
    archive_dir: Path,
    *,
    reason: str,
    moved_paths: tuple[str, ...],
    now: datetime,
) -> Path:
    manifest_path = archive_dir / "schema_archive_manifest.json"
    _atomic_write_json(
        manifest_path,
        {
            "schema_version": "1.0",
            "kind": "schema_archive_manifest",
            "epoch_id": CURRENT_WORKSPACE_SCHEMA_EPOCH,
            "reason": reason,
            "archived_at": now.isoformat(),
            "moved_paths": list(moved_paths),
        },
    )
    return manifest_path


def _allocate_archive_dir(paths: WorkspacePaths, now: datetime) -> Path:
    archives_root = paths.runtime_root / "archives"
    archives_root.mkdir(parents=True, exist_ok=True)
    base_name = f"schema-reset-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    candidate = archives_root / base_name
    suffix = 1
    while candidate.exists():
        candidate = archives_root / f"{base_name}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _acquire_reset_lock(paths: WorkspacePaths) -> Path:
    lock_path = paths.runtime_root / _RESET_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({"owner_pid": os.getpid()}) + "\n")
    except FileExistsError as exc:
        raise SchemaEpochError(f"workspace schema reset lock already exists: {lock_path}") from exc
    return lock_path


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


__all__ = [
    "CURRENT_WORKSPACE_SCHEMA_EPOCH",
    "SchemaArchiveResetResult",
    "SchemaEpochError",
    "WorkspaceSchemaEpochMarker",
    "archive_reset_workspace_schema",
    "ensure_workspace_schema_epoch_current",
    "load_workspace_schema_epoch_marker",
    "workspace_schema_epoch_marker_path",
    "write_workspace_schema_epoch_marker",
]
