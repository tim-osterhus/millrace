from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from millrace_ai import cli
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    ResultClass,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.paths import initialize_workspace

NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_compile_graph_json_outputs_compiled_stage_graphs(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")

    result = CliRunner().invoke(
        cli.app,
        [
            "compile",
            "graph",
            "--workspace",
            str(paths.root),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert '"kind": "compiled_stage_graph"' in result.output
    assert '"plane": "execution"' in result.output
    assert '"node_id": "builder"' in result.output


def test_runs_trace_json_outputs_fallback_trace_for_existing_run(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    run_dir = paths.runs_dir / "run-cli"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stage_result = StageResultEnvelope(
        run_id="run-cli",
        plane="execution",
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        metadata={"request_id": "request-001"},
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-001.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "runs",
            "trace",
            "run-cli",
            "--workspace",
            str(paths.root),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert '"kind": "run_trace_graph"' in result.output
    assert '"run_id": "run-cli"' in result.output
    assert '"trace_node_id": "request-001"' in result.output


def test_runs_trace_reports_missing_run(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")

    result = CliRunner().invoke(
        cli.app,
        ["runs", "trace", "missing", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1
    assert "error: run not found: missing" in result.output
