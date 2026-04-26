"""Mailbox-drain and mailbox-command application helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import fingerprint_runtime_config, load_runtime_config
from millrace_ai.contracts import (
    MailboxAddIdeaPayload,
    MailboxAddSpecPayload,
    MailboxAddTaskPayload,
    MailboxCommandEnvelope,
    Plane,
    ReloadOutcome,
)
from millrace_ai.errors import ControlRoutingError, RuntimeLifecycleError, WorkspaceStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.mailbox import drain_incoming_mailbox_commands
from millrace_ai.queue_store import QueueStore
from millrace_ai.state_store import save_snapshot

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
        engine.snapshot = engine.snapshot.model_copy(update={"paused": True, "updated_at": engine._now()})
        save_snapshot(engine.paths, engine.snapshot)
        return
    if command == "resume":
        engine.snapshot = engine.snapshot.model_copy(update={"paused": False, "updated_at": engine._now()})
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
        reload_config_from_mailbox(engine)
        return
    if command == "add_task":
        enqueue_task_from_mailbox(engine, envelope)
        return
    if command == "add_spec":
        enqueue_spec_from_mailbox(engine, envelope)
        return
    if command == "add_idea":
        enqueue_idea_from_mailbox(engine, envelope)
        return
    raise ControlRoutingError(f"Unsupported mailbox command: {command}")


def reload_config_from_mailbox(engine: RuntimeEngine) -> None:
    assert engine.snapshot is not None
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
            "compiled_plan_id": active_plan.compiled_plan_id,
            "compiled_plan_path": str((engine.paths.state_dir / "compiled_plan.json").relative_to(engine.paths.root)),
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
    "drain_mailbox",
    "enqueue_idea_from_mailbox",
    "enqueue_spec_from_mailbox",
    "enqueue_task_from_mailbox",
    "handle_mailbox_command",
    "mailbox_reason",
    "mailbox_retry_scope",
    "reload_config_from_mailbox",
]
