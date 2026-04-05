from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.widgets import Static

from millrace_engine.tui.models import FailureCategory, GatewayFailure, PanelId, notice_from_failure
from millrace_engine.tui.screens.health_gate import HealthGateScreen
from millrace_engine.tui.screens.help_modal import HelpModal
from millrace_engine.tui.screens.run_detail_modal import RunDetailModal
from millrace_engine.tui.screens.shell import ShellScreen
from millrace_engine.tui.widgets.config_panel import ConfigPanel
from millrace_engine.tui.widgets.overview_panel import OverviewPanel
from millrace_engine.tui.widgets.publish_panel import PublishPanel
from millrace_engine.tui.widgets.queue_panel import QueuePanel
from millrace_engine.tui.widgets.research_panel import ResearchPanel
from millrace_engine.tui.widgets.runs_panel import RunsPanel
from tests.tui_support import static_text


SNAPSHOT_APPS = Path(__file__).resolve().parent / "snapshot_apps"
TERMINAL_SIZE = (120, 40)


async def _wait_for_shell(pilot) -> None:
    for _ in range(80):
        if isinstance(pilot.app.screen, ShellScreen) and pilot.app.screen._store.state.runtime is not None:
            return
        await pilot.pause()
    raise AssertionError("shell screen did not finish the initial refresh")


async def _wait_for_help_modal(pilot) -> None:
    await _wait_for_shell(pilot)
    await pilot.press("question_mark")
    for _ in range(40):
        if isinstance(pilot.app.screen, HelpModal):
            return
        await pilot.pause()
    raise AssertionError("help modal did not open")


async def _wait_for_logs_panel(pilot) -> None:
    await _wait_for_shell(pilot)
    await pilot.press("5")
    await pilot.pause()
    await pilot.press("c")
    await pilot.pause()


async def _wait_for_panel_snapshot(pilot, *, key: str, panel_id: str, panel_type, expected_text: str) -> None:
    await _wait_for_shell(pilot)
    await pilot.press(key)
    await pilot.pause()
    await pilot.press("c")
    for _ in range(40):
        if isinstance(pilot.app.screen, ShellScreen):
            panel = pilot.app.screen.query_one(f"#{panel_id}", panel_type)
            summary = panel.summary_text()
            if expected_text in summary:
                return
        await pilot.pause()
    raise AssertionError(f"{panel_id} did not render expected operator text")


async def _wait_for_queue_panel(pilot) -> None:
    await _wait_for_panel_snapshot(
        pilot,
        key="2",
        panel_id="panel-queue",
        panel_type=QueuePanel,
        expected_text="BACKLOG 1 visible",
    )


async def _wait_for_runs_panel(pilot) -> None:
    await _wait_for_panel_snapshot(
        pilot,
        key="3",
        panel_id="panel-runs",
        panel_type=RunsPanel,
        expected_text="WARN smoke-standard",
    )


async def _wait_for_research_panel(pilot) -> None:
    await _wait_for_panel_snapshot(
        pilot,
        key="4",
        panel_id="panel-research",
        panel_type=ResearchPanel,
        expected_text="INTERVIEW 1 pending",
    )


async def _wait_for_config_panel(pilot) -> None:
    await _wait_for_panel_snapshot(
        pilot,
        key="6",
        panel_id="panel-config",
        panel_type=ConfigPanel,
        expected_text="EDITABLE",
    )


async def _wait_for_publish_panel(pilot) -> None:
    await _wait_for_panel_snapshot(
        pilot,
        key="7",
        panel_id="panel-publish",
        panel_type=PublishPanel,
        expected_text="STATUS  blocked | staging repo is missing a git worktree",
    )


async def _wait_for_launching_daemon_state(pilot) -> None:
    await _wait_for_shell(pilot)
    pilot.app.action_start_daemon()
    for _ in range(80):
        if isinstance(pilot.app.screen, ShellScreen):
            status_line = static_text(pilot.app.screen.query_one("#shell-status", Static))
            if "state launching" in status_line and "busy " in status_line:
                return
        await pilot.pause()
    raise AssertionError("daemon launching lifecycle state did not render")


async def _wait_for_debug_mode(pilot) -> None:
    await _wait_for_shell(pilot)
    pilot.app.action_toggle_display_mode()
    for _ in range(40):
        if isinstance(pilot.app.screen, ShellScreen):
            status_line = static_text(pilot.app.screen.query_one("#shell-status", Static))
            if "DEBUG |" in status_line and "lifecycle" in status_line:
                return
        await pilot.pause()
    raise AssertionError("debug mode shell state did not render")


async def _wait_for_expanded_mode(pilot) -> None:
    await _wait_for_shell(pilot)
    pilot.app.action_toggle_expanded_mode()
    for _ in range(40):
        if isinstance(pilot.app.screen, ShellScreen):
            status_line = static_text(pilot.app.screen.query_one("#shell-status", Static))
            expanded = static_text(pilot.app.screen.query_one("#shell-expanded-stream", Static))
            if (
                "Overview Expanded" in status_line
                and "State: LIVE TAIL" in expanded
                and "Narrated activity feed" in expanded
                and "payload=" not in expanded
                and "Engine started" in expanded
            ):
                return
        await pilot.pause()
    raise AssertionError("expanded mode shell state did not render")


async def _wait_for_debug_expanded_mode(pilot) -> None:
    await _wait_for_shell(pilot)
    pilot.app.action_toggle_display_mode()
    await pilot.pause()
    pilot.app.action_toggle_expanded_mode()
    for _ in range(40):
        if isinstance(pilot.app.screen, ShellScreen):
            status_line = static_text(pilot.app.screen.query_one("#shell-status", Static))
            expanded = static_text(pilot.app.screen.query_one("#shell-expanded-stream", Static))
            if (
                "DEBUG | Overview Expanded" in status_line
                and "Raw structured runtime events from the current shell event stream." in expanded
                and "State: LIVE TAIL" in expanded
                and "source=" in expanded
                and "payload=" in expanded
                and "Narrated activity feed" not in expanded
            ):
                return
        await pilot.pause()
    raise AssertionError("debug expanded mode shell state did not render")


async def _wait_for_operator_degraded_overview(pilot) -> None:
    await _wait_for_shell(pilot)
    assert isinstance(pilot.app.screen, ShellScreen)
    failure = GatewayFailure(
        operation="refresh.workspace",
        category=FailureCategory.IO,
        message="engine_events.log unavailable; using cached workspace snapshot",
        exception_type="OSError",
        retryable=True,
    )
    pilot.app.screen._store.apply_refresh_failure(
        failure,
        panels=(PanelId.OVERVIEW,),
        notice=notice_from_failure(
            failure,
            created_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
        ),
    )
    pilot.app.screen._render_state()
    for _ in range(40):
        if isinstance(pilot.app.screen, ShellScreen):
            status_line = static_text(pilot.app.screen.query_one("#shell-status", Static))
            overview = pilot.app.screen.query_one("#panel-overview", OverviewPanel).summary_text()
            notices = static_text(pilot.app.screen.query_one("#shell-notices", Static))
            if (
                "stale" in status_line
                and "Refresh degraded" in overview
                and "refresh workspace" in notices
            ):
                return
        await pilot.pause()
    raise AssertionError("operator degraded overview did not render")


async def _wait_for_health_gate_failure(pilot) -> None:
    for _ in range(80):
        if isinstance(pilot.app.screen, HealthGateScreen):
            body = static_text(pilot.app.screen.query_one("#health-gate-body", Static))
            if "Workspace health failed." in body:
                return
        await pilot.pause()
    raise AssertionError("health gate failure did not render")


async def _wait_for_run_detail(pilot) -> None:
    for _ in range(80):
        if isinstance(pilot.app.screen, RunDetailModal):
            body = static_text(pilot.app.screen.query_one("#run-detail-body", Static))
            if "RUN     smoke-standard" in body:
                return
        await pilot.pause()
    raise AssertionError("run detail modal did not load")


def test_tui_snapshot_shell_overview(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_shell,
    )


def test_tui_snapshot_shell_overview_idle(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_idle_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_shell,
    )


def test_tui_snapshot_shell_lifecycle_launching_daemon(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_launching_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_launching_daemon_state,
    )


def test_tui_snapshot_shell_overview_debug(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_debug_mode,
    )


def test_tui_snapshot_shell_overview_expanded(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_expanded_mode,
    )


def test_tui_snapshot_shell_overview_debug_expanded(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_debug_expanded_mode,
    )


def test_tui_snapshot_shell_overview_degraded(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_operator_degraded_overview,
    )


def test_tui_snapshot_logs_panel(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_logs_panel,
    )


def test_tui_snapshot_queue_panel(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_queue_panel,
    )


def test_tui_snapshot_runs_panel(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_runs_panel,
    )


def test_tui_snapshot_research_panel(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_research_panel,
    )


def test_tui_snapshot_config_panel(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_config_panel,
    )


def test_tui_snapshot_publish_panel(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_publish_panel,
    )


def test_tui_snapshot_help_modal(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "shell_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_help_modal,
    )


def test_tui_snapshot_health_gate_failure(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "health_gate_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_health_gate_failure,
    )


def test_tui_snapshot_run_detail_modal(snap_compare) -> None:
    assert snap_compare(
        str(SNAPSHOT_APPS / "run_detail_snapshot_app.py"),
        terminal_size=TERMINAL_SIZE,
        run_before=_wait_for_run_detail,
    )
