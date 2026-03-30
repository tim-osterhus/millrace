"""Workspace initialization from the packaged baseline bundle."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import as_file
from pathlib import Path, PurePosixPath
from stat import S_IMODE
from typing import Any, Iterable

from .baseline_assets import (
    iter_packaged_baseline_directories,
    iter_packaged_baseline_files,
    packaged_baseline_asset,
    packaged_baseline_bundle_version,
)
from .policies.sizing import SizeClass, format_size_status


class WorkspaceInitError(RuntimeError):
    """Raised when a target workspace cannot be initialized safely."""


_RUNTIME_OWNED_DIRECTORY_PATHS: tuple[PurePosixPath, ...] = (
    PurePosixPath("agents/diagnostics"),
    PurePosixPath("agents/historylog"),
    PurePosixPath("agents/ideas/ambiguous"),
    PurePosixPath("agents/ideas/archived"),
    PurePosixPath("agents/ideas/blockers/archived"),
    PurePosixPath("agents/ideas/blockers/incoming"),
    PurePosixPath("agents/ideas/blockers/resolved"),
    PurePosixPath("agents/ideas/blockers/working"),
    PurePosixPath("agents/ideas/finished"),
    PurePosixPath("agents/ideas/incidents/archived"),
    PurePosixPath("agents/ideas/incidents/incoming"),
    PurePosixPath("agents/ideas/incidents/resolved"),
    PurePosixPath("agents/ideas/incidents/working"),
    PurePosixPath("agents/ideas/later"),
    PurePosixPath("agents/ideas/nonviable"),
    PurePosixPath("agents/ideas/raw"),
    PurePosixPath("agents/ideas/specs"),
    PurePosixPath("agents/ideas/specs_reviewed"),
    PurePosixPath("agents/prompts/completed"),
    PurePosixPath("agents/runs"),
    PurePosixPath("agents/specs/decisions"),
    PurePosixPath("agents/specs/questions"),
    PurePosixPath("agents/taskspending"),
)

_RUNTIME_OWNED_FILE_CONTENTS: tuple[tuple[PurePosixPath, str], ...] = (
    (
        PurePosixPath("agents/audit_history.md"),
        "# Audit History\n\n"
        "Local audit outcomes recorded by `millrace_engine.research.audit` (newest first).\n",
    ),
    (PurePosixPath("agents/engine_events.log"), ""),
    (PurePosixPath("agents/expectations.md"), "# Expectations\n"),
    (PurePosixPath("agents/gaps.md"), "# Gaps\n\nNo active gaps recorded.\n"),
    (
        PurePosixPath("agents/historylog.md"),
        "# History Log\n\n"
        "This file is the short human-readable index for runtime history.\n\n"
        "Detailed entries belong under `historylog/` and use UTC filenames such as "
        "`2026-03-16T21-05-33Z__stage-qa__task-123.md`.\n",
    ),
    (PurePosixPath("agents/iterations.md"), "# Iterations\n\nNo recorded iterations yet.\n"),
    (PurePosixPath("agents/quickfix.md"), "# Quickfix\n"),
    (PurePosixPath("agents/research_events.md"), "# Research Events\n"),
    (PurePosixPath("agents/research_status.md"), "### IDLE\n"),
    (PurePosixPath("agents/retrospect.md"), "# Retrospect\n\n## Entries (newest first)\n"),
    (PurePosixPath("agents/roadmap.md"), "# Project Roadmap\n\nNo roadmap entries yet.\n"),
    (
        PurePosixPath("agents/roadmapchecklist.md"),
        "# Roadmap Checklist\n\nNo checklist entries yet.\n",
    ),
    (PurePosixPath("agents/size_status.md"), format_size_status(SizeClass.SMALL)),
    (PurePosixPath("agents/status.md"), "### IDLE\n"),
    (PurePosixPath("agents/tasks.md"), "# Active Task\n"),
    (PurePosixPath("agents/tasksarchive.md"), "# Task Archive\n"),
    (PurePosixPath("agents/tasksbackburner.md"), "# Task Backburner\n"),
    (PurePosixPath("agents/tasksbacklog.md"), "# Task Backlog\n"),
    (PurePosixPath("agents/tasksblocker.md"), "# Task Blockers\n"),
    (PurePosixPath("agents/taskspending.md"), "# Tasks Pending\n"),
)


@dataclass(frozen=True, slots=True)
class WorkspaceInitReport:
    """Deterministic summary of one workspace initialization run."""

    workspace_root: Path
    bundle_version: str
    created_file_count: int
    overwritten_file_count: int
    created_directory_count: int


def iter_runtime_owned_workspace_directories() -> tuple[str, ...]:
    """Return empty workspace directories created by init instead of the asset bundle."""

    return tuple(path.as_posix() for path in _RUNTIME_OWNED_DIRECTORY_PATHS)


def iter_runtime_owned_workspace_files() -> tuple[str, ...]:
    """Return starter workspace files created by init instead of the asset bundle."""

    return tuple(path.as_posix() for path, _ in _RUNTIME_OWNED_FILE_CONTENTS)


def _entry_relative_path(entry: dict[str, Any], *, entry_kind: str) -> PurePosixPath:
    raw_path = entry.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise RuntimeError(f"packaged baseline {entry_kind} entry is missing a path")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError(f"packaged baseline {entry_kind} entry has an invalid path: {raw_path!r}")
    return path


def _manifest_relative_paths(
    entries: Iterable[dict[str, Any]],
    *,
    entry_kind: str,
) -> tuple[PurePosixPath, ...]:
    paths: list[PurePosixPath] = []
    seen: set[str] = set()
    for entry in entries:
        path = _entry_relative_path(entry, entry_kind=entry_kind)
        key = path.as_posix()
        if key in seen:
            raise RuntimeError(f"packaged baseline {entry_kind} manifest contains a duplicate path: {key}")
        seen.add(key)
        paths.append(path)
    return tuple(sorted(paths, key=lambda value: (len(value.parts), value.as_posix())))


def _destination_path(workspace_root: Path, relative_path: PurePosixPath) -> Path:
    return workspace_root.joinpath(*relative_path.parts)


def _validate_workspace_root(workspace_root: Path) -> None:
    if workspace_root == workspace_root.parent:
        raise WorkspaceInitError("destination must not be a filesystem root")


def _ensure_directory(path: Path, *, display_path: str) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise WorkspaceInitError(f"unable to create directory {display_path}: {detail}") from exc


def _prepare_workspace_root(workspace_root: Path, *, force: bool) -> None:
    _validate_workspace_root(workspace_root)
    if workspace_root.exists():
        if not workspace_root.is_dir():
            raise WorkspaceInitError("destination exists and is not a directory")
        if next(workspace_root.iterdir(), None) is not None and not force:
            raise WorkspaceInitError("destination exists and is not empty; re-run with --force to overwrite")
        return
    _ensure_directory(workspace_root, display_path=workspace_root.as_posix())


def _materialize_directories(workspace_root: Path) -> int:
    created_count = 0
    manifest_paths = _manifest_relative_paths(
        iter_packaged_baseline_directories(),
        entry_kind="directory",
    )
    directory_paths = sorted(
        {*(manifest_paths), *(_RUNTIME_OWNED_DIRECTORY_PATHS)},
        key=lambda value: (len(value.parts), value.as_posix()),
    )
    for relative_path in directory_paths:
        destination = _destination_path(workspace_root, relative_path)
        if destination.exists():
            if not destination.is_dir():
                raise WorkspaceInitError(
                    f"manifest directory path is occupied by a file: {relative_path.as_posix()}"
                )
            continue
        _ensure_directory(destination, display_path=relative_path.as_posix())
        created_count += 1
    return created_count


def _write_manifest_file(
    workspace_root: Path,
    relative_path: PurePosixPath,
    *,
    force: bool,
) -> bool:
    destination = _destination_path(workspace_root, relative_path)
    if destination.exists():
        if destination.is_dir():
            raise WorkspaceInitError(f"manifest file path is occupied by a directory: {relative_path.as_posix()}")
        if not force:
            raise WorkspaceInitError(f"manifest file already exists: {relative_path.as_posix()}")
        overwritten = True
    else:
        overwritten = False

    asset = packaged_baseline_asset(relative_path.as_posix())
    if not asset.is_file():
        raise WorkspaceInitError(f"packaged baseline asset is missing: {relative_path.as_posix()}")

    _ensure_directory(destination.parent, display_path=destination.parent.as_posix())
    try:
        destination.write_bytes(asset.read_bytes())
        with as_file(asset) as source_path:
            destination.chmod(S_IMODE(source_path.stat().st_mode))
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise WorkspaceInitError(f"unable to write {relative_path.as_posix()}: {detail}") from exc
    return overwritten


def _write_runtime_owned_file(
    workspace_root: Path,
    relative_path: PurePosixPath,
    contents: str,
    *,
    force: bool,
) -> bool:
    destination = _destination_path(workspace_root, relative_path)
    if destination.exists():
        if destination.is_dir():
            raise WorkspaceInitError(
                f"runtime-owned file path is occupied by a directory: {relative_path.as_posix()}"
            )
        if not force:
            return True
        overwritten = True
    else:
        overwritten = False

    _ensure_directory(destination.parent, display_path=destination.parent.as_posix())
    try:
        destination.write_text(contents, encoding="utf-8")
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise WorkspaceInitError(f"unable to write {relative_path.as_posix()}: {detail}") from exc
    return overwritten


def _materialize_runtime_owned_files(workspace_root: Path, *, force: bool) -> tuple[int, int]:
    created_count = 0
    overwritten_count = 0
    for relative_path, contents in _RUNTIME_OWNED_FILE_CONTENTS:
        overwritten = _write_runtime_owned_file(workspace_root, relative_path, contents, force=force)
        if overwritten:
            overwritten_count += 1
        else:
            created_count += 1
    return created_count, overwritten_count


def initialize_workspace(destination: Path | str, *, force: bool = False) -> WorkspaceInitReport:
    """Seed one workspace from the packaged baseline manifest and resources."""

    workspace_root = Path(destination).expanduser().resolve()
    _prepare_workspace_root(workspace_root, force=force)

    created_directory_count = _materialize_directories(workspace_root)
    created_file_count = 0
    overwritten_file_count = 0

    for relative_path in _manifest_relative_paths(
        iter_packaged_baseline_files(),
        entry_kind="file",
    ):
        overwritten = _write_manifest_file(workspace_root, relative_path, force=force)
        if overwritten:
            overwritten_file_count += 1
        else:
            created_file_count += 1

    runtime_created_file_count, runtime_overwritten_file_count = _materialize_runtime_owned_files(
        workspace_root,
        force=force,
    )
    created_file_count += runtime_created_file_count
    overwritten_file_count += runtime_overwritten_file_count

    return WorkspaceInitReport(
        workspace_root=workspace_root,
        bundle_version=packaged_baseline_bundle_version(),
        created_file_count=created_file_count,
        overwritten_file_count=overwritten_file_count,
        created_directory_count=created_directory_count,
    )
