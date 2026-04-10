"""Config and queue-facing control surface helpers."""

from __future__ import annotations

from pydantic import ValidationError

from .adapters.control_mailbox import ControlCommand, write_command
from .config import build_runtime_paths
from .contracts import ExecutionStatus
from .control_common import ControlError, expected_error_message, queue_control_error
from .control_models import ConfigShowReport, OperationResult, QueueSnapshot
from .control_mutations import apply_native_config_value
from .control_reports import (
    asset_inventory_for,
    config_hash,
    mailbox_task_intake_view,
    selection_explanation,
    selection_preview_for,
    size_status_view,
    task_view,
)
from .queue import QueueError, TaskQueue
from .status import ControlPlane, StatusStore


def config_show(control) -> ConfigShowReport:
    active_task = TaskQueue(control.paths).active_task()
    size = size_status_view(control.loaded, task=active_task)
    current_status = StatusStore(control.paths.status_file, ControlPlane.EXECUTION).read()
    if not isinstance(current_status, ExecutionStatus):
        raise ControlError("execution status could not be read")
    selection = selection_preview_for(
        control.loaded,
        size=size,
        current_status=current_status,
    )
    return ConfigShowReport(
        source=control.loaded.source,
        config=control.loaded.config,
        config_hash=config_hash(control.loaded.config),
        selection=selection,
        selection_explanation=selection_explanation(
            size=size,
            current_status=current_status,
            selection=selection,
        ),
        assets=asset_inventory_for(control.loaded),
    )


def config_reload(control) -> OperationResult:
    if control.is_daemon_running():
        envelope = write_command(control.paths, ControlCommand.RELOAD_CONFIG)
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="reload_config queued",
        )
    reloaded = control.reload_local_config()
    return OperationResult(
        mode="direct",
        applied=True,
        message="config reloaded",
        payload={"config_hash": config_hash(reloaded.config)},
    )


def config_set(control, key: str, value: str) -> OperationResult:
    if control.is_daemon_running():
        envelope = write_command(
            control.paths,
            ControlCommand.SET_CONFIG,
            payload={"key": key, "value": value},
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="set_config queued",
            payload={"key": key},
        )

    control.loaded = apply_native_config_value(
        control.config_path,
        control.loaded,
        key,
        value,
        reject_startup_only=False,
    )
    control.paths = build_runtime_paths(control.loaded.config)
    return OperationResult(
        mode="direct",
        applied=True,
        message="config updated",
        payload={"key": key, "config_hash": config_hash(control.loaded.config)},
    )


def queue(control) -> QueueSnapshot:
    queue = TaskQueue(control.paths)
    try:
        return QueueSnapshot(
            active_task=task_view(queue.active_task()),
            backlog_depth=queue.backlog_depth(),
            next_task=task_view(queue.peek_next()),
            mailbox_task_intake=mailbox_task_intake_view(control.paths),
        )
    except (FileNotFoundError, QueueError, ValidationError, ValueError) as exc:
        raise queue_control_error(exc, prefix="queue state could not be read") from exc


def queue_inspect(control) -> QueueSnapshot:
    queue = TaskQueue(control.paths)
    try:
        from .markdown import parse_task_store

        backlog = parse_task_store(
            control.paths.backlog_file.read_text(encoding="utf-8"),
            source_file=control.paths.backlog_file,
        ).cards
        return QueueSnapshot(
            active_task=task_view(queue.active_task()),
            backlog_depth=len(backlog),
            next_task=task_view(backlog[0] if backlog else None),
            backlog=tuple(task_view(card) for card in backlog if task_view(card) is not None),
            mailbox_task_intake=mailbox_task_intake_view(control.paths),
        )
    except (FileNotFoundError, QueueError, ValidationError, ValueError) as exc:
        raise queue_control_error(exc, prefix="queue state could not be read") from exc
