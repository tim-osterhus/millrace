"""Explicit read-only workspace registry."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from millrace_ai.paths import require_initialized_workspace

from millrace_web.models import WorkspaceRef

_SAFE_ID_RE = re.compile(r"[^a-z0-9._-]+")


class WorkspaceRegistry:
    """Registry of workspaces explicitly passed to the web server."""

    def __init__(self, workspaces: Iterable[WorkspaceRef]) -> None:
        workspace_tuple = tuple(workspaces)
        self._by_id = {workspace.id: workspace for workspace in workspace_tuple}
        if len(self._by_id) != len(workspace_tuple):
            raise ValueError("duplicate workspace id")

    @classmethod
    def from_paths(cls, workspace_paths: Iterable[str | Path]) -> "WorkspaceRegistry":
        refs: list[WorkspaceRef] = []
        seen_paths: set[Path] = set()
        seen_ids: set[str] = set()
        for workspace_path in workspace_paths:
            paths = require_initialized_workspace(workspace_path)
            resolved = paths.root.resolve()
            if resolved in seen_paths:
                raise ValueError(f"duplicate workspace path: {resolved}")
            workspace_id = _workspace_id_for_path(resolved)
            if workspace_id in seen_ids:
                raise ValueError(f"duplicate workspace id: {workspace_id}")
            seen_paths.add(resolved)
            seen_ids.add(workspace_id)
            refs.append(
                WorkspaceRef(
                    id=workspace_id,
                    name=resolved.name,
                    path=str(resolved),
                )
            )
        if not refs:
            raise ValueError("at least one --workspace path is required")
        return cls(refs)

    def list_workspaces(self) -> tuple[WorkspaceRef, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def get(self, workspace_id: str) -> WorkspaceRef:
        try:
            return self._by_id[workspace_id]
        except KeyError as exc:
            raise KeyError(f"unknown workspace id: {workspace_id}") from exc


def _workspace_id_for_path(path: Path) -> str:
    slug = _SAFE_ID_RE.sub("-", path.name.strip().lower()).strip("-._")
    return slug or "workspace"
