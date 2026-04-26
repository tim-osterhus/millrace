from __future__ import annotations

import importlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import TaskDocument
from millrace_ai.doctor import run_workspace_doctor
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runtime_lock import acquire_runtime_ownership_lock
from millrace_ai.work_documents import render_work_document

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _bootstrap(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _copy_assets(tmp_path: Path) -> Path:
    source_assets = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    destination = tmp_path / "assets"
    shutil.copytree(source_assets, destination)
    return destination


def test_workspace_package_exposes_support_module_facades() -> None:
    workspace_package = importlib.import_module("millrace_ai.workspace")
    runtime_lock_module = importlib.import_module("millrace_ai.runtime_lock")
    work_documents_module = importlib.import_module("millrace_ai.work_documents")

    assert hasattr(workspace_package, "workspace_paths")
    assert runtime_lock_module.acquire_runtime_ownership_lock.__module__ == (
        "millrace_ai.workspace.runtime_lock"
    )
    assert work_documents_module.render_work_document.__module__ == (
        "millrace_ai.workspace.work_documents"
    )


def test_doctor_passes_for_bootstrapped_workspace(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)

    report = run_workspace_doctor(paths)

    assert report.ok is True
    assert report.errors == ()


def test_doctor_flags_invalid_status_and_unparseable_queue_artifact(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.execution_status_file.write_text("RUNNING\n", encoding="utf-8")
    (paths.tasks_queue_dir / "bad.md").write_text("# Bad task\nnot a valid task document\n", encoding="utf-8")

    report = run_workspace_doctor(paths)

    assert report.ok is False
    error_codes = {item.code for item in report.errors}
    assert "execution_status_invalid" in error_codes
    assert "queue_artifact_invalid" in error_codes


def test_doctor_flags_queue_filename_and_document_id_mismatch(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    mismatch_doc = TaskDocument(
        task_id="task-mismatch",
        title="Task mismatch",
        summary="mismatched filename and frontmatter id",
        target_paths=["millrace/runtime.py"],
        acceptance=["doctor flags mismatch"],
        required_checks=["uv run pytest tests/workspace/test_doctor.py -q"],
        references=["lab/specs/pending/2026-04-15-millrace-recheck-remediation-task-breakdown.md"],
        risk=["queue routing drift"],
        created_at=NOW,
        created_by="tests",
    )
    (paths.tasks_queue_dir / "task-alias.md").write_text(
        render_work_document(mismatch_doc),
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    mismatch_errors = [item for item in report.errors if item.code == "queue_artifact_invalid"]
    assert mismatch_errors
    assert any("filename stem does not match task_id" in item.message for item in mismatch_errors)


def test_doctor_flags_snapshot_reconciliation_problems(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)

    snapshot_payload = json.loads(paths.runtime_snapshot_file.read_text(encoding="utf-8"))
    snapshot_payload.update(
        {
            "process_running": False,
            "active_plane": "execution",
            "active_stage": "checker",
            "active_run_id": "run-001",
            "active_work_item_kind": "task",
            "active_work_item_id": "task-001",
            "active_since": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
    )
    paths.runtime_snapshot_file.write_text(
        json.dumps(snapshot_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.execution_status_file.write_text("### CHECKER_PASS\n", encoding="utf-8")

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(
        item.code == "snapshot_reconciliation_signal" and "stale_active_ownership" in item.message
        for item in report.errors
    )


def test_doctor_flags_invalid_mode_assets_deterministically(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    assets_root = _copy_assets(tmp_path)

    broken_mode_path = assets_root / "modes" / "default_codex.json"
    broken_mode_path.write_text("{not-valid-json", encoding="utf-8")

    report = run_workspace_doctor(paths, assets_root=assets_root)

    assert report.ok is False
    assert any(item.code == "mode_definition_invalid" for item in report.errors)


def test_doctor_warns_when_resolved_runner_binary_is_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    paths = _bootstrap(tmp_path)
    paths.runtime_root.joinpath("millrace.toml").write_text(
        "\n".join(
            [
                "[runtime]",
                'default_mode = "default_pi"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("millrace_ai.doctor.shutil.which", lambda command: None)

    report = run_workspace_doctor(paths)

    assert any(item.code == "runner_binary_unavailable" for item in report.warnings)
    assert any("pi_rpc" in item.message for item in report.warnings)


def test_doctor_reports_active_runtime_ownership_lock_health(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="doctor-active",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is True
    assert any(item.code == "runtime_ownership_lock_active" for item in report.warnings)


def test_doctor_flags_stale_runtime_ownership_lock(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=999_999_999,
        owner_session_id="doctor-stale",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "runtime_ownership_lock_stale" for item in report.errors)


def test_doctor_flags_invalid_runtime_ownership_lock_payload(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.runtime_lock_file.write_text("{not-valid-json", encoding="utf-8")

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "runtime_ownership_lock_invalid" for item in report.errors)


def test_doctor_flags_missing_baseline_manifest(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.baseline_manifest_file.unlink()

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "baseline_manifest_missing" for item in report.errors)


def test_doctor_flags_invalid_baseline_manifest_schema(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    paths.baseline_manifest_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "manifest_id": "bad",
                "seed_package_version": "0.0.0",
                "entries": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "baseline_manifest_invalid" for item in report.errors)


def test_doctor_flags_missing_manifest_tracked_managed_file(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    (paths.runtime_root / "entrypoints" / "execution" / "builder.md").unlink()

    report = run_workspace_doctor(paths)

    assert report.ok is False
    assert any(item.code == "baseline_manifest_managed_file_missing" for item in report.errors)
