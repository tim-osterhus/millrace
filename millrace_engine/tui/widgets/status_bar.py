"""Always-visible workspace and runtime status strip for the shell screen."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.widgets import Static

from ...health import WorkspaceHealthReport
from ..models import (
    DisplayMode,
    GatewayFailure,
    LifecycleSignalView,
    LifecycleState,
    QueueOverviewView,
    RuntimeOverviewView,
)
from .overview_panel import _runtime_label


def _refresh_label(moment: datetime | None) -> str:
    if moment is None:
        return "waiting"
    normalized = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).strftime("%H:%M:%SZ")


def _compact_refresh_label(moment: datetime | None) -> str:
    if moment is None:
        return "wait"
    normalized = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).strftime("%H:%MZ")


def _clip_fragment(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    if limit <= 3:
        return normalized[:limit]
    return f"{normalized[: limit - 3]}..."


def _compact_lifecycle_label(lifecycle: LifecycleSignalView) -> str:
    if lifecycle.state is LifecycleState.IDLE:
        return "idle"
    if lifecycle.state is LifecycleState.DAEMON_RUNNING:
        return "running"
    if lifecycle.state is LifecycleState.LAUNCHING_ONCE:
        return "launching"
    if lifecycle.state is LifecycleState.LAUNCHING_DAEMON:
        return "launching"
    if lifecycle.state is LifecycleState.PAUSED:
        return "paused"
    if lifecycle.state is LifecycleState.STOP_IN_PROGRESS:
        return "stopping"
    if lifecycle.state is LifecycleState.LIFECYCLE_FAILURE:
        return "failure"
    return _clip_fragment(lifecycle.label, limit=8)


def _operator_health_fragment(report: WorkspaceHealthReport | None) -> str:
    if report is None:
        return "health --"
    counts = f"{report.summary.passed_checks}/{report.summary.total_checks}"
    if report.status.value == "pass":
        return counts
    return f"{report.status.value} {counts}"


def _operator_refresh_fragment(*, refreshed_at: datetime | None, failure: GatewayFailure | None) -> str:
    if failure is not None:
        return f"stale {_clip_fragment(failure.message, limit=16)}"
    return ""


class StatusBar(Static):
    """Compact workspace summary that remains visible above the active panel."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, markup=False)

    def show_state(
        self,
        *,
        workspace_path: Path,
        active_panel_label: str,
        expanded_mode: bool,
        display_mode: DisplayMode,
        lifecycle: LifecycleSignalView,
        health_report: WorkspaceHealthReport | None,
        runtime: RuntimeOverviewView | None,
        queue: QueueOverviewView | None,
        last_refreshed_at: datetime | None,
        refresh_failure: GatewayFailure | None,
        busy_message: str | None = None,
    ) -> None:
        self.update(
            self._render_text(
                workspace_path=workspace_path,
                active_panel_label=active_panel_label,
                expanded_mode=expanded_mode,
                display_mode=display_mode,
                lifecycle=lifecycle,
                health_report=health_report,
                runtime=runtime,
                queue=queue,
                last_refreshed_at=last_refreshed_at,
                refresh_failure=refresh_failure,
                busy_message=busy_message,
            )
        )

    def _render_text(
        self,
        *,
        workspace_path: Path,
        active_panel_label: str,
        expanded_mode: bool,
        display_mode: DisplayMode,
        lifecycle: LifecycleSignalView,
        health_report: WorkspaceHealthReport | None,
        runtime: RuntimeOverviewView | None,
        queue: QueueOverviewView | None,
        last_refreshed_at: datetime | None,
        refresh_failure: GatewayFailure | None,
        busy_message: str | None,
    ) -> str:
        if display_mode is DisplayMode.DEBUG:
            return self._render_debug_text(
                workspace_path=workspace_path,
                active_panel_label=active_panel_label,
                expanded_mode=expanded_mode,
                display_mode=display_mode,
                lifecycle=lifecycle,
                health_report=health_report,
                runtime=runtime,
                queue=queue,
                last_refreshed_at=last_refreshed_at,
                refresh_failure=refresh_failure,
                busy_message=busy_message,
            )
        return self._render_operator_text(
            active_panel_label=active_panel_label,
            expanded_mode=expanded_mode,
            lifecycle=lifecycle,
            health_report=health_report,
            runtime=runtime,
            queue=queue,
            last_refreshed_at=last_refreshed_at,
            refresh_failure=refresh_failure,
            busy_message=busy_message,
        )

    def _render_operator_text(
        self,
        *,
        active_panel_label: str,
        expanded_mode: bool,
        lifecycle: LifecycleSignalView,
        health_report: WorkspaceHealthReport | None,
        runtime: RuntimeOverviewView | None,
        queue: QueueOverviewView | None,
        last_refreshed_at: datetime | None,
        refresh_failure: GatewayFailure | None,
        busy_message: str | None,
    ) -> str:
        if runtime is None:
            daemon_label = "unknown"
            backlog_depth = "?"
            active_label = "none"
        else:
            daemon_label = "running" if runtime.process_running else "stopped"
            backlog_depth = str(runtime.backlog_depth)
            active_label = (
                queue.active_task.title
                if queue is not None and queue.active_task is not None
                else _runtime_label(runtime.active_task_id)
            )
            if runtime.liveness_degraded:
                daemon_label = f"{daemon_label}*"
        active_fragment = _clip_fragment(active_label, limit=12)
        health_fragment = _operator_health_fragment(health_report)
        tail_fragment = _operator_refresh_fragment(refreshed_at=last_refreshed_at, failure=refresh_failure)
        telemetry_fragment = f"{health_fragment} {tail_fragment}".strip()
        if busy_message is not None:
            telemetry_fragment = f"{health_fragment} busy {_clip_fragment(busy_message, limit=12)}".strip()
        panel_fragment = active_panel_label
        if expanded_mode:
            panel_fragment = f"{panel_fragment} Expanded"
        segments = [
            f"OPERATOR | {panel_fragment}",
            f"daemon {daemon_label}",
            f"backlog {backlog_depth}",
            f"active {active_fragment}",
            telemetry_fragment,
        ]
        lifecycle_label = _compact_lifecycle_label(lifecycle)
        if lifecycle_label not in {"idle", "running"}:
            segments.insert(2, f"state {lifecycle_label}")
        return " | ".join(segments)

    def _render_debug_text(
        self,
        *,
        workspace_path: Path,
        active_panel_label: str,
        expanded_mode: bool,
        display_mode: DisplayMode,
        lifecycle: LifecycleSignalView,
        health_report: WorkspaceHealthReport | None,
        runtime: RuntimeOverviewView | None,
        queue: QueueOverviewView | None,
        last_refreshed_at: datetime | None,
        refresh_failure: GatewayFailure | None,
        busy_message: str | None,
    ) -> str:
        if runtime is None:
            daemon_label = "unknown"
            backlog_depth = "?"
            active_label = "none"
            source_label = "awaiting snapshot"
        else:
            daemon_label = "running" if runtime.process_running else "stopped"
            backlog_depth = str(runtime.backlog_depth)
            active_label = (
                queue.active_task.title
                if queue is not None and queue.active_task is not None
                else _runtime_label(runtime.active_task_id)
            )
            source_label = runtime.source_kind
            if runtime.liveness_degraded:
                source_label = f"{source_label} | liveness degraded"

        health_fragment = "health pending"
        if health_report is not None:
            health_fragment = (
                f"health {health_report.status.value} "
                f"{health_report.summary.passed_checks}/{health_report.summary.total_checks}"
            )
        refresh_fragment = f"refresh {_refresh_label(last_refreshed_at)} {source_label}"
        if refresh_failure is not None:
            refresh_fragment = f"refresh stale ({refresh_failure.message})"
        panel_fragment = active_panel_label
        if expanded_mode:
            panel_fragment = f"{panel_fragment} Expanded"
        line = (
            f"{display_mode.value.upper()} | {panel_fragment} | daemon {daemon_label}"
            f" | lifecycle {lifecycle.label}"
            f" | backlog {backlog_depth}"
            f" | active {active_label}"
            f" | {health_fragment}"
            f" | {refresh_fragment}"
        )
        if busy_message is not None:
            line += f" | action {busy_message}"
        return line


__all__ = ["StatusBar"]
