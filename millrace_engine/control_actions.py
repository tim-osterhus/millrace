"""Control-plane action helpers for queue, supervisor, and lifecycle mutations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .adapters.control_mailbox import ControlCommand, normalize_command_issuer, write_command
from .compounding import deprecate_procedure, promote_procedure
from .control_common import ControlError, queue_control_error
from .control_models import (
    DeferredActiveTaskClear,
    ActiveTaskRemediationIntent,
    ActiveTaskRemediationOutcome,
    ActiveTaskRemediationRequest,
    ActiveTaskRemediationResult,
    OperationResult,
)
from .control_mutations import append_task_to_backlog, copy_idea_into_raw_queue
from .markdown import write_text_atomic
from .paths import RuntimePaths
from .queue import QueueEmptyError, QueueError, TaskQueue


def normalize_supervisor_issuer(issuer: str) -> str:
    """Return one validated supervisor issuer token."""

    try:
        return normalize_command_issuer(issuer)
    except ValueError as exc:
        raise ControlError(str(exc)) from exc


def normalize_lifecycle_actor(changed_by: str) -> str:
    """Return one validated lifecycle actor token."""

    normalized = str(changed_by).strip()
    if not normalized:
        raise ControlError("changed_by may not be empty")
    return normalized


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


def normalize_task_id(task_id: str, *, action_label: str) -> str:
    """Return one validated task identifier for queue mutations."""

    normalized = task_id.strip()
    if not normalized:
        raise ControlError(f"{action_label} requires a task id")
    return normalized


def _active_task_remediation_result(
    *,
    command_id: str | None = None,
    mode: str,
    applied: bool,
    message: str,
    outcome_state: ActiveTaskRemediationOutcome,
    request: ActiveTaskRemediationRequest,
    payload: dict[str, object] | None = None,
) -> ActiveTaskRemediationResult:
    return ActiveTaskRemediationResult(
        command_id=command_id,
        mode=mode,
        applied=applied,
        message=message,
        outcome_state=outcome_state,
        request=request,
        payload=payload or {},
    )


def _pending_active_task_clear_path(paths: RuntimePaths) -> Path:
    return paths.runtime_dir / "pending_active_task_clear.json"


def _last_active_task_clear_path(paths: RuntimePaths) -> Path:
    return paths.runtime_dir / "last_active_task_clear.json"


def read_pending_active_task_clear(paths: RuntimePaths) -> DeferredActiveTaskClear | None:
    path = _pending_active_task_clear_path(paths)
    if not path.exists():
        return None
    try:
        return DeferredActiveTaskClear.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise ControlError(f"pending active-task clear state is invalid: {exc}") from exc


def write_pending_active_task_clear(paths: RuntimePaths, pending: DeferredActiveTaskClear) -> None:
    path = _pending_active_task_clear_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, pending.model_dump_json(indent=2) + "\n")


def clear_pending_active_task_clear(
    paths: RuntimePaths,
    *,
    command_id: str | None = None,
) -> DeferredActiveTaskClear | None:
    pending = read_pending_active_task_clear(paths)
    if pending is None:
        return None
    if command_id is not None and pending.command_id != command_id:
        return pending
    _pending_active_task_clear_path(paths).unlink(missing_ok=True)
    return pending


def read_last_active_task_clear(paths: RuntimePaths) -> ActiveTaskRemediationResult | None:
    path = _last_active_task_clear_path(paths)
    if not path.exists():
        return None
    try:
        return ActiveTaskRemediationResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise ControlError(f"last active-task clear state is invalid: {exc}") from exc


def write_last_active_task_clear(paths: RuntimePaths, result: ActiveTaskRemediationResult) -> None:
    path = _last_active_task_clear_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, result.model_dump_json(indent=2) + "\n")


def _record_last_active_task_clear(
    paths: RuntimePaths,
    result: ActiveTaskRemediationResult,
) -> ActiveTaskRemediationResult:
    write_last_active_task_clear(paths, result)
    return result


def active_task_remediate(
    paths: RuntimePaths,
    *,
    intent: str,
    reason: str,
    daemon_running: bool,
    issuer: str | None = None,
    requested_at: datetime | None = None,
    command_id: str | None = None,
    response_mode: str = "direct",
) -> ActiveTaskRemediationResult:
    """Apply one supported active-task clear or recover request."""

    normalized_intent = " ".join(intent.strip().split())
    normalized_reason = " ".join(reason.strip().split())
    if not normalized_reason:
        return active_task_rejected(
            intent=intent,
            reason=reason,
            issuer=issuer,
            rejected_reason="reason_required",
        )
    if normalized_intent not in {item.value for item in ActiveTaskRemediationIntent}:
        return active_task_rejected(intent=intent, reason=reason, issuer=issuer)
    request = ActiveTaskRemediationRequest.model_validate(
        {
            "intent": normalized_intent,
            "reason": normalized_reason,
            "requested_at": requested_at or datetime.now(timezone.utc),
            "issuer": issuer,
        }
    )
    queue = TaskQueue(paths)
    backlog_depth = queue.backlog_depth()
    next_task = queue.peek_next()

    active_task = queue.active_task()
    if daemon_running and request.intent == ActiveTaskRemediationIntent.CLEAR.value:
        if active_task is None:
            return _record_last_active_task_clear(
                paths,
                _active_task_remediation_result(
                    mode="direct",
                    applied=False,
                    message="active task already clear",
                    outcome_state=ActiveTaskRemediationOutcome.NOOP_IDEMPOTENT,
                    request=request,
                    payload={
                        "intent": request.intent,
                        "backlog_depth": backlog_depth,
                        "next_task_id": next_task.task_id if next_task is not None else None,
                    },
                ),
            )
        pending = read_pending_active_task_clear(paths)
        if pending is not None:
            return _record_last_active_task_clear(
                paths,
                _active_task_remediation_result(
                    mode="mailbox",
                    applied=False,
                    message="active-task clear already pending until daemon boundary",
                    outcome_state=ActiveTaskRemediationOutcome.NOOP_IDEMPOTENT,
                    request=request,
                    payload={
                        "intent": request.intent,
                        "backlog_depth": backlog_depth,
                        "next_task_id": next_task.task_id if next_task is not None else None,
                        "pending_command_id": pending.command_id,
                    },
                ),
            )
        envelope = write_command(
            paths,
            ControlCommand.ACTIVE_TASK_CLEAR,
            payload={"reason": request.reason},
            issuer=issuer or "cli",
        )
        write_pending_active_task_clear(
            paths,
            DeferredActiveTaskClear(
                command_id=envelope.command_id,
                request=request,
                deferred_at=datetime.now(timezone.utc),
            ),
        )
        return _record_last_active_task_clear(
            paths,
            _active_task_remediation_result(
                command_id=envelope.command_id,
                mode="mailbox",
                applied=True,
                message="active-task clear deferred until daemon boundary",
                outcome_state=ActiveTaskRemediationOutcome.DEFERRED,
                request=request,
                payload={
                    "intent": request.intent,
                    "backlog_depth": backlog_depth,
                    "next_task_id": next_task.task_id if next_task is not None else None,
                    "deferred_reason": "daemon_running",
                },
            ),
        )

    if daemon_running:
        return _active_task_remediation_result(
            mode="direct",
            applied=False,
            message=f"active-task {request.intent} blocked while daemon is running",
            outcome_state=ActiveTaskRemediationOutcome.BLOCKED,
            request=request,
            payload={
                "intent": request.intent,
                "backlog_depth": backlog_depth,
                "next_task_id": next_task.task_id if next_task is not None else None,
                "blocked_reason": "daemon_running",
            },
        )

    if active_task is None:
        result = _active_task_remediation_result(
            mode=response_mode,
            applied=False,
            message="active task already clear",
            outcome_state=ActiveTaskRemediationOutcome.NOOP_IDEMPOTENT,
            request=request,
            payload={
                "intent": request.intent,
                "backlog_depth": backlog_depth,
                "next_task_id": next_task.task_id if next_task is not None else None,
            },
        )
        if request.intent == ActiveTaskRemediationIntent.CLEAR.value:
            return _record_last_active_task_clear(paths, result)
        return result

    try:
        record = queue.remediate_active_task(
            intent=request.intent,
            reason=request.reason,
            requested_at=request.requested_at,
            issuer=request.issuer,
        )
    except QueueEmptyError:
        result = _active_task_remediation_result(
            mode=response_mode,
            applied=False,
            message="active task already clear",
            outcome_state=ActiveTaskRemediationOutcome.NOOP_IDEMPOTENT,
            request=request,
            payload={
                "intent": request.intent,
                "backlog_depth": backlog_depth,
                "next_task_id": next_task.task_id if next_task is not None else None,
            },
        )
        if request.intent == ActiveTaskRemediationIntent.CLEAR.value:
            return _record_last_active_task_clear(paths, result)
        return result
    except (FileNotFoundError, QueueError, ValueError) as exc:
        raise queue_control_error(exc, prefix=f"active-task {request.intent} failed") from exc

    remaining_backlog_depth = queue.backlog_depth()
    next_after = queue.peek_next()
    payload: dict[str, object] = {
        "intent": record.intent,
        "task_id": record.task.task_id,
        "title": record.task.title,
        "source_store": record.source_store,
        "destination_store": record.destination_store,
        "reason": record.reason,
        "requested_at": record.requested_at,
        "applied_at": record.applied_at,
        "backlog_depth": remaining_backlog_depth,
        "next_task_id": next_after.task_id if next_after is not None else None,
    }
    if record.issuer is not None:
        payload["issuer"] = record.issuer
    result = _active_task_remediation_result(
        command_id=command_id,
        mode=response_mode,
        applied=True,
        message=f"active task {record.intent} applied",
        outcome_state=ActiveTaskRemediationOutcome.APPLIED,
        request=request,
        payload=payload,
    )
    if record.intent == ActiveTaskRemediationIntent.CLEAR.value:
        clear_pending_active_task_clear(paths, command_id=command_id)
        return _record_last_active_task_clear(paths, result)
    return result


def active_task_rejected(
    *,
    intent: str,
    reason: str,
    issuer: str | None = None,
    rejected_reason: str = "unsupported_intent",
) -> ActiveTaskRemediationResult:
    """Return one deterministic rejected result for unsupported active-task requests."""

    normalized_reason = " ".join(reason.strip().split())
    normalized_issuer = " ".join((issuer or "").strip().split()) or None
    normalized_intent = " ".join(intent.strip().split()) or intent.strip()
    if not normalized_reason:
        normalized_reason = "request rejected"
    if rejected_reason == "reason_required":
        message = "active-task request rejected: reason is required"
    else:
        message = f"active-task request rejected: unsupported intent {normalized_intent!r}"
    return _active_task_remediation_result(
        mode="direct",
        applied=False,
        message=message,
        outcome_state=ActiveTaskRemediationOutcome.REJECTED,
        request=ActiveTaskRemediationRequest.model_construct(
            intent=normalized_intent,
            reason=normalized_reason,
            requested_at=datetime.now(timezone.utc),
            issuer=normalized_issuer,
        ),
        payload={
            "intent": normalized_intent,
            "rejected_reason": rejected_reason,
        },
    )


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


def supervisor_queue_cleanup_remove(
    paths: RuntimePaths,
    *,
    task_id: str,
    reason: str,
    issuer: str,
    daemon_running: bool,
) -> OperationResult:
    """Remove one visible queued task through the supervisor-safe cleanup path."""

    normalized_issuer = normalize_supervisor_issuer(issuer)
    normalized_task_id = normalize_task_id(task_id, action_label="queue cleanup remove")
    if daemon_running:
        envelope = write_command(
            paths,
            ControlCommand.QUEUE_CLEANUP_REMOVE,
            payload={"task_id": normalized_task_id, "reason": reason},
            issuer=normalized_issuer,
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="queue_cleanup_remove queued",
            payload={
                "task_id": normalized_task_id,
                "reason": reason,
                "cleanup_action": "remove",
                "issuer": normalized_issuer,
            },
        )
    return operation_with_payload_value(
        queue_cleanup_remove(paths, task_id=normalized_task_id, reason=reason, daemon_running=False),
        key="issuer",
        value=normalized_issuer,
    )


def supervisor_queue_cleanup_quarantine(
    paths: RuntimePaths,
    *,
    task_id: str,
    reason: str,
    issuer: str,
    daemon_running: bool,
) -> OperationResult:
    """Quarantine one visible queued task through the supervisor-safe cleanup path."""

    normalized_issuer = normalize_supervisor_issuer(issuer)
    normalized_task_id = normalize_task_id(task_id, action_label="queue cleanup quarantine")
    if daemon_running:
        envelope = write_command(
            paths,
            ControlCommand.QUEUE_CLEANUP_QUARANTINE,
            payload={"task_id": normalized_task_id, "reason": reason},
            issuer=normalized_issuer,
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="queue_cleanup_quarantine queued",
            payload={
                "task_id": normalized_task_id,
                "reason": reason,
                "cleanup_action": "quarantine",
                "issuer": normalized_issuer,
            },
        )
    return operation_with_payload_value(
        queue_cleanup_quarantine(paths, task_id=normalized_task_id, reason=reason, daemon_running=False),
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


def compounding_promote(
    paths: RuntimePaths,
    *,
    procedure_id: str,
    changed_by: str,
    reason: str,
    daemon_running: bool,
) -> OperationResult:
    """Promote one procedure into broader-scope reuse directly or by mailbox."""

    normalized_changed_by = normalize_lifecycle_actor(changed_by)
    normalized_procedure_id = normalize_task_id(procedure_id, action_label="compounding promote")
    if daemon_running:
        envelope = write_command(
            paths,
            ControlCommand.COMPOUNDING_PROMOTE,
            payload={
                "procedure_id": normalized_procedure_id,
                "changed_by": normalized_changed_by,
                "reason": reason,
            },
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="compounding_promote queued",
            payload={
                "action": "promote",
                "procedure_id": normalized_procedure_id,
                "changed_by": normalized_changed_by,
                "reason": reason,
            },
        )
    try:
        result = promote_procedure(
            paths,
            procedure_id=normalized_procedure_id,
            changed_by=normalized_changed_by,
            reason=reason,
        )
    except ValueError as exc:
        raise ControlError(str(exc)) from exc
    payload: dict[str, object] = {
        "action": "promote",
        "procedure_id": result.procedure.artifact.procedure_id,
        "changed_by": normalized_changed_by,
        "reason": reason,
        "artifact_path": result.procedure.artifact_path.as_posix(),
        "retrieval_status": result.procedure.retrieval_status,
    }
    if result.source_procedure_id is not None:
        payload["source_procedure_id"] = result.source_procedure_id
    if result.source_artifact_path is not None:
        payload["source_artifact_path"] = result.source_artifact_path.as_posix()
    if result.lifecycle_record is not None:
        payload["record_id"] = result.lifecycle_record.record.record_id
        payload["record_path"] = result.lifecycle_record.path.as_posix()
    return OperationResult(
        mode="direct",
        applied=result.applied,
        message="procedure promoted" if result.applied else "procedure already promoted",
        payload=payload,
    )


def compounding_deprecate(
    paths: RuntimePaths,
    *,
    procedure_id: str,
    changed_by: str,
    reason: str,
    replacement_procedure_id: str | None,
    daemon_running: bool,
) -> OperationResult:
    """Deprecate one workspace-scope reusable procedure."""

    normalized_changed_by = normalize_lifecycle_actor(changed_by)
    normalized_procedure_id = normalize_task_id(procedure_id, action_label="compounding deprecate")
    normalized_replacement = replacement_procedure_id.strip() if replacement_procedure_id is not None else None
    if normalized_replacement == "":
        normalized_replacement = None
    if daemon_running:
        envelope = write_command(
            paths,
            ControlCommand.COMPOUNDING_DEPRECATE,
            payload={
                "procedure_id": normalized_procedure_id,
                "changed_by": normalized_changed_by,
                "reason": reason,
                "replacement_procedure_id": normalized_replacement,
            },
        )
        return OperationResult(
            command_id=envelope.command_id,
            mode="mailbox",
            applied=True,
            message="compounding_deprecate queued",
            payload={
                "action": "deprecate",
                "procedure_id": normalized_procedure_id,
                "changed_by": normalized_changed_by,
                "reason": reason,
                "replacement_procedure_id": normalized_replacement,
            },
        )
    try:
        result = deprecate_procedure(
            paths,
            procedure_id=normalized_procedure_id,
            changed_by=normalized_changed_by,
            reason=reason,
            replacement_procedure_id=normalized_replacement,
        )
    except ValueError as exc:
        raise ControlError(str(exc)) from exc
    payload: dict[str, object] = {
        "action": "deprecate",
        "procedure_id": result.procedure.artifact.procedure_id,
        "changed_by": normalized_changed_by,
        "reason": reason,
        "replacement_procedure_id": normalized_replacement,
        "artifact_path": result.procedure.artifact_path.as_posix(),
        "retrieval_status": result.procedure.retrieval_status,
    }
    if result.lifecycle_record is not None:
        payload["record_id"] = result.lifecycle_record.record.record_id
        payload["record_path"] = result.lifecycle_record.path.as_posix()
    return OperationResult(
        mode="direct",
        applied=result.applied,
        message="procedure deprecated" if result.applied else "procedure already deprecated",
        payload=payload,
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
