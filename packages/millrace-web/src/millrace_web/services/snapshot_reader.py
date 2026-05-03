"""Read-only workspace summary assembly."""

from __future__ import annotations

from datetime import datetime, timezone

from millrace_ai.paths import workspace_paths
from millrace_ai.state_store import load_snapshot

from millrace_web.models import (
    ActiveRunSummary,
    DaemonSummary,
    DashboardSummary,
    RuntimeSummary,
    WorkspaceRef,
)
from millrace_web.services.arbiter_reader import read_arbiter_summary
from millrace_web.services.baseline_reader import read_baseline_summary
from millrace_web.services.compiled_plan_reader import read_compiled_plan_summary, read_stage_graphs
from millrace_web.services.event_stream import list_event_summaries
from millrace_web.services.queue_reader import read_queue_summary
from millrace_web.services.run_reader import read_runs_response
from millrace_web.services.usage_governance_reader import read_usage_governance_summary


def build_workspace_summary(workspace: WorkspaceRef) -> DashboardSummary:
    paths = workspace_paths(workspace.path)
    snapshot = load_snapshot(paths)
    runtime = RuntimeSummary(
        mode_id=snapshot.active_mode_id,
        active_plane=snapshot.active_plane.value if snapshot.active_plane else None,
        active_stage=snapshot.active_stage.value if snapshot.active_stage else None,
        active_node_id=snapshot.active_node_id,
        active_stage_kind_id=snapshot.active_stage_kind_id,
        active_run_id=snapshot.active_run_id,
        active_work_item_kind=(
            snapshot.active_work_item_kind.value if snapshot.active_work_item_kind else None
        ),
        active_work_item_id=snapshot.active_work_item_id,
        started_at=snapshot.started_at,
        active_since=snapshot.active_since,
        elapsed_seconds=_elapsed_seconds(snapshot.active_since),
        active_runs_by_plane=tuple(
            ActiveRunSummary(
                plane=run.plane.value,
                stage=run.stage.value,
                node_id=run.node_id,
                stage_kind_id=run.stage_kind_id,
                run_id=run.run_id,
                request_kind=run.request_kind,
                work_item_kind=run.work_item_kind.value if run.work_item_kind else None,
                work_item_id=run.work_item_id,
                active_since=run.active_since,
            )
            for _, run in sorted(snapshot.active_runs_by_plane.items(), key=lambda item: item[0].value)
        ),
    )
    return DashboardSummary(
        workspace=workspace,
        daemon=DaemonSummary(
            state=_daemon_state(snapshot.process_running, snapshot.paused),
            process_running=snapshot.process_running,
            pause_sources=tuple(snapshot.pause_sources),
        ),
        runtime=runtime,
        compiled_plan=read_compiled_plan_summary(workspace, snapshot_plan_id=snapshot.compiled_plan_id),
        baseline=read_baseline_summary(workspace),
        queues=read_queue_summary(workspace),
        usage_governance=read_usage_governance_summary(workspace),
        arbiter=read_arbiter_summary(
            workspace,
            latest_result=snapshot.last_terminal_result.value if snapshot.last_terminal_result else None,
        ),
        graphs=read_stage_graphs(workspace),
        recent_runs=read_runs_response(workspace, limit=5).runs,
        events=list_event_summaries(workspace, limit=25),
    )


def _daemon_state(process_running: bool, paused: bool) -> str:
    if paused:
        return "paused"
    if process_running:
        return "running"
    return "stopped"


def _elapsed_seconds(active_since: datetime | None) -> float | None:
    if active_since is None:
        return None
    now = datetime.now(timezone.utc)
    return max(0.0, (now - active_since).total_seconds())

