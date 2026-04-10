"""Pure helper seams for the persistent shell screen."""

from __future__ import annotations

from dataclasses import dataclass

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
from ..widgets.overview_panel import LatestRunSummary
from ..widgets.shell_inspector import ShellInspectorView
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
    selected_run_id: str | None = None,
    selected_event: RuntimeEventView | None = None,
    selected_question_id: str | None = None,
    selected_config_field_key: str | None = None,
) -> ShellInspectorView:
    title = active_panel.label
    headline = "waiting for the first workspace snapshot"
    detail_lines: tuple[str, ...] = ()
    action_lines: tuple[str, ...] = ()

    if active_panel.id is PanelId.OVERVIEW:
        if runtime is not None:
            daemon_state = "running" if runtime.process_running else "stopped"
            headline = f"daemon {daemon_state} | exec {runtime.execution_status.lower()}"
            detail = [
                f"research {runtime.research_status.lower()} | backlog {runtime.backlog_depth}",
            ]
            if latest_run is not None:
                detail.append(f"latest run {latest_run.run_id} | status {latest_run.latest_status or 'unknown'}")
            if runtime.selection.selection_ref:
                detail.append(f"selection {runtime.selection.selection_ref}")
            detail_lines = tuple(detail)
            action_lines = ("Use the left rail or 1-7 to switch work surfaces.",)
    elif active_panel.id is PanelId.QUEUE:
        selected = _queue_task_by_id(queue, selected_task_id)
        if queue is None:
            headline = "queue snapshot not loaded"
        elif selected is not None:
            title = selected.title
            headline = f"task {selected.task_id}"
            detail = [f"backlog depth {queue.backlog_depth}"]
            if selected.spec_id:
                detail.append(f"spec {selected.spec_id}")
            if runtime is not None and runtime.process_running:
                detail.append("daemon running | reorder requests queue through mailbox")
            detail_lines = tuple(detail)
            action_lines = ("Up/Down select backlog items.", "Enter reviews a staged reorder draft.")
        else:
            headline = f"backlog {queue.backlog_depth}"
            detail_lines = ("No backlog item selected yet.",)
            action_lines = ("Focus content and use Up/Down to choose a task.",)
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
            action_lines = ("Up/Down changes the selected run.", "Enter opens the run-detail workflow.")
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
            action_lines = ("Up/Down chooses the pending question.", "Enter opens answer, accept, and skip actions.")
        else:
            headline = f"{research.status} | mode {research.current_mode}"
            detail_lines = (f"selected family {research.selected_family or 'none'}",)
            action_lines = ("Research drilldown will become richer in later panel slices.",)
    elif active_panel.id is PanelId.LOGS:
        if selected_event is not None:
            title = selected_event.event_type
            headline = f"{selected_event.source} | {selected_event.category or 'event'}"
            detail = [selected_event.summary or selected_event.event_type]
            if selected_event.run_id:
                detail.append(f"run {selected_event.run_id}")
            detail_lines = tuple(detail)
        elif display_mode is DisplayMode.DEBUG:
            headline = "debug log view"
            detail_lines = ("Expanded payload detail stays in the workspace surface.",)
        else:
            headline = "live log stream"
            detail_lines = ("Focus content to select an event and populate this inspector.",)
        if expanded_mode:
            action_lines = ("Escape returns from expanded stream mode.", "L jumps the expanded stream back to live.")
        else:
            action_lines = ("Up/Down selects events.", "Enter opens run detail when the selected event has a run id.")
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
            action_lines = ("Up/Down changes the selected field.", "Enter or E opens guided editing.")
        else:
            headline = f"{config.source_kind} | guided edits unavailable"
            detail_lines = (config.editing_disabled_reason or "no editable fields are visible",)
    elif active_panel.id is PanelId.PUBLISH:
        if publish is None:
            headline = "publish preflight not loaded"
        else:
            headline = f"{publish.status} | changed {len(publish.changed_paths)}"
            detail_lines = (
                f"branch {publish.branch or 'detached'} | origin {'configured' if publish.origin_configured else 'missing'}",
                f"selected manifest paths {len(publish.selected_paths)}",
            )
            action_lines = ("Shift+R refreshes preflight facts.", "Publish sync and commit actions remain available in palette and footer.")

    if panel_failure is not None:
        detail_lines = (*detail_lines, f"refresh degraded: {panel_failure.message}")
    return ShellInspectorView(
        panel_label=active_panel.label,
        title=title,
        headline=headline,
        detail_lines=detail_lines,
        action_lines=action_lines,
    )


def _queue_task_by_id(queue: QueueOverviewView | None, task_id: str | None) -> QueueTaskView | None:
    if queue is None or task_id is None:
        return None
    for task in queue.backlog:
        if task.task_id == task_id:
            return task
    return None


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
    push_ready = publish.commit_allowed and publish.origin_configured and publish.branch is not None
    lines = [
        (
            "Create a staging commit and push it to the configured origin?"
            if push
            else "Create a local staging commit without pushing?"
        ),
        "",
        f"Status: {publish.status}",
        f"Staging repo: {publish.staging_repo_dir}",
        f"Branch: {publish.branch or 'detached'}",
        f"Origin configured: {'yes' if publish.origin_configured else 'no'}",
        f"Changed paths: {len(publish.changed_paths)}",
        f"Skip reason: {publish.skip_reason or 'none'}",
        f"Push ready from current facts: {'yes' if push_ready else 'no'}",
    ]
    if push:
        lines.extend(
            [
                "",
                "Push stays intentionally higher friction than the default local-commit path.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "This is the default no-push publish surface. Remote orchestration stays out of scope here.",
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
    "queue_reorder_confirmation_lines",
    "refresh_panels_for_action",
    "selected_config_field",
    "targeted_refresh_worker_name",
    "worker_state_outcome",
]
