"""Read-only run and artifact readers."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.contracts.run_trace import RunTraceGraph
from millrace_ai.run_inspection import InspectedRunSummary, inspect_run_id, inspect_run_trace_id, list_runs

from millrace_web.models import (
    RunArtifactSummary,
    RunsResponse,
    RunSummary,
    RunTraceSummary,
    TraceEdgeSummary,
    TraceNodeSummary,
    WorkspaceRef,
)


def read_runs_response(workspace: WorkspaceRef, *, limit: int = 20) -> RunsResponse:
    summaries = sorted(
        list_runs(workspace.path),
        key=lambda run: run.completed_at or run.started_at or run.run_id,
        reverse=True,
    )
    return RunsResponse(runs=tuple(_run_summary(run) for run in summaries[:limit]))


def read_run_summary(workspace: WorkspaceRef, run_id: str) -> RunSummary | None:
    if "/" in run_id or "\\" in run_id or run_id in {"", ".", ".."}:
        return None
    inspected = inspect_run_id(workspace.path, run_id)
    if inspected is None:
        return None
    return _run_summary(inspected)


def read_run_trace_summary(workspace: WorkspaceRef, run_id: str) -> RunTraceSummary | None:
    trace = read_run_trace_graph(workspace, run_id)
    if trace is None:
        return None
    return _trace_summary(trace)


def read_recent_run_traces(workspace: WorkspaceRef, *, limit: int = 3) -> tuple[RunTraceSummary, ...]:
    traces: list[RunTraceSummary] = []
    for run in read_runs_response(workspace, limit=limit).runs:
        trace = read_run_trace_summary(workspace, run.run_id)
        if trace is not None:
            traces.append(trace)
    return tuple(traces)


def read_run_trace_graph(workspace: WorkspaceRef, run_id: str) -> RunTraceGraph | None:
    if "/" in run_id or "\\" in run_id or run_id in {"", ".", ".."}:
        return None
    return inspect_run_trace_id(workspace.path, run_id)


def _run_summary(run: InspectedRunSummary) -> RunSummary:
    latest_stage = run.stage_results[-1] if run.stage_results else None
    return RunSummary(
        run_id=run.run_id,
        status=run.status,
        stage=latest_stage.stage if latest_stage else None,
        result=latest_stage.terminal_result if latest_stage else None,
        work_item_kind=run.work_item_kind.value if run.work_item_kind is not None else None,
        work_item_id=run.work_item_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_seconds=run.duration_seconds,
        total_tokens=_total_tokens(run),
        artifacts=tuple(_artifact_summaries(run)),
    )


def _trace_summary(trace: RunTraceGraph) -> RunTraceSummary:
    return RunTraceSummary(
        run_id=trace.run_id,
        status=trace.status,
        nodes=tuple(
            TraceNodeSummary(
                trace_node_id=node.trace_node_id,
                plane=node.plane.value,
                stage=node.stage,
                node_id=node.node_id,
                terminal_result=node.terminal_result,
                result_class=node.result_class.value,
                started_at=node.started_at.isoformat() if node.started_at else None,
                completed_at=node.completed_at.isoformat() if node.completed_at else None,
                duration_seconds=node.duration_seconds,
            )
            for node in trace.nodes
        ),
        edges=tuple(
            TraceEdgeSummary(
                source_trace_node_id=edge.source_trace_node_id,
                outcome=edge.outcome,
                target_node_id=edge.target_node_id,
                target_trace_node_id=edge.target_trace_node_id,
                terminal_state_id=edge.terminal_state_id,
                edge_kind=edge.edge_kind,
            )
            for edge in trace.edges
        ),
        notes=trace.notes,
    )


def _total_tokens(run: InspectedRunSummary) -> int | None:
    if run.token_usage is None:
        return None
    return (
        run.token_usage.input_tokens
        + run.token_usage.cached_input_tokens
        + run.token_usage.output_tokens
    )


def _artifact_summaries(run: InspectedRunSummary) -> tuple[RunArtifactSummary, ...]:
    run_dir = Path(run.run_dir)
    paths: list[str] = []
    for stage_result in run.stage_results:
        paths.append(stage_result.stage_result_path)
        if stage_result.stdout_path:
            paths.append(stage_result.stdout_path)
        if stage_result.stderr_path:
            paths.append(stage_result.stderr_path)
        if stage_result.report_artifact:
            paths.append(stage_result.report_artifact)
        paths.extend(stage_result.artifact_paths)
    seen: set[str] = set()
    artifacts: list[RunArtifactSummary] = []
    for relative_path in paths:
        if relative_path in seen:
            continue
        seen.add(relative_path)
        artifact_path = run_dir / relative_path
        artifacts.append(
            RunArtifactSummary(
                path=relative_path,
                name=Path(relative_path).name,
                size_bytes=artifact_path.stat().st_size if artifact_path.is_file() else None,
                kind=_artifact_kind(relative_path),
            )
        )
    return tuple(artifacts)


def _artifact_kind(path: str) -> str:
    name = Path(path).name
    if name.endswith(".json"):
        return "json"
    if name.endswith(".md"):
        return "report"
    if "stdout" in name:
        return "stdout"
    if "stderr" in name:
        return "stderr"
    return "artifact"


__all__ = [
    "read_recent_run_traces",
    "read_run_summary",
    "read_run_trace_graph",
    "read_run_trace_summary",
    "read_runs_response",
]
