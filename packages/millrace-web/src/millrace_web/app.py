"""FastAPI application factory for the read-only Millrace dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from millrace_web.models import EventsResponse, HealthResponse, WorkspacesResponse
from millrace_web.services.compiled_plan_reader import read_compiled_plan_summary, read_stage_graphs
from millrace_web.services.event_stream import list_event_summaries, sse_events
from millrace_web.services.queue_reader import read_queue_summary
from millrace_web.services.run_reader import read_run_summary, read_runs_response
from millrace_web.services.snapshot_reader import build_workspace_summary
from millrace_web.services.workspace_registry import WorkspaceRegistry


def create_app(
    *,
    workspaces: Sequence[str | Path],
    poll_interval_seconds: float = 1.0,
    default_view: str = "detail",
) -> FastAPI:
    registry = WorkspaceRegistry.from_paths(workspaces)
    app = FastAPI(title="Millrace Web", version="0.17.2")
    app.state.workspace_registry = registry
    app.state.poll_interval_seconds = poll_interval_seconds
    app.state.default_view = default_view

    static_root = files("millrace_web.static")
    assets_root = static_root.joinpath("assets")
    app.mount("/assets", StaticFiles(directory=str(assets_root)), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(static_root.joinpath("index.html")))

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/workspaces", response_model=WorkspacesResponse)
    def workspaces_route() -> WorkspacesResponse:
        return WorkspacesResponse(workspaces=registry.list_workspaces())

    @app.get("/api/workspaces/{workspace_id}/summary")
    def summary_route(workspace_id: str) -> object:
        return build_workspace_summary(_workspace_or_404(registry, workspace_id))

    @app.get("/api/workspaces/{workspace_id}/status")
    def status_route(workspace_id: str) -> object:
        return build_workspace_summary(_workspace_or_404(registry, workspace_id))

    @app.get("/api/workspaces/{workspace_id}/queues")
    def queues_route(workspace_id: str) -> object:
        return read_queue_summary(_workspace_or_404(registry, workspace_id))

    @app.get("/api/workspaces/{workspace_id}/runs")
    def runs_route(workspace_id: str) -> object:
        return read_runs_response(_workspace_or_404(registry, workspace_id))

    @app.get("/api/workspaces/{workspace_id}/runs/{run_id}")
    def run_route(workspace_id: str, run_id: str) -> object:
        run = read_run_summary(_workspace_or_404(registry, workspace_id), run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/api/workspaces/{workspace_id}/compiled-plan")
    def compiled_plan_route(workspace_id: str) -> object:
        workspace = _workspace_or_404(registry, workspace_id)
        return {
            "summary": read_compiled_plan_summary(workspace),
            "graphs": read_stage_graphs(workspace),
        }

    @app.get("/api/workspaces/{workspace_id}/arbiter")
    def arbiter_route(workspace_id: str) -> object:
        return build_workspace_summary(_workspace_or_404(registry, workspace_id)).arbiter

    @app.get("/api/workspaces/{workspace_id}/usage-governance")
    def usage_governance_route(workspace_id: str) -> object:
        return build_workspace_summary(_workspace_or_404(registry, workspace_id)).usage_governance

    @app.get("/api/workspaces/{workspace_id}/events", response_model=EventsResponse)
    def workspace_events_route(workspace_id: str) -> EventsResponse:
        workspace = _workspace_or_404(registry, workspace_id)
        return EventsResponse(events=list_event_summaries(workspace))

    @app.get("/api/events")
    def events_route() -> StreamingResponse:
        return StreamingResponse(
            sse_events(registry.list_workspaces(), poll_interval_seconds=poll_interval_seconds),
            media_type="text/event-stream",
        )

    return app


def _workspace_or_404(registry: WorkspaceRegistry, workspace_id: str) -> object:
    try:
        return registry.get(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
