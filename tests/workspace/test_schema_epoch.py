from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import initialize_workspace
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime_lock import acquire_runtime_ownership_lock, release_runtime_ownership_lock
from millrace_ai.workspace.schema_epoch import (
    CURRENT_WORKSPACE_SCHEMA_EPOCH,
    SchemaEpochError,
    archive_reset_workspace_schema,
    load_workspace_schema_epoch_marker,
    workspace_schema_epoch_marker_path,
)

NOW = datetime(2026, 5, 19, tzinfo=timezone.utc)


def test_schema_epoch_reset_refuses_daemon_owned_workspace(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    record = acquire_runtime_ownership_lock(paths, owner_pid=os.getpid(), owner_session_id="daemon")

    try:
        with pytest.raises(SchemaEpochError, match="active daemon owner"):
            archive_reset_workspace_schema(paths, reason="test reset", now=NOW)
    finally:
        release_runtime_ownership_lock(paths, owner_session_id=record.owner_session_id)

    assert not (paths.runtime_root / "archives").exists()


def test_schema_epoch_reset_archives_mutable_state_without_parsing_old_json(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    paths.runtime_snapshot_file.write_text("{not-json", encoding="utf-8")
    paths.tasks_active_dir.mkdir(parents=True, exist_ok=True)
    (paths.tasks_active_dir / "task-001.md").write_text("old active task", encoding="utf-8")

    result = archive_reset_workspace_schema(paths, reason="upgrade to v0.20", now=NOW)

    assert result.epoch_id == CURRENT_WORKSPACE_SCHEMA_EPOCH
    assert result.archive_dir.is_dir()
    assert (result.archive_dir / "state" / "runtime_snapshot.json").read_text(encoding="utf-8") == "{not-json"
    assert (result.archive_dir / "tasks" / "active" / "task-001.md").is_file()
    assert paths.runtime_snapshot_file.is_file()
    assert json.loads(paths.runtime_snapshot_file.read_text(encoding="utf-8"))["kind"] == "runtime_snapshot"

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["reason"] == "upgrade to v0.20"
    assert "state/runtime_snapshot.json" in manifest["moved_paths"]
    assert "tasks/active/task-001.md" in manifest["moved_paths"]
    assert load_workspace_schema_epoch_marker(paths).epoch_id == CURRENT_WORKSPACE_SCHEMA_EPOCH


def test_schema_epoch_reset_writes_marker_during_initialize(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")

    marker = load_workspace_schema_epoch_marker(paths)

    assert marker.epoch_id == CURRENT_WORKSPACE_SCHEMA_EPOCH
    assert workspace_schema_epoch_marker_path(paths).is_file()


def test_schema_epoch_reset_compile_failure_is_reported_after_clean_state(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    mode_path = paths.runtime_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["loop_ids_by_plane"]["planning"] = "planning.unknown"
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SchemaEpochError, match="post-reset compile failed"):
        archive_reset_workspace_schema(
            paths,
            reason="compile failure proof",
            config=RuntimeConfig(),
            requested_mode_id="default_codex",
            assets_root=paths.runtime_root,
            now=NOW,
        )

    assert paths.runtime_snapshot_file.is_file()
    assert load_workspace_schema_epoch_marker(paths).epoch_id == CURRENT_WORKSPACE_SCHEMA_EPOCH


def test_runtime_startup_refuses_missing_schema_epoch_marker(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    workspace_schema_epoch_marker_path(paths).unlink()

    engine = RuntimeEngine(
        paths,
        stage_runner=lambda request: pytest.fail("stage runner should not run during startup"),
    )
    with pytest.raises(Exception, match="workspace schema epoch"):
        engine.startup()
