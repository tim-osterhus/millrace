"""Persistent shell screen for the Millrace operator TUI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import ContentSwitcher, Static
from textual.worker import Worker, WorkerState

from ...health import WorkspaceHealthReport
from ..gateway import RuntimeGateway
from ..launcher import launch_start_daemon, launch_start_once
from ..messages import (
    ActionFailed,
    ActionSucceeded,
    EventStreamFailed,
    EventsAppended,
    RefreshFailed,
    RefreshSucceeded,
)
from ..models import (
    DEFAULT_PANEL,
    EXPANDED_STREAM_WIDGET_ID,
    GatewayFailure,
    GatewayResult,
    PANEL_BY_ID,
    PANELS,
    PanelId,
    ShellBodyMode,
    lifecycle_signal_from_context,
    notice_from_action,
    notice_from_failure,
    panel_widget_id,
    shell_content_target,
)
from .add_idea_modal import AddIdeaModal, AddIdeaRequest
from .add_task_modal import AddTaskModal, AddTaskRequest
from .confirm_modal import ConfirmModal
from .config_edit_modal import ConfigEditModal, ConfigEditRequest
from .help_modal import HelpModal
from .interview_modal import InterviewModal, InterviewResolutionRequest
from .run_detail_modal import RunDetailModal
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
    ACTION_WORKER_GROUP,
    ACTION_WORKER_PREFIX,
    INITIAL_REFRESH_PANELS,
    LIFECYCLE_WORKER_GROUP,
    LIFECYCLE_WORKER_PREFIX,
    PUBLISH_REFRESH_WORKER_GROUP,
    PUBLISH_REFRESH_WORKER_NAME,
    WORKSPACE_REFRESH_PANELS,
    is_lifecycle_action,
    latest_run_summary_from_runs,
    notification_severity,
    publish_confirmation_lines,
    queue_reorder_confirmation_lines,
    selected_config_field,
    worker_state_outcome,
)

ACTION_GROUP = Binding.Group("Actions", compact=True)
DISCOVERY_GROUP = Binding.Group("Discover", compact=True)


class ShellScreen(Screen[None]):
    """Persistent shell layout with refresh workers and inline failure state."""

    BINDINGS = (
        Binding("t", "open_add_task", "Add Task", group=ACTION_GROUP),
        Binding("i", "open_add_idea", "Add Idea", group=ACTION_GROUP),
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

    def compose(self) -> ComposeResult:
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
                yield NoticesView(id="shell-notices")

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
        focus_sidebar = self.focused is not None and self.focused.id is not None and self.focused.id.startswith("nav-")
        focus_content = self.focused is not None and self.focused.id == panel_widget_id(self.active_panel)
        self.active_panel = panel_id
        self._sync_panel_state()
        if focus_sidebar:
            self.focus_sidebar()
        elif focus_content:
            self.focus_content()

    def focus_sidebar(self) -> None:
        self.query_one(SidebarNav).focus_active_button()

    def focus_content(self) -> None:
        if self._shell_body_mode is ShellBodyMode.EXPANDED:
            self.query_one(f"#{EXPANDED_STREAM_WIDGET_ID}").focus()
            return
        self.query_one(f"#{panel_widget_id(self.active_panel)}").focus()

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
        self._render_state()
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
        self._render_state()
        self._notify_if_added(previous_notices)

    def on_action_succeeded(self, message: ActionSucceeded) -> None:
        if message.result.action == "reorder_queue":
            self.query_one(QueuePanel).clear_reorder_draft()
        if is_lifecycle_action(message.result.action):
            self._last_lifecycle_failure = None
        notice = notice_from_action(message.result)
        previous_notices = self._store.state.notices
        self._store.apply_action_success(message.result, notice=notice)
        self._render_state()
        self._notify_if_added(previous_notices)
        if message.result.action in {"publish_sync", "publish_commit"}:
            self._start_publish_refresh()
        if message.result.applied:
            self._start_periodic_refresh()

    def on_action_failed(self, message: ActionFailed) -> None:
        if is_lifecycle_action(message.failure.operation):
            self._last_lifecycle_failure = message.failure
        notice = notice_from_failure(message.failure)
        previous_notices = self._store.state.notices
        self._store.apply_action_failure(message.failure, notice=notice)
        self._render_state()
        self._notify_if_added(previous_notices)

    def on_events_appended(self, message: EventsAppended) -> None:
        self._store.append_events(message.events, received_at=message.received_at, clear_panels=(PanelId.LOGS,))
        self._render_state()

    def on_event_stream_failed(self, message: EventStreamFailed) -> None:
        notice = notice_from_failure(message.failure)
        previous_notices = self._store.state.notices
        self._store.apply_panel_failure(message.failure, panels=(PanelId.LOGS,), notice=notice)
        self._render_state()
        self._notify_if_added(previous_notices)

    def action_open_add_task(self) -> None:
        self.app.push_screen(AddTaskModal(), self._handle_add_task_request)

    def action_open_add_idea(self) -> None:
        self.app.push_screen(
            AddIdeaModal(workspace_path=self.workspace_path),
            self._handle_add_idea_request,
        )

    def action_open_help(self) -> None:
        self.app.push_screen(HelpModal(active_panel=self.active_panel))

    def action_toggle_display_mode(self) -> None:
        self._store.toggle_display_mode()
        self._render_state()

    def action_toggle_expanded_mode(self) -> None:
        next_mode = (
            ShellBodyMode.COMPACT
            if self._shell_body_mode is ShellBodyMode.EXPANDED
            else ShellBodyMode.EXPANDED
        )
        self._set_shell_body_mode(next_mode)

    def action_exit_expanded_mode(self) -> None:
        if self._shell_body_mode is ShellBodyMode.EXPANDED:
            self._set_shell_body_mode(ShellBodyMode.COMPACT)

    def action_jump_expanded_stream_live(self) -> None:
        if self._shell_body_mode is not ShellBodyMode.EXPANDED:
            return
        self.query_one(ExpandedStreamView).action_jump_to_live()

    def action_start_once(self) -> None:
        self._run_lifecycle_action(
            "start_once",
            busy_message="foreground once run in progress",
            work_factory=lambda: launch_start_once(self.config_path),
            thread=False,
        )

    def action_start_daemon(self) -> None:
        self._run_lifecycle_action(
            "start_daemon",
            busy_message="detached daemon launch in progress",
            work_factory=lambda: launch_start_daemon(self.config_path),
            thread=False,
        )

    def action_pause_runtime(self) -> None:
        self._run_lifecycle_action(
            "pause",
            busy_message="pause request in progress",
            work_factory=lambda: RuntimeGateway(self.config_path).pause_runtime,
            thread=True,
        )

    def action_resume_runtime(self) -> None:
        self._run_lifecycle_action(
            "resume",
            busy_message="resume request in progress",
            work_factory=lambda: RuntimeGateway(self.config_path).resume_runtime,
            thread=True,
        )

    def action_stop_runtime(self) -> None:
        self._run_lifecycle_action(
            "stop",
            busy_message="stop request in progress",
            work_factory=lambda: RuntimeGateway(self.config_path).stop_runtime,
            thread=True,
        )

    def action_edit_selected_config(self) -> None:
        self._open_config_edit_modal()

    def action_reload_config(self) -> None:
        self._request_config_reload()

    def action_refresh_publish_preflight(self) -> None:
        self._start_publish_refresh()

    def action_publish_sync(self) -> None:
        self._request_publish_sync()

    def action_publish_commit(self) -> None:
        self._confirm_publish_commit(push=False)

    def action_publish_push(self) -> None:
        self._confirm_publish_commit(push=True)

    def _handle_add_task_request(self, request: AddTaskRequest | None) -> None:
        if request is None:
            return
        self._run_gateway_action(
            "add_task",
            lambda: RuntimeGateway(self.config_path).add_task(
                request.title,
                body=request.body,
                spec_id=request.spec_id,
            ),
        )

    def _handle_add_idea_request(self, request: AddIdeaRequest | None) -> None:
        if request is None:
            return
        self._run_gateway_action(
            "add_idea",
            lambda: RuntimeGateway(self.config_path).add_idea(request.source_path),
        )

    def _maybe_offer_startup_daemon_launch(self) -> None:
        if not self._startup_prompt_window_open:
            return
        runtime = self._store.state.runtime
        if runtime is None:
            return
        self._startup_prompt_window_open = False
        if runtime.process_running:
            return
        self.app.push_screen(
            ConfirmModal(
                title="Launch Daemon Now",
                body_lines=(
                    "The Millrace runtime is not currently running.",
                    "",
                    "Launch daemon mode now and keep the TUI attached as the live control surface?",
                    "",
                    "You can still use Start Once instead if you only want a single foreground cycle.",
                ),
                confirm_label="Start Daemon",
                cancel_label="Stay Idle",
            ),
            self._handle_startup_daemon_confirmation,
        )

    def _handle_startup_daemon_confirmation(self, confirmed: bool) -> None:
        if not confirmed:
            return
        self.action_start_daemon()

    def _run_gateway_action(
        self,
        name: str,
        action: Callable[[], GatewayResult],
    ) -> None:
        self.run_worker(
            action,
            name=f"{ACTION_WORKER_PREFIX}{name}",
            group=ACTION_WORKER_GROUP,
            exclusive=True,
            exit_on_error=False,
            thread=True,
        )

    def _run_lifecycle_action(
        self,
        name: str,
        *,
        busy_message: str,
        work_factory: Callable[[], object],
        thread: bool,
    ) -> None:
        if self._lifecycle_busy_message is not None:
            self.app.notify(
                f"Lifecycle action already in progress: {self._lifecycle_busy_message}.",
                title="Lifecycle Busy",
                severity="warning",
            )
            return
        worker_name = f"{LIFECYCLE_WORKER_PREFIX}{name}"
        self._lifecycle_busy_message = busy_message
        self._lifecycle_worker_name = worker_name
        self._last_lifecycle_failure = None
        if self.is_mounted:
            self._render_state()
        self.run_worker(
            work_factory(),
            name=worker_name,
            group=LIFECYCLE_WORKER_GROUP,
            description=busy_message,
            exclusive=True,
            exit_on_error=False,
            thread=thread,
        )

    def _clear_lifecycle_busy(self, worker_name: str) -> None:
        if self._lifecycle_worker_name != worker_name:
            return
        self._lifecycle_busy_message = None
        self._lifecycle_worker_name = None
        if self.is_mounted:
            self._render_state()

    @on(LogsPanel.RunRequested)
    def _handle_logs_run_requested(self, message: LogsPanel.RunRequested) -> None:
        self._open_run_workflow(message.run_id)
        message.stop()

    @on(RunsPanel.RunRequested)
    def _handle_runs_run_requested(self, message: RunsPanel.RunRequested) -> None:
        self._open_run_workflow(message.run_id)
        message.stop()

    @on(QueuePanel.RunRequested)
    def _handle_queue_run_requested(self, message: QueuePanel.RunRequested) -> None:
        self._open_run_workflow(message.run_id)
        message.stop()

    @on(QueuePanel.ReorderRequested)
    def _handle_queue_reorder_requested(self, message: QueuePanel.ReorderRequested) -> None:
        self._pending_queue_reorder = message.task_ids
        self.app.push_screen(
            ConfirmModal(
                title="Apply Queue Reorder",
                body_lines=queue_reorder_confirmation_lines(
                    message.task_ids,
                    queue=self._store.state.queue,
                    runtime=self._store.state.runtime,
                ),
                confirm_label="Apply Reorder",
            ),
            self._handle_queue_reorder_confirmation,
        )
        message.stop()

    @on(ConfigPanel.EditRequested)
    def _handle_config_edit_requested(self, message: ConfigPanel.EditRequested) -> None:
        self._open_config_edit_modal(message.field_key)
        message.stop()

    @on(ConfigPanel.ReloadRequested)
    def _handle_config_reload_requested(self, message: ConfigPanel.ReloadRequested) -> None:
        self._request_config_reload()
        message.stop()

    @on(ResearchPanel.InterviewRequested)
    def _handle_research_interview_requested(self, message: ResearchPanel.InterviewRequested) -> None:
        self.app.push_screen(InterviewModal(question=message.question), self._handle_interview_resolution_request)
        message.stop()

    @on(PublishPanel.PreflightRequested)
    def _handle_publish_preflight_requested(self, message: PublishPanel.PreflightRequested) -> None:
        self._start_publish_refresh()
        message.stop()

    @on(PublishPanel.SyncRequested)
    def _handle_publish_sync_requested(self, message: PublishPanel.SyncRequested) -> None:
        self._request_publish_sync()
        message.stop()

    @on(PublishPanel.CommitRequested)
    def _handle_publish_commit_requested(self, message: PublishPanel.CommitRequested) -> None:
        self._confirm_publish_commit(push=message.push)
        message.stop()

    def _request_config_reload(self) -> None:
        self._run_gateway_action(
            "reload_config",
            lambda: RuntimeGateway(self.config_path).reload_config(),
        )

    def _open_config_edit_modal(self, field_key: str | None = None) -> None:
        field = selected_config_field(
            self._store.state.config,
            selected_key=self.query_one(ConfigPanel).selected_field_key,
            field_key=field_key,
        )
        if field is None:
            self.app.notify(
                "No guided config field is currently available for editing.",
                title="Config Edit Unavailable",
                severity="warning",
            )
            return
        runtime = self._store.state.runtime
        self.app.push_screen(
            ConfigEditModal(
                field=field,
                daemon_running=bool(runtime.process_running) if runtime is not None else False,
            ),
            self._handle_config_edit_request,
        )

    def _handle_config_edit_request(self, request: ConfigEditRequest | None) -> None:
        if request is None:
            return
        self._run_gateway_action(
            "set_config",
            lambda: RuntimeGateway(self.config_path).set_config(request.key, request.value),
        )

    def _handle_interview_resolution_request(self, request: InterviewResolutionRequest | None) -> None:
        if request is None:
            return
        if request.action == "answer":
            self._run_gateway_action(
                "interview_answer",
                lambda: RuntimeGateway(self.config_path).answer_interview(
                    request.question_id,
                    text=request.answer_text or "",
                ),
            )
            return
        if request.action == "accept":
            self._run_gateway_action(
                "interview_accept",
                lambda: RuntimeGateway(self.config_path).accept_interview(request.question_id),
            )
            return
        if request.action == "skip":
            self._run_gateway_action(
                "interview_skip",
                lambda: RuntimeGateway(self.config_path).skip_interview(
                    request.question_id,
                    reason=request.skip_reason,
                ),
            )

    def _request_publish_sync(self) -> None:
        self._run_gateway_action(
            "publish_sync",
            lambda: RuntimeGateway(self.config_path).publish_sync(),
        )

    def _confirm_publish_commit(self, *, push: bool) -> None:
        publish = self._store.state.publish
        if publish is None:
            self._start_publish_refresh()
            self.app.notify(
                "Publish preflight is loading. Review it before confirming a commit action.",
                title="Publish Preflight",
                severity="warning",
            )
            return
        self._pending_publish_push = push
        self.app.push_screen(
            ConfirmModal(
                title=("Commit And Push" if push else "Commit Without Push"),
                body_lines=publish_confirmation_lines(publish, push=push),
                confirm_label=("Commit And Push" if push else "Commit Locally"),
            ),
            self._handle_publish_commit_confirmation,
        )

    def _handle_publish_commit_confirmation(self, confirmed: bool) -> None:
        push = self._pending_publish_push
        self._pending_publish_push = None
        if not confirmed or push is None:
            return
        self._run_gateway_action(
            "publish_commit",
            lambda: RuntimeGateway(self.config_path).publish_commit(push=push),
        )

    def _notify_if_added(self, previous_notices: tuple) -> None:
        current_notices = self._store.state.notices
        if current_notices == previous_notices or not current_notices:
            return
        latest = current_notices[-1]
        self.app.notify(latest.message, title=latest.title, severity=notification_severity(latest))

    def _handle_queue_reorder_confirmation(self, confirmed: bool) -> None:
        task_ids = self._pending_queue_reorder
        self._pending_queue_reorder = None
        if not confirmed or not task_ids:
            return
        self._run_gateway_action(
            "reorder_queue",
            lambda: RuntimeGateway(self.config_path).reorder_queue(task_ids),
        )

    def _open_run_workflow(self, run_id: str) -> None:
        normalized_run_id = " ".join(run_id.strip().split())
        if not normalized_run_id:
            return
        self._requested_run_id = normalized_run_id
        self.open_panel(PanelId.RUNS)
        self.app.push_screen(RunDetailModal(config_path=self.config_path, run_id=normalized_run_id))

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
        self._shell_body_mode = mode
        if self.is_mounted:
            self._sync_panel_state()
            if mode is ShellBodyMode.EXPANDED:
                self.focus_content()
            elif previous_mode is ShellBodyMode.EXPANDED and self.focused is not None:
                if self.focused.id == EXPANDED_STREAM_WIDGET_ID:
                    self.focus_content()

    def _render_state(self) -> None:
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
        self.query_one(OverviewPanel).show_snapshot(
            runtime=state.runtime,
            queue=state.queue,
            research=state.research,
            latest_run=self._latest_run_summary,
            failure=self._store.panel_failure(PanelId.OVERVIEW),
            display_mode=state.display_mode,
        )
        self.query_one(QueuePanel).show_snapshot(
            state.queue,
            run_id=(state.runtime.selection.run_id if state.runtime is not None else None),
            failure=self._store.panel_failure(PanelId.QUEUE),
            display_mode=state.display_mode,
        )
        self.query_one(RunsPanel).show_snapshot(
            state.runs,
            requested_run_id=self._requested_run_id,
            failure=self._store.panel_failure(PanelId.RUNS),
            display_mode=state.display_mode,
        )
        self.query_one(ResearchPanel).show_snapshot(
            state.research,
            failure=self._store.panel_failure(PanelId.RESEARCH),
            display_mode=state.display_mode,
        )
        self.query_one(LogsPanel).show_snapshot(
            state.events,
            failure=self._store.panel_failure(PanelId.LOGS),
            display_mode=state.display_mode,
        )
        self.query_one(ConfigPanel).show_snapshot(
            state.config,
            runtime=state.runtime,
            failure=self._store.panel_failure(PanelId.CONFIG),
            display_mode=state.display_mode,
        )
        self.query_one(PublishPanel).show_snapshot(
            state.publish,
            failure=self._store.panel_failure(PanelId.PUBLISH),
            display_mode=state.display_mode,
        )
        self.query_one(ExpandedStreamView).show_snapshot(
            active_panel_label=active.label,
            display_mode=state.display_mode,
            events=state.events,
            live=self._shell_body_mode is ShellBodyMode.EXPANDED,
        )
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


__all__ = ["ShellScreen"]
