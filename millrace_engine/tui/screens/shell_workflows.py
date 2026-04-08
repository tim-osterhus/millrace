"""Workflow and action orchestration helpers for the persistent shell."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from textual import on

from ..models import GatewayResult, PanelId
from ..widgets.config_panel import ConfigPanel
from ..widgets.logs_panel import LogsPanel
from ..widgets.publish_panel import PublishPanel
from ..widgets.queue_panel import QueuePanel
from ..widgets.research_panel import ResearchPanel
from ..widgets.runs_panel import RunsPanel
from .add_idea_modal import AddIdeaModal, AddIdeaRequest
from .add_task_modal import AddTaskModal, AddTaskRequest
from .config_edit_modal import ConfigEditModal, ConfigEditRequest
from .confirm_modal import ConfirmModal
from .help_modal import HelpModal
from .interview_modal import InterviewModal, InterviewResolutionRequest
from .run_detail_modal import RunDetailModal
from .shell_support import (
    ACTION_WORKER_GROUP,
    ACTION_WORKER_PREFIX,
    LIFECYCLE_WORKER_GROUP,
    LIFECYCLE_WORKER_PREFIX,
    notification_severity,
    publish_confirmation_lines,
    queue_reorder_confirmation_lines,
    selected_config_field,
)

if TYPE_CHECKING:
    from textual.app import App

    from ..models import GatewayFailure
    from ..store import TUIStore


class ShellWorkflowMixin:
    """Owns shell action wiring, confirmation flows, and gateway dispatch."""

    config_path: object
    workspace_path: object
    app: App
    _store: TUIStore
    _startup_prompt_window_open: bool
    _pending_queue_reorder: tuple[str, ...] | None
    _pending_publish_push: bool | None
    _requested_run_id: str | None
    _lifecycle_busy_message: str | None
    _lifecycle_worker_name: str | None
    _last_lifecycle_failure: GatewayFailure | None

    def _runtime_gateway(self): ...

    def _launch_start_once_request(self): ...

    def _launch_start_daemon_request(self): ...

    def _start_publish_refresh(self) -> None: ...

    def _render_state(self) -> None: ...

    def open_panel(self, panel_id: PanelId) -> None: ...

    def action_open_add_task(self) -> None:
        self.app.push_screen(AddTaskModal(), self._handle_add_task_request)

    def action_open_add_idea(self) -> None:
        self.app.push_screen(
            AddIdeaModal(workspace_path=self.workspace_path),
            self._handle_add_idea_request,
        )

    def action_open_help(self) -> None:
        self.app.push_screen(HelpModal(active_panel=self.active_panel))

    def action_start_once(self) -> None:
        self._run_lifecycle_action(
            "start_once",
            busy_message="foreground once run in progress",
            work_factory=self._launch_start_once_request,
            thread=False,
        )

    def action_start_daemon(self) -> None:
        self._run_lifecycle_action(
            "start_daemon",
            busy_message="detached daemon launch in progress",
            work_factory=self._launch_start_daemon_request,
            thread=False,
        )

    def action_pause_runtime(self) -> None:
        self._run_lifecycle_action(
            "pause",
            busy_message="pause request in progress",
            work_factory=lambda: self._runtime_gateway().pause_runtime,
            thread=True,
        )

    def action_resume_runtime(self) -> None:
        self._run_lifecycle_action(
            "resume",
            busy_message="resume request in progress",
            work_factory=lambda: self._runtime_gateway().resume_runtime,
            thread=True,
        )

    def action_stop_runtime(self) -> None:
        self._run_lifecycle_action(
            "stop",
            busy_message="stop request in progress",
            work_factory=lambda: self._runtime_gateway().stop_runtime,
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
            lambda: self._runtime_gateway().add_task(
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
            lambda: self._runtime_gateway().add_idea(request.source_path),
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
        self.app.push_screen(
            InterviewModal(question=message.question),
            self._handle_interview_resolution_request,
        )
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
            lambda: self._runtime_gateway().reload_config(),
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
            lambda: self._runtime_gateway().set_config(request.key, request.value),
        )

    def _handle_interview_resolution_request(self, request: InterviewResolutionRequest | None) -> None:
        if request is None:
            return
        if request.action == "answer":
            self._run_gateway_action(
                "interview_answer",
                lambda: self._runtime_gateway().answer_interview(
                    request.question_id,
                    text=request.answer_text or "",
                ),
            )
            return
        if request.action == "accept":
            self._run_gateway_action(
                "interview_accept",
                lambda: self._runtime_gateway().accept_interview(request.question_id),
            )
            return
        if request.action == "skip":
            self._run_gateway_action(
                "interview_skip",
                lambda: self._runtime_gateway().skip_interview(
                    request.question_id,
                    reason=request.skip_reason,
                ),
            )

    def _request_publish_sync(self) -> None:
        self._run_gateway_action(
            "publish_sync",
            lambda: self._runtime_gateway().publish_sync(),
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
            lambda: self._runtime_gateway().publish_commit(push=push),
        )

    def _notify_if_added(self, previous_notices: tuple) -> None:
        current_notices = self._store.state.notices
        if current_notices == previous_notices or not current_notices:
            return
        latest = current_notices[-1]
        self.app.notify(
            latest.message,
            title=latest.title,
            severity=notification_severity(latest),
        )

    def _handle_queue_reorder_confirmation(self, confirmed: bool) -> None:
        task_ids = self._pending_queue_reorder
        self._pending_queue_reorder = None
        if not confirmed or not task_ids:
            return
        self._run_gateway_action(
            "reorder_queue",
            lambda: self._runtime_gateway().reorder_queue(task_ids),
        )

    def _open_run_workflow(self, run_id: str) -> None:
        normalized_run_id = " ".join(run_id.strip().split())
        if not normalized_run_id:
            return
        self._requested_run_id = normalized_run_id
        self.open_panel(PanelId.RUNS)
        self.app.push_screen(
            RunDetailModal(config_path=self.config_path, run_id=normalized_run_id)
        )


__all__ = ["ShellWorkflowMixin"]
