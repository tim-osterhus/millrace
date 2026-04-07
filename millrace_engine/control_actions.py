"""Control-plane action helpers for queue, supervisor, and lifecycle mutations."""

from __future__ import annotations

from pathlib import Path

from .adapters.control_mailbox import ControlCommand, normalize_command_issuer, write_command
from .control_common import ControlError, queue_control_error
from .control_models import OperationResult
from .control_mutations import append_task_to_backlog, copy_idea_into_raw_queue
from .paths import RuntimePaths
from .queue import QueueError, TaskQueue


def normalize_supervisor_issuer(issuer: str) -> str:
    """Return one validated supervisor issuer token."""

    try:
        return normalize_command_issuer(issuer)
    except ValueError as exc:
        raise ControlError(str(exc)) from exc


def operation_with_payload_value(operation: OperationResult, *, key: str, value: object) -> OperationResult:
    """Return one copied operation result with one extra payload value."""

    payload = dict(operation.payload)
    payload[key] = value
    return OperationResult(
        command_id=operation.command_id,
        mode=operation.mode,
        applied=operation.applied,
        message=operation.message,
        payload=payload,
    )


def queue_summary(paths: RuntimePaths):
    """Return one queue adapter."""

    return TaskQueue(paths)


def queue_reorder(
    paths: RuntimePaths,
    *,
    task_ids: list[str] | tuple[str, ...],
    daemon_running: bool,
) -> OperationResult:
    """Rewrite the backlog order exactly as requested."""

    requested_ids = [task_id.strip() for task_id in task_ids if task_id.strip()]
    if not requested_ids:
        raise ControlError("queue reorder requires at least one task id")

    if daemon_running:
        envelope = write_command(
            paths,
            ControlCommand.QUEUE_REORDER,
            payload={"task_ids": requested_ids},
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="queue_reorder queued",
            payload={"task_ids": requested_ids},
        )

    try:
        reordered = TaskQueue(paths).reorder(requested_ids)
    except (FileNotFoundError, QueueError, ValueError) as exc:
        raise queue_control_error(exc, prefix="queue reorder failed") from exc
    return OperationResult(
        mode="direct",
        applied=True,
        message="queue reordered",
        payload={
            "task_ids": [card.task_id for card in reordered],
            "backlog_depth": len(reordered),
        },
    )


def queue_cleanup_remove(
    paths: RuntimePaths,
    *,
    task_id: str,
    reason: str,
    daemon_running: bool,
) -> OperationResult:
    """Remove one visible queued task through the local cleanup path."""

    if daemon_running:
        raise ControlError("queue cleanup remove requires the daemon to be stopped")
    try:
        record = TaskQueue(paths).remove_task(task_id, reason=reason)
    except (FileNotFoundError, QueueError, ValueError) as exc:
        raise queue_control_error(exc, prefix="queue cleanup remove failed") from exc
    return OperationResult(
        mode="direct",
        applied=True,
        message="queue cleanup removed task",
        payload={
            "task_id": record.task.task_id,
            "title": record.task.title,
            "source_store": record.source_store,
            "destination_store": record.destination_store,
            "reason": record.reason,
            "cleanup_action": record.action,
        },
    )


def queue_cleanup_quarantine(
    paths: RuntimePaths,
    *,
    task_id: str,
    reason: str,
    daemon_running: bool,
) -> OperationResult:
    """Quarantine one visible queued task through the local cleanup path."""

    if daemon_running:
        raise ControlError("queue cleanup quarantine requires the daemon to be stopped")
    try:
        record = TaskQueue(paths).quarantine_task(task_id, reason=reason)
    except (FileNotFoundError, QueueError, ValueError) as exc:
        raise queue_control_error(exc, prefix="queue cleanup quarantine failed") from exc
    return OperationResult(
        mode="direct",
        applied=True,
        message="queue cleanup quarantined task",
        payload={
            "task_id": record.task.task_id,
            "title": record.task.title,
            "source_store": record.source_store,
            "destination_store": record.destination_store,
            "reason": record.reason,
            "cleanup_action": record.action,
        },
    )


def supervisor_queue_reorder(
    paths: RuntimePaths,
    *,
    task_ids: list[str] | tuple[str, ...],
    issuer: str,
    daemon_running: bool,
) -> OperationResult:
    """Rewrite backlog order through the supervisor-safe mutation path."""

    normalized_issuer = normalize_supervisor_issuer(issuer)
    requested_ids = [task_id.strip() for task_id in task_ids if task_id.strip()]
    if not requested_ids:
        raise ControlError("queue reorder requires at least one task id")

    if daemon_running:
        envelope = write_command(
            paths,
            ControlCommand.QUEUE_REORDER,
            payload={"task_ids": requested_ids},
            issuer=normalized_issuer,
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="queue_reorder queued",
            payload={"task_ids": requested_ids, "issuer": normalized_issuer},
        )
    return operation_with_payload_value(
        queue_reorder(paths, task_ids=requested_ids, daemon_running=False),
        key="issuer",
        value=normalized_issuer,
    )


def add_task(
    paths: RuntimePaths,
    *,
    title: str,
    daemon_running: bool,
    body: str | None = None,
    spec_id: str | None = None,
) -> OperationResult:
    """Add one task card to the backlog or daemon mailbox."""

    if daemon_running:
        envelope = write_command(
            paths,
            ControlCommand.ADD_TASK,
            payload={"title": title, "body": body, "spec_id": spec_id},
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="add_task queued",
        )
    card = append_task_to_backlog(paths, title=title, body=body, spec_id=spec_id)
    return OperationResult(
        mode="direct",
        applied=True,
        message="task added",
        payload={"task_id": card.task_id},
    )


def supervisor_add_task(
    paths: RuntimePaths,
    *,
    title: str,
    issuer: str,
    daemon_running: bool,
    body: str | None = None,
    spec_id: str | None = None,
) -> OperationResult:
    """Add one task through the supported supervisor-safe mutation path."""

    normalized_issuer = normalize_supervisor_issuer(issuer)
    if daemon_running:
        envelope = write_command(
            paths,
            ControlCommand.ADD_TASK,
            payload={"title": title, "body": body, "spec_id": spec_id},
            issuer=normalized_issuer,
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="add_task queued",
            payload={"issuer": normalized_issuer},
        )
    return operation_with_payload_value(
        add_task(paths, title=title, body=body, spec_id=spec_id, daemon_running=False),
        key="issuer",
        value=normalized_issuer,
    )


def add_idea(paths: RuntimePaths, *, file: Path | str, daemon_running: bool) -> OperationResult:
    """Queue one idea file."""

    source_file = Path(file).expanduser().resolve()
    if daemon_running:
        envelope = write_command(
            paths,
            ControlCommand.ADD_IDEA,
            payload={"file": source_file.as_posix()},
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="add_idea queued",
        )
    copied = copy_idea_into_raw_queue(paths, source_file)
    return OperationResult(
        mode="direct",
        applied=True,
        message="idea queued",
        payload={"path": copied.as_posix()},
    )


def lifecycle_action(
    paths: RuntimePaths,
    *,
    command: ControlCommand,
    daemon_running: bool,
) -> OperationResult:
    """Queue one daemon lifecycle command or return the direct no-op state."""

    if not daemon_running:
        return OperationResult(mode="direct", applied=False, message="engine is not running")
    envelope = write_command(paths, command)
    return OperationResult(
        command_id=envelope.command_id,
        mode="mailbox",
        applied=True,
        message=f"{command.value} queued",
    )


def supervisor_lifecycle_action(
    paths: RuntimePaths,
    *,
    command: ControlCommand,
    issuer: str,
    daemon_running: bool,
) -> OperationResult:
    """Queue one supervisor-owned lifecycle command or annotate the direct no-op state."""

    normalized_issuer = normalize_supervisor_issuer(issuer)
    if daemon_running:
        envelope = write_command(paths, command, issuer=normalized_issuer)
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message=f"{command.value} queued",
            payload={"issuer": normalized_issuer},
        )
    return operation_with_payload_value(
        lifecycle_action(paths, command=command, daemon_running=False),
        key="issuer",
        value=normalized_issuer,
    )
