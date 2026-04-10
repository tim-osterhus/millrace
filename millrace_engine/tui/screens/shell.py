"""Persistent shell screen for the Millrace operator TUI."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Footer
from textual.worker import Worker, WorkerState

from ...health import WorkspaceHealthReport
from ..gateway import RuntimeGateway
from ..launcher import launch_start_daemon, launch_start_once
from ..messages import (
    ActionFailed,
    ActionSucceeded,
    EventsAppended,
    EventStreamFailed,
    RefreshFailed,
    RefreshSucceeded,
)
from ..models import (
    DEFAULT_PANEL,
    EXPANDED_STREAM_WIDGET_ID,
    PANEL_BY_ID,
    PANELS,
    GatewayFailure,
    PanelId,
    ShellBodyMode,
    lifecycle_signal_from_context,
    notice_from_action,
    notice_from_failure,
    panel_widget_id,
    shell_content_target,
)
from ..store import TUIStore
from ..widgets.config_panel import ConfigPanel
from ..widgets.expanded_stream import ExpandedStreamView
from ..widgets.logs_panel import LogsPanel
from ..widgets.notices import NoticesView
from ..widgets.overview_panel import LatestRunSummary, OverviewPanel
from ..widgets.publish_panel import PublishPanel
from ..widgets.queue_panel import QueuePanel
from ..widgets.research_panel import ResearchPanel
from ..widgets.runs_panel import RunsPanel
from ..widgets.shell_inspector import ShellInspector
from ..widgets.sidebar import SidebarNav
from ..widgets.status_bar import StatusBar
from ..workers import (
    EVENT_STREAM_WORKER_GROUP,
    EVENT_STREAM_WORKER_NAME,
    INITIAL_REFRESH_WORKER_NAME,
    PERIODIC_REFRESH_WORKER_NAME,
    REFRESH_WORKER_GROUP,
    WorkerSettings,
    load_workspace_refresh,
    stream_event_updates,
)
from .shell_support import (
    INITIAL_REFRESH_PANELS,
    LIFECYCLE_WORKER_PREFIX,
    PUBLISH_REFRESH_WORKER_GROUP,
    PUBLISH_REFRESH_WORKER_NAME,
    WORKSPACE_REFRESH_PANELS,
    build_shell_inspector_view,
    is_lifecycle_action,
    latest_run_summary_from_runs,
    refresh_panels_for_action,
    targeted_refresh_worker_name,
    worker_state_outcome,
)
from .shell_workflows import ShellWorkflowMixin

ACTION_GROUP = Binding.Group("Actions", compact=True)
DISCOVERY_GROUP = Binding.Group("Discover", compact=True)


class ShellFocusZone(Enum):
    """Focusable shell regions that the shell owns explicitly."""

    SIDEBAR = "sidebar"
    WORKSPACE = "workspace"


class ShellScreen(ShellWorkflowMixin, Screen[None]):
    """Persistent shell layout with refresh workers and inline failure state."""

    BINDINGS = (
        Binding("t", "open_add_task", "Add Task", group=ACTION_GROUP),
        Binding("i", "open_add_idea", "Add Idea", group=ACTION_GROUP),
        Binding("d", "toggle_display_mode", "Mode", group=DISCOVERY_GROUP),
        Binding("e", "toggle_expanded_mode", "Expanded", group=DISCOVERY_GROUP),
        Binding("l", "jump_expanded_stream_live", "Jump Live", show=False),
        Binding("escape", "exit_expanded_mode", "Exit Expanded", show=False),
        Binding("question_mark", "open_help", "Help", key_display="?", group=DISCOVERY_GROUP),
    )

    def __init__(
        self,
        *,
        config_path: Path,
        workspace_path: Path,
        worker_settings: WorkerSettings | None = None,
        offer_startup_daemon_launch: bool = True,
    ) -> None:
        super().__init__()
        self.config_path = config_path
        self.workspace_path = workspace_path
        self.active_panel: PanelId = DEFAULT_PANEL
        self._shell_body_mode = ShellBodyMode.COMPACT
        self.worker_settings = worker_settings or WorkerSettings()
        self.offer_startup_daemon_launch = offer_startup_daemon_launch
        self._store = TUIStore()
        self._refresh_timer: Timer | None = None
        self._bootstrap_health_report: WorkspaceHealthReport | None = None
        self._background_started = False
        self._event_stream_worker: Worker[None] | None = None
        self._latest_run_summary: LatestRunSummary | None = None
        self._requested_run_id: str | None = None
        self._pending_queue_reorder: tuple[str, ...] | None = None
        self._pending_publish_push: bool | None = None
        self._lifecycle_busy_message: str | None = None
        self._lifecycle_worker_name: str | None = None
        self._last_lifecycle_failure: GatewayFailure | None = None
        self._startup_prompt_window_open = offer_startup_daemon_launch
        self._focus_zone = ShellFocusZone.SIDEBAR
        self._focus_zone_before_expanded: ShellFocusZone | None = None
        self._focus_zone_before_modal: ShellFocusZone | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="shell-root"):
            with Horizontal(id="shell-body"):
                yield SidebarNav(
                    PANELS,
                    active_panel=self.active_panel,
                    display_mode=self._store.state.display_mode,
                    lifecycle_signal=self._lifecycle_signal(),
                    id="shell-sidebar",
                )
                with Vertical(id="shell-main"):
                    yield StatusBar(id="shell-status")
                    with ContentSwitcher(
                        id="shell-content",
                        initial=shell_content_target(self.active_panel, self._shell_body_mode),
                    ):
                        for panel in PANELS:
                            if panel.id is PanelId.OVERVIEW:
                                yield OverviewPanel(id=panel_widget_id(panel.id))
                            elif panel.id is PanelId.QUEUE:
                                yield QueuePanel(id=panel_widget_id(panel.id))
                            elif panel.id is PanelId.RUNS:
                                yield RunsPanel(id=panel_widget_id(panel.id))
                            elif panel.id is PanelId.RESEARCH:
                                yield ResearchPanel(id=panel_widget_id(panel.id))
                            elif panel.id is PanelId.LOGS:
                                yield LogsPanel(id=panel_widget_id(panel.id))
                            elif panel.id is PanelId.CONFIG:
                                yield ConfigPanel(id=panel_widget_id(panel.id))
                            elif panel.id is PanelId.PUBLISH:
                                yield PublishPanel(id=panel_widget_id(panel.id))
                            else:
                                continue
                        yield ExpandedStreamView(id=EXPANDED_STREAM_WIDGET_ID)
                yield ShellInspector(id="shell-inspector")
            with Horizontal(id="shell-bottom"):
                yield NoticesView(id="shell-notices")
                yield Footer(id="shell-footer")

    def on_mount(self) -> None:
        self._sync_panel_state()
        self.focus_sidebar()
        self._start_initial_refresh()

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None
        if self._event_stream_worker is not None:
            self._event_stream_worker.cancel()
            self._event_stream_worker = None
        self.workers.cancel_all()

    def prime_bootstrap_health(self, report: WorkspaceHealthReport) -> None:
        self._bootstrap_health_report = report
        if self.is_mounted:
            self._render_state()

    def open_panel(self, panel_id: PanelId) -> None:
        if self.active_panel == panel_id:
            return
        previous_zone = self._focused_zone(self.focused) or self._focus_zone
        self.active_panel = panel_id
        self._sync_panel_state()
        self.restore_focus_zone(previous_zone)

    def focus_sidebar(self) -> None:
        self._focus_zone = ShellFocusZone.SIDEBAR
        self.query_one(SidebarNav).focus_active_button()

    def focus_content(self) -> None:
        self._focus_zone = ShellFocusZone.WORKSPACE
        if self._shell_body_mode is ShellBodyMode.EXPANDED:
            self.query_one(f"#{EXPANDED_STREAM_WIDGET_ID}").focus()
            return
        self.query_one(f"#{panel_widget_id(self.active_panel)}").focus()

    def action_focus_next(self) -> None:
        if self._focus_zone is ShellFocusZone.WORKSPACE:
            self.focus_sidebar()
            return
        self.focus_content()

    def action_focus_previous(self) -> None:
        if self._focus_zone is ShellFocusZone.SIDEBAR:
            self.focus_content()
            return
        self.focus_sidebar()

    def capture_focus_zone_for_modal(self) -> None:
        self._focus_zone_before_modal = self._focused_zone(self.focused) or self._focus_zone

    def restore_focus_after_modal(self) -> None:
        zone = self._focus_zone_before_modal
        self._focus_zone_before_modal = None
        self.restore_focus_zone(zone)

    def restore_focus_zone(self, zone: ShellFocusZone | None) -> None:
        if zone is ShellFocusZone.SIDEBAR:
            self.focus_sidebar()
            return
        self.focus_content()

    def _runtime_gateway(self) -> RuntimeGateway:
        return RuntimeGateway(self.config_path)

    def _launch_start_once_request(self):
        return launch_start_once(self.config_path)

    def _launch_start_daemon_request(self):
        return launch_start_daemon(self.config_path)

    def _start_initial_refresh(self) -> None:
        self.run_worker(
            lambda: load_workspace_refresh(
                self.config_path,
                include_events=True,
                settings=self.worker_settings,
            ),
            name=INITIAL_REFRESH_WORKER_NAME,
            group=REFRESH_WORKER_GROUP,
            exclusive=True,
            exit_on_error=False,
            thread=True,
        )

    def _start_periodic_refresh(self) -> None:
        self.run_worker(
            lambda: load_workspace_refresh(
                self.config_path,
                include_events=False,
                settings=self.worker_settings,
            ),
            name=PERIODIC_REFRESH_WORKER_NAME,
            group=REFRESH_WORKER_GROUP,
            exclusive=True,
            exit_on_error=False,
            thread=True,
        )

    def _start_targeted_refresh(self, action: str) -> None:
        panels = refresh_panels_for_action(action)
        if panels is None:
            return
        self.run_worker(
            lambda: load_workspace_refresh(
                self.config_path,
                include_events=False,
                settings=self.worker_settings,
            ),
            name=targeted_refresh_worker_name(action),
            group=REFRESH_WORKER_GROUP,
            exclusive=True,
            exit_on_error=False,
            thread=True,
        )

    def _start_publish_refresh(self) -> None:
        self.run_worker(
            lambda: RuntimeGateway(self.config_path).load_publish_status(),
            name=PUBLISH_REFRESH_WORKER_NAME,
            group=PUBLISH_REFRESH_WORKER_GROUP,
            exclusive=True,
            exit_on_error=False,
            thread=True,
        )

    def _ensure_background_runtime(self) -> None:
        if self._background_started:
            return
        self._background_started = True
        self._refresh_timer = self.set_interval(
            self.worker_settings.refresh_interval_seconds,
            self._start_periodic_refresh,
            name="workspace-refresh",
        )
        self._event_stream_worker = self.run_worker(
            lambda: stream_event_updates(
                self.config_path,
                post_message=self.post_message,
                settings=self.worker_settings,
                start_at_end=True,
            ),
            name=EVENT_STREAM_WORKER_NAME,
            group=EVENT_STREAM_WORKER_GROUP,
            exclusive=True,
            exit_on_error=False,
            thread=True,
        )

    @on(Worker.StateChanged)
    def _handle_worker_state_changed(self, message: Worker.StateChanged) -> None:
        outcome = worker_state_outcome(
            worker_name=message.worker.name,
            state=message.state,
            result=message.worker.result,
            error=(message.worker.error if isinstance(message.worker.error, Exception) else None),
        )
        if outcome is None:
            return
        if outcome.clear_lifecycle_busy:
            self._clear_lifecycle_busy(message.worker.name)
        if outcome.message is not None:
            self.post_message(outcome.message)
        if outcome.ensure_background_runtime:
            self._ensure_background_runtime()

    def on_refresh_succeeded(self, message: RefreshSucceeded) -> None:
        self._store.apply_refresh_success(message.payload, panels=message.panels)
        self._latest_run_summary = latest_run_summary_from_runs(self._store.state.runs)
        self._render_for_panels(message.panels, include_shell_chrome=True)
        self._maybe_offer_startup_daemon_launch()
        if self.active_panel is PanelId.PUBLISH and PanelId.PUBLISH not in message.panels:
            self._start_publish_refresh()

    def on_refresh_failed(self, message: RefreshFailed) -> None:
        if self._store.state.last_refresh_failure == message.failure and all(
            self._store.panel_failure(panel_id) == message.failure for panel_id in message.panels
        ):
            return
        notice = notice_from_failure(message.failure)
        previous_notices = self._store.state.notices
        self._store.apply_refresh_failure(message.failure, panels=message.panels, notice=notice)
        self._render_for_panels(message.panels, include_shell_chrome=True, include_notices=True)
        self._notify_if_added(previous_notices)

    def on_action_succeeded(self, message: ActionSucceeded) -> None:
        if message.result.action == "reorder_queue":
            self.query_one(QueuePanel).clear_reorder_draft()
        if is_lifecycle_action(message.result.action):
            self._last_lifecycle_failure = None
        notice = notice_from_action(message.result)
        previous_notices = self._store.state.notices
        self._store.apply_action_success(message.result, notice=notice)
        if message.result.action == "reorder_queue":
            self._render_for_panels((PanelId.QUEUE,), include_inspector=True)
        self._render_notices()
        self._notify_if_added(previous_notices)
        if message.result.action in {"publish_sync", "publish_commit"}:
            self._start_publish_refresh()
        elif message.result.applied:
            self._start_targeted_refresh(message.result.action)

    def on_action_failed(self, message: ActionFailed) -> None:
        if is_lifecycle_action(message.failure.operation):
            self._last_lifecycle_failure = message.failure
        notice = notice_from_failure(message.failure)
        previous_notices = self._store.state.notices
        self._store.apply_action_failure(message.failure, notice=notice)
        self._render_shell_chrome()
        self._render_inspector()
        self._render_notices()
        self._notify_if_added(previous_notices)

    def on_events_appended(self, message: EventsAppended) -> None:
        self._store.append_events(message.events, received_at=message.received_at, clear_panels=(PanelId.LOGS,))
        self._render_for_panels((PanelId.LOGS,), include_expanded=True)

    def on_event_stream_failed(self, message: EventStreamFailed) -> None:
        notice = notice_from_failure(message.failure)
        previous_notices = self._store.state.notices
        self._store.apply_panel_failure(message.failure, panels=(PanelId.LOGS,), notice=notice)
        self._render_for_panels((PanelId.LOGS,), include_notices=True)
        self._notify_if_added(previous_notices)

    def action_toggle_display_mode(self) -> None:
        self._store.toggle_display_mode()
        self._render_state()

    def action_toggle_expanded_mode(self) -> None:
        next_mode = (
            ShellBodyMode.COMPACT if self._shell_body_mode is ShellBodyMode.EXPANDED else ShellBodyMode.EXPANDED
        )
        self._set_shell_body_mode(next_mode)

    def action_exit_expanded_mode(self) -> None:
        if self._shell_body_mode is ShellBodyMode.EXPANDED:
            self._set_shell_body_mode(ShellBodyMode.COMPACT)

    def action_jump_expanded_stream_live(self) -> None:
        if self._shell_body_mode is not ShellBodyMode.EXPANDED:
            return
        self.query_one(ExpandedStreamView).action_jump_to_live()

    def _sync_panel_state(self) -> None:
        self.query_one("#shell-content", ContentSwitcher).current = shell_content_target(
            self.active_panel,
            self._shell_body_mode,
        )
        self.query_one(SidebarNav).set_active_panel(self.active_panel)
        self._render_state()
        if self.active_panel is PanelId.PUBLISH:
            self._start_publish_refresh()

    def _set_shell_body_mode(self, mode: ShellBodyMode) -> None:
        previous_mode = self._shell_body_mode
        if mode is ShellBodyMode.EXPANDED and previous_mode is not ShellBodyMode.EXPANDED:
            self._focus_zone_before_expanded = self._focused_zone(self.focused) or self._focus_zone
        self._shell_body_mode = mode
        if self.is_mounted:
            self._sync_panel_state()
            if mode is ShellBodyMode.EXPANDED:
                self.focus_content()
            elif previous_mode is ShellBodyMode.EXPANDED:
                zone = self._focus_zone_before_expanded
                self._focus_zone_before_expanded = None
                self.restore_focus_zone(zone)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        zone = self._focused_zone(event.widget)
        if zone is not None:
            self._focus_zone = zone

    def _focused_zone(self, widget: Widget | None) -> ShellFocusZone | None:
        current = widget
        active_panel_id = panel_widget_id(self.active_panel)
        while current is not None:
            widget_id = current.id
            if widget_id is not None and widget_id.startswith("nav-"):
                return ShellFocusZone.SIDEBAR
            if widget_id == active_panel_id or widget_id == EXPANDED_STREAM_WIDGET_ID:
                return ShellFocusZone.WORKSPACE
            current = current.parent
        return None

    def _render_state(self) -> None:
        self._render_shell_chrome()
        self._render_panels(PANELS)
        self._render_expanded_stream()
        self._render_inspector()
        self._render_notices()

    def _render_for_panels(
        self,
        panels: tuple[PanelId, ...],
        *,
        include_shell_chrome: bool = False,
        include_inspector: bool = False,
        include_notices: bool = False,
        include_expanded: bool = False,
    ) -> None:
        if include_shell_chrome:
            self._render_shell_chrome()
        render_panels = list(dict.fromkeys((*panels, *((PanelId.OVERVIEW,) if PanelId.RUNS in panels else ()))))
        self._render_panels(render_panels)
        if include_expanded or PanelId.LOGS in render_panels:
            self._render_expanded_stream()
        if include_inspector or self.active_panel in render_panels:
            self._render_inspector()
        if include_notices:
            self._render_notices()

    def _render_shell_chrome(self) -> None:
        state = self._store.state
        active = PANEL_BY_ID[self.active_panel]
        lifecycle_signal = self._lifecycle_signal()
        sidebar = self.query_one(SidebarNav)
        sidebar.set_display_mode(state.display_mode)
        sidebar.set_expanded_mode(self._shell_body_mode is ShellBodyMode.EXPANDED)
        sidebar.set_lifecycle_signal(lifecycle_signal)
        self.query_one(StatusBar).show_state(
            workspace_path=self.workspace_path,
            active_panel_label=active.label,
            expanded_mode=self._shell_body_mode is ShellBodyMode.EXPANDED,
            display_mode=state.display_mode,
            lifecycle=lifecycle_signal,
            health_report=self._bootstrap_health_report,
            runtime=state.runtime,
            queue=state.queue,
            last_refreshed_at=state.last_refreshed_at,
            refresh_failure=state.last_refresh_failure,
            busy_message=self._lifecycle_busy_message,
        )

    def _render_panels(self, panels: tuple | list) -> None:
        for panel in panels:
            panel_id = panel.id if hasattr(panel, "id") else panel
            self._render_panel(panel_id)

    def _render_panel(self, panel_id: PanelId) -> None:
        state = self._store.state
        if panel_id is PanelId.OVERVIEW:
            self.query_one(OverviewPanel).show_snapshot(
                runtime=state.runtime,
                queue=state.queue,
                research=state.research,
                compounding=state.compounding,
                latest_run=self._latest_run_summary,
                failure=self._store.panel_failure(PanelId.OVERVIEW),
                display_mode=state.display_mode,
            )
            return
        if panel_id is PanelId.QUEUE:
            self.query_one(QueuePanel).show_snapshot(
                state.queue,
                run_id=(state.runtime.selection.run_id if state.runtime is not None else None),
                failure=self._store.panel_failure(PanelId.QUEUE),
                display_mode=state.display_mode,
            )
            return
        if panel_id is PanelId.RUNS:
            self.query_one(RunsPanel).show_snapshot(
                state.runs,
                requested_run_id=self._requested_run_id,
                failure=self._store.panel_failure(PanelId.RUNS),
                display_mode=state.display_mode,
            )
            return
        if panel_id is PanelId.RESEARCH:
            self.query_one(ResearchPanel).show_snapshot(
                state.research,
                failure=self._store.panel_failure(PanelId.RESEARCH),
                display_mode=state.display_mode,
            )
            return
        if panel_id is PanelId.LOGS:
            self.query_one(LogsPanel).show_snapshot(
                state.events,
                failure=self._store.panel_failure(PanelId.LOGS),
                display_mode=state.display_mode,
            )
            return
        if panel_id is PanelId.CONFIG:
            self.query_one(ConfigPanel).show_snapshot(
                state.config,
                runtime=state.runtime,
                failure=self._store.panel_failure(PanelId.CONFIG),
                display_mode=state.display_mode,
            )
            return
        if panel_id is PanelId.PUBLISH:
            self.query_one(PublishPanel).show_snapshot(
                state.publish,
                failure=self._store.panel_failure(PanelId.PUBLISH),
                display_mode=state.display_mode,
            )

    def _render_expanded_stream(self) -> None:
        state = self._store.state
        active = PANEL_BY_ID[self.active_panel]
        self.query_one(ExpandedStreamView).show_snapshot(
            active_panel_label=active.label,
            display_mode=state.display_mode,
            events=state.events,
            live=self._shell_body_mode is ShellBodyMode.EXPANDED,
        )

    def _render_inspector(self) -> None:
        state = self._store.state
        active = PANEL_BY_ID[self.active_panel]
        self.query_one(ShellInspector).show_view(
            build_shell_inspector_view(
                active_panel=active,
                display_mode=state.display_mode,
                expanded_mode=self._shell_body_mode is ShellBodyMode.EXPANDED,
                runtime=state.runtime,
                queue=state.queue,
                runs=state.runs,
                research=state.research,
                config=state.config,
                publish=state.publish,
                latest_run=self._latest_run_summary,
                panel_failure=self._store.panel_failure(self.active_panel),
                selected_task_id=self.query_one(QueuePanel).selected_task_id,
                selected_run_id=self.query_one(RunsPanel).selected_run_id,
                selected_event=self.query_one(LogsPanel).selected_event,
                selected_question_id=self.query_one(ResearchPanel).selected_question_id,
                selected_config_field_key=self.query_one(ConfigPanel).selected_field_key,
                selected_publish_path=self.query_one(PublishPanel).selected_path,
            )
        )

    def _render_notices(self) -> None:
        self.query_one(NoticesView).show_notices(self._store.state.notices)

    def _pending_lifecycle_action_name(self) -> str | None:
        if self._lifecycle_worker_name is None:
            return None
        if self._lifecycle_worker_name.startswith(LIFECYCLE_WORKER_PREFIX):
            return self._lifecycle_worker_name.removeprefix(LIFECYCLE_WORKER_PREFIX)
        return None

    def _lifecycle_signal(self):
        state = self._store.state
        return lifecycle_signal_from_context(
            runtime=state.runtime,
            pending_action=self._pending_lifecycle_action_name(),
            pending_message=self._lifecycle_busy_message,
            lifecycle_failure=self._last_lifecycle_failure,
        )

    @on(SidebarNav.PanelSelected)
    def _handle_sidebar_selection(self, message: SidebarNav.PanelSelected) -> None:
        self.open_panel(message.panel_id)

    @on(SidebarNav.ModeToggleRequested)
    def _handle_sidebar_mode_toggle(self, message: SidebarNav.ModeToggleRequested) -> None:
        self.action_toggle_display_mode()
        message.stop()

    @on(SidebarNav.ExpandedToggleRequested)
    def _handle_sidebar_expanded_toggle(self, message: SidebarNav.ExpandedToggleRequested) -> None:
        self.action_toggle_expanded_mode()
        message.stop()

    @on(QueuePanel.SelectionChanged)
    def _handle_queue_selection_changed(self, message: QueuePanel.SelectionChanged) -> None:
        if self.active_panel is PanelId.QUEUE:
            self._render_inspector()
        message.stop()

    @on(RunsPanel.SelectionChanged)
    def _handle_runs_selection_changed(self, message: RunsPanel.SelectionChanged) -> None:
        if self.active_panel is PanelId.RUNS:
            self._render_inspector()
        message.stop()

    @on(ResearchPanel.SelectionChanged)
    def _handle_research_selection_changed(self, message: ResearchPanel.SelectionChanged) -> None:
        if self.active_panel is PanelId.RESEARCH:
            self._render_inspector()
        message.stop()

    @on(ConfigPanel.SelectionChanged)
    def _handle_config_selection_changed(self, message: ConfigPanel.SelectionChanged) -> None:
        if self.active_panel is PanelId.CONFIG:
            self._render_inspector()
        message.stop()

    @on(PublishPanel.SelectionChanged)
    def _handle_publish_selection_changed(self, message: PublishPanel.SelectionChanged) -> None:
        if self.active_panel is PanelId.PUBLISH:
            self._render_inspector()
        message.stop()


_PANEL_MESSAGE_HANDLERS = (
    (LogsPanel.RunRequested, ShellWorkflowMixin._handle_logs_run_requested),
    (RunsPanel.RunRequested, ShellWorkflowMixin._handle_runs_run_requested),
    (QueuePanel.RunRequested, ShellWorkflowMixin._handle_queue_run_requested),
    (QueuePanel.ReorderRequested, ShellWorkflowMixin._handle_queue_reorder_requested),
    (ConfigPanel.EditRequested, ShellWorkflowMixin._handle_config_edit_requested),
    (ConfigPanel.ReloadRequested, ShellWorkflowMixin._handle_config_reload_requested),
    (ResearchPanel.InterviewRequested, ShellWorkflowMixin._handle_research_interview_requested),
    (PublishPanel.PreflightRequested, ShellWorkflowMixin._handle_publish_preflight_requested),
    (PublishPanel.SyncRequested, ShellWorkflowMixin._handle_publish_sync_requested),
    (PublishPanel.CommitRequested, ShellWorkflowMixin._handle_publish_commit_requested),
)
for _message_type, _handler in _PANEL_MESSAGE_HANDLERS:
    ShellScreen._decorated_handlers.setdefault(_message_type, []).append((_handler, {}))


__all__ = ["ShellScreen"]
