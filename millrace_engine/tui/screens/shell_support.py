"""Pure helper seams for the persistent shell screen."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.worker import WorkerState

from ..formatting import format_timestamp
from ..messages import ActionFailed, ActionSucceeded, RefreshFailed, RefreshSucceeded
from ..models import (
    ConfigFieldView,
    ConfigOverviewView,
    DisplayMode,
    GatewayFailure,
    GatewayResult,
    InterviewQuestionSummaryView,
    PanelDefinition,
    NoticeView,
    PanelId,
    PublishOverviewView,
    QueueOverviewView,
    QueueTaskView,
    ResearchOverviewView,
    RunSummaryView,
    RunsOverviewView,
    RuntimeEventView,
    RuntimeOverviewView,
)
from ..publish_support import (
    publish_commit_block_reason,
    publish_push_block_reason,
    publish_push_ready,
    publish_safe_next_step_detail,
    publish_safe_next_step_headline,
    publish_skip_reason_copy,
    publish_status_copy,
)
from ..widgets.overview_panel import LatestRunSummary
from ..widgets.shell_inspector import ShellInspectorAction, ShellInspectorView
from ..workers import (
    INITIAL_REFRESH_WORKER_NAME,
    PERIODIC_REFRESH_WORKER_NAME,
    gateway_failure_from_exception,
)

WORKSPACE_REFRESH_PANELS = (
    PanelId.OVERVIEW,
    PanelId.QUEUE,
    PanelId.RUNS,
    PanelId.RESEARCH,
    PanelId.CONFIG,
)
INITIAL_REFRESH_PANELS = WORKSPACE_REFRESH_PANELS + (PanelId.LOGS,)
ACTION_WORKER_GROUP = "actions"
ACTION_WORKER_PREFIX = "action."
LIFECYCLE_WORKER_GROUP = "lifecycle"
LIFECYCLE_WORKER_PREFIX = "lifecycle."
PUBLISH_REFRESH_WORKER_NAME = "refresh.publish"
PUBLISH_REFRESH_WORKER_GROUP = "refresh.publish"
TARGETED_REFRESH_WORKER_PREFIX = "refresh.targeted."
LIFECYCLE_ACTION_NAMES = {
    "start_once",
    "start_daemon",
    "start.once",
    "start.daemon",
    "pause",
    "resume",
    "stop",
}
ACTION_REFRESH_PANELS: dict[str, tuple[PanelId, ...]] = {
    "add_task": (PanelId.OVERVIEW, PanelId.QUEUE),
    "add_idea": (PanelId.OVERVIEW, PanelId.RESEARCH),
    "reorder_queue": (PanelId.OVERVIEW, PanelId.QUEUE),
    "queue_cleanup_remove": (PanelId.OVERVIEW, PanelId.QUEUE),
    "queue_cleanup_quarantine": (PanelId.OVERVIEW, PanelId.QUEUE),
    "interview_answer": (PanelId.OVERVIEW, PanelId.RESEARCH),
    "interview_accept": (PanelId.OVERVIEW, PanelId.RESEARCH),
    "interview_skip": (PanelId.OVERVIEW, PanelId.RESEARCH),
    "reload_config": (PanelId.OVERVIEW, PanelId.CONFIG),
    "set_config": (PanelId.OVERVIEW, PanelId.CONFIG),
    "pause": WORKSPACE_REFRESH_PANELS,
    "resume": WORKSPACE_REFRESH_PANELS,
    "stop": WORKSPACE_REFRESH_PANELS,
    "start_once": WORKSPACE_REFRESH_PANELS,
    "start_daemon": WORKSPACE_REFRESH_PANELS,
    "start.once": WORKSPACE_REFRESH_PANELS,
    "start.daemon": WORKSPACE_REFRESH_PANELS,
}


@dataclass(frozen=True, slots=True)
class WorkerStateOutcome:
    message: ActionSucceeded | ActionFailed | RefreshSucceeded | RefreshFailed | None = None
    clear_lifecycle_busy: bool = False
    ensure_background_runtime: bool = False


def build_shell_inspector_view(
    *,
    active_panel: PanelDefinition,
    display_mode: DisplayMode,
    expanded_mode: bool,
    runtime: RuntimeOverviewView | None,
    queue: QueueOverviewView | None,
    runs: RunsOverviewView | None,
    research: ResearchOverviewView | None,
    config: ConfigOverviewView | None,
    publish: PublishOverviewView | None,
    latest_run: LatestRunSummary | None,
    panel_failure: GatewayFailure | None,
    selected_task_id: str | None = None,
    queue_reorder_mode: bool = False,
    selected_run_id: str | None = None,
    selected_event: RuntimeEventView | None = None,
    selected_log_artifact_path: str | None = None,
    log_artifact_root: str | None = None,
    logs_focus_surface: str | None = None,
    selected_question_id: str | None = None,
    selected_config_field_key: str | None = None,
    selected_publish_path: str | None = None,
) -> ShellInspectorView:
    title = active_panel.label
    headline = "waiting for the first workspace snapshot"
    detail_lines: tuple[str, ...] = ()
    primary_action: ShellInspectorAction | None = None
    action_lines: tuple[ShellInspectorAction, ...] = ()
    collapsed = False

    if active_panel.id is PanelId.OVERVIEW:
        if runtime is not None:
            daemon_state = "running" if runtime.process_running else "stopped"
            headline = f"daemon {daemon_state} | exec {runtime.execution_status.lower()}"
            detail = [
                f"research {runtime.research_status.lower()} | backlog {runtime.backlog_depth}",
            ]
            if runtime.sentinel is not None:
                sentinel = runtime.sentinel
                sentinel_status = "unavailable"
                if sentinel.available:
                    sentinel_status = (sentinel.status or "unknown").lower()
                    if sentinel.monitoring_active:
                        sentinel_status = f"{sentinel_status} | monitoring {sentinel.route_target}"
                    elif sentinel.hard_cap_triggered:
                        sentinel_status = f"{sentinel_status} | hard cap"
                    elif sentinel.soft_cap_active:
                        sentinel_status = f"{sentinel_status} | soft cap"
                elif not sentinel.config_enabled:
                    sentinel_status = "disabled by config"
                detail.append(f"sentinel {sentinel_status}")
                if sentinel.available:
                    sentinel_detail = [f"checks {sentinel.checks_performed}"]
                    if sentinel.recovery_cycles_queued > 0:
                        sentinel_detail.append(f"recovery {sentinel.recovery_cycles_queued}")
                    if sentinel.acknowledgment_required:
                        sentinel_detail.append("ack required")
                    if sentinel.last_notification_status:
                        sentinel_detail.append(f"notify {sentinel.last_notification_status.lower()}")
                    detail.append(" | ".join(sentinel_detail))
            if latest_run is not None:
                detail.append(f"latest run {latest_run.run_id} | status {latest_run.latest_status or 'unknown'}")
            if runtime.selection.selection_ref:
                detail.append(f"selection {runtime.selection.selection_ref}")
            detail_lines = tuple(detail)
            collapsed = True
    elif active_panel.id is PanelId.QUEUE:
        selected = _queue_task_by_id(queue, selected_task_id)
        if queue is None:
            headline = "queue snapshot not loaded"
        elif selected is not None:
            title = selected.title
            position = _queue_task_position(queue, selected.task_id)
            lane = "next up" if queue.next_task is not None and queue.next_task.task_id == selected.task_id else "queued"
            headline = f"{lane} | task {selected.task_id}"
            cleanup_mode = (
                "cleanup and reorder queue through the mailbox first"
                if runtime is not None and runtime.process_running
                else "cleanup and reorder apply directly"
            )
            detail = [f"backlog depth {queue.backlog_depth} | position {position or '?'}"]
            detail.append(f"spec {selected.spec_id or 'none'}")
            detail.append(cleanup_mode)
            if queue_reorder_mode:
                detail.append("cleanup is paused until the active reorder draft is confirmed or cancelled")
                primary_action = _inspector_action(
                    "Enter",
                    "Review reorder",
                    "Confirm the staged queue order after you finish moving the selected task.",
                )
                action_lines = (
                    _inspector_action("[ / ]", "Move task", "Shift the selected task earlier or later inside the draft."),
                    _inspector_action("Esc", "Cancel draft", "Drop the staged reorder and return to normal queue actions."),
                )
            else:
                primary_action = _inspector_action(
                    "Enter / R",
                    "Start reorder",
                    "Stage a queue reorder draft for the selected task.",
                )
                action_lines = (
                    _inspector_action(
                        "Q",
                        "Quarantine",
                        "Quarantine the selected task after confirmation through the supported cleanup path.",
                    ),
                    _inspector_action(
                        "X",
                        "Remove",
                        "Remove the selected task after confirmation through the supported cleanup path.",
                    ),
                )
            if runtime is not None and runtime.selection.run_id:
                action_lines = (
                    *action_lines,
                    _inspector_action("O", "Run detail", "Open concise detail for the queue's current active run context."),
                )
            detail_lines = tuple(detail)
        else:
            headline = f"backlog {queue.backlog_depth}"
            detail_lines = ("No backlog item selected yet.",)
            primary_action = _inspector_action(
                "Up/Down",
                "Choose task",
                "Move the queue cursor to unlock reorder, cleanup, and run-detail actions.",
            )
    elif active_panel.id is PanelId.RUNS:
        selected = _run_by_id(runs, selected_run_id)
        if runs is None:
            headline = "recent runs are not loaded yet"
        elif selected is not None:
            title = selected.run_id
            headline = f"{selected.latest_status or 'unknown'} | {selected.latest_transition_label or 'no transition'}"
            detail = []
            if selected.selection_ref:
                detail.append(f"selection {selected.selection_ref}")
            if selected.note:
                detail.append(selected.note)
            if selected.issue:
                detail.append(selected.issue)
            detail_lines = tuple(detail or ("Press Enter to open concise run detail.",))
            primary_action = _inspector_action(
                "Enter",
                "Open run detail",
                "Inspect concise frozen-plan, transition, and provenance detail for the selected run.",
            )
            action_lines = (_inspector_action("Up/Down", "Change run", "Move through recent runs without losing selection."),)
        else:
            headline = "no run selected"
            detail_lines = ("The recent-runs list is empty.",)
    elif active_panel.id is PanelId.RESEARCH:
        selected = _research_question_by_id(research, selected_question_id)
        if research is None:
            headline = "research snapshot not loaded"
        elif selected is not None:
            title = selected.title or selected.question_id
            headline = f"{selected.spec_id} | {selected.status}"
            detail = [selected.question or "pending interview question"]
            if selected.why_this_matters:
                detail.append(selected.why_this_matters)
            detail_lines = tuple(detail)
            primary_action = _inspector_action(
                "Enter",
                "Open interview",
                "Answer, accept, or skip the selected research question through the guided workflow.",
            )
            action_lines = (
                _inspector_action("Up/Down", "Change question", "Move through pending questions and keep interview context visible."),
            )
        else:
            headline = f"{research.status} | mode {research.current_mode}"
            detail_lines = (f"selected family {research.selected_family or 'none'}",)
            primary_action = _inspector_action(
                "Up/Down",
                "Choose question",
                "Move to a pending interview question to unlock the research workflow.",
            )
    elif active_panel.id is PanelId.LOGS:
        if selected_event is not None:
            title = selected_event.event_type
            headline = f"{selected_event.source} | {selected_event.category or 'event'}"
            detail = [selected_event.summary or selected_event.event_type]
            if selected_event.run_id:
                detail.append(f"run {selected_event.run_id}")
            if selected_event.payload:
                detail.append(
                    "payload " + ", ".join(f"{item.key}={item.value}" for item in selected_event.payload[:4])
                )
            if log_artifact_root:
                detail.append(f"artifacts {Path(log_artifact_root).name or log_artifact_root}")
            if selected_log_artifact_path:
                detail.append(f"selected artifact {selected_log_artifact_path}")
            detail_lines = tuple(detail)
        elif display_mode is DisplayMode.DEBUG:
            headline = "debug log view"
            detail_lines = ("Expanded payload detail stays in the workspace surface.",)
        else:
            headline = "live log stream"
            detail_lines = ("Focus content to select an event and populate this inspector.",)
        if expanded_mode:
            primary_action = _inspector_action(
                "Esc",
                "Return to panel",
                "Leave expanded stream mode and go back to the active compact panel.",
            )
            action_lines = (_inspector_action("L", "Jump live", "Pin the expanded stream back to the newest runtime lines."),)
        elif selected_event is not None and selected_event.run_id:
            primary_action = _inspector_action(
                "Enter",
                "Open run detail",
                "Inspect concise run detail for the run attached to the selected log event.",
            )
            action_lines = (
                _inspector_action("Tab", "Events or artifacts", "Switch between event focus and artifact browsing."),
                _inspector_action(
                    "F",
                    "Freeze live" if selected_event is not None else "Toggle follow",
                    "Leave the live tail when you need to inspect older runtime output.",
                ),
            )
        else:
            primary_action = _inspector_action(
                "Tab",
                "Switch surface",
                f"Move between events and artifacts while {logs_focus_surface or 'events'} stays active.",
            )
            action_lines = (
                _inspector_action(
                    "F",
                    "Freeze live",
                    "Pause live-follow when you need to inspect older runtime lines without jumping back.",
                ),
            )
    elif active_panel.id is PanelId.CONFIG:
        selected = selected_config_field(config, selected_key=selected_config_field_key)
        if config is None:
            headline = "config snapshot not loaded"
        elif selected is not None:
            title = selected.label
            headline = f"{selected.value} | {selected.boundary.lower()}"
            detail = [selected.description]
            if selected.editable:
                detail.append("editable through the guided config modal")
            detail_lines = tuple(detail)
            primary_action = _inspector_action(
                "Enter / E",
                "Edit field",
                "Open guided validation and apply the selected config change through the supported control path.",
            )
            action_lines = (
                _inspector_action("R", "Reload config", "Refresh runtime config from disk through the supported control path."),
                _inspector_action("Up/Down", "Change field", "Move through editable fields without leaving the panel."),
            )
        else:
            headline = f"{config.source_kind} | guided edits unavailable"
            detail_lines = (config.editing_disabled_reason or "no editable fields are visible",)
    elif active_panel.id is PanelId.PUBLISH:
        if publish is None:
            headline = "publish preflight not loaded"
        else:
            commit_blocked = publish_commit_block_reason(publish)
            push_blocked = publish_push_block_reason(publish)
            if selected_publish_path is not None:
                title = selected_publish_path
                headline = f"{publish_status_copy(publish)} | changed path"
                detail = [
                    f"resolved staging repo {publish.staging_repo_dir}",
                    f"branch {publish.branch or 'detached'} | origin {'configured' if publish.origin_configured else 'missing'}",
                    (
                        "tracked by the staging manifest"
                        if selected_publish_path in publish.selected_paths
                        else "changed outside the current manifest selection"
                    ),
                ]
                detail_lines = tuple(detail)
            else:
                headline = f"{publish_status_copy(publish)} | changed {len(publish.changed_paths)}"
                detail_lines = (
                    f"resolved staging repo {publish.staging_repo_dir}",
                    "Publish acts on staging, not the main workspace checkout directly.",
                )
            primary_action = _publish_primary_action(publish)
            action_lines = (_inspector_action("Up/Down", "Inspect paths", "Review changed staging paths and manifest coverage."),)
            if commit_blocked is not None:
                detail_lines = (*detail_lines, f"commit blocked by {commit_blocked}")
            elif push_blocked is not None:
                detail_lines = (
                    *detail_lines,
                    f"push blocked by {push_blocked}",
                    "local commit still stays available from this panel",
                )
            elif publish_push_ready(publish):
                detail_lines = (*detail_lines, "local commit is the safer default even though push looks ready")

    if panel_failure is not None:
        detail_lines = (*detail_lines, f"refresh degraded: {panel_failure.message}")
        collapsed = False
    return ShellInspectorView(
        panel_label=active_panel.label,
        title=title,
        headline=headline,
        detail_lines=detail_lines,
        primary_action=primary_action,
        action_lines=action_lines,
        collapsed=collapsed,
    )


def _inspector_action(key: str, label: str, detail: str) -> ShellInspectorAction:
    return ShellInspectorAction(key=key, label=label, detail=detail)


def _publish_primary_action(publish: PublishOverviewView) -> ShellInspectorAction:
    commit_blocked = publish_commit_block_reason(publish)
    push_blocked = publish_push_block_reason(publish)
    if commit_blocked == publish_skip_reason_copy("no_changes"):
        return _inspector_action(
            "G",
            "Sync staging",
            "Re-sync manifest-selected files into staging when you expected diffs but none are visible.",
        )
    if commit_blocked is not None:
        return _inspector_action(
            "R",
            "Refresh preflight",
            publish_safe_next_step_headline(publish),
        )
    if push_blocked is not None:
        return _inspector_action(
            "N",
            "Commit locally",
            "Create the safer local staging commit now and resolve push prerequisites before using P.",
        )
    return _inspector_action(
        "N",
        "Commit locally",
        "Create the safer local staging commit first; use P only for an intentional remote publish.",
    )


def _queue_task_by_id(queue: QueueOverviewView | None, task_id: str | None) -> QueueTaskView | None:
    if queue is None or task_id is None:
        return None
    for task in queue.backlog:
        if task.task_id == task_id:
            return task
    return None


def _queue_task_position(queue: QueueOverviewView | None, task_id: str | None) -> int | None:
    if queue is None or task_id is None:
        return None
    for index, task in enumerate(queue.backlog, start=1):
        if task.task_id == task_id:
            return index
    return None


QUEUE_CLEANUP_REASONS = {
    "remove": "Removed from queue via TUI queue board.",
    "quarantine": "Quarantined from queue via TUI queue board.",
}


def queue_cleanup_reason(cleanup_action: str) -> str:
    return QUEUE_CLEANUP_REASONS.get(cleanup_action, "Cleaned up from queue via TUI queue board.")


def _run_by_id(runs: RunsOverviewView | None, run_id: str | None) -> RunSummaryView | None:
    if runs is None or run_id is None:
        return None
    for run in runs.runs:
        if run.run_id == run_id:
            return run
    return None


def _research_question_by_id(
    research: ResearchOverviewView | None,
    question_id: str | None,
) -> InterviewQuestionSummaryView | None:
    if research is None or question_id is None:
        return None
    for question in research.interview_questions:
        if question.question_id == question_id:
            return question
    return None


def selected_config_field(
    config: ConfigOverviewView | None,
    *,
    selected_key: str | None,
    field_key: str | None = None,
) -> ConfigFieldView | None:
    if config is None:
        return None
    chosen_key = field_key or selected_key
    editable_fields = [field for field in config.fields if field.editable]
    if not editable_fields:
        return None
    for field in editable_fields:
        if field.key == chosen_key:
            return field
    return editable_fields[0]


def publish_confirmation_lines(publish: PublishOverviewView | None, *, push: bool) -> tuple[str, ...]:
    if publish is None:
        return ("Publish preflight is not loaded yet.",)
    commit_blocked = publish_commit_block_reason(publish)
    push_blocked = publish_push_block_reason(publish)
    push_ready = publish_push_ready(publish)
    lines = [
        (
            "Create a staging commit and push it to the configured origin?"
            if push
            else "Create a local staging commit without pushing?"
        ),
        "",
        f"Status: {publish_status_copy(publish)}",
        f"Staging repo: {publish.staging_repo_dir}",
        "Scope: this action operates on the resolved staging repo, not the main workspace checkout directly.",
        f"Branch: {publish.branch or 'detached'}",
        f"Origin configured: {'yes' if publish.origin_configured else 'no'}",
        f"Changed paths: {len(publish.changed_paths)}",
        f"Skip reason: {publish_skip_reason_copy(publish.skip_reason)}",
        f"Push ready from current facts: {'yes' if push_ready else 'no'}",
    ]
    if commit_blocked is not None:
        lines.append(f"Commit blocked by: {commit_blocked}")
    elif push_blocked is not None:
        lines.append(f"Push blocked by: {push_blocked}")
    if push:
        lines.extend(
            [
                "",
                "Push stays intentionally higher friction than the default local-commit path.",
                publish_safe_next_step_headline(publish),
                publish_safe_next_step_detail(publish),
            ]
        )
    else:
        lines.extend(
            [
                "",
                "This is the default safer publish path from the TUI.",
                publish_safe_next_step_detail(publish),
            ]
        )
    return tuple(lines)


def queue_reorder_confirmation_lines(
    task_ids: tuple[str, ...],
    *,
    queue: QueueOverviewView | None,
    runtime: RuntimeOverviewView | None,
) -> tuple[str, ...]:
    backlog_by_id = {task.task_id: task for task in queue.backlog} if queue is not None else {}
    lines = [
        "Apply this backlog order?",
        "",
        *[
            f"{index}. {backlog_by_id.get(task_id).title if task_id in backlog_by_id else task_id} [{task_id}]"
            for index, task_id in enumerate(task_ids, start=1)
        ],
    ]
    if runtime is not None and runtime.process_running:
        lines.extend(
            [
                "",
                "The daemon is running. This reorder will be queued to the mailbox first.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "This will rewrite the visible backlog order immediately.",
            ]
    )
    return tuple(lines)


def queue_cleanup_confirmation_lines(
    cleanup_action: str,
    *,
    task_id: str,
    queue: QueueOverviewView | None,
    runtime: RuntimeOverviewView | None,
) -> tuple[str, ...]:
    task = _queue_task_by_id(queue, task_id)
    position = _queue_task_position(queue, task_id)
    reason = queue_cleanup_reason(cleanup_action)
    action_label = "Remove" if cleanup_action == "remove" else "Quarantine"
    lines = [
        f"{action_label} this queued task?",
        "",
        (
            f"{task.title} [{task.task_id}]"
            if task is not None
            else task_id
        ),
        f"Position: {position if position is not None else 'unknown'}",
        f"Spec: {task.spec_id if task is not None and task.spec_id else 'none'}",
        f"Recorded reason: {reason}",
    ]
    if runtime is not None and runtime.process_running:
        lines.extend(
            [
                "",
                "The daemon is running. This cleanup will be queued to the mailbox first.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "This cleanup will be applied immediately through the supported control path.",
            ]
        )
    return tuple(lines)


def notification_severity(notice: NoticeView) -> str:
    if notice.level.value == "warning":
        return "warning"
    if notice.level.value == "error":
        return "error"
    return "information"


def latest_run_summary_from_runs(runs: RunsOverviewView | None) -> LatestRunSummary | None:
    if runs is None or not runs.runs:
        return None
    latest = runs.runs[0]
    if latest.issue is not None:
        return LatestRunSummary(run_id=latest.run_id, error=latest.issue)
    note = latest.note
    if note is None and not latest.history_present:
        note = "transition history not present"
    return LatestRunSummary(
        run_id=latest.run_id,
        compiled_at=(format_timestamp(latest.compiled_at) if latest.compiled_at is not None else None),
        selection_ref=latest.selection_ref,
        stage_count=latest.stage_count,
        latest_status=latest.latest_status,
        latest_transition_label=latest.latest_transition_label,
        history_present=latest.history_present,
        note=note,
    )


def is_lifecycle_action(name: str) -> bool:
    normalized = name.replace("action.", "").replace("lifecycle.", "")
    return normalized in LIFECYCLE_ACTION_NAMES


def refresh_panels_for_action(action: str) -> tuple[PanelId, ...] | None:
    normalized = " ".join(action.split())
    return ACTION_REFRESH_PANELS.get(normalized)


def targeted_refresh_worker_name(action: str) -> str:
    return f"{TARGETED_REFRESH_WORKER_PREFIX}{' '.join(action.split())}"


def worker_state_outcome(
    *,
    worker_name: str,
    state: WorkerState,
    result: object,
    error: Exception | None,
) -> WorkerStateOutcome | None:
    if worker_name.startswith(LIFECYCLE_WORKER_PREFIX):
        clear_busy = state in {WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED}
        if state == WorkerState.SUCCESS:
            return WorkerStateOutcome(
                message=_action_message_from_result(result),
                clear_lifecycle_busy=clear_busy,
            )
        if state == WorkerState.ERROR and error is not None:
            return WorkerStateOutcome(
                message=ActionFailed(gateway_failure_from_exception(worker_name, error)),
                clear_lifecycle_busy=clear_busy,
            )
        return WorkerStateOutcome(clear_lifecycle_busy=clear_busy) if clear_busy else None
    if worker_name.startswith(ACTION_WORKER_PREFIX):
        if state == WorkerState.SUCCESS:
            return WorkerStateOutcome(message=_action_message_from_result(result))
        if state == WorkerState.ERROR and error is not None:
            return WorkerStateOutcome(
                message=ActionFailed(gateway_failure_from_exception(worker_name, error))
            )
        return None
    if worker_name == PUBLISH_REFRESH_WORKER_NAME:
        if state == WorkerState.SUCCESS:
            return WorkerStateOutcome(
                message=_refresh_message_from_result(result, panels=(PanelId.PUBLISH,))
            )
        if state == WorkerState.ERROR and error is not None:
            return WorkerStateOutcome(
                message=RefreshFailed(
                    gateway_failure_from_exception("refresh.publish", error),
                    panels=(PanelId.PUBLISH,),
                )
            )
        return None
    if worker_name.startswith(TARGETED_REFRESH_WORKER_PREFIX):
        action = worker_name.removeprefix(TARGETED_REFRESH_WORKER_PREFIX)
        panels = refresh_panels_for_action(action)
        if panels is None:
            return None
        if state == WorkerState.SUCCESS:
            return WorkerStateOutcome(
                message=_refresh_message_from_result(result, panels=panels)
            )
        if state == WorkerState.ERROR and error is not None:
            return WorkerStateOutcome(
                message=RefreshFailed(
                    gateway_failure_from_exception("refresh.workspace", error),
                    panels=panels,
                )
            )
        return None
    if worker_name not in {INITIAL_REFRESH_WORKER_NAME, PERIODIC_REFRESH_WORKER_NAME}:
        return None
    panels = INITIAL_REFRESH_PANELS if worker_name == INITIAL_REFRESH_WORKER_NAME else WORKSPACE_REFRESH_PANELS
    ensure_background_runtime = worker_name == INITIAL_REFRESH_WORKER_NAME and state in {
        WorkerState.SUCCESS,
        WorkerState.ERROR,
    }
    if state == WorkerState.SUCCESS:
        return WorkerStateOutcome(
            message=_refresh_message_from_result(result, panels=panels),
            ensure_background_runtime=ensure_background_runtime,
        )
    if state == WorkerState.ERROR and error is not None:
        return WorkerStateOutcome(
            message=RefreshFailed(
                gateway_failure_from_exception("refresh.workspace", error),
                panels=panels,
            ),
            ensure_background_runtime=ensure_background_runtime,
        )
    return None


def _action_message_from_result(result: object) -> ActionSucceeded | ActionFailed | None:
    if not isinstance(result, GatewayResult):
        return None
    if result.ok and result.value is not None:
        return ActionSucceeded(result.value)
    if result.failure is not None:
        return ActionFailed(result.failure)
    return None


def _refresh_message_from_result(
    result: object,
    *,
    panels: tuple[PanelId, ...],
) -> RefreshSucceeded | RefreshFailed | None:
    if not isinstance(result, GatewayResult):
        return None
    if result.ok and result.value is not None:
        return RefreshSucceeded(result.value, panels=panels)
    if result.failure is not None:
        return RefreshFailed(result.failure, panels=panels)
    return None


__all__ = [
    "ACTION_WORKER_GROUP",
    "ACTION_WORKER_PREFIX",
    "INITIAL_REFRESH_PANELS",
    "LIFECYCLE_ACTION_NAMES",
    "LIFECYCLE_WORKER_GROUP",
    "LIFECYCLE_WORKER_PREFIX",
    "TARGETED_REFRESH_WORKER_PREFIX",
    "PUBLISH_REFRESH_WORKER_GROUP",
    "PUBLISH_REFRESH_WORKER_NAME",
    "WORKSPACE_REFRESH_PANELS",
    "WorkerStateOutcome",
    "build_shell_inspector_view",
    "is_lifecycle_action",
    "latest_run_summary_from_runs",
    "notification_severity",
    "publish_confirmation_lines",
    "queue_cleanup_confirmation_lines",
    "queue_cleanup_reason",
    "queue_reorder_confirmation_lines",
    "refresh_panels_for_action",
    "selected_config_field",
    "targeted_refresh_worker_name",
    "worker_state_outcome",
]
