"""Application entry surface for the Millrace Textual shell."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from textual.app import App, ScreenStackError, SystemCommand
from textual.screen import Screen

from ..health import WorkspaceHealthReport
from .bindings import APP_BINDINGS
from .models import PANEL_BY_ID, PANELS, PanelId
from .screens.health_gate import HealthGateScreen
from .screens.shell import ShellScreen
from .widgets.logs_panel import LogsPanel
from .widgets.queue_panel import QueuePanel
from .widgets.research_panel import ResearchPanel
from .widgets.runs_panel import RunsPanel
from .workers import WorkerSettings


class MillraceTUIApplication(App[None]):
    """Top-level Textual app that hosts the persistent shell screen."""

    TITLE = "Millrace TUI"
    CSS_PATH = ["styles/app.tcss", "styles/shell.tcss", "styles/panels.tcss"]
    BINDINGS = APP_BINDINGS

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
        self.worker_settings = worker_settings or WorkerSettings()
        self.offer_startup_daemon_launch = offer_startup_daemon_launch
        self.sub_title = workspace_path.as_posix()
        self._pending_panel: PanelId | None = None
        self._shell_screen = ShellScreen(
            config_path=config_path,
            workspace_path=workspace_path,
            worker_settings=self.worker_settings,
            offer_startup_daemon_launch=offer_startup_daemon_launch,
        )
        self._health_gate_screen = HealthGateScreen(config_path=config_path, workspace_path=workspace_path)

    @classmethod
    def from_config_path(
        cls,
        config_path: Path,
        *,
        worker_settings: WorkerSettings | None = None,
        offer_startup_daemon_launch: bool = True,
    ) -> "MillraceTUIApplication":
        resolved_config = config_path.expanduser()
        if not resolved_config.is_absolute():
            resolved_config = resolved_config.resolve()
        return cls(
            config_path=resolved_config,
            workspace_path=resolved_config.parent,
            worker_settings=worker_settings,
            offer_startup_daemon_launch=offer_startup_daemon_launch,
        )

    def on_mount(self) -> None:
        self.install_screen(self._shell_screen, name="shell")
        self.install_screen(self._health_gate_screen, name="health-gate")
        self.push_screen("health-gate")

    async def on_event(self, event) -> None:
        try:
            await super().on_event(event)
        except ScreenStackError:
            if not self._screen_stack:
                return
            raise

    def enter_shell(self, report: WorkspaceHealthReport) -> None:
        self._shell_screen.prime_bootstrap_health(report)
        if self._pending_panel is not None:
            self._shell_screen.active_panel = self._pending_panel
        self.switch_screen("shell")

    def shell_screen(self) -> ShellScreen:
        return self._shell_screen

    def open_panel(self, panel_id: PanelId) -> None:
        if not self._shell_screen.is_mounted:
            self._pending_panel = panel_id
            return
        self._pending_panel = None
        self._shell_screen.open_panel(panel_id)

    def action_open_panel(self, panel_id: str) -> None:
        self.open_panel(PanelId(panel_id))

    def action_focus_sidebar(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.focus_sidebar()

    def action_focus_content(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.focus_content()

    def action_focus_next(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_focus_next()

    def action_focus_previous(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_focus_previous()

    def action_toggle_display_mode(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_toggle_display_mode()

    def action_toggle_expanded_mode(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_toggle_expanded_mode()

    def action_exit_expanded_mode(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_exit_expanded_mode()

    def action_jump_expanded_stream_live(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_jump_expanded_stream_live()

    def action_start_once(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_start_once()

    def action_start_daemon(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_start_daemon()

    def action_pause_runtime(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_pause_runtime()

    def action_resume_runtime(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_resume_runtime()

    def action_stop_runtime(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_stop_runtime()

    def action_edit_selected_config(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_edit_selected_config()

    def action_reload_config(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_reload_config()

    def action_refresh_publish_preflight(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_refresh_publish_preflight()

    def action_open_help(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_open_help()

    def action_publish_sync(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_publish_sync()

    def action_publish_commit(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_publish_commit()

    def action_publish_push(self) -> None:
        if isinstance(self.screen, ShellScreen):
            self._shell_screen.action_publish_push()

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        if not isinstance(self.screen, ShellScreen):
            return
        yield SystemCommand(
            "Start Once",
            "Run one foreground cycle now.",
            self.action_start_once,
        )
        yield SystemCommand(
            "Start Daemon",
            "Launch daemon mode and keep the shell attached.",
            self.action_start_daemon,
        )
        yield SystemCommand(
            "Pause Runtime",
            "Queue a pause request for the running daemon.",
            self.action_pause_runtime,
        )
        yield SystemCommand(
            "Resume Runtime",
            "Queue a resume request for the running daemon.",
            self.action_resume_runtime,
        )
        yield SystemCommand(
            "Stop Runtime",
            "Queue a stop request for the running daemon.",
            self.action_stop_runtime,
        )
        yield SystemCommand(
            "Edit Config Field",
            "Edit the selected config field with guided validation.",
            self.action_edit_selected_config,
        )
        yield SystemCommand(
            "Reload Config",
            "Reload config from disk.",
            self.action_reload_config,
        )
        yield SystemCommand(
            "Publish Preflight",
            "Refresh read-only staging preflight facts.",
            self.action_refresh_publish_preflight,
        )
        yield SystemCommand(
            "Publish Sync",
            "Sync manifest-selected paths into staging.",
            self.action_publish_sync,
        )
        yield SystemCommand(
            "Publish Commit (No Push)",
            "Create a local staging commit without push.",
            self.action_publish_commit,
        )
        yield SystemCommand(
            "Publish Commit And Push",
            "Create a staging commit and push to origin.",
            self.action_publish_push,
        )
        for panel in PANELS:
            yield SystemCommand(
                f"Open {panel.label}",
                panel.palette_help,
                lambda panel_id=panel.id: self.open_panel(panel_id),
            )
        yield SystemCommand(
            "Toggle Display Mode",
            "Switch between operator and debug views for this session.",
            self.action_toggle_display_mode,
        )
        yield SystemCommand(
            "Toggle Expanded Mode",
            "Replace the main content area with the expanded stream surface.",
            self.action_toggle_expanded_mode,
        )
        yield SystemCommand(
            "Exit Expanded Mode",
            "Return from the expanded stream surface to the active compact panel.",
            self.action_exit_expanded_mode,
        )
        yield SystemCommand(
            "Jump Expanded Stream To Live",
            "Pin the expanded stream back to the newest runtime lines.",
            self.action_jump_expanded_stream_live,
        )
        yield SystemCommand("Focus Sidebar", "Move focus to the left sidebar navigation.", self.action_focus_sidebar)
        yield SystemCommand("Focus Workspace", "Move focus to the active panel workspace.", self.action_focus_content)
        yield SystemCommand(
            "Focus Next Region",
            "Cycle focus between the sidebar and workspace regions.",
            self.action_focus_next,
        )
        yield SystemCommand(
            "Focus Previous Region",
            "Cycle focus backward between the sidebar and workspace regions.",
            self.action_focus_previous,
        )
        yield SystemCommand(
            "Open Keyboard Help",
            "Show shortcuts plus operator/debug guidance for the active panel.",
            self.action_open_help,
        )
        if self._shell_screen.active_panel is PanelId.QUEUE:
            queue_panel = self._shell_screen.query_one(QueuePanel)
            if queue_panel.reorder_mode:
                yield SystemCommand(
                    "Review Queue Reorder",
                    "Review the staged queue reorder for the selected task.",
                    queue_panel.action_submit_selection,
                )
                yield SystemCommand(
                    "Cancel Queue Reorder",
                    "Cancel the current queue reorder draft.",
                    queue_panel.action_cancel_reorder,
                )
            else:
                yield SystemCommand(
                    "Start Queue Reorder",
                    "Begin a reorder draft for the selected queue task.",
                    queue_panel.action_begin_reorder,
                )
                if queue_panel.selected_task_id is not None:
                    yield SystemCommand(
                        "Quarantine Selected Queue Task",
                        "Safely quarantine the selected queued task after confirmation.",
                        queue_panel.action_quarantine_selected,
                    )
                    yield SystemCommand(
                        "Remove Selected Queue Task",
                        "Safely remove the selected queued task after confirmation.",
                        queue_panel.action_remove_selected,
                    )
            if self._shell_screen._store.state.runtime is not None and self._shell_screen._store.state.runtime.selection.run_id:
                yield SystemCommand(
                    "Open Active Queue Run Detail",
                    "Open concise run detail for the queue's active run context.",
                    queue_panel.action_open_run_detail,
                )
        elif self._shell_screen.active_panel is PanelId.RUNS:
            runs_panel = self._shell_screen.query_one(RunsPanel)
            yield SystemCommand(
                "Open Selected Run Detail",
                "Open concise detail for the selected run.",
                runs_panel.action_submit_selection,
            )
        elif self._shell_screen.active_panel is PanelId.RESEARCH:
            research_panel = self._shell_screen.query_one(ResearchPanel)
            if research_panel.selected_question_id is not None:
                yield SystemCommand(
                    "Open Selected Interview",
                    "Open the selected interview workflow.",
                    research_panel.action_open_interview,
                )
        elif self._shell_screen.active_panel is PanelId.LOGS:
            logs_panel = self._shell_screen.query_one(LogsPanel)
            yield SystemCommand(
                "Freeze Or Resume Live Logs",
                "Toggle between live follow and frozen scrollback.",
                logs_panel.action_toggle_follow,
            )
            yield SystemCommand(
                "Switch Logs Focus Surface",
                "Move between runtime events and artifact browsing.",
                logs_panel.action_toggle_focus_surface,
            )
            if logs_panel.selected_run_id is not None:
                yield SystemCommand(
                    "Open Selected Log Run Detail",
                    "Open concise run detail for the run attached to the selected log event.",
                    logs_panel.action_submit_selection,
                )
        active = PANEL_BY_ID[self._shell_screen.active_panel]
        yield SystemCommand(
            "Open Active Panel",
            f"Reopen {active.label} while staying in the current context.",
            lambda: self.open_panel(active.id),
        )
