"""Workspace-owned filesystem contract surfaces."""

from __future__ import annotations

from .paths import WorkspacePaths, bootstrap_workspace, workspace_paths

__all__ = ["WorkspacePaths", "bootstrap_workspace", "workspace_paths"]
