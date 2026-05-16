"""Mailbox-drain and mailbox-command application helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import JsonValue

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import fingerprint_runtime_config, load_runtime_config
from millrace_ai.contracts import (
    MailboxAddIdeaPayload,
    MailboxAddProbePayload,
    MailboxAddSpecPayload,
    MailboxAddTaskPayload,
    MailboxArchiveBlockedTaskPayload,
    MailboxArchiveInvalidIncidentPayload,
    MailboxCancelWorkItemPayload,
    MailboxCommandEnvelope,
    MailboxExecutionCapabilityApprovalPayload,
    MailboxIncidentInterventionPayload,
    MailboxRetargetTaskDependencyPayload,
    MailboxSupersedeTaskPayload,
    Plane,
    ReloadOutcome,
)
from millrace_ai.errors import ControlRoutingError, RuntimeLifecycleError, WorkspaceStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.mailbox import drain_incoming_mailbox_commands, write_mailbox_command
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime.approvals import (
    approve_execution_capability_request,
    deny_execution_capability_request,
)
from millrace_ai.runtime.pause_state import (
    OPERATOR_PAUSE_SOURCE,
    add_pause_source,
    remove_pause_source,
)
from millrace_ai.state_store import save_snapshot
from millrace_ai.workspace.operator_interventions import (
    OperatorInterventionResult,
    archive_blocked_task,
    archive_invalid_incident_artifact,
    cancel_incident,
    cancel_work_item,
    resolve_incident_by_operator,
    retarget_queued_task_dependency,
    supersede_task,
)

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def drain_mailbox(engine: RuntimeEngine) -> None:
    drain_incoming_mailbox_commands(engine.paths, handler=engine._handle_mailbox_command)


def handle_mailbox_command(
    engine: RuntimeEngine,
    envelope: MailboxCommandEnvelope,
) -> None:  # pragma: no cover - thin integration path
    assert engine.snapshot is not None
    command = envelope.command.value
    if command == "pause":
        engine.snapshot = add_pause_source(
            engine.snapshot,
            source=OPERATOR_PAUSE_SOURCE,
            now=engine._now(),
        )
        save_snapshot(engine.paths, engine.snapshot)
        return
    if command == "resume":
        engine.snapshot = remove_pause_source(
            engine.snapshot,
            source=OPERATOR_PAUSE_SOURCE,
            now=engine._now(),
        )
        save_snapshot(engine.paths, engine.snapshot)
        return
    if command == "stop":
        engine.snapshot = engine.snapshot.model_copy(
            update={"stop_requested": True, "updated_at": engine._now()}
        )
        save_snapshot(engine.paths, engine.snapshot)
        return
    if command == "clear_stale_state":
        engine._clear_stale_state(reason=mailbox_reason(envelope, default="operator requested stale-state clear"))
        return
    if command == "retry_active":
        engine._retry_active(
            reason=mailbox_reason(envelope, default="operator requested retry"),
            scope=mailbox_retry_scope(envelope),
        )
        return
    if command == "reload_config":
        reload_config_from_mailbox(engine, envelope)
        return
    if command == "add_task":
        enqueue_task_from_mailbox(engine, envelope)
        return
    if command == "add_probe":
        enqueue_probe_from_mailbox(engine, envelope)
        return
    if command == "add_spec":
        enqueue_spec_from_mailbox(engine, envelope)
        return
    if command == "add_idea":
        enqueue_idea_from_mailbox(engine, envelope)
        return
    if command == "cancel_work_item":
        cancel_work_item_from_mailbox(engine, envelope)
        return
    if command == "archive_blocked_task":
        archive_blocked_task_from_mailbox(engine, envelope)
        return
    if command == "supersede_task":
        supersede_task_from_mailbox(engine, envelope)
        return
    if command == "retarget_task_dependency":
        retarget_task_dependency_from_mailbox(engine, envelope)
        return
    if command == "resolve_incident":
        resolve_incident_from_mailbox(engine, envelope)
        return
    if command == "cancel_incident":
        cancel_incident_from_mailbox(engine, envelope)
        return
    if command == "archive_invalid_incident":
        archive_invalid_incident_from_mailbox(engine, envelope)
        return
    if command == "approve_execution_capability":
        approve_execution_capability_from_mailbox(engine, envelope)
        return
    if command == "deny_execution_capability":
        deny_execution_capability_from_mailbox(engine, envelope)
        return
    raise ControlRoutingError(f"Unsupported mailbox command: {command}")


def reload_config_from_mailbox(
    engine: RuntimeEngine,
    envelope: MailboxCommandEnvelope | None = None,
) -> None:
    assert engine.snapshot is not None
    if engine.snapshot.active_runs_by_plane:
        if envelope is not None:
            deferred = envelope.model_copy(
                update={
                    "command_id": f"{envelope.command.value}-deferred-{uuid4().hex[:8]}",
                    "issued_at": engine._now(),
                }
            )
            write_mailbox_command(engine.paths, deferred)
        engine.snapshot = engine.snapshot.model_copy(
            update={
                "last_reload_error": "deferred until active planes drain",
                "updated_at": engine._now(),
            }
        )
        save_snapshot(engine.paths, engine.snapshot)
        active_planes: list[JsonValue] = [plane.value for plane in engine.snapshot.active_runs_by_plane]
        write_runtime_event(
            engine.paths,
            event_type="runtime_config_reload_deferred",
            data={
                "reason": "active_planes",
                "active_planes": active_planes,
            },
        )
        engine._emit_monitor_event(
            "runtime_config_reload_deferred",
            reason="active_planes",
            active_planes=active_planes,
        )
        return
    reloaded_config = load_runtime_config(engine.config_path)
    compile_outcome = compile_and_persist_workspace_plan(
        engine.paths,
        config=reloaded_config,
        requested_mode_id=engine.mode_id,
        assets_root=engine.assets_root,
        compile_if_needed=True,
        refuse_stale_last_known_good=True,
    )
    active_plan = compile_outcome.active_plan
    if active_plan is None:
        errors = ", ".join(compile_outcome.diagnostics.errors) or "compile failed"
        engine.snapshot = engine.snapshot.model_copy(
            update={
                "last_reload_outcome": ReloadOutcome.FAILED_RETAINED_PREVIOUS_PLAN,
                "last_reload_error": errors,
                "process_running": False,
                "stop_requested": True,
                "updated_at": engine._now(),
            }
        )
        save_snapshot(engine.paths, engine.snapshot)
        write_runtime_event(
            engine.paths,
            event_type="runtime_config_reload_failed",
            data={
                "error": errors,
                "retained_previous_plan": False,
            },
        )
        raise RuntimeLifecycleError(errors)

    if not compile_outcome.diagnostics.ok:
        errors = ", ".join(compile_outcome.diagnostics.errors) or "compile failed"
        engine.snapshot = engine.snapshot.model_copy(
            update={
                "last_reload_outcome": ReloadOutcome.FAILED_RETAINED_PREVIOUS_PLAN,
                "last_reload_error": errors,
                "updated_at": engine._now(),
            }
        )
        save_snapshot(engine.paths, engine.snapshot)
        write_runtime_event(
            engine.paths,
            event_type="runtime_config_reload_failed",
            data={
                "error": errors,
                "retained_previous_plan": True,
                "compiled_plan_id": engine.snapshot.compiled_plan_id,
            },
        )
        return

    engine.config = reloaded_config
    engine._rebuild_watcher_session()
    engine.compiled_plan = active_plan
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "runtime_mode": reloaded_config.runtime.run_style,
            "active_mode_id": active_plan.mode_id,
            "execution_loop_id": active_plan.execution_loop_id,
            "planning_loop_id": active_plan.planning_loop_id,
            "learning_loop_id": active_plan.learning_loop_id,
            "loop_ids_by_plane": active_plan.loop_ids_by_plane,
            "compiled_plan_id": active_plan.compiled_plan_id,
            "compiled_plan_path": str((engine.paths.state_dir / "compiled_plan.json").relative_to(engine.paths.root)),
            "queue_depth_learning": engine._learning_queue_depth(),
            "queue_depths_by_plane": {
                Plane.EXECUTION: engine._execution_queue_depth(),
                Plane.PLANNING: engine._planning_queue_depth(),
                Plane.LEARNING: engine._learning_queue_depth(),
            },
            "config_version": fingerprint_runtime_config(reloaded_config),
            "watcher_mode": engine._watcher_mode_value(),
            "last_reload_outcome": ReloadOutcome.APPLIED,
            "last_reload_error": None,
            "updated_at": engine._now(),
        }
    )
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="runtime_config_reloaded",
        data={
            "mode_id": active_plan.mode_id,
            "compiled_plan_id": active_plan.compiled_plan_id,
        },
    )


def enqueue_task_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxAddTaskPayload.model_validate(envelope.payload)
    destination = QueueStore(engine.paths).enqueue_task(payload.document)
    engine._refresh_runtime_queue_depths()
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="mailbox_add_task_applied",
        data={
            "task_id": payload.document.task_id,
            "path": str(destination.relative_to(engine.paths.root)),
        },
    )


def enqueue_probe_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxAddProbePayload.model_validate(envelope.payload)
    destination = QueueStore(engine.paths).enqueue_probe(payload.document)
    engine._refresh_runtime_queue_depths()
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="mailbox_add_probe_applied",
        data={
            "probe_id": payload.document.probe_id,
            "path": str(destination.relative_to(engine.paths.root)),
        },
    )


def enqueue_spec_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxAddSpecPayload.model_validate(envelope.payload)
    destination = QueueStore(engine.paths).enqueue_spec(payload.document)
    engine._refresh_runtime_queue_depths()
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="mailbox_add_spec_applied",
        data={
            "spec_id": payload.document.spec_id,
            "path": str(destination.relative_to(engine.paths.root)),
        },
    )


def enqueue_idea_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxAddIdeaPayload.model_validate(envelope.payload)
    destination_dir = engine.paths.root / "ideas" / "inbox"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / payload.source_name
    if destination.exists():
        raise WorkspaceStateError(f"idea document already exists: {destination}")
    destination.write_text(payload.markdown, encoding="utf-8")
    engine._refresh_runtime_queue_depths()
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="mailbox_add_idea_applied",
        data={
            "source_name": payload.source_name,
            "path": str(destination.relative_to(engine.paths.root)),
        },
    )


def cancel_work_item_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxCancelWorkItemPayload.model_validate(envelope.payload)
    if _defer_operator_intervention_if_active(engine, envelope):
        return
    result = cancel_work_item(
        engine.paths,
        work_item_id=payload.work_item_id,
        work_item_kind=payload.work_item_kind,
        reason=payload.reason,
        actor=envelope.issuer,
        now=engine._now(),
        issued_at=envelope.issued_at,
        force=payload.force,
    )
    _complete_operator_intervention(engine, envelope, result)


def archive_blocked_task_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxArchiveBlockedTaskPayload.model_validate(envelope.payload)
    if _defer_operator_intervention_if_active(engine, envelope):
        return
    result = archive_blocked_task(
        engine.paths,
        task_id=payload.task_id,
        reason=payload.reason,
        actor=envelope.issuer,
        now=engine._now(),
        issued_at=envelope.issued_at,
    )
    _complete_operator_intervention(engine, envelope, result)


def supersede_task_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxSupersedeTaskPayload.model_validate(envelope.payload)
    if _defer_operator_intervention_if_active(engine, envelope):
        return
    result = supersede_task(
        engine.paths,
        old_task_id=payload.old_task_id,
        replacement_task_id=payload.replacement_task_id,
        reason=payload.reason,
        actor=envelope.issuer,
        cascade=payload.cascade,
        now=engine._now(),
        issued_at=envelope.issued_at,
    )
    _complete_operator_intervention(engine, envelope, result)


def retarget_task_dependency_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxRetargetTaskDependencyPayload.model_validate(envelope.payload)
    if _defer_operator_intervention_if_active(engine, envelope):
        return
    result = retarget_queued_task_dependency(
        engine.paths,
        task_id=payload.task_id,
        old_dependency_id=payload.old_dependency_id,
        new_dependency_id=payload.new_dependency_id,
        reason=payload.reason,
        actor=envelope.issuer,
        now=engine._now(),
        issued_at=envelope.issued_at,
    )
    _complete_operator_intervention(engine, envelope, result)


def resolve_incident_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxIncidentInterventionPayload.model_validate(envelope.payload)
    if _defer_operator_intervention_if_active(engine, envelope):
        return
    result = resolve_incident_by_operator(
        engine.paths,
        incident_id=payload.incident_id,
        reason=payload.reason,
        actor=envelope.issuer,
        now=engine._now(),
        issued_at=envelope.issued_at,
    )
    _complete_operator_intervention(engine, envelope, result)


def cancel_incident_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxIncidentInterventionPayload.model_validate(envelope.payload)
    if _defer_operator_intervention_if_active(engine, envelope):
        return
    result = cancel_incident(
        engine.paths,
        incident_id=payload.incident_id,
        reason=payload.reason,
        actor=envelope.issuer,
        now=engine._now(),
        issued_at=envelope.issued_at,
    )
    _complete_operator_intervention(engine, envelope, result)


def archive_invalid_incident_from_mailbox(engine: RuntimeEngine, envelope: MailboxCommandEnvelope) -> None:
    assert engine.snapshot is not None
    payload = MailboxArchiveInvalidIncidentPayload.model_validate(envelope.payload)
    if _defer_operator_intervention_if_active(engine, envelope):
        return
    result = archive_invalid_incident_artifact(
        engine.paths,
        filename=payload.filename,
        reason=payload.reason,
        actor=envelope.issuer,
        now=engine._now(),
        issued_at=envelope.issued_at,
    )
    _complete_operator_intervention(engine, envelope, result)


def approve_execution_capability_from_mailbox(
    engine: RuntimeEngine,
    envelope: MailboxCommandEnvelope,
) -> None:
    assert engine.snapshot is not None
    payload = MailboxExecutionCapabilityApprovalPayload.model_validate(envelope.payload)
    approval = approve_execution_capability_request(
        engine.paths,
        payload.approval_id,
        decided_by=envelope.issuer,
        reason=payload.reason,
        now=engine._now(),
    )
    engine.snapshot = engine.snapshot.model_copy(update={"updated_at": engine._now()})
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="execution_capability_approval_approved",
        data={"approval_id": approval.approval_id, "grant_id": approval.grant_id},
    )


def deny_execution_capability_from_mailbox(
    engine: RuntimeEngine,
    envelope: MailboxCommandEnvelope,
) -> None:
    assert engine.snapshot is not None
    payload = MailboxExecutionCapabilityApprovalPayload.model_validate(envelope.payload)
    approval = deny_execution_capability_request(
        engine.paths,
        payload.approval_id,
        decided_by=envelope.issuer,
        reason=payload.reason,
        now=engine._now(),
    )
    engine.snapshot = engine.snapshot.model_copy(update={"updated_at": engine._now()})
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="execution_capability_approval_denied",
        data={"approval_id": approval.approval_id, "grant_id": approval.grant_id},
    )


def _defer_operator_intervention_if_active(
    engine: RuntimeEngine,
    envelope: MailboxCommandEnvelope,
) -> bool:
    assert engine.snapshot is not None
    if not engine.snapshot.active_runs_by_plane and engine.snapshot.active_stage is None:
        return False

    deferred = envelope.model_copy(
        update={
            "command_id": f"{envelope.command.value}-deferred-{uuid4().hex[:8]}",
        }
    )
    write_mailbox_command(engine.paths, deferred)
    active_planes: list[JsonValue] = [plane.value for plane in engine.snapshot.active_runs_by_plane]
    engine.snapshot = engine.snapshot.model_copy(update={"updated_at": engine._now()})
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="operator_intervention_deferred",
        data={
            "command": envelope.command.value,
            "reason": "active_runtime_stage",
            "active_planes": active_planes,
        },
    )
    engine._emit_monitor_event(
        "operator_intervention_deferred",
        command=envelope.command.value,
        reason="active_runtime_stage",
        active_planes=active_planes,
    )
    return True


def _complete_operator_intervention(
    engine: RuntimeEngine,
    envelope: MailboxCommandEnvelope,
    result: OperatorInterventionResult,
) -> None:
    assert engine.snapshot is not None
    engine._refresh_runtime_queue_depths()
    save_snapshot(engine.paths, engine.snapshot)
    write_runtime_event(
        engine.paths,
        event_type="mailbox_operator_intervention_applied",
        data={
            "command": envelope.command.value,
            "work_item_kind": result.work_item_kind.value,
            "work_item_id": result.work_item_id,
            "destination_path": str(result.destination_path.relative_to(engine.paths.root)),
        },
    )


def mailbox_reason(envelope: MailboxCommandEnvelope, *, default: str) -> str:
    value = envelope.payload.get("reason")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def mailbox_retry_scope(envelope: MailboxCommandEnvelope) -> Plane | None:
    value = envelope.payload.get("scope")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ControlRoutingError("retry_active scope must be a string")
    try:
        return Plane(value)
    except ValueError as exc:
        raise ControlRoutingError(f"Unsupported retry_active scope: {value}") from exc


__all__ = [
    "archive_blocked_task_from_mailbox",
    "archive_invalid_incident_from_mailbox",
    "approve_execution_capability_from_mailbox",
    "cancel_incident_from_mailbox",
    "cancel_work_item_from_mailbox",
    "drain_mailbox",
    "enqueue_idea_from_mailbox",
    "enqueue_probe_from_mailbox",
    "enqueue_spec_from_mailbox",
    "enqueue_task_from_mailbox",
    "deny_execution_capability_from_mailbox",
    "handle_mailbox_command",
    "mailbox_reason",
    "mailbox_retry_scope",
    "reload_config_from_mailbox",
    "resolve_incident_from_mailbox",
    "retarget_task_dependency_from_mailbox",
    "supersede_task_from_mailbox",
]
