from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from inspect import signature
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    BlueprintDraftDocument,
    ExecutionStageName,
    ExecutionTerminalResult,
    ResultClass,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.paths import initialize_workspace
from millrace_ai.workspace.blueprint_state import enqueue_blueprint_draft
from typer.testing import CliRunner

from millrace_web.app import create_app
from millrace_web.cli import app as cli_app
from millrace_web.cli import serve

NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_read_only_api_and_static_shell(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "aura-cascade-port")
    enqueue_blueprint_draft(
        paths,
        BlueprintDraftDocument(
            draft_id="draft-blueprint-001",
            manifest_id="manifest-blueprint-001",
            root_spec_id="spec-blueprint-001",
            root_idea_id="idea-blueprint-001",
            source_spec_id="spec-blueprint-001",
            draft_index=1,
            title="Blueprint Draft 001",
            summary="API queue depth fixture.",
            target_paths=("packages/millrace-web/src/millrace_web/app.py",),
            acceptance_intent=("API reports Blueprint draft depth.",),
            context_excerpt="API queue depth fixture.",
            current_revision=0,
            created_at=NOW,
        ),
    )
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
    assert summary.json()["queues"]["blueprint_drafts"]["incoming"] == 1
    assert summary.json()["queues"]["graph_owned_families"]["blueprint_draft"]["incoming"] == 1

    no_control = client.post("/api/workspaces/aura-cascade-port/control/pause")
    assert no_control.status_code == 404

    shell = client.get("/")
    assert shell.status_code == 200
    assert "Millrace" in shell.text
    assert "Detail" in shell.text
    assert "Flow" in shell.text


def test_static_dashboard_renders_graph_owned_family_queue_rows() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for static dashboard JavaScript smoke test")
    app_js = Path(__file__).resolve().parents[1] / "src" / "millrace_web" / "static" / "assets" / "app.js"
    script = """
const fs = require("fs");
global.window = { location: { search: "" } };
const raw = fs.readFileSync(process.argv[1], "utf8");
const source = raw.slice(0, raw.lastIndexOf('$("detail-button").addEventListener'));
eval(source);
const queues = {
  tasks: { incoming: 1, active: 0, done: 0, blocked: 0 },
  specs: { incoming: 0, active: 0, done: 0, blocked: 0 },
  incidents: { incoming: 0, active: 0, done: 0, blocked: 0 },
  learning: { incoming: 0, active: 0, done: 0, blocked: 0 },
  blueprint_drafts: { incoming: 2, active: 0, done: 0, blocked: 0 },
  graph_owned_families: {
    task: { incoming: 1, active: 0, done: 0, blocked: 0 },
    blueprint_draft: { incoming: 2, active: 0, done: 0, blocked: 0 },
    custom_review: { incoming: 3, active: 4, done: 5, blocked: 6 },
  },
};
process.stdout.write(JSON.stringify({ total: totalIncoming(queues), rows: queueRows(queues) }));
"""
    result = subprocess.run(
        [node, "-e", script, str(app_js)],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["total"] == 6
    assert "<td>blueprint_drafts</td><td>2</td>" in payload["rows"]
    assert "<td>custom_review</td><td>3</td><td>4</td><td>5</td><td>6</td>" in payload["rows"]
    assert "graph_owned_families" not in payload["rows"]
    assert "undefined" not in payload["rows"]


def test_static_dashboard_orders_flow_nodes_from_graph_topology() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for static dashboard JavaScript smoke test")
    app_js = Path(__file__).resolve().parents[1] / "src" / "millrace_web" / "static" / "assets" / "app.js"
    script = """
const fs = require("fs");
global.window = { location: { search: "" } };
const raw = fs.readFileSync(process.argv[1], "utf8");
const source = raw.slice(0, raw.lastIndexOf('$("detail-button").addEventListener'));
eval(source);
const graph = {
  plane: "execution",
  nodes: [
    { node_id: "last", label: "last" },
    { node_id: "first", label: "first" },
    { node_id: "middle", label: "middle" },
  ],
  edges: [
    { source_node_id: "first", target_node_id: "middle", outcome: "NEXT", kind: "success" },
    { source_node_id: "middle", target_node_id: "last", outcome: "DONE", kind: "success" },
  ],
};
const tooltip = terminalTooltip({
  terminal_state_id: "update_complete",
  terminal_action_id: "complete_work_item",
  terminal_action_router_consequence: "idle",
  lifecycle_mutation_plan_id: "complete_work_item",
  lifecycle_action_id: "complete",
  terminal_writes_status: "UPDATE_COMPLETE",
});
process.stdout.write(JSON.stringify({
  ordered: orderNodesForPlane(graph).map((node) => node.node_id),
  tooltip,
}));
"""
    result = subprocess.run(
        [node, "-e", script, str(app_js)],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["ordered"] == ["first", "middle", "last"]
    assert "action: complete_work_item" in payload["tooltip"]
    assert "lifecycle action: complete" in payload["tooltip"]


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
