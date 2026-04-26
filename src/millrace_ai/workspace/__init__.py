"""Workspace-owned filesystem contract surfaces."""

from __future__ import annotations

from .baseline import (
    BaselineManifest,
    BaselineManifestEntry,
    build_baseline_manifest,
    load_baseline_manifest,
    write_baseline_manifest,
)
from .paths import WorkspacePaths, bootstrap_workspace, workspace_paths

__all__ = [
    "BaselineManifest",
    "BaselineManifestEntry",
    "WorkspacePaths",
    "bootstrap_workspace",
    "build_baseline_manifest",
    "load_baseline_manifest",
    "workspace_paths",
    "write_baseline_manifest",
]
