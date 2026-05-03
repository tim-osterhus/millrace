from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from millrace_ai.paths import initialize_workspace
from typer.testing import CliRunner

from millrace_web.app import create_app
from millrace_web.cli import app as cli_app


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


def test_cli_exposes_serve_subcommand() -> None:
    result = CliRunner().invoke(cli_app, ["serve", "--help"])

    assert result.exit_code == 0
    assert "--workspace" in result.output
