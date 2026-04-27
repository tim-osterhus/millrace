"""Compiler mode, workspace, and asset-root resolution."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.assets import resolve_builtin_mode_id
from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import WorkspacePaths, workspace_paths

DEFAULT_MODE_ID = "default_codex"


def resolve_mode_id(requested_mode_id: str | None, config: RuntimeConfig) -> str:
    if requested_mode_id and requested_mode_id.strip():
        return resolve_builtin_mode_id(requested_mode_id.strip())

    default_mode = config.runtime.default_mode.strip()
    if default_mode:
        return resolve_builtin_mode_id(default_mode)

    return resolve_builtin_mode_id(DEFAULT_MODE_ID)


def resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


def resolve_compile_assets_root(paths: WorkspacePaths, assets_root: Path | None) -> Path:
    del paths
    if assets_root is not None:
        return assets_root
    from millrace_ai.modes import ASSETS_ROOT

    return ASSETS_ROOT


__all__ = [
    "DEFAULT_MODE_ID",
    "resolve_compile_assets_root",
    "resolve_mode_id",
    "resolve_paths",
]
