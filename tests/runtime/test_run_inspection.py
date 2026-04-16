from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.run_inspection import inspect_run, inspect_run_id, list_runs

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_runtime_package_exposes_inspection_module() -> None:
    inspection_module = importlib.import_module("millrace_ai.runtime.inspection")

    assert inspection_module.inspect_run is inspect_run
    assert inspection_module.inspect_run_id is inspect_run_id


def test_inspect_run_surfaces_stage_result_and_primary_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-001"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "runner_stdout.txt"
    stdout_path.write_text("### CHECKER_PASS\n", encoding="utf-8")
    report_path = run_dir / "troubleshoot_report.md"
    report_path.write_text("# Troubleshoot\n", encoding="utf-8")

    stage_result = StageResultEnvelope(
        run_id="run-001",
        plane="execution",
        stage="checker",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="CHECKER_PASS",
        result_class="success",
        summary_status_marker="### CHECKER_PASS",
        success=True,
        artifact_paths=(str(report_path),),
        stdout_path=str(stdout_path),
        report_artifact=str(report_path),
        metadata={"failure_class": None},
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    summary = inspect_run(run_dir)

    assert summary.run_id == "run-001"
    assert summary.status == "valid"
    assert summary.stage_results[0].terminal_result == "CHECKER_PASS"
    assert summary.primary_stdout_path == "runner_stdout.txt"
    assert summary.troubleshoot_report_path == "troubleshoot_report.md"


def test_inspect_run_marks_incomplete_when_stage_results_are_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-002"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = inspect_run(run_dir)

    assert summary.status == "incomplete"
    assert "no stage result artifacts" in summary.notes[0]


def test_inspect_run_marks_malformed_stage_result_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-003"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    (stage_results_dir / "request-001.json").write_text("{not-json\n", encoding="utf-8")

    summary = inspect_run(run_dir)

    assert summary.status == "malformed"
    assert "invalid JSON" in summary.notes[0]


def test_list_runs_keeps_incomplete_and_malformed_runs_visible(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    valid_run_dir = paths.runs_dir / "run-b"
    malformed_run_dir = paths.runs_dir / "run-a"
    valid_stage_results_dir = valid_run_dir / "stage_results"
    valid_stage_results_dir.mkdir(parents=True, exist_ok=True)
    malformed_stage_results_dir = malformed_run_dir / "stage_results"
    malformed_stage_results_dir.mkdir(parents=True, exist_ok=True)
    (malformed_stage_results_dir / "request-001.json").write_text("{bad\n", encoding="utf-8")

    payload = {
        "schema_version": "1.0",
        "kind": "stage_result",
        "run_id": "run-b",
        "plane": "execution",
        "stage": "builder",
        "work_item_kind": "task",
        "work_item_id": "task-123",
        "terminal_result": "BUILDER_COMPLETE",
        "result_class": "success",
        "summary_status_marker": "### BUILDER_COMPLETE",
        "success": True,
        "started_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
    }
    (valid_stage_results_dir / "request-001.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    summaries = list_runs(paths)

    assert [summary.run_id for summary in summaries] == ["run-a", "run-b"]
    assert [summary.status for summary in summaries] == ["malformed", "valid"]
