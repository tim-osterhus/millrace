"""Stable public facade for workspace path helpers."""

from __future__ import annotations

from millrace_ai.workspace.initialization import (
    bootstrap_workspace,
    ensure_runtime_state_surfaces,
    initialize_workspace,
    require_initialized_workspace,
)
from millrace_ai.workspace.paths import WorkspacePaths, workspace_paths

__all__ = [
    "WorkspacePaths",
    "bootstrap_workspace",
    "ensure_runtime_state_surfaces",
    "initialize_workspace",
    "require_initialized_workspace",
    "workspace_paths",
]
