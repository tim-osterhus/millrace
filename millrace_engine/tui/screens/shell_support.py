"""Pure helper seams for the persistent shell screen."""

from __future__ import annotations

from dataclasses import dataclass

from textual.worker import WorkerState

from ..formatting import format_timestamp
from ..messages import ActionFailed, ActionSucceeded, RefreshFailed, RefreshSucceeded
from ..models import (
    ConfigFieldView,
    ConfigOverviewView,
    GatewayFailure,
    GatewayResult,
    NoticeView,
    PanelId,
    PublishOverviewView,
    QueueOverviewView,
    RuntimeOverviewView,
    RunsOverviewView,
)
from ..workers import INITIAL_REFRESH_WORKER_NAME, PERIODIC_REFRESH_WORKER_NAME, gateway_failure_from_exception
from ..widgets.overview_panel import LatestRunSummary

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
LIFECYCLE_ACTION_NAMES = {
    "start_once",
    "start_daemon",
    "start.once",
    "start.daemon",
    "pause",
    "resume",
    "stop",
}


@dataclass(frozen=True, slots=True)
class WorkerStateOutcome:
    message: ActionSucceeded | ActionFailed | RefreshSucceeded | RefreshFailed | None = None
    clear_lifecycle_busy: bool = False
    ensure_background_runtime: bool = False


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
    "PUBLISH_REFRESH_WORKER_GROUP",
    "PUBLISH_REFRESH_WORKER_NAME",
    "WORKSPACE_REFRESH_PANELS",
    "WorkerStateOutcome",
    "is_lifecycle_action",
    "latest_run_summary_from_runs",
    "notification_severity",
    "publish_confirmation_lines",
    "queue_reorder_confirmation_lines",
    "selected_config_field",
    "worker_state_outcome",
]
