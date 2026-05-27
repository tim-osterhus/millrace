"""Filesystem store helpers for runtime-effect operation runners."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile

from millrace_ai.workspace.paths import WorkspacePaths


def effect_path(paths: WorkspacePaths, path: Path) -> str:
    return path.relative_to(paths.root).as_posix()


def copy_unique_file(
    source: Path,
    destination: Path,
    *,
    exists_message: str | None = None,
) -> None:
    if destination.exists():
        raise FileExistsError(exists_message or f"artifact already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(f".{destination.name}.tmp")
    copyfile(source, tmp_path)
    tmp_path.replace(destination)


__all__ = [
    "copy_unique_file",
    "effect_path",
]
