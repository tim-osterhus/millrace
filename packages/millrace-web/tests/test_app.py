from __future__ import annotations

from datetime import datetime, timezone
from inspect import signature
from pathlib import Path

from fastapi.testclient import TestClient
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    ResultClass,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.paths import initialize_workspace
from typer.testing import CliRunner

from millrace_web.app import create_app
from millrace_web.cli import app as cli_app
from millrace_web.cli import serve

NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_read_only_api_and_static_shell(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "aura-cascade-port")
    client = TestClient(create_app(workspaces=[paths.root]))

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    workspaces = client.get("/api/workspaces")
    assert workspaces.status_code == 200
    assert workspaces.json()["workspaces"][0]["id"] == "aura-cascade-port"

    summary = client.get("/api/workspaces/aura-cascade-port/summary")
    assert summary.status_code == 200
    assert summary.json()["workspace"]["name"] == "aura-cascade-port"

    no_control = client.post("/api/workspaces/aura-cascade-port/control/pause")
    assert no_control.status_code == 404

    shell = client.get("/")
    assert shell.status_code == 200
    assert "Millrace" in shell.text
    assert "Detail" in shell.text
    assert "Flow" in shell.text


def test_api_rejects_unregistered_workspace_ids(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "registered")
    client = TestClient(create_app(workspaces=[paths.root]))

    response = client.get("/api/workspaces/not-registered/summary")

    assert response.status_code == 404


def test_compiled_graph_endpoint_returns_shared_graph_exports(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
    )
    client = TestClient(create_app(workspaces=[paths.root]))

    response = client.get("/api/workspaces/workspace/compiled-plan/graphs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["graphs"][0]["kind"] == "compiled_stage_graph"
    assert payload["graphs"][0]["plane"] == "execution"
    assert any(node["node_id"] == "builder" for node in payload["graphs"][0]["nodes"])


def test_run_trace_endpoint_returns_fallback_trace_summary(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    run_dir = paths.runs_dir / "run-web"
    stage_results_dir = run_dir / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    stage_result = StageResultEnvelope(
        run_id="run-web",
        plane="execution",
        stage=ExecutionStageName.BUILDER,
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="TASK-001",
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        metadata={"request_id": "request-web"},
        started_at=NOW,
        completed_at=NOW,
    )
    (stage_results_dir / "request-web.json").write_text(
        stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(workspaces=[paths.root]))

    response = client.get("/api/workspaces/workspace/runs/run-web/trace")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "run-web"
    assert payload["status"] == "incomplete"
    assert payload["nodes"][0]["trace_node_id"] == "request-web"
    assert payload["nodes"][0]["terminal_result"] == "BUILDER_COMPLETE"


def test_cli_exposes_serve_subcommand() -> None:
    result = CliRunner().invoke(cli_app, ["serve", "--help"], terminal_width=140)

    assert result.exit_code == 0
    assert any(command.callback is serve for command in cli_app.registered_commands)
    assert "workspace" in signature(serve).parameters
