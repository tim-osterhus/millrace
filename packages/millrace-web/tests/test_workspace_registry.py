from __future__ import annotations

from pathlib import Path

import pytest
from millrace_ai.paths import initialize_workspace

from millrace_web.services.workspace_registry import WorkspaceRegistry


def test_registry_registers_explicit_workspaces_and_rejects_duplicates(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "aura-cascade-port")

    registry = WorkspaceRegistry.from_paths([paths.root])

    workspaces = registry.list_workspaces()
    assert len(workspaces) == 1
    assert workspaces[0].id == "aura-cascade-port"
    assert workspaces[0].path == str(paths.root)

    with pytest.raises(ValueError, match="duplicate workspace path"):
        WorkspaceRegistry.from_paths([paths.root, paths.root])


def test_registry_rejects_missing_workspace_and_unregistered_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace is not initialized"):
        WorkspaceRegistry.from_paths([tmp_path / "missing"])

    paths = initialize_workspace(tmp_path / "known")
    registry = WorkspaceRegistry.from_paths([paths.root])

    with pytest.raises(KeyError, match="unknown workspace id"):
        registry.get("not-registered")
