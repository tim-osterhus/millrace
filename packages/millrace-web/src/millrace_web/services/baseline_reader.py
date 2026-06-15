"""Read-only baseline manifest reader."""

from __future__ import annotations

from millrace_ai.paths import workspace_paths
from millrace_ai.workspace.baseline import load_baseline_manifest

from millrace_web.models import BaselineSummary, WorkspaceRef


def read_baseline_summary(workspace: WorkspaceRef) -> BaselineSummary:
    paths = workspace_paths(workspace.path)
    if not paths.baseline_manifest_file.is_file():
        return BaselineSummary(state="missing", manifest_id=None)
    try:
        manifest = load_baseline_manifest(paths)
    except Exception:
        return BaselineSummary(state="unknown", manifest_id=None)
    return BaselineSummary(state="initialized", manifest_id=manifest.manifest_id)
