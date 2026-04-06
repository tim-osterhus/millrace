from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import shutil

import pytest
from textual.app import App, SystemCommand
from textual.geometry import Region
from textual.worker import WorkerState
from textual.widgets import Button, ContentSwitcher, Input, Static, TextArea

import millrace_engine.tui.gateway as gateway_module
import millrace_engine.tui.gateway_support as gateway_support_module
import millrace_engine.tui.launcher as launcher_module
import millrace_engine.tui.screens.run_detail_modal as run_detail_modal_module
import millrace_engine.tui.screens.shell as shell_module
import millrace_engine.tui.screens.shell_support as shell_support_module
import millrace_engine.tui.workers as workers_module
from millrace_engine.config import EngineConfig
from millrace_engine.events import EventRecord, EventSource, EventType, render_structured_event_line
from millrace_engine.markdown import parse_task_cards
from millrace_engine.tui.app import MillraceTUIApplication
from millrace_engine.tui.gateway import RuntimeGateway
from millrace_engine.tui.launcher import (
    LauncherObservationPaths,
    LauncherSettings,
    ObservationPathSource,
)
from millrace_engine.tui.formatting import compact_display_path, compact_run_label
from millrace_engine.tui.messages import (
    ActionFailed,
    ActionSucceeded,
    EventStreamFailed,
    EventsAppended,
    HealthCheckCompleted,
    HealthCheckFailed,
    RefreshFailed,
    RefreshSucceeded,
)
from millrace_engine.tui.models import (
    ActionResultView,
    ConfigFieldInputKind,
    ConfigFieldView,
    ConfigOverviewView,
    DisplayMode,
    EXPANDED_STREAM_WIDGET_ID,
    EventLogView,
    FailureCategory,
    GatewayFailure,
    GatewayResult,
    InterviewQuestionSummaryView,
    KeyValueView,
    LifecycleState,
    NoticeLevel,
    NoticeView,
    PanelId,
    PublishOverviewView,
    QueueOverviewView,
    QueueTaskView,
    RefreshPayload,
    ResearchAuditSummaryView,
    ResearchGovernanceOverviewView,
    ResearchOverviewView,
    RunDetailView,
    RunIntegrationSummaryView,
    RunPolicyEvidenceView,
    RunSummaryView,
    RunTransitionView,
    RunsOverviewView,
    RuntimeEventView,
    RuntimeOverviewView,
    SelectionDecisionView,
    SelectionSummaryView,
    lifecycle_signal_from_context,
    notice_from_action,
    notice_from_failure,
)
from millrace_engine.tui.screens.add_idea_modal import AddIdeaModal
from millrace_engine.tui.screens.add_task_modal import AddTaskModal
from millrace_engine.tui.screens.confirm_modal import ConfirmModal
from millrace_engine.tui.screens.config_edit_modal import ConfigEditModal
from millrace_engine.tui.screens.health_gate import HealthGateScreen
from millrace_engine.tui.screens.help_modal import HelpModal
from millrace_engine.tui.screens.interview_modal import InterviewModal
from millrace_engine.tui.screens.run_detail_modal import RunDetailModal
from millrace_engine.tui.screens.shell import ShellScreen
from millrace_engine.tui.widgets.config_panel import ConfigPanel
from millrace_engine.tui.widgets.expanded_stream import (
    ExpandedStreamView,
    render_runtime_event_operator_line,
)
from millrace_engine.tui.store import TUIStore
from millrace_engine.tui.widgets.logs_panel import LogsPanel
from millrace_engine.tui.widgets.notices import NoticesView, render_notices
from millrace_engine.tui.widgets.overview_panel import LatestRunSummary, OverviewPanel
from millrace_engine.tui.widgets.publish_panel import PublishPanel
from millrace_engine.tui.widgets.queue_panel import QueuePanel
from millrace_engine.tui.widgets.research_panel import ResearchPanel
from millrace_engine.tui.widgets.runs_panel import RunsPanel
from millrace_engine.tui.widgets.status_bar import StatusBar
from millrace_engine.tui.workers import WorkerSettings
from tests.support import FIXTURE_ROOT, load_workspace_fixture
from tests.tui_support import load_operator_workspace, seed_pending_interview_question


def _run_app_scenario(
    config_path,
    scenario,
    *,
    worker_settings: WorkerSettings | None = None,
    offer_startup_daemon_launch: bool = False,
) -> None:
    async def runner() -> None:
        app = MillraceTUIApplication.from_config_path(
            config_path,
            worker_settings=worker_settings,
            offer_startup_daemon_launch=offer_startup_daemon_launch,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await scenario(app, pilot)

    asyncio.run(runner())


def _run_modal_scenario(modal: RunDetailModal, scenario) -> None:
    class ModalHost(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.modal = modal

        async def on_mount(self) -> None:
            self.push_screen(self.modal)

    async def runner() -> None:
        app = ModalHost()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await scenario(app, pilot)

    asyncio.run(runner())


async def _wait_for_condition(pilot, predicate, *, attempts: int = 40) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError("condition not met before timeout")


def _static_text(widget: Static) -> str:
    return str(widget.render())


def _panel_text(widget) -> str:
    summary = getattr(widget, "summary_text", None)
    if callable(summary):
        return summary()
    return _static_text(widget)


def _write_backlog(workspace: Path, cards: list[tuple[str, str, str | None]]) -> None:
    lines = ["# Task Backlog", ""]
    for date, title, spec_id in cards:
        lines.append(f"## {date} - {title}")
        lines.append("")
        lines.append(f"- **Goal:** {title}.")
        if spec_id is not None:
            lines.append(f"- **Spec-ID:** {spec_id}")
        lines.append("")
    (workspace / "agents" / "tasksbacklog.md").write_text("\n".join(lines), encoding="utf-8")


def _write_runtime_state_snapshot(
    workspace: Path,
    *,
    process_running: bool,
    backlog_depth: int,
    mode: str = "daemon",
) -> None:
    state_path = workspace / "agents" / ".runtime" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "process_running": process_running,
        "paused": False,
        "pause_reason": None,
        "pause_run_id": None,
        "execution_status": "IDLE",
        "research_status": "IDLE",
        "active_task_id": None,
        "backlog_depth": backlog_depth,
        "deferred_queue_size": 0,
        "uptime_seconds": 1.0 if process_running else 0.0,
        "config_hash": "test-config-hash",
        "asset_bundle_version": "test-bundle",
        "pending_config_hash": None,
        "previous_config_hash": None,
        "pending_config_boundary": None,
        "pending_config_fields": [],
        "rollback_armed": False,
        "started_at": datetime(2026, 3, 25, tzinfo=timezone.utc).isoformat() if process_running else None,
        "updated_at": datetime(2026, 3, 25, tzinfo=timezone.utc).isoformat(),
        "mode": mode,
    }
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class _CompletedProcess:
    def __init__(self, *, returncode: int, stdout: bytes = b"", stderr: bytes = b"", pid: int = 4242) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.pid = pid

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    async def wait(self) -> int:
        return self.returncode


class _BlockingOnceProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.pid = 5150
        self.terminated = False
        self.killed = False
        self.communicate_started = asyncio.Event()

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicate_started.set()
        await asyncio.Future()
        return b"", b""

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 143

    def kill(self) -> None:
        self.killed = True
        self.returncode = 137

    async def wait(self) -> int:
        return self.returncode or 143


class _DetachedProcess:
    def __init__(self, *, returncode: int | None, pid: int = 6161) -> None:
        self.returncode = returncode
        self.pid = pid
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode or 0


def test_tui_shell_bootstraps_and_switches_panels(tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ShellScreen))
        assert isinstance(app.screen, ShellScreen)
        assert app.screen.query_one(StatusBar).display
        assert app.screen.active_panel == PanelId.OVERVIEW
        assert app.screen.query_one("#shell-content", ContentSwitcher).current == "panel-overview"
        assert app.screen.focused is not None
        assert app.screen.focused.id == "nav-overview"

        await pilot.press("2")
        await pilot.pause()
        assert app.screen.active_panel == PanelId.QUEUE
        assert app.screen.query_one("#shell-content", ContentSwitcher).current == "panel-queue"

        await pilot.press("c")
        await pilot.pause()
        assert app.screen.focused is not None
        assert app.screen.focused.id == "panel-queue"

        await pilot.press("s")
        await pilot.pause()
        assert app.screen.focused is not None
        assert app.screen.focused.id == "nav-queue"

    _run_app_scenario(config_path, scenario)


def test_tui_registers_minimal_system_commands(tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ShellScreen))
        commands = list(app.get_system_commands(app.screen))
        titles = {command.title for command in commands if isinstance(command, SystemCommand)}
        assert "Open Overview" in titles
        assert "Open Logs" in titles
        assert "Focus Sidebar" in titles
        assert "Focus Content" in titles
        assert "Open Keyboard Help" in titles
        assert "Start Once" in titles
        assert "Start Daemon" in titles
        assert "Pause Runtime" in titles
        assert "Resume Runtime" in titles
        assert "Stop Runtime" in titles
        assert "Edit Config Field" in titles
        assert "Reload Config" in titles
        assert "Publish Preflight" in titles
        assert "Publish Sync" in titles
        assert "Publish Commit (No Push)" in titles
        assert "Publish Commit And Push" in titles
        assert "Toggle Display Mode" in titles
        assert "Toggle Expanded Mode" in titles
        assert "Exit Expanded Mode" in titles

        await pilot.press("7")
        await pilot.pause()
        assert app.screen.active_panel == PanelId.PUBLISH

    _run_app_scenario(config_path, scenario)


def test_tui_shell_operator_fixture_boots_with_real_runs_and_logs(tmp_path) -> None:
    _, config_path = load_operator_workspace(tmp_path)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.runtime is not None
            and app.screen._store.state.runs is not None
            and app.screen._store.state.events is not None,
        )
        assert isinstance(app.screen, ShellScreen)

        overview = app.screen.query_one("#panel-overview", OverviewPanel)
        assert _static_text(overview.query_one("#overview-runtime-headline", Static)) == "running | mode daemon"
        assert _static_text(overview.query_one("#overview-next-value", Static)) == "Ship the happy path | SPEC-HAPPY-PATH"
        assert "WARN smoke-standard" in _static_text(overview.query_one("#overview-latest-headline", Static))

        await pilot.press("3")
        await pilot.pause()
        runs_text = _panel_text(app.screen.query_one("#panel-runs", RunsPanel))
        assert "smoke-standard" in runs_text
        assert "transition history not present" in runs_text

        await pilot.press("5")
        await pilot.pause()
        logs_panel = app.screen.query_one("#panel-logs", LogsPanel)
        logs_text = _panel_text(logs_panel)
        assert "SUMMARY follow" in logs_text
        assert "engine.started" in logs_text
        assert _static_text(logs_panel.query_one("#logs-mode-value", Static)) == "follow"
        assert "Recent runtime events" in _static_text(logs_panel.query_one("#logs-list-headline", Static))

    _run_app_scenario(config_path, scenario)


def test_tui_shell_display_mode_toggle_is_session_scoped_and_safe(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.runtime is not None,
        )
        assert isinstance(app.screen, ShellScreen)
        sidebar_toggle = app.screen.query_one("#sidebar-mode-toggle", Button)
        assert "operator -> debug" in str(sidebar_toggle.label)
        assert app.screen._store.state.display_mode is DisplayMode.OPERATOR

        app.action_toggle_display_mode()
        await pilot.pause()
        assert app.screen._store.state.display_mode is DisplayMode.DEBUG
        assert "debug -> operator" in str(sidebar_toggle.label)
        status_line = _static_text(app.screen.query_one("#shell-status", Static))
        assert status_line.startswith("DEBUG | Overview")

        app.action_toggle_display_mode()
        await pilot.pause()
        assert app.screen._store.state.display_mode is DisplayMode.OPERATOR
        assert "operator -> debug" in str(sidebar_toggle.label)

    _run_app_scenario(config_path, scenario)


def test_tui_shell_expanded_mode_replaces_main_content_only(monkeypatch, tmp_path) -> None:
    _, config_path = load_operator_workspace(tmp_path)
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.events is not None,
        )
        assert isinstance(app.screen, ShellScreen)

        sidebar_toggle = app.screen.query_one("#sidebar-expanded-toggle", Button)
        assert "enter expanded" in str(sidebar_toggle.label)
        assert app.screen.query_one("#shell-content", ContentSwitcher).current == "panel-overview"

        await pilot.press("e")
        await pilot.pause()
        assert app.screen.query_one("#shell-content", ContentSwitcher).current == EXPANDED_STREAM_WIDGET_ID
        assert "exit expanded" in str(sidebar_toggle.label)
        assert app.screen.active_panel is PanelId.OVERVIEW
        status_line = _static_text(app.screen.query_one("#shell-status", Static))
        assert status_line.startswith("OPERATOR | Overview Expanded")
        expanded = app.screen.query_one(f"#{EXPANDED_STREAM_WIDGET_ID}", ExpandedStreamView)
        expanded_text = expanded.summary_text()
        assert "State: LIVE TAIL" in expanded_text
        assert "Narrated activity feed" in expanded_text
        assert "l jumps to the live tail." in expanded_text
        assert "payload=" not in expanded_text
        assert expanded.follow_live is True
        assert app.screen.query_one("#shell-sidebar").display
        assert app.screen.query_one("#shell-notices").display

        await pilot.press("2")
        await pilot.pause()
        assert app.screen.active_panel is PanelId.QUEUE
        assert app.screen.query_one("#shell-content", ContentSwitcher).current == EXPANDED_STREAM_WIDGET_ID
        assert "OPERATOR EXPANDED | Queue" in expanded.summary_text()

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.query_one("#shell-content", ContentSwitcher).current == "panel-queue"
        assert "enter expanded" in str(sidebar_toggle.label)

    _run_app_scenario(config_path, scenario)


def test_expanded_stream_operator_mode_renders_narrated_runtime_lines() -> None:
    events = (
        RuntimeEventView(
            event_type="engine.started",
            source="engine",
            timestamp=datetime(2026, 3, 26, 14, 2, 11, tzinfo=timezone.utc),
            is_research_event=False,
            payload=(KeyValueView(key="mode", value="daemon"),),
            summary="mode=daemon",
            run_id=None,
        ),
        RuntimeEventView(
            event_type="execution.stage.started",
            source="execution",
            timestamp=datetime(2026, 3, 26, 14, 2, 13, tzinfo=timezone.utc),
            is_research_event=False,
            payload=(
                KeyValueView(key="run_id", value="run-123"),
                KeyValueView(key="stage", value="builder"),
            ),
            summary="stage=builder",
            run_id="run-123",
        ),
        RuntimeEventView(
            event_type="handoff.needs_research",
            source="execution",
            timestamp=datetime(2026, 3, 26, 14, 3, 1, tzinfo=timezone.utc),
            is_research_event=True,
            payload=(KeyValueView(key="task_id", value="task-7"),),
            summary="task_id=task-7",
            run_id=None,
        ),
        RuntimeEventView(
            event_type="research.blocked",
            source="research",
            timestamp=datetime(2026, 3, 26, 14, 3, 5, tzinfo=timezone.utc),
            is_research_event=True,
            payload=(
                KeyValueView(key="reason", value="lock unavailable"),
                KeyValueView(key="failure_kind", value="lock_unavailable"),
            ),
            summary="reason=lock unavailable",
            run_id=None,
        ),
        RuntimeEventView(
            event_type="control.command.applied",
            source="control",
            timestamp=datetime(2026, 3, 26, 14, 3, 7, tzinfo=timezone.utc),
            is_research_event=False,
            payload=(
                KeyValueView(key="command", value="pause"),
                KeyValueView(key="applied", value="true"),
            ),
            summary="command=pause | applied=true",
            run_id=None,
        ),
    )
    widget = ExpandedStreamView()

    widget.show_snapshot(
        active_panel_label="Overview",
        display_mode=DisplayMode.OPERATOR,
        events=EventLogView(events=events, last_loaded_at=events[-1].timestamp),
    )

    rendered = widget.summary_text()
    assert "OPERATOR EXPANDED | Overview" in rendered
    assert "State: LIVE TAIL" in rendered
    assert "[2026-03-26 14:02:11 UTC] Engine started in daemon mode" in rendered
    assert "[2026-03-26 14:02:13 UTC] Run run-123: Builder started" in rendered
    assert "[2026-03-26 14:03:01 UTC] Research wakeup: blocker handoff ready for task-7" in rendered
    assert "[2026-03-26 14:03:05 UTC] Research blocked (lock unavailable)" in rendered
    assert "[2026-03-26 14:03:07 UTC] Control applied pause" in rendered
    assert "l jumps to the live tail." in rendered
    assert "payload=" not in rendered


def test_expanded_stream_debug_mode_renders_raw_structured_event_lines() -> None:
    event = RuntimeEventView(
        event_type="execution.stage.completed",
        source="execution",
        timestamp=datetime(2026, 3, 26, 14, 2, 13, tzinfo=timezone.utc),
        is_research_event=False,
        payload=(
            KeyValueView(key="run_id", value="run-123"),
            KeyValueView(key="stage", value="builder"),
            KeyValueView(key="status", value="success"),
        ),
        summary="stage=builder | status=success",
        run_id="run-123",
    )
    widget = ExpandedStreamView()

    widget.show_snapshot(
        active_panel_label="Logs",
        display_mode=DisplayMode.DEBUG,
        events=EventLogView(events=(event,), last_loaded_at=event.timestamp),
    )

    rendered = widget.summary_text()
    assert "DEBUG EXPANDED | Logs" in rendered
    assert "Raw structured runtime events from the current shell event stream." in rendered
    assert "State: LIVE TAIL" in rendered
    expected_line = render_structured_event_line(
        timestamp=event.timestamp,
        event_type=event.event_type,
        source=event.source,
        payload={"run_id": "run-123", "stage": "builder", "status": "success"},
    )
    assert expected_line in rendered
    assert '"run_id": "run-123"' in rendered
    assert '"stage": "builder"' in rendered
    assert '"status": "success"' in rendered


def test_render_runtime_event_operator_line_falls_back_to_summary() -> None:
    event = RuntimeEventView(
        event_type="custom.event",
        source="engine",
        timestamp=datetime(2026, 3, 26, 14, 4, 0, tzinfo=timezone.utc),
        is_research_event=False,
        payload=(KeyValueView(key="note", value="hello"),),
        summary="note=hello",
        run_id=None,
    )

    assert render_runtime_event_operator_line(event) == "[2026-03-26 14:04:00 UTC] note=hello"


def test_render_runtime_event_operator_line_compacts_generated_run_ids() -> None:
    run_id = "20260401T043039323989Z__2026-03-31-2026-03-31-messaging-p0-schema-and-sender-contract-refactor"
    event = RuntimeEventView(
        event_type="execution.stage.started",
        source="execution",
        timestamp=datetime(2026, 4, 1, 4, 37, 43, tzinfo=timezone.utc),
        is_research_event=False,
        payload=(
            KeyValueView(key="run_id", value=run_id),
            KeyValueView(key="stage", value="troubleshoot"),
        ),
        summary="stage=troubleshoot",
        run_id=run_id,
    )

    rendered = render_runtime_event_operator_line(event)

    assert compact_run_label(run_id) in rendered
    assert run_id not in rendered
    assert rendered.endswith("Troubleshoot started")


def test_compact_display_path_prefers_stable_tail_for_long_absolute_paths() -> None:
    path = Path(
        "/private/var/folders/nc/fh6yj4vx1vqc35w5vx48vblr0000gn/T/"
        "millrace-tui-health-gate-snapshot/millrace/millrace.toml"
    )

    assert compact_display_path(path) == ".../millrace/millrace.toml"


def test_compact_display_path_drops_temp_root_marker_from_workspace_tail() -> None:
    path = Path(
        "/private/var/folders/nc/fh6yj4vx1vqc35w5vx48vblr0000gn/T/"
        "millrace-tui-health-gate-snapshot/millrace"
    )

    assert compact_display_path(path) == ".../millrace-tui-health-gate-snapshot/millrace"


def test_tui_shell_debug_expanded_mode_shows_structured_event_feed(monkeypatch, tmp_path) -> None:
    _, config_path = load_operator_workspace(tmp_path)
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.events is not None,
        )
        assert isinstance(app.screen, ShellScreen)

        app.action_toggle_display_mode()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

        expanded = app.screen.query_one(f"#{EXPANDED_STREAM_WIDGET_ID}", ExpandedStreamView)
        expanded_text = expanded.summary_text()
        assert "DEBUG EXPANDED | Overview" in expanded_text
        assert "Raw structured runtime events from the current shell event stream." in expanded_text
        assert "State: LIVE TAIL" in expanded_text
        assert "source=" in expanded_text
        assert "payload=" in expanded_text
        assert "No runtime events cached yet." not in expanded_text

    _run_app_scenario(config_path, scenario)


def test_tui_shell_expanded_mode_switches_renderer_with_display_mode(monkeypatch, tmp_path) -> None:
    _, config_path = load_operator_workspace(tmp_path)
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.events is not None,
        )
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("5")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

        expanded = app.screen.query_one(f"#{EXPANDED_STREAM_WIDGET_ID}", ExpandedStreamView)
        operator_text = expanded.summary_text()
        assert app.screen.active_panel is PanelId.LOGS
        assert "OPERATOR EXPANDED | Logs" in operator_text
        assert "Narrated activity feed" in operator_text
        assert "payload=" not in operator_text

        app.action_toggle_display_mode()
        await pilot.pause()
        debug_text = expanded.summary_text()
        status_line = _static_text(app.screen.query_one("#shell-status", Static))
        assert app.screen.query_one("#shell-content", ContentSwitcher).current == EXPANDED_STREAM_WIDGET_ID
        assert status_line.startswith("DEBUG | Logs Expanded")
        assert "DEBUG EXPANDED | Logs" in debug_text
        assert "Raw structured runtime events from the current shell event stream." in debug_text
        assert "payload=" in debug_text
        assert "Narrated activity feed" not in debug_text

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.query_one("#shell-content", ContentSwitcher).current == "panel-logs"
        assert app.screen.active_panel is PanelId.LOGS
        assert app.screen._store.state.display_mode is DisplayMode.DEBUG

    _run_app_scenario(config_path, scenario)


def test_tui_shell_expanded_stream_scrollback_and_jump_live(monkeypatch, tmp_path) -> None:
    _, config_path = load_operator_workspace(tmp_path)
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.events is not None,
        )
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("e")
        await pilot.pause()
        expanded = app.screen.query_one(f"#{EXPANDED_STREAM_WIDGET_ID}", ExpandedStreamView)

        base_time = datetime(2026, 3, 26, 15, 0, tzinfo=timezone.utc)
        for index in range(30):
            event = _sample_runtime_event(
                event_type="execution.stage.completed",
                source="execution",
                observed_at=base_time.replace(minute=index % 60),
                summary=f"stage=builder-{index} | status=success",
                run_id=f"run-shell-{index}",
                is_research_event=False,
            )
            app.screen.post_message(EventsAppended((event,), received_at=event.timestamp))
        await _wait_for_condition(pilot, lambda: expanded.max_scroll_y > 0)

        expanded.action_page_up()
        await pilot.pause()
        frozen_scroll_y = expanded.scroll_y
        assert expanded.follow_live is False
        assert "State: SCROLLBACK" in expanded.summary_text()

        new_event = _sample_runtime_event(
            event_type="execution.stage.failed",
            source="execution",
            observed_at=datetime(2026, 3, 26, 15, 59, tzinfo=timezone.utc),
            summary="stage=qa | status=blocked",
            run_id="run-shell-late",
            is_research_event=False,
        )
        app.screen.post_message(EventsAppended((new_event,), received_at=new_event.timestamp))
        await _wait_for_condition(
            pilot,
            lambda: "[2026-03-26 15:59:00 UTC]" in expanded.summary_text(),
        )

        assert expanded.follow_live is False
        assert expanded.scroll_y == frozen_scroll_y

        app.action_focus_sidebar()
        await pilot.pause()
        app.action_jump_expanded_stream_live()
        await _wait_for_condition(pilot, lambda: expanded.follow_live and expanded.at_live_tail)

        assert expanded.follow_live is True
        assert expanded.at_live_tail is True
        assert expanded.scroll_y >= frozen_scroll_y

    _run_app_scenario(config_path, scenario)


def test_tui_help_modal_surfaces_global_and_panel_shortcuts(tmp_path) -> None:
    _, config_path = load_operator_workspace(tmp_path)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ShellScreen))
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("question_mark")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, HelpModal))
        assert isinstance(app.screen, HelpModal)
        overview_help = _static_text(app.screen.query_one("#help-modal-body", Static))
        assert "Current panel: Overview" in overview_help
        assert "e toggles expanded stream mode and Escape exits it." in overview_help
        assert "Ctrl+P opens the command palette" in overview_help
        assert "This panel does not add extra keyboard controls." in overview_help

        await pilot.press("escape")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ShellScreen))
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("5")
        await pilot.pause()
        await pilot.press("question_mark")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, HelpModal))
        assert isinstance(app.screen, HelpModal)
        logs_help = _static_text(app.screen.query_one("#help-modal-body", Static))
        assert "Current panel: Logs" in logs_help
        assert "f toggles follow and freeze." in logs_help
        assert "Ctrl+Left/Right changes the source filter" in logs_help

    _run_app_scenario(config_path, scenario)


def test_tui_notices_render_stays_slim_and_truthful() -> None:
    observed_at = datetime(2026, 3, 25, 3, 4, 5, tzinfo=timezone.utc)
    info_notice = NoticeView(
        level=NoticeLevel.INFO,
        title="Start Daemon",
        message="daemon launched",
        created_at=observed_at,
    )
    warning_notice = NoticeView(
        level=NoticeLevel.WARNING,
        title="Publish Sync",
        message="origin not configured",
        created_at=observed_at,
    )

    assert render_notices(()) == "no notices"
    rendered = render_notices((info_notice, warning_notice))
    assert "2026-03-25" not in rendered
    assert "Start Daemon: daemon launched" not in rendered
    assert "03:04:05Z warn publish sync: origin not configured" in rendered


def test_tui_notices_view_escalates_warning_and_error_only() -> None:
    observed_at = datetime(2026, 3, 25, 3, 4, 5, tzinfo=timezone.utc)
    view = NoticesView(id="shell-notices")
    info_notice = NoticeView(
        level=NoticeLevel.INFO,
        title="Refresh",
        message="workspace refreshed",
        created_at=observed_at,
    )
    warning_notice = NoticeView(
        level=NoticeLevel.WARNING,
        title="Publish Commit",
        message="push skipped",
        created_at=observed_at,
    )
    error_notice = NoticeView(
        level=NoticeLevel.ERROR,
        title="Pause Runtime",
        message="daemon not running",
        created_at=observed_at,
    )

    view.show_notices((info_notice,))
    assert not view.has_class("notice-warning")
    assert not view.has_class("notice-error")

    view.show_notices((warning_notice,))
    assert view.has_class("notice-warning")
    assert not view.has_class("notice-error")

    view.show_notices((error_notice,))
    assert not view.has_class("notice-warning")
    assert view.has_class("notice-error")


def test_tui_health_gate_exposes_recovery_actions_and_retry_recovers(tmp_path) -> None:
    scenario_root = (
        tmp_path
        / "health-gate-path-compaction"
        / "segment-for-deterministic-display"
        / "fixture-root"
    )
    workspace, config_path = load_workspace_fixture(scenario_root, "control_mailbox")
    missing_size_status = workspace / "agents" / "size_status.md"
    logs_path = workspace / "agents" / "engine_events.log"
    workspace_display = compact_display_path(workspace)
    config_display = compact_display_path(config_path)
    logs_display = compact_display_path(logs_path)
    assert workspace_display != workspace.as_posix()
    assert config_display != config_path.as_posix()
    assert logs_display != logs_path.as_posix()

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, HealthGateScreen)
            and "Workspace health failed." in _static_text(app.screen.query_one("#health-gate-body", Static)),
        )
        assert isinstance(app.screen, HealthGateScreen)
        failure_body = _static_text(app.screen.query_one("#health-gate-body", Static))
        assert f"Workspace: {workspace_display}" in failure_body
        assert f"Config: {config_display}" in failure_body
        assert workspace.as_posix() not in failure_body
        assert config_path.as_posix() not in failure_body
        button_ids = {button.id for button in app.screen.query(Button)}
        assert {
            "health-gate-retry",
            "health-gate-open-config",
            "health-gate-open-logs",
            "health-gate-quit",
        } <= button_ids

        await pilot.click("#health-gate-open-config")
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, HealthGateScreen)
            and "Config recovery surface." in _static_text(app.screen.query_one("#health-gate-body", Static)),
        )
        config_body = _static_text(app.screen.query_one("#health-gate-body", Static))
        assert f"Config: {config_display}" in config_body
        assert f"Path: {config_display}" in config_body
        assert config_path.as_posix() not in config_body
        assert 'mode = "once"' in config_body
        assert isinstance(app.screen, HealthGateScreen)

        await pilot.click("#health-gate-open-logs")
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, HealthGateScreen)
            and "Runtime log recovery surface." in _static_text(app.screen.query_one("#health-gate-body", Static)),
        )
        logs_body = _static_text(app.screen.query_one("#health-gate-body", Static))
        assert f"Path: {logs_display}" in logs_body
        assert logs_path.as_posix() not in logs_body
        assert "Runtime event log is empty." in logs_body
        assert isinstance(app.screen, HealthGateScreen)

        missing_size_status.write_text("### SMALL\n", encoding="utf-8")
        await pilot.press("r")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ShellScreen))
        assert isinstance(app.screen, ShellScreen)

    _run_app_scenario(config_path, scenario)


def _selection_summary(*, run_id: str | None = None) -> SelectionSummaryView:
    return SelectionSummaryView(
        scope="preview",
        selection_ref="mode.standard.default",
        mode_ref="mode.standard.default",
        execution_loop_ref="loop.standard.default",
        frozen_plan_id="plan-123",
        frozen_plan_hash="hash-123",
        run_id=run_id,
        research_participation="stub",
        stage_labels=("builder:builder", "qa:qa"),
    )


def _selection_decision() -> SelectionDecisionView:
    return SelectionDecisionView(
        selected_size="SMALL",
        route_decision="default",
        route_reason="backlog is small",
        large_profile_decision="standard",
    )


def _sample_run_summary(
    *,
    run_id: str,
    observed_at: datetime,
    note: str | None = None,
    issue: str | None = None,
) -> RunSummaryView:
    return RunSummaryView(
        run_id=run_id,
        compiled_at=observed_at,
        selection_ref="mode:mode.standard@1.0.0",
        frozen_plan_id=f"frozen-plan:{run_id}",
        frozen_plan_hash=f"{run_id}-hash",
        stage_count=2,
        transition_count=3,
        latest_transition_at=observed_at,
        latest_transition_label="builder success",
        latest_status="QA_PENDING",
        routing_modes=("small",),
        latest_policy_decision="PASS",
        integration_target="qa",
        integration_enabled=False,
        snapshot_present=True,
        history_present=True,
        note=note,
        issue=issue,
    )


def _sample_runs_overview(*, observed_at: datetime, runs: tuple[RunSummaryView, ...] | None = None) -> RunsOverviewView:
    return RunsOverviewView(
        runs_dir="/tmp/workspace/agents/runs",
        scanned_at=observed_at,
        runs=runs
        or (
            _sample_run_summary(run_id="smoke-standard", observed_at=observed_at, note="transition history not present"),
        ),
    )


def _sample_run_detail(*, run_id: str, observed_at: datetime) -> RunDetailView:
    return RunDetailView(
        run_id=run_id,
        compiled_at=observed_at,
        frozen_plan_id=f"frozen-plan:{run_id}",
        frozen_plan_hash=f"{run_id}-hash",
        stage_count=2,
        selection=_selection_summary(run_id=run_id),
        selection_decision=_selection_decision(),
        current_preview=_selection_summary(),
        current_preview_decision=_selection_decision(),
        current_preview_error=None,
        routing_modes=("small",),
        snapshot_path=f"/tmp/{run_id}/resolved_snapshot.json",
        transition_history_path=f"/tmp/{run_id}/transition_history.jsonl",
        policy_hook_count=2,
        latest_policy_decision="PASS",
        latest_policy_evidence=RunPolicyEvidenceView(
            hook="pre_stage",
            evaluator="execution_integration_policy",
            decision="PASS",
            timestamp=observed_at,
            event_name="execution.stage.completed",
            node_id="builder",
            routing_mode="small",
            notes=("builder success path is allowed",),
            evidence_summaries=("task gate did not require integration", "builder routes to qa"),
        ),
        integration_policy=RunIntegrationSummaryView(
            effective_mode="large_only",
            builder_success_target="qa",
            should_run_integration=False,
            task_gate_required=False,
            task_integration_preference="inherit",
            requested_sequence=("builder", "qa"),
            effective_sequence=("builder", "qa"),
            available_execution_nodes=("builder", "qa"),
            reason="Builder routes to qa.",
        ),
        transitions=(
            RunTransitionView(
                event_id="evt-1",
                timestamp=observed_at,
                observed_timestamp=observed_at,
                event_name="stage.completed",
                source="engine",
                plane="execution",
                node_id="builder",
                kind_id="builder",
                outcome="success",
                status_before="IDLE",
                status_after="BUILDER_COMPLETE",
                active_task_before=None,
                active_task_after="task-1",
                routing_mode="small",
                queue_mutations_applied=("promoted",),
                artifacts_emitted=("artifact.md",),
            ),
        ),
    )


def _sample_refresh_payload(
    *,
    observed_at: datetime,
    selection_run_id: str | None = None,
    runs: RunsOverviewView | None = None,
) -> RefreshPayload:
    return RefreshPayload(
        refreshed_at=observed_at,
        runtime=RuntimeOverviewView(
            workspace_path="/tmp/workspace",
            config_path="/tmp/workspace/millrace.toml",
            config_source_kind="workspace",
            source_kind="live",
            process_running=False,
            paused=False,
            pause_reason=None,
            pause_run_id=None,
            mode="once",
            execution_status="IDLE",
            research_status="IDLE",
            active_task_id=None,
            backlog_depth=1,
            deferred_queue_size=0,
            uptime_seconds=None,
            asset_bundle_version="test-bundle",
            pending_config_hash=None,
            previous_config_hash=None,
            pending_config_boundary=None,
            pending_config_fields=(),
            rollback_armed=False,
            started_at=None,
            updated_at=observed_at,
            selection=_selection_summary(run_id=selection_run_id),
            selection_decision=_selection_decision(),
        ),
        config=_sample_config_overview(),
        queue=QueueOverviewView(
            active_task=None,
            next_task=QueueTaskView(task_id="task-1", title="Example task"),
            backlog_depth=1,
            backlog=(QueueTaskView(task_id="task-1", title="Example task"),),
        ),
        research=ResearchOverviewView(
            status="IDLE",
            source_kind="live",
            configured_mode="stub",
            configured_idle_mode="watch",
            current_mode="stub",
            last_mode="stub",
            mode_reason="bootstrap",
            cycle_count=0,
            transition_count=0,
            selected_family=None,
            deferred_breadcrumb_count=0,
            deferred_request_count=0,
            queue_families=(),
            audit_summary_path="/tmp/workspace/agents/audit_summary.json",
            audit_history_path="/tmp/workspace/agents/audit_history.md",
            audit_summary_present=False,
            latest_gate_decision=None,
            latest_completion_decision=None,
            completion_allowed=False,
            completion_reason="marker_missing",
            updated_at=observed_at,
            next_poll_at=None,
        ),
        events=EventLogView(events=(), last_loaded_at=observed_at),
        runs=runs,
    )


def _sample_interview_question(
    *,
    question_id: str = "SPEC-TUI-001__interview-001",
    spec_id: str = "SPEC-TUI-001",
    title: str = "Operator interview spec",
    question: str = "Should queue reorder approvals stay operator-confirmed in the TUI?",
) -> InterviewQuestionSummaryView:
    return InterviewQuestionSummaryView(
        question_id=question_id,
        status="pending",
        spec_id=spec_id,
        title=title,
        question=question,
        why_this_matters="This keeps queue mutation behavior aligned across foreground and daemon flows.",
        recommended_answer="Keep confirmation so the operator explicitly approves queue order changes.",
        answer_source="operator",
        blocking=True,
        source_path="agents/specs/staging/SPEC-TUI-001__operator-interview.md",
        updated_at=datetime(2026, 4, 4, tzinfo=timezone.utc),
    )


def _sample_config_overview(*, editing_enabled: bool = True) -> ConfigOverviewView:
    return ConfigOverviewView(
        config_path="/tmp/workspace/millrace.toml",
        source_kind="native_toml",
        source_ref="/tmp/workspace/millrace.toml",
        config_hash="config-hash-123456",
        bundle_version="test-bundle",
        editing_enabled=editing_enabled,
        editing_disabled_reason=(None if editing_enabled else "guided edits are available only for native TOML configs"),
        fields=(
            ConfigFieldView(
                key="engine.poll_interval_seconds",
                label="Poll interval",
                value="60",
                boundary="live_immediate",
                description="Seconds between idle polls when filesystem watch mode is not active.",
                editable=editing_enabled,
                input_kind=(ConfigFieldInputKind.INTEGER if editing_enabled else None),
                minimum=(1 if editing_enabled else None),
            ),
            ConfigFieldView(
                key="engine.idle_mode",
                label="Idle mode",
                value="watch",
                boundary="cycle_boundary",
                description="Choose whether the daemon idles by filesystem watch or by polling.",
                editable=editing_enabled,
                input_kind=(ConfigFieldInputKind.CHOICE if editing_enabled else None),
                options=(("watch", "poll") if editing_enabled else ()),
            ),
            ConfigFieldView(
                key="execution.integration_mode",
                label="Integration mode",
                value="large_only",
                boundary="stage_boundary",
                description="Control when integration runs after builder success.",
                editable=editing_enabled,
                input_kind=(ConfigFieldInputKind.CHOICE if editing_enabled else None),
                options=(("always", "large_only", "never") if editing_enabled else ()),
            ),
        ),
        startup_only_fields=(
            ConfigFieldView(
                key="paths.workspace",
                label="Workspace root",
                value=".",
                boundary="startup_only",
                description="Configured workspace root used to resolve all runtime-relative paths.",
            ),
            ConfigFieldView(
                key="paths.agents_dir",
                label="Agents directory",
                value="agents",
                boundary="startup_only",
                description="Runtime workspace subdirectory that contains queues, state, and artifacts.",
            ),
        ),
    )


def _sample_publish_overview(
    *,
    status: str = "ready",
    has_changes: bool = True,
    skip_reason: str | None = "push_disabled",
    git_worktree_present: bool = True,
    git_worktree_valid: bool = True,
    origin_configured: bool = True,
    branch: str | None = "main",
) -> PublishOverviewView:
    changed_paths = ("agents/status.md", "millrace.toml") if has_changes else ()
    return PublishOverviewView(
        staging_repo_dir="/tmp/workspace/staging",
        manifest_source_kind="packaged",
        manifest_source_ref="package:agents/staging_manifest.yml",
        manifest_version=1,
        selected_paths=(
            "agents",
            "README.md",
            "ADVISOR.md",
            "OPERATOR_GUIDE.md",
            "docs/RUNTIME_DEEP_DIVE.md",
        ),
        branch=branch,
        commit_message="Millrace staging sync",
        push_requested=False,
        git_worktree_present=git_worktree_present,
        git_worktree_valid=git_worktree_valid,
        origin_configured=origin_configured,
        has_changes=has_changes,
        changed_paths=changed_paths,
        commit_allowed=git_worktree_valid and has_changes,
        publish_allowed=False,
        status=status,
        skip_reason=skip_reason,
    )


def _sample_runtime_event(
    *,
    event_type: str,
    source: str,
    observed_at: datetime,
    summary: str = "",
    run_id: str | None = None,
    category: str | None = None,
    is_research_event: bool | None = None,
    payload: tuple[KeyValueView, ...] = (),
) -> RuntimeEventView:
    if category is None:
        category = source[:3].upper()
    return RuntimeEventView(
        event_type=event_type,
        source=source,
        timestamp=observed_at,
        is_research_event=(source == "research" if is_research_event is None else is_research_event),
        payload=payload,
        category=category,
        summary=summary or event_type,
        run_id=run_id,
    )


def _selection_object(*, scope: str, run_id: str | None) -> SimpleNamespace:
    ref = SimpleNamespace(kind=SimpleNamespace(value="mode"), id="mode.standard", version="1.0.0")
    return SimpleNamespace(
        scope=scope,
        selection=SimpleNamespace(ref=ref),
        mode=SimpleNamespace(ref=ref),
        execution_loop=SimpleNamespace(
            ref=SimpleNamespace(kind=SimpleNamespace(value="loop"), id="loop.standard", version="1.0.0")
        ),
        frozen_plan_id="plan-123",
        frozen_plan_hash="hash-123",
        run_id=run_id,
        research_participation="stub",
        stage_bindings=(SimpleNamespace(node_id="builder", kind_id="builder"),),
    )


def test_launcher_start_once_shapes_success_result(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _CompletedProcess(
            returncode=0,
            stdout=b"Process: stopped\nExecution status: IDLE\n",
            stderr=b"",
        )

    monkeypatch.setattr(launcher_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(launcher_module.launch_start_once(tmp_path / "millrace.toml"))

    assert result.ok
    action = result.value
    assert action is not None
    assert action.message == "once run completed"
    assert action.mode == "foreground"
    assert ("stdout", "Process: stopped Execution status: IDLE") in {
        (item.key, item.value) for item in action.details
    }
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] == asyncio.subprocess.PIPE
    assert captured["kwargs"]["stderr"] == asyncio.subprocess.PIPE


def test_launcher_start_once_surfaces_failure_excerpt(monkeypatch, tmp_path) -> None:
    async def fake_create_subprocess_exec(*args, **kwargs):
        return _CompletedProcess(
            returncode=1,
            stdout=b"",
            stderr=b"engine already running\n",
        )

    monkeypatch.setattr(launcher_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(launcher_module.launch_start_once(tmp_path / "millrace.toml"))

    assert not result.ok
    assert result.failure is not None
    assert "exit code 1" in result.failure.message
    assert "engine already running" in result.failure.message


def test_launcher_start_once_cancellation_terminates_owned_process(monkeypatch, tmp_path) -> None:
    process = _BlockingOnceProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(launcher_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    async def scenario() -> None:
        task = asyncio.create_task(
            launcher_module.launch_start_once(
                tmp_path / "millrace.toml",
                settings=LauncherSettings(foreground_cancel_timeout_seconds=0.01),
            )
        )
        await process.communicate_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert process.terminated is True
    assert process.killed is False


def test_launcher_start_daemon_uses_detached_launch_and_runtime_state(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    running_states = iter((False, True))

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _DetachedProcess(returncode=None, pid=7331)

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(launcher_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(launcher_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(launcher_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        launcher_module,
        "_load_observation_paths",
        lambda config_path: LauncherObservationPaths(
            state_path=tmp_path / "state.json",
            events_log_path=tmp_path / "engine_events.log",
            source=ObservationPathSource.CONFIG,
            source_detail="native_toml",
        ),
    )
    monkeypatch.setattr(launcher_module, "_daemon_running", lambda path: next(running_states))

    result = asyncio.run(
        launcher_module.launch_start_daemon(
            tmp_path / "millrace.toml",
            settings=LauncherSettings(
                daemon_startup_timeout_seconds=0.5,
                daemon_startup_poll_interval_seconds=0.01,
            ),
        )
    )

    assert result.ok
    action = result.value
    assert action is not None
    assert action.message == "daemon launched"
    assert action.mode == "detached"
    assert ("pid", "7331") in {(item.key, item.value) for item in action.details}
    assert ("path_source", "config") in {(item.key, item.value) for item in action.details}
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.DEVNULL
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["stdout"] is captured["kwargs"]["stderr"]


def test_launcher_start_daemon_rejects_preexisting_running_state(monkeypatch, tmp_path) -> None:
    called = False

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal called
        called = True
        return _DetachedProcess(returncode=None, pid=7331)

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(launcher_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(launcher_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        launcher_module,
        "_load_observation_paths",
        lambda config_path: LauncherObservationPaths(
            state_path=tmp_path / "state.json",
            events_log_path=tmp_path / "engine_events.log",
            source=ObservationPathSource.CONFIG,
        ),
    )
    monkeypatch.setattr(launcher_module, "_daemon_running", lambda path: True)

    result = asyncio.run(launcher_module.launch_start_daemon(tmp_path / "millrace.toml"))

    assert not result.ok
    assert result.failure is not None
    assert "already reports a running daemon" in result.failure.message
    assert result.failure.retryable is False
    assert called is False


def test_launcher_start_daemon_surfaces_early_exit_with_actionable_detail(monkeypatch, tmp_path) -> None:
    async def fake_create_subprocess_exec(*args, **kwargs):
        kwargs["stdout"].write(b"engine already running\n")
        kwargs["stdout"].flush()
        return _DetachedProcess(returncode=1, pid=7444)

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(launcher_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(launcher_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        launcher_module,
        "_load_observation_paths",
        lambda config_path: LauncherObservationPaths(
            state_path=tmp_path / "state.json",
            events_log_path=tmp_path / "engine_events.log",
            source=ObservationPathSource.WORKSPACE_FALLBACK,
            source_detail="No native config found",
        ),
    )
    monkeypatch.setattr(launcher_module, "_daemon_running", lambda path: False)

    result = asyncio.run(launcher_module.launch_start_daemon(tmp_path / "millrace.toml"))

    assert not result.ok
    assert result.failure is not None
    assert "engine_events.log" in result.failure.message
    assert "start --daemon" in result.failure.message
    assert "engine already running" in result.failure.message
    assert "workspace fallback" in result.failure.message


def test_launcher_start_daemon_cancellation_does_not_terminate_detached_process(monkeypatch, tmp_path) -> None:
    process = _DetachedProcess(returncode=None, pid=7555)
    sleep_started = asyncio.Event()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def blocking_sleep(delay: float) -> None:
        sleep_started.set()
        await asyncio.Future()

    monkeypatch.setattr(launcher_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(launcher_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(launcher_module.asyncio, "sleep", blocking_sleep)
    monkeypatch.setattr(
        launcher_module,
        "_load_observation_paths",
        lambda config_path: LauncherObservationPaths(
            state_path=tmp_path / "state.json",
            events_log_path=tmp_path / "engine_events.log",
            source=ObservationPathSource.CONFIG,
        ),
    )
    monkeypatch.setattr(launcher_module, "_daemon_running", lambda path: False)

    async def scenario() -> None:
        task = asyncio.create_task(
            launcher_module.launch_start_daemon(
                tmp_path / "millrace.toml",
                settings=LauncherSettings(
                    daemon_startup_timeout_seconds=10.0,
                    daemon_startup_poll_interval_seconds=0.01,
                ),
            )
        )
        await sleep_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert process.terminated is False
    assert process.killed is False


def test_launcher_observation_paths_fallback_is_explicit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        launcher_module,
        "load_engine_config",
        lambda config_path: (_ for _ in ()).throw(FileNotFoundError("missing config")),
    )

    paths = launcher_module._load_observation_paths(tmp_path / "millrace.toml")

    assert paths.source is ObservationPathSource.WORKSPACE_FALLBACK
    assert paths.state_path == tmp_path / "agents" / ".runtime" / "state.json"
    assert paths.events_log_path == tmp_path / "agents" / "engine_events.log"
    assert paths.source_detail is not None
    assert "missing config" in paths.source_detail


def test_launcher_start_daemon_surfaces_observation_path_resolution_failure(monkeypatch, tmp_path) -> None:
    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(launcher_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        launcher_module,
        "_load_observation_paths",
        lambda config_path: (_ for _ in ()).throw(ValueError("invalid config payload")),
    )

    result = asyncio.run(launcher_module.launch_start_daemon(tmp_path / "millrace.toml"))

    assert not result.ok
    assert result.failure is not None
    assert result.failure.category is FailureCategory.INPUT
    assert result.failure.retryable is True
    assert "unable to resolve daemon observation paths" in result.failure.message
    assert "invalid config payload" in result.failure.message


def test_runtime_gateway_loads_shaped_snapshot_and_publish_status(tmp_path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "golden_path")
    gateway = RuntimeGateway(config_path)

    snapshot_result = gateway.load_workspace_snapshot(log_limit=10)
    assert snapshot_result.ok
    snapshot = snapshot_result.value
    assert snapshot is not None
    assert snapshot.runtime is not None
    assert snapshot.runtime.config_path == config_path.as_posix()
    assert snapshot.runtime.execution_status == "IDLE"
    assert snapshot.runtime.selection.selection_ref.startswith("mode:mode.standard@")
    assert snapshot.config is not None
    assert snapshot.config.source_kind == "native_toml"
    assert {field.key for field in snapshot.config.fields} >= {
        "engine.poll_interval_seconds",
        "engine.inter_task_delay_seconds",
        "engine.idle_mode",
        "execution.integration_mode",
        "execution.run_update_on_empty",
    }
    assert snapshot.queue is not None
    assert snapshot.queue.backlog_depth == 1
    assert snapshot.queue.backlog[0].task_id == "2026-03-19__ship-the-happy-path"
    assert snapshot.queue.backlog[0].spec_id == "SPEC-HAPPY-PATH"
    assert snapshot.research is not None
    assert snapshot.research.status == "IDLE"
    assert snapshot.events is not None
    assert snapshot.events.events == ()
    assert snapshot.runs is not None
    assert snapshot.runs.runs == ()

    publish_result = gateway.load_publish_status()
    assert publish_result.ok
    publish_payload = publish_result.value
    assert publish_payload is not None
    assert publish_payload.publish is not None
    assert publish_payload.publish.status == "skip_publish"
    assert publish_payload.publish.publish_allowed is False
    assert publish_payload.publish.staging_repo_dir.endswith("/staging")


def test_runtime_gateway_loads_sparse_snapshot_for_empty_backlog(tmp_path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "backlog_empty")
    gateway = RuntimeGateway(config_path)

    snapshot_result = gateway.load_workspace_snapshot(log_limit=5)

    assert snapshot_result.ok
    snapshot = snapshot_result.value
    assert snapshot is not None
    assert snapshot.runtime is not None
    assert snapshot.runtime.execution_status == "IDLE"
    assert snapshot.queue is not None
    assert snapshot.queue.backlog_depth == 0
    assert snapshot.queue.backlog == ()
    assert snapshot.research is not None
    assert snapshot.research.status == "IDLE"
    assert snapshot.events is not None
    assert snapshot.events.events == ()
    assert snapshot.runs is not None
    assert snapshot.runs.runs == ()


def test_runtime_gateway_shapes_research_audit_governance_and_recent_activity(monkeypatch, tmp_path) -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)

    class FakeControl:
        def __init__(self, config_path: Path) -> None:
            self.config_path = config_path
            self.paths = SimpleNamespace(runs_dir=tmp_path / "agents" / "runs")
            self.paths.runs_dir.mkdir(parents=True, exist_ok=True)

        def status(self, *, detail: bool = False):
            return SimpleNamespace(
                runtime=SimpleNamespace(
                    workspace_path=Path("/tmp/workspace"),
                    process_running=False,
                    paused=False,
                    pause_reason=None,
                    pause_run_id=None,
                    mode="once",
                    execution_status="IDLE",
                    research_status="AUDIT_FAIL",
                    active_task_id=None,
                    backlog_depth=0,
                    deferred_queue_size=1,
                    uptime_seconds=None,
                    asset_bundle_version="test-bundle",
                    pending_config_hash=None,
                    previous_config_hash=None,
                    pending_config_boundary=None,
                    pending_config_fields=(),
                    rollback_armed=False,
                    started_at=None,
                    updated_at=observed_at,
                ),
                config_path=Path("/tmp/workspace/millrace.toml"),
                config_source_kind="workspace",
                source_kind="live",
                selection=_selection_object(scope="preview", run_id=None),
                selection_explanation=_selection_decision(),
            )

        def queue_inspect(self):
            return SimpleNamespace(
                active_task=None,
                next_task=None,
                backlog_depth=0,
                backlog=(),
            )

        def config_show(self):
            return SimpleNamespace(
                source=SimpleNamespace(kind="native_toml", primary_path=tmp_path / "millrace.toml"),
                config=EngineConfig(),
                config_hash="config-hash",
                assets=SimpleNamespace(bundle_version="test-bundle"),
            )

        def research_report(self):
            return SimpleNamespace(
                status="AUDIT_FAIL",
                source_kind="snapshot",
                configured_mode="audit",
                configured_idle_mode="watch",
                runtime=SimpleNamespace(
                    current_mode="AUDIT",
                    last_mode="STUB",
                    mode_reason="audit queue ready",
                    cycle_count=3,
                    transition_count=2,
                    queue_snapshot=SimpleNamespace(selected_family=SimpleNamespace(value="audit")),
                    deferred_requests=("request-1",),
                    updated_at=observed_at,
                    next_poll_at=observed_at,
                ),
                queue_families=(
                    SimpleNamespace(
                        family=SimpleNamespace(value="audit"),
                        ready=True,
                        item_count=1,
                        queue_owner=SimpleNamespace(value="research"),
                        queue_paths=(Path("/tmp/workspace/agents/research/audit"),),
                        contract_paths=(Path("/tmp/workspace/agents/audit/strict_contract.json"),),
                        first_item=SimpleNamespace(
                            family=SimpleNamespace(value="audit"),
                            item_key="audit-1",
                            title="Audit backlog empty handoff",
                            item_kind=SimpleNamespace(value="audit_record"),
                            queue_path=Path("/tmp/workspace/agents/research/audit"),
                            item_path=Path("/tmp/workspace/agents/research/audit/audit-1.md"),
                            occurred_at=observed_at,
                            source_status=SimpleNamespace(value="READY"),
                            stage_blocked=None,
                        ),
                    ),
                ),
                deferred_breadcrumb_count=2,
                audit_history_path=Path("/tmp/workspace/agents/audit_history.md"),
                audit_summary_path=Path("/tmp/workspace/agents/audit_summary.json"),
                audit_summary=SimpleNamespace(
                    updated_at=observed_at,
                    counts={"total": 4, "pass": 3, "fail": 1},
                    last_outcome=SimpleNamespace(
                        status="AUDIT_FAIL",
                        details="contract drift detected",
                        at=observed_at,
                        title="Backlog empty audit",
                        decision="FAIL",
                        reason_count=2,
                    ),
                ),
                latest_gate_decision=SimpleNamespace(decision="FAIL"),
                latest_completion_decision=SimpleNamespace(decision="FAIL"),
                latest_audit_remediation=SimpleNamespace(
                    selected_action="enqueue_backlog_task",
                    remediation_spec_id="SPEC-AUDIT",
                    remediation_task_id="task-42",
                    remediation_task_title="Regenerate audit remediation",
                ),
                governance=SimpleNamespace(
                    queue_governor=SimpleNamespace(status="pinned", reason="frozen-family-policy-preserved"),
                    drift=SimpleNamespace(
                        status="warning",
                        reason="policy drift detected",
                        drift_fields=("initial_family_max_specs",),
                    ),
                    governance_canary=SimpleNamespace(
                        status="drifted",
                        reason="governance canary drifted",
                        changed_fields=("hard_latch_on_policy_drift",),
                    ),
                    progress_watchdog=SimpleNamespace(
                        status="stalled",
                        reason="visible recovery work missing",
                        batch_id="recovery-batch-7",
                        visible_recovery_task_count=0,
                        escalation_action="manual_review",
                        recovery_regeneration=SimpleNamespace(
                            status="manual_only",
                            regenerated_task_id="task-regen-1",
                            regenerated_task_title="Manual recovery review",
                        ),
                    ),
                ),
                completion_state=SimpleNamespace(completion_allowed=False, reason="audit_not_passed"),
            )

        def research_history(self, limit: int):
            return [
                EventRecord(
                    type=EventType.RESEARCH_SCAN_COMPLETED,
                    source=EventSource.RESEARCH,
                    timestamp=observed_at,
                    payload={"family": "audit", "run_id": "run-audit-1"},
                )
            ]

        def interview_list(self):
            return SimpleNamespace(questions=())

        def logs(self, n: int = 50):
            return [
                EventRecord(
                    type=EventType.STAGE_COMPLETED,
                    source=EventSource.EXECUTION,
                    timestamp=observed_at,
                    payload={"stage": "builder", "run_id": "run-exec-1"},
                )
            ]

    monkeypatch.setattr(gateway_module, "EngineControl", FakeControl)
    gateway = RuntimeGateway(tmp_path / "millrace.toml")

    result = gateway.load_workspace_snapshot(log_limit=5)

    assert result.ok
    payload = result.value
    assert payload is not None
    assert payload.research is not None
    assert payload.research.audit_summary is not None
    assert payload.research.audit_summary.fail_count == 1
    assert payload.research.audit_summary.remediation_task_id == "task-42"
    assert payload.research.governance is not None
    assert payload.research.governance.drift_status == "warning"
    assert payload.research.governance.recovery_status == "stalled"
    assert payload.research.recent_activity[0].category == "RSH"
    assert payload.research.recent_activity[0].run_id == "run-audit-1"
    assert payload.events is not None
    assert payload.events.events[0].category == "EXE"
    assert payload.events.events[0].run_id == "run-exec-1"


def test_runtime_gateway_load_workspace_snapshot_includes_recent_runs(monkeypatch, tmp_path) -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    runs_dir = tmp_path / "agents" / "runs"
    run_ok = runs_dir / "run-ok"
    run_bad = runs_dir / "run-bad"
    run_empty = runs_dir / "run-empty"
    for path in (run_ok, run_bad, run_empty):
        path.mkdir(parents=True, exist_ok=True)
    (run_ok / "resolved_snapshot.json").write_text("{}", encoding="utf-8")
    (run_ok / "transition_history.jsonl").write_text("{}\n", encoding="utf-8")
    (run_bad / "resolved_snapshot.json").write_text("{}", encoding="utf-8")
    (run_empty / "transition_history.jsonl").write_text("{}\n", encoding="utf-8")

    class FakeControl:
        def __init__(self, config_path: Path) -> None:
            self.paths = SimpleNamespace(runs_dir=runs_dir)

        def status(self, *, detail: bool = False):
            return SimpleNamespace(
                runtime=SimpleNamespace(
                    process_running=False,
                    paused=False,
                    pause_reason=None,
                    pause_run_id=None,
                    mode="once",
                    execution_status="IDLE",
                    research_status="IDLE",
                    active_task_id=None,
                    backlog_depth=0,
                    deferred_queue_size=0,
                    uptime_seconds=None,
                    asset_bundle_version="test-bundle",
                    pending_config_hash=None,
                    previous_config_hash=None,
                    pending_config_boundary=None,
                    pending_config_fields=(),
                    rollback_armed=False,
                    started_at=None,
                    updated_at=observed_at,
                ),
                config_path=tmp_path / "millrace.toml",
                config_source_kind="workspace",
                source_kind="live",
                selection=_selection_object(scope="preview", run_id=None),
                selection_explanation=_selection_decision(),
            )

        def queue_inspect(self):
            return SimpleNamespace(active_task=None, next_task=None, backlog_depth=0, backlog=())

        def config_show(self):
            return SimpleNamespace(
                source=SimpleNamespace(kind="native_toml", primary_path=tmp_path / "millrace.toml"),
                config=EngineConfig(),
                config_hash="config-hash",
                assets=SimpleNamespace(bundle_version="test-bundle"),
            )

        def research_report(self):
            return SimpleNamespace(
                status=SimpleNamespace(value="IDLE"),
                source_kind="live",
                configured_mode=SimpleNamespace(value="stub"),
                configured_idle_mode="watch",
                runtime=SimpleNamespace(
                    current_mode=SimpleNamespace(value="stub"),
                    last_mode=SimpleNamespace(value="stub"),
                    mode_reason="bootstrap",
                    cycle_count=0,
                    transition_count=0,
                    queue_snapshot=SimpleNamespace(selected_family=None),
                    deferred_requests=(),
                    updated_at=observed_at,
                    next_poll_at=None,
                ),
                queue_families=(),
                deferred_breadcrumb_count=0,
                audit_summary_path=tmp_path / "audit_summary.json",
                audit_history_path=tmp_path / "audit_history.md",
                audit_summary=None,
                latest_gate_decision=None,
                latest_completion_decision=None,
                latest_audit_remediation=None,
                governance=None,
                completion_state=SimpleNamespace(completion_allowed=False, reason="marker_missing"),
            )

        def research_history(self, limit: int):
            return ()

        def interview_list(self):
            return SimpleNamespace(questions=())

        def logs(self, limit: int):
            return ()

    def fake_read_run_provenance(run_dir: Path):
        if run_dir.name == "run-ok":
            frozen_plan = SimpleNamespace(
                plan_id="frozen-plan:run-ok-hash",
                content_hash="run-ok-hash",
                selection_ref=SimpleNamespace(kind=SimpleNamespace(value="mode"), id="mode.standard", version="1.0.0"),
            )
            latest_transition = SimpleNamespace(
                timestamp=observed_at,
                node_id="builder",
                outcome="success",
                event_name="stage.completed",
                status_after="QA_PENDING",
                frozen_plan=frozen_plan,
            )
            return SimpleNamespace(
                run_id="run-ok",
                compile_snapshot=SimpleNamespace(
                    created_at=observed_at,
                    selection_ref=frozen_plan.selection_ref,
                    frozen_plan=frozen_plan,
                    content=SimpleNamespace(execution_plan=SimpleNamespace(stages=("builder", "qa"))),
                ),
                runtime_history=(latest_transition,),
                latest_policy_evidence=SimpleNamespace(decision="PASS"),
                integration_policy=SimpleNamespace(builder_success_target="qa", should_run_integration=False),
                policy_hooks=SimpleNamespace(latest_decision="PASS"),
                expected_routing_modes=lambda: ("small",),
            )
        if run_dir.name == "run-bad":
            raise ValueError("bad snapshot")
        return None

    monkeypatch.setattr(gateway_module, "EngineControl", FakeControl)
    monkeypatch.setattr(gateway_module, "read_run_provenance", fake_read_run_provenance)

    gateway = RuntimeGateway(tmp_path / "millrace.toml")
    result = gateway.load_workspace_snapshot(log_limit=0)

    assert result.ok
    payload = result.value
    assert payload is not None
    assert payload.runs is not None
    run_by_id = {run.run_id: run for run in payload.runs.runs}
    assert set(run_by_id) == {"run-empty", "run-bad", "run-ok"}
    assert run_by_id["run-empty"].note == "run provenance artifacts missing"
    assert run_by_id["run-bad"].issue == "invalid provenance: bad snapshot"
    assert run_by_id["run-ok"].frozen_plan_hash == "run-ok-hash"
    assert run_by_id["run-ok"].latest_policy_decision == "PASS"


def test_status_bar_and_overview_panel_render_runtime_cockpit_summary() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    payload = _sample_refresh_payload(observed_at=observed_at)
    lifecycle = lifecycle_signal_from_context(runtime=payload.runtime)
    latest_run = LatestRunSummary(
        run_id="smoke-standard",
        compiled_at="2026-03-25T00:00:00Z",
        selection_ref="mode:mode.standard@1.0.0",
        stage_count=2,
        history_present=False,
        note="transition history not present",
    )

    status_bar = StatusBar(id="shell-status")
    status_bar.show_state(
        workspace_path=Path("/tmp/workspace"),
        active_panel_label="Overview",
        expanded_mode=False,
        display_mode=DisplayMode.OPERATOR,
        lifecycle=lifecycle,
        health_report=None,
        runtime=payload.runtime,
        queue=payload.queue,
        last_refreshed_at=observed_at,
        refresh_failure=None,
        busy_message=None,
    )
    status_text = _static_text(status_bar)
    assert "OPERATOR | Overview | daemon stopped | backlog 1 | active none" in status_text
    assert "health pending | 00:00:00Z" in status_text

    overview = OverviewPanel(id="panel-overview")
    overview.show_snapshot(
        runtime=payload.runtime,
        queue=payload.queue,
        research=payload.research,
        latest_run=latest_run,
    )
    overview_text = overview.summary_text()
    assert "RUNTIME  stopped | mode once | exec IDLE | uptime --" in overview_text
    assert "NEXT     Example task" in overview_text
    assert "LATEST   WARN smoke-standard | sel mode.std | 00:00:00Z | stg 2 | hist no | transition history not present" in overview_text
    assert "ATTN     transition history not present | latest run metadata is incomplete" in overview_text

    overview.show_snapshot(
        runtime=payload.runtime,
        queue=payload.queue,
        research=payload.research,
        latest_run=latest_run,
        display_mode=DisplayMode.DEBUG,
    )
    debug_overview_text = overview.summary_text()
    assert "WORK     queued 1 | deferred 0 | active none | next Example task" in debug_overview_text
    assert "LATEST   WARN smoke-standard" in debug_overview_text


def test_tui_shell_operator_overview_and_status_fit_single_line_at_standard_shell_size(tmp_path) -> None:
    _, config_path = load_operator_workspace(tmp_path)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.runtime is not None,
        )
        assert isinstance(app.screen, ShellScreen)

        status_bar = app.screen.query_one(StatusBar)
        status_lines = [
            strip.text.replace("│", " ").strip()
            for strip in status_bar.render_lines(Region(0, 0, status_bar.size.width, status_bar.size.height))
            if strip.text.strip()
        ]
        assert len(status_lines) == 1
        assert status_lines[0].startswith("OPERATOR | Overview | daemon running | backlog 1 | active none")

        overview = app.screen.query_one(OverviewPanel)
        assert _static_text(overview.query_one("#overview-active-label", Static)) == "Active task"
        assert _static_text(overview.query_one("#overview-next-label", Static)) == "Next"
        assert _static_text(overview.query_one("#overview-backlog-label", Static)) == "Backlog"
        latest_detail = _static_text(overview.query_one("#overview-latest-detail", Static))
        assert "sel mode.std" in latest_detail
        assert "hist no" in latest_detail

    _run_app_scenario(config_path, scenario)


def test_lifecycle_signal_models_required_states() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    payload = _sample_refresh_payload(observed_at=observed_at)
    assert payload.runtime is not None
    runtime_idle = payload.runtime
    idle = lifecycle_signal_from_context(runtime=runtime_idle)
    assert idle.state is LifecycleState.IDLE

    launching_once = lifecycle_signal_from_context(
        runtime=runtime_idle,
        pending_action="start_once",
        pending_message="foreground once run in progress",
    )
    assert launching_once.state is LifecycleState.LAUNCHING_ONCE

    launching_daemon = lifecycle_signal_from_context(
        runtime=runtime_idle,
        pending_action="start_daemon",
    )
    assert launching_daemon.state is LifecycleState.LAUNCHING_DAEMON

    running = lifecycle_signal_from_context(runtime=replace(runtime_idle, process_running=True, mode="daemon"))
    assert running.state is LifecycleState.DAEMON_RUNNING

    paused_runtime = replace(runtime_idle, process_running=True, mode="daemon", paused=True, pause_reason="operator pause")
    paused = lifecycle_signal_from_context(runtime=paused_runtime)
    assert paused.state is LifecycleState.PAUSED

    stop_in_progress = lifecycle_signal_from_context(
        runtime=replace(runtime_idle, process_running=True, mode="daemon"),
        pending_action="stop",
    )
    assert stop_in_progress.state is LifecycleState.STOP_IN_PROGRESS

    failure = lifecycle_signal_from_context(
        runtime=runtime_idle,
        lifecycle_failure=GatewayFailure(
            operation="start.daemon",
            category=FailureCategory.CONTROL,
            message="daemon launch failed",
            exception_type="RuntimeError",
        ),
    )
    assert failure.state is LifecycleState.LIFECYCLE_FAILURE


def test_queue_panel_renders_empty_and_active_only_states_truthfully() -> None:
    panel = QueuePanel(id="panel-queue")

    active_only = QueueOverviewView(
        active_task=QueueTaskView(task_id="task-active", title="Ship the active task", spec_id="SPEC-ACTIVE"),
        next_task=None,
        backlog_depth=0,
        backlog=(),
    )
    panel.show_snapshot(active_only)
    active_text = panel.summary_text()
    assert "SUMMARY active 1 | next none | backlog 0" in active_text
    assert "ACTIVE  Ship the active task" in active_text
    assert "NEXT    none" in active_text
    assert "BACKLOG empty | one task is active and nothing is queued behind it" in active_text
    assert panel.selected_task_id is None

    empty_queue = QueueOverviewView(
        active_task=None,
        next_task=None,
        backlog_depth=0,
        backlog=(),
    )
    panel.show_snapshot(empty_queue)
    empty_text = panel.summary_text()
    assert "SUMMARY active 0 | next none | backlog 0" in empty_text
    assert "BACKLOG empty | no queued tasks are waiting" in empty_text
    assert panel.selected_task_id is None

    debug_queue = QueueOverviewView(
        active_task=None,
        next_task=QueueTaskView(task_id="task-1", title="Queued with metadata", spec_id="SPEC-001"),
        backlog_depth=1,
        backlog=(QueueTaskView(task_id="task-1", title="Queued with metadata", spec_id="SPEC-001"),),
    )
    panel.show_snapshot(debug_queue, display_mode=DisplayMode.DEBUG)
    debug_text = panel.summary_text()
    assert "NEXT    Queued with metadata [task-1] | SPEC-001" in debug_text
    assert "Queued with metadata [task-1 | SPEC-001]" in debug_text


def test_research_panel_renders_audit_governance_and_recent_activity() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    panel = ResearchPanel(id="panel-research")
    research = ResearchOverviewView(
        status="AUDIT_FAIL",
        source_kind="snapshot",
        configured_mode="audit",
        configured_idle_mode="watch",
        current_mode="AUDIT",
        last_mode="STUB",
        mode_reason="audit queue ready",
        cycle_count=4,
        transition_count=3,
        selected_family="audit",
        deferred_breadcrumb_count=2,
        deferred_request_count=1,
        queue_families=(),
        audit_summary_path="/tmp/workspace/agents/audit_summary.json",
        audit_history_path="/tmp/workspace/agents/audit_history.md",
        audit_summary_present=True,
        latest_gate_decision="FAIL",
        latest_completion_decision="FAIL",
        completion_allowed=False,
        completion_reason="audit_not_passed",
        updated_at=observed_at,
        next_poll_at=observed_at,
        audit_summary=ResearchAuditSummaryView(
            updated_at=observed_at,
            total_count=4,
            pass_count=3,
            fail_count=1,
            last_status="AUDIT_FAIL",
            last_details="contract drift detected",
            last_at=observed_at,
            last_title="Backlog empty audit",
            last_decision="FAIL",
            last_reason_count=2,
            remediation_action="enqueue_backlog_task",
            remediation_spec_id="SPEC-AUDIT",
            remediation_task_id="task-42",
            remediation_task_title="Regenerate audit remediation",
        ),
        governance=ResearchGovernanceOverviewView(
            queue_governor_status="pinned",
            queue_governor_reason="frozen-family-policy-preserved",
            drift_status="warning",
            drift_reason="policy drift detected",
            drift_fields=("initial_family_max_specs",),
            canary_status="drifted",
            canary_reason="governance canary drifted",
            canary_changed_fields=("hard_latch_on_policy_drift",),
            recovery_status="stalled",
            recovery_reason="visible recovery work missing",
            recovery_batch_id="recovery-batch-7",
            recovery_visible_task_count=0,
            recovery_escalation_action="manual_review",
            recovery_regeneration_status="manual_only",
            regenerated_task_id="task-regen-1",
            regenerated_task_title="Manual recovery review",
        ),
        recent_activity=(
            _sample_runtime_event(
                event_type="research.scan.completed",
                source="research",
                observed_at=observed_at,
                category="RSH",
                summary="family=audit",
                run_id="run-audit-1",
            ),
        ),
    )

    panel.show_snapshot(research)
    text = panel.summary_text()

    assert "STATE   AUDIT_FAIL | mode AUDIT | selected audit" in text
    assert "METRIC  families 0/0 ready | items 0 | deferred 1 | breadcrumbs 2" in text
    assert "AUDIT   blocked | reason audit_not_passed | gate FAIL" in text
    assert "LAST    00:00:00 | research.scan.completed | family=audit | run run-audit-1" in text
    assert "NEXT    switch to debug for audit and governance detail" in text
    assert "drift warning" in text
    assert "recovery stalled" in text
    assert "regen manual_only" in text

    panel.show_snapshot(research, display_mode=DisplayMode.DEBUG)
    debug_text = panel.summary_text()
    assert "MODE    current AUDIT | last STUB | configured audit | idle watch" in debug_text
    assert "AUDIT   total 4 | pass 3 | fail 1" in debug_text
    assert "REMEDY  enqueue_backlog_task | task task-42 | spec SPEC-AUDIT" in debug_text
    assert "drift warning | policy drift detected | fields initial_family_max_specs" in debug_text
    assert "recovery stalled | visible recovery work missing | batch recovery-batch-7 | visible tasks 0 | action manual_review" in debug_text
    assert "research.scan.completed | family=audit | run run-audit-1" in debug_text


def test_research_panel_operator_mode_compacts_generated_run_ids() -> None:
    observed_at = datetime(2026, 4, 1, 4, 37, 43, tzinfo=timezone.utc)
    run_id = "20260401T043039323989Z__2026-03-31-2026-03-31-messaging-p0-schema-and-sender-contract-refactor"
    panel = ResearchPanel(id="panel-research")
    research = ResearchOverviewView(
        status="INCIDENT",
        source_kind="live",
        configured_mode="auto",
        configured_idle_mode="watch",
        current_mode="INCIDENT",
        last_mode="AUTO",
        mode_reason="incident queue ready",
        cycle_count=1,
        transition_count=1,
        selected_family="incident",
        deferred_breadcrumb_count=0,
        deferred_request_count=0,
        queue_families=(),
        audit_summary_path="/tmp/workspace/agents/audit_summary.json",
        audit_history_path="/tmp/workspace/agents/audit_history.md",
        audit_summary_present=False,
        latest_gate_decision=None,
        latest_completion_decision=None,
        completion_allowed=False,
        completion_reason="incident_open",
        updated_at=observed_at,
        next_poll_at=observed_at,
        recent_activity=(
            _sample_runtime_event(
                event_type="research.scan.completed",
                source="research",
                observed_at=observed_at,
                category="RSH",
                summary="family=incident",
                run_id=run_id,
            ),
        ),
    )

    panel.show_snapshot(research)
    text = panel.summary_text()
    compact_label = compact_run_label(run_id)

    assert compact_label in text
    assert run_id not in text


def test_research_panel_renders_pending_interview_questions_and_selection() -> None:
    observed_at = datetime(2026, 4, 4, tzinfo=timezone.utc)
    panel = ResearchPanel(id="panel-research")
    question = _sample_interview_question()
    research = ResearchOverviewView(
        status="SPEC_INTERVIEW_RUNNING",
        source_kind="live",
        configured_mode="goalspec",
        configured_idle_mode="watch",
        current_mode="GOALSPEC",
        last_mode="GOALSPEC",
        mode_reason="operator interview pending",
        cycle_count=2,
        transition_count=2,
        selected_family="goalspec",
        deferred_breadcrumb_count=0,
        deferred_request_count=0,
        queue_families=(),
        audit_summary_path="/tmp/workspace/agents/audit_summary.json",
        audit_history_path="/tmp/workspace/agents/audit_history.md",
        audit_summary_present=False,
        latest_gate_decision=None,
        latest_completion_decision=None,
        completion_allowed=False,
        completion_reason="interview_pending",
        updated_at=observed_at,
        next_poll_at=observed_at,
        interview_questions=(question,),
    )

    panel.show_snapshot(research)
    text = panel.summary_text()

    assert "INTERVIEW 1 pending | focus Operator interview spec | blocking" in text
    assert "PENDING interview questions" in text
    assert "> Operator interview spec | SPEC-TUI-001 | focus | blocking" in text
    assert "NEXT    press Enter to answer, accept, or skip the selected interview question" in text


def test_interview_modal_validates_answer_and_returns_requests() -> None:
    class ModalHost(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.result = None

        async def on_mount(self) -> None:
            self.push_screen(InterviewModal(question=_sample_interview_question()), self._capture)

        def _capture(self, result) -> None:
            self.result = result

    async def runner() -> None:
        app = ModalHost()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, InterviewModal)
            app.screen.action_submit_answer()
            await _wait_for_condition(
                pilot,
                lambda: isinstance(app.screen, InterviewModal)
                and "Answer text is required" in _static_text(app.screen.query_one("#interview-error", Static)),
            )

            app.screen.query_one("#interview-answer-text", TextArea).load_text(
                "Keep explicit confirmation in the TUI."
            )
            await pilot.pause()
            app.screen.action_submit_answer()
            await _wait_for_condition(pilot, lambda: app.result is not None)
            assert app.result.action == "answer"
            assert app.result.question_id == "SPEC-TUI-001__interview-001"
            assert app.result.answer_text == "Keep explicit confirmation in the TUI."

        app = ModalHost()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, InterviewModal)
            app.screen.action_accept_recommendation()
            await _wait_for_condition(pilot, lambda: app.result is not None)
            assert app.result.action == "accept"

        app = ModalHost()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, InterviewModal)
            app.screen.query_one("#interview-skip-reason", Input).value = "Defer until governance review."
            await pilot.pause()
            app.screen.action_skip_question()
            await _wait_for_condition(pilot, lambda: app.result is not None)
            assert app.result.action == "skip"
            assert app.result.skip_reason == "Defer until governance review."

    asyncio.run(runner())


def test_runs_panel_operator_mode_compacts_generated_run_ids() -> None:
    observed_at = datetime(2026, 4, 1, 4, 37, 43, tzinfo=timezone.utc)
    run_id = "20260401T043039323989Z__2026-03-31-2026-03-31-messaging-p0-schema-and-sender-contract-refactor"
    panel = RunsPanel(id="panel-runs")
    runs = _sample_runs_overview(
        observed_at=observed_at,
        runs=(
            _sample_run_summary(run_id=run_id, observed_at=observed_at),
        ),
    )

    panel.show_snapshot(runs, requested_run_id=run_id)
    text = panel.summary_text()
    compact_label = compact_run_label(run_id)

    assert compact_label in text
    assert run_id not in text


def test_config_panel_operator_and_debug_modes_split_summary_and_internal_detail() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    payload = _sample_refresh_payload(observed_at=observed_at)
    assert payload.config is not None
    assert payload.runtime is not None

    panel = ConfigPanel(id="panel-config")
    panel.show_snapshot(payload.config, runtime=payload.runtime, display_mode=DisplayMode.OPERATOR)
    operator_text = panel.summary_text()
    assert "STATUS  edits on | supported 3 | pending none" in operator_text
    assert "SOURCE  native_toml" in operator_text
    assert "QUEUE   none queued" in operator_text
    assert "EDITABLE" in operator_text
    assert "Poll interval = 60 [LIVE]" in operator_text
    assert "BOUNDARY [LIVE] now | [STAGE] after stage | [CYCLE] next cycle | [STARTUP] restart only" in operator_text
    assert "NEXT    Up/Down select field | Enter/E edit selected | R reload config from disk" in operator_text
    assert "DETAIL  open debug for hashes, startup-only fields, and raw keys" in operator_text

    panel.show_snapshot(payload.config, runtime=payload.runtime, display_mode=DisplayMode.DEBUG)
    debug_text = panel.summary_text()
    assert "SUPPORTED" in debug_text
    assert "Poll interval [engine.poll_interval_seconds] = 60 | live_immediate | editable" in debug_text
    assert "STARTUP" in debug_text
    assert "Workspace root [paths.workspace] = ." in debug_text
    assert "live_immediate: takes effect on the next accepted reload" in debug_text


def test_config_panel_operator_mode_hides_debug_only_source_and_startup_detail() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    payload = _sample_refresh_payload(observed_at=observed_at)
    assert payload.config is not None
    assert payload.runtime is not None

    panel = ConfigPanel(id="panel-config")
    panel.show_snapshot(payload.config, runtime=payload.runtime, display_mode=DisplayMode.OPERATOR)
    operator_text = panel.summary_text()
    assert "SOURCE  native_toml | /tmp/workspace/millrace.toml" in operator_text
    assert " | hash " not in operator_text
    assert " | bundle " not in operator_text
    assert "STARTUP\n" not in operator_text
    assert "Workspace root [paths.workspace] = ." not in operator_text
    assert "engine.poll_interval_seconds" not in operator_text

    panel.show_snapshot(payload.config, runtime=payload.runtime, display_mode=DisplayMode.DEBUG)
    debug_text = panel.summary_text()
    assert " | hash " in debug_text
    assert " | bundle test-bundle" in debug_text
    assert "Workspace root [paths.workspace] = ." in debug_text


def test_publish_panel_operator_and_debug_modes_split_action_summary_and_internal_detail() -> None:
    panel = PublishPanel(id="panel-publish")
    publish = _sample_publish_overview(
        status="skip_publish",
        has_changes=False,
        skip_reason="missing_git_worktree",
        git_worktree_present=False,
        git_worktree_valid=False,
        origin_configured=False,
        branch=None,
    )
    panel.show_snapshot(publish, display_mode=DisplayMode.OPERATOR)
    operator_text = panel.summary_text()
    assert "STATUS  blocked | staging repo is missing a git worktree | changed 0" in operator_text
    assert "READY   commit no | push-ready no | branch detached" in operator_text
    assert "HEALTH  worktree no | valid no | origin no" in operator_text
    assert "NEXT    G sync staging, then R refresh preflight to re-check readiness" in operator_text
    assert "ALERT   publish is blocked until staging health is fixed" in operator_text
    assert "DETAIL  open debug for raw status, manifest refs, and full path lists" in operator_text

    panel.show_snapshot(publish, display_mode=DisplayMode.DEBUG)
    debug_text = panel.summary_text()
    assert "MANIFST packaged | package:agents/staging_manifest.yml | version 1 | selected 5" in debug_text
    assert "SKIP    staging repo is missing a git worktree | publish_allowed no" in debug_text
    assert "SELECTED" in debug_text


def test_publish_panel_operator_ready_no_push_path_is_not_blocked() -> None:
    panel = PublishPanel(id="panel-publish")
    publish = _sample_publish_overview(
        status="ready",
        has_changes=True,
        skip_reason="push_disabled",
        git_worktree_present=True,
        git_worktree_valid=True,
        origin_configured=True,
        branch="main",
    )
    panel.show_snapshot(publish, display_mode=DisplayMode.OPERATOR)
    operator_text = panel.summary_text()
    assert "STATUS  ready | commit path available | changed 2" in operator_text
    assert "READY   commit yes | push-ready yes | branch main" in operator_text
    assert "NEXT    N commit locally (default safe path) | P commit and push (higher friction)" in operator_text
    assert "ALERT   blocked" not in operator_text


def test_major_panels_failure_progressive_disclosure_splits_operator_and_debug() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    payload = _sample_refresh_payload(observed_at=observed_at)
    assert payload.runtime is not None
    assert payload.queue is not None
    assert payload.research is not None
    assert payload.events is not None
    assert payload.config is not None
    runs = _sample_runs_overview(observed_at=observed_at)

    failure = GatewayFailure(
        operation="refresh.workspace",
        category=FailureCategory.CONTROL,
        message="panel snapshot failed",
        exception_type="ControlError",
        retryable=False,
    )
    publish = _sample_publish_overview(
        status="ready",
        has_changes=True,
        skip_reason="push_disabled",
        git_worktree_present=True,
        git_worktree_valid=True,
        origin_configured=True,
        branch="main",
    )

    panel_cases = (
        (
            "OVERVIEW",
            OverviewPanel(id="panel-overview"),
            lambda panel: panel.show_snapshot(
                runtime=payload.runtime,
                queue=payload.queue,
                research=payload.research,
                latest_run=None,
                failure=failure,
                display_mode=DisplayMode.OPERATOR,
            ),
            lambda panel: panel.show_snapshot(
                runtime=payload.runtime,
                queue=payload.queue,
                research=payload.research,
                latest_run=None,
                failure=failure,
                display_mode=DisplayMode.DEBUG,
            ),
        ),
        (
            "QUEUE",
            QueuePanel(id="panel-queue"),
            lambda panel: panel.show_snapshot(
                payload.queue,
                failure=failure,
                display_mode=DisplayMode.OPERATOR,
            ),
            lambda panel: panel.show_snapshot(
                payload.queue,
                failure=failure,
                display_mode=DisplayMode.DEBUG,
            ),
        ),
        (
            "RUNS",
            RunsPanel(id="panel-runs"),
            lambda panel: panel.show_snapshot(
                runs,
                failure=failure,
                display_mode=DisplayMode.OPERATOR,
            ),
            lambda panel: panel.show_snapshot(
                runs,
                failure=failure,
                display_mode=DisplayMode.DEBUG,
            ),
        ),
        (
            "RESEARCH",
            ResearchPanel(id="panel-research"),
            lambda panel: panel.show_snapshot(
                payload.research,
                failure=failure,
                display_mode=DisplayMode.OPERATOR,
            ),
            lambda panel: panel.show_snapshot(
                payload.research,
                failure=failure,
                display_mode=DisplayMode.DEBUG,
            ),
        ),
        (
            "LOGS",
            LogsPanel(id="panel-logs"),
            lambda panel: panel.show_snapshot(
                payload.events,
                failure=failure,
                display_mode=DisplayMode.OPERATOR,
            ),
            lambda panel: panel.show_snapshot(
                payload.events,
                failure=failure,
                display_mode=DisplayMode.DEBUG,
            ),
        ),
        (
            "CONFIG",
            ConfigPanel(id="panel-config"),
            lambda panel: panel.show_snapshot(
                payload.config,
                runtime=payload.runtime,
                failure=failure,
                display_mode=DisplayMode.OPERATOR,
            ),
            lambda panel: panel.show_snapshot(
                payload.config,
                runtime=payload.runtime,
                failure=failure,
                display_mode=DisplayMode.DEBUG,
            ),
        ),
        (
            "PUBLISH",
            PublishPanel(id="panel-publish"),
            lambda panel: panel.show_snapshot(
                publish,
                failure=failure,
                display_mode=DisplayMode.OPERATOR,
            ),
            lambda panel: panel.show_snapshot(
                publish,
                failure=failure,
                display_mode=DisplayMode.DEBUG,
            ),
        ),
    )

    expected_detail = "DETAIL  op refresh.workspace | cat control | type ControlError | retry no"
    for panel_label, panel, render_operator, render_debug in panel_cases:
        render_operator(panel)
        operator_text = panel.summary_text() if isinstance(panel, OverviewPanel) else _panel_text(panel)
        assert f"{panel_label} stale: panel snapshot failed" in operator_text
        assert "STATE   showing last known snapshot" in operator_text
        assert "DETAIL  open debug for technical detail" in operator_text
        assert expected_detail not in operator_text

        render_debug(panel)
        debug_text = panel.summary_text() if isinstance(panel, OverviewPanel) else _panel_text(panel)
        assert f"{panel_label} stale: panel snapshot failed" in debug_text
        assert "STATE   last known snapshot available" in debug_text
        assert expected_detail in debug_text


def test_panel_failure_progressive_disclosure_debug_expands_unavailable_state() -> None:
    failure = GatewayFailure(
        operation="refresh.queue",
        category=FailureCategory.IO,
        message="queue state missing",
        exception_type="FileNotFoundError",
        retryable=True,
    )
    panel = QueuePanel(id="panel-queue")
    panel.show_snapshot(None, failure=failure, display_mode=DisplayMode.DEBUG)
    text = panel.summary_text()
    assert "QUEUE unavailable: queue state missing" in text
    assert "STATE   no snapshot available" in text
    assert "DETAIL  op refresh.queue | cat io | type FileNotFoundError | retry yes" in text


def test_runs_panel_renders_recent_summaries_and_requested_missing_run() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    panel = RunsPanel(id="panel-runs")
    runs = _sample_runs_overview(
        observed_at=observed_at,
        runs=(
            _sample_run_summary(run_id="run-2", observed_at=observed_at),
            _sample_run_summary(run_id="run-1", observed_at=observed_at.replace(second=1), issue="invalid provenance"),
        ),
    )

    panel.show_snapshot(runs, requested_run_id="deleted-run")
    text = panel.summary_text()

    assert "SUMMARY recent 2 | flagged 1 | scanned 2026-03-25T00:00:00Z" in text
    assert "requested run deleted-run is not in the current recent-runs list" in text
    assert "INFO run-2 | sel mode.std | stg 2 | status qa_pending | 00:00:00 | tr 3" in text
    assert "FAIL   invalid provenance" in text
    assert panel.selected_run_id == "run-2"

    posted: list[RunsPanel.RunRequested] = []
    panel.post_message = posted.append  # type: ignore[method-assign]
    panel.action_cursor_down()
    panel.action_submit_selection()

    assert panel.selected_run_id == "run-1"
    assert len(posted) == 1
    assert posted[0].run_id == "run-1"

    panel.show_snapshot(runs, requested_run_id="deleted-run", display_mode=DisplayMode.DEBUG)
    debug_text = panel.summary_text()
    assert "source /tmp/workspace/agents/runs" in debug_text
    assert "run-2 | mode:mode.standard@1.0.0 | 2026-03-25T00:00:00Z | plan run-2-hash | 2 stages" in debug_text
    assert "issue invalid provenance" in debug_text


def test_logs_panel_filters_freezes_and_emits_run_requests() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    panel = LogsPanel(id="panel-logs")
    first_batch = EventLogView(
        events=(
            _sample_runtime_event(
                event_type="engine.started",
                source="engine",
                observed_at=observed_at,
                category="ENG",
                summary="stage=bootstrap",
            ),
            _sample_runtime_event(
                event_type="execution.stage.started",
                source="execution",
                observed_at=observed_at.replace(second=1),
                category="EXE",
                summary="stage=builder",
                run_id="run-1",
                is_research_event=False,
                payload=(KeyValueView("stage", "builder"),),
            ),
            _sample_runtime_event(
                event_type="research.received",
                source="research",
                observed_at=observed_at.replace(second=2),
                category="RSH",
                summary="family=audit",
                is_research_event=True,
            ),
        ),
        last_loaded_at=observed_at.replace(second=2),
    )

    panel.show_snapshot(first_batch)
    assert panel.follow_mode is True
    assert panel.selected_run_id is None

    panel.action_toggle_follow()
    panel.action_cursor_up()
    assert panel.follow_mode is False
    assert panel.selected_run_id == "run-1"

    second_batch = EventLogView(
        events=first_batch.events
        + (
            _sample_runtime_event(
                event_type="execution.stage.completed",
                source="execution",
                observed_at=observed_at.replace(second=3),
                category="EXE",
                summary="stage=builder | status=success",
                run_id="run-2",
                is_research_event=False,
                payload=(KeyValueView("stage", "builder"), KeyValueView("status", "success")),
            ),
        ),
        last_loaded_at=observed_at.replace(second=3),
    )
    panel.show_snapshot(second_batch)
    assert panel.selected_run_id == "run-1"

    panel.set_source_filter("execution")
    panel.set_event_type_filter("execution.stage.started")
    text = panel.summary_text()
    assert "SUMMARY frozen | visible 1 | alert 0 | warn 0" in text
    assert "source execution | type execution.stage.started" in text
    assert "FOCUS   1/1 | INFO | execution.stage.started | stage=builder | run run-1" in text
    assert "execution.stage.completed" not in text

    posted: list[LogsPanel.RunRequested] = []
    panel.post_message = posted.append  # type: ignore[method-assign]
    panel.action_submit_selection()

    assert len(posted) == 1
    assert posted[0].run_id == "run-1"

    panel.show_snapshot(second_batch, display_mode=DisplayMode.DEBUG)
    debug_text = panel.summary_text()
    assert "MODE    frozen | retained 4 | visible 1" in debug_text
    assert "payload stage=builder" in debug_text


def test_logs_panel_operator_mode_compacts_generated_run_ids() -> None:
    observed_at = datetime(2026, 4, 1, 4, 37, 43, tzinfo=timezone.utc)
    run_id = "20260401T043039323989Z__2026-03-31-2026-03-31-messaging-p0-schema-and-sender-contract-refactor"
    panel = LogsPanel(id="panel-logs")

    panel.show_snapshot(
        EventLogView(
            events=(
                _sample_runtime_event(
                    event_type="execution.stage.started",
                    source="execution",
                    observed_at=observed_at,
                    category="EXE",
                    summary="stage=troubleshoot",
                    run_id=run_id,
                    is_research_event=False,
                    payload=(KeyValueView("stage", "troubleshoot"),),
                ),
            ),
            last_loaded_at=observed_at,
        )
    )

    text = panel.summary_text()
    compact_label = compact_run_label(run_id)

    assert compact_label in text
    assert run_id not in text


def test_logs_panel_selection_distinguishes_events_with_different_payloads() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    first = _sample_runtime_event(
        event_type="execution.stage.completed",
        source="execution",
        observed_at=observed_at,
        category="EXE",
        summary="stage=builder | status=success",
        run_id="run-1",
        is_research_event=False,
        payload=(KeyValueView("stage", "builder"), KeyValueView("status", "success")),
    )
    second = _sample_runtime_event(
        event_type="execution.stage.completed",
        source="execution",
        observed_at=observed_at,
        category="EXE",
        summary="stage=builder | status=success",
        run_id="run-1",
        is_research_event=False,
        payload=(KeyValueView("stage", "qa"), KeyValueView("status", "success")),
    )
    panel = LogsPanel(id="panel-logs")

    panel.show_snapshot(EventLogView(events=(first, second), last_loaded_at=observed_at))
    assert panel._selected_event() == second

    panel.action_toggle_follow()
    panel.action_cursor_up()
    assert panel._selected_event() == first


def test_queue_panel_preserves_reorder_and_emits_run_requests() -> None:
    panel = QueuePanel(id="panel-queue")
    queue = QueueOverviewView(
        active_task=None,
        next_task=QueueTaskView(task_id="task-1", title="First task"),
        backlog_depth=2,
        backlog=(
            QueueTaskView(task_id="task-1", title="First task"),
            QueueTaskView(task_id="task-2", title="Second task"),
        ),
    )

    panel.show_snapshot(queue, run_id="run-active-1")
    text = panel.summary_text()
    assert "RUN     run-active-1 | o detail" in text

    posted: list[QueuePanel.RunRequested] = []
    panel.post_message = posted.append  # type: ignore[method-assign]
    panel.action_open_run_detail()

    assert len(posted) == 1
    assert posted[0].run_id == "run-active-1"
    assert panel.reorder_mode is False

    panel.action_submit_selection()
    assert panel.reorder_mode is True


def test_runtime_gateway_rebuilds_engine_control_per_call(monkeypatch, tmp_path) -> None:
    seen_config_paths: list[Path] = []

    class FakeControl:
        def __init__(self, config_path: Path) -> None:
            seen_config_paths.append(Path(config_path))

        def add_task(self, title: str, *, body=None, spec_id=None):
            return SimpleNamespace(
                message="task added",
                applied=True,
                mode="direct",
                command_id=None,
                payload={"task_id": title.lower().replace(" ", "-")},
            )

    monkeypatch.setattr(gateway_module, "EngineControl", FakeControl)
    config_path = tmp_path / "millrace.toml"
    gateway = RuntimeGateway(config_path)

    first = gateway.add_task("First Task")
    second = gateway.add_task("Second Task")

    assert first.ok
    assert second.ok
    assert len(seen_config_paths) == 2
    assert seen_config_paths == [config_path.resolve(), config_path.resolve()]


def test_runtime_gateway_load_run_detail_maps_success_payload(monkeypatch, tmp_path) -> None:
    class FakeControl:
        def __init__(self, config_path: Path) -> None:
            self.config_path = config_path

        def run_provenance(self, run_id: str):
            selection = _selection_object(scope="frozen_run", run_id=run_id)
            current_preview = _selection_object(scope="preview", run_id=None)
            explanation = SimpleNamespace(
                selected_size="SMALL",
                route_decision="default",
                route_reason="fixture",
                large_profile_decision="standard",
                large_profile_reason=None,
            )
            record = SimpleNamespace(
                event_id="evt-1",
                timestamp=datetime(2026, 3, 25, tzinfo=timezone.utc),
                observed_timestamp=datetime(2026, 3, 25, tzinfo=timezone.utc),
                event_name="stage.completed",
                source="engine",
                plane=SimpleNamespace(value="execution"),
                node_id="builder",
                kind_id="builder",
                outcome="success",
                status_before="IDLE",
                status_after="BUILDER_COMPLETE",
                active_task_before=None,
                active_task_after="task-1",
                routing_mode="small",
                queue_mutations_applied=("promoted",),
                artifacts_emitted=("artifact.md",),
            )
            return SimpleNamespace(
                run_id=run_id,
                selection=selection,
                selection_explanation=explanation,
                current_preview=current_preview,
                current_preview_explanation=explanation,
                current_preview_error=None,
                routing_modes=("small",),
                snapshot_path=Path("/tmp/run-123/snapshot.json"),
                transition_history_path=Path("/tmp/run-123/transitions.json"),
                policy_hooks=SimpleNamespace(record_count=2, latest_decision="PASS"),
                latest_policy_evidence=SimpleNamespace(
                    hook="pre_stage",
                    evaluator="execution_integration_policy",
                    decision="PASS",
                    timestamp=datetime(2026, 3, 25, tzinfo=timezone.utc),
                    event_name="stage.completed",
                    node_id="builder",
                    routing_mode="small",
                    notes=("builder success path is allowed",),
                    evidence=(SimpleNamespace(summary="task gate did not require integration"),),
                ),
                integration_policy=SimpleNamespace(
                    effective_mode="large_only",
                    builder_success_target="qa",
                    should_run_integration=False,
                    task_gate_required=False,
                    task_integration_preference="inherit",
                    requested_sequence=("builder", "qa"),
                    effective_sequence=("builder", "qa"),
                    available_execution_nodes=("builder", "qa"),
                    reason="Builder routes to qa.",
                ),
                compile_snapshot=SimpleNamespace(
                    created_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
                    frozen_plan=SimpleNamespace(plan_id="frozen-plan:hash-123", content_hash="hash-123"),
                    content=SimpleNamespace(execution_plan=SimpleNamespace(stages=("builder", "qa"))),
                ),
                runtime_history=(record,),
            )

    monkeypatch.setattr(gateway_module, "EngineControl", FakeControl)
    gateway = RuntimeGateway(tmp_path / "millrace.toml")

    result = gateway.load_run_detail("run-123")

    assert result.ok
    payload = result.value
    assert payload is not None
    assert payload.run_detail is not None
    assert payload.run_detail.run_id == "run-123"
    assert payload.run_detail.frozen_plan_id == "frozen-plan:hash-123"
    assert payload.run_detail.frozen_plan_hash == "hash-123"
    assert payload.run_detail.stage_count == 2
    assert payload.run_detail.selection is not None
    assert payload.run_detail.selection.selection_ref == "mode:mode.standard@1.0.0"
    assert payload.run_detail.current_preview is not None
    assert payload.run_detail.policy_hook_count == 2
    assert payload.run_detail.latest_policy_evidence is not None
    assert payload.run_detail.latest_policy_evidence.evaluator == "execution_integration_policy"
    assert payload.run_detail.integration_policy is not None
    assert payload.run_detail.integration_policy.builder_success_target == "qa"
    assert len(payload.run_detail.transitions) == 1
    assert payload.run_detail.transitions[0].routing_mode == "small"


def test_runtime_gateway_normalizes_input_and_control_failures(tmp_path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    gateway = RuntimeGateway(config_path)

    blank_title = gateway.add_task("   ")
    assert blank_title.failure == GatewayFailure(
        operation="action.add_task",
        category=FailureCategory.INPUT,
        message="task title is required",
        exception_type="ValueError",
        retryable=False,
    )

    missing_run = gateway.load_run_detail("missing-run")
    assert missing_run.failure is not None
    assert missing_run.failure.category == FailureCategory.CONTROL
    assert missing_run.failure.message == "run provenance not found: missing-run"
    assert missing_run.failure.exception_type == "ControlError"


def test_runtime_gateway_support_normalizes_optional_text_and_resolves_paths(tmp_path) -> None:
    config_path = tmp_path / "workspace" / "millrace.toml"
    assert gateway_support_module.normalized_optional_text("  hello   there  ") == "hello there"
    assert gateway_support_module.normalized_optional_text("   ") is None
    assert gateway_support_module.resolve_config_path(config_path) == config_path.resolve()


def test_runtime_gateway_commit_result_reports_missing_origin_downgrade(tmp_path) -> None:
    gateway = RuntimeGateway(tmp_path / "millrace.toml")

    result = gateway._commit_result(
        SimpleNamespace(
            status="committed",
            push_requested=True,
            push_performed=False,
            skip_reason="missing_origin",
            marker="SKIP_PUBLISH reason=missing_origin path=/tmp/staging",
            branch="main",
            commit_sha="abc123",
        )
    )

    assert result.message == "publish committed locally; push skipped: origin is missing"
    assert result.applied is True


def test_runtime_gateway_commit_result_reports_detached_head_downgrade(tmp_path) -> None:
    gateway = RuntimeGateway(tmp_path / "millrace.toml")

    result = gateway._commit_result(
        SimpleNamespace(
            status="committed",
            push_requested=True,
            push_performed=False,
            skip_reason="detached_head",
            marker="SKIP_PUBLISH reason=detached_head path=/tmp/staging",
            branch=None,
            commit_sha="abc123",
        )
    )

    assert result.message == "publish committed locally; push skipped: HEAD is detached"
    assert result.applied is True


def test_tui_shell_worker_state_outcome_maps_initial_refresh_success() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    payload = _sample_refresh_payload(observed_at=observed_at)

    outcome = shell_support_module.worker_state_outcome(
        worker_name=workers_module.INITIAL_REFRESH_WORKER_NAME,
        state=WorkerState.SUCCESS,
        result=GatewayResult(value=payload),
        error=None,
    )

    assert outcome is not None
    assert outcome.ensure_background_runtime is True
    assert isinstance(outcome.message, RefreshSucceeded)
    assert outcome.message.panels == shell_support_module.INITIAL_REFRESH_PANELS


def test_tui_shell_publish_confirmation_lines_reflect_push_readiness() -> None:
    lines = shell_support_module.publish_confirmation_lines(_sample_publish_overview(), push=True)

    assert "Push ready from current facts: yes" in lines
    assert lines[-1] == "Push stays intentionally higher friction than the default local-commit path."


def test_run_detail_modal_renders_loaded_provenance(monkeypatch, tmp_path) -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    detail = _sample_run_detail(run_id="run-modal-1", observed_at=observed_at)

    class FakeGateway:
        def __init__(self, config_path: Path) -> None:
            self.config_path = config_path

        def load_run_detail(self, run_id: str):
            return GatewayResult(value=RefreshPayload(refreshed_at=observed_at, run_detail=detail))

    monkeypatch.setattr(run_detail_modal_module, "RuntimeGateway", FakeGateway)

    modal = RunDetailModal(config_path=tmp_path / "millrace.toml", run_id="run-modal-1")

    async def scenario(app: App[None], pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, RunDetailModal)
            and "run-modal-1" in _static_text(app.screen.query_one("#run-detail-body", Static)),
        )
        body = _static_text(app.screen.query_one("#run-detail-body", Static))
        assert "PLAN    frozen-plan:run-modal-1" in body
        assert "HASH    run-modal-1-hash" in body
        assert "POLICY  records 2 | latest PASS | hook pre_stage | evaluator execution_integration_policy" in body
        assert "INTEGRATION mode large_only | target qa | run no" in body
        assert "TRACE   /tmp/run-modal-1/transition_history.jsonl" in body

    _run_modal_scenario(modal, scenario)


def test_run_detail_modal_surfaces_failure_without_crashing(monkeypatch, tmp_path) -> None:
    class FakeGateway:
        def __init__(self, config_path: Path) -> None:
            self.config_path = config_path

        def load_run_detail(self, run_id: str):
            return GatewayResult(
                failure=GatewayFailure(
                    operation="refresh.run_detail",
                    category=FailureCategory.CONTROL,
                    message="run provenance not found: missing-run",
                    exception_type="ControlError",
                )
            )

    monkeypatch.setattr(run_detail_modal_module, "RuntimeGateway", FakeGateway)

    modal = RunDetailModal(config_path=tmp_path / "millrace.toml", run_id="missing-run")

    async def scenario(app: App[None], pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, RunDetailModal)
            and "run provenance not found: missing-run" in _static_text(app.screen.query_one("#run-detail-body", Static)),
        )
        body = _static_text(app.screen.query_one("#run-detail-body", Static))
        assert "RUN DETAIL unavailable: run provenance not found: missing-run" in body
        assert "Requested run id: missing-run" in body

    _run_modal_scenario(modal, scenario)


def test_tui_store_reducers_merge_updates_atomically() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    refresh_payload = _sample_refresh_payload(observed_at=observed_at)
    publish_payload = RefreshPayload(
        refreshed_at=observed_at,
        publish=PublishOverviewView(
            staging_repo_dir="/tmp/workspace/staging",
            manifest_source_kind="workspace",
            manifest_source_ref="agents/staging_manifest.yml",
            manifest_version=1,
            selected_paths=(
                "agents",
                "README.md",
                "ADVISOR.md",
                "OPERATOR_GUIDE.md",
                "docs/RUNTIME_DEEP_DIVE.md",
            ),
            branch=None,
            commit_message="Millrace staging sync",
            push_requested=False,
            git_worktree_present=False,
            git_worktree_valid=False,
            origin_configured=False,
            has_changes=False,
            changed_paths=(),
            commit_allowed=False,
            publish_allowed=False,
            status="skip_publish",
            skip_reason="staging repo is not a git worktree",
        ),
    )
    failure = GatewayFailure(
        operation="refresh.workspace",
        category=FailureCategory.CONTROL,
        message="workspace refresh failed",
        exception_type="ControlError",
    )
    action = ActionResultView(
        action="pause",
        message="pause queued",
        applied=True,
        mode="mailbox",
    )
    event1 = RuntimeEventView(
        event_type="engine.started",
        source="engine",
        timestamp=observed_at,
        is_research_event=False,
    )
    event2 = RuntimeEventView(
        event_type="engine.stopped",
        source="engine",
        timestamp=observed_at,
        is_research_event=False,
    )
    event3 = RuntimeEventView(
        event_type="research.received",
        source="research",
        timestamp=observed_at,
        is_research_event=True,
    )

    store = TUIStore(event_limit=2)

    state = store.apply_refresh_success(refresh_payload, panels=(PanelId.OVERVIEW, PanelId.QUEUE))
    assert state.runtime == refresh_payload.runtime
    assert state.config == refresh_payload.config
    assert state.queue == refresh_payload.queue
    assert state.publish is None

    state = store.apply_refresh_success(publish_payload, panels=(PanelId.PUBLISH,))
    assert state.runtime == refresh_payload.runtime
    assert state.config == refresh_payload.config
    assert state.publish == publish_payload.publish

    state = store.append_events((event1, event2, event3), received_at=observed_at, clear_panels=(PanelId.LOGS,))
    assert state.events is not None
    assert [event.event_type for event in state.events.events] == ["engine.stopped", "research.received"]

    state = store.apply_action_success(action, notice=notice_from_action(action, created_at=observed_at))
    assert state.last_action == action
    assert state.last_action_failure is None
    assert state.notices[-1].level == NoticeLevel.SUCCESS

    state = store.apply_refresh_failure(
        failure,
        panels=(PanelId.QUEUE, PanelId.CONFIG),
        notice=notice_from_failure(failure, created_at=observed_at),
    )
    assert state.runtime == refresh_payload.runtime
    assert state.config == refresh_payload.config
    assert state.last_refresh_failure == failure
    assert state.notices[-1].message == failure.message
    assert store.panel_failure(PanelId.QUEUE) == failure
    assert store.panel_failure(PanelId.CONFIG) == failure

    state = store.apply_panel_failure(
        failure,
        panels=(PanelId.LOGS,),
        notice=notice_from_failure(failure, created_at=observed_at),
    )
    assert store.panel_failure(PanelId.LOGS) == failure
    assert len(state.notices) == 2


def test_tui_store_append_events_suppresses_exact_duplicates() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    event = _sample_runtime_event(
        event_type="execution.stage.completed",
        source="execution",
        observed_at=observed_at,
        category="EXE",
        summary="stage=builder | status=success",
        run_id="run-1",
        is_research_event=False,
        payload=(KeyValueView("stage", "builder"), KeyValueView("status", "success")),
    )
    store = TUIStore(event_limit=10)

    state = store.append_events((event,), received_at=observed_at)
    state = store.append_events((event,), received_at=observed_at)

    assert state.events is not None
    assert state.events.events == (event,)


def test_tui_store_append_events_retains_distinct_payload_variants() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    first = _sample_runtime_event(
        event_type="execution.stage.completed",
        source="execution",
        observed_at=observed_at,
        category="EXE",
        summary="stage=builder | status=success",
        run_id="run-1",
        is_research_event=False,
        payload=(KeyValueView("stage", "builder"), KeyValueView("status", "success")),
    )
    second = _sample_runtime_event(
        event_type="execution.stage.completed",
        source="execution",
        observed_at=observed_at,
        category="EXE",
        summary="stage=builder | status=success",
        run_id="run-1",
        is_research_event=False,
        payload=(KeyValueView("stage", "qa"), KeyValueView("status", "success")),
    )
    store = TUIStore(event_limit=10)

    state = store.append_events((first, second), received_at=observed_at)

    assert state.events is not None
    assert state.events.events == (first, second)


def test_tui_shell_queue_panel_navigation_preserves_selection_across_panel_switches(tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    _write_backlog(
        workspace,
        [
            ("2026-03-19", "First queued task", "SPEC-001"),
            ("2026-03-20", "Second queued task", "SPEC-002"),
        ],
    )

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.queue is not None
            and app.screen._store.state.queue.backlog_depth == 2,
        )
        await pilot.press("2")
        await pilot.pause()
        panel = app.screen.query_one("#panel-queue", QueuePanel)
        assert panel.selected_task_id == "2026-03-19__first-queued-task"

        await pilot.press("c")
        await pilot.pause()
        assert app.screen.focused is not None
        assert app.screen.focused.id == "panel-queue"

        await pilot.press("down")
        await pilot.pause()
        assert panel.selected_task_id == "2026-03-20__second-queued-task"
        assert "FOCUS   Second queued task" in panel.summary_text()

        await pilot.press("1")
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        assert panel.selected_task_id == "2026-03-20__second-queued-task"

    _run_app_scenario(config_path, scenario)


def test_tui_shell_add_task_modal_validates_locally_and_refreshes_queue(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.queue is not None,
        )

        await pilot.press("t")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, AddTaskModal))
        assert isinstance(app.screen, AddTaskModal)

        await pilot.click("#add-task-submit")
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, AddTaskModal)
            and "Task title is required." in _static_text(app.screen.query_one("#add-task-error", Static)),
        )

        modal = app.screen
        modal.query_one("#add-task-title", Input).value = "Operator-authored TUI task"
        modal.query_one("#add-task-spec-id", Input).value = "SPEC-TUI-ADD-TASK"
        modal.query_one("#add-task-body", TextArea).load_text("Follow the queued operator workflow.")
        await pilot.pause()
        modal.action_submit()
        await pilot.pause()

        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.queue is not None
            and any(task.title == "Operator-authored TUI task" for task in app.screen._store.state.queue.backlog),
        )
        assert isinstance(app.screen, ShellScreen)
        assert app.screen._store.state.last_action is not None
        assert app.screen._store.state.last_action.message == "task added"
        assert app.screen._store.state.notices[-1].message == "task added"
        backlog_cards = parse_task_cards((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))
        assert [card.title for card in backlog_cards] == ["Operator-authored TUI task"]
        assert backlog_cards[0].spec_id == "SPEC-TUI-ADD-TASK"

    _run_app_scenario(config_path, scenario)


def test_tui_shell_add_idea_modal_validates_locally_and_copies_source(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    source_path = workspace / "ideas" / "candidate.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Candidate idea\n", encoding="utf-8")
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.queue is not None,
        )

        await pilot.press("i")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, AddIdeaModal))
        assert isinstance(app.screen, AddIdeaModal)

        await pilot.click("#add-idea-submit")
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, AddIdeaModal)
            and "Idea source path is required."
            in _static_text(app.screen.query_one("#add-idea-error", Static)),
        )

        modal = app.screen
        modal.query_one("#add-idea-path", Input).value = "ideas/missing.md"
        await pilot.pause()
        modal.action_submit()
        await pilot.pause()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, AddIdeaModal)
            and "does not exist:" in _static_text(app.screen.query_one("#add-idea-error", Static)),
        )

        modal.query_one("#add-idea-path", Input).value = "ideas/candidate.md"
        await pilot.pause()
        modal.action_submit()
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ShellScreen))
        raw_dir = workspace / "agents" / "ideas" / "raw"
        await _wait_for_condition(
            pilot,
            lambda: app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "idea queued",
        )
        assert isinstance(app.screen, ShellScreen)
        assert app.screen._store.state.last_action is not None
        assert app.screen._store.state.last_action.message == "idea queued"
        assert app.screen._store.state.notices[-1].message == "idea queued"
        assert any(path.name.endswith("__candidate.md") for path in raw_dir.iterdir())

    _run_app_scenario(config_path, scenario)


def test_tui_shell_research_panel_resolves_pending_interview_from_modal(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    question_id = seed_pending_interview_question(workspace)
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.research is not None
            and app.screen._store.state.research.interview_questions,
        )

        await pilot.press("4")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, ShellScreen)
        research_panel = app.screen.query_one("#panel-research", ResearchPanel)
        assert research_panel.summary_text().count("pending") >= 1

        await pilot.press("enter")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, InterviewModal))
        assert isinstance(app.screen, InterviewModal)
        app.screen.query_one("#interview-answer-text", TextArea).load_text(
            "Keep the confirmation path explicit so queue mutations stay governed."
        )
        await pilot.pause()
        app.screen.action_submit_answer()

        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "interview answer recorded",
        )
        assert isinstance(app.screen, ShellScreen)
        assert app.screen._store.state.research is not None
        assert all(
            question.status != "pending" for question in app.screen._store.state.research.interview_questions
        )
        assert app.screen._store.state.notices[-1].message == "interview answer recorded"
        decision_path = workspace / "agents" / "specs" / "decisions" / f"{question_id}__decision.json"
        assert decision_path.exists()

    _run_app_scenario(config_path, scenario)


def test_tui_shell_mailbox_add_task_notice_stays_truthful(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    _write_runtime_state_snapshot(workspace, process_running=True, backlog_depth=0)
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.runtime is not None
            and app.screen._store.state.runtime.process_running,
        )

        await pilot.press("t")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, AddTaskModal))
        assert isinstance(app.screen, AddTaskModal)

        modal = app.screen
        modal.query_one("#add-task-title", Input).value = "Mailbox queued task"
        await pilot.pause()
        modal.action_submit()
        await pilot.pause()

        incoming_dir = workspace / "agents" / ".runtime" / "commands" / "incoming"
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and any(incoming_dir.glob("*.json")),
        )
        assert isinstance(app.screen, ShellScreen)
        assert app.screen._store.state.last_action is not None
        assert app.screen._store.state.last_action.mode == "mailbox"
        assert app.screen._store.state.last_action.message == "add_task queued"
        assert app.screen._store.state.notices[-1].message.startswith("add_task queued (command ")
        assert parse_task_cards((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8")) == []

    _run_app_scenario(config_path, scenario)


def test_tui_shell_lifecycle_busy_state_blocks_overlapping_launches(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    calls = {"once": 0, "daemon": 0}
    once_started = asyncio.Event()
    allow_once_finish = asyncio.Event()

    async def fake_launch_start_once(config_path: Path):
        calls["once"] += 1
        once_started.set()
        await allow_once_finish.wait()
        return GatewayResult(
            value=ActionResultView(
                action="start.once",
                message="once run completed",
                applied=True,
                mode="foreground",
            )
        )

    async def fake_launch_start_daemon(config_path: Path):
        calls["daemon"] += 1
        return GatewayResult(
            value=ActionResultView(
                action="start.daemon",
                message="daemon launched",
                applied=True,
                mode="detached",
            )
        )

    monkeypatch.setattr(shell_module, "launch_start_once", fake_launch_start_once)
    monkeypatch.setattr(shell_module, "launch_start_daemon", fake_launch_start_daemon)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.runtime is not None,
        )

        app.action_start_once()
        await once_started.wait()
        await _wait_for_condition(
            pilot,
            lambda: "busy foregroun" in _static_text(app.screen.query_one(StatusBar)),
        )

        app.action_start_daemon()
        await pilot.pause()
        assert calls["once"] == 1
        assert calls["daemon"] == 0

        allow_once_finish.set()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "once run completed",
        )
        assert "busy foregroun" not in _static_text(app.screen.query_one(StatusBar))
        assert app.screen._store.state.notices[-1].message == "once run completed"

    _run_app_scenario(config_path, scenario)


def test_tui_shell_prompts_to_launch_daemon_on_startup_when_runtime_is_idle(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    calls = {"daemon": 0}

    async def fake_launch_start_daemon(config_path: Path):
        calls["daemon"] += 1
        return GatewayResult(
            value=ActionResultView(
                action="start.daemon",
                message="daemon launched",
                applied=True,
                mode="detached",
            )
        )

    monkeypatch.setattr(shell_module, "launch_start_daemon", fake_launch_start_daemon)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ConfirmModal))
        assert isinstance(app.screen, ConfirmModal)
        confirm_text = _static_text(app.screen.query_one(".modal-copy", Static))
        assert "runtime is not currently running" in confirm_text.lower()
        assert "Start Once" in confirm_text

        app.screen.action_confirm()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "daemon launched",
        )
        assert calls["daemon"] == 1
        assert app.screen._store.state.notices[-1].message == "daemon launched"

    _run_app_scenario(
        config_path,
        scenario,
        offer_startup_daemon_launch=True,
    )


def test_tui_shell_skips_startup_daemon_prompt_when_runtime_already_running(tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    _write_runtime_state_snapshot(workspace, process_running=True, backlog_depth=0)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.runtime is not None,
        )
        for _ in range(5):
            await pilot.pause()
        assert isinstance(app.screen, ShellScreen)
        assert app.screen._store.state.runtime is not None
        assert app.screen._store.state.runtime.process_running is True

    _run_app_scenario(
        config_path,
        scenario,
        offer_startup_daemon_launch=True,
    )


def test_tui_shell_lifecycle_controls_route_through_gateway(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    calls: list[str] = []

    class FakeGateway:
        def __init__(self, config_path: Path) -> None:
            self.config_path = config_path

        def pause_runtime(self):
            calls.append("pause")
            return GatewayResult(
                value=ActionResultView(
                    action="pause",
                    message="pause queued",
                    applied=True,
                    mode="mailbox",
                    command_id="cmd-pause",
                )
            )

        def resume_runtime(self):
            calls.append("resume")
            return GatewayResult(
                value=ActionResultView(
                    action="resume",
                    message="resume queued",
                    applied=True,
                    mode="mailbox",
                    command_id="cmd-resume",
                )
            )

        def stop_runtime(self):
            calls.append("stop")
            return GatewayResult(
                value=ActionResultView(
                    action="stop",
                    message="stop queued",
                    applied=True,
                    mode="mailbox",
                    command_id="cmd-stop",
                )
            )

    monkeypatch.setattr(shell_module, "RuntimeGateway", FakeGateway)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.runtime is not None,
        )

        app.action_pause_runtime()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "pause queued",
        )
        assert calls == ["pause"]
        assert app.screen._store.state.notices[-1].message == "pause queued (command cmd-pause)"

        app.action_resume_runtime()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "resume queued",
        )
        assert calls == ["pause", "resume"]
        assert app.screen._store.state.notices[-1].message == "resume queued (command cmd-resume)"

        app.action_stop_runtime()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "stop queued",
        )
        assert calls == ["pause", "resume", "stop"]
        assert app.screen._store.state.notices[-1].message == "stop queued (command cmd-stop)"

    _run_app_scenario(config_path, scenario)


def test_tui_shell_queue_reorder_uses_draft_and_confirmation(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    _write_backlog(
        workspace,
        [
            ("2026-03-19", "First queued task", "SPEC-001"),
            ("2026-03-20", "Second queued task", "SPEC-002"),
        ],
    )
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.queue is not None
            and app.screen._store.state.queue.backlog_depth == 2,
        )
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("2")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()

        panel = app.screen.query_one("#panel-queue", QueuePanel)
        assert panel.reorder_mode is False
        await pilot.press("]")
        await pilot.pause()
        assert panel.reorder_mode is False
        assert [card.title for card in parse_task_cards((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))] == [
            "First queued task",
            "Second queued task",
        ]

        await pilot.press("r")
        await pilot.pause()
        assert panel.reorder_mode is True
        assert "DRAFT   no position changes yet" in panel.summary_text()

        await pilot.press("]")
        await pilot.pause()
        assert panel._reorder_task_ids == (
            "2026-03-20__second-queued-task",
            "2026-03-19__first-queued-task",
        )
        assert [card.title for card in parse_task_cards((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))] == [
            "First queued task",
            "Second queued task",
        ]

        await pilot.press("enter")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ConfirmModal))
        assert isinstance(app.screen, ConfirmModal)
        await pilot.click("#confirm-submit")

        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.queue is not None
            and [task.title for task in app.screen._store.state.queue.backlog]
            == ["Second queued task", "First queued task"],
        )
        assert isinstance(app.screen, ShellScreen)
        panel = app.screen.query_one("#panel-queue", QueuePanel)
        assert panel.reorder_mode is False
        assert app.screen._store.state.notices[-1].message == "queue reordered"
        assert [card.title for card in parse_task_cards((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))] == [
            "Second queued task",
            "First queued task",
        ]

    _run_app_scenario(config_path, scenario)


def test_tui_shell_config_panel_renders_supported_fields_and_applies_direct_edit(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.config is not None
            and app.screen._store.state.runtime is not None,
        )
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("6")
        await pilot.pause()
        assert app.screen.active_panel == PanelId.CONFIG

        panel = app.screen.query_one("#panel-config", ConfigPanel)
        panel_text = _panel_text(panel)
        assert "EDITABLE" in panel_text
        assert "Poll interval = 1 [LIVE]" in panel_text
        assert "BOUNDARY [LIVE] now" in panel_text
        assert "NEXT    Up/Down select field | Enter/E edit selected | R reload config from disk" in panel_text
        assert _static_text(panel.query_one("#config-fields-headline", Static)) == "5 guided fields"
        assert "selected field opens the controlled edit modal" in _static_text(
            panel.query_one("#config-fields-detail", Static)
        )

        await pilot.press("c")
        await pilot.pause()
        assert app.screen.focused is not None
        assert app.screen.focused.id == "panel-config"
        assert panel.selected_field_key == "engine.poll_interval_seconds"

        await pilot.press("enter")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ConfigEditModal))
        assert isinstance(app.screen, ConfigEditModal)
        app.screen.query_one("#config-edit-value", Input).value = "7"
        await pilot.pause()
        app.screen.action_submit()

        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "config updated",
        )
        assert isinstance(app.screen, ShellScreen)
        assert "poll_interval_seconds = 7" in config_path.read_text(encoding="utf-8")
        await _wait_for_condition(
            pilot,
            lambda: app.screen._store.state.config is not None
            and any(
                field.key == "engine.poll_interval_seconds" and field.value == "7"
                for field in app.screen._store.state.config.fields
            ),
        )
        assert app.screen._store.state.notices[-1].message == "config updated"

        app.screen.action_reload_config()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "config reloaded",
        )
        assert app.screen._store.state.notices[-1].message == "config reloaded"

    _run_app_scenario(config_path, scenario)


def test_tui_shell_mailbox_config_edit_and_reload_stay_truthful(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    _write_runtime_state_snapshot(workspace, process_running=True, backlog_depth=0)
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.runtime is not None
            and app.screen._store.state.runtime.process_running,
        )
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("6")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("enter")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ConfigEditModal))
        assert isinstance(app.screen, ConfigEditModal)
        app.screen.query_one("#config-edit-value", Input).value = "9"
        await pilot.pause()
        app.screen.action_submit()

        incoming_dir = workspace / "agents" / ".runtime" / "commands" / "incoming"
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "set_config queued"
            and any(incoming_dir.glob("*.json")),
        )
        assert isinstance(app.screen, ShellScreen)
        assert app.screen._store.state.last_action is not None
        assert app.screen._store.state.last_action.mode == "mailbox"
        assert "poll_interval_seconds = 1" in config_path.read_text(encoding="utf-8")

        app.screen.action_reload_config()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "reload_config queued",
        )
        assert app.screen._store.state.last_action is not None
        assert app.screen._store.state.last_action.mode == "mailbox"
        assert len(list(incoming_dir.glob("*.json"))) >= 2

    _run_app_scenario(config_path, scenario)


def test_tui_shell_publish_panel_renders_skip_state_and_preflight_details(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.runtime is not None,
        )
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("7")
        await pilot.pause()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.publish is not None,
        )
        panel = app.screen.query_one("#panel-publish", PublishPanel)
        text = _panel_text(panel)
        assert "STATUS  blocked | staging repo is missing a git worktree | changed 0" in text
        assert "HEALTH  worktree no | valid no | origin no" in text
        assert "ALERT   publish is blocked until staging health is fixed" in text
        assert "NEXT    G sync staging, then R refresh preflight to re-check readiness" in text
        assert "blocked | staging repo is missing a git worktree" in _static_text(
            panel.query_one("#publish-status-headline", Static)
        )
        assert "worktree no | valid no | origin no" in _static_text(panel.query_one("#publish-health-headline", Static))
        assert app.screen._store.state.publish is not None
        assert app.screen._store.state.publish.staging_repo_dir.endswith("/staging")

    _run_app_scenario(config_path, scenario)


def test_tui_shell_publish_actions_use_confirmation_and_surface_degraded_push_message(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    commit_calls: list[bool] = []

    class FakeGateway:
        def __init__(self, config_path: Path) -> None:
            self.config_path = config_path

        def load_publish_status(self):
            return GatewayResult(
                value=RefreshPayload(
                    refreshed_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
                    publish=_sample_publish_overview(),
                )
            )

        def publish_sync(self):
            return GatewayResult(
                value=ActionResultView(
                    action="publish_sync",
                    message="staging synchronized",
                    applied=True,
                )
            )

        def publish_commit(self, *, push: bool = False):
            commit_calls.append(push)
            return GatewayResult(
                value=ActionResultView(
                    action="publish_commit",
                    message=(
                        "publish committed locally; push skipped: origin is missing"
                        if push
                        else "publish committed"
                    ),
                    applied=True,
                )
            )

    monkeypatch.setattr(shell_module, "RuntimeGateway", FakeGateway)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.runtime is not None,
        )
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("7")
        await pilot.pause()
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.publish is not None,
        )
        await pilot.press("c")
        await pilot.pause()
        assert app.screen.focused is not None
        assert app.screen.focused.id == "panel-publish"

        await pilot.press("g")
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "staging synchronized",
        )
        assert isinstance(app.screen, ShellScreen)

        await pilot.press("n")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ConfirmModal))
        assert isinstance(app.screen, ConfirmModal)
        confirm_text = _static_text(app.screen.query_one(".modal-copy", Static))
        assert "Create a local staging commit without pushing?" in confirm_text
        await pilot.click("#confirm-submit")

        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message == "publish committed",
        )
        assert commit_calls[-1] is False

        await pilot.press("c")
        await pilot.pause()
        await pilot.press("p")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ConfirmModal))
        assert isinstance(app.screen, ConfirmModal)
        push_text = _static_text(app.screen.query_one(".modal-copy", Static))
        assert "higher friction" in push_text
        await pilot.click("#confirm-submit")

        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and app.screen._store.state.last_action is not None
            and app.screen._store.state.last_action.message
            == "publish committed locally; push skipped: origin is missing",
        )
        assert commit_calls[-1] is True

    _run_app_scenario(config_path, scenario)


def test_tui_shell_research_and_logs_panels_render_and_logs_handoff_to_runs(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)

    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    class FakeGateway:
        def __init__(self, config_path: Path) -> None:
            self.config_path = config_path

        def load_run_detail(self, run_id: str):
            return GatewayResult(
                value=RefreshPayload(
                    refreshed_at=observed_at,
                    run_detail=_sample_run_detail(run_id=run_id, observed_at=observed_at),
                )
            )

    monkeypatch.setattr(run_detail_modal_module, "RuntimeGateway", FakeGateway)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.research is not None,
        )
        assert isinstance(app.screen, ShellScreen)
        research_panel = app.screen.query_one("#panel-research", ResearchPanel)
        logs_panel = app.screen.query_one("#panel-logs", LogsPanel)
        assert "STATE" in research_panel.summary_text()
        assert "Waiting for the event stream." not in _panel_text(logs_panel)

        app.screen.post_message(
            EventsAppended(
                (
                    _sample_runtime_event(
                        event_type="execution.stage.completed",
                        source="execution",
                        observed_at=observed_at,
                        category="EXE",
                        summary="stage=builder | status=success",
                        run_id="run-log-1",
                        is_research_event=False,
                    ),
                ),
                received_at=observed_at,
            )
        )
        await _wait_for_condition(
            pilot,
            lambda: "run run-log-1" in _panel_text(app.screen.query_one("#panel-logs", LogsPanel)),
        )

        await pilot.press("5")
        await pilot.pause()
        assert app.screen.active_panel == PanelId.LOGS

        await pilot.press("c")
        await pilot.pause()
        assert app.screen.focused is not None
        assert app.screen.focused.id == "panel-logs"

        await pilot.press("enter")
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, RunDetailModal)
            and "run-log-1" in _static_text(app.screen.query_one("#run-detail-body", Static)),
        )
        assert isinstance(app.screen, RunDetailModal)
        modal_text = _static_text(app.screen.query_one("#run-detail-body", Static))
        assert "PLAN    frozen-plan:run-log-1" in modal_text
        assert "POLICY  records 2 | latest PASS" in modal_text

        await pilot.press("escape")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ShellScreen) and app.screen.active_panel == PanelId.RUNS)
        assert isinstance(app.screen, ShellScreen)
        runs_panel = app.screen.query_one("#panel-runs", RunsPanel)
        runs_text = runs_panel.summary_text()
        assert "requested run run-log-1 is not in the current recent-runs list" in runs_text

    _run_app_scenario(config_path, scenario)


def test_tui_shell_queue_panel_opens_run_detail_modal(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)

    monkeypatch.setattr(
        shell_module,
        "load_workspace_refresh",
        lambda *args, **kwargs: GatewayResult(
            value=_sample_refresh_payload(
                observed_at=observed_at,
                selection_run_id="run-queue-1",
                runs=_sample_runs_overview(
                    observed_at=observed_at,
                    runs=(_sample_run_summary(run_id="run-queue-1", observed_at=observed_at),),
                ),
            )
        ),
    )
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    class FakeGateway:
        def __init__(self, config_path: Path) -> None:
            self.config_path = config_path

        def load_run_detail(self, run_id: str):
            return GatewayResult(
                value=RefreshPayload(
                    refreshed_at=observed_at,
                    run_detail=_sample_run_detail(run_id=run_id, observed_at=observed_at),
                )
            )

    monkeypatch.setattr(run_detail_modal_module, "RuntimeGateway", FakeGateway)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.runtime is not None,
        )

        await pilot.press("2")
        await pilot.pause()
        assert isinstance(app.screen, ShellScreen)
        assert app.screen.active_panel == PanelId.QUEUE

        await pilot.press("c")
        await pilot.pause()
        assert app.screen.focused is not None
        assert app.screen.focused.id == "panel-queue"

        await pilot.press("o")
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, RunDetailModal)
            and "run-queue-1" in _static_text(app.screen.query_one("#run-detail-body", Static)),
        )
        assert isinstance(app.screen, RunDetailModal)
        assert "PLAN    frozen-plan:run-queue-1" in _static_text(app.screen.query_one("#run-detail-body", Static))

        await pilot.press("escape")
        await _wait_for_condition(pilot, lambda: isinstance(app.screen, ShellScreen) and app.screen.active_panel == PanelId.RUNS)
        assert isinstance(app.screen, ShellScreen)
        runs_text = _panel_text(app.screen.query_one("#panel-runs", RunsPanel))
        assert "run-queue-1" in runs_text

    _run_app_scenario(config_path, scenario)


def test_tui_messages_wrap_shaped_payloads() -> None:
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    payload = _sample_refresh_payload(observed_at=observed_at)
    failure = GatewayFailure(
        operation="action.pause",
        category=FailureCategory.CONTROL,
        message="engine is not running",
        exception_type="ControlError",
    )
    result = ActionResultView(action="pause", message="pause queued", applied=True, mode="mailbox")
    event = RuntimeEventView(
        event_type="engine.started",
        source="engine",
        timestamp=observed_at,
        is_research_event=False,
    )

    refresh_success = RefreshSucceeded(payload, panels=(PanelId.OVERVIEW,))
    refresh_failure = RefreshFailed(failure, panels=(PanelId.LOGS,))
    action_success = ActionSucceeded(result)
    action_failure = ActionFailed(failure)
    events_appended = EventsAppended((event,), received_at=observed_at)
    event_stream_failed = EventStreamFailed(failure)
    report = SimpleNamespace(ok=True)
    health_success = HealthCheckCompleted(report)  # type: ignore[arg-type]
    health_failure = HealthCheckFailed(failure)

    assert refresh_success.payload == payload
    assert refresh_success.panels == (PanelId.OVERVIEW,)
    assert refresh_failure.failure == failure
    assert refresh_failure.panels == (PanelId.LOGS,)
    assert action_success.result == result
    assert action_failure.failure == failure
    assert events_appended.events == (event,)
    assert events_appended.received_at == observed_at
    assert event_stream_failed.failure == failure
    assert health_success.report == report
    assert health_failure.failure == failure


def test_tui_shell_refresh_failure_keeps_last_good_snapshot(monkeypatch, tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    observed_at = datetime(2026, 3, 25, tzinfo=timezone.utc)
    payload = _sample_refresh_payload(observed_at=observed_at)
    failure = GatewayFailure(
        operation="refresh.workspace",
        category=FailureCategory.CONTROL,
        message="snapshot failed",
        exception_type="ControlError",
    )
    refresh_calls = {"count": 0}

    def fake_load_workspace_refresh(config_path: Path, *, include_events: bool, settings: WorkerSettings):
        refresh_calls["count"] += 1
        if refresh_calls["count"] == 1:
            return GatewayResult(value=payload)
        return GatewayResult(failure=failure)

    monkeypatch.setattr(shell_module, "load_workspace_refresh", fake_load_workspace_refresh)
    monkeypatch.setattr(shell_module, "stream_event_updates", lambda *args, **kwargs: None)

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.runtime is not None,
        )
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen) and app.screen._store.state.last_refresh_failure is not None,
        )
        assert isinstance(app.screen, ShellScreen)
        assert app.screen._store.state.runtime == payload.runtime
        assert app.screen._store.state.notices[-1].message == failure.message
        assert app.screen._store.panel_failure(PanelId.CONFIG) == failure
        queue_text = _panel_text(app.screen.query_one("#panel-queue", QueuePanel))
        assert "QUEUE stale: snapshot failed" in queue_text
        assert "Example task" in queue_text

        await pilot.press("2")
        await pilot.pause()
        assert app.screen.active_panel == PanelId.QUEUE

    _run_app_scenario(
        config_path,
        scenario,
        worker_settings=WorkerSettings(refresh_interval_seconds=0.01, event_retry_delay_seconds=0.01),
    )


def test_worker_event_stream_coalesces_bounded_batches(monkeypatch, tmp_path) -> None:
    cancel_event = Event()

    class FakeWorker:
        def __init__(self) -> None:
            self.cancelled_event = cancel_event

    class FakeControl:
        def __init__(self, config_path: Path) -> None:
            self.config_path = config_path

        def events_subscribe(self, *, start_at_end: bool, poll_interval_seconds: float, idle_timeout_seconds: float):
            yield EventRecord(
                type=EventType.ENGINE_STARTED,
                source=EventSource.ENGINE,
                timestamp=datetime(2026, 3, 25, tzinfo=timezone.utc),
                payload={"step": 1},
            )
            yield EventRecord(
                type=EventType.ENGINE_PAUSED,
                source=EventSource.ENGINE,
                timestamp=datetime(2026, 3, 25, 0, 0, 1, tzinfo=timezone.utc),
                payload={"step": 2},
            )
            yield EventRecord(
                type=EventType.ENGINE_RESUMED,
                source=EventSource.ENGINE,
                timestamp=datetime(2026, 3, 25, 0, 0, 2, tzinfo=timezone.utc),
                payload={"step": 3},
            )
            cancel_event.set()

    posted: list[EventsAppended | EventStreamFailed] = []
    monkeypatch.setattr(workers_module, "get_current_worker", lambda: FakeWorker())
    monkeypatch.setattr(workers_module, "EngineControl", FakeControl)

    workers_module.stream_event_updates(
        tmp_path / "millrace.toml",
        post_message=posted.append,
        settings=WorkerSettings(
            refresh_interval_seconds=1.0,
            event_batch_size=2,
            event_batch_window_seconds=60.0,
            event_retry_delay_seconds=0.01,
        ),
        start_at_end=True,
    )

    batches = [message for message in posted if isinstance(message, EventsAppended)]
    assert [len(message.events) for message in batches] == [2, 1]
    assert not [message for message in posted if isinstance(message, EventStreamFailed)]


def test_tui_shell_overview_handles_invalid_latest_run_artifact_gracefully(tmp_path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")
    run_dir = workspace / "agents" / "runs" / "smoke-standard"
    run_dir.mkdir(parents=True, exist_ok=True)
    sample_snapshot = FIXTURE_ROOT / "tui_samples" / "agents" / "runs" / "smoke-standard" / "resolved_snapshot.json"
    shutil.copy2(sample_snapshot, run_dir / "resolved_snapshot.json")

    async def scenario(app: MillraceTUIApplication, pilot) -> None:
        await _wait_for_condition(
            pilot,
            lambda: isinstance(app.screen, ShellScreen)
            and "LATEST   FAIL smoke-standard | invalid provenance"
            in app.screen.query_one("#panel-overview", OverviewPanel).summary_text(),
        )
        overview_text = app.screen.query_one("#panel-overview", OverviewPanel).summary_text()
        assert "LATEST   FAIL smoke-standard | invalid provenance" in overview_text

    _run_app_scenario(config_path, scenario)
